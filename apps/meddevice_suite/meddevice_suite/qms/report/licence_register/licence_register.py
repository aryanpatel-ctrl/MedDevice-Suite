"""Licence register (module 41; URS-REG-001, URS-REG-004; DFD 9E): each licence of the plant with
its standing today. "Expired" and "Due for renewal" are worked out from the dates each time the
register is read, never stored, so the register cannot show a licence as valid past its end."""

import frappe
from frappe import _
from frappe.utils import getdate, today

from meddevice_suite.qms import regulatory_licence as rl

EXPIRED, DUE_FOR_RENEWAL, VALID = "Expired", "Due for renewal", "Valid"


def standing(row: frappe._dict, day, lead_day) -> str:
	"""A verified licence's standing on the day; any other licence's status as it is."""
	if row.status != rl.VERIFIED:
		return row.status
	if row.valid_until and getdate(row.valid_until) < day:
		return EXPIRED
	if row.valid_until and getdate(row.valid_until) <= lead_day:
		return DUE_FOR_RENEWAL
	return VALID


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	frappe.has_permission(rl.DOCTYPE, "report", throw=True)
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "name",
			"label": _("Licence"),
			"fieldtype": "Link",
			"options": rl.DOCTYPE,
			"width": 140,
		},
		{
			"fieldname": "licence_type",
			"label": _("Type"),
			"fieldtype": "Link",
			"options": rl.TYPE_DOCTYPE,
			"width": 180,
		},
		{"fieldname": "licence_number", "label": _("Number"), "fieldtype": "Data", "width": 150},
		{"fieldname": "issuing_authority", "label": _("Authority"), "fieldtype": "Data", "width": 160},
		{"fieldname": "valid_from", "label": _("Valid From"), "fieldtype": "Date", "width": 100},
		{"fieldname": "valid_until", "label": _("Valid Until"), "fieldtype": "Date", "width": 100},
		{"fieldname": "standing", "label": _("Standing"), "fieldtype": "Data", "width": 130},
		{
			"fieldname": "superseded_by",
			"label": _("Superseded By"),
			"fieldtype": "Link",
			"options": rl.DOCTYPE,
			"width": 140,
		},
	]
	day, lead_day = getdate(today()), rl.lead_day()
	query_filters = {"docstatus": ["<", 2]}
	if not filters.get("include_inactive"):
		query_filters["status"] = ["in", (rl.DRAFT, rl.VERIFIED)]
	rows = frappe.get_list(
		rl.DOCTYPE,
		filters=query_filters,
		fields=[
			"name",
			"licence_type",
			"licence_number",
			"issuing_authority",
			"valid_from",
			"valid_until",
			"status",
			"superseded_by",
		],
		order_by="valid_until asc",
		limit=0,
	)
	for row in rows:
		row.standing = _(standing(row, day, lead_day))
	return columns, rows
