from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import check_password

from meddevice_suite.qa import verifies
from meddevice_suite.qa.ui_users import email_for, prepare_job_users, reachable
from meddevice_suite.setup.lookups import REVIEWED_CLOSED
from meddevice_suite.setup.roles import JOB_PROFILES, apply_roles


@verifies("URS-PLT-009")
class TestBrowserTestUsers(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def setUp(self):
		self.enterContext(patch.dict(frappe.local.conf, {"allow_tests": 1}))

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for helper in (prepare_job_users, reachable):
				with self.subTest(helper=helper.__name__), self.assertRaises(frappe.PermissionError):
					helper()

	def test_only_the_administrator_makes_the_users(self):
		users = prepare_job_users()["users"]
		frappe.set_user(users["System Administrator"])

		with self.assertRaises(frappe.PermissionError):
			prepare_job_users()

	def test_makes_one_user_per_job_with_a_password_for_this_run(self):
		first = prepare_job_users()
		second = prepare_job_users()

		self.assertEqual(list(second["users"]), list(JOB_PROFILES))
		self.assertNotEqual(first["password"], second["password"])
		for profile, email in second["users"].items():
			with self.subTest(profile=profile):
				self.assertEqual(email, email_for(profile))
				user = frappe.get_doc("User", email)
				self.assertTrue(user.enabled)
				self.assertEqual([row.role_profile for row in user.role_profiles], [profile])
				self.assertEqual(check_password(email, second["password"]), email)
				with self.assertRaises(frappe.AuthenticationError):
					check_password(email, first["password"])

	def test_brings_a_changed_user_back_to_its_job(self):
		email = prepare_job_users()["users"]["Sales"]
		user = frappe.get_doc("User", email)
		user.enabled = 0
		user.set("role_profiles", [{"role_profile": "Purchase"}])
		user.save(ignore_permissions=True)

		prepare_job_users()

		user.reload()
		self.assertTrue(user.enabled)
		self.assertEqual([row.role_profile for row in user.role_profiles], ["Sales"])

	def test_describes_what_the_session_users_desk_reaches(self):
		frappe.set_user(prepare_job_users()["users"]["Stores"])

		reach = reachable()

		self.assertEqual(set(reach), {"areas", "lists", "reports", "create", "closed_lookups"})
		self.assertTrue({"Buying", "Stock"} <= set(reach["areas"]))
		self.assertTrue({"Purchase Receipt", "Stock Entry", "Delivery Note"} <= set(reach["create"]))
		self.assertTrue(set(reach["create"]) <= set(reach["lists"]))
		self.assertIn("Item", reach["lists"])  # read, never created, by stores
		self.assertNotIn("Item", reach["create"])
		# lots are created by the receipt, never in their own form (roles.CREATED_THROUGH_TRANSACTIONS)
		self.assertIn("Batch", reach["lists"])
		self.assertNotIn("Batch", reach["create"])
		self.assertIn("Stock Ledger", reach["reports"])
		self.assertEqual(
			reach["closed_lookups"], sorted([doctype, field] for doctype, field in REVIEWED_CLOSED["Stores"])
		)

	def test_includes_the_areas_without_a_workspace_of_their_own(self):
		# The organization area is a sidebar only: its records are listed all the same.
		frappe.set_user(prepare_job_users()["users"]["Training Coordinator"])

		reach = reachable()

		self.assertIn("Employee", reach["create"])
		self.assertIn("Department", reach["lists"])

	def test_leaves_out_forms_the_desk_does_not_offer_or_the_system_writes(self):
		frappe.set_user(prepare_job_users()["users"]["System Administrator"])

		reach = reachable()

		self.assertIn("User", reach["create"])
		for doctype in ("Error Log", "Scheduled Job Log"):  # cannot be created; written by the system
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, reach["lists"])
				self.assertNotIn(doctype, reach["create"])

	def test_offers_only_new_forms_the_user_may_open(self):
		for profile in ("Operator", "QA Manager"):
			frappe.set_user("Administrator")
			frappe.set_user(prepare_job_users()["users"][profile])
			with self.subTest(profile=profile):
				for doctype in reachable()["create"]:
					meta = frappe.get_meta(doctype)
					self.assertFalse(meta.istable or meta.issingle or meta.custom, doctype)
					self.assertTrue(frappe.has_permission(doctype, "create"), doctype)
