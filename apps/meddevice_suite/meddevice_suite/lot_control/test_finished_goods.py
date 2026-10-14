from unittest.mock import patch

import frappe
from frappe.model.document import Document

from meddevice_suite.lot_control import finished_goods, stores
from meddevice_suite.lot_control.finished_goods import FinishedGoodNotProduced, as_opening_stock
from meddevice_suite.lot_control.lot_status import QUARANTINE, LotNotApproved, status_of
from meddevice_suite.lot_control.receipt import made_by_production, material_receipt_of
from meddevice_suite.lot_control.test_lot_status import company, lot_item, qty_in, store
from meddevice_suite.lot_control.test_release import FINISHED_ITEM, ITEM, ReleaseTestCase, made_by_work_order
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.setup.test_stock import approve_for_test

FROM_PRODUCTION = "comes into stock only from production"


def material_receipt(item: str, warehouse: str, opening: bool = False) -> Document:
	"""A material receipt of one unit of `item` into `warehouse`, as built before it is saved."""
	approve_for_test(item)  # module 1: only approved items move
	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company(),
			"items": [{"item_code": item, "qty": 1, "t_warehouse": warehouse, "basic_rate": 10}],
		}
	)
	return as_opening_stock(entry) if opening else entry


@verifies("URS-STK-013")
class TestFinishedGoodsComeFromProduction(ReleaseTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		lot_item(FINISHED_ITEM, "Products")

	def test_a_material_receipt_of_a_finished_good_is_refused_on_the_form(self):
		frappe.set_user(self.stores_user)

		with self.assertRaises(FinishedGoodNotProduced) as refused:
			material_receipt(FINISHED_ITEM, store(stores.QUARANTINE)).insert()

		self.assertIn(FROM_PRODUCTION, str(refused.exception))
		self.assertIn("Only opening stock at migration", str(refused.exception))

	def test_a_material_receipt_of_a_finished_good_is_refused_at_the_stock_ledger(self):
		"""Whatever posts it past the form: the ledger, which every posting passes, refuses it too."""
		frappe.set_user("Administrator")
		with patch.object(finished_goods, "guard_stock_entry", lambda doc, method=None: None):
			entry = material_receipt(FINISHED_ITEM, store(stores.QUARANTINE)).insert()
			with self.assertRaises(FinishedGoodNotProduced):
				entry.submit()
		self.assertIn(
			"meddevice_suite.lot_control.finished_goods.guard_stock_ledger_entry",
			frappe.get_hooks("doc_events")["Stock Ledger Entry"]["validate"],
		)

	def test_other_material_receipts_and_cancellations_are_not_held_to_it(self):
		frappe.set_user("Administrator")
		entry = material_receipt(FINISHED_ITEM, store(stores.QUARANTINE), opening=True).insert()
		ledger = frappe.get_doc(
			{
				"doctype": "Stock Ledger Entry",
				"item_code": FINISHED_ITEM,
				"warehouse": store(stores.QUARANTINE),
				"actual_qty": 1,
				"voucher_type": "Stock Entry",
				"voucher_no": entry.name,
				"is_cancelled": 0,
			}
		)
		finished_goods.guard_stock_ledger_entry(ledger)  # opening stock
		frappe.db.set_value("Stock Entry", entry.name, "is_opening", "No")
		with self.assertRaises(FinishedGoodNotProduced):
			finished_goods.guard_stock_ledger_entry(ledger)
		ledger.is_cancelled = 1  # the reversal of a posting made before this rule
		finished_goods.guard_stock_ledger_entry(ledger)
		ledger.is_cancelled, ledger.item_code = 0, ITEM  # a material, not a finished good
		finished_goods.guard_stock_ledger_entry(ledger)

	def test_opening_stock_of_a_finished_good_comes_in_as_a_quarantine_lot_for_qa_to_release(self):
		frappe.set_user(self.stores_user)
		entry = material_receipt(FINISHED_ITEM, store(stores.QUARANTINE), opening=True).insert()
		entry.submit()

		lot = lot_of(frappe.get_doc("Stock Entry", entry.name).items[0])
		self.assertEqual(entry.is_opening, "Yes")
		self.assertEqual(status_of([lot])[lot], QUARANTINE)
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 1)
		# Inspected on that entry and released by QA, as any material receipt (decided 2026-10-09).
		self.assertEqual(material_receipt_of(lot), entry.name)
		with self.assertRaisesRegex(frappe.ValidationError, "has no accepted incoming inspection"):
			self.decide(lot, "Release", "Opening stock counted at migration")
		self.assertEqual(status_of([lot])[lot], QUARANTINE)

	def test_opening_stock_of_a_finished_good_does_not_come_into_an_approved_store(self):
		frappe.set_user(self.stores_user)
		with self.assertRaises(LotNotApproved):
			material_receipt(FINISHED_ITEM, store("Finished Goods"), opening=True).insert().submit()

	def test_production_brings_a_finished_good_in(self):
		lot = made_by_work_order()

		self.assertTrue(made_by_production(lot))
		self.assertEqual(status_of([lot])[lot], QUARANTINE)
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 1)
