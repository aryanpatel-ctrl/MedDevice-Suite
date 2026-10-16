from unittest.mock import patch

import frappe
from frappe.model.document import Document

from meddevice_suite.lot_control import finished_goods, lot_status, stores
from meddevice_suite.lot_control.finished_goods import (
	STOCK_ENTRY,
	STOCK_RECONCILIATION,
	WAYS_IN,
	FinishedGoodNotProduced,
	as_opening_stock,
	may_enter,
)
from meddevice_suite.lot_control.lot_status import QUARANTINE, LotNotApproved, status_of
from meddevice_suite.lot_control.receipt import made_by_production, material_receipt_of
from meddevice_suite.lot_control.test_lot_status import company, lot_item, qty_in, store
from meddevice_suite.lot_control.test_release import FINISHED_ITEM, ITEM, ReleaseTestCase, made_by_work_order
from meddevice_suite.qa import verifies
from meddevice_suite.setup.fixtures.records import loaded_as_data_import as loaded
from meddevice_suite.setup.fixtures.records import lot_of
from meddevice_suite.setup.test_stock import approve_for_test

FROM_PRODUCTION = "comes into stock only from production"

# The guardrail, stated again on its own: how a finished good may come in on each voucher type and
# stock entry purpose that ERPNext has. "always": made by production, or moved between stores;
# "opening": only as opening stock at migration; "return": only as a customer's goods coming back;
# "never": not at all, or it brings no stock in. A path ERPNext adds fails the tests until it is
# declared in finished_goods.WAYS_IN and here.
ALWAYS, OPENING, RETURN, NEVER = "always", "opening", "return", "never"
EXPECTED: dict[tuple[str, str | None], str] = {
	(STOCK_ENTRY, "Manufacture"): ALWAYS,
	(STOCK_ENTRY, "Repack"): ALWAYS,
	(STOCK_ENTRY, "Material Transfer"): ALWAYS,
	(STOCK_ENTRY, "Material Transfer for Manufacture"): ALWAYS,
	(STOCK_ENTRY, "Send to Subcontractor"): ALWAYS,
	(STOCK_ENTRY, "Material Receipt"): OPENING,
	(STOCK_ENTRY, "Material Issue"): NEVER,
	(STOCK_ENTRY, "Material Consumption for Manufacture"): NEVER,
	(STOCK_ENTRY, "Return Raw Material to Customer"): NEVER,
	(STOCK_ENTRY, "Subcontracting Delivery"): NEVER,
	(STOCK_ENTRY, "Disassemble"): NEVER,
	(STOCK_ENTRY, "Receive from Customer"): NEVER,
	(STOCK_ENTRY, "Subcontracting Return"): NEVER,
	(STOCK_RECONCILIATION, None): NEVER,  # a new lot never; a count of a lot in stock is counted_back's
	("Purchase Receipt", None): NEVER,
	("Purchase Invoice", None): NEVER,
	("Subcontracting Receipt", None): NEVER,
	("Asset Capitalization", None): NEVER,
	("Delivery Note", None): RETURN,
	("Sales Invoice", None): RETURN,
	("POS Invoice", None): RETURN,
	**dict.fromkeys(
		(
			(doctype, None)
			for doctype in (
				"Lead",
				"Quotation",
				"Sales Order",
				"Supplier Quotation",
				"Request for Quotation",
				"Purchase Order",
				"Material Request",
				"Subcontracting Order",
				"Subcontracting Inward Order",
				"Serial No",
			)
		),
		NEVER,
	),
}


def stock_voucher_types() -> set[str]:
	"""Every record type of the installed apps that ERPNext treats as a stock voucher (its controller
	is a StockController), whether or not it posts stock."""
	from erpnext.controllers.stock_controller import StockController
	from frappe.model.base_document import get_controller

	modules = [
		module
		for app in frappe.get_installed_apps()
		if app != "frappe"
		for module in frappe.get_module_list(app)
	]
	doctypes = frappe.get_all(
		"DocType",
		filters={"module": ["in", modules], "istable": 0, "issingle": 0, "is_virtual": 0, "custom": 0},
		pluck="name",
	)
	return {doctype for doctype in doctypes if issubclass(get_controller(doctype), StockController)}


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
		with patch.object(finished_goods, "guard_document", lambda doc, method=None: None):
			entry = material_receipt(FINISHED_ITEM, store(stores.QUARANTINE)).insert()
			with self.assertRaises(FinishedGoodNotProduced):
				entry.submit()
		self.assertIn(
			"meddevice_suite.lot_control.finished_goods.guard_stock_ledger_entry",
			frappe.get_hooks("doc_events")["Stock Ledger Entry"]["validate"],
		)

	def test_other_material_receipts_and_cancellations_are_not_held_to_it(self):
		frappe.set_user("Administrator")
		entry = loaded(material_receipt(FINISHED_ITEM, store(stores.QUARANTINE), opening=True), submit=False)
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
		# Changed past its form, as no posting does: read afresh, as its next submission would be.
		lot_status.forget_vouchers()
		with self.assertRaises(FinishedGoodNotProduced):
			finished_goods.guard_stock_ledger_entry(ledger)
		ledger.is_cancelled = 1  # the reversal of a posting made before this rule
		finished_goods.guard_stock_ledger_entry(ledger)
		ledger.is_cancelled, ledger.item_code = 0, ITEM  # a material, not a finished good
		finished_goods.guard_stock_ledger_entry(ledger)

	def test_opening_stock_of_a_finished_good_comes_in_as_a_quarantine_lot_for_qa_to_release(self):
		frappe.set_user(self.stores_user)
		entry = loaded(material_receipt(FINISHED_ITEM, store(stores.QUARANTINE), opening=True))

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
			loaded(material_receipt(FINISHED_ITEM, store("Finished Goods"), opening=True))

	def test_production_brings_a_finished_good_in(self):
		lot = made_by_work_order()

		self.assertTrue(made_by_production(lot))
		self.assertEqual(status_of([lot])[lot], QUARANTINE)
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 1)

	def test_a_finished_good_bought_in_on_a_goods_receipt_is_refused(self):
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": company(),
				"items": [{"item_code": FINISHED_ITEM, "qty": 1, "warehouse": store(stores.QUARANTINE)}],
			}
		)
		with self.assertRaises(FinishedGoodNotProduced) as refused:
			finished_goods.guard_document(receipt)
		self.assertIn(FROM_PRODUCTION, str(refused.exception))
		self.assertIn("Purchase Receipt", str(refused.exception))
		receipt.items[0].item_code = ITEM
		finished_goods.guard_document(receipt)  # materials are bought in as before

		ledger = self.ledger_entry("Purchase Receipt", "PR-NOT-STORED")
		with self.assertRaises(FinishedGoodNotProduced):
			finished_goods.guard_stock_ledger_entry(ledger)
		for doctype in ("Purchase Receipt", STOCK_RECONCILIATION):
			with self.subTest(doctype=doctype):
				self.assertIn(
					"meddevice_suite.lot_control.finished_goods.guard_document",
					frappe.get_hooks("doc_events")[doctype]["validate"],
				)

	def test_a_count_never_brings_a_new_lot_of_a_finished_good_in_not_even_as_opening_stock(self):
		frappe.set_user("Administrator")
		approve_for_test(FINISHED_ITEM)
		new_lot = frappe.get_doc({"doctype": "Batch", "item": FINISHED_ITEM}).insert().name

		for purpose in ("Stock Reconciliation", "Opening Stock"):
			count = self.count(new_lot)
			count.purpose = purpose
			with self.subTest(purpose=purpose), self.assertRaises(FinishedGoodNotProduced) as refused:
				finished_goods.guard_document(count)
			# Pointed to the path QA inspects and releases (decided 2026-10-09).
			self.assertIn("material receipt with Is Opening set to Yes", str(refused.exception))
		with self.assertRaises(FinishedGoodNotProduced):
			finished_goods.guard_stock_ledger_entry(
				self.ledger_entry(STOCK_RECONCILIATION, "SR-NOT-STORED", new_lot)
			)
		with self.assertRaises(frappe.ValidationError):
			as_opening_stock(self.count(new_lot))  # opening stock is a material receipt only

	def test_a_count_of_a_finished_lot_already_in_stock_is_left_to_the_count_rule(self):
		frappe.set_user("Administrator")
		entry = loaded(material_receipt(FINISHED_ITEM, store(stores.QUARANTINE), opening=True))
		lot = lot_of(frappe.get_doc("Stock Entry", entry.name).items[0])

		finished_goods.guard_document(self.count(lot))  # not new stock: lot_status.counted_back decides
		finished_goods.guard_stock_ledger_entry(self.ledger_entry(STOCK_RECONCILIATION, "SR-NOT-STORED", lot))

	def test_every_way_stock_comes_in_is_declared_and_answered(self):
		purposes = set(frappe.get_meta("Stock Entry").get_field("purpose").options.split("\n")) - {""}
		self.assertEqual({purpose for doctype, purpose in WAYS_IN if doctype == STOCK_ENTRY}, purposes)
		self.assertEqual({doctype for doctype, _purpose in WAYS_IN}, stock_voucher_types())
		self.assertEqual(set(WAYS_IN), set(EXPECTED))
		for (doctype, purpose), answer in EXPECTED.items():
			for opening in (False, True):
				for is_return in (False, True):
					expected = (
						answer == ALWAYS
						or (answer == OPENING and opening)
						or (answer == RETURN and is_return)
					)
					with self.subTest(doctype=doctype, purpose=purpose, opening=opening, is_return=is_return):
						self.assertEqual(may_enter(doctype, purpose, opening, is_return), expected)
		self.assertFalse(may_enter("A Voucher Type Not Declared"))
		self.assertFalse(may_enter(STOCK_ENTRY, "A Purpose Not Declared", opening=True))

	def ledger_entry(self, voucher_type: str, voucher_no: str, lot: str | None = None) -> Document:
		"""A stock ledger entry bringing one unit of the finished good in on the voucher, as built
		before it is validated."""
		return frappe.get_doc(
			{
				"doctype": "Stock Ledger Entry",
				"item_code": FINISHED_ITEM,
				"warehouse": store(stores.QUARANTINE),
				"actual_qty": 1,
				"voucher_type": voucher_type,
				"voucher_no": voucher_no,
				"batch_no": lot,
				"is_cancelled": 0,
			}
		)

	def count(self, lot: str) -> Document:
		"""Stores' count of one unit of the lot in the Quarantine store, as built before it is saved."""
		return frappe.get_doc(
			{
				"doctype": STOCK_RECONCILIATION,
				"company": company(),
				"purpose": "Stock Reconciliation",
				"items": [
					{
						"item_code": FINISHED_ITEM,
						"warehouse": store(stores.QUARANTINE),
						"qty": 1,
						"valuation_rate": 10,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)


@verifies("URS-STK-015")
class TestOpeningStockIsTheDataLoads(ReleaseTestCase):
	"""Independent review 2026-10-15: Is Opening, set by hand on a material receipt, brought a finished
	good in outside production, or a material from no approved supplier, for QA to release on an
	incoming inspection. Opening stock is the go-live data load's alone."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		lot_item(FINISHED_ITEM, "Products")

	def opening_count(self) -> Document:
		approve_for_test(ITEM)
		return frappe.get_doc(
			{
				"doctype": STOCK_RECONCILIATION,
				"company": company(),
				"purpose": "Opening Stock",
				"items": [
					{
						"item_code": ITEM,
						"warehouse": store(stores.QUARANTINE),
						"qty": 1,
						"valuation_rate": 10,
					}
				],
			}
		)

	def test_opening_stock_is_refused_on_the_form_and_through_the_api(self):
		for item in (ITEM, FINISHED_ITEM):
			frappe.set_user(self.stores_user)
			with self.subTest(item=item):
				with self.assertRaises(frappe.PermissionError) as refused:
					material_receipt(item, store(stores.QUARANTINE), opening=True).insert()
				self.assertIn("loaded once at go-live through Data Import", str(refused.exception))
		frappe.set_user("Administrator")  # not even the Administrator, outside the data load
		with self.assertRaises(frappe.PermissionError):
			material_receipt(ITEM, store(stores.QUARANTINE), opening=True).insert()
		with self.assertRaises(frappe.PermissionError):
			finished_goods.guard_opening_stock(self.opening_count())

	def test_opening_stock_comes_in_through_the_data_load_into_quarantine(self):
		frappe.set_user(self.stores_user)
		entry = loaded(material_receipt(ITEM, store(stores.QUARANTINE), opening=True))

		lot = lot_of(frappe.get_doc("Stock Entry", entry.name).items[0])
		self.assertEqual(status_of([lot])[lot], QUARANTINE)
		self.assertEqual(material_receipt_of(lot), entry.name)  # inspected on it, released by QA
		for flag in ("in_import", "in_migrate", "in_install"):
			with self.subTest(flag=flag), patch.dict(frappe.flags, {flag: True}):
				finished_goods.guard_opening_stock(self.opening_count())

	def test_other_stock_entries_and_counts_are_not_held_to_it(self):
		frappe.set_user("Administrator")
		finished_goods.guard_opening_stock(material_receipt(ITEM, store(stores.QUARANTINE)))
		count = self.opening_count()
		count.purpose = "Stock Reconciliation"
		finished_goods.guard_opening_stock(count)

	def test_every_opening_entry_is_checked_first(self):
		for doctype in (STOCK_ENTRY, STOCK_RECONCILIATION):
			hooks = frappe.get_hooks("doc_events")[doctype]["validate"]
			with self.subTest(doctype=doctype):
				self.assertLess(
					hooks.index("meddevice_suite.lot_control.finished_goods.guard_opening_stock"),
					hooks.index("meddevice_suite.lot_control.finished_goods.guard_document"),
				)
