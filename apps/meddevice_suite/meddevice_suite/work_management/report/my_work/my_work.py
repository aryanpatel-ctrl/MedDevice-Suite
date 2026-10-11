"""My Work (module 59; URS-PLT-017): everything waiting on the person who opens it."""

from frappe import _

from meddevice_suite.work_management.my_work import waiting_on


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	columns = [
		{"fieldname": "what", "label": _("Waiting For"), "fieldtype": "Data", "width": 380},
		{
			"fieldname": "doctype",
			"label": _("Record Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 150,
		},
		{
			"fieldname": "name",
			"label": _("Record"),
			"fieldtype": "Dynamic Link",
			"options": "doctype",
			"width": 190,
		},
		{"fieldname": "due", "label": _("Due"), "fieldtype": "Date", "width": 100},
		{"fieldname": "since", "label": _("Waiting Since"), "fieldtype": "Datetime", "width": 160},
	]
	rows = [
		{"what": item.what, "doctype": item.doctype, "name": item.name, "due": item.due, "since": item.since}
		for item in waiting_on()
	]
	return columns, rows
