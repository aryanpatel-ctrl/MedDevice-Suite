from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import check_password

from meddevice_suite.pilot.demo import (
	CUSTOMERS,
	DEMO,
	EXTRA_STORES,
	PRODUCTS,
	RAW_MATERIALS,
	ROUTINGS,
	SUPPLIERS,
	demo_user_email,
	load_demo_data,
)
from meddevice_suite.pilot.site import prepare_pilot_site
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.roles import JOB_PROFILES

PASSWORD = "Pl7!vDemo#Walk2026"  # test site only
PILOT = {"pilot_site": 1, "pilot_user_password": PASSWORD}
AUTOMATIC_ROLES = {"All", "Guest", "Desk User"}
COUNTED = (
	"Item",
	"BOM",
	"Routing",
	"Operation",
	"Workstation",
	"Warehouse",
	"Supplier",
	"Customer",
	"User",
	"Item Price",
	"Device Master Record",
)


class PilotTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		with patch.dict(frappe.conf, PILOT):
			load_demo_data()
		cls.abbr = frappe.db.get_value("Company", frappe.defaults.get_global_default("company"), "abbr")


@verifies("URS-PLT-006")
class TestOnlyOnAPilotSite(IntegrationTestCase):
	def test_demo_data_is_refused_on_any_other_site(self):
		with patch.dict(frappe.conf, {"pilot_site": 0}), self.assertRaises(frappe.ValidationError):
			load_demo_data()

	def test_pilot_setup_is_refused_on_any_other_site(self):
		with patch.dict(frappe.conf, {"pilot_site": 0}), self.assertRaises(frappe.ValidationError):
			prepare_pilot_site()

	def test_the_demo_password_must_come_from_the_site_config(self):
		with (
			patch.dict(frappe.conf, {"pilot_site": 1, "pilot_user_password": ""}),
			self.assertRaises(frappe.ValidationError),
		):
			load_demo_data()

	def test_a_pilot_site_whose_setup_is_complete_is_left_as_it_is(self):
		with patch.dict(frappe.conf, PILOT):
			prepare_pilot_site()

		self.assertTrue(frappe.is_setup_complete())


@verifies("URS-PLT-006")
class TestDemoData(PilotTestCase):
	def test_products_and_raw_materials_are_batch_tracked_with_an_expiry(self):
		for code in (*RAW_MATERIALS, *PRODUCTS):
			with self.subTest(item=code):
				item = frappe.db.get_value(
					"Item",
					code,
					["has_batch_no", "create_new_batch", "has_expiry_date", "gst_hsn_code"],
					as_dict=True,
				)
				self.assertEqual((item.has_batch_no, item.create_new_batch, item.has_expiry_date), (1, 1, 1))
				self.assertTrue(item.gst_hsn_code)

	def test_raw_materials_are_received_into_quarantine(self):
		for code in RAW_MATERIALS:
			with self.subTest(item=code):
				self.assertEqual(
					frappe.db.get_value("Item Default", {"parent": code}, "default_warehouse"),
					f"Stores - {self.abbr}",  # the approved store a release moves the lot to
				)

	def test_the_quarantine_rejected_and_hold_stores_exist(self):
		for name in EXTRA_STORES:
			with self.subTest(store=name):
				self.assertTrue(frappe.db.exists("Warehouse", f"{name} - {self.abbr}"))

	def test_each_line_follows_the_d3_process_steps_in_order(self):
		for line, steps in ROUTINGS.items():
			with self.subTest(line=line):
				operations = frappe.get_all(
					"BOM Operation",
					filters={"parent": line, "parenttype": "Routing"},
					pluck="operation",
					order_by="sequence_id",
				)
				self.assertEqual(operations, [operation for operation, _, _ in steps])

	def test_every_product_has_a_submitted_default_bom_on_its_line(self):
		for code, (_name, _hsn, _rate, line, materials) in PRODUCTS.items():
			with self.subTest(product=code):
				bom = frappe.get_doc(
					"BOM", frappe.db.get_value("BOM", {"item": code, "is_default": 1, "docstatus": 1})
				)
				self.assertEqual(bom.routing, line)
				self.assertEqual({row.item_code: row.qty for row in bom.items}, materials)
				self.assertEqual(
					[row.operation for row in bom.operations], [step[0] for step in ROUTINGS[line]]
				)

	@verifies("URS-QMS-031")
	def test_every_product_can_be_put_into_production_on_its_current_device_master_record(self):
		for code in PRODUCTS:
			with self.subTest(product=code):
				name = frappe.db.get_value(
					"Device Master Record", {"item": code, "status": "Current", "docstatus": 1}
				)
				record = frappe.get_doc("Device Master Record", name)
				bom = frappe.db.get_value("BOM", {"item": code, "is_default": 1, "docstatus": 1})
				self.assertIn(
					("BOM", bom), [(row.reference_doctype, row.reference_name) for row in record.elements]
				)
				self.assertEqual(record.author, demo_user_email("Production Supervisor"))
				self.assertEqual(record.approver, demo_user_email("QA Manager"))

	def test_each_job_has_a_demo_user_with_exactly_its_roles(self):
		for profile, roles in JOB_PROFILES.items():
			user = demo_user_email(profile)
			with self.subTest(profile=profile):
				self.assertEqual(set(frappe.get_roles(user)) - AUTOMATIC_ROLES, set(roles))
				check_password(user, PASSWORD)

	def test_every_party_and_user_is_marked_as_demo(self):
		for name in (*SUPPLIERS, *CUSTOMERS):
			with self.subTest(party=name):
				self.assertTrue(name.endswith(DEMO))
		for profile in JOB_PROFILES:
			with self.subTest(user=profile):
				self.assertEqual(frappe.db.get_value("User", demo_user_email(profile), "last_name"), DEMO)

	def test_loading_again_adds_nothing(self):
		before = {doctype: frappe.db.count(doctype) for doctype in COUNTED}

		with patch.dict(frappe.conf, PILOT):
			load_demo_data()

		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in COUNTED}, before)
