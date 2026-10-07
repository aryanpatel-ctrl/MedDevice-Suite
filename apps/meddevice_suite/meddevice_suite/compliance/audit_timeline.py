"""The audit trail on each regulated record's form (URS-CMP-012).

Users allowed to review the audit trail see the record's latest audit events in
its activity timeline, and an Audit Trail button that lists all of them, where
they can be filtered and exported, and a Verify Integrity button that checks the
record's audit and signature chains. Other users see the form as usual. Searching
across records by user, action and date range is the Audit Event list itself.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.utils import escape_html, get_fullname, get_link_to_form

from meddevice_suite.compliance.audit import as_data, verify_events
from meddevice_suite.compliance.audit_scope import is_regulated
from meddevice_suite.compliance.esign import verify_signatures

# The form loads quickly however long a record's history is; the button lists every event.
TIMELINE_LIMIT = 50
LINES_PER_EVENT = 5
VALUE_LENGTH = 80


def onload(doc: Document, method: str | None = None) -> None:
	if shows_audit_trail(doc.doctype):
		doc.set_onload("audit_trail", True)


@frappe.whitelist()
def verify_record(doctype: str, name: str) -> dict:
	"""For reviewers: whether the record's audit trail and signatures are intact (21 CFR 11.10(e))."""
	if not shows_audit_trail(doctype):
		frappe.throw(
			_("You cannot review the audit trail of {0}.").format(_(doctype)), frappe.PermissionError
		)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return {
		"audit_trail": verify_events(doctype, name),
		"signatures_intact": verify_signatures(doctype, name),
	}


def timeline_content(doctype: str, docname: str) -> list[dict]:
	if not shows_audit_trail(doctype):
		return []
	events = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": docname},
		fields=["name", "action", "user", "impersonated_by", "event_at", "reason", "changes"],
		order_by="event_at desc",
		limit=TIMELINE_LIMIT,
	)
	meta = frappe.get_meta(doctype)
	return [
		{"icon": "edit", "creation": event.event_at, "content": describe(event, meta)} for event in events
	]


def shows_audit_trail(doctype: str) -> bool:
	meta = frappe.get_meta(doctype)
	return (
		not meta.istable
		and is_regulated(doctype, meta.module)
		and bool(frappe.has_permission("Audit Event", "read"))
	)


def describe(event: frappe._dict, meta: Meta) -> str:
	who = escape_html(get_fullname(event.user))
	if event.impersonated_by:
		who = _("{0}, acting as {1}").format(escape_html(get_fullname(event.impersonated_by)), who)
	headline = _("{0} by {1}").format(f"<strong>{escape_html(_(event.action))}</strong>", who)
	details = get_link_to_form("Audit Event", event.name, _("Details"))

	lines = []
	if event.reason:
		lines.append(_("Reason: {0}").format(escape_html(event.reason)))
	changes = as_data(event.changes) or {}
	if caused_by := changes.get("caused_by"):
		lines.append(_("Caused by {0} {1}").format(_(caused_by[0]), escape_html(caused_by[1])))
	lines += change_lines(changes, meta)

	listed = lines[:LINES_PER_EVENT]
	if len(lines) > len(listed):
		listed.append(_("and {0} more").format(len(lines) - len(listed)))

	html = f"<div>{headline} · {details}</div>"
	if listed:
		html += '<ul class="text-muted small">' + "".join(f"<li>{line}</li>" for line in listed) + "</ul>"
	return html


def change_lines(changes: dict, meta: Meta) -> list[str]:
	if "old_name" in changes:
		return [
			_("Renamed from {0} to {1}").format(
				escape_html(changes["old_name"]), escape_html(changes["new_name"])
			)
		]

	lines = [
		_("{0}: {1} → {2}").format(escape_html(meta.get_translated_label(field)), shown(old), shown(new))
		for field, old, new in changes.get("changed", [])
	]

	rows: dict[str, dict[str, int]] = {}
	for kind in ("added", "removed", "row_changed"):
		for entry in changes.get(kind, []):
			counts = rows.setdefault(entry[0], {"added": 0, "removed": 0, "row_changed": 0})
			counts[kind] += 1
	for table, counts in rows.items():
		lines.append(
			_("{0}: {1} rows added, {2} changed, {3} removed").format(
				escape_html(meta.get_translated_label(table)),
				counts["added"],
				counts["row_changed"],
				counts["removed"],
			)
		)
	return lines


def shown(value) -> str:
	if value in (None, ""):
		return _("empty")
	text = str(value)
	if len(text) > VALUE_LENGTH:
		text = text[:VALUE_LENGTH] + "…"
	return escape_html(text)
