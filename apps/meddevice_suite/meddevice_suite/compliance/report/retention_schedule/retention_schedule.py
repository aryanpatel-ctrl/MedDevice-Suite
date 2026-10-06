"""How long each regulated record type is kept, and why (module 54), for QA and auditors."""

from frappe import _

from meddevice_suite.compliance.retention import RETENTION_SCHEDULE


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	columns = [
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 240},
		{
			"fieldname": "record_type",
			"label": _("Record type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 220,
		},
		{"fieldname": "years", "label": _("Kept for (years)"), "fieldtype": "Int", "width": 130},
		{"fieldname": "counted_from", "label": _("Counted from"), "fieldtype": "Data", "width": 200},
		{"fieldname": "basis", "label": _("Legal basis"), "fieldtype": "Data", "width": 380},
	]
	data = [
		{
			"category": _(category),
			"record_type": doctype,
			"years": years,
			"counted_from": _("The date the record was made"),
			"basis": basis,
		}
		for category, (years, basis, doctypes) in RETENTION_SCHEDULE.items()
		for doctype in doctypes
	]
	return columns, data
