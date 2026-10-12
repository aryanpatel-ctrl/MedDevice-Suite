"""Demo device master records (module 31), for the pilot site and the browser journey's test site.

A finished good is put into production only on its Current device master record (URS-QMS-031), so
each demo product gets one, made as the system makes it: the Production Supervisor drafts it with
its headings and its Packaging and Labelling document and signs it as its author; the QA Manager,
another person, approves it with a signature. The labelling document is issued through document
control and the product's in-process specification registered (module 6), if missing. The record
pins the risk management report in force of the product's family (plan D-23), which the caller
puts in force first (pilot/risk.py).

A product whose Current record still matches the live records is left as it is; one without a
record, or whose record has gone out of date, gets the next revision. A revision left part-way by
an earlier run is taken on from where it stopped, or withdrawn by QA if someone else drafted it.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe.model.document import Document

from meddevice_suite.pilot.documents import ensure_work_instruction
from meddevice_suite.pilot.inspection import ensure_in_process_specification
from meddevice_suite.qms import device_master_record as dmr

AUTHOR, APPROVER = "Production Supervisor", "QA Manager"
LABELLING_TITLE = "Labelling and instructions for use (demo)"
LABELLING_TYPE = "Packaging and Labelling"
SUMMARY = "Elements brought to the records now in force (demo)"
# What the demo writes under each heading; the client's medical device file replaces it.
HEADING_TEXT = {
	dmr.GENERAL: "Single-use device for drainage of the urinary bladder; general description, intended use and contraindications as in the instructions for use (demo).",
	dmr.LABELLING: "Unit label, carton label and instructions for use as in the Packaging and Labelling document named below (demo).",
	dmr.PRODUCT_SPECIFICATIONS: "Materials and components as in the bill of materials; dimensions and performance as in the final specification (demo).",
	dmr.MANUFACTURE: "Made on the routing below, each operation to its work instruction; packed, stored below 30 °C and dispatched as the labelling and storage condition state (demo).",
	dmr.MEASURING: "In-process and final inspection against the specifications below, on the sampling plans they state (demo).",
	dmr.RISK: "Risks managed under ISO 14971; the product family's risk management report in force is named below (demo).",
}
NOT_APPLICABLE = {
	dmr.INSTALLATION: "Not applicable: a sterile single-use device, neither installed nor serviced (demo).",
}
REASON = "Device master record checked (demo)"


def ensure_device_master_records(
	items: list[str],
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	labelling_title: str = LABELLING_TITLE,
) -> dict[str, str]:
	"""Each finished good's Current device master record; returns item -> record."""
	return {
		item: ensure_device_master_record(item, act_as, user_of, password, labelling_title) for item in items
	}


def ensure_device_master_record(
	item: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	labelling_title: str = LABELLING_TITLE,
) -> str:
	"""The item's Current device master record, drafted, signed and approved by the demo jobs."""
	current = dmr.current_record(item)
	if current and not dmr.changes_since_approval(current):
		return current
	ensure_in_process_specification(item)
	labelling = ensure_work_instruction(labelling_title, act_as, user_of, password, LABELLING_TYPE)
	author = user_of(AUTHOR)
	name = dmr.revision_in_progress(item)
	if name and frappe.db.get_value(dmr.DOCTYPE, name, "author") != author:
		with act_as(APPROVER):  # left by someone else: QA withdraws it, with the reason
			dmr.withdraw(
				frappe.get_doc(dmr.DOCTYPE, name),
				password=password,
				reason="Draft left by an earlier run (demo)",
			)
		name = None
	if not name:
		with act_as(AUTHOR):
			name = dmr.revise(item)
	if frappe.db.get_value(dmr.DOCTYPE, name, "status") == dmr.AWAITING_APPROVAL:
		# Signed by an earlier run against records that may have moved on: QA returns it, and its
		# author signs it again as it is assembled now.
		with act_as(APPROVER):
			dmr.return_for_changes(
				frappe.get_doc(dmr.DOCTYPE, name), password=password, reason="Taken on by a later run (demo)"
			)
	if frappe.db.get_value(dmr.DOCTYPE, name, "status") == dmr.DRAFT:
		with act_as(AUTHOR):
			fill(frappe.get_doc(dmr.DOCTYPE, name), labelling)
			dmr.author(frappe.get_doc(dmr.DOCTYPE, name), password=password, reason=REASON)
	with act_as(APPROVER):
		dmr.approve(frappe.get_doc(dmr.DOCTYPE, name), password=password, reason=REASON)
	return name


def fill(doc: Document, labelling: str) -> None:
	"""The author completes the draft: every heading written, the labelling document named."""
	for row in doc.headings:
		if not (row.content or "").strip():
			row.not_applicable = int(row.heading in NOT_APPLICABLE)
			row.content = NOT_APPLICABLE.get(row.heading) or HEADING_TEXT.get(row.heading)
	if labelling not in {row.controlled_document for row in doc.documents}:
		doc.append("documents", {"controlled_document": labelling, "heading": dmr.LABELLING})
	if doc.revision != "01" and not (doc.summary_of_change or "").strip():
		doc.summary_of_change = SUMMARY
	doc.save()
