"""My Work (module 59; URS-PLT-017; DFD 9B Task and Approval across the modules): everything waiting
on the person who opens it, in one list, each line opening its record.

Each source below says what waits on whom: a revision to review or approve, a change request to
assess or approve, a lot to inspect or release, a master or supplier or licence waiting for QA, a
quotation or purchase order waiting for its approver, a task to do or approve, an open reminder.
Every source reads with the person's own permissions, so nobody is shown a record they could not
open; a source the person's jobs have no part in is not read at all.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import frappe
from frappe import _

QA_ROLE = "Quality Manager"
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
	"""The records the person may read that match."""
	return frappe.get_list(doctype, filters=filters, fields=list(fields), limit=0, order_by="modified asc")


# --- The sources --------------------------------------------------------------------------------


def reminders(user: str, roles: set[str]) -> Iterator[Waiting]:
	for row in records(
		"Reminder",
		{"status": ["in", ["Open", "Overdue", "Escalated"]]},
		("name", "subject", "status", "raised_on", "due_date"),
	):
		yield Waiting(
			_("{0} ({1})").format(row.subject, _(row.status)),
			"Reminder",
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
	if QA_ROLE in roles:
		for status, what in (
			("Under Assessment", _("Assess: {0}")),
			("Awaiting Approval", _("Approve: {0}")),
		):
			for row in records("Change Request", {"status": status}, ("name", "title", "modified")):
				yield Waiting(what.format(row.title), "Change Request", row.name, row.modified)
	if set(MANAGEMENT) & roles:
		for row in records(
			"Change Request", {"status": "Awaiting Management Approval"}, ("name", "title", "modified")
		):
			yield Waiting(
				_("Approve as management: {0}").format(row.title), "Change Request", row.name, row.modified
			)


def lots(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Received lots waiting on QC (certificate, inspection) or on QA (the decision)."""
	if not {QA_ROLE, QC_ROLE} & roles:
		return
	from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import (
		WAITING_FOR,
		execute,
	)

	for_qc = {WAITING_FOR["verification"], WAITING_FOR["inspection"]}
	for_qa = {WAITING_FOR["release"], WAITING_FOR["rejected"]}
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


def waiting_for_qa(user: str, roles: set[str]) -> Iterator[Waiting]:
	"""Masters, supplier decisions and customer licences in draft, waiting for QA's signature."""
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
		("Supplier Approval", {"docstatus": 0}, _("Decide the supplier")),
		("Customer Licence", {"docstatus": 0, "status": ["in", ["", None]]}, _("Verify the licence")),
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


SOURCES: tuple[Callable[[str, set[str]], Iterator[Waiting]], ...] = (
	reminders,
	tasks,
	revisions,
	change_requests,
	lots,
	waiting_for_qa,
	approvals,
)


def waiting_on(user: str | None = None) -> list[Waiting]:
	"""Everything waiting on the person, the longest-waiting first."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	found = [item for source in SOURCES for item in source(user, roles)]
	return sorted(found, key=lambda item: (str(item.due or "9999"), str(item.since or "")))
