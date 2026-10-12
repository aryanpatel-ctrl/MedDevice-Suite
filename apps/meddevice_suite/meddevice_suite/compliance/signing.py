"""Signing from the desk, and the signatures shown with a record (module 49).

Every act that can be signed in the system is declared in SIGNING_ACTIONS: the
record type, the act as the signer sees it, the meaning of the signature, and
the function that checks the act is allowed, signs through `esign.sign` and
performs the act. The desk's signing dialog can only reach the acts listed
here, so the list is also the system's register of signed acts.

The declared meaning is the one recorded (21 CFR 11.50(a)(3)): the dialog sends the
meaning it showed, which must be the declared one, and `esign.sign` refuses to sign a
declared act with any other, so the dialog, the register and the act cannot drift apart.

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
CALIBRATION = "meddevice_suite.qms.calibration"
MAINTENANCE = "meddevice_suite.qms.preventive_maintenance"
QUALIFICATION = "meddevice_suite.qms.qualification"
TRAINING = "meddevice_suite.qms.training"
RISK = "meddevice_suite.qms.risk_management"
REGULATORY_LICENCE = "meddevice_suite.qms.regulatory_licence"
DEVICE_MASTER_RECORD = "meddevice_suite.qms.device_master_record"

# (record type, act) -> the signed act. Each module adds the acts it signs.
SIGNING_ACTIONS: dict[tuple[str, str], SignedAct] = {
	# Module 30, document control
	("Document Revision", "Author"): SignedAct("Authored", f"{DOCUMENT_CONTROL}.author"),
	("Document Revision", "Review"): SignedAct("Reviewed", f"{DOCUMENT_CONTROL}.review"),
	("Document Revision", "Return"): SignedAct("Rejected", f"{DOCUMENT_CONTROL}.return_for_changes"),
	("Document Revision", "Approve"): SignedAct("Approved", f"{DOCUMENT_CONTROL}.approve"),
	("Controlled Document", "Withdraw"): SignedAct("Withdrawn", f"{DOCUMENT_CONTROL}.withdraw"),
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
	# Ending an exemption QA approved revokes that approval, signed as a revoked competence is.
	("Customer", "Require Licence"): SignedAct("Rejected", f"{CUSTOMER_LICENCE}.require_licence_again"),
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
	("Calibration Record", "Complete"): SignedAct("Verified", f"{CALIBRATION}.complete"),
	("Calibration Record", "Assess: No Product Impact"): SignedAct(
		"Reviewed", f"{CALIBRATION}.assess_no_impact"
	),
	("Calibration Record", "Assess: Product Action Needed"): SignedAct(
		"Reviewed", f"{CALIBRATION}.assess_product_action"
	),
	("Maintenance Record", "Complete"): SignedAct("Verified", f"{MAINTENANCE}.complete"),
	("Equipment Qualification", "Execute"): SignedAct("Verified", f"{QUALIFICATION}.execute"),
	("Equipment Qualification", "Approve"): SignedAct("Approved", f"{QUALIFICATION}.approve"),
	("Equipment Qualification", "Return"): SignedAct("Rejected", f"{QUALIFICATION}.return_for_rework"),
	# Module 37, training
	("Training Matrix", "Approve"): SignedAct("Approved", f"{TRAINING}.approve_matrix"),
	("Training Record", "Trained"): SignedAct("Trained", f"{TRAINING}.sign_trained"),
	("Training Record", "Competent"): SignedAct("Competent", f"{TRAINING}.sign_competent"),
	("Training Record", "Not Competent"): SignedAct("Rejected", f"{TRAINING}.sign_not_competent"),
	("Training Record", "Record History"): SignedAct("Verified", f"{TRAINING}.record_history"),
	("Training Record", "Revoke"): SignedAct("Rejected", f"{TRAINING}.revoke"),
	# Module 39, risk management: QA reviews the policy and Management approves it (plan D-21)
	("Risk Acceptability Policy", "Review"): SignedAct("Reviewed", f"{RISK}.review_policy"),
	("Risk Acceptability Policy", "Approve"): SignedAct("Approved", f"{RISK}.approve_policy"),
	("Risk Acceptability Policy", "Return"): SignedAct("Rejected", f"{RISK}.return_policy"),
	("Risk Assessment", "Author"): SignedAct("Authored", f"{RISK}.author"),
	("Risk Assessment", "Review"): SignedAct("Reviewed", f"{RISK}.review"),
	("Risk Assessment", "Return"): SignedAct("Rejected", f"{RISK}.return_for_changes"),
	("Risk Assessment", "Approve"): SignedAct("Approved", f"{RISK}.approve"),
	("Risk Assessment", "Withdraw"): SignedAct("Rejected", f"{RISK}.withdraw"),
	("Risk Management Report", "Author"): SignedAct("Authored", f"{RISK}.author_report"),
	("Risk Management Report", "Return"): SignedAct("Rejected", f"{RISK}.return_report"),
	("Risk Management Report", "Approve"): SignedAct("Approved", f"{RISK}.approve_report"),
	("Risk Management Report", "Withdraw"): SignedAct("Rejected", f"{RISK}.withdraw"),
	("Risk Review", "Close: No Change"): SignedAct("Reviewed", f"{RISK}.close_review"),
	# Module 41, regulatory licences
	("Regulatory Licence", "Verify"): SignedAct("Verified", f"{REGULATORY_LICENCE}.verify"),
	("Regulatory Licence", "Reject"): SignedAct("Rejected", f"{REGULATORY_LICENCE}.reject"),
	("Regulatory Licence", "Withdraw"): SignedAct("Rejected", f"{REGULATORY_LICENCE}.withdraw"),
	# Module 31, the device master record: its author signs it and QA, another person, approves it
	("Device Master Record", "Author"): SignedAct("Authored", f"{DEVICE_MASTER_RECORD}.author"),
	("Device Master Record", "Approve"): SignedAct("Approved", f"{DEVICE_MASTER_RECORD}.approve"),
	("Device Master Record", "Return"): SignedAct("Rejected", f"{DEVICE_MASTER_RECORD}.return_for_changes"),
	("Device Master Record", "Withdraw"): SignedAct("Rejected", f"{DEVICE_MASTER_RECORD}.withdraw"),
}


@frappe.whitelist(methods=["POST"])
def sign_action(
	doctype: str,
	name: str,
	action: str,
	password: str,
	reason: str | None = None,
	meaning: str | None = None,
) -> None:
	"""Sign `action` on a record, from the signing dialog.

	`meaning` is the meaning the dialog showed the signer; a page loaded before the declaration
	changed may show another, and nobody signs under a meaning they were not shown.
	"""
	signed_act = SIGNING_ACTIONS.get((doctype, action))
	if not signed_act:
		frappe.throw(_("{0} cannot be signed on {1}.").format(action, _(doctype)), frappe.PermissionError)
	if meaning and meaning != signed_act.meaning:
		frappe.throw(
			_("{0} is now signed as {1}, not as {2}. Reload the page and sign again.").format(
				_(action), _(signed_act.meaning), _(meaning)
			)
		)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	frappe.get_attr(signed_act.perform)(doc, password=password, reason=(reason or "").strip() or None)


def declared_meaning(doctype: str, action: str) -> str | None:
	"""The meaning a declared signed act is signed with; None for an act not declared here."""
	signed_act = SIGNING_ACTIONS.get((doctype, action))
	return signed_act.meaning if signed_act else None


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
