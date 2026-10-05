"""Keep the site's modules in line with this app's modules.txt.

Bench registers an app's modules only when the app is installed, so a module
added later would never reach an existing site, and the placeholder module
`bench new-app` creates would stay behind. Frappe also caches every app's
module list and does not notice when modules.txt changes. This runs before
every migrate, so the cache is current and a new module exists before its
DocTypes are synced.

Records are written directly: saving or deleting a Module Def in developer
mode also creates or deletes folders and edits modules.txt.
"""

import frappe

APP = "meddevice_suite"

# DocTypes whose records belong to a module; a module still owning any is kept.
MODULE_CONTENT = ("DocType", "Report", "Page", "Workspace", "Print Format", "Web Form", "Dashboard")


def sync_module_defs() -> None:
	refresh_module_map()

	declared = set(frappe.get_module_list(APP))
	existing = set(frappe.get_all("Module Def", filters={"app_name": APP}, pluck="name"))

	for module in sorted(declared - existing):
		frappe.get_doc({"doctype": "Module Def", "module_name": module, "app_name": APP}).db_insert()

	for module in sorted(existing - declared):
		if not owns_anything(module):
			frappe.db.delete("Module Def", module)


def refresh_module_map() -> None:
	"""Rebuild Frappe's cached module map when this app's modules.txt has changed."""
	declared = [frappe.scrub(module) for module in frappe.get_module_list(APP)]
	if (frappe.local.app_modules or {}).get(APP) != declared:
		frappe.cache.delete_value("app_modules")
		frappe.setup_module_map()


def owns_anything(module: str) -> bool:
	return any(frappe.db.exists(doctype, {"module": module}) for doctype in MODULE_CONTENT)
