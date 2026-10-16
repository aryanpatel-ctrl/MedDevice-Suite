"""My Work (module 59; URS-PLT-017; DFD 9B Task and Approval across the modules): everything waiting
on the person who opens it, in one list, each line opening its record.

Each source below says what waits on whom: a revision to review or approve, a change request to
assess or approve, a lot to inspect or release, a master or supplier or licence waiting for QA, a
quotation or purchase order waiting for its approver, a task to do or approve, a device master
record, risk policy, risk assessment or risk report to sign, a qualification to approve, an open
reminder. Every source reads with the person's own permissions, so nobody is shown a record they
could not open; a source the person's jobs have no part in is not read at all, and a line is shown
only to someone the signed act would accept (an author is never asked to approve their own work).

Every signed act of the system (`compliance.signing.SIGNING_ACTIONS`) is accounted for below: listed
by a source (`LISTED_BY`), listed through the reminder kept for it (`REMINDED_AS`), or not a queue
at all, with the reason (`NOT_WAITING`). The tests hold the register of signed acts to these, so an
act added without its place in My Work fails them (found in the foundation review of 2026-10-12:
six approval queues were missing).
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import frappe
from frappe import _

from meddevice_suite.compliance.checks import QA_ROLE

QC_ROLE = "QC Inspector"
MANAGEMENT = ("Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager")


@dataclass(frozen=True)
class Waiting:
	what: str
	doctype: str
	name: str
	since: object = None
	due: object = None


def records(
	doctype: str, filters: dict, fields: tuple[str, ...] = ("name", "modified")
) -> list[frappe._dict]:
	"""The records the person may read that match; none of a record type they may not read, which
	Frappe's list would refuse outright, so one source a job does not see never empties the list."""
	if not frappe.has_permission(doctype, "read"):
		return []
	return frappe.get_list(doctype, filters=filters, fields=list(fields), limit=0, order_by="modified asc")


# --- The sources --------------------------------------------------------------------------------


def reminders(user: str, roles: set[str]) -> Iterator[Waiting]:
	for row in records(
		"Due Reminder",
		{"status": ["in", ["Open", "Overdue", "Escalated"]]},
		("name", "subject", "status", "raised_on", "due_date"),
	):
		yield Waiting(
			_("{0} ({1})").format(row.subject, _(row.status)),
			"Due Reminder",
			row.name,
			row.raised_on,
			row.due_date,
		)


def tasks(user: str, roles: set[str]) -> Iterator[Waiting]:
	for row in records(
		"Work Task",
		{"assigned_to": user, "status": ["in", ["Open", "Returned"]]},
		("name", "subject", "status", "modified", "due_date"),
	):
		what = _("Do the task again: {0}") if row.status == "Returned" else _("Do the task: {0}")
		yield Waiting(what.format(row.subject), "Work Task", row.name, row.modified, row.due_date)
	for row in records(
		"Work Task",
		{"approver": user, "status": "Submitted"},
		("name", "subject", "submitted_on", "due_date"),
	):
		yield Waiting(
			_("Approve the task: {0}").format(row.subject),
			"Work Task",
			row.name,
			row.submitted_on,
			row.due_date,
		)


def revisions(user: str, roles: set[str]) -> Iterator[Waiting]:
	for row in records(
		"Document Revision", {"status": "Draft", "author": user}, ("name", "document_title", "modified")
	):
		yield Waiting(
			_("Complete and sign as author: {0}").format(row.document_title),
			"Document Revision",
			row.name,
			row.modified,
		)
	for row in records(
		"Document Revision", {"status": "In Review", "reviewer": user}, ("name", "document_title", "modified")
	):
		yield Waiting(
			_("Review: {0}").format(row.document_title), "Document Revision", row.name, row.modified
		)
	if QA_ROLE in roles:
		for row in records(
			"Document Revision",
			{"status": "Awaiting Approval"},
			("name", "document_title", "modified", "author", "reviewer"),
		):
			if user not in (row.author, row.reviewer):  # QA approves, never its author or reviewer
				yield Waiting(
					_("Approve: {0}").format(row.document_title), "Document Revision", row.name, row.modified
				)


def change_requests(user: str, roles: set[str]) -> Iterator[Waiting]:
	for row in records("Change Request", {"status": "Draft", "owner": user}, ("name", "title", "modified")):
		yield Waiting(
			_("Submit for assessment: {0}").format(row.title), "Change Request", row.name, row.modified
		)
	fields = ("name", "title", "modified", "requested_by", "qa_approver")
	if QA_ROLE in roles:
		for status, what in (
			("Under Assessment", _("Assess: {0}")),
			("Awaiting Approval", _("Approve: {0}")),
		):
			for row in records("Change Request", {"status": status}, fields):
				# The person who raised a change never approves it.
				if status == "Under Assessment" or row.requested_by != user:
					yield Waiting(what.format(row.title), "Change Request", row.name, row.modified)
	if set(MANAGEMENT) & roles:
		for row in records("Change Request", {"status": "Awaiting Management Approval"}, fields):
			# Management's approval is a second person's: neither the requester nor QA's approver.
			if user not in (row.requested_by, row.qa_approver):
				yield Waiting(
					_("Approve as management: {0}").format(row.title),
					"Change Request",
					row.name,
					row.modified,
				)


def lots(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Received lots waiting on QC (certificate, inspection) or on QA (the decision), and stock set
	apart in the hold store, waiting on QA's decision whatever its lot's status (URS-QC-004)."""
	if not {QA_ROLE, QC_ROLE} & roles:
		return
	from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import (
		WAITING_FOR,
		execute,
	)

	for_qc = {WAITING_FOR["verification"], WAITING_FOR["inspection"]}
	for_qa = {WAITING_FOR["release"], WAITING_FOR["rejected"], WAITING_FOR["set_apart"]}
	_columns, rows = execute({})
	for row in rows:
		if (row["waiting_for"] in for_qc and QC_ROLE in roles) or (
			row["waiting_for"] in for_qa and QA_ROLE in roles
		):
			yield Waiting(
				_("Lot {0}: {1}").format(row["batch_no"], _(row["waiting_for"])),
				"Batch",
				row["batch_no"],
				row.get("since"),
			)


def customer_returns(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Goods a customer sent back, kept in the returns store until QA decides on them: back to stock
	once inspected, or rejected (URS-STK-010, plan D-25)."""
	if QA_ROLE not in roles:
		return
	from meddevice_suite.lot_control import lot_status, stores

	returns = frappe.get_all(
		"Warehouse", filters={stores.ROLE_FIELD: stores.RETURNS, "is_group": 0}, pluck="name"
	)
	# Every company's Returns store read at once, not the ledger once per store.
	for row in lot_status.stock_by_lot(warehouse=returns) if returns else []:
		yield Waiting(
			_("Lot {0}: decide on {1} returned to {2}").format(row.batch_no, row.qty, row.warehouse),
			"Batch",
			row.batch_no,
		)


def waiting_for_qa(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Masters (specifications among them), supplier decisions, customer and regulatory licences and
	training matrices in draft, waiting for QA's signature."""
	if QA_ROLE not in roles:
		return
	from meddevice_suite.master_data import equipment, item_master, master_approval

	drafts = (
		("Item", {item_master.APPROVAL_STATUS: item_master.DRAFT, "disabled": 0}, _("Approve the item")),
		("BOM", {"docstatus": 0, "is_active": 1}, _("Approve the bill of materials")),
		(
			"Asset",
			{equipment.APPROVAL_STATUS: equipment.DRAFT, "docstatus": ["<", 2]},
			_("Approve the equipment"),
		),
		("Operation", {master_approval.APPROVAL_STATUS: master_approval.DRAFT}, _("Approve the operation")),
		(
			"Workstation",
			{master_approval.APPROVAL_STATUS: master_approval.DRAFT},
			_("Approve the workstation"),
		),
		(
			"Routing",
			{master_approval.APPROVAL_STATUS: master_approval.DRAFT, "disabled": 0},
			_("Approve the routing"),
		),
		(
			"Warehouse",
			{master_approval.APPROVAL_STATUS: master_approval.DRAFT, "is_group": 0, "disabled": 0},
			_("Approve the store"),
		),
		# Module 6 (plan D-33): a specification, new or changed; a template naming no item is not one.
		(
			"Quality Inspection Template",
			{master_approval.APPROVAL_STATUS: master_approval.DRAFT, "mds_item": ["is", "set"]},
			_("Approve the specification"),
		),
		("Supplier Approval", {"docstatus": 0}, _("Decide the supplier")),
		("Customer Licence", {"docstatus": 0, "status": ["in", ["", None]]}, _("Verify the licence")),
		# Module 41: a licence counts only once QA verifies it, or rejects it, against its copy.
		("Regulatory Licence", {"docstatus": 0}, _("Verify the regulatory licence")),
		# Module 37: the Training Coordinator keeps a job's matrix; it applies once QA approves it.
		("Training Matrix", {"docstatus": 0}, _("Approve the training matrix")),
	)
	for doctype, filters, what in drafts:
		if not configured(doctype, filters):
			continue  # its approval field is not on this site yet
		for row in records(doctype, filters):
			yield Waiting(what, doctype, row.name, row.modified)


def configured(doctype: str, filters: dict) -> bool:
	meta = frappe.get_meta(doctype)
	return all(field == "docstatus" or meta.has_field(field) for field in filters)


def approvals(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Quotations and purchase orders waiting for their approver (module 48)."""
	for doctype, state, role, what in (
		("Quotation", "Awaiting Price Approval", "Sales Manager", _("Approve the price")),
		("Purchase Order", "Awaiting Approval", "Purchase Manager", _("Approve the order")),
	):
		if role in roles:
			for row in records(doctype, {"workflow_state": state, "docstatus": 0}):
				yield Waiting(what, doctype, row.name, row.modified)


def device_master_records(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""A device master record revision: its author's to sign while in draft, then QA's to approve,
	never the author's (module 31)."""
	from meddevice_suite.qms import device_master_record as dmr

	for row in records(
		dmr.DOCTYPE,
		{"docstatus": 0, "status": dmr.DRAFT, "author": user},
		("name", "item", "item_name", "modified"),
	):
		yield Waiting(
			_("Complete and sign as author: device master record of {0}").format(row.item_name or row.item),
			dmr.DOCTYPE,
			row.name,
			row.modified,
		)
	if QA_ROLE not in roles:
		return
	for row in records(
		dmr.DOCTYPE,
		{"docstatus": 0, "status": dmr.AWAITING_APPROVAL},
		("name", "item", "item_name", "author", "modified"),
	):
		if row.author != user:
			yield Waiting(
				_("Approve the device master record of {0}").format(row.item_name or row.item),
				dmr.DOCTYPE,
				row.name,
				row.modified,
			)


def risk_management(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Module 39: the acceptability policy, QA's to review while in draft and then Management's to
	approve, never its reviewer's; a risk assessment revision, its author's to sign, its reviewer's
	to review, then QA's to approve, never its author's or reviewer's; a risk management report, its
	author's to sign, then QA's to approve, never its author's."""
	from meddevice_suite.qms import risk_management as rm

	if QA_ROLE in roles:
		for row in records(rm.POLICY, {"docstatus": 0, "status": rm.DRAFT}, ("name", "title", "modified")):
			yield Waiting(
				_("Review the risk acceptability policy: {0}").format(row.title),
				rm.POLICY,
				row.name,
				row.modified,
			)
	if rm.MANAGEMENT_ROLES & roles:
		for row in records(
			rm.POLICY, {"docstatus": 0, "status": rm.REVIEWED}, ("name", "title", "reviewed_by", "modified")
		):
			if row.reviewed_by != user:
				yield Waiting(
					_("Approve the risk acceptability policy: {0}").format(row.title),
					rm.POLICY,
					row.name,
					row.modified,
				)
	for doctype, status, person, what in (
		(rm.ASSESSMENT, rm.DRAFT, "author", _("Complete and sign as author: risk assessment of {0}")),
		(rm.ASSESSMENT, rm.IN_REVIEW, "reviewer", _("Review the risk assessment of {0}")),
		(rm.REPORT, rm.DRAFT, "author", _("Complete and sign as author: risk management report of {0}")),
	):
		for row in records(
			doctype, {"docstatus": 0, "status": status, person: user}, ("name", "family_name", "modified")
		):
			yield Waiting(what.format(row.family_name), doctype, row.name, row.modified)
	if QA_ROLE not in roles:
		return
	for doctype, fields, what in (
		(rm.ASSESSMENT, ("author", "reviewer"), _("Approve the risk assessment of {0}")),
		(rm.REPORT, ("author",), _("Approve the risk management report of {0}")),
	):
		for row in records(
			doctype,
			{"docstatus": 0, "status": rm.AWAITING_APPROVAL},
			("name", "family_name", "modified", *fields),
		):
			if user not in (row.get(field) for field in fields):  # QA approves another's work only
				yield Waiting(what.format(row.family_name), doctype, row.name, row.modified)


def qualifications(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""An executed IQ, OQ or PQ waiting for QA's approval, never its performer's (module 40)."""
	if QA_ROLE not in roles:
		return
	for row in records(
		"Equipment Qualification",
		{"docstatus": 0, "executed_by": ["is", "set"]},
		("name", "asset", "stage", "executed_by", "modified"),
	):
		if row.executed_by != user:
			yield Waiting(
				_("Approve the {0} of {1}").format(row.stage, row.asset),
				"Equipment Qualification",
				row.name,
				row.modified,
			)


Source = Callable[[str, set[str]], Iterator[Waiting]]

SOURCES: tuple[Source, ...] = (
	reminders,
	tasks,
	revisions,
	change_requests,
	lots,
	customer_returns,
	waiting_for_qa,
	approvals,
	device_master_records,
	risk_management,
	qualifications,
)

# --- Every signed act accounted for ---------------------------------------------------------------

# Signed act (compliance.signing.SIGNING_ACTIONS) -> the source that lists the records waiting for
# it. An act that answers the same wait as another (Return beside Approve) is listed by the same
# source: the line asks the person to act, and the record offers each act it allows.
LISTED_BY: dict[tuple[str, str], Source] = {
	("Document Revision", "Author"): revisions,
	("Document Revision", "Review"): revisions,
	("Document Revision", "Return"): revisions,
	("Document Revision", "Approve"): revisions,
	("Change Request", "Assess"): change_requests,
	("Change Request", "Approve"): change_requests,
	("Change Request", "Return"): change_requests,
	("Change Request", "Reject"): change_requests,
	("Lot Release", "Release"): lots,
	("Lot Release", "Reject"): lots,
	("Lot Release", "Hold"): lots,
	("Lot Release", "Return to Stock"): customer_returns,
	("Lot Release", "Reject Return"): customer_returns,
	("Supplier Approval", "Approve"): waiting_for_qa,
	("Supplier Approval", "Approve Conditionally"): waiting_for_qa,
	("Supplier Approval", "Not Approve"): waiting_for_qa,
	("Item", "Approve"): waiting_for_qa,
	("Customer Licence", "Verify"): waiting_for_qa,
	("Customer Licence", "Reject"): waiting_for_qa,
	("BOM", "Approve"): waiting_for_qa,
	("Asset", "Approve"): waiting_for_qa,
	("Operation", "Approve"): waiting_for_qa,
	("Workstation", "Approve"): waiting_for_qa,
	("Routing", "Approve"): waiting_for_qa,
	("Warehouse", "Approve"): waiting_for_qa,
	("Quality Inspection Template", "Approve"): waiting_for_qa,
	("Work Task", "Approve"): tasks,
	("Work Task", "Return"): tasks,
	("Equipment Qualification", "Approve"): qualifications,
	("Equipment Qualification", "Return"): qualifications,
	("Training Matrix", "Approve"): waiting_for_qa,
	("Risk Acceptability Policy", "Review"): risk_management,
	("Risk Acceptability Policy", "Approve"): risk_management,
	("Risk Acceptability Policy", "Return"): risk_management,
	("Risk Assessment", "Author"): risk_management,
	("Risk Assessment", "Review"): risk_management,
	("Risk Assessment", "Return"): risk_management,
	("Risk Assessment", "Approve"): risk_management,
	("Risk Management Report", "Author"): risk_management,
	("Risk Management Report", "Return"): risk_management,
	("Risk Management Report", "Approve"): risk_management,
	("Regulatory Licence", "Verify"): waiting_for_qa,
	("Regulatory Licence", "Reject"): waiting_for_qa,
	("Device Master Record", "Author"): device_master_records,
	("Device Master Record", "Approve"): device_master_records,
	("Device Master Record", "Return"): device_master_records,
}

# Signed act -> the reminder category (work_management.reminders.CATEGORIES) whose open reminder
# lists the wait, with its person and due date; listing the record as well would show it twice.
REMINDED_AS: dict[tuple[str, str], str] = {
	("Change Request", "Verify Effective"): "Effectiveness Check",
	("Change Request", "Verify Not Effective"): "Effectiveness Check",
	("Calibration Record", "Assess: No Product Impact"): "Calibration Impact",
	("Calibration Record", "Assess: Product Action Needed"): "Calibration Impact",
	("Training Record", "Trained"): "Training Due",
	("Training Record", "Competent"): "Training Due",
	("Training Record", "Not Competent"): "Training Due",
	("Risk Review", "Close: No Change"): "Risk Review",
	# The periodic review falls due as its reminder (module 30); QA signs it as still current.
	("Controlled Document", "Review"): "Document Review",
}

# Signed acts nobody waits to do, so no queue lists them, each with the reason.
NOT_WAITING: dict[tuple[str, str], str] = {
	# QA withdraws a completed equipment record for correction as it finds the error (module 38);
	# the amendment it opens is then the performer's, as any draft record.
	("Calibration Record", "Correct"): "QA corrects a completed record as it finds the error",
	("Maintenance Record", "Correct"): "QA corrects a completed record as it finds the error",
	("Equipment Qualification", "Correct"): "QA corrects a completed record as it finds the error",
	# Signed by the person doing the work as they finish it; the work itself, when it falls due,
	# is tracked by its reminder (Calibration Due, Maintenance Due, Requalification Due).
	("Calibration Record", "Complete"): "signed by its performer as the calibration is done",
	("Maintenance Record", "Complete"): "signed by its performer as the maintenance is done",
	("Equipment Qualification", "Execute"): "signed by its performer as the protocol is executed",
	("Training Record", "Record History"): "QA enters paper training history as it is transcribed",
	# QA's or the owner's own decision, taken when it is decided: nothing is waiting for it.
	("Controlled Document", "Withdraw"): "QA retires a document when it is no longer used",
	("Customer Licence", "Withdraw"): "QA withdraws a licence the authority suspended or cancelled",
	("Customer", "Exempt From Licence"): "QA exempts a customer when the law does not require a licence",
	("Customer", "Require Licence"): "QA ends an exemption when it no longer applies",
	("Training Record", "Revoke"): "QA revokes a competence after a nonconformity or a failed check",
	("Risk Assessment", "Withdraw"): "QA abandons a revision that will not be finished",
	("Document Revision", "Abandon"): "QA abandons a revision that will not be finished",
	("Risk Management Report", "Withdraw"): "QA abandons a report that will not be finished",
	("Regulatory Licence", "Withdraw"): "QA withdraws a licence the authority suspended or cancelled",
	("Device Master Record", "Withdraw"): "QA withdraws the record of a product no longer made",
	("Batch", "Correct Dates"): "QA corrects a lot's dates as it finds the error",
}


def waiting_on(user: str | None = None) -> list[Waiting]:
	"""Everything waiting on the person, the longest-waiting first."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	found = [item for source in SOURCES for item in source(user, roles)]
	return sorted(found, key=lambda item: (str(item.due or "9999"), str(item.since or "")))
