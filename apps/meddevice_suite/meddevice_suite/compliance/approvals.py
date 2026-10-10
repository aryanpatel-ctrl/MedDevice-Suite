"""Approvals that cannot be bypassed (module 48; 21 CFR 11.10(f), (g)).

Frappe checks a workflow's roles and its self-approval rule only when an action
is taken through the workflow buttons. Anyone with submit permission could
otherwise submit the record directly, through the API too, and Frappe would move
it straight to the approved state. So every submit of a record type with an
active workflow must be a workflow transition into the submitted state: from the
state the record was in, taken by a role allowed to take it, and never by the
person who raised the record.

Runs on submit, which no flag skips; a refused submit rolls back with the request.

Frappe also leaves three more things to the desk's buttons, which the API does not
press, so they are enforced here on every save:

- a record's state always has the docstatus the workflow gives it, so a draft is
  never labelled approved and an approved record never labelled cancelled;
- while a record is in a state, only the role allowed to edit in that state
  changes it, so a requester cannot alter what the approver is about to approve;
- once approved, what was approved (items, quantities, prices, totals) stands.
  ERPNext's "Update Items" would change it in place; a change is made by
  cancelling and amending instead, and the amendment is approved again.
"""

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import WorkflowPermissionError, get_workflow, get_workflow_name
from frappe.utils import cint

# What an approver approves on a record; none of it changes after approval.
APPROVED_TOTALS = (
	"total_qty",
	"net_total",
	"grand_total",
	"taxes_and_charges",
	"apply_discount_on",
	"additional_discount_percentage",
	"discount_amount",
)
APPROVED_ITEM_FIELDS = ("item_code", "qty", "uom", "conversion_factor", "rate", "discount_percentage")
# A tax row changed in a way that leaves the totals as they were is still a change of the terms.
APPROVED_TAX_FIELDS = ("charge_type", "account_head", "rate", "tax_amount", "included_in_print_rate")


def on_submit(doc: Document, method: str | None = None) -> None:
	if not get_workflow_name(doc.doctype):
		return

	workflow = get_workflow(doc.doctype)
	before = doc.get_doc_before_save()
	source = before.get(workflow.workflow_state_field) if before else workflow.states[0].state
	target = doc.get(workflow.workflow_state_field)
	transitions = [row for row in workflow.transitions if row.state == source and row.next_state == target]
	if not transitions:
		frappe.throw(
			_("{0} {1} can only be submitted by approving it.").format(_(doc.doctype), doc.name),
			WorkflowPermissionError,
			title=_("Approval required"),
		)

	allowed = [row for row in transitions if row.allowed in frappe.get_roles()]
	if not allowed:
		frappe.throw(
			_("You are not allowed to approve {0} {1}.").format(_(doc.doctype), doc.name),
			WorkflowPermissionError,
			title=_("Approval required"),
		)
	if doc.owner == frappe.session.user and not any(row.allow_self_approval for row in allowed):
		frappe.throw(
			_("You cannot approve {0} {1}, because you raised it.").format(_(doc.doctype), doc.name),
			WorkflowPermissionError,
			title=_("Approval required"),
		)


def validate(doc: Document, method: str | None = None) -> None:
	if not get_workflow_name(doc.doctype) or doc._action != "save" or set_up_by_code():
		return

	workflow = get_workflow(doc.doctype)
	field = workflow.workflow_state_field
	states = {row.state: row for row in workflow.states}
	target = doc.get(field) or workflow.states[0].state
	if doc.is_new():
		require(
			target == workflow.states[0].state,
			_("A new {0} starts as {1}.").format(_(doc.doctype), _(workflow.states[0].state)),
		)
		return

	before = doc.get_doc_before_save()
	require(
		target in states and int(states[target].doc_status) == doc.docstatus,
		_("{0} {1} cannot be marked {2} without the action that makes it so.").format(
			_(doc.doctype), doc.name, _(target)
		),
	)
	current = before.get(field) if before else target
	editor = states[current].allow_edit if current in states else None
	require(
		editor in frappe.get_roles(),
		_("{0} {1} is {2}; only {3} can change it now.").format(
			_(doc.doctype), doc.name, _(current), _(editor)
		),
	)


def before_update_after_submit(doc: Document, method: str | None = None) -> None:
	if not get_workflow_name(doc.doctype) or set_up_by_code():
		return
	before = doc.get_doc_before_save()
	if before and approved_terms(before) != approved_terms(doc):
		frappe.throw(
			_(
				"{0} {1} has been approved; its items, quantities, prices, taxes and discounts cannot change. "
				"Cancel and amend it, and the amendment is approved again."
			).format(_(doc.doctype), doc.name),
			WorkflowPermissionError,
			title=_("Approval required"),
		)


def approved_terms(doc: Document) -> tuple:
	items = sorted(
		(row.name, *(flt_or_value(row.get(field)) for field in APPROVED_ITEM_FIELDS))
		for row in doc.get("items")
	)
	taxes = [
		tuple(
			cint(row.get(field)) if field == "included_in_print_rate" else flt_or_value(row.get(field))
			for field in APPROVED_TAX_FIELDS
		)
		for row in doc.get("taxes") or []
	]
	return (tuple(flt_or_value(doc.get(field)) for field in APPROVED_TOTALS), tuple(items), tuple(taxes))


def flt_or_value(value: Any) -> Any:
	# Numbers compared at a fixed precision, so float noise from recalculation is not a change.
	return round(float(value), 6) if isinstance(value, int | float) else value


def set_up_by_code() -> bool:
	return bool(frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch)


def require(condition, message: str) -> None:
	if not condition:
		frappe.throw(message, WorkflowPermissionError, title=_("Approval required"))
