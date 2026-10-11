"""Training status (module 37; URS-TRN-001, URS-TRN-003): for each active employee, each document
their job needs, on the revision in effect and on any approved to take effect later, whether they
are competent, in training, or not yet started, and until when the training is valid."""

import frappe
from frappe import _
from frappe.utils import getdate, today

from meddevice_suite.qms import training


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	frappe.has_permission("Training Record", "report", throw=True)
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 140,
		},
		{"fieldname": "employee_name", "label": _("Name"), "fieldtype": "Data", "width": 170},
		{
			"fieldname": "designation",
			"label": _("Job"),
			"fieldtype": "Link",
			"options": "Designation",
			"width": 140,
		},
		{
			"fieldname": "controlled_document",
			"label": _("Document"),
			"fieldtype": "Link",
			"options": "Controlled Document",
			"width": 150,
		},
		{"fieldname": "revision", "label": _("Revision"), "fieldtype": "Data", "width": 80},
		{"fieldname": "revision_status", "label": _("Revision Is"), "fieldtype": "Data", "width": 100},
		{"fieldname": "state", "label": _("Training"), "fieldtype": "Data", "width": 120},
		{"fieldname": "due_date", "label": _("Due"), "fieldtype": "Date", "width": 100},
		{"fieldname": "valid_until", "label": _("Valid Until"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "record",
			"label": _("Record"),
			"fieldtype": "Link",
			"options": "Training Record",
			"width": 140,
		},
	]
	employees = [filters.employee] if filters.get("employee") else None
	need = training.needed(employees)
	staff = {
		e.name: e
		for e in frappe.get_all(
			"Employee",
			filters={"name": ["in", list(need) or [""]]},
			fields=["name", "employee_name", "designation"],
		)
	}
	revisions = training.revisions_to_train({d for docs in need.values() for d in docs})
	records = frappe.get_all(
		"Training Record",
		filters={
			"employee": ["in", list(need) or [""]],
			"docstatus": ["<", 2],
			"status": ["not in", ("Superseded", "Withdrawn")],
		},
		fields=["name", "employee", "document_revision", "status", "due_date", "valid_until", "docstatus"],
		order_by="creation asc",
	)
	latest = {}
	for rec in records:
		latest[(rec.employee, rec.document_revision)] = rec
	labels = dict(
		frappe.get_all(
			"Document Revision",
			filters={"name": ["in", [r.name for revs in revisions.values() for r in revs] or [""]]},
			fields=["name", "revision"],
			as_list=True,
		)
	)
	rows = []
	for employee in sorted(need):
		person = staff[employee]
		for document in sorted(need[employee]):
			for rev in revisions.get(document, []):
				rec = latest.get((employee, rev.name)) or frappe._dict()
				state = rec.get("status") or _("Not started")
				if (
					rec.get("status") == training.COMPETENT
					and rec.valid_until
					and getdate(rec.valid_until) < getdate(today())
				):
					state = _("Expired")
				rows.append(
					{
						"employee": employee,
						"employee_name": person.employee_name,
						"designation": person.designation,
						"controlled_document": document,
						"revision": labels.get(rev.name),
						"revision_status": _("In effect")
						if rev.status == "Effective"
						else _("To take effect"),
						"state": _(state),
						"due_date": rec.get("due_date"),
						"valid_until": rec.get("valid_until"),
						"record": rec.get("name"),
					}
				)
	return columns, rows
