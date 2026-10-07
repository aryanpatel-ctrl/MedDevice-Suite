from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.pilot import flow
from meddevice_suite.pilot.demo import CUSTOMERS, ROUTINGS, URINE_BAG_LINE
from meddevice_suite.pilot.samples import (
	AWAITING_PRICE_APPROVAL,
	AWAITING_PURCHASE_APPROVAL,
	DELIVERED,
	IN_PRODUCTION,
	IN_QUARANTINE,
	load_sample_records,
)
from meddevice_suite.pilot.test_demo import PILOT
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration

COUNTED = (
	"Opportunity",
	"Quotation",
	"Sales Order",
	"Purchase Order",
	"Purchase Receipt",
	"Work Order",
	"Delivery Note",
)


@verifies("URS-PLT-006", "URS-PLT-007")
class TestSampleRecords(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		with patch.dict(frappe.conf, PILOT):
			load_sample_records()

	def test_an_order_was_delivered(self):
		item, qty = DELIVERED
		order = frappe.get_all(
			"Sales Order Item",
			filters={"item_code": item, "qty": qty},
			pluck="parent",
			order_by="creation desc",
		)[0]
		self.assertEqual(
			frappe.db.get_value("Sales Order", order, ["customer", "per_delivered"]), (CUSTOMERS[0], 100)
		)

	def test_an_order_is_in_production_with_job_cards_open_for_the_operators(self):
		item, qty = IN_PRODUCTION
		work_order = frappe.get_all(
			"Work Order",
			filters={"production_item": item, "qty": qty},
			pluck="name",
			order_by="creation desc",
		)[0]
		self.assertEqual(frappe.db.get_value("Work Order", work_order, "status"), "In Process")
		self.assertEqual(
			frappe.db.count("Job Card", {"work_order": work_order, "docstatus": 0}),
			len(ROUTINGS[URINE_BAG_LINE]),
		)

	def test_a_quotation_and_a_purchase_order_wait_for_approval(self):
		item, _ = AWAITING_PRICE_APPROVAL
		quotation = frappe.get_all("Quotation Item", filters={"item_code": item}, pluck="parent")[0]
		self.assertEqual(
			frappe.db.get_value("Quotation", quotation, "workflow_state"), "Awaiting Price Approval"
		)

		material = next(iter(AWAITING_PURCHASE_APPROVAL))
		orders = frappe.get_all("Purchase Order Item", filters={"item_code": material}, pluck="parent")
		states = {frappe.db.get_value("Purchase Order", order, "workflow_state") for order in orders}
		self.assertIn("Awaiting Approval", states)

	def test_material_waits_in_quarantine(self):
		material, qty = next(iter(IN_QUARANTINE.items()))

		self.assertGreaterEqual(
			frappe.db.get_value(
				"Bin", {"item_code": material, "warehouse": flow.store("Quarantine")}, "actual_qty"
			),
			qty,
		)

	def test_loading_again_adds_nothing(self):
		before = {doctype: frappe.db.count(doctype) for doctype in COUNTED}

		with patch.dict(frappe.conf, PILOT):
			load_sample_records()

		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in COUNTED}, before)


class TestSamplesOnlyOnAPilotSite(IntegrationTestCase):
	@verifies("URS-PLT-006")
	def test_sample_records_are_refused_on_any_other_site(self):
		with patch.dict(frappe.conf, {"pilot_site": 0}), self.assertRaises(frappe.ValidationError):
			load_sample_records()
