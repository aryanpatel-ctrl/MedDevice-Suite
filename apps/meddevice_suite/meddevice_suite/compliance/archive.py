"""Archive copies of regulated records (module 54; D3 "Archival"; 21 CFR 11.10(b), (c)).

An archive holds every regulated record changed in a period, in full, each with
its complete audit history and electronic signatures, plus the period's login
history. It is one ZIP file that a person can read without this system:

- records/<record type>/<name>.json   one file per record
- index.csv                            one line per record, with the SHA-256
                                       fingerprint of its file
- logins.csv                           the period's login history
- manifest.json                        what the archive covers, and the
                                       fingerprints of index.csv and logins.csv

The fingerprint of the whole file is kept on the Record Archive, so the archive
can be verified at any time. Records stay in the system: an archive is a copy.

Scale: records are read in pages ordered by their last change, and the file is
written as it goes, so millions of records never sit in memory at once.
"""

import csv
import hashlib
import io
import json
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from urllib.parse import quote

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime

from meddevice_suite.compliance.retention import LOGIN_HISTORY, RETENTION_SCHEDULE, scheduled_doctypes

PAGE_SIZE = 500
# Kept inside each record's file, or in a file of their own, rather than as records.
NOT_ARCHIVED_AS_RECORDS = frozenset({"Audit Event", "Electronic Signature", LOGIN_HISTORY, "Record Archive"})
MANIFEST, INDEX, LOGINS = "manifest.json", "index.csv", "logins.csv"
INDEX_COLUMNS = ["record_type", "name", "docstatus", "last_changed", "file", "sha256"]
CHUNK = 1 << 20


def build_archive(archive: str) -> None:
	doc = frappe.get_doc("Record Archive", archive)
	doc.db_set("status", "Running")
	try:
		path, counts = write_archive(doc)
		sha256, md5 = file_digests(path)
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": path.name,
				"file_url": f"/private/files/{path.name}",
				"is_private": 1,
				"attached_to_doctype": "Record Archive",
				"attached_to_name": doc.name,
				"attached_to_field": "archive_file",
				"content_hash": md5,  # given, so Frappe never reads the whole file into memory
			}
		).insert(ignore_permissions=True)  # the archive service is the only writer
		doc.db_set(
			{
				"status": "Completed",
				"record_count": sum(counts.values()),
				"archive_file": file.file_url,
				"archive_hash": sha256,
				"completed_at": now_datetime(),
			}
		)
	except Exception:
		frappe.log_error(title=f"Record archive {archive} failed")
		doc.db_set({"status": "Failed", "error": frappe.get_traceback(with_context=False)[-2000:]})


def write_archive(doc: Document) -> tuple[Path, Counter]:
	start = datetime.combine(getdate(doc.from_date), time.min)
	end = datetime.combine(getdate(doc.to_date), time.max)
	path = Path(frappe.get_site_path("private", "files", f"{doc.name}.zip"))
	counts: Counter = Counter()

	# The index is buffered on disk past 16 MB: only one archive entry can be written at a time.
	with (
		zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive,
		tempfile.SpooledTemporaryFile(max_size=16 << 20, mode="w+", newline="") as index,
	):
		index_writer = csv.writer(index)
		index_writer.writerow(INDEX_COLUMNS)
		for doctype in archived_doctypes():
			for name in changed_records(doctype, start, end):
				record = frappe.get_doc(doctype, name)
				arcname = f"records/{frappe.scrub(doctype)}/{quote(str(name), safe=' -_.()')}.json"
				data = as_json(record_file(record))
				archive.writestr(arcname, data)
				digest = hashlib.sha256(data).hexdigest()
				index_writer.writerow([doctype, name, record.docstatus, record.modified, arcname, digest])
				counts[doctype] += 1

		index.seek(0)
		manifest_files = {
			INDEX: write_entry(archive, INDEX, (line.encode() for line in index)),
			LOGINS: write_entry(archive, LOGINS, login_history(start, end)),
		}
		archive.writestr(
			MANIFEST,
			as_json(
				{
					"archive": doc.name,
					"records_changed_from": str(start),
					"records_changed_to": str(end),
					"made_at": str(now_datetime()),
					"made_by": doc.owner,
					"record_counts": dict(sorted(counts.items())),
					"retention_schedule": {
						category: {"years": years, "basis": basis, "record_types": list(doctypes)}
						for category, (years, basis, doctypes) in RETENTION_SCHEDULE.items()
					},
					# Each record file's fingerprint is on its line of the index.
					"files": manifest_files,
				}
			),
		)
	return path, counts


def write_entry(archive: zipfile.ZipFile, arcname: str, chunks) -> str:
	"""Stream an entry into the archive and return its SHA-256."""
	digest = hashlib.sha256()
	with archive.open(arcname, "w", force_zip64=True) as entry:
		for chunk in chunks:
			entry.write(chunk)
			digest.update(chunk)
	return digest.hexdigest()


def archived_doctypes() -> list[str]:
	return [doctype for doctype in scheduled_doctypes() if doctype not in NOT_ARCHIVED_AS_RECORDS]


def changed_records(doctype: str, start: datetime, end: datetime):
	"""Names of the records last changed in the period, page by page, using the index on `modified`."""
	if frappe.get_meta(doctype).issingle:
		modified = get_datetime(frappe.db.get_single_value(doctype, "modified") or "1900-01-01")
		if start <= modified <= end:
			yield doctype
		return

	table = frappe.qb.DocType(doctype)
	after = None
	while True:
		query = (
			frappe.qb.from_(table)
			.select(table.name, table.modified)
			.where(table.modified[start:end])
			.orderby(table.modified)
			.orderby(table.name)
			.limit(PAGE_SIZE)
		)
		if after:
			query = query.where(
				(table.modified > after[1]) | ((table.modified == after[1]) & (table.name > after[0]))
			)
		page = query.run()
		yield from (name for name, _ in page)
		if len(page) < PAGE_SIZE:
			return
		after = page[-1]


def record_file(record: Document) -> dict:
	reference = {"reference_doctype": record.doctype, "reference_name": record.name}
	return {
		"record_type": record.doctype,
		"record": record.as_dict(convert_dates_to_str=True, no_private_properties=True),
		"audit_events": frappe.get_all(
			"Audit Event", filters=reference, fields=["*"], order_by="event_at asc, creation asc"
		),
		"electronic_signatures": frappe.get_all(
			"Electronic Signature", filters=reference, fields=["*"], order_by="signed_at asc, creation asc"
		),
	}


def login_history(start: datetime, end: datetime):
	"""The period's login history as CSV, page by page."""
	columns = ["creation", "name", "user", "full_name", "operation", "status", "subject", "ip_address"]
	out = io.StringIO()
	csv.writer(out).writerow(columns)
	yield out.getvalue().encode()

	table = frappe.qb.DocType(LOGIN_HISTORY)
	after = None
	while True:
		query = (
			frappe.qb.from_(table)
			.select(*(table[column] for column in columns))
			.where(table.creation[start:end])
			.orderby(table.creation)
			.orderby(table.name)
			.limit(PAGE_SIZE)
		)
		if after:
			query = query.where(
				(table.creation > after[0]) | ((table.creation == after[0]) & (table.name > after[1]))
			)
		page = query.run()
		out = io.StringIO()
		csv.writer(out).writerows(page)
		yield out.getvalue().encode()
		if len(page) < PAGE_SIZE:
			return
		after = page[-1]


@frappe.whitelist()
def verify(archive: str) -> list[str]:
	"""Problems found in the archive; an empty list means it is exactly as it was made."""
	doc = frappe.get_doc("Record Archive", archive)
	doc.check_permission("read")
	if doc.status != "Completed":
		frappe.throw(_("Only a completed archive can be verified."))

	path = Path(frappe.get_site_path("private", "files", Path(doc.archive_file).name))
	if not path.exists():
		return [_("The archive file is missing.")]

	problems = []
	if file_digests(path)[0] != doc.archive_hash:
		problems.append(_("The archive file has changed since it was made."))
	try:
		with zipfile.ZipFile(path) as archive:
			expected = json.loads(archive.read(MANIFEST))["files"]
			stored = set(archive.namelist())
			for arcname, digest in expected.items():
				problems += entry_problems(archive, stored, arcname, digest)
			listed = {MANIFEST, *expected}
			if INDEX in stored:
				with archive.open(INDEX) as index:
					for row in csv.DictReader(io.TextIOWrapper(index, encoding="utf-8", newline="")):
						listed.add(row["file"])
						problems += entry_problems(archive, stored, row["file"], row["sha256"])
			problems += [
				_("{0} was added to the archive.").format(arcname) for arcname in sorted(stored - listed)
			]
	except (zipfile.BadZipFile, KeyError, ValueError):
		problems.append(_("The archive file cannot be read."))
	return problems


def entry_problems(archive: zipfile.ZipFile, stored: set[str], arcname: str, digest: str) -> list[str]:
	if arcname not in stored:
		return [_("{0} is missing from the archive.").format(arcname)]
	sha256 = hashlib.sha256()
	with archive.open(arcname) as entry:
		while chunk := entry.read(CHUNK):
			sha256.update(chunk)
	return [] if sha256.hexdigest() == digest else [_("{0} has changed.").format(arcname)]


def file_digests(path: Path) -> tuple[str, str]:
	"""SHA-256 (the archive's fingerprint) and MD5 (Frappe's duplicate-file check), read in chunks."""
	sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
	with path.open("rb") as file:
		while chunk := file.read(CHUNK):
			sha256.update(chunk)
			md5.update(chunk)
	return sha256.hexdigest(), md5.hexdigest()


def as_json(value) -> bytes:
	return json.dumps(value, indent=1, sort_keys=True, default=str, ensure_ascii=False).encode()
