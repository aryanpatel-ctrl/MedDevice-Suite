"""Risk register (module 39; URS-QMS-019 to URS-QMS-021): each hazard of the risk assessment in force
of each risk management file, with its risk before and after control and the controls that answer
it, as approved. Drafts and superseded revisions are left out: the register is what is in force."""

import frappe
from frappe import _

from meddevice_suite.qms.risk_management import ASSESSMENT, CONTROL, FILE, HAZARD, IN_FORCE


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	frappe.has_permission(ASSESSMENT, "report", throw=True)
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "risk_management_file",
			"label": _("Risk Management File"),
			"fieldtype": "Link",
			"options": FILE,
			"width": 120,
		},
		{"fieldname": "family_name", "label": _("Product Family"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "risk_assessment",
			"label": _("Revision"),
			"fieldtype": "Link",
			"options": ASSESSMENT,
			"width": 130,
		},
		{"fieldname": "hazard_id", "label": _("Hazard"), "fieldtype": "Data", "width": 80},
		{"fieldname": "hazard", "label": _("Description"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "hazardous_situation",
			"label": _("Hazardous Situation"),
			"fieldtype": "Data",
			"width": 200,
		},
		{"fieldname": "harm", "label": _("Harm"), "fieldtype": "Data", "width": 160},
		{"fieldname": "severity", "label": _("S"), "fieldtype": "Int", "width": 50},
		{"fieldname": "probability", "label": _("P"), "fieldtype": "Int", "width": 50},
		{"fieldname": "initial_acceptability", "label": _("Risk"), "fieldtype": "Data", "width": 120},
		{"fieldname": "controls", "label": _("Controls"), "fieldtype": "Data", "width": 260},
		{"fieldname": "residual_severity", "label": _("Residual S"), "fieldtype": "Int", "width": 80},
		{"fieldname": "residual_probability", "label": _("Residual P"), "fieldtype": "Int", "width": 80},
		{
			"fieldname": "residual_acceptability",
			"label": _("Residual Risk"),
			"fieldtype": "Data",
			"width": 120,
		},
		{"fieldname": "justification", "label": _("Justification"), "fieldtype": "Data", "width": 200},
	]
	file_filters = {"status": IN_FORCE}
	if filters.get("risk_management_file"):
		file_filters["name"] = filters.risk_management_file
	files = frappe.get_list(
		FILE,
		filters=file_filters,
		fields=["name", "family_name", "current_revision"],
		order_by="family_name asc",
	)
	revisions = {f.current_revision: f for f in files if f.current_revision}
	if not revisions:
		return columns, []
	hazard_filters = {"parenttype": ASSESSMENT, "parent": ["in", list(revisions)]}
	if filters.get("residual_risk"):
		hazard_filters["residual_acceptability"] = filters.residual_risk
	hazards = frappe.get_all(
		HAZARD,
		filters=hazard_filters,
		fields=[
			"parent",
			"hazard_id",
			"hazard",
			"hazardous_situation",
			"harm",
			"severity",
			"probability",
			"initial_acceptability",
			"residual_severity",
			"residual_probability",
			"residual_acceptability",
			"justification",
		],
		order_by="parent asc, idx asc",
	)
	controls: dict[tuple[str, str], list[str]] = {}
	for row in frappe.get_all(
		CONTROL,
		filters={"parenttype": ASSESSMENT, "parent": ["in", list(revisions)]},
		fields=["parent", "hazard_id", "option", "control_doctype", "control_name", "pinned_version"],
		order_by="idx asc",
	):
		controls.setdefault((row.parent, row.hazard_id), []).append(
			_("{0}: {1} {2} (version {3})").format(
				_(row.option), _(row.control_doctype), row.control_name, row.pinned_version
			)
		)
	rows = []
	for hazard in hazards:
		file = revisions[hazard.parent]
		rows.append(
			{
				**hazard,
				"risk_management_file": file.name,
				"family_name": file.family_name,
				"risk_assessment": hazard.parent,
				"controls": "; ".join(controls.get((hazard.parent, hazard.hazard_id), [])),
			}
		)
	return columns, rows
