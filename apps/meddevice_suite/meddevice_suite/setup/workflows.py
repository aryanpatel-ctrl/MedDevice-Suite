"""Approval workflows (module 48), declared from the client's D1 flow map.

D1 marks two approval points that exist before any quality module is built:

- Sales: Quotation Preparation → Internal Price Approval? (Revision → Revise /
  Re-estimate) → Send Quote to Customer.
- Purchasing: Quote Compare / Final Vendor → Purchase Approval? (Revise) →
  Purchase Order.

Each becomes a Frappe workflow in which the approver's action submits the
record. The approver roles are proposals pending the client (requirement
baseline, question 3), as is approval of every record rather than above an
amount. The rules Frappe's workflow alone does not enforce are enforced in
`compliance/approvals.py`.
"""

import frappe

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


def apply_workflows() -> None:
	for name, declared in WORKFLOWS.items():
		for state, *_ in declared["states"]:
			ensure("Workflow State", "workflow_state_name", state)
		for _, action, *_ in declared["transitions"]:
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
			{"state": state, "doc_status": str(docstatus), "allow_edit": role, "send_email": 0}
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
			}
			for source, action, target, role in declared["transitions"]
		],
	}


def approves(declared: dict, source: str, target: str) -> bool:
	"""A transition approves when it submits the record: from a draft state to a submitted one."""
	docstatus = {state: status for state, status, _ in declared["states"]}
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
		doc.save(ignore_permissions=True)  # setup code
	else:
		frappe.get_doc({"doctype": "Workflow", **wanted}).insert(ignore_permissions=True)  # setup code


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
				"send_email": row.send_email,
			}
			for row in doc.states
		],
		"transitions": [
			{
				"state": row.state,
				"action": row.action,
				"next_state": row.next_state,
				"allowed": row.allowed,
				"allow_self_approval": row.allow_self_approval,
			}
			for row in doc.transitions
		],
	}
