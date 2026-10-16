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
from frappe import _
from frappe.model.document import Document

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
		# This app: document control for everyone, the compliance records for QA (modules 30, 49-54)
		"Documents",
		"Compliance",
		# Administration
		"ERPNext Settings",
		"Users",
		"System",
		"Data",
		"Email",
		"Printing",
	}
)


# Who works in each area, by role (URS-PLT-008). ERPNext shows every area to everyone
# and each area's dashboard draws on records and reports that many jobs may not use;
# a job sees only the areas of its work, so the desk follows the permission matrix.
STOCK_ROLES = ("Stock User", "Stock Manager")
SALES_ROLES = ("Sales User", "Sales Manager")
PURCHASE_ROLES = ("Purchase User", "Purchase Manager")
PRODUCTION_ROLES = ("Manufacturing User", "Manufacturing Manager")
QUALITY_ROLES = ("Quality Manager", "QC Inspector")
ACCOUNTS_ROLES = ("Accounts User", "Accounts Manager")
ADMINISTRATION = ("System Manager",)
DESK_AREAS: dict[str, tuple[str, ...]] = {
	"CRM": SALES_ROLES,
	"Selling": (*SALES_ROLES, "Quality Manager"),
	"Buying": (*PURCHASE_ROLES, *STOCK_ROLES, "Quality Manager"),
	"Stock": (*STOCK_ROLES, *PURCHASE_ROLES, *SALES_ROLES, *PRODUCTION_ROLES, *QUALITY_ROLES, "Item Manager"),
	"Manufacturing": (*PRODUCTION_ROLES, "Operator", "Quality Manager"),
	"Quality": (*QUALITY_ROLES, *PRODUCTION_ROLES),
	"Assets": ("Maintenance User", "Quality Manager", *ACCOUNTS_ROLES),
	"Organization": ("HR User", "Quality Manager", *ADMINISTRATION),
	# Finance (modules 28, 29); company and fiscal-year set-up is also the administrator's.
	"Accounting": (*ACCOUNTS_ROLES, *ADMINISTRATION),
	"Accounts Setup": (*ACCOUNTS_ROLES, *ADMINISTRATION),
	"Invoicing": ACCOUNTS_ROLES,
	"Banking": ACCOUNTS_ROLES,
	"Taxes": ACCOUNTS_ROLES,
	"Financial Reports": ACCOUNTS_ROLES,
	"India Compliance": ACCOUNTS_ROLES,
	"GST India": ACCOUNTS_ROLES,
	# TDS: ERPNext opens its records to the accounts manager only, as it ships this area.
	"Income Tax India": ("Accounts Manager",),
	# Everyone reads the documents in effect and works on the revisions they author or review;
	# the record types' own permissions decide what each person sees in them.
	"Documents": ("Desk User",),
	# The audit trail, signatures, archives and retention schedule are reviewed by QA.
	"Compliance": ("Quality Manager", *ADMINISTRATION),
	# Administration
	"Framework": ADMINISTRATION,
	"ERPNext Settings": ADMINISTRATION,
	"Users": ADMINISTRATION,
	"System": ADMINISTRATION,
	"Data": ADMINISTRATION,
	"Email": ADMINISTRATION,
	"Printing": ADMINISTRATION,
}


# Only the built-in Administrator account, which installs the system.
UNUSED_AREA = ("Administrator",)


def apply_desk_configuration() -> None:
	# ERPNext's setup tutorials ("Getting Started") are for configuring a new ERPNext, not for the
	# plant's jobs: their panel opened over the forms people fill in and covered a table's columns
	# (found by the browser walk-through, 2026-10-15). The configuration is code (rules section 1).
	if frappe.db.get_single_value("System Settings", "enable_onboarding"):
		frappe.db.set_single_value("System Settings", "enable_onboarding", 0)
		frappe.clear_cache(doctype="System Settings")
	for label in frappe.get_all("Desktop Icon", pluck="name"):
		set_desktop_icon_visibility(label, label in DESKTOP_ICONS_SHOWN)
	# Areas no job uses stay closed even to someone typing their address: their dashboards
	# would otherwise greet the person with the errors of records they may not read.
	unused = set(frappe.get_all("Workspace", pluck="name")) - set(DESK_AREAS)
	areas = {**DESK_AREAS, **dict.fromkeys(unused, UNUSED_AREA)}
	# Every area is applied: any() over a generator would stop at the first one changed.
	changed = [set_area_roles(area, roles) for area, roles in areas.items()]
	if any(changed):
		frappe.cache.delete_key("desktop_icons")
		frappe.cache.delete_key("bootinfo")

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


def set_area_roles(area: str, roles: tuple[str, ...]) -> bool:
	"""Restrict an area's desk icon and page to `roles`; True when anything changed.

	The role rows are written directly, as the visibility is: saving a standard
	Workspace or Desktop Icon in developer mode would export it into the owning app."""
	changed = False
	for parenttype in ("Desktop Icon", "Workspace"):
		if not frappe.db.exists(parenttype, area):
			continue
		filters = {"parent": area, "parenttype": parenttype, "parentfield": "roles"}
		if set(frappe.get_all("Has Role", filters=filters, pluck="role")) == set(roles):
			continue
		frappe.db.delete("Has Role", filters)
		for idx, role in enumerate(sorted(set(roles)), start=1):
			frappe.get_doc({"doctype": "Has Role", **filters, "role": role, "idx": idx}).db_insert()
		# Frappe reads both from its document cache, which direct writes do not refresh.
		frappe.clear_document_cache(parenttype, area)
		changed = True
	return changed


# Security review: open to guests exactly as Frappe's own loader is; a guest gets Frappe's
# behaviour unchanged, and the plain refusal, given only to a signed-in user, names nothing
# but a standard record type.
@frappe.whitelist(allow_guest=True)  # nosemgrep
def getpage(name: str) -> None:
	"""Frappe's page loader, with a plain refusal for a record type the user may not open.

	The desk opens a record type the user may not read as if it were a page, and Frappe
	then reports a missing permission on "document Page". Overridden in hooks.py because
	only the loader knows the route; every other page loads exactly as in Frappe."""
	from frappe.desk.desk_page import getpage as frappe_getpage

	doctype = frappe.unscrub(name)
	signed_in = frappe.session.user != "Guest"
	if signed_in and not frappe.db.exists("Page", name) and frappe.db.exists("DocType", doctype):
		frappe.response["403"] = 1
		frappe.throw(_("You are not permitted to open {0}.").format(_(doctype)), frappe.PermissionError)
	return frappe_getpage(name)


def enforce_on_desktop_layout(doc: Document, method: str | None = None) -> None:
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
