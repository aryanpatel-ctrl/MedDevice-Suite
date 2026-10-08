"""Stock by lot and status (module 17; URS-STK-006): what is in quarantine, on hold,
rejected or approved, where, and who set the status, when and why."""

import frappe
from frappe import _

from meddevice_suite.lot_control.lot_status import STATUS_FIELD, STATUSES, stock_by_lot


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	filters = frappe._dict(filters or {})
	columns = [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "batch_no", "label": _("Lot"), "fieldtype": "Link", "options": "Batch", "width": 200},
		{"fieldname": "lot_status", "label": _("Lot Status"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "warehouse",
			"label": _("Store"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 180,
		},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100},
		{"fieldname": "expiry_date", "label": _("Expiry"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "set_by",
			"label": _("Status Set By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 180,
		},
		{"fieldname": "set_at", "label": _("Status Set At"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "reason", "label": _("Status Reason"), "fieldtype": "Data", "width": 300},
	]
	holdings = stock_by_lot(filters.get("item_code"), filters.get("warehouse"))
	lots = {
		row.name: row
		for row in frappe.get_all(
			"Batch",
			filters={"name": ["in", [h.batch_no for h in holdings] or [""]]},
			fields=[
				"name",
				STATUS_FIELD,
				"expiry_date",
				"mds_status_set_by",
				"mds_status_set_at",
				"mds_status_reason",
			],
		)
	}
	data = []
	for h in holdings:
		lot = lots[h.batch_no]
		status = lot.get(STATUS_FIELD) or STATUSES[0]
		if filters.get("lot_status") and status != filters.lot_status:
			continue
		data.append(
			{
				"item_code": h.item_code,
				"batch_no": h.batch_no,
				"lot_status": _(status),
				"warehouse": h.warehouse,
				"qty": h.qty,
				"expiry_date": lot.expiry_date,
				"set_by": lot.mds_status_set_by,
				"set_at": lot.mds_status_set_at,
				"reason": lot.mds_status_reason,
			}
		)
	return columns, data
