"""Reason for change (URS-CMP-011; 21 CFR 11.10(e), ISO 13485 4.2.5).

Cancelling or amending a regulated record, or changing it after submission,
needs a reason, which is stored in that action's audit event. The desk asks for
it in a dialog and stages it here just before it sends the action; the audit
trail takes it when the action runs and refuses the action when there is none,
however the action arrives (desk, REST API, background job). Code in this app
that performs such an action itself sets `doc.flags.audit_reason` first.

An action often causes others in the same request: "Cancel All" cancels the
records linked to the one being cancelled, and ERPNext cancels dependent records
in its own hooks. Those take the reason of the action that caused them, and
their audit event names that record under `caused_by`. A submitted record that
ERPNext changes while saving another record (a job card updating its work
order) was not changed by hand: no reason is asked, and its event names that
other record.
"""

import sys

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.compliance.audit_scope import is_regulated
from meddevice_suite.compliance.fingerprint import plain_text

# Audit action -> the action a reason is staged for
STAGED_ACTION = {"Cancelled": "cancel", "Amended": "amend", "Updated after submission": "update_after_submit"}
PERMISSION_FOR = {"cancel": "cancel", "amend": "amend", "update_after_submit": "write"}
STAGE_SECONDS = 15 * 60
# Document methods that run while a record is being saved, submitted or cancelled.
SAVE_STEPS = frozenset({"insert", "_save", "run_before_save_methods", "run_post_save_methods"})
MAX_LENGTH = 1000
CANCEL_ALL_LINKED = "frappe.desk.form.linked_with.cancel_all_linked_docs"


@frappe.whitelist()
def requires_reason(doctype: str) -> bool:
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	return bool(meta.is_submittable) and not meta.istable and is_regulated(doctype, meta.module)


@frappe.whitelist(methods=["POST"])
def stage_reason(doctype: str, name: str, action: str, reason: str) -> None:
	"""Hold the user's reason for an action they are about to send on a record."""
	if action not in PERMISSION_FOR:
		frappe.throw(_("Unknown action {0}.").format(action))
	if not requires_reason(doctype):
		frappe.throw(_("{0} is not a regulated record type that needs a reason.").format(_(doctype)))
	frappe.has_permission(doctype, PERMISSION_FOR[action], doc=name, throw=True)

	reason = clean(reason)
	frappe.cache.set_value(staged_key(action, doctype, name), reason, expires_in_sec=STAGE_SECONDS)
	if action == "cancel":
		# "Cancel All" cancels the linked records in a request of its own, before this one.
		frappe.cache.set_value(
			staged_key("cancelling"),
			{"doctype": doctype, "name": name, "reason": reason},
			expires_in_sec=STAGE_SECONDS,
		)


def reason_for(doc: Document, action: str) -> tuple[str | None, list[str] | None]:
	"""The reason for `action` on `doc`, and the record whose action caused it; refuses the action
	when there is none. Called when the action starts and again when it is recorded."""
	if doc.flags.audit_reason_resolved:
		return doc.flags.audit_reason, doc.flags.audit_caused_by

	staged_action = STAGED_ACTION[action]
	name = doc.amended_from if action == "Amended" else doc.name
	reason, caused_by = doc.flags.audit_reason or take_staged(staged_action, doc.doctype, name), None
	if not reason:
		cause = frappe.flags.audit_cause or linked_cancellation(action)
		if cause:
			source = cause.doc or cause
			reason, caused_by = cause.reason, [source.doctype, source.name]
	if not reason and action == "Updated after submission" and (outer := saved_around(doc)):
		# ERPNext changes a submitted record as part of another record's action: a job card
		# rolls its completed quantity up to the work order. Nobody changed it by hand, so no
		# reason is asked; its event names the record whose action changed it.
		doc.flags.update(audit_reason=None, audit_caused_by=list(outer), audit_reason_resolved=True)
		return None, list(outer)
	if not reason:
		frappe.throw(
			_("Give a reason to {0} {1} {2}. It is recorded in the audit trail.").format(
				verb(action), _(doc.doctype), name
			),
			title=_("Reason required"),
		)

	doc.flags.update(audit_reason=clean(reason), audit_caused_by=caused_by, audit_reason_resolved=True)
	if not caused_by and not frappe.flags.audit_cause:
		frappe.flags.audit_cause = frappe._dict(doc=doc, reason=doc.flags.audit_reason)
	return doc.flags.audit_reason, caused_by


def saved_around(doc: Document) -> tuple[str, str] | None:
	"""Another record whose save is running around this record's, taken from the call stack.

	Frappe's own list of records being saved is not cleared when a save fails, so in a
	request that carries on after an error (a bulk job) it would name a record whose save
	has ended. The call stack only ever holds the saves that are really running."""
	frame = sys._getframe(1)
	while frame:
		if frame.f_code.co_name in SAVE_STEPS:
			outer = frame.f_locals.get("self")
			if isinstance(outer, Document) and (outer.doctype, outer.name) != (doc.doctype, doc.name):
				return outer.doctype, outer.name
		frame = frame.f_back
	return None


def reason_used(doc: Document) -> None:
	"""One reason explains one action: the next action on the same record object needs its own."""
	for flag in ("audit_reason", "audit_caused_by", "audit_reason_resolved"):
		doc.flags.pop(flag, None)


def action_finished(doc: Document) -> None:
	"""Actions later in the request are no longer caused by this record's action."""
	cause = frappe.flags.audit_cause
	if cause and cause.doc is doc:
		frappe.flags.audit_cause = None


def linked_cancellation(action: str) -> frappe._dict | None:
	if action == "Cancelled" and frappe.form_dict.get("cmd") == CANCEL_ALL_LINKED:
		cancelling = frappe.cache.get_value(staged_key("cancelling"))
		return frappe._dict(cancelling) if cancelling else None
	return None


def take_staged(action: str, doctype: str, name: str) -> str | None:
	key = staged_key(action, doctype, name)
	reason = frappe.cache.get_value(key)
	if reason:
		frappe.cache.delete_value(key)
		if action == "cancel":
			frappe.cache.delete_value(staged_key("cancelling"))
	return reason


def staged_key(*parts: str) -> str:
	# Per user: a reason staged by one person never explains another person's action.
	return ":".join(("meddevice_suite", "audit_reason", frappe.session.user, *parts))


def clean(reason: str | None) -> str:
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Enter a reason."), title=_("Reason required"))
	if len(reason) > MAX_LENGTH:
		frappe.throw(_("Keep the reason within {0} characters.").format(MAX_LENGTH))
	return plain_text(reason)


def verb(action: str) -> str:
	return {"Cancelled": _("cancel"), "Amended": _("amend"), "Updated after submission": _("change")}[action]
