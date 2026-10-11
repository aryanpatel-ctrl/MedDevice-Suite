"""Lots taken out of expiry order (module 18; URS-STK-009): every submitted document that took a lot
while one expiring earlier was still in the store, with the reason it gave, for QA's review."""

import frappe
from frappe import _
from frappe.utils import getdate

from meddevice_suite.lot_control.expiry import CUSTOM_FIELDS, REASON_FIELD


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	filters = frappe._dict(filters or {})
	columns = [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "document_type",
			"label": _("Document Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 130,
		},
		{
			"fieldname": "document",
			"label": _("Document"),
			"fieldtype": "Dynamic Link",
			"options": "document_type",
			"width": 190,
		},
		{"fieldname": "reason", "label": _("FEFO Exception Reason"), "fieldtype": "Data", "width": 360},
		{"fieldname": "owner", "label": _("Made By"), "fieldtype": "Link", "options": "User", "width": 200},
	]
	rows = []
	for doctype in CUSTOM_FIELDS:
		if not frappe.has_permission(doctype, "read"):
			continue
		conditions = {"docstatus": 1, REASON_FIELD: ["is", "set"]}
		if filters.get("from_date"):
			conditions["posting_date"] = [">=", getdate(filters.from_date)]
		for row in frappe.get_list(
			doctype,
			filters=conditions,
			fields=["name", "posting_date", f"{REASON_FIELD} as reason", "owner"],
		):
			rows.append({**row, "document_type": doctype, "document": row.name})
	rows.sort(key=lambda row: (getdate(row["posting_date"]), row["document"]), reverse=True)
	return columns, rows
