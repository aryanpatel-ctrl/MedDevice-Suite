"""Archive copies of regulated records (module 54; D3 "Archival"; 21 CFR 11.10(b), (c)).

An archive holds every regulated record changed in a period, in full, each with
its complete audit history and electronic signatures, plus the period's login
history. It is one ZIP file that a person can read without this system:

- records/<record type>/<name>.json   one file per record
- deleted/<record type>/<name> <event>.json
                                       each record deleted in the period: its
                                       content when deleted, and its history
- index.csv                            one line per file above, with the SHA-256
                                       fingerprint of the file
- logins.csv                           the period's login history
- manifest.json                        what the archive covers, and the
                                       fingerprints of index.csv and logins.csv

The fingerprint of the whole file is kept on the Record Archive, so the archive
can be verified at any time. Records stay in the system: an archive is a copy.

Scale: records are read in pages ordered by their last change (indexed for
every archived record type), and the file is written as it goes, so millions of
records never sit in memory at once. The finished file is registered where it
lies, never read into memory or copied. A large archive is verified in the
background and the person who asked is notified of the result.

An archive whose job stopped without finishing (a worker restarted or ran out
of memory) is marked Failed by a daily check, so a new one can be requested.
"""

import csv
import hashlib
import io
import json
import tempfile
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import datetime, time
from pathlib import Path
from urllib.parse import quote

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime

from meddevice_suite.compliance.audit import as_data
from meddevice_suite.compliance.retention import LOGIN_HISTORY, RETENTION_SCHEDULE, scheduled_doctypes

PAGE_SIZE = 500
# Kept inside each record's file, or in a file of their own, rather than as records.
NOT_ARCHIVED_AS_RECORDS = frozenset({"Audit Event", "Electronic Signature", LOGIN_HISTORY, "Record Archive"})
MANIFEST, INDEX, LOGINS = "manifest.json", "index.csv", "logins.csv"
INDEX_COLUMNS = ["record_type", "name", "docstatus", "last_changed", "file", "sha256"]
CHUNK = 1 << 20
# Larger archives are verified in a background job instead of the web request.
INLINE_VERIFY_BYTES = 256 << 20
# Longer than the build job may run (six hours), so only a stopped job is caught.
STALLED_AFTER_HOURS = 12
# Names Windows cannot open as files, and a length every unzip tool accepts.
RESERVED_FILE_NAMES = frozenset(
	{"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)
NAME_LENGTH = 150
# What a damaged or tampered archive may raise while it is read.
UNREADABLE = (
	zipfile.BadZipFile,
	zlib.error,
	EOFError,
	KeyError,
	ValueError,
	TypeError,
	AttributeError,
	NotImplementedError,
	RuntimeError,
	OSError,
)


def build_archive(archive: str) -> None:
	doc = frappe.get_doc("Record Archive", archive)
	doc.db_set("status", "Running")
	if not frappe.in_test:
		# Visible while the job runs, and left for the daily check if the job is stopped.
		# A background job owns its transaction, so committing here commits nothing else.
		frappe.db.commit()  # nosemgrep
	path = Path(frappe.get_site_path("private", "files", f"{doc.name}.zip"))
	try:
		counts = write_archive(doc, path)
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
				"content_hash": md5,
				"file_size": path.stat().st_size,
			}
		)
		# Registered where it lies: Frappe would otherwise read it into memory, refuse it above
		# its upload size limit, and write a second copy under another name.
		file.flags.copy_from_existing_file = True
		file.insert(ignore_permissions=True)  # the archive service is the only writer
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
		path.unlink(missing_ok=True)  # never leave a partial archive behind
		doc.db_set({"status": "Failed", "error": frappe.get_traceback(with_context=False)[-2000:]})


def fail_stalled_archives() -> None:
	"""Daily: an archive whose job stopped without finishing is marked Failed."""
	for name in frappe.get_all(
		"Record Archive",
		filters={
			"status": ["in", ["Queued", "Running"]],
			"modified": ["<", add_to_date(now_datetime(), hours=-STALLED_AFTER_HOURS)],
		},
		pluck="name",
	):
		Path(frappe.get_site_path("private", "files", f"{name}.zip")).unlink(missing_ok=True)
		frappe.get_doc("Record Archive", name).db_set(
			{
				"status": "Failed",
				"error": _("The archive job stopped before it finished. Request a new archive."),
			}
		)


def ensure_indexes() -> None:
	"""Each archived record type is read by its last change; ERPNext indexes that only for some."""
	for doctype in archived_doctypes():
		if not frappe.get_meta(doctype).issingle:
			frappe.db.add_index(doctype, ["modified", "name"])
	frappe.db.add_index(LOGIN_HISTORY, ["creation", "name"])
	frappe.db.add_index("Audit Event", ["action", "event_at"])


def write_archive(doc: Document, path: Path) -> Counter:
	start = datetime.combine(getdate(doc.from_date), time.min)
	end = datetime.combine(getdate(doc.to_date), time.max)
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
				arcname = f"records/{frappe.scrub(doctype)}/{file_name(name)}.json"
				data = as_json(record_file(record))
				archive.writestr(arcname, data)
				digest = hashlib.sha256(data).hexdigest()
				index_writer.writerow([doctype, name, record.docstatus, record.modified, arcname, digest])
				counts[doctype] += 1

		archived = set(archived_doctypes())
		for event in deletions(start, end):
			if event.reference_doctype not in archived:
				continue
			arcname = (
				f"deleted/{frappe.scrub(event.reference_doctype)}/"
				f"{file_name(event.reference_name)} {event.name}.json"
			)
			data = as_json(deleted_record_file(event))
			archive.writestr(arcname, data)
			digest = hashlib.sha256(data).hexdigest()
			index_writer.writerow(
				[event.reference_doctype, event.reference_name, "deleted", event.event_at, arcname, digest]
			)
			counts[event.reference_doctype] += 1

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
	return counts


def file_name(name: str) -> str:
	"""A record name as a file name any system can open: separators encoded, reserved device
	names avoided, and long names shortened with a fingerprint that keeps them distinct (the
	index line still gives the full name)."""
	encoded = quote(str(name), safe=" -_.()")
	if len(encoded) > NAME_LENGTH:
		encoded = f"{encoded[:100]}~{hashlib.sha256(str(name).encode()).hexdigest()[:16]}"
	if encoded.split(".")[0].upper() in RESERVED_FILE_NAMES:
		encoded = f"_{encoded}"
	return encoded


def write_entry(archive: zipfile.ZipFile, arcname: str, chunks: Iterable[bytes]) -> str:
	"""Stream an entry into the archive and return its SHA-256."""
	digest = hashlib.sha256()
	with archive.open(arcname, "w", force_zip64=True) as entry:
		for chunk in chunks:
			entry.write(chunk)
			digest.update(chunk)
	return digest.hexdigest()


def archived_doctypes() -> list[str]:
	return [doctype for doctype in scheduled_doctypes() if doctype not in NOT_ARCHIVED_AS_RECORDS]


def changed_records(doctype: str, start: datetime, end: datetime) -> Iterator[str]:
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
	return {
		"record_type": record.doctype,
		"record": record.as_dict(convert_dates_to_str=True, no_private_properties=True),
		**history(record.doctype, record.name),
	}


def deleted_record_file(event: frappe._dict) -> dict:
	return {
		"record_type": event.reference_doctype,
		"deleted": {"by": event.user, "at": event.event_at, "audit_event": event.name},
		"record": as_data(event.changes).get("snapshot"),
		**history(event.reference_doctype, event.reference_name),
	}


def history(doctype: str, name: str) -> dict:
	reference = {"reference_doctype": doctype, "reference_name": name}
	return {
		"audit_events": frappe.get_all(
			"Audit Event", filters=reference, fields=["*"], order_by="event_at asc, creation asc"
		),
		"electronic_signatures": frappe.get_all(
			"Electronic Signature", filters=reference, fields=["*"], order_by="signed_at asc, creation asc"
		),
	}


def deletions(start: datetime, end: datetime) -> Iterator[frappe._dict]:
	"""The period's deletions of records, page by page."""
	table = frappe.qb.DocType("Audit Event")
	after = None
	while True:
		query = (
			frappe.qb.from_(table)
			.select(
				table.name,
				table.reference_doctype,
				table.reference_name,
				table.user,
				table.event_at,
				table.changes,
			)
			.where(table.action == "Deleted")
			.where(table.event_at[start:end])
			.orderby(table.event_at)
			.orderby(table.name)
			.limit(PAGE_SIZE)
		)
		if after:
			query = query.where(
				(table.event_at > after.event_at)
				| ((table.event_at == after.event_at) & (table.name > after.name))
			)
		page = query.run(as_dict=True)
		yield from page
		if len(page) < PAGE_SIZE:
			return
		after = page[-1]


def login_history(start: datetime, end: datetime) -> Iterator[bytes]:
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


@frappe.whitelist(methods=["POST"])
def verify(archive: str) -> list[str] | None:
	"""Problems found in the archive; an empty list means it is exactly as it was made. A large
	archive is verified in the background (None is returned) and the requester is notified."""
	doc = frappe.get_doc("Record Archive", archive)
	doc.check_permission("read")
	if doc.status != "Completed":
		frappe.throw(_("Only a completed archive can be verified."))

	path = archive_file_path(doc)
	if path.exists() and path.stat().st_size > INLINE_VERIFY_BYTES:
		frappe.enqueue(
			verify_in_background,
			queue="long",
			timeout=6 * 60 * 60,
			now=frappe.in_test,
			archive=archive,
			user=frappe.session.user,
		)
		return None
	return problems_in(doc)


def verify_in_background(archive: str, user: str) -> None:
	problems = problems_in(frappe.get_doc("Record Archive", archive))
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"document_type": "Record Archive",
			"document_name": archive,
			"subject": _("Archive {0} verified: every file is as it was made").format(archive)
			if not problems
			else _("Archive {0} has changed: {1}").format(archive, "; ".join(problems[:20])),
		}
	).insert(ignore_permissions=True)  # the system's notice to the person who asked


def archive_file_path(doc: Document) -> Path:
	return Path(frappe.get_site_path("private", "files", Path(doc.archive_file).name))


def problems_in(doc: Document) -> list[str]:
	path = archive_file_path(doc)
	if not path.exists():
		return [_("The archive file is missing.")]

	problems = []
	if file_digests(path)[0] != doc.archive_hash:
		problems.append(_("The archive file has changed since it was made."))
	try:
		with zipfile.ZipFile(path) as archive:
			expected = dict(json.loads(archive.read(MANIFEST))["files"])
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
	except UNREADABLE:
		problems.append(_("The archive file cannot be read."))
	return problems


def entry_problems(archive: zipfile.ZipFile, stored: set[str], arcname: str, digest: str) -> list[str]:
	if arcname not in stored:
		return [_("{0} is missing from the archive.").format(arcname)]
	sha256 = hashlib.sha256()
	try:
		with archive.open(arcname) as entry:
			while chunk := entry.read(CHUNK):
				sha256.update(chunk)
	except UNREADABLE:
		return [_("{0} cannot be read.").format(arcname)]
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
