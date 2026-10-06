"""Approvals that cannot be bypassed (module 48; 21 CFR 11.10(f), (g)).

Frappe checks a workflow's roles and its self-approval rule only when an action
is taken through the workflow buttons. Anyone with submit permission could
otherwise submit the record directly, through the API too, and Frappe would move
it straight to the approved state. So every submit of a record type with an
active workflow must be a workflow transition into the submitted state: from the
state the record was in, taken by a role allowed to take it, and never by the
person who raised the record.

Runs on submit, which no flag skips; a refused submit rolls back with the request.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import WorkflowPermissionError, get_workflow, get_workflow_name


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
