import frappe
from frappe.desk.search import search_link
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.lookups import REVIEWED_CLOSED, closed_lookups
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile


@verifies("URS-CMP-036")
class TestEveryJobCanUseItsForms(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_only_the_reviewed_lookups_stay_closed(self):
		self.assertEqual(closed_lookups(), REVIEWED_CLOSED)

	def test_the_lookups_found_closed_in_the_pilot_now_search(self):
		for profile, doctype, reference in (
			("Operator", "Employee", "Job Card Time Log"),
			("Operator", "Item", "Job Card Item"),
			("Production Supervisor", "Stock Entry Type", "Stock Entry"),
			("QC Inspector", "Quality Inspection Parameter", "Quality Inspection Reading"),
			("Maintenance", "Asset Maintenance", "Asset Maintenance Log"),
			# found by the browser test of each job (URS-PLT-009)
			("Sales", "Sales Stage", "Opportunity"),
			("QC Inspector", "Quality Feedback Template", "Quality Feedback"),
			("Production Supervisor", "Quality Feedback Template", "Quality Feedback"),
			("Accounts", "Sales Taxes and Charges Template", "Sales Invoice"),
		):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile, doctype=doctype):
				search_link(doctype, "", reference_doctype=reference)

	def test_maintenance_picks_a_task_through_the_forms_own_query(self):
		from erpnext.assets.doctype.asset_maintenance_log.asset_maintenance_log import get_maintenance_tasks

		frappe.set_user(user_with_profile("Maintenance"))
		get_maintenance_tasks("Asset Maintenance Task", "", "name", 0, 10, {"asset_maintenance": "none"})

	def test_picking_never_opens_the_record(self):
		frappe.set_user(user_with_profile("Operator"))

		self.assertFalse(frappe.has_permission("Employee", "read"))
		self.assertFalse(frappe.has_permission("Item", "read"))

	def test_stock_and_production_forms_can_read_the_stock_settings(self):
		for profile in ("Stores", "Production Supervisor"):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile):
				frappe.client.get_single_value("Stock Settings", "disable_serial_no_and_batch_selector")
				self.assertFalse(frappe.has_permission("Stock Settings", "write"))

	@verifies("URS-PLT-009")
	def test_forms_can_read_what_they_fetch_on_opening(self):
		company = frappe.get_all("Company", pluck="name", limit=1)[0]
		# a new material request, inspection, asset or repair reads the company's letter head
		for profile in ("Production Supervisor", "QC Inspector", "QA Manager", "Maintenance"):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile):
				frappe.client.get_value("Company", "default_letter_head", company)
				self.assertFalse(frappe.has_permission("Company", "write"))
		# the chart of accounts and the GST tools
		frappe.set_user(user_with_profile("Accounts"))
		frappe.client.get_single_value("Accounts Settings", "show_balance_in_coa")
		frappe.get_list("Accounting Dimension", fields=["fieldname"])
		self.assertFalse(frappe.has_permission("Accounts Settings", "write"))
		self.assertFalse(frappe.has_permission("Accounting Dimension", "write"))

	@verifies("URS-PLT-009", "URS-PLT-008")
	def test_the_reports_of_a_jobs_areas_can_read_their_records(self):
		for profile, doctype in (
			("Production Supervisor", "Sales Order"),  # Exponential Smoothing Forecasting
			("Production Supervisor", "Fiscal Year"),  # Job Card Summary
			("QA Manager", "Fiscal Year"),  # Fixed Asset Register
			("Purchase", "Serial and Batch Bundle"),  # Serial No and Batch Traceability
			("Management", "Sales Invoice"),  # Sales Analytics
			("Management", "Payment Entry"),
		):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile, doctype=doctype):
				self.assertTrue(frappe.has_permission(doctype, "read"))
				self.assertFalse(frappe.has_permission(doctype, "write"))
