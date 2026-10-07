import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from meddevice_suite.compliance.audit_scope import REGULATED_DOCTYPES
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.roles import (
	ACCESS_CONTROL,
	FLAGS,
	HIGHER_LEVELS,
	JOB_PROFILES,
	NEW_ROLES,
	PERMISSIONS,
	SUPPORTING_PERMISSIONS,
	apply_roles,
	declared_rows,
)

BUSINESS_RECORD_TYPES = sorted(PERMISSIONS)
# Roles every user has, whatever their job.
AUTOMATIC_ROLES = {"All", "Guest", "Desk User"}


def user_with_profile(profile: str) -> str:
	email = f"{frappe.scrub(profile).replace('_', '.')}@job-profiles.example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": profile,
				"send_welcome_email": 0,
				"role_profiles": [{"role_profile": profile}],
			}
		).insert(ignore_permissions=True)
	return email


def can(user: str, doctype: str, permission: str) -> bool:
	return bool(frappe.has_permission(doctype, permission, user=user))


def applied_rows(doctype: str) -> dict[tuple[str, int], dict[str, int]]:
	rows = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["role", "permlevel", *FLAGS])
	return {(row.role, row.permlevel): {flag: int(row[flag] or 0) for flag in FLAGS} for row in rows}


class ProfileTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		cls.users = {profile: user_with_profile(profile) for profile in JOB_PROFILES}


@verifies("URS-CMP-014")
class TestJobProfiles(ProfileTestCase):
	def test_each_job_profile_grants_exactly_its_roles(self):
		for profile, roles in JOB_PROFILES.items():
			with self.subTest(profile=profile):
				self.assertEqual(
					sorted(
						frappe.get_all(
							"Has Role",
							filters={"parent": profile, "parenttype": "Role Profile"},
							pluck="role",
						)
					),
					sorted(roles),
				)
				self.assertEqual(set(frappe.get_roles(self.users[profile])) - AUTOMATIC_ROLES, set(roles))

	def test_roles_erpnext_lacks_are_created(self):
		for role in NEW_ROLES:
			with self.subTest(role=role):
				self.assertTrue(frappe.db.get_value("Role", role, "desk_access"))


@verifies("URS-CMP-014")
class TestProfileRules(UnitTestCase):
	def test_every_role_in_the_matrix_belongs_to_a_job_profile(self):
		in_profiles = {role for roles in JOB_PROFILES.values() for role in roles}
		in_matrix = {role for rows in PERMISSIONS.values() for role in rows}
		in_matrix |= {role for rows in HIGHER_LEVELS.values() for role, _ in rows}

		self.assertEqual(in_matrix - in_profiles, set())

	def test_the_administrator_profile_holds_no_business_role(self):
		self.assertEqual(JOB_PROFILES["System Administrator"], ("System Manager",))
		for profile, roles in JOB_PROFILES.items():
			if profile != "System Administrator":
				with self.subTest(profile=profile):
					self.assertNotIn("System Manager", roles)


@verifies("URS-CMP-015")
class TestPermissionMatrix(ProfileTestCase):
	def test_every_regulated_business_record_type_has_a_declared_matrix(self):
		self.assertEqual(set(PERMISSIONS), set(REGULATED_DOCTYPES) - ACCESS_CONTROL)

	def test_applied_permissions_are_exactly_the_declared_ones(self):
		for doctype in BUSINESS_RECORD_TYPES:
			with self.subTest(doctype=doctype):
				self.assertEqual(applied_rows(doctype), declared_rows(doctype))

	def test_a_manual_change_is_undone_on_the_next_migrate_and_audited(self):
		row = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": "Stock Entry", "role": "Stock User", "permlevel": 0},
			pluck="name",
		)[0]
		frappe.db.set_value("Custom DocPerm", row, {"delete": 1, "share": 1})
		frappe.get_doc(
			{"doctype": "Custom DocPerm", "parent": "Stock Entry", "role": "Sales User", "read": 1}
		).db_insert()

		apply_configuration()

		self.assertEqual(applied_rows("Stock Entry"), declared_rows("Stock Entry"))
		restored = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": "Custom DocPerm", "reference_name": row, "action": "Updated"},
			pluck="changes",
		)
		self.assertTrue(restored)

	def test_missing_roles_profiles_and_permissions_are_recreated_on_the_next_migrate(self):
		frappe.db.delete("Role", "QC Inspector")
		frappe.db.delete("Has Role", {"parent": "Stores", "parenttype": "Role Profile"})
		frappe.db.delete("Custom DocPerm", {"parent": "Quality Inspection", "role": "QC Inspector"})

		apply_configuration()

		self.assertTrue(frappe.db.exists("Role", "QC Inspector"))
		self.assertEqual(
			frappe.get_all(
				"Has Role", filters={"parent": "Stores", "parenttype": "Role Profile"}, pluck="role"
			),
			["Stock User"],
		)
		self.assertEqual(applied_rows("Quality Inspection"), declared_rows("Quality Inspection"))

	def test_an_unchanged_matrix_writes_nothing(self):
		before = frappe.db.count("Audit Event")

		apply_roles()

		self.assertEqual(frappe.db.count("Audit Event"), before)


# What each job must still be able to do under least privilege.
DAILY_WORK = {
	"Sales": [("Customer", "create"), ("Delivery Note", "submit")],
	"Purchase": [("Supplier", "create"), ("Material Request", "submit"), ("Purchase Order", "create")],
	"Stores": [
		("Purchase Receipt", "submit"),
		("Stock Entry", "submit"),
		("Delivery Note", "cancel"),
		("Batch", "create"),
	],
	"Production Supervisor": [
		("BOM", "create"),
		("Work Order", "submit"),
		("Job Card", "submit"),
		("Batch", "create"),
	],
	"QC Inspector": [("Quality Inspection", "submit")],
	"QA Manager": [("BOM", "submit"), ("Item", "create"), ("Quality Inspection Template", "create")],
	"Maintenance": [("Asset Maintenance Log", "submit"), ("Asset Repair", "submit")],
	"Training Coordinator": [("Employee", "create")],
	"Accounts": [("Purchase Receipt", "read"), ("Delivery Note", "read")],
	"Management": [("Stock Reconciliation", "submit"), ("Purchase Order", "cancel")],
}


@verifies("URS-CMP-014", "URS-CMP-015")
class TestDailyWork(ProfileTestCase):
	"""Least privilege must still leave each job able to do its work."""

	def test_each_job_can_do_its_daily_work(self):
		self.assertEqual(set(DAILY_WORK) | {"Operator", "System Administrator"}, set(JOB_PROFILES))
		for profile, work in DAILY_WORK.items():
			for doctype, permission in work:
				with self.subTest(profile=profile, doctype=doctype, permission=permission):
					self.assertTrue(can(self.users[profile], doctype, permission))

	def test_jobs_do_not_do_each_others_work(self):
		refused = {
			"Purchase": [("Purchase Receipt", "create"), ("Purchase Order", "submit")],
			"Sales": [("Delivery Note", "cancel"), ("Stock Entry", "read")],
			"Production Supervisor": [("BOM", "submit")],
			"Stores": [("Quality Inspection", "create"), ("Batch", "write")],
			"Accounts": [("Stock Entry", "create")],
		}
		for profile, work in refused.items():
			for doctype, permission in work:
				with self.subTest(profile=profile, doctype=doctype, permission=permission):
					self.assertFalse(can(self.users[profile], doctype, permission))


@verifies("URS-CMP-014", "URS-CMP-015")
class TestSupportingPermissions(ProfileTestCase):
	def test_stores_can_select_an_account_but_not_read_it(self):
		stores = self.users["Stores"]

		self.assertTrue(can(stores, "Account", "select"))
		self.assertFalse(can(stores, "Account", "read"))
		self.assertFalse(can(stores, "Account", "write"))

	def test_erpnext_permissions_on_the_record_type_are_kept(self):
		for doctype in SUPPORTING_PERMISSIONS:
			standard = frappe.get_all(
				"DocPerm", filters={"parent": doctype}, fields=["role", "permlevel", *FLAGS]
			)
			applied = applied_rows(doctype)
			for row in standard:
				with self.subTest(doctype=doctype, role=row.role):
					for flag in FLAGS:
						self.assertGreaterEqual(
							applied[(row.role, row.permlevel)][flag], int(row.get(flag) or 0), flag
						)


@verifies("URS-CMP-016")
class TestNoDeletion(ProfileTestCase):
	def test_no_job_may_delete_share_or_import_a_regulated_record(self):
		for profile, user in self.users.items():
			for doctype in BUSINESS_RECORD_TYPES:
				for permission in ("delete", "share", "import"):
					with self.subTest(profile=profile, doctype=doctype, permission=permission):
						self.assertFalse(can(user, doctype, permission))


@verifies("URS-CMP-017")
class TestAdministrators(ProfileTestCase):
	def test_system_administrators_read_but_never_change_regulated_records(self):
		user = self.users["System Administrator"]
		for doctype in BUSINESS_RECORD_TYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(can(user, doctype, "read"))
				for permission in ("write", "create", "submit", "cancel", "amend"):
					self.assertFalse(can(user, doctype, permission), permission)


@verifies("URS-CMP-018")
class TestSegregationOfDuties(ProfileTestCase):
	def test_inspectors_record_inspections_and_only_qa_corrects_them(self):
		inspector, qa = self.users["QC Inspector"], self.users["QA Manager"]

		for permission in ("create", "write", "submit"):
			self.assertTrue(can(inspector, "Quality Inspection", permission), permission)
		for permission in ("cancel", "amend"):
			self.assertFalse(can(inspector, "Quality Inspection", permission), permission)
			self.assertTrue(can(qa, "Quality Inspection", permission), permission)

		for profile in set(self.users) - {"QC Inspector", "QA Manager"}:
			with self.subTest(profile=profile):
				self.assertFalse(can(self.users[profile], "Quality Inspection", "submit"))

	def test_operators_record_their_operations_only(self):
		operator = self.users["Operator"]

		self.assertTrue(can(operator, "Job Card", "write"))
		for doctype in ("Job Card", "Work Order", "Stock Entry"):
			for permission in ("create", "submit", "cancel"):
				with self.subTest(doctype=doctype, permission=permission):
					self.assertFalse(can(operator, doctype, permission))
