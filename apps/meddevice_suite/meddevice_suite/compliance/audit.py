"""Tamper-evident audit trail (21 CFR 11.10(e); ISO 13485 4.2.5).

Every create, change, submit, cancel, amend, discard, rename and delete of a
regulated record (see `audit_scope`) writes an Audit Event: who, when, what, and
the old and new values, or the whole record on creation and deletion. Values that
code sets on the record with `db_set` are recorded too. Cancelling, amending and
changing after submission need a reason (see `audit_reason`). A record's events form a
chain: each one is fingerprinted together with the fingerprint of the event
before it, so altering, removing or inserting an event breaks the chain. The
fingerprint is keyed with a key kept outside the database (fingerprint.py, version 2),
so database access alone cannot rewrite an event and compute the chain again.

Not recorded: totals and statuses that ERPNext recalculates with
`frappe.db.set_value` from other records (a purchase order's received
percentage, for example). They are derived by the system, not entered, and the
transaction that changed them is itself audited. Code in this app never writes
a regulated record that way.

Scale: one insert and one indexed read per audited write. Frappe already loads
and row-locks the record before a save, which also serialises the chain per
record; there is no lock shared between records.
"""

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.model import child_table_fields, no_value_fields, table_fields
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, to_timedelta

from meddevice_suite.compliance import audit_scope
from meddevice_suite.compliance.audit_reason import (
	STAGED_ACTION,
	action_finished,
	reason_for,
	reason_used,
	saved_around,
)
from meddevice_suite.compliance.audit_scope import is_regulated
from meddevice_suite.compliance.fingerprint import (
	CURRENT_VERSION,
	FIRST_VERSION,
	KEYED_VERSION,
	Trail,
	chain_head,
	chain_problems,
	in_chain_order,
	keyed_sha256,
	keyed_since,
	record_content,
	sha256,
	version_of,
)

NUMBERS = frozenset({"Int", "Check", "Float", "Currency", "Percent"})
COMPARED_AS_VALUES = NUMBERS | {"Date", "Datetime", "Time"}

ACTION_FOR = {
	"submit": "Submitted",
	"cancel": "Cancelled",
	"update_after_submit": "Updated after submission",
	"discard": "Discarded",
}

TRAIL = Trail(
	"Audit Event",
	link="previous_event",
	fingerprint="event_hash",
	recorded_at="event_at",
	name_at="document_name_at_event",
)


def before_insert(doc: Document, method: str | None = None) -> None:
	# Runs for every insert, even with validation skipped; marks the record's first event as its creation.
	if should_audit(doc):
		doc.flags.audit_creation_pending = True
		if doc.get("amended_from"):
			reason_for(doc, "Amended")


# The reason is taken when the action starts, so that the records it causes to be cancelled
# or changed in the same request take it too.
def before_cancel(doc: Document, method: str | None = None) -> None:
	if should_audit(doc):
		reason_for(doc, "Cancelled")


def before_update_after_submit(doc: Document, method: str | None = None) -> None:
	if should_audit(doc):
		reason_for(doc, "Updated after submission")


def before_change(doc: Document, method: str | None = None) -> None:
	# Frappe runs this only from `db_set`: a value written by code, not by a save.
	if should_audit(doc):
		doc.flags.audit_set_by_code = True


def on_change(doc: Document, method: str | None = None) -> None:
	"""Runs at the end of every insert, save, submit, cancel, update after submit and discard,
	and after every `db_set`, which ERPNext also calls while those are still running (a status
	set in `on_submit`, for example). Each action is recorded once and each change once: the
	first call of a save records the action, and any later call on the same record object
	records only what changed since the last event recorded from it.
	"""
	set_by_code = doc.flags.pop("audit_set_by_code", False)
	# Frappe also runs on_change while deleting; on_trash has recorded the deletion.
	if doc.flags.in_delete or not should_audit(doc):
		return
	if set_by_code and doc.flags.audit_creation_pending and not frappe.db.exists(doc.doctype, doc.name):
		# Code writing to a record while it is being validated, before it is stored (ERPNext's
		# lot bundles do): nothing is stored yet, and the creation event records the final values.
		return

	# Frappe reloads the stored record at the start of every save; `db_set` loads it only when
	# it is missing, so the same object means the same save, or code writing after it.
	before = doc.get_doc_before_save()
	last = doc.flags.audit_last_recorded
	in_save = (doc.doctype, doc.name) in frappe.flags.currently_saving
	# A db_set outside a save writes only its own fields; the object may hold other edits that
	# were never saved, so what is stored is compared, not the object.
	if before is None and not doc.flags.get("audit_creation_pending"):
		# Every insert is marked by before_insert, and every save of a stored record loads it,
		# so this is code writing to a record that was never inserted: nothing was stored.
		return
	current = frappe.get_doc(doc.doctype, doc.name) if set_by_code and not in_save else doc

	if doc.flags.pop("audit_creation_pending", False):
		action, changes = creation_event(doc)
	elif last and (last.before is before or (set_by_code and not in_save)):
		action, changes = "Updated", differences(last.state, current)
	elif set_by_code and not in_save:
		action, changes = "Updated", differences(before, current)
	else:
		action, changes = ACTION_FOR.get(getattr(doc, "_action", "save"), "Updated"), differences(before, doc)

	if set_by_code and not in_save and changes:
		if outer := saved_around(doc):
			# ERPNext updating this record while another is saved (a receipt marking its order
			# received): the record whose action changed it.
			changes["caused_by"] = list(outer)
		elif command := frappe.form_dict.get("cmd"):
			# A status command run on its own (Close, Hold, Stop, Re-open): which one was used.
			changes["command"] = command

	if action in STAGED_ACTION:
		# Taken again here for actions that skipped their start hook (flags.ignore_validate).
		reason, caused_by = reason_for(doc, action)
		if caused_by:
			changes["caused_by"] = caused_by
		record(doc.doctype, doc.name, action, changes, reason)
		reason_used(doc)
	elif action != "Updated" or changes:
		record(doc.doctype, doc.name, action, changes)
	doc.flags.audit_last_recorded = frappe._dict(before=before, state=copy_of(current))
	if not set_by_code:  # the save's own final call
		action_finished(doc)


def creation_event(doc: Document) -> tuple[str, dict]:
	if doc.get("amended_from"):
		action = "Amended"
	elif doc.docstatus == 1:
		action = "Submitted"
	else:
		action = "Created"
	return action, {"snapshot": record_content(doc)}


def copy_of(doc: Document) -> Document:
	"""The record as the trail now knows it, to compare later writes on the same object against."""
	return frappe.get_doc(deepcopy(doc.as_dict()))


def on_trash(doc: Document, method: str | None = None) -> None:
	if should_audit(doc):
		record(doc.doctype, doc.name, "Deleted", {"snapshot": record_content(doc)})


def after_rename(doc: Document, method: str, old: str, new: str, merge: bool = False) -> None:
	if should_audit(doc):
		record(doc.doctype, new, "Renamed", {"old_name": old, "new_name": new, "merged": bool(merge)})


def keep_user_id(doc: Document, method: str, old: str, new: str, merge: bool = False) -> None:
	"""User before_rename: a user ID is permanent (21 CFR 11.300(b)). Renaming a user would rewrite
	the user on every audit event and signature they made, which the chains would then report
	as tampering; a person with a new address gets a new account."""
	if not (frappe.flags.in_install or frappe.flags.in_migrate):
		frappe.throw(
			_(
				"User IDs are permanent in the audit trail. Create a new account instead of renaming {0}."
			).format(old),
			frappe.PermissionError,
		)


def keep_user_with_history(doc: Document, method: str | None = None) -> None:
	"""User on_trash: a user ID in the audit trail is never deleted, so it is never given to anyone
	else (21 CFR 11.300(b)). The audit trail does not stop a deletion (hooks.py,
	ignore_links_on_delete), and a new account under the deleted ID would take over the deleted
	person's events and signatures. A person who leaves has the account disabled instead.

	Runs before Frappe checks the links that would otherwise block the deletion."""
	if doc.name in ("Administrator", "Guest"):  # Frappe never deletes these
		return
	if has_history(doc.name):
		frappe.throw(
			_(
				"{0} has acted on regulated records, so the user ID stays in the audit trail and cannot be "
				"deleted. Disable the account instead."
			).format(doc.name),
			frappe.PermissionError,
		)


def has_history(user: str) -> bool:
	"""Whether the user appears in the audit trail, has signed, or owns a regulated record.

	The indexed lookups come first; ownership covers records made before the audit trail was
	installed. A user is deleted rarely, so reading each regulated record type once is affordable.
	"""
	if frappe.db.exists("Audit Event", {"user": user}) or frappe.db.exists(
		"Electronic Signature", {"signer": user}
	):
		return True
	if frappe.db.exists("Audit Event", {"impersonated_by": user}):
		return True
	return any(frappe.db.exists(doctype, {"owner": user}) for doctype in regulated_record_types())


def regulated_record_types() -> list[str]:
	"""The regulated record types stored one record per row: declared ones and this app's own."""
	return frappe.get_all(
		"DocType",
		filters={"istable": 0, "issingle": 0, "is_virtual": 0},
		or_filters={
			"name": ["in", sorted(audit_scope.REGULATED_DOCTYPES)],
			"module": ["in", frappe.get_module_list(audit_scope.APP)],
		},
		pluck="name",
	)


def should_audit(doc: Document) -> bool:
	# Child-table rows are recorded in their parent record's events.
	return not frappe.flags.in_install and not doc.meta.istable and is_regulated(doc.doctype, doc.meta.module)


def differences(before: Document, after: Document) -> dict:
	"""Old and new stored values of what changed, as plain JSON.

	Frappe's Version diff is built for display: it formats values and shows a
	linked record's title instead of its id. An audit entry keeps the values
	exactly as stored.
	"""
	changed, added, removed, row_changed = [], [], [], []
	for df in after.meta.fields:
		if getattr(df, "is_virtual", False):
			continue
		# Checked before no_value_fields, which Frappe defines to include tables.
		if df.fieldtype in table_fields:
			old_rows = {row.name: row for row in before.get(df.fieldname) or []}
			new_rows = {row.name: row for row in after.get(df.fieldname) or []}
			added += [
				[df.fieldname, row_content(row)] for name, row in new_rows.items() if name not in old_rows
			]
			removed += [
				[df.fieldname, row_content(row)] for name, row in old_rows.items() if name not in new_rows
			]
			for name in old_rows.keys() & new_rows.keys():
				if row_diff := field_changes(old_rows[name], new_rows[name], old_rows[name].meta.fields):
					row_changed.append([df.fieldname, name, row_diff])
		elif df.fieldtype not in no_value_fields and not same(
			before.get(df.fieldname), after.get(df.fieldname), df.fieldtype
		):
			changed.append([df.fieldname, before.get(df.fieldname), after.get(df.fieldname)])

	for key in ("name", "docstatus"):
		if before.get(key) != after.get(key):
			changed.append([key, before.get(key), after.get(key)])

	diff = {"changed": changed, "added": added, "removed": removed, "row_changed": row_changed}
	return json.loads(frappe.as_json({key: value for key, value in diff.items() if value}))


def field_changes(before: Document, after: Document, fields: list) -> list:
	return [
		[df.fieldname, before.get(df.fieldname), after.get(df.fieldname)]
		for df in fields
		if df.fieldtype not in no_value_fields
		and df.fieldtype not in table_fields
		and not same(before.get(df.fieldname), after.get(df.fieldname), df.fieldtype)
	]


def row_content(row: Document) -> dict:
	return {key: value for key, value in record_content(row).items() if key not in child_table_fields}


def same(old: Any, new: Any, fieldtype: str | None = None) -> bool:
	"""Equal as stored values. An empty string and no value are the same; dates, times and numbers
	are compared as values, because the database returns a date where the desk and ERPNext set
	the same date as text, and a time as "9:30:00" where they set "09:30:00"."""
	if old == new or (old in (None, "") and new in (None, "")):
		return True
	# A number left blank is stored as 0; any other blank differs from a value.
	if fieldtype not in COMPARED_AS_VALUES or (
		fieldtype not in NUMBERS and (old in (None, "") or new in (None, ""))
	):
		return False
	try:
		return as_value(old, fieldtype) == as_value(new, fieldtype)
	except (TypeError, ValueError, frappe.ValidationError):
		return False  # a value that is not of its type is recorded as it is


def as_value(value: Any, fieldtype: str) -> date | datetime | timedelta | int | float:
	"""A value of a field type as one comparable form, whatever form code holds it in."""
	if fieldtype == "Date":
		return getdate(value)
	if fieldtype == "Datetime":
		return get_datetime(value)
	if fieldtype == "Time":
		return to_timedelta(str(value))
	if fieldtype in ("Int", "Check"):
		return cint(value)
	return flt(value, 9)


def record(doctype: str, name: str, action: str, changes: dict, reason: str | None = None) -> Document:
	previous = last_event(doctype, name)
	event = frappe.get_doc(
		{
			"doctype": "Audit Event",
			"reference_doctype": doctype,
			"reference_name": name,
			"document_name_at_event": name,
			"action": action,
			"user": frappe.session.user,
			"impersonated_by": (frappe.session.data or {}).get("impersonated_by"),
			"event_at": now_datetime(),
			"reason": reason,
			"changes": changes,
			"previous_event": previous.name if previous else None,
			"fingerprint_version": CURRENT_VERSION,
		}
	)
	keyed_since()  # recorded before the first keyed event, if the migrate has not recorded it
	event.event_hash = event_fingerprint(event, previous.event_hash if previous else "")
	# No role may create audit events; this service is the only writer, and the record refuses
	# an insert without this mark, which a request cannot set on a document.
	event.flags.recorded_by_audit_trail = True
	event.insert(ignore_permissions=True)
	return event


def last_event(doctype: str, name: str) -> frappe._dict | None:
	return chain_head("Audit Event", "previous_event", "event_hash", (doctype, name))


def event_fingerprint(event: Document | frappe._dict, previous_hash: str) -> str | None:
	"""The event's fingerprint in its own version (fingerprint.py); None for an unknown version.

	The name as it was at the event: renaming a record rewrites `reference_name` in its events, which
	must not read as tampering; verification checks that name is one the record has had."""
	content = [
		previous_hash,
		event.reference_doctype,
		event.document_name_at_event,
		event.action,
		event.user,
		event.impersonated_by or "",
		str(get_datetime(event.event_at)),
		event.reason or "",
		as_data(event.changes),
	]
	version = version_of(event)
	if version == FIRST_VERSION:
		return sha256(content)
	if version == KEYED_VERSION:
		return keyed_sha256(TRAIL.doctype, [version, *content])
	return None


def verify_events(doctype: str, name: str) -> list[str]:
	"""Problems found in the record's audit chain; an empty list means it is intact
	(fingerprint.chain_problems, the rule the signatures are verified by too)."""
	events = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=[
			"name",
			"reference_doctype",
			"document_name_at_event",
			"action",
			"user",
			"impersonated_by",
			"event_at",
			"reason",
			"changes",
			"previous_event",
			"event_hash",
			"fingerprint_version",
		],
		order_by="event_at asc, creation asc",
	)
	return chain_problems(TRAIL, events, record_names(doctype, name), event_fingerprint)


def record_names(doctype: str, name: str) -> set[str]:
	"""Every name the record has had: its name now and each one its Renamed events record. Entries
	carry the name the record had when they were recorded; renaming rewrites their reference only."""
	renames = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name, "action": "Renamed"},
		pluck="changes",
	)
	return {name} | {old for changes in renames if (old := (as_data(changes) or {}).get("old_name"))}


def history(doctype: str, name: str) -> list[frappe._dict]:
	"""The record's audit events, newest first by their links."""
	events = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["name", "action", "user", "event_at", "reason", "changes", "previous_event"],
		order_by="event_at asc, creation asc",
	)
	return list(reversed(in_chain_order(events, TRAIL.link)))


def undo(doc: Document, changes: dict) -> None:
	"""Set `doc` back to what it was before an event, from what the event recorded: the inverse of
	`differences`. Used to tell whether the audit trail accounts for a change since a signature."""
	for field, old, _new in changes.get("changed", []):
		if field != "name":  # a rename is its own event, and no signature binds the name
			doc.set(field, old)
	for table, row_name, row_changes in changes.get("row_changed", []):
		for row in doc.get(table) or []:
			if row.name == row_name:
				for field, old, _new in row_changes:
					row.set(field, old)
	added = {(table, row.get("name")) for table, row in changes.get("added", [])}
	removed = changes.get("removed", [])
	for table in {table for table, _row in added} | {table for table, _row in removed}:
		rows = [row for row in doc.get(table) or [] if (table, row.name) not in added]
		rows += [doc.append(table, dict(row)) for removed_from, row in removed if removed_from == table]
		doc.set(table, sorted(rows, key=lambda row: cint(row.idx)))


def as_data(value: Any) -> Any:
	"""JSON fields come back from the database as text; compare their data."""
	return json.loads(value) if isinstance(value, str) else value
