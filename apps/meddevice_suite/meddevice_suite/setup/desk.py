"""Desk layout: show exactly the standard areas the client requirement uses.

The icons belong to Frappe, ERPNext and India Compliance, so they are hidden
rather than deleted. Those apps re-sync their icons on ``bench migrate``,
which is why this runs after every migrate as well as on install.
"""

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
