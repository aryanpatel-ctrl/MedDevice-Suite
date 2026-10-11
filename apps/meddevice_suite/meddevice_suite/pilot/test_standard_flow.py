from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from meddevice_suite.lot_control.lot_status import STATUS_FIELD
from meddevice_suite.pilot import flow
from meddevice_suite.pilot.demo import CUSTOMERS, PRODUCT_SHELF_LIFE_DAYS, demo_user_email, load_demo_data
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
		# The pilot's configuration for the whole class: the demo users sign with its password.
		cls.enterClassContext(patch.dict(frappe.conf, PILOT))
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
					self.assertEqual(
						frappe.db.get_value("Batch", flow.lot_of(row), STATUS_FIELD), "Quarantine"
					)
			# QA releases what quarantine holds, with a signature; the lots become Approved in the
			# approved store.
			for decision in flow.release_received_lots(receipt):
				self.assertEqual((decision.decision, decision.docstatus), ("Release", 1))
				self.assertTrue(decision.quality_inspection)
				moved = frappe.get_doc("Stock Entry", decision.stock_entry)
				self.assertEqual({row.t_warehouse for row in moved.items}, {flow.store("Stores")})
			for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
				self.assertEqual(frappe.db.get_value("Batch", flow.lot_of(row), STATUS_FIELD), "Approved")

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
		# The finished lot waits in quarantine until QA releases it to the finished goods store.
		self.assertEqual(frappe.db.get_value("Batch", lot, STATUS_FIELD), "Quarantine")
		self.assertEqual(flow.release_finished_lot(finished), lot)
		self.assertEqual(frappe.db.get_value("Batch", lot, STATUS_FIELD), "Approved")

		# Dispatch: the finished lot goes to the customer (D2, D3)
		delivery = flow.deliver(order)
		self.assertEqual([flow.lot_of(row) for row in delivery.items], [lot])
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "per_delivered"), 100)

		# Every regulated step is in the audit trail, approvals included.
		self.assertIn("Submitted", audited("Purchase Receipt", receipts[0].name))
		self.assertIn("Submitted", audited("Work Order", work_order.name))
		self.assertIn("Submitted", audited("Delivery Note", delivery.name))
		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "workflow_state"), "Approved")

	@verifies("URS-PLT-010")
	def test_every_lot_passes_qc_and_qa_on_its_way_in_and_out(self):
		"""Each received lot has its certificate verified and is inspected by the QC Inspector, and
		only the QA Manager's signed release brings it into the approved store; the finished lot
		reaches the finished goods store the same way."""
		qc, qa = demo_user_email("QC Inspector"), demo_user_email("QA Manager")
		quotation = flow.approve_price(flow.quote(flow.record_enquiry(CUSTOMERS[0], PRODUCT, QUANTITY)))
		order = flow.book_sales_order(quotation)

		for receipt in flow.buy(flow.materials_for(PRODUCT, QUANTITY)):
			decisions = {decision.batch: decision for decision in flow.release_received_lots(receipt)}
			for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
				lot = flow.lot_of(row)
				with self.subTest(material=row.item_code):
					self.assertEqual((row.mds_certificate_verified, row.mds_certificate_verified_by), (1, qc))
					inspection = frappe.get_doc("Quality Inspection", decisions[lot].quality_inspection)
					self.assertEqual(
						(
							inspection.inspection_type,
							inspection.status,
							inspection.docstatus,
							inspection.owner,
						),
						("Incoming", "Accepted", 1, qc),
					)
					self.assertReleasedBy(decisions[lot], qa)
					self.assertEqual(brought_into(lot, flow.store("Stores")), [decisions[lot].stock_entry])

		work_order = flow.plan_production(order, PRODUCT, QUANTITY)
		flow.issue_materials(work_order)
		flow.record_operations(work_order)
		lot = flow.release_finished_lot(flow.finish_production(work_order))
		release = frappe.get_last_doc("Lot Release", filters={"batch": lot, "docstatus": 1})
		self.assertReleasedBy(release, qa)
		self.assertEqual(brought_into(lot, flow.store("Finished Goods")), [release.stock_entry])

	def assertReleasedBy(self, decision, signer: str) -> None:
		self.assertEqual((decision.decision, decision.docstatus), ("Release", 1))
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Lot Release", "reference_name": decision.name},
			fields=["signer", "action"],
		)
		self.assertEqual([(s.signer, s.action) for s in signatures], [(signer, "Release")])


def brought_into(lot: str, warehouse: str) -> list[str]:
	"""The documents that brought stock of the lot into the store."""
	return frappe.get_all(
		"Serial and Batch Bundle",
		filters=[
			["Serial and Batch Entry", "batch_no", "=", lot],
			["warehouse", "=", warehouse],
			["type_of_transaction", "=", "Inward"],
			["is_cancelled", "=", 0],
			["docstatus", "=", 1],
		],
		pluck="voucher_no",
		distinct=True,
	)


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
		flow.release_received_lots(later)
		flow.release_received_lots(sooner)

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
