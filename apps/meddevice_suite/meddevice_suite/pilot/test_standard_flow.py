from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from meddevice_suite.pilot import flow
from meddevice_suite.pilot.demo import CUSTOMERS, PRODUCT_SHELF_LIFE_DAYS, load_demo_data
from meddevice_suite.pilot.test_demo import PILOT
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration

PRODUCT = "FC-2W-SIL-16"
QUANTITY = 200


def audited(doctype: str, name: str) -> list[str]:
	return frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		pluck="action",
		order_by="event_at",
	)


class FlowTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		with patch.dict(frappe.conf, PILOT):
			load_demo_data()


@verifies("URS-PLT-007")
class TestStandardFlow(FlowTestCase):
	def test_enquiry_to_delivery_with_each_job_doing_its_part(self):
		# Sales: enquiry, quotation, price approval, sales order (D1)
		quotation = flow.quote(flow.record_enquiry(CUSTOMERS[0], PRODUCT, QUANTITY))
		self.assertEqual(quotation.workflow_state, "Awaiting Price Approval")
		quotation = flow.approve_price(quotation)
		order = flow.book_sales_order(quotation)

		# Purchasing and receiving: one approved purchase order per supplier, into quarantine (D1, D2)
		receipts = flow.buy(flow.materials_for(PRODUCT, QUANTITY))
		for receipt in receipts:
			for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
				with self.subTest(material=row.item_code):
					self.assertEqual(row.warehouse, flow.store("Quarantine"))
					self.assertTrue(frappe.db.get_value("Batch", flow.lot_of(row), "expiry_date"))
			flow.move_to_approved_store(receipt)

		# Production: work order on the D3 routing, materials issued, operations logged, finished lot (D2, D3)
		work_order = flow.plan_production(order, PRODUCT, QUANTITY)
		flow.issue_materials(work_order)
		cards = flow.record_operations(work_order)
		self.assertEqual(len(cards), len(frappe.get_doc("BOM", work_order.bom_no).operations))
		finished = flow.finish_production(work_order)
		lot = next(flow.lot_of(row) for row in finished.items if row.item_code == PRODUCT)
		self.assertEqual(
			getdate(frappe.db.get_value("Batch", lot, "expiry_date")),
			getdate(add_days(today(), PRODUCT_SHELF_LIFE_DAYS)),
		)

		# Dispatch: the finished lot goes to the customer (D2, D3)
		delivery = flow.deliver(order)
		self.assertEqual([flow.lot_of(row) for row in delivery.items], [lot])
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "per_delivered"), 100)

		# Every regulated step is in the audit trail, approvals included.
		self.assertIn("Submitted", audited("Purchase Receipt", receipts[0].name))
		self.assertIn("Submitted", audited("Work Order", work_order.name))
		self.assertIn("Submitted", audited("Delivery Note", delivery.name))
		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "workflow_state"), "Approved")


@verifies("URS-STK-002")
class TestExpiryFirst(FlowTestCase):
	def test_the_lot_that_expires_first_is_issued_first_even_if_received_later(self):
		later, sooner = (flow.buy({"RM-SILICONE": 2})[0] for _ in range(2))
		later_lot, sooner_lot = (
			flow.lot_of(frappe.get_doc("Purchase Receipt", r.name).items[0]) for r in (later, sooner)
		)
		# Expiry follows the supplier's lot (its certificate), not the day it arrived.
		frappe.db.set_value("Batch", later_lot, "expiry_date", add_days(today(), 600))
		frappe.db.set_value("Batch", sooner_lot, "expiry_date", add_days(today(), 200))
		flow.move_to_approved_store(later)
		flow.move_to_approved_store(sooner)

		with flow.as_job("Stores"):
			issue = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Issue",
					"company": flow.company(),
					"items": [{"item_code": "RM-SILICONE", "qty": 1, "s_warehouse": flow.store("Stores")}],
				}
			).insert()
			issue.submit()

		bundle = frappe.get_doc("Serial and Batch Bundle", issue.items[0].serial_and_batch_bundle)
		self.assertEqual([entry.batch_no for entry in bundle.entries], [sooner_lot])


@verifies("URS-PLT-006")
class TestActingAsAJobOnlyOnAPilotSite(IntegrationTestCase):
	def test_switching_to_a_job_is_refused_outside_a_pilot_site_and_tests(self):
		with (
			patch.object(frappe, "in_test", False),
			patch.dict(frappe.conf, {"pilot_site": 0}),
			self.assertRaises(frappe.ValidationError),
			flow.as_job("Stores"),
		):
			pass
		self.assertEqual(frappe.session.user, "Administrator")
