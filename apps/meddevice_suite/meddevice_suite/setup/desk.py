"""Desk layout: show exactly the standard areas the client requirement uses, and
hide every other area, including any a future release adds.

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

# The areas the client requirement uses. Every other desktop icon is hidden,
# including any a future release of Frappe, ERPNext or India Compliance adds,
# until it is checked against the requirement and added here. Hidden on purpose:
# Projects, Website, Budget, Share Management, Subscription, My Workspaces,
# Payments (duplicates Invoicing), Subcontracting (sterilization is not stated as
# outsourced), Automation, Integrations, Support and Build (configuration is code).
DESKTOP_ICONS_SHOWN = frozenset(
	{
		# Apps and folders that hold the areas below
		"Framework",
		"Accounting",
		"India Compliance",
		# Sales, purchasing, stores, production, quality, assets (modules 1-10)
		"CRM",  # customer enquiry and product selection (Opportunity) precede the quotation
		"Selling",
		"Buying",
		"Stock",
		"Manufacturing",
		"Quality",
		"Assets",
		"Organization",
		# Finance and GST (modules 28, 29)
		"Accounts Setup",
		"Invoicing",
		"Banking",
		"Taxes",
		"Financial Reports",
		"GST India",
		"Income Tax India",
		# Administration
		"ERPNext Settings",
		"Users",
		"System",
		"Data",
		"Email",
		"Printing",
	}
)


def apply_desk_configuration() -> None:
	for label in frappe.get_all("Desktop Icon", pluck="name"):
		set_desktop_icon_visibility(label, label in DESKTOP_ICONS_SHOWN)

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
		if not isinstance(icon, dict) or not icon.get("label"):
			continue
		hidden = int(icon["label"] not in DESKTOP_ICONS_SHOWN)
		if icon.get("hidden") != hidden:
			icon["hidden"] = hidden
			changed = True

	return json.dumps(icons) if changed else None
