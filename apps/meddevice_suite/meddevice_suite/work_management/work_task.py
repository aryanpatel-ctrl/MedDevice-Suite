"""Tasks and approvals (module 59; URS-PLT-016; DFD 9B: B1 create task, B2 assign task, B3 execute
task, B4 submit for approval, B5 approve or reject, B6 close task; data store D11).

A task outside the records that carry their own approvals (a revision, a change, a lot): any
business job raises it, assigns it to a person with a due date and names who approves it, never
the person assigned. The person assigned does it, says what was done and submits it for approval;
the approver approves it with an electronic signature, which closes it, or returns it with a
signed reason, and it is done again. Until it is approved, whoever raised it, or QA, may withdraw
it with a reason. A task is never deleted or cancelled; an approved task is final.

Its due date is tracked by the reminders (module 60, category Work Task): the person assigned, or
the approver once it is submitted, answers for it until it is approved or withdrawn.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import SHARED_ACCOUNTS, sign

DOCTYPE = "Work Task"
OPEN, SUBMITTED, RETURNED, APPROVED, WITHDRAWN = "Open", "Submitted", "Returned", "Approved", "Withdrawn"
IN_HAND = (OPEN, RETURNED)  # with the person assigned
NOT_FINISHED = (OPEN, SUBMITTED, RETURNED)
MANAGEMENT = ("Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager")
SEES_ALL = frozenset({QA_ROLE, *MANAGEMENT, "System Manager"})
# What the task is, set by whoever raised it (or QA) while it is in hand.
DEFINITION = (
	"subject",
	"description",
	"reference_doctype",
	"reference_name",
	"assigned_to",
	"approver",
	"due_date",
)
RESULT = ("result",)
# Set by the acts below only.
SET_BY_ACTS = ("status", "submitted_on", "return_reason", "approved_by", "approved_on", "withdrawn_reason")


def check(doc: Document) -> None:
	"""Work Task validate: two different, named people; and each part changed only by whom and
	when the task allows."""
	require(doc.assigned_to != doc.approver, _("The approver is not the person assigned."))
	for field in ("assigned_to", "approver"):
		user = doc.get(field)
		require(
			user not in SHARED_ACCOUNTS and frappe.db.get_value("User", user, "enabled"),
			_("{0} is a person's own active account.").format(_(doc.meta.get_label(field))),
		)
	if doc.is_new():
		for field in SET_BY_ACTS:
			doc.set(field, None)
		doc.status = OPEN
		require(not (doc.result or "").strip(), _("What was done is filled in by the person assigned."))
		return
	if doc.flags.work_task:
		return
	before = doc.get_doc_before_save()
	# Compared as stored values: the desk sends a date as text.
	changed = {
		field
		for field in (*DEFINITION, *RESULT, *SET_BY_ACTS)
		if not same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype)
	}
	user = frappe.session.user
	require(
		not changed & set(SET_BY_ACTS),
		_("The status of a task changes only by its acts."),
		frappe.PermissionError,
	)
	if changed:
		require(
			before.status in IN_HAND,
			_("{0} is {1}; it changes only as it is approved, returned or withdrawn.").format(
				doc.name, _(before.status).lower()
			),
			frappe.PermissionError,
		)
	require(
		not changed & set(DEFINITION) or user == doc.owner or is_qa(),
		_("Only whoever raised {0}, or QA, changes what it is.").format(doc.name),
		frappe.PermissionError,
	)
	require(
		not changed & set(RESULT) or user == before.assigned_to,
		_("Only the person assigned says what was done."),
		frappe.PermissionError,
	)


def guard_submit(doc: Document) -> None:
	require(
		doc.flags.work_task, _("A task is closed by its approver's signed approval."), frappe.PermissionError
	)


def act(doc: Document) -> None:
	doc.flags.work_task = True
	doc.save(ignore_permissions=True)  # the acts check who may take them, above all permissions


@frappe.whitelist()
def submit_for_approval(name: str, result: str) -> None:
	"""The person assigned says what was done and passes the task to its approver."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	require(
		frappe.session.user == doc.assigned_to,
		_("Only the person assigned submits {0}.").format(name),
		frappe.PermissionError,
	)
	require(doc.status in IN_HAND, _("{0} is {1}.").format(name, _(doc.status).lower()))
	result = (result or "").strip()
	require(result, _("Say what was done."))
	doc.result, doc.status, doc.submitted_on, doc.return_reason = result, SUBMITTED, now_datetime(), None
	act(doc)


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the approver approves the result, which closes the task."""
	require(doc.status == SUBMITTED, _("Only a task submitted for approval is approved."))
	require(
		frappe.session.user == doc.approver,
		_("Only the approver of {0} approves it.").format(doc.name),
		frappe.PermissionError,
	)
	require(
		frappe.session.user != doc.assigned_to,
		_("The person assigned does not approve their own task."),
		frappe.PermissionError,
	)
	doc.status, doc.approved_by, doc.approved_on = APPROVED, frappe.session.user, now_datetime()
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.work_task = True
	# Nobody holds Frappe's submit permission on a task: the signed approval is its only submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def return_for_rework(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the approver returns the task to the person assigned, saying why."""
	require(doc.status == SUBMITTED, _("Only a task submitted for approval is returned."))
	require(
		frappe.session.user == doc.approver,
		_("Only the approver of {0} returns it.").format(doc.name),
		frappe.PermissionError,
	)
	require(reason, _("Say what must be done again."))
	doc.status, doc.return_reason = RETURNED, reason
	sign(doc, "Rejected", "Return", password, reason)
	act(doc)


@frappe.whitelist()
def withdraw(name: str, reason: str) -> None:
	"""Whoever raised the task, or QA, withdraws it before it is approved, saying why."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	require(
		frappe.session.user == doc.owner or is_qa(),
		_("Only whoever raised {0}, or QA, withdraws it.").format(name),
		frappe.PermissionError,
	)
	require(
		doc.status in NOT_FINISHED, _("{0} is {1}; it is not withdrawn.").format(name, _(doc.status).lower())
	)
	reason = (reason or "").strip()
	require(reason, _("Say why it is withdrawn."))
	doc.status, doc.withdrawn_reason = WITHDRAWN, reason
	doc.flags.audit_reason = reason
	act(doc)


def query_conditions(user: str | None = None) -> str:
	"""Work Task list: QA, management and the administrator see all; everyone else the tasks they
	raised, are assigned or approve."""
	user = user or frappe.session.user
	if set(frappe.get_roles(user)) & SEES_ALL:
		return ""
	who = frappe.db.escape(user)
	return f"(`tabWork Task`.`owner` = {who} or `tabWork Task`.`assigned_to` = {who} or `tabWork Task`.`approver` = {who})"


def has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if set(frappe.get_roles(user)) & SEES_ALL:
		return True
	return user in (doc.owner, doc.assigned_to, doc.approver) or bool(doc.is_new())
