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

The job and role names are proposals built from the departments in the client's
diagrams, which do not name job roles (requirement baseline, question 3).
"""

import frappe

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
		}
	),
	"Batch": oversight(
		{
			"Item Manager": EDIT,
			"Stock User": VIEW,
			"Purchase User": VIEW,
			"Sales User": VIEW,
			"Manufacturing User": VIEW,
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
	"Asset": oversight({"Quality Manager": APPROVE, "Maintenance User": VIEW, "Accounts User": VIEW}),
	"Employee": oversight({"HR User": EDIT, "Manufacturing User": VIEW}),
	# --- Purchasing and receiving
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

ACCESS_CONTROL = frozenset(
	{"User", "Role", "Role Profile", "User Permission", "Custom DocPerm", "System Settings", "Stock Settings"}
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
