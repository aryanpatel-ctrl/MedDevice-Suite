"""Signing from the desk, and the signatures shown with a record (module 49).

Every act that can be signed in the system is declared in SIGNING_ACTIONS: the
record type, the act as the signer sees it, the meaning of the signature, and
the function that checks the act is allowed, signs through `esign.sign` and
performs the act. The desk's signing dialog can only reach the acts listed
here, so the list is also the system's register of signed acts.

The signatures on a record are shown on its form (`onload`) and are available
to its print formats (`signatures_for_print`), with the signer's name, the date
and time and the meaning (21 CFR 11.50, URS-CMP-003).
"""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.compliance.audit_scope import is_regulated


@dataclass(frozen=True)
class SignedAct:
	meaning: str  # one of esign.MEANINGS
	perform: str  # dotted path of perform(doc, password, reason) -> None: checks, signs, acts


DOCUMENT_CONTROL = "meddevice_suite.qms.document_control"
CHANGE_CONTROL = "meddevice_suite.qms.change_control"
LOT_RELEASE = "meddevice_suite.lot_control.release"
SUPPLIER_APPROVAL = "meddevice_suite.master_data.supplier_approval"
ITEM_MASTER = "meddevice_suite.master_data.item_master"
CUSTOMER_LICENCE = "meddevice_suite.master_data.customer_licence"
BOM = "meddevice_suite.master_data.bom"
EQUIPMENT = "meddevice_suite.master_data.equipment"
ROUTING = "meddevice_suite.master_data.routing"
WAREHOUSE = "meddevice_suite.master_data.warehouse"
WORK_TASK = "meddevice_suite.work_management.work_task"

# (record type, act) -> the signed act. Each module adds the acts it signs.
SIGNING_ACTIONS: dict[tuple[str, str], SignedAct] = {
	# Module 30, document control
	("Document Revision", "Author"): SignedAct("Authored", f"{DOCUMENT_CONTROL}.author"),
	("Document Revision", "Review"): SignedAct("Reviewed", f"{DOCUMENT_CONTROL}.review"),
	("Document Revision", "Return"): SignedAct("Rejected", f"{DOCUMENT_CONTROL}.return_for_changes"),
	("Document Revision", "Approve"): SignedAct("Approved", f"{DOCUMENT_CONTROL}.approve"),
	("Controlled Document", "Withdraw"): SignedAct("Approved", f"{DOCUMENT_CONTROL}.withdraw"),
	# Module 36, change control
	("Change Request", "Assess"): SignedAct("Reviewed", f"{CHANGE_CONTROL}.assess"),
	("Change Request", "Approve"): SignedAct("Approved", f"{CHANGE_CONTROL}.approve"),
	("Change Request", "Return"): SignedAct("Rejected", f"{CHANGE_CONTROL}.return_for_changes"),
	("Change Request", "Reject"): SignedAct("Rejected", f"{CHANGE_CONTROL}.reject"),
	("Change Request", "Verify Effective"): SignedAct("Verified", f"{CHANGE_CONTROL}.verify_effective"),
	("Change Request", "Verify Not Effective"): SignedAct(
		"Verified", f"{CHANGE_CONTROL}.verify_not_effective"
	),
	# Modules 16 and 17, the decision on a lot
	("Lot Release", "Release"): SignedAct("Released", f"{LOT_RELEASE}.release"),
	("Lot Release", "Reject"): SignedAct("Rejected", f"{LOT_RELEASE}.reject"),
	("Lot Release", "Hold"): SignedAct("Approved", f"{LOT_RELEASE}.hold"),
	# Module 4, the decision on a supplier
	("Supplier Approval", "Approve"): SignedAct("Approved", f"{SUPPLIER_APPROVAL}.approve"),
	("Supplier Approval", "Approve Conditionally"): SignedAct(
		"Approved", f"{SUPPLIER_APPROVAL}.approve_conditionally"
	),
	("Supplier Approval", "Not Approve"): SignedAct("Rejected", f"{SUPPLIER_APPROVAL}.not_approve"),
	# Module 1, the approval of an item
	("Item", "Approve"): SignedAct("Approved", f"{ITEM_MASTER}.approve"),
	# Module 5, a customer's licence
	("Customer Licence", "Verify"): SignedAct("Verified", f"{CUSTOMER_LICENCE}.verify"),
	("Customer Licence", "Reject"): SignedAct("Rejected", f"{CUSTOMER_LICENCE}.reject"),
	("Customer Licence", "Withdraw"): SignedAct("Rejected", f"{CUSTOMER_LICENCE}.withdraw"),
	("Customer", "Exempt From Licence"): SignedAct("Approved", f"{CUSTOMER_LICENCE}.exempt_from_licence"),
	("Customer", "Require Licence"): SignedAct("Approved", f"{CUSTOMER_LICENCE}.require_licence_again"),
	# Module 2, the approval of a bill of materials
	("BOM", "Approve"): SignedAct("Approved", f"{BOM}.approve"),
	# Module 8, the approval of equipment
	("Asset", "Approve"): SignedAct("Approved", f"{EQUIPMENT}.approve"),
	# Module 3, the approval of operations, workstations and routings
	("Operation", "Approve"): SignedAct("Approved", f"{ROUTING}.approve"),
	("Workstation", "Approve"): SignedAct("Approved", f"{ROUTING}.approve"),
	("Routing", "Approve"): SignedAct("Approved", f"{ROUTING}.approve"),
	# Module 7, the approval of a store
	("Warehouse", "Approve"): SignedAct("Approved", f"{WAREHOUSE}.approve"),
	("Work Task", "Approve"): SignedAct("Approved", f"{WORK_TASK}.approve"),
	("Work Task", "Return"): SignedAct("Rejected", f"{WORK_TASK}.return_for_rework"),
}


@frappe.whitelist(methods=["POST"])
def sign_action(doctype: str, name: str, action: str, password: str, reason: str | None = None) -> None:
	"""Sign `action` on a record, from the signing dialog."""
	signed_act = SIGNING_ACTIONS.get((doctype, action))
	if not signed_act:
		frappe.throw(_("{0} cannot be signed on {1}.").format(action, _(doctype)), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	frappe.get_attr(signed_act.perform)(doc, password=password, reason=(reason or "").strip() or None)


@frappe.whitelist()
def signing_actions(doctype: str) -> dict[str, str]:
	"""The acts the dialog may offer on a record type, with their meanings."""
	frappe.has_permission(doctype, "read", throw=True)
	return {
		action: act.meaning
		for (signed_doctype, action), act in SIGNING_ACTIONS.items()
		if signed_doctype == doctype
	}


def signatures_of(doctype: str, name: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["signer", "signer_name", "meaning", "action", "signed_at", "reason"],
		order_by="signed_at asc, creation asc",
	)


def onload(doc: Document, method: str | None = None) -> None:
	# Anyone who may open a regulated record sees who signed it, when and why.
	if doc.meta.istable or not is_regulated(doc.doctype, doc.meta.module):
		return
	if signatures := signatures_of(doc.doctype, doc.name):
		doc.set_onload("signatures", signatures)


def signatures_for_print(doc: Document) -> list[frappe._dict]:
	"""For print formats: `{% for s in signatures_for_print(doc) %}…{% endfor %}`."""
	return signatures_of(doc.doctype, doc.name)
