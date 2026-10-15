"""Equipment care schedule (modules 38 and 40; URS-EQP-007; flowchart "calibration schedule",
"maintenance plan"): each approved piece of equipment's next calibration, maintenance and
requalification, whether each is in date, due soon, overdue or never done, and, when it is not fit
to use, why (an open repair, or a repair or move since it was last calibrated or qualified)."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.master_data import equipment
from meddevice_suite.qms.equipment_care import latest, unfit

NOT_NEEDED, NEVER, OVERDUE, DUE_SOON, IN_DATE = "Not needed", "Never done", "Overdue", "Due soon", "In date"


def state(needed: bool, due, warning_days: int) -> str:
	if not needed:
		return NOT_NEEDED
	if not due:
		return NEVER
	if getdate(due) < getdate(today()):
		return OVERDUE
	return DUE_SOON if getdate(due) <= getdate(add_days(today(), warning_days)) else IN_DATE


def qualification_state(needed: bool, last_pq, interval: int, due, warn: dict) -> str:
	if not needed:
		return NOT_NEEDED
	if not last_pq:
		return NEVER
	if not interval:
		return IN_DATE  # qualified, and it does not expire
	return state(True, due, warn["requalification"])


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	frappe.has_permission("Asset", "read", throw=True)
	columns = [
		{
			"fieldname": "asset",
			"label": _("Equipment"),
			"fieldtype": "Link",
			"options": "Asset",
			"width": 170,
		},
		{"fieldname": "asset_name", "label": _("Name"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "workstation",
			"label": _("Workstation"),
			"fieldtype": "Link",
			"options": "Workstation",
			"width": 150,
		},
		{"fieldname": "calibration_due", "label": _("Calibration Due"), "fieldtype": "Date", "width": 120},
		{"fieldname": "calibration", "label": _("Calibration"), "fieldtype": "Data", "width": 110},
		{"fieldname": "maintenance_due", "label": _("Maintenance Due"), "fieldtype": "Date", "width": 120},
		{"fieldname": "maintenance", "label": _("Maintenance"), "fieldtype": "Data", "width": 110},
		{
			"fieldname": "requalification_due",
			"label": _("Requalification Due"),
			"fieldtype": "Date",
			"width": 140,
		},
		{"fieldname": "qualification", "label": _("Qualification"), "fieldtype": "Data", "width": 110},
		{"fieldname": "not_fit", "label": _("Not Fit Because"), "fieldtype": "Data", "width": 320},
	]
	assets = frappe.get_list(
		"Asset",
		filters={
			equipment.APPROVAL_STATUS: equipment.APPROVED,
			equipment.STATUS: ["!=", equipment.RETIRED],
			"docstatus": ["<", 2],
		},
		fields=[
			"name",
			"asset_name",
			equipment.WORKSTATION,  # where it is now, rightly: the schedule is of today
			equipment.CALIBRATION,
			equipment.MAINTENANCE,
			equipment.QUALIFICATION,
			equipment.REQUALIFICATION_DAYS,
		],
		order_by="name asc",
		limit=0,
	)
	names = [a.name for a in assets]
	calibrated = latest("Calibration Record", names, ["next_due_date"]) if names else {}
	maintained = latest("Maintenance Record", names, ["next_due_date"]) if names else {}
	qualified = (
		latest("Equipment Qualification", names, [], {"stage": "PQ", "outcome": "Pass"}) if names else {}
	)
	not_fit = unfit(names)
	warn = {
		key: quality_settings.value(f"{key}_warning_days")
		for key in ("calibration", "maintenance", "requalification")
	}
	rows = []
	for a in assets:
		cal_due = (calibrated.get(a.name) or {}).get("next_due_date")
		pm_due = (maintained.get(a.name) or {}).get("next_due_date")
		pq = qualified.get(a.name)
		interval = cint(a.get(equipment.REQUALIFICATION_DAYS))
		requal_due = add_days(pq.performed_on, interval) if pq and interval else None
		needs_qualification = bool(cint(a.get(equipment.QUALIFICATION)))
		rows.append(
			{
				"asset": a.name,
				"asset_name": a.asset_name,
				"workstation": a.get(equipment.WORKSTATION),
				"calibration_due": cal_due,
				"calibration": _(state(cint(a.get(equipment.CALIBRATION)), cal_due, warn["calibration"])),
				"maintenance_due": pm_due,
				"maintenance": _(state(cint(a.get(equipment.MAINTENANCE)), pm_due, warn["maintenance"])),
				"requalification_due": requal_due,
				"qualification": _(qualification_state(needs_qualification, pq, interval, requal_due, warn)),
				"not_fit": "; ".join(not_fit.get(a.name) or []),
			}
		)
	return columns, rows
