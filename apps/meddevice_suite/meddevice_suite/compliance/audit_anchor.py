"""Anchors of the audit trail and the electronic signatures (URS-CMP-040; 21 CFR 11.10(e)).

Each record's audit events, and each record's signatures, form a chain (audit.py, esign.py):
an entry altered, removed or inserted before the newest is detected. Removing a record's newest
entries, or its whole history, leaves a shorter chain that still verifies, so the chains alone
cannot show that an entry once existed. 21 CFR 11.10(e) requires that recorded changes are never
obscured, and ALCOA+ that the record is complete and enduring: removing the newest entries must
be detectable as well.

What protects what (independent review 2026-10-15):

- An entry rewritten, anywhere in its chain: the entry fingerprints. Since fingerprint version 2
  they are keyed with a key derived from the site's `encryption_key`, kept in site_config.json
  outside the database, so database access alone cannot compute them again (fingerprint.py). The
  anchor does not protect older entries: it pins only each trail's newest entry and the count.
- Entries removed from the end of a chain, or a whole history: the anchors, below.
- The anchors themselves: their database copy is fingerprinted unkeyed and chained, which shows
  an anchor altered by hand but not one rewritten with its fingerprint computed again; the copy
  in the anchor file, outside the database, is what protects them.
- Not protected by any of these: entries recorded before version 2 (unkeyed) rewritten by someone
  who can write the database, as far as the anchors do not pin them; and anything done by someone
  who holds both the database and the site's files (site_config.json and the anchor file), which
  only the backups and the period archives of module 54 can show.

An anchor records the head of a whole trail at one moment: its newest entry, that entry's
fingerprint, and how many entries the trail held up to it. Verification then reports entries
that existed and no longer do: the anchored entry is missing or changed, or fewer entries remain
up to it than were anchored. It is recorded every hour by the scheduler and whenever a reviewer
asks (before an inspection, for example).

Where it is kept. A database-level deletion of audit rows can delete anchor rows with them, so
every anchor is kept twice: as an Audit Anchor record, which no one may create, change or delete
(like the Audit Event), and as a line appended to a file, which this app only ever appends to.
The file is kept in the site's `private/audit` folder, outside every folder Frappe serves: Frappe
sends a private file only from `private/files`, and only one a File record names, and backups only
from `private/backups`; a File record cannot name a path outside the files folders, and the File
hook refuses one naming the anchor file wherever it is (compliance/evidence_files.py). Until
2026-10-15 it was kept in `private/files`, where any desk user could make a File record name it and
read it; the patch move_audit_anchor_file moves it (`move_former_anchor_file`). Removing entries
without it being reported then needs write access to the server's file system as well as to the
database. The period archives of module 54 (which copy the anchors as records) keep further copies;
Frappe's file backups (`bench backup --with-files`) copy only the files folders, so the server's
own backup must include `private/audit`; on Linux the file can also be made append-only for every
user with `chattr +a`. The line is written only once the anchor is committed, so an anchor that is
rolled back never reaches the file.

The count is carried forward: an anchor records at least the previous anchor's count plus the
entries added since, so an anchor taken after entries were removed keeps reporting them instead of
recording the shorter trail as the new truth. An entry whose transaction commits after an anchor
but carries an earlier time is added by the next full count.

A problem the anchors show alerts QA and the system administrator once (alert, below), whether the
hourly job finds it or a reviewer's check does, and is kept as an Audit Trail Finding.

Not detected: entries removed after the newest anchor and before the next one (at most an hour of
entries); removed together with the anchors in the database and the lines in the file.

Scale: anchoring and verification each read the newest entry and count entries through the index
on `creation` that Frappe gives every table, and read the end of the file only.
"""

import json
import os
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime

from meddevice_suite.compliance.fingerprint import sha256

DOCTYPE = "Audit Anchor"
# trail -> the field holding each entry's fingerprint
LEDGERS: dict[str, str] = {"Audit Event": "event_hash", "Electronic Signature": "signature_hash"}
SCHEDULED, ON_REQUEST = "Scheduled", "On request"
FILE_NAME = "audit-anchors.jsonl"
# The anchor file's folder in the site: under its private folder, outside every folder Frappe serves.
FOLDER = ("private", "audit")
# Where the file was kept until 2026-10-15, in the private files folder Frappe serves.
FORMER_FOLDER = ("private", "files")
# What an anchor holds, in its record and in its line of the file.
ANCHORED = (
	"ledger",
	"head_entry",
	"head_fingerprint",
	"head_created",
	"entry_count",
	"anchored_at",
	"previous_anchor",
	"anchor_hash",
)
DATETIMES = frozenset({"head_created", "anchored_at"})
ANCHOR_FILE = "Anchor file"
# What a finding is: the options of the Audit Trail Finding's kind.
ENTRY_MISSING = "Newest entry missing"
ENTRY_CHANGED = "Newest entry changed"
ENTRIES_MISSING = "Entries missing"
ANCHOR_REMOVED = "Anchor removed"
ANCHOR_DIFFERS = "Anchor differs from the file"
ANCHOR_ALTERED = "Anchor altered"
ANCHOR_UNLINKED = "Anchor before it missing"
FILE_MISSING = "Anchor file missing"
FILE_DAMAGED = "Anchor file damaged"
KINDS = (
	ENTRY_MISSING,
	ENTRY_CHANGED,
	ENTRIES_MISSING,
	ANCHOR_REMOVED,
	ANCHOR_DIFFERS,
	ANCHOR_ALTERED,
	ANCHOR_UNLINKED,
	FILE_MISSING,
	FILE_DAMAGED,
)
FINDING = "Audit Trail Finding"
PROBLEM_ALERT = "Audit Trail Problem Found"
SAVEPOINT = "audit_trail_finding"
# The file is read from its end in blocks of this size until each trail's newest anchor is found.
TAIL_BLOCK = 64 << 10


def anchor_file() -> Path:
	return Path(frappe.get_site_path(*FOLDER, FILE_NAME))


def former_anchor_file() -> Path:
	return Path(frappe.get_site_path(*FORMER_FOLDER, FILE_NAME))


def names_anchor_file(address: str | None) -> bool:
	"""Whether a File's address or name points at the anchor file, where it is kept or was kept. Any
	file of its name is refused, in whatever folder, so no spelling of the address gets past."""
	if not address:
		return False
	# Letter case too: a file system may ignore it (macOS does).
	path = unquote(address).split("?", 1)[0].replace("\\", "/").lower()
	return path.rstrip("/").rsplit("/", 1)[-1] == FILE_NAME or "/".join(FOLDER) in path


def move_former_anchor_file() -> None:
	"""Patch move_audit_anchor_file (2026-10-15): bring the anchors kept in `private/files` to the
	anchor file's place. Anchors written there already, by the hourly job between the release and
	the migrate, follow the moved ones, so the file stays in the order the anchors were made. Run
	again, or after a move cut short, it copies nothing twice. A file made append-only (`chattr +a`)
	cannot be moved: the patch then fails, naming it, until the attribute is removed."""
	former, current = former_anchor_file(), anchor_file()
	if not former.exists():
		return
	moved = former.read_bytes()
	if moved and not moved.endswith(b"\n"):
		moved += b"\n"
	held = current.read_bytes() if current.exists() else b""
	if not held.startswith(moved):
		current.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
		staged = current.with_name(f"{current.name}.moving")
		descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
		with os.fdopen(descriptor, "wb") as file:
			file.write(moved + held)
			file.flush()
			os.fsync(file.fileno())
		os.replace(staged, current)  # in one step: the anchor file is never seen half written
	former.unlink()


def anchor_trail(trigger: str = SCHEDULED) -> list[Document]:
	"""Hourly, and on request: anchor each trail that has had entries since its last anchor, and
	alert what the anchors before showed."""
	findings = trail_findings()  # the trail as it was found, before this run anchors it
	anchors = [anchor for ledger in LEDGERS if (anchor := anchor_ledger(ledger, trigger))]
	# After the anchors: an alert that fails never costs one.
	alert(findings)
	return anchors


@frappe.whitelist(methods=["POST"])
def anchor_now() -> list[dict]:
	"""For reviewers: anchor the trails now, before an inspection or an archive."""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	anchor_trail(ON_REQUEST)
	return [
		{"ledger": ledger, "anchor": anchor.name, "entry_count": anchor.entry_count}
		for ledger in LEDGERS
		if (anchor := newest_anchor(ledger))
	]


@frappe.whitelist()
def verify_audit_trail() -> list[str]:
	"""For reviewers: what the anchors show about the whole audit trail and the signatures."""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	return checked_trail()


def checked_trail() -> list[str]:
	"""A check a reviewer runs: what the anchors show, each new finding alerted. The finding and its
	alert are kept when the request is committed: the desk sends these checks as POST requests."""
	findings = trail_findings()
	alert(findings)
	return [finding.message for finding in findings]


def what_to_do() -> str:
	return _(
		"Treat this as a data-integrity incident: change nothing on the records concerned, find out who "
		"had access to the database and the server since the anchor named, compare the trail with the "
		"latest backup and record archive, and record a deviation under QA. Verify Audit Trail on the "
		"Audit Anchor list shows what is found now."
	)


def alert(findings: list[Finding]) -> None:
	"""QA and the system administrator are alerted once per finding (21 CFR 11.10(e): a removal must
	reach someone who acts on it). The Audit Trail Finding recorded with the alert is what keeps the
	same finding from being alerted again, as the lot expiry alerts keep theirs on the lot. A finding
	whose alert fails is logged and alerted on the next check instead."""
	if not findings:
		return
	alerted = set(
		frappe.get_all(
			FINDING,
			filters={"finding_key": ["in", [finding.key for finding in findings]]},
			pluck="finding_key",
		)
	)
	for finding in findings:
		if finding.key in alerted:
			continue
		alerted.add(finding.key)
		frappe.db.savepoint(SAVEPOINT)
		try:
			record = frappe.get_doc(
				{
					"doctype": FINDING,
					"trail": finding.trail,
					"kind": finding.kind,
					"subject": finding.subject,
					"finding": finding.message,
					"what_to_do": what_to_do(),
					"finding_key": finding.key,
					"detected_at": now_datetime(),
				}
			)
			# No role may create findings; this service is the only writer, as for the anchors.
			record.flags.recorded_by_audit_anchor = True
			record.insert(ignore_permissions=True)
			frappe.get_doc("Notification", PROBLEM_ALERT).send(record)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=_("Audit trail problem could not be alerted: {0}").format(finding.kind))
		else:
			frappe.db.release_savepoint(SAVEPOINT)


def anchor_ledger(ledger: str, trigger: str) -> Document | None:
	"""A new anchor of the trail, or None when it has no entry or none since its last anchor."""
	# Locked, so that the hourly job and a reviewer anchoring at the same moment chain one after
	# the other instead of both following the same anchor.
	previous = newest_anchor(ledger, for_update=True)
	head = newest_entry(ledger)
	if head is None or (previous and previous.head_entry == head.name):
		return None

	count = count_entries(ledger, up_to=head.creation)
	if previous:
		added = count_entries(ledger, after=previous.head_created, up_to=head.creation)
		count = max(count, cint(previous.entry_count) + added)

	anchor = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"ledger": ledger,
			"head_entry": head.name,
			"head_fingerprint": head.fingerprint,
			"head_created": head.creation,
			"entry_count": count,
			"anchored_at": now_datetime(),
			"trigger": trigger,
			"previous_anchor": previous.name if previous else None,
		}
	)
	anchor.anchor_hash = anchor_fingerprint(anchor, previous.anchor_hash if previous else "")
	# No role may create anchors; this service is the only writer, and the record refuses an insert
	# without this mark, which a request cannot set on a document.
	anchor.flags.recorded_by_audit_anchor = True
	anchor.insert(ignore_permissions=True)
	frappe.db.after_commit.add(partial(append_to_file, anchor_file(), as_line(anchor)))
	return anchor


def newest_anchor(ledger: str, for_update: bool = False) -> frappe._dict | None:
	table = frappe.qb.DocType(DOCTYPE)
	query = (
		frappe.qb.from_(table)
		.select(table.name, *(table[field] for field in ANCHORED))
		.where(table.ledger == ledger)
		.orderby(table.creation, order=frappe.qb.desc)
		.orderby(table.name, order=frappe.qb.desc)
		.limit(1)
	)
	anchors = (query.for_update() if for_update else query).run(as_dict=True)
	return anchors[0] if anchors else None


def newest_entry(ledger: str) -> frappe._dict | None:
	table = frappe.qb.DocType(ledger)
	entries = (
		frappe.qb.from_(table)
		.select(table.name, table.creation, table[LEDGERS[ledger]].as_("fingerprint"))
		.orderby(table.creation, order=frappe.qb.desc)
		.orderby(table.name, order=frappe.qb.desc)
		.limit(1)
		.run(as_dict=True)
	)
	return entries[0] if entries else None


def count_entries(ledger: str, up_to, after=None) -> int:
	filters = [["creation", "<=", get_datetime(up_to)]]
	if after is not None:
		filters.append(["creation", ">", get_datetime(after)])
	return frappe.db.count(ledger, filters)


def anchor_fingerprint(anchor: Document | frappe._dict, previous_hash: str) -> str:
	return sha256(
		[
			previous_hash,
			anchor.ledger,
			anchor.head_entry,
			anchor.head_fingerprint,
			str(get_datetime(anchor.head_created)),
			cint(anchor.entry_count),
			str(get_datetime(anchor.anchored_at)),
		]
	)


def as_line(anchor: Document) -> str:
	content = {"anchor": anchor.name, **{field: plain(field, anchor.get(field)) for field in ANCHORED}}
	return json.dumps(content, sort_keys=True, ensure_ascii=False)


def plain(field: str, value):
	"""An anchor's value in one form, whether read from the database, a document or the file."""
	if field in DATETIMES:
		return str(get_datetime(value))
	if field == "entry_count":
		return cint(value)
	return value or None


def append_to_file(path: Path, line: str) -> None:
	"""Runs once the anchor is committed. The file is opened for appending only: nothing written
	to it is ever rewritten. A failure is logged; the anchor stays in the database."""
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		# A line cut short by a full disk is closed first, so the next line stays readable.
		text = ("" if ends_with_newline(path) else "\n") + line + "\n"
		descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
		with os.fdopen(descriptor, "a", encoding="utf-8") as file:
			# One write of one short line: appends from two workers at once never interleave.
			file.write(text)
			file.flush()
			os.fsync(file.fileno())
	except OSError:
		# Deferred: the transaction this ran after has already been committed.
		frappe.log_error(title=_("Audit anchor not written to {0}").format(path.name), defer_insert=True)


def ends_with_newline(path: Path) -> bool:
	"""Whether the file ends where a new line can start: missing, empty, or ending with a newline."""
	if not path.exists() or not path.stat().st_size:
		return True
	with path.open("rb") as file:
		file.seek(-1, os.SEEK_END)
		return file.read(1) == b"\n"


class Finding(NamedTuple):
	"""One problem the anchors show. Its key names the problem, not the moment: the same removal
	found again an hour later, after the next anchor, has the same key and is alerted once."""

	trail: str  # the audit trail, the signatures, or the anchor file
	kind: str
	subject: str  # the entry, the anchor, or how many entries are missing
	message: str

	@property
	def key(self) -> str:
		return sha256([self.trail, self.kind, self.subject])


def verify_trail() -> list[str]:
	"""Problems the anchors show in the audit trail and the signatures; empty when none is found."""
	return [finding.message for finding in trail_findings()]


def trail_findings() -> list[Finding]:
	path = anchor_file()
	findings: list[Finding] = []
	filed: dict[str, frappe._dict] = {}
	if path.exists():
		filed, findings = newest_in_file(path)
	elif frappe.get_all(DOCTYPE, limit=1, pluck="name"):
		findings.append(
			Finding(ANCHOR_FILE, FILE_MISSING, "", _("The anchor file {0} is missing.").format(path.name))
		)

	for ledger in LEDGERS:
		anchors = []
		if stored := newest_anchor(ledger):
			findings += check_stored(stored)
			anchors.append(stored)
		if copy := filed.get(ledger):
			findings += check_against_database(copy)
			anchors.append(copy)
		if anchors:
			# The newer of the two: the file's when the newest anchors were removed from the database.
			newest = max(anchors, key=lambda anchor: (get_datetime(anchor.anchored_at), anchor.entry_count))
			findings += check_ledger(ledger, newest)
	return findings


def check_ledger(ledger: str, anchor: frappe._dict) -> list[Finding]:
	findings = []
	fingerprint = frappe.db.get_value(ledger, anchor.head_entry, LEDGERS[ledger])
	if fingerprint is None:
		findings.append(
			Finding(
				ledger,
				ENTRY_MISSING,
				anchor.head_entry,
				_(
					"{0}: entry {1}, the newest when anchor {2} was recorded, is missing: the newest "
					"entries have been removed."
				).format(_(ledger), anchor.head_entry, anchor_name(anchor)),
			)
		)
	elif fingerprint != anchor.head_fingerprint:
		findings.append(
			Finding(
				ledger,
				ENTRY_CHANGED,
				anchor.head_entry,
				_("{0}: entry {1} has changed since anchor {2} was recorded.").format(
					_(ledger), anchor.head_entry, anchor_name(anchor)
				),
			)
		)
	present = count_entries(ledger, up_to=anchor.head_created)
	if (missing := cint(anchor.entry_count) - present) > 0:
		# Keyed by how many are missing, which later anchors carry forward: a further removal is a
		# new finding, the same one found again is not.
		findings.append(
			Finding(
				ledger,
				ENTRIES_MISSING,
				str(missing),
				_("{0}: {1} of the {2} entries recorded up to anchor {3} are missing.").format(
					_(ledger), missing, cint(anchor.entry_count), anchor_name(anchor)
				),
			)
		)
	return findings


def check_stored(anchor: frappe._dict) -> list[Finding]:
	previous_hash = ""
	if anchor.previous_anchor:
		previous_hash = frappe.db.get_value(DOCTYPE, anchor.previous_anchor, "anchor_hash")
		if previous_hash is None:
			return [
				Finding(
					anchor.ledger,
					ANCHOR_UNLINKED,
					anchor.name,
					_("Anchor {0}: the anchor before it is missing.").format(anchor.name),
				)
			]
	if anchor.anchor_hash != anchor_fingerprint(anchor, previous_hash):
		return [
			Finding(
				anchor.ledger,
				ANCHOR_ALTERED,
				anchor.name,
				_("Anchor {0}: content does not match its fingerprint.").format(anchor.name),
			)
		]
	return []


def check_against_database(copy: frappe._dict) -> list[Finding]:
	stored = frappe.db.get_value(DOCTYPE, copy.anchor, list(ANCHORED), as_dict=True)
	if stored is None:
		return [
			Finding(
				copy.ledger,
				ANCHOR_REMOVED,
				copy.anchor,
				_("Anchor {0} of the {1} is in the anchor file but no longer in the database.").format(
					copy.anchor, _(copy.ledger)
				),
			)
		]
	if any(plain(field, stored.get(field)) != plain(field, copy.get(field)) for field in ANCHORED):
		return [
			Finding(
				copy.ledger,
				ANCHOR_DIFFERS,
				copy.anchor,
				_("Anchor {0} differs from its copy in the anchor file.").format(copy.anchor),
			)
		]
	return []


def anchor_name(anchor: frappe._dict) -> str:
	return anchor.get("anchor") or anchor.name


def newest_in_file(path: Path) -> tuple[dict[str, frappe._dict], list[Finding]]:
	"""The last anchor of each trail in the file, read from its end, and any damaged line met."""
	newest: dict[str, frappe._dict] = {}
	damaged = 0
	for line in lines_from_end(path):
		if len(newest) == len(LEDGERS):
			break
		if not line.strip():
			continue
		copy = readable(line)
		if copy is None:
			damaged += 1
		elif copy.ledger not in newest:
			newest[copy.ledger] = copy
	message = _("The anchor file {0} has a damaged line.").format(path.name)
	return newest, [Finding(ANCHOR_FILE, FILE_DAMAGED, "", message)] if damaged else []


def readable(line: str) -> frappe._dict | None:
	"""An anchor's line of the file, or None when it is not one whole anchor."""
	try:
		copy = frappe._dict(json.loads(line))
		if copy.get("ledger") not in LEDGERS or any(
			copy.get(field) in (None, "") for field in ("anchor", *ANCHORED) if field != "previous_anchor"
		):
			return None
		for field in DATETIMES:
			get_datetime(copy[field])
	except (ValueError, TypeError, AttributeError):
		return None
	return copy


def lines_from_end(path: Path) -> Iterator[str]:
	with path.open("rb") as file:
		position = file.seek(0, os.SEEK_END)
		rest = b""
		while position > 0:
			step = min(TAIL_BLOCK, position)
			position -= step
			file.seek(position)
			lines = (file.read(step) + rest).split(b"\n")
			# The first piece may be the end of a line that started in the block before.
			rest = lines.pop(0) if position > 0 else b""
			for line in reversed(lines):
				yield line.decode("utf-8", errors="replace")
