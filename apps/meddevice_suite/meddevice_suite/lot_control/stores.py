"""Stores by role (module 17; D2 "Quarantine / Approved Stock"): where each lot status lives.

A lot that is not Approved is kept apart from approved stock in a store of its
own role: Quarantine, Hold, Rejected or Returns. A store with no role is an
approved store. The kind of a store (`kind_of`), its role or approved, is what the
stock path rule of `lot_status` reads, with the lot's status. Each company gets one
store of each role, created by configuration if it is missing and named after its
role (decided under the client's instruction of 2026-10-08); the client may rename
a store, the role stays, so the enforcement in `lot_status` never depends on a
store's name.

The role is a declared list on the warehouse rather than ERPNext's free
Warehouse Type master, so a site cannot invent a fifth kind of store.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import flt

ROLE_FIELD = "mds_store_role"
QUARANTINE, HOLD, REJECTED, RETURNS = "Quarantine", "Hold", "Rejected", "Returns"
ROLES = (QUARANTINE, HOLD, REJECTED, RETURNS)
# ERPNext's company default for the store a customer's return comes back to.
SALES_RETURN_STORE = "default_warehouse_for_sales_return"
# The kind of a store without a role: it holds approved stock. With the roles, every kind of store.
APPROVED = "Approved"
KINDS = (APPROVED, *ROLES)

CUSTOM_FIELDS = {
	"Warehouse": [
		{
			"fieldname": ROLE_FIELD,
			"label": "Store Role",
			"fieldtype": "Select",
			"options": "\n" + "\n".join(ROLES),
			"insert_after": "warehouse_type",
			"in_list_view": 1,
			"in_standard_filter": 1,
			"description": "Which lot status this store holds. A store with no role holds approved stock.",
		}
	]
}


def apply_store_roles() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	for company, abbr in frappe.get_all("Company", fields=["name", "abbr"], as_list=True):
		ensure_role_stores(company, abbr)


def ensure_role_stores(company: str, abbr: str) -> None:
	"""One store of each role for the company: an existing store named after the role takes
	the role, otherwise the store is created under the company's root store."""
	for role in ROLES:
		if frappe.db.exists("Warehouse", {"company": company, ROLE_FIELD: role}):
			continue
		make_role_store(company, abbr, role)
	returns_by_default(company)


def make_role_store(company: str, abbr: str, role: str) -> None:
	"""The company's store of `role`: the store already named after it takes the role, otherwise
	a new one is made under the company's root store."""
	name = f"{role} - {abbr}"
	if frappe.db.exists("Warehouse", name) and not frappe.db.get_value("Warehouse", name, "is_group"):
		store = frappe.get_doc("Warehouse", name)
		store.set(ROLE_FIELD, role)
		# The store named after the role is that store already: its stock does not stop it.
		store.flags.first_role_by_configuration = True
		store.save(ignore_permissions=True)  # setup code
		return
	frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": role if not frappe.db.exists("Warehouse", name) else f"{role} Store",
			"company": company,
			"parent_warehouse": root_store(company),
			ROLE_FIELD: role,
		}
	).insert(ignore_permissions=True)  # setup code


def returns_by_default(company: str) -> None:
	"""ERPNext's sales return made from a delivery note or an invoice takes the company's default store
	for sales returns: set to its Returns store, where a customer's goods come back to and wait for QA's
	decision (plan D-25), so Stores does not have to choose it. Any other store is refused there
	(`lot_status.movement_allowed`)."""
	returns = frappe.db.get_value("Warehouse", {"company": company, ROLE_FIELD: RETURNS, "is_group": 0})
	if returns and frappe.db.get_value("Company", company, SALES_RETURN_STORE) != returns:
		# Configuration, like the role stores above; the company's own form is not opened for it.
		frappe.db.set_value("Company", company, SALES_RETURN_STORE, returns, update_modified=False)


def stores_for_new_company(doc: Document, method: str | None = None) -> None:
	"""Company on_update: a company gets its role stores as soon as it exists, whether the setup
	wizard made it after the app was installed or it was added later, not only on the next
	migrate (found in CI, 2026-10-09: a new site's company had no Quarantine store). ERPNext
	creates the company's root store in its own on_update, which runs first."""
	if frappe.get_meta("Warehouse").has_field(ROLE_FIELD) and root_store(doc.name):
		ensure_role_stores(doc.name, doc.abbr)


def root_store(company: str) -> str | None:
	return frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["in", ("", None)]}
	)


def store_for(role: str, company: str) -> str:
	"""The company's store of `role`; refused plainly when the configuration has not run."""
	store = frappe.db.get_value("Warehouse", {"company": company, ROLE_FIELD: role, "is_group": 0})
	if not store:
		frappe.throw(_("{0} has no {1} store; run the site configuration.").format(company, _(role)))
	return store


def role_of(warehouse: str | None) -> str | None:
	"""The store's role, None for an approved store. A store made after the role field exists
	holds an empty value rather than none, so the two are read alike on every site."""
	return (frappe.get_cached_value("Warehouse", warehouse, ROLE_FIELD) or None) if warehouse else None


def kind_of(warehouse: str | None) -> str | None:
	"""What the store holds: its role, or APPROVED for a store without one; None for no store at
	all, the stock leaving the stores (issued, consumed, delivered, scrapped, sent back)."""
	return (role_of(warehouse) or APPROVED) if warehouse else None


def protect_store_role(doc: Document, method: str | None = None) -> None:
	"""Warehouse validate: one store per role per company, never a group, and a store that
	holds stock keeps its role until the stock is moved. The one exception is the configuration
	giving a store named after a role that role for the first time (a site with stock before lot
	control): the store is already what the role says."""
	role = doc.get(ROLE_FIELD)
	if role and doc.is_group:
		frappe.throw(_("A {0} store holds stock itself; a group of stores cannot be one.").format(_(role)))
	if role:
		other = frappe.db.get_value(
			"Warehouse", {"company": doc.company, ROLE_FIELD: role, "name": ["!=", doc.name]}
		)
		if other:
			frappe.throw(_("{0} is already the {1} store of {2}.").format(other, _(role), doc.company))
	before = doc.get_doc_before_save()
	first_role = bool(doc.flags.first_role_by_configuration) and not (before and before.get(ROLE_FIELD))
	if before and not first_role and (before.get(ROLE_FIELD) or "") != (role or "") and holds_stock(doc.name):
		frappe.throw(
			_("{0} holds stock; move the stock before changing what the store is for.").format(doc.name)
		)


def holds_stock(warehouse: str) -> bool:
	return flt(frappe.db.get_value("Bin", {"warehouse": warehouse, "actual_qty": [">", 0]}, "actual_qty")) > 0
