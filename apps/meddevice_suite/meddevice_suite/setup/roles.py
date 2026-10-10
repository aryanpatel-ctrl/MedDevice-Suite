"""Job profiles and permissions on regulated records (module 47; 21 CFR 11.10(d), (g)).

Every person is given one job profile, a Frappe Role Profile that bundles the
roles that job needs. Profiles reuse ERPNext's own roles where their meaning
fits, so ERPNext's internal role checks keep working; a new role is added only
for a job ERPNext has no role for, and only once records exist that it needs.

For every regulated business record type, the permission matrix below replaces
ERPNext's default permissions (as Custom DocPerm rows). It is the specification:
an ERPNext upgrade can never widen access to a regulated record unnoticed, and a
change to it is a change to validated behaviour. Three rules hold throughout:

- Nobody deletes a regulated record. Drafts are discarded, masters disabled,
  and cancelled records kept for their retention period.
- System Manager administers the system and may read, but never creates,
  submits, cancels or amends a regulated record.
- Nobody shares or imports regulated records, which would bypass the roles.

Access-control and settings record types (User, Role, Role Profile, User Permission,
Custom DocPerm, System Settings, Stock Settings) keep Frappe's own permissions: they
are changed by configuration code, not by jobs.

The diagrams do not name job roles; these jobs were decided from their departments
on the client's instruction of 2026-10-07, which left such choices to us. Job titles
can be renamed without changing what each job may do.
"""

import frappe
from frappe import _
from frappe.model.document import Document

# Roles ERPNext has no equivalent for.
NEW_ROLES = ("QC Inspector", "Operator")

JOB_PROFILES: dict[str, tuple[str, ...]] = {
	"Sales": ("Sales User",),
	"Purchase": ("Purchase User",),
	"Stores": ("Stock User",),
	"Production Supervisor": ("Manufacturing User",),
	"Operator": ("Operator",),
	"QC Inspector": ("QC Inspector",),
	# Masters are version-controlled under QA (D2: "All masters are version controlled").
	"QA Manager": ("Quality Manager", "Item Manager"),
	"Maintenance": ("Maintenance User",),
	"Training Coordinator": ("HR User",),
	"Accounts": ("Accounts User",),
	"Management": ("Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager"),
	"System Administrator": ("System Manager",),
}

FLAGS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
	"if_owner",
	"mask",
)

PICK = ("select",)  # choose it in a form; never open it
VIEW = ("select", "read", "report", "print")
REVIEW = (*VIEW, "export")
EDIT = (*VIEW, "write", "create")  # masters: never submitted
ENTER = (*EDIT, "submit")  # transactions: enter and submit
CORRECT = (*ENTER, "cancel", "amend")  # and correct by cancel and amend
APPROVE = (*CORRECT, "export")

MANAGEMENT = ("Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager")


def oversight(rows: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
	"""QA, management and the system administrator may read every regulated record;
	`rows` adds the roles that work with it and may raise those defaults."""
	return {"Quality Manager": REVIEW, **dict.fromkeys(MANAGEMENT, REVIEW), "System Manager": VIEW, **rows}


# doctype -> role -> permissions, at permission level 0
PERMISSIONS: dict[str, dict[str, tuple[str, ...]]] = {
	# --- Master data
	"Item": oversight(
		{
			"Item Manager": (*EDIT, "export"),
			"Stock User": VIEW,
			"Purchase User": VIEW,
			"Sales User": VIEW,
			"Manufacturing User": VIEW,
			"QC Inspector": VIEW,
			"Maintenance User": VIEW,
			"Accounts User": VIEW,
			"Operator": PICK,  # the item on the job card
		}
	),
	"BOM": oversight(
		{
			# Production drafts a BOM; QA approves it by submitting.
			"Manufacturing User": EDIT,
			"Quality Manager": APPROVE,
			"Stock User": VIEW,
			"Purchase User": VIEW,
			"QC Inspector": VIEW,
		}
	),
	"Routing": oversight({"Manufacturing User": EDIT, "Operator": VIEW, "QC Inspector": VIEW}),
	"Operation": oversight({"Manufacturing User": EDIT, "Operator": VIEW, "QC Inspector": VIEW}),
	"Workstation": oversight({"Manufacturing User": EDIT, "Operator": VIEW, "Maintenance User": VIEW}),
	"Supplier": oversight(
		{
			"Purchase User": EDIT,
			"Purchase Manager": (*EDIT, "export"),
			"Stock User": VIEW,
			"QC Inspector": VIEW,
			"Accounts User": VIEW,
		}
	),
	"Customer": oversight(
		{"Sales User": EDIT, "Sales Manager": (*EDIT, "export"), "Stock User": VIEW, "Accounts User": VIEW}
	),
	"Warehouse": oversight(
		{
			# Quarantine, approved, rejected and hold stores are QA-controlled.
			"Item Manager": EDIT,
			"Stock User": VIEW,
			"Purchase User": VIEW,
			"Sales User": VIEW,
			"Manufacturing User": VIEW,
			"QC Inspector": VIEW,
			"Accounts User": VIEW,
			"Operator": PICK,  # the job card's stores
			"Maintenance User": PICK,  # where a repair takes its spare parts from
		}
	),
	"Batch": oversight(
		{
			# Receiving and production create lots; changing one is QA's (lot status, module 17).
			"Item Manager": EDIT,
			"Stock User": (*VIEW, "create"),
			"Manufacturing User": (*VIEW, "create"),
			"Purchase User": VIEW,
			"Sales User": VIEW,
			"QC Inspector": VIEW,
			"Accounts User": VIEW,
		}
	),
	"Quality Inspection Template": oversight(
		{
			# Specifications are QA-controlled.
			"Quality Manager": (*EDIT, "export"),
			"QC Inspector": VIEW,
			"Stock User": VIEW,
			"Manufacturing User": VIEW,
		}
	),
	"Asset": oversight(
		{
			"Quality Manager": APPROVE,
			"Maintenance User": VIEW,
			"Accounts User": VIEW,
			"QC Inspector": PICK,  # the instrument a reading was taken with (module 16)
		}
	),
	# An operator names themselves on the time they log against a job card.
	"Employee": oversight({"HR User": EDIT, "Manufacturing User": VIEW, "Operator": PICK}),
	# --- Purchasing and receiving
	# --- Sales
	"Quotation": oversight(
		{
			# Sales drafts, amends and withdraws a quotation; only the price approver submits (module 48).
			"Sales User": (*EDIT, "cancel", "amend"),
			"Sales Manager": APPROVE,
		}
	),
	"Material Request": oversight(
		{
			"Stock User": CORRECT,
			"Purchase User": CORRECT,
			"Manufacturing User": ENTER,
			"Purchase Manager": APPROVE,
		}
	),
	"Purchase Order": oversight(
		{
			# Purchasing drafts and amends; only the approver submits and cancels (module 48).
			"Purchase User": (*EDIT, "amend"),
			"Purchase Manager": APPROVE,
			"Stock User": VIEW,
			"Accounts User": VIEW,
		}
	),
	"Purchase Receipt": oversight(
		{
			# Stores receives goods; purchasing follows them up but does not receive.
			"Stock User": CORRECT,
			"Stock Manager": APPROVE,
			"Purchase User": VIEW,
			"QC Inspector": VIEW,
			"Accounts User": VIEW,
		}
	),
	"Quality Inspection": oversight(
		{
			# The inspector records and submits results; QA alone corrects them.
			"QC Inspector": ENTER,
			"Quality Manager": APPROVE,
			"Stock User": VIEW,
			"Purchase User": VIEW,
			"Manufacturing User": VIEW,
		}
	),
	# --- Stock and production
	"Stock Entry": oversight(
		{
			"Stock User": CORRECT,
			"Stock Manager": APPROVE,
			"Manufacturing User": CORRECT,
			"Accounts User": VIEW,
			# QA's lot release moves the lot between stores (module 17): the release posts the
			# transfer under the QA user, who never fills in a stock entry by hand (the lot
			# control refuses any other stock entry of theirs) and never cancels one.
			"Quality Manager": (*REVIEW, "write", "create", "submit"),
		}
	),
	"Stock Reconciliation": oversight({"Stock Manager": APPROVE, "Accounts User": VIEW}),
	"Work Order": oversight(
		{
			"Manufacturing User": CORRECT,
			"Manufacturing Manager": APPROVE,
			"Stock User": VIEW,
			"Operator": VIEW,
			"QC Inspector": VIEW,
		}
	),
	"Job Card": oversight(
		{
			# Operators record their own operations; the supervisor submits and corrects.
			"Manufacturing User": CORRECT,
			"Operator": (*VIEW, "write"),
			"QC Inspector": VIEW,
		}
	),
	# The lots on each stock transaction (ERPNext v16 keeps them in a bundle of their own),
	# made and cancelled with the transaction by whoever may post it.
	"Serial and Batch Bundle": oversight(
		{
			"Stock User": CORRECT,
			"Stock Manager": APPROVE,
			"Manufacturing User": ENTER,
			"Sales User": ENTER,
			# purchasing traces a supplier's lots (the Serial No and Batch Traceability report)
			"Purchase User": VIEW,
			# the lots on the transfer QA's lot release posts (module 17)
			"Quality Manager": (*REVIEW, "write", "create", "submit"),
		}
	),
	"Pick List": oversight(
		{"Stock User": CORRECT, "Stock Manager": APPROVE, "Manufacturing User": ENTER, "Sales User": VIEW}
	),
	# --- Dispatch
	"Delivery Note": oversight(
		{"Stock User": CORRECT, "Stock Manager": APPROVE, "Sales User": ENTER, "Accounts User": VIEW}
	),
	"Packing Slip": oversight({"Stock User": CORRECT, "Stock Manager": APPROVE, "Sales User": VIEW}),
	# --- Maintenance
	"Asset Maintenance Log": oversight(
		{"Maintenance User": ENTER, "Quality Manager": APPROVE, "Manufacturing User": VIEW}
	),
	"Asset Repair": oversight(
		{"Maintenance User": ENTER, "Quality Manager": APPROVE, "Manufacturing User": VIEW}
	),
}

# Field-level permissions (permission level 1) on the same record types.
HIGHER_LEVELS: dict[str, dict[tuple[str, int], tuple[str, ...]]] = {
	"Customer": {("Sales Manager", 1): ("read", "write"), ("Sales User", 1): ("read",)},
	"Purchase Order": {("Purchase Manager", 1): ("read", "write")},
	"Purchase Receipt": {("Stock Manager", 1): ("read", "write")},
	"Delivery Note": {("Stock Manager", 1): ("read", "write")},
}

# Record types outside the regulated scope that a job cannot work without. Each keeps
# ERPNext's own permissions; only the listed flags are added.
SUPPORTING_PERMISSIONS: dict[str, dict[str, tuple[str, ...]]] = {
	# A goods receipt or delivery looks up the supplier's or customer's account, which
	# ERPNext lets Stock User neither read nor select. Select only: pick it, never open it.
	# The administrator picks the company's default accounts (below).
	"Account": {"Stock User": PICK, "System Manager": PICK},
	# The forms these jobs fill in look these up (found in the live pilot test, 2026-10-07).
	"Stock Entry Type": {"Manufacturing User": PICK},
	# A work order made against an order; production also plans from the orders it reads in
	# the forecasting report of its area (Exponential Smoothing Forecasting, module 13).
	"Sales Order": {"Manufacturing User": ("select", "read", "report")},
	"Workstation Type": {"Operator": PICK, "Quality Manager": PICK, **dict.fromkeys(MANAGEMENT, PICK)},
	"Quality Inspection Parameter": {"QC Inspector": PICK},
	"Price List": {"Quality Manager": PICK},  # a BOM's costing
	"Terms and Conditions": dict.fromkeys(MANAGEMENT, PICK),  # an approver correcting a quotation
	"Asset Maintenance": {"Maintenance User": PICK},
	# ERPNext's stock forms read these settings and fail quietly when they may not.
	"Stock Settings": {"Stock User": ("read",), "Manufacturing User": ("read",)},
	# Found by the browser test of each job (URS-PLT-009), 2026-10-07:
	# a new material request, inspection, asset or repair reads the company's letter head, and the
	# desk tells the person they may not when they cannot read the company.
	"Company": dict.fromkeys(
		("Manufacturing User", "QC Inspector", "Quality Manager", "Maintenance User"), ("read",)
	),
	# the feedback form's template, for the jobs of the Quality area (module 42)
	"Quality Feedback Template": {
		"QC Inspector": PICK,
		"Quality Manager": PICK,
		"Manufacturing User": PICK,
		"Manufacturing Manager": PICK,
	},
	"Sales Stage": {"Sales User": PICK},  # the stage of an opportunity (module 10)
	# Invoicing (module 29): the GST template of a sales invoice, the accounting settings
	# the chart of accounts reads, and the accounting dimensions the GST tools list.
	"Sales Taxes and Charges Template": {"Accounts User": PICK},
	"Accounts Settings": {"Accounts User": ("read",)},
	"Accounting Dimension": {"Accounts User": ("read",)},
	# The equipment register (Fixed Asset Register) and the job card summary are filtered by
	# fiscal year.
	"Fiscal Year": {"Quality Manager": ("read",), "Manufacturing User": ("read",)},
	# Management's sales analytics (Selling area) totals invoices and payments as well.
	"Sales Invoice": {"Sales Manager": ("select", "read", "report")},
	"Payment Entry": {"Sales Manager": ("select", "read", "report")},
	# Company and fiscal-year set-up is also the administrator's (setup/desk.py): the company
	# form's default accounts, holiday list and finance book, and a payment term's mode.
	"Holiday List": {"System Manager": PICK},
	"Finance Book": {"System Manager": PICK},
	"Mode of Payment": {"System Manager": PICK},
}

# Record types a job may create only through its transactions, never in their own form.
# A lot is made by the receipt or production entry that brings it in: ERPNext creates it
# with the job's own create permission. Changing a lot is QA's, so these jobs may not write
# one, and a new lot's own form is QA's (Item Manager).
CREATED_THROUGH_TRANSACTIONS: dict[str, tuple[str, ...]] = {"Batch": ("Stock User", "Manufacturing User")}

# Record types a job writes only through a service of this app, never in their own form:
# QA's lot release posts the transfer between stores (module 17), and the lot control refuses
# any other stock entry of QA's. The job holds write and create for the service to post.
POSTED_BY_SERVICES: dict[str, tuple[str, ...]] = {
	"Stock Entry": ("Quality Manager",),
	"Serial and Batch Bundle": ("Quality Manager",),
}


def fills_in_by_hand(doctype: str, role: str, flags: tuple[str, ...]) -> bool:
	"""Whether the role fills in the record type's form itself: it may write or create it,
	other than only through its transactions or through a service."""
	if not ({"write", "create"} & set(flags)):
		return False
	return role not in CREATED_THROUGH_TRANSACTIONS.get(doctype, ()) and role not in POSTED_BY_SERVICES.get(
		doctype, ()
	)


ACCESS_CONTROL = frozenset(
	{"User", "Role", "Role Profile", "User Permission", "Custom DocPerm", "System Settings", "Stock Settings"}
)


SYSTEM_ADMINISTRATION = "System Manager"
BUSINESS_ROLES = frozenset(
	role for roles in JOB_PROFILES.values() for role in roles if role != SYSTEM_ADMINISTRATION
)


def separate_administration(doc: Document, method: str | None = None) -> None:
	"""User validate: whoever administers the system takes no part in the regulated work,
	so no one can both grant access and use it (21 CFR 11.10(g); EU GMP Annex 11 §12)."""
	if doc.name in ("Administrator", "Guest") or frappe.flags.in_install or frappe.flags.in_migrate:
		return
	roles = {row.role for row in doc.roles}
	if SYSTEM_ADMINISTRATION in roles and roles & BUSINESS_ROLES:
		frappe.throw(
			_(
				"{0} is a system administrator and cannot also hold {1}. Give the business work a "
				"separate account."
			).format(doc.name, ", ".join(sorted(roles & BUSINESS_ROLES))),
			frappe.PermissionError,
		)


def separate_administration_in_profile(doc: Document, method: str | None = None) -> None:
	"""Role Profile validate: a job profile never combines system administration with business
	roles either; the user check alone would catch it only when a user holding it is saved."""
	if frappe.flags.in_install or frappe.flags.in_migrate:
		return
	roles = {row.role for row in doc.roles}
	if SYSTEM_ADMINISTRATION in roles and roles & BUSINESS_ROLES:
		frappe.throw(
			_(
				"A job profile cannot hold both system administration and {0}; administration is a "
				"separate job."
			).format(", ".join(sorted(roles & BUSINESS_ROLES))),
			frappe.PermissionError,
		)


def apply_roles() -> None:
	for role in NEW_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True  # setup code
			)
	for profile, roles in JOB_PROFILES.items():
		sync_job_profile(profile, roles)
	for doctype in PERMISSIONS:
		sync_permissions(doctype, declared_rows(doctype))
	for doctype in SUPPORTING_PERMISSIONS:
		sync_permissions(doctype, supporting_rows(doctype))


def sync_job_profile(profile: str, roles: tuple[str, ...]) -> None:
	doc = (
		frappe.get_doc("Role Profile", profile)
		if frappe.db.exists("Role Profile", profile)
		else frappe.new_doc("Role Profile", role_profile=profile)
	)
	if sorted(row.role for row in doc.roles) == sorted(roles) and not doc.is_new():
		return
	doc.set("roles", [{"role": role} for role in roles])
	doc.save(ignore_permissions=True)  # setup code


def declared_rows(doctype: str) -> dict[tuple[str, int], dict[str, int]]:
	rows = {(role, 0): flags for role, flags in PERMISSIONS[doctype].items()}
	rows.update(HIGHER_LEVELS.get(doctype, {}))
	return {key: {flag: int(flag in granted) for flag in FLAGS} for key, granted in rows.items()}


def supporting_rows(doctype: str) -> dict[tuple[str, int], dict[str, int]]:
	"""ERPNext's own permissions on the record type, plus the declared additions."""
	rows = {
		(row.role, row.permlevel): {flag: int(row.get(flag) or 0) for flag in FLAGS}
		for row in frappe.get_all(
			"DocPerm", filters={"parent": doctype}, fields=["role", "permlevel", *FLAGS]
		)
	}
	for role, flags in SUPPORTING_PERMISSIONS[doctype].items():
		row = rows.setdefault((role, 0), dict.fromkeys(FLAGS, 0))
		row.update(dict.fromkeys(flags, 1))
	return rows


def sync_permissions(doctype: str, declared: dict[tuple[str, int], dict[str, int]]) -> None:
	"""Make the record type's Custom DocPerm rows exactly the declared ones, touching only
	rows that differ, so an unchanged matrix writes nothing and records nothing."""
	existing = {
		(row.role, row.permlevel): row
		for row in frappe.get_all(
			"Custom DocPerm", filters={"parent": doctype}, fields=["name", "role", "permlevel", *FLAGS]
		)
	}
	changed = False
	for key, flags in declared.items():
		row = existing.pop(key, None)
		if row is None:
			frappe.get_doc(
				{"doctype": "Custom DocPerm", "parent": doctype, "role": key[0], "permlevel": key[1], **flags}
			).insert(ignore_permissions=True)  # setup code
			changed = True
		elif any(int(row[flag] or 0) != value for flag, value in flags.items()):
			doc = frappe.get_doc("Custom DocPerm", row.name)
			doc.update(flags)
			doc.save(ignore_permissions=True)  # setup code
			changed = True
	for row in existing.values():
		frappe.delete_doc("Custom DocPerm", row.name, ignore_permissions=True)  # setup code
		changed = True
	if changed:
		frappe.clear_cache(doctype=doctype)
