"""Which lookups in the forms a job fills in that job may not use (URS-CMP-036).

A form's link field searches the record type it points to, which needs at least
the select permission. A lookup the job may not use shows the person a permission
error when they click it. The permission matrix leaves some closed on purpose; this
lists those still closed, so that the test can hold them to the reviewed list.
"""

import frappe

from meddevice_suite.setup.roles import JOB_PROFILES, PERMISSIONS, SYSTEM_ADMINISTRATION

AUTOMATIC_ROLES = ("All", "Desk User")


def closed_lookups() -> dict[str, set[tuple[str, str]]]:
	"""job profile -> {(record type looked up, "Form.field")} the job cannot search."""
	closed: dict[str, set[tuple[str, str]]] = {}
	for profile, roles in JOB_PROFILES.items():
		if SYSTEM_ADMINISTRATION in roles:
			continue
		user_roles = (*roles, *AUTOMATIC_ROLES)
		for doctype, rows in PERMISSIONS.items():
			if not any({"write", "create"} & set(rows.get(role, ())) for role in user_roles):
				continue
			meta = frappe.get_meta(doctype)
			for form in (meta, *(frappe.get_meta(df.options) for df in meta.get_table_fields())):
				for df in form.fields:
					if df.fieldtype != "Link" or df.read_only or df.hidden or df.options == "DocType":
						continue
					if frappe.get_meta(df.options).istable:
						continue  # a child table's rows are searched through their parent by the form's own query
					if not can_pick(df.options, user_roles):
						closed.setdefault(profile, set()).add((df.options, f"{form.name}.{df.fieldname}"))
	return closed


def can_pick(doctype: str, roles: tuple[str, ...]) -> bool:
	table = "Custom DocPerm" if frappe.db.exists("Custom DocPerm", {"parent": doctype}) else "DocPerm"
	return bool(
		frappe.get_all(
			table,
			filters={"parent": doctype, "permlevel": 0, "role": ["in", roles]},
			or_filters={"select": 1, "read": 1},
			limit=1,
		)
	)
