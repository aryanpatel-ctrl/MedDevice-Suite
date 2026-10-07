"""Approval workflows (module 48), declared from the client's D1 flow map.

D1 marks two approval points that exist before any quality module is built:

- Sales: Quotation Preparation → Internal Price Approval? (Revision → Revise /
  Re-estimate) → Send Quote to Customer.
- Purchasing: Quote Compare / Final Vendor → Purchase Approval? (Revise) →
  Purchase Order.

Each becomes a Frappe workflow in which the approver's action submits the
record. Management approves, and every record needs approval rather than only
those above an amount, as D1 draws it: decided on the client's instruction of
2026-10-07. The rules Frappe's workflow alone does not enforce are enforced in
`compliance/approvals.py`.

The workflows are configuration as code: a declared workflow cannot be changed,
switched off or deleted on a site, and no other workflow can take over its record
type; every setting, including those the desk hides, is restored on migrate.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

DRAFT, CANCELLED, APPROVED = "Draft", "Cancelled", "Approved"

# name -> declaration. States: (state, docstatus, role allowed to edit in it).
# Transitions: (from, action, to, role allowed to take it).
WORKFLOWS: dict[str, dict] = {
	"Quotation Price Approval": {
		"document_type": "Quotation",
		"states": [
			(DRAFT, 0, "Sales User"),
			("Awaiting Price Approval", 0, "Sales Manager"),
			(APPROVED, 1, "Sales Manager"),
			(CANCELLED, 2, "Sales Manager"),
		],
		"transitions": [
			(DRAFT, "Send for Price Approval", "Awaiting Price Approval", "Sales User"),
			("Awaiting Price Approval", "Approve", APPROVED, "Sales Manager"),
			("Awaiting Price Approval", "Return for Revision", DRAFT, "Sales Manager"),
			# A customer's revision request is quoted again, and approved again (D1 "Revision").
			(APPROVED, "Cancel", CANCELLED, "Sales User"),
			(APPROVED, "Cancel", CANCELLED, "Sales Manager"),
		],
	},
	"Purchase Order Approval": {
		"document_type": "Purchase Order",
		"states": [
			(DRAFT, 0, "Purchase User"),
			("Awaiting Approval", 0, "Purchase Manager"),
			(APPROVED, 1, "Purchase Manager"),
			(CANCELLED, 2, "Purchase Manager"),
		],
		"transitions": [
			(DRAFT, "Send for Approval", "Awaiting Approval", "Purchase User"),
			("Awaiting Approval", "Approve", APPROVED, "Purchase Manager"),
			("Awaiting Approval", "Return for Revision", DRAFT, "Purchase Manager"),
			(APPROVED, "Cancel", CANCELLED, "Purchase Manager"),
		],
	},
}


# Settings the declaration does not use, held at Frappe's defaults so that none can be
# set on a site: a condition or task on a transition, a field a state writes.
STATE_DEFAULTS = {
	"update_field": None,
	"update_value": None,
	"message": None,
	"next_action_email_template": None,
	"is_optional_state": 0,
	"avoid_status_override": 0,
	"evaluate_as_expression": 0,
}
TRANSITION_DEFAULTS = {"condition": None, "send_email_to_creator": 0, "transition_tasks": None}


def apply_workflows() -> None:
	for name, declared in WORKFLOWS.items():
		for state, *_settings in declared["states"]:
			ensure("Workflow State", "workflow_state_name", state)
		for _source, action, *_target in declared["transitions"]:
			ensure("Workflow Action Master", "workflow_action_name", action)
		sync_workflow(name, declared)


def ensure(doctype: str, fieldname: str, value: str) -> None:
	if not frappe.db.exists(doctype, value):
		frappe.get_doc({"doctype": doctype, fieldname: value}).insert(ignore_permissions=True)  # setup code


def declared_workflow(name: str, declared: dict) -> dict:
	return {
		"workflow_name": name,
		"document_type": declared["document_type"],
		"is_active": 1,
		"override_status": 0,
		# Approvers are told by the alerts of module 51, not by Frappe's emails.
		"send_email_alert": 0,
		"enable_action_confirmation": 0,
		"workflow_state_field": "workflow_state",
		"states": [
			{
				"state": state,
				"doc_status": str(docstatus),
				"allow_edit": role,
				"send_email": 0,
				**STATE_DEFAULTS,
			}
			for state, docstatus, role in declared["states"]
		],
		"transitions": [
			{
				"state": source,
				"action": action,
				"next_state": target,
				"allowed": role,
				# Frappe allows self-approval unless told otherwise; the person who raised a
				# record may send it for approval, but never take the step that approves it.
				"allow_self_approval": int(not approves(declared, source, target)),
				**TRANSITION_DEFAULTS,
			}
			for source, action, target, role in declared["transitions"]
		],
	}


def approves(declared: dict, source: str, target: str) -> bool:
	"""A transition approves when it submits the record: from a draft state to a submitted one."""
	docstatus = {state: status for state, status, _editor in declared["states"]}
	return docstatus[source] == 0 and docstatus[target] == 1


def sync_workflow(name: str, declared: dict) -> None:
	wanted = declared_workflow(name, declared)
	if frappe.db.exists("Workflow", name):
		doc = frappe.get_doc("Workflow", name)
		if as_declared(doc) == wanted:
			return
		doc.update({key: value for key, value in wanted.items() if key not in ("states", "transitions")})
		doc.set("states", wanted["states"])
		doc.set("transitions", wanted["transitions"])
		doc.flags.declared_by_code = True
		doc.save(ignore_permissions=True)  # setup code
	else:
		doc = frappe.get_doc({"doctype": "Workflow", **wanted})
		doc.flags.declared_by_code = True
		doc.insert(ignore_permissions=True)  # setup code


def as_declared(doc) -> dict:
	"""The workflow as stored, in the shape of its declaration, to compare the two."""
	return {
		**{
			key: doc.get(key)
			for key in declared_workflow(doc.name, WORKFLOWS[doc.name])
			if key not in ("states", "transitions")
		},
		"states": [
			{
				"state": row.state,
				"doc_status": str(row.doc_status),
				"allow_edit": row.allow_edit,
				"send_email": cint(row.send_email),
				**stored(row, STATE_DEFAULTS),
			}
			for row in doc.states
		],
		"transitions": [
			{
				"state": row.state,
				"action": row.action,
				"next_state": row.next_state,
				"allowed": row.allowed,
				"allow_self_approval": cint(row.allow_self_approval),
				**stored(row, TRANSITION_DEFAULTS),
			}
			for row in doc.transitions
		],
	}


def stored(row: Document, defaults: dict) -> dict:
	# Blank text is stored as "" or NULL, a clear check as 0 or NULL: both are the default.
	return {
		key: (cint(row.get(key)) if isinstance(default, int) else row.get(key) or None)
		for key, default in defaults.items()
	}


def protect(doc: Document, method: str | None = None) -> None:
	"""Workflow before_validate and on_trash, ahead of Frappe's own validation (which
	writes to other workflows): only `apply_workflows` changes the declared workflows."""
	if doc.flags.declared_by_code or frappe.flags.in_install or frappe.flags.in_migrate:
		return
	declared_types = {declared["document_type"] for declared in WORKFLOWS.values()}
	if doc.name in WORKFLOWS:
		frappe.throw(
			_("The workflow {0} is part of the validated configuration and cannot be changed here.").format(
				doc.name
			),
			frappe.PermissionError,
		)
	if doc.document_type in declared_types and cint(doc.is_active) and method != "on_trash":
		frappe.throw(
			_("{0} is approved through its declared workflow; another workflow cannot replace it.").format(
				_(doc.document_type)
			),
			frappe.PermissionError,
		)
