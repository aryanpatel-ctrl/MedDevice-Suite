"""Desk layout: show exactly the standard areas the client requirement uses.

The icons belong to Frappe, ERPNext and India Compliance, so they are hidden
rather than deleted. Those apps re-sync their icons on ``bench migrate``,
which is why this runs after every migrate as well as on install.

A user who customises their desk gets a saved Desktop Layout: a full copy of
the icons that the desk renders instead of the site's icons. The declared
visibility is therefore enforced inside saved layouts too, both when a layout
is saved and on every migrate. Users keep their own order and arrangement.
"""

import json

import frappe

# Visibility of standard desktop icons wherever it differs from what the owning
# app ships. Icons not listed here keep their shipped visibility.
DESKTOP_ICON_VISIBILITY: dict[str, bool] = {
	# Shipped hidden, but needed
	"CRM": True,  # customer enquiry and product selection (Opportunity) precede the quotation
	# Shipped visible, but outside the requirement
	"Projects": False,  # project management, timesheets
	"Website": False,  # public website, customer portal
	"Budget": False,  # budgeting; cost centres stay under Accounts Setup
	"Share Management": False,  # shareholder register
	"Subscription": False,  # recurring billing
	"My Workspaces": False,  # personal workspaces
}


def apply_desk_configuration() -> None:
	for label, visible in DESKTOP_ICON_VISIBILITY.items():
		set_desktop_icon_visibility(label, visible)

	for user, layout in frappe.get_all("Desktop Layout", fields=["name", "layout"], as_list=True):
		if (enforced := enforce_declared_visibility(layout)) is not None:
			frappe.db.set_value("Desktop Layout", user, "layout", enforced, update_modified=False)


def set_desktop_icon_visibility(label: str, visible: bool) -> None:
	"""Show or hide a standard desktop icon for every user.

	Written with ``db.set_value`` on purpose: saving the document in developer
	mode would export it back into the owning app's source tree.
	"""
	hidden = frappe.db.get_value("Desktop Icon", label, "hidden")
	if hidden is None or bool(hidden) != visible:
		return  # the owning app no longer ships it, or it is already as declared

	frappe.db.set_value("Desktop Icon", label, "hidden", int(not visible), update_modified=False)
	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")


def enforce_on_desktop_layout(doc, method=None) -> None:
	"""``validate`` hook of Desktop Layout."""
	if (enforced := enforce_declared_visibility(doc.layout)) is not None:
		doc.layout = enforced


def enforce_declared_visibility(layout_json: str | None) -> str | None:
	"""Return the layout with declared visibility applied, or None if nothing changes."""
	try:
		icons = json.loads(layout_json or "null")
	except ValueError:
		return None
	if not isinstance(icons, list):
		return None  # empty layout: the desk falls back to the site's icons

	changed = False
	for icon in icons:
		if not isinstance(icon, dict) or icon.get("label") not in DESKTOP_ICON_VISIBILITY:
			continue
		hidden = int(not DESKTOP_ICON_VISIBILITY[icon["label"]])
		if icon.get("hidden") != hidden:
			icon["hidden"] = hidden
			changed = True

	return json.dumps(icons) if changed else None
