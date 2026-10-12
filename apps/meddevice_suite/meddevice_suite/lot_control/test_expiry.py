"""Expired lots and first expiry, first out (module 18; URS-STK-008, URS-STK-009): tests derived
from the requirements' clauses."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.expiry import REASON_FIELD
from meddevice_suite.lot_control.lot_status import APPROVED, REJECTED, holdings_of, move_to_role, set_status
from meddevice_suite.lot_control.report.lots_taken_out_of_expiry_order.lots_taken_out_of_expiry_order import (
	execute as exceptions_report,
)
from meddevice_suite.lot_control.test_lot_status import (
	CUSTOMER,
	company,
	ensure_parties,
	lot_item,
	purchase_receive,
	receive,
	stock_entry,
	store,
)
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import approve_for_test

EXPIRED = "Lot expired"


class ExpiryTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()

	def setUp(self):
		frappe.set_user("Administrator")
		self.item = lot_item(f"MDS-EXP-{frappe.generate_hash(length=6)}")
		approve_for_test(self.item)
		self.stores = store("Stores")

	def lot(self, expires_in_days: int, qty: float = 5, warehouse: str | None = None, status=APPROVED) -> str:
		"""A lot of this test's item, in the approved store unless told, expiring in so many days."""
		lot = receive(self.item, qty, warehouse or self.stores)
		set_status(lot, status, "Set for the test")  # as QA's decision leaves it (module 16)
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), expires_in_days))
		return lot

	def row(self, lot: str, qty: float = 1, **values) -> dict:
		return {
			"item_code": self.item,
			"qty": qty,
			"s_warehouse": self.stores,
			"batch_no": lot,
			"use_serial_batch_fields": 1,
			**values,
		}

	def issue(self, *rows: dict, **values):
		return stock_entry("Material Issue", list(rows), **values)

	def delivery(self, lot: str, qty: float = 1, **values):
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": CUSTOMER,
				"company": company(),
				"items": [
					{
						"item_code": self.item,
						"qty": qty,
						"rate": 50,
						"warehouse": self.stores,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
				**values,
			}
		).insert()
		note.submit()
		return note


@verifies("URS-STK-008")
class TestExpiredLots(ExpiryTestCase):
	def test_an_expired_lot_is_not_issued(self):
		lot = self.lot(-1)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {lot} expired"):
			self.issue(self.row(lot))

	def test_an_expired_lot_is_not_delivered(self):
		lot = self.lot(-1)
		with self.assertRaisesRegex(frappe.ValidationError, "expired"):  # ERPNext refuses it first
			self.delivery(lot)

	def test_an_expired_lot_does_not_go_to_production(self):
		lot = self.lot(-1)
		with self.assertRaises(frappe.ValidationError):  # ERPNext refuses this one too
			stock_entry(
				"Material Transfer for Manufacture",
				[self.row(lot, t_warehouse=store("Work In Progress"))],
			)

	def test_an_expired_lot_is_not_moved_to_another_usable_store(self):
		lot = self.lot(-1)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {lot} expired"):
			stock_entry("Material Transfer", [self.row(lot, t_warehouse=store("Finished Goods"))])

	def test_an_expired_lot_is_moved_to_the_hold_store(self):
		lot = self.lot(-1)
		moved = stock_entry("Material Transfer", [self.row(lot, qty=5, t_warehouse=store(stores.HOLD))])
		self.assertEqual(moved.docstatus, 1)

	def test_an_expired_lot_goes_back_to_its_supplier(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		receipt = purchase_receive(self.item, 4)
		lot = lot_of(receipt.items[0])
		set_status(lot, REJECTED, "Rejected for the test")
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), -1))
		# Into the rejected store, as QA's rejection moves it: rejected goods go back from there only
		# (URS-STK-010).
		move_to_role(frappe.get_doc("Batch", lot), stores.REJECTED, "Rejected for the test", holdings_of(lot))
		back = make_return_doc("Purchase Receipt", receipt.name)
		for row in back.items:
			row.update(
				{
					"warehouse": store(stores.REJECTED),
					"batch_no": lot,
					"use_serial_batch_fields": 1,
					"serial_and_batch_bundle": None,
				}
			)
		back.insert()
		back.submit()
		self.assertEqual(back.docstatus, 1)

	def test_a_lot_expiring_today_is_still_usable(self):
		lot = self.lot(0)
		self.assertEqual(self.issue(self.row(lot)).docstatus, 1)


@verifies("URS-STK-009")
class TestFirstExpiryFirstOut(ExpiryTestCase):
	def test_a_later_lot_is_refused_while_an_earlier_one_is_in_the_store(self):
		sooner, later = self.lot(30), self.lot(200)
		with self.assertRaisesRegex(
			frappe.ValidationError, f"Lot {sooner} expires on .*, before lot {later}"
		):
			self.issue(self.row(later))
		self.assertEqual(self.issue(self.row(sooner)).docstatus, 1)

	def test_the_same_holds_for_a_delivery(self):
		sooner, later = self.lot(30), self.lot(200)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {sooner} expires"):
			self.delivery(later)

	def test_a_stated_reason_lets_it_through_and_qa_sees_it(self):
		self.lot(30)
		later = self.lot(200)
		reason = "Customer asked for one lot with the longest shelf life (test)"
		entry = self.issue(self.row(later), **{REASON_FIELD: reason})
		self.assertEqual(entry.docstatus, 1)
		frappe.set_user("Administrator")
		_columns, rows = exceptions_report({})
		self.assertIn((entry.name, reason), [(row["document"], row["reason"]) for row in rows])

	def test_a_blank_reason_is_no_reason(self):
		sooner, later = self.lot(30), self.lot(200)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {sooner} expires"):
			self.issue(self.row(later), **{REASON_FIELD: "   "})

	def test_the_earlier_lot_taken_in_full_by_the_same_document_is_first(self):
		sooner, later = self.lot(30, qty=2), self.lot(200)
		entry = self.issue(self.row(later), self.row(sooner, qty=2))  # the later lot's line first
		self.assertEqual(entry.docstatus, 1)

	def test_the_earlier_lot_taken_in_part_by_the_same_document_still_comes_first(self):
		sooner, later = self.lot(30, qty=3), self.lot(200)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {sooner} expires"):
			self.issue(self.row(sooner, qty=2), self.row(later))

	def test_an_earlier_lot_that_is_expired_not_approved_or_elsewhere_does_not_count(self):
		self.lot(-1)  # expired: it goes to the hold store, not to use
		self.lot(20, status=REJECTED)
		self.lot(25, warehouse=store("Finished Goods"))
		later = self.lot(200)
		self.assertEqual(self.issue(self.row(later)).docstatus, 1)

	def test_a_lot_without_an_expiry_date_goes_after_those_with_one(self):
		dated = self.lot(30)
		undated = self.lot(30)
		frappe.db.set_value("Batch", undated, "expiry_date", None)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {dated} expires"):
			self.issue(self.row(undated))

	def test_moving_stock_between_stores_is_not_a_use(self):
		self.lot(30)
		later = self.lot(200)
		moved = stock_entry("Material Transfer", [self.row(later, t_warehouse=store("Finished Goods"))])
		self.assertEqual(moved.docstatus, 1)

	def test_without_a_named_lot_the_one_expiring_first_is_taken(self):
		sooner, _later = self.lot(30), self.lot(200)
		entry = self.issue({"item_code": self.item, "qty": 1, "s_warehouse": self.stores})
		bundle = frappe.get_doc("Serial and Batch Bundle", entry.items[0].serial_and_batch_bundle)
		self.assertEqual([row.batch_no for row in bundle.entries], [sooner])
