from unittest.mock import patch

import frappe
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return
from frappe.model.document import Document
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control import lot_status, release, stores
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	HOLD,
	QUARANTINE,
	REJECTED,
	STATUS_FIELD,
	TRANSFER_FIELD,
	LotNotApproved,
	initial_status,
	set_status,
	status_of,
	stock_by_lot,
)
from meddevice_suite.lot_control.report.stock_by_lot_status.stock_by_lot_status import execute as report
from meddevice_suite.lot_control.stores import role_of, store_for
from meddevice_suite.patches.v0_1 import set_initial_lot_status
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import approve_for_test, licensed_for_test, stock_item

RAW = "MDS-LOT-RAW"
PRODUCT = "MDS-LOT-PRODUCT"
SUPPLIER = "MDS Lot Supplier"
CUSTOMER = "MDS Lot Customer"


def company() -> str:
	return frappe.defaults.get_global_default("company")


def store(role_or_name: str) -> str:
	if role_or_name in stores.ROLES:
		return store_for(role_or_name, company())
	return frappe.db.get_value("Warehouse", {"company": company(), "warehouse_name": role_or_name})


def lot_item(code: str, group: str = "Raw Material") -> str:
	return stock_item(
		code,
		item_group=group,
		has_batch_no=1,
		create_new_batch=1,
		batch_number_series=f"{code}-.####",
		has_expiry_date=1,
		shelf_life_in_days=365,
		item_defaults=[{"company": company(), "default_warehouse": store("Stores")}],
	)


def ensure_parties() -> None:
	if not frappe.db.exists("Supplier", SUPPLIER):
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": SUPPLIER, "supplier_group": "Raw Material"}
		).insert()
	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER,
				"customer_group": "Commercial",
				"territory": "India",
			}
		).insert()
	licensed_for_test(CUSTOMER)  # module 5: delivered to only under a verified licence


def stock_entry(purpose: str, rows: list[dict], submit: bool = True, **values) -> Document:
	approve_for_test(*(row["item_code"] for row in rows))  # module 1: only approved items move
	entry = frappe.get_doc(
		{"doctype": "Stock Entry", "stock_entry_type": purpose, "company": company(), "items": rows, **values}
	)
	entry.insert()
	if submit:
		entry.submit()
	return frappe.get_doc("Stock Entry", entry.name)


def receive(item: str, qty: float, warehouse: str) -> str:
	"""A new lot of `item` into `warehouse` by a material receipt; returns the lot."""
	entry = stock_entry(
		"Material Receipt", [{"item_code": item, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10}]
	)
	return lot_of(entry.items[0])


def purchase_receive(item: str, qty: float, warehouse: str | None = None, submit: bool = True) -> Document:
	# Goods come only from a supplier QA approved for them (module 4). Imported here: that test
	# module builds on this one.
	from meddevice_suite.master_data.test_supplier_approval import approved_for

	approved_for(SUPPLIER, [item])
	approve_for_test(item)  # module 1: only approved items are received
	receipt = frappe.get_doc(
		{
			"doctype": "Purchase Receipt",
			"supplier": SUPPLIER,
			"company": company(),
			"items": [
				{
					"item_code": item,
					"qty": qty,
					"rate": 10,
					"warehouse": warehouse or store(stores.QUARANTINE),
				}
			],
		}
	)
	receipt.insert()
	if submit:
		receipt.submit()
	return frappe.get_doc("Purchase Receipt", receipt.name)


def move(item: str, lot: str, qty: float, source: str, target: str, submit: bool = True) -> Document:
	return stock_entry(
		"Material Transfer",
		[
			{
				"item_code": item,
				"qty": qty,
				"s_warehouse": source,
				"t_warehouse": target,
				"batch_no": lot,
				"use_serial_batch_fields": 1,
			}
		],
		submit=submit,
	)


def issue(item: str, lot: str, qty: float, source: str) -> Document:
	return stock_entry(
		"Material Issue",
		[
			{
				"item_code": item,
				"qty": qty,
				"s_warehouse": source,
				"batch_no": lot,
				"use_serial_batch_fields": 1,
			}
		],
	)


def qty_in(lot: str, warehouse: str) -> float:
	return sum(row.qty for row in stock_by_lot() if row.batch_no == lot and row.warehouse == warehouse)


def audit_actions(doctype: str, name: str) -> list[str]:
	return frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		pluck="action",
		order_by="event_at",
	)


class LotTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		lot_item(RAW)
		lot_item(PRODUCT, "Products")
		ensure_parties()
		cls.qa = signer("QA Manager")
		cls.stores_user = signer("Stores")
		cls.qc = signer("QC Inspector")
		from meddevice_suite.pilot.inspection import ensure_inspection_masters, ensure_inspection_template

		cls.instrument = ensure_inspection_masters(company(), approve_for_test)
		cls.template = ensure_inspection_template(RAW, "Raw Material")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def as_qa(self):
		frappe.set_user(self.qa)

	def quarantined(self, qty: float = 10) -> str:
		"""A new lot in quarantine, received by Stores (the Administrator here), whoever is acting."""
		acting = frappe.session.user
		frappe.set_user("Administrator")
		try:
			return receive(RAW, qty, store(stores.QUARANTINE))
		finally:
			frappe.set_user(acting)

	def inspected_on_its_entry(self, lot: str) -> None:
		"""The QC Inspector's accepted incoming inspection of a material-receipt lot, made on the
		entry that brought it in, as QA's release needs (URS-QC-003, decided 2026-10-09)."""
		from meddevice_suite.lot_control.receipt import material_receipt_of
		from meddevice_suite.master_data.specification import INCOMING, specification_for
		from meddevice_suite.pilot.inspection import readings_for

		entry = material_receipt_of(lot)
		if not entry:
			return
		item = frappe.db.get_value("Batch", lot, "item")
		frappe.set_user(self.qc)
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Stock Entry",
				"reference_name": entry,
				"item_code": item,
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": self.qc,
				"readings": readings_for(specification_for(item, INCOMING), self.instrument),
			}
		).insert()
		inspection.submit()

	def decide(self, lot: str, decision: str, reason: str = "decided in a test") -> Document:
		"""QA's signed decision on the lot, whoever is acting; a release is preceded by the incoming
		inspection the lot needs. A supplier's lot is covered in test_release."""
		acting = frappe.session.user
		if decision == "Release":
			self.inspected_on_its_entry(lot)
		self.as_qa()
		try:
			record = frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
			act = {"Release": release.release, "Reject": release.reject, "Hold": release.hold}[decision]
			act(record, password=PASSWORD, reason=reason)
			return frappe.get_doc("Lot Release", record.name)
		finally:
			frappe.set_user(acting)

	def approved(self, qty: float = 10) -> str:
		"""A lot QA has released to the approved store, whoever is acting."""
		lot = self.quarantined(qty)
		self.decide(lot, "Release", "accepted")
		return lot


@verifies("URS-STK-004")
class TestALotIsBornInQuarantine(LotTestCase):
	def test_a_received_lot_starts_in_quarantine_whatever_it_was_sent_with(self):
		lot = frappe.get_doc(
			{
				"doctype": "Batch",
				"item": RAW,
				"expiry_date": add_days(today(), 30),
				STATUS_FIELD: APPROVED,
				"mds_status_set_by": self.qa,
			}
		).insert()

		self.assertEqual(lot.get(STATUS_FIELD), QUARANTINE)
		self.assertIsNone(lot.mds_status_set_by)
		self.assertIsNone(lot.mds_status_set_at)

	def test_a_lot_from_a_purchase_receipt_is_quarantine(self):
		receipt = purchase_receive(RAW, 5)

		self.assertEqual(status_of([lot_of(receipt.items[0])])[lot_of(receipt.items[0])], QUARANTINE)

	def test_the_status_cannot_be_edited_on_the_lot(self):
		lot = frappe.get_doc("Batch", self.quarantined())
		lot.set(STATUS_FIELD, APPROVED)

		with self.assertRaises(frappe.PermissionError):
			lot.save()
		self.assertEqual(frappe.db.get_value("Batch", lot.name, STATUS_FIELD), QUARANTINE)

	def test_the_status_cannot_be_set_through_the_api_even_by_qa(self):
		lot = self.quarantined()
		self.as_qa()

		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Batch", lot, STATUS_FIELD, APPROVED)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Batch", lot, "mds_status_reason", "typed in")

	def test_other_fields_of_the_lot_can_still_be_edited_by_qa(self):
		lot = frappe.get_doc("Batch", self.quarantined())
		self.as_qa()
		lot.description = "silicone, drum 3"
		lot.save()

		self.assertEqual(frappe.db.get_value("Batch", lot.name, "description"), "silicone, drum 3")

	def test_the_service_records_who_when_and_why_in_the_audit_trail(self):
		lot = self.quarantined()
		self.as_qa()

		set_status(lot, HOLD, "label unreadable")

		stored = frappe.get_doc("Batch", lot)
		self.assertEqual(
			(stored.get(STATUS_FIELD), stored.mds_status_set_by, stored.mds_status_reason),
			(HOLD, self.qa, "label unreadable"),
		)
		self.assertTrue(stored.mds_status_set_at)
		self.assertIn("Updated", audit_actions("Batch", lot))
		event = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": "Batch", "reference_name": lot, "action": "Updated"},
			fields=["changes"],
			order_by="event_at desc",
			limit=1,
		)[0]
		self.assertIn([STATUS_FIELD, QUARANTINE, HOLD], frappe.parse_json(event.changes)["changed"])

	def test_the_service_refuses_an_empty_reason_an_unknown_status_and_an_endless_reason(self):
		lot = self.quarantined()
		with self.assertRaises(frappe.ValidationError):
			set_status(lot, HOLD, "  ")
		with self.assertRaises(frappe.ValidationError):
			set_status(lot, "Released", "x")
		with self.assertRaises(frappe.ValidationError):
			set_status(lot, HOLD, "x" * 1001)


@verifies("URS-STK-005")
class TestOnlyAnApprovedLotLeaves(LotTestCase):
	def test_a_quarantined_lot_cannot_be_issued(self):
		lot = self.quarantined()
		with self.assertRaises(LotNotApproved) as refused:
			issue(RAW, lot, 1, store(stores.QUARANTINE))
		self.assertIn("is Quarantine and cannot leave", str(refused.exception))
		self.assertIn("QA releases it after incoming inspection", str(refused.exception))

	def test_a_quarantined_lot_cannot_be_moved_to_the_approved_store(self):
		lot = self.quarantined()
		with self.assertRaises(LotNotApproved):
			move(RAW, lot, 1, store(stores.QUARANTINE), store("Stores"))
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 10)

	def test_what_a_lot_may_still_do_within_its_own_kind_of_store(self):
		allowed = lot_status.movement_allowed
		self.assertTrue(
			allowed(QUARANTINE, "Stock Entry", "Material Transfer", target_role=stores.QUARANTINE)
		)
		self.assertTrue(allowed(HOLD, "Stock Entry", "Material Transfer", target_role=stores.HOLD))
		self.assertTrue(allowed(REJECTED, "Stock Entry", "Material Transfer", target_role=stores.RETURNS))
		self.assertFalse(allowed(QUARANTINE, "Stock Entry", "Material Transfer", target_role=stores.RETURNS))
		self.assertFalse(allowed(HOLD, "Stock Entry", "Material Issue"))
		self.assertTrue(allowed(QUARANTINE, "Stock Entry", "Material Issue", lot_release_transfer=True))

	def test_the_ledger_refuses_what_a_form_let_through(self):
		# A document whose rows name no lot is checked where the lot is finally known.
		lot = self.quarantined()
		entry = frappe.get_doc(
			{
				"doctype": "Stock Ledger Entry",
				"item_code": RAW,
				"warehouse": store(stores.QUARANTINE),
				"batch_no": lot,
				"actual_qty": -1,
				"voucher_type": "Delivery Note",
				"voucher_no": "DN-TEST",
				"is_cancelled": 0,
			}
		)
		with self.assertRaises(LotNotApproved):
			lot_status.guard_stock_ledger_entry(entry)
		entry.is_cancelled = 1
		lot_status.guard_stock_ledger_entry(entry)  # the reversal of a cancelled document
		entry.is_cancelled, entry.actual_qty = 0, 1
		with self.assertRaisesRegex(frappe.ValidationError, "new stock comes in as a new lot"):
			lot_status.guard_stock_ledger_entry(
				entry
			)  # more stock into a lot already in, on another document
		entry.voucher_type, entry.voucher_no = (
			"Stock Entry",
			frappe.db.get_value("Batch", lot, "reference_name"),
		)
		lot_status.guard_stock_ledger_entry(entry)  # the stock its own receipt brings in
		entry.actual_qty, entry.voucher_type, entry.voucher_no = -1, "Stock Reconciliation", "SR-TEST"
		lot_status.guard_stock_ledger_entry(entry)  # a counted loss

	def test_a_quarantined_lot_cannot_be_delivered(self):
		lot = self.quarantined()
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": CUSTOMER,
				"company": company(),
				"items": [
					{
						"item_code": RAW,
						"qty": 1,
						"rate": 10,
						"warehouse": store(stores.QUARANTINE),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		with self.assertRaises(LotNotApproved):
			note.insert()

	def test_a_lot_held_after_the_note_was_saved_cannot_be_packed(self):
		lot = self.approved()
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": CUSTOMER,
				"company": company(),
				"items": [
					{
						"item_code": RAW,
						"qty": 1,
						"rate": 10,
						"warehouse": store("Stores"),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		).insert()
		self.decide(lot, "Hold", "customer complaint under investigation")
		slip = frappe.get_doc(
			{
				"doctype": "Packing Slip",
				"delivery_note": note.name,
				"from_case_no": 1,
				"to_case_no": 1,
				"items": [{"item_code": RAW, "qty": 1, "dn_detail": note.items[0].name}],
			}
		)
		with self.assertRaises(LotNotApproved):
			slip.insert()

	def test_a_quarantined_lot_cannot_be_picked(self):
		lot = self.quarantined()
		pick = frappe.get_doc(
			{
				"doctype": "Pick List",
				"company": company(),
				"purpose": "Material Transfer",
				"locations": [
					{
						"item_code": RAW,
						"qty": 1,
						"stock_qty": 1,
						"warehouse": store(stores.QUARANTINE),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		with self.assertRaises(LotNotApproved):
			pick.insert()

	def test_an_approved_lot_is_issued_moved_and_delivered(self):
		lot = self.approved()
		move(RAW, lot, 2, store("Stores"), store("Work In Progress"))
		issue(RAW, lot, 1, store("Stores"))
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": CUSTOMER,
				"company": company(),
				"items": [
					{
						"item_code": RAW,
						"qty": 1,
						"rate": 10,
						"warehouse": store("Stores"),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		note.insert()
		note.submit()

		self.assertEqual(qty_in(lot, store("Stores")), 6)

	def test_a_held_lot_waits_in_the_hold_store(self):
		lot = self.quarantined()
		held = self.decide(lot, "Hold", "label unreadable")

		moved = frappe.get_doc("Stock Entry", held.stock_entry)
		self.assertEqual({row.t_warehouse for row in moved.items}, {store(stores.HOLD)})
		self.assertEqual(status_of([lot])[lot], HOLD)
		with self.assertRaises(LotNotApproved):
			move(RAW, lot, 1, store(stores.HOLD), store("Stores"))
		self.decide(lot, "Release", "retested and accepted")
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(qty_in(lot, store("Stores")), 10)

	def test_an_approved_lot_can_be_put_on_hold_and_comes_back_to_the_hold_store(self):
		lot = self.approved()
		self.decide(lot, "Hold", "customer complaint under investigation")

		self.assertEqual(qty_in(lot, store(stores.HOLD)), 10)
		self.assertEqual(qty_in(lot, store("Stores")), 0)

	def test_a_rejected_lot_goes_back_to_its_supplier_or_is_scrapped_and_nowhere_else(self):
		lot = self.quarantined(6)
		self.decide(lot, "Reject", "certificate does not match")
		self.assertEqual(qty_in(lot, store(stores.REJECTED)), 6)

		with self.assertRaises(LotNotApproved):
			move(RAW, lot, 1, store(stores.REJECTED), store("Stores"))
		move(RAW, lot, 1, store(stores.REJECTED), store(stores.RETURNS))
		issue(RAW, lot, 1, store(stores.REJECTED))
		self.assertEqual(qty_in(lot, store(stores.REJECTED)), 4)

		# A supplier's lot, once Rejected, goes back on a purchase return from the rejected store.
		receipt = purchase_receive(RAW, 6)
		supplier_lot = lot_of(receipt.items[0])
		set_status(supplier_lot, REJECTED, "test: rejected without the record")
		move(RAW, supplier_lot, 6, store(stores.QUARANTINE), store(stores.REJECTED))
		back = make_purchase_return(receipt.name)
		for row in back.items:
			row.qty, row.received_qty, row.warehouse, row.batch_no, row.use_serial_batch_fields = (
				-2,
				-2,
				store(stores.REJECTED),
				supplier_lot,
				1,
			)
		back.insert()
		back.submit()
		self.assertEqual(qty_in(supplier_lot, store(stores.REJECTED)), 4)

	def test_a_work_order_draws_from_an_approved_store_and_finishes_into_quarantine(self):
		approve_for_test(PRODUCT, RAW)  # module 1: production uses approved items only
		bom = frappe.db.get_value("BOM", {"item": PRODUCT, "docstatus": 1}) or (
			frappe.get_doc(
				{
					"doctype": "BOM",
					"item": PRODUCT,
					"company": company(),
					"quantity": 1,
					"is_active": 1,
					"is_default": 1,
					"items": [{"item_code": RAW, "qty": 1}],
				}
			)
			.insert()
			.submit()
			.name
		)

		def work_order(**warehouses) -> Document:
			return frappe.get_doc(
				{
					"doctype": "Work Order",
					"production_item": PRODUCT,
					"bom_no": bom,
					"qty": 1,
					"company": company(),
					"source_warehouse": store("Stores"),
					"wip_warehouse": store("Work In Progress"),
					"fg_warehouse": store(stores.QUARANTINE),
					**warehouses,
				}
			)

		with self.assertRaises(frappe.ValidationError) as refused:
			work_order(source_warehouse=store(stores.QUARANTINE)).insert()
		self.assertIn("draws materials from an approved store", str(refused.exception))
		with self.assertRaises(frappe.ValidationError) as refused:
			work_order(fg_warehouse=store("Finished Goods")).insert()
		self.assertIn("goes to the Quarantine store until QA releases it", str(refused.exception))
		work_order().insert()

		# Production Plan and Subcontracting Inward Order make their work orders with validation
		# skipped; the stores are checked on those too, not only when the order is submitted.
		def unvalidated(**warehouses) -> Document:
			order = work_order(**warehouses)
			order.flags.ignore_validate = order.flags.ignore_mandatory = True
			return order

		with self.assertRaisesRegex(frappe.ValidationError, "draws materials from an approved store"):
			unvalidated(wip_warehouse=store(stores.QUARANTINE)).insert()
		with self.assertRaisesRegex(
			frappe.ValidationError, "goes to the Quarantine store until QA releases it"
		):
			unvalidated(fg_warehouse=store("Finished Goods")).insert()
		unvalidated().insert()

	def test_goods_are_received_into_the_quarantine_store_whatever_store_was_named(self):
		receipt = purchase_receive(RAW, 1, warehouse=store("Stores"))

		self.assertEqual(receipt.items[0].warehouse, store(stores.QUARANTINE))
		self.assertEqual(qty_in(lot_of(receipt.items[0]), store(stores.QUARANTINE)), 1)
		self.assertEqual(qty_in(lot_of(receipt.items[0]), store("Stores")), 0)

	def test_quantity_rejected_at_the_door_goes_to_the_rejected_store(self):
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": SUPPLIER,
				"company": company(),
				"set_warehouse": store("Stores"),
				"items": [
					{
						"item_code": RAW,
						"qty": 1,
						"received_qty": 2,
						"rejected_qty": 1,
						"rate": 10,
						"warehouse": store("Stores"),
						"rejected_warehouse": store(stores.HOLD),  # a store, but not the Rejected one
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError) as refused:
			receipt.insert()
		self.assertIn("rejected at receipt goes to the Rejected store", str(refused.exception))
		self.assertEqual(receipt.set_warehouse, store(stores.QUARANTINE))

	def test_only_the_release_marks_its_transfer(self):
		lot = self.approved()
		with self.assertRaises(frappe.PermissionError):
			stock_entry(
				"Material Transfer",
				[
					{
						"item_code": RAW,
						"qty": 1,
						"s_warehouse": store("Stores"),
						"t_warehouse": store("Work In Progress"),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
				**{TRANSFER_FIELD: 1},
			)


@verifies("URS-STK-004", "URS-STK-005")
class TestTheDecisionMovesTheStock(LotTestCase):
	def test_release_moves_the_stock_to_the_approved_store_and_approves_the_lot(self):
		lot = self.quarantined(7)

		decision = self.decide(lot, "Release", "accepted")

		moved = frappe.get_doc("Stock Entry", decision.stock_entry)
		self.assertTrue(moved.get(TRANSFER_FIELD))
		self.assertEqual(moved.remarks, "accepted")
		self.assertEqual(
			[(row.s_warehouse, row.t_warehouse, row.qty) for row in moved.items],
			[(store(stores.QUARANTINE), store("Stores"), 7)],
		)
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(frappe.db.get_value("Batch", lot, "mds_status_set_by"), self.qa)
		self.assertEqual(frappe.db.get_value("Batch", lot, "mds_lot_release"), decision.name)
		self.assertIn("Submitted", audit_actions("Stock Entry", moved.name))

	def test_qa_posts_no_stock_entry_other_than_a_release(self):
		lot = self.approved()
		self.as_qa()
		with self.assertRaises(frappe.PermissionError) as refused:
			move(RAW, lot, 1, store("Stores"), store("Work In Progress"))
		self.assertIn("QA moves a lot through its release", str(refused.exception))

	def test_the_release_transfer_is_never_cancelled(self):
		decision = self.decide(self.quarantined(), "Release", "accepted")
		moved = frappe.get_doc("Stock Entry", decision.stock_entry)
		with self.assertRaises(frappe.PermissionError) as refused:
			moved.cancel()
		self.assertIn("put the lot on hold instead", str(refused.exception))

	def test_a_lot_already_in_the_approved_store_is_approved_without_a_transfer(self):
		# Opening stock posted straight into the approved store is still Quarantine until QA says so.
		lot = receive(RAW, 2, store("Stores"))

		decision = self.decide(lot, "Release", "opening stock, certificates checked")

		self.assertIsNone(decision.stock_entry)
		self.assertEqual(status_of([lot])[lot], APPROVED)

	def test_release_needs_an_approved_default_store_on_the_item(self):
		code = stock_item(
			"MDS-LOT-NO-STORE",
			has_batch_no=1,
			create_new_batch=1,
			has_expiry_date=1,
			shelf_life_in_days=30,
			item_defaults=[{"company": company(), "default_warehouse": store(stores.QUARANTINE)}],
		)
		from meddevice_suite.pilot.inspection import ensure_inspection_template

		ensure_inspection_template(code, "Raw Material")  # inspected first, so the store is what is checked
		lot = receive(code, 1, store(stores.QUARANTINE))
		with self.assertRaises(frappe.ValidationError) as refused:
			self.decide(lot, "Release", "accepted")
		self.assertIn("has no approved default store", str(refused.exception))

	def test_a_lot_spread_over_two_companies_is_not_moved(self):
		lot = self.quarantined()
		elsewhere = frappe.db.get_value("Warehouse", {"company": ["!=", company()], "is_group": 0}) or store(
			"Stores"
		)
		holdings = [
			{"batch_no": lot, "warehouse": store(stores.QUARANTINE), "qty": 5},
			{"batch_no": lot, "warehouse": elsewhere, "qty": 5},
		]
		with (
			patch("meddevice_suite.lot_control.lot_status.get_batch_qty", return_value=holdings),
			patch(
				"meddevice_suite.lot_control.lot_status.frappe.get_cached_value",
				side_effect=lambda doctype, name, field: ("A" if name == store(stores.QUARANTINE) else "B")
				if field == "company"
				else frappe.get_cached_value(doctype, name, field),
			),
			self.assertRaises(frappe.ValidationError) as refused,
		):
			lot_status.move_to_role(frappe.get_doc("Batch", lot), None, "accepted")
		self.assertIn("stock in more than one company", str(refused.exception))


@verifies("URS-STK-006")
class TestStockByLotStatus(LotTestCase):
	def test_the_report_shows_each_lot_with_its_status_store_and_reason(self):
		held = self.quarantined(4)
		self.decide(held, "Hold", "label unreadable")
		approved = self.approved(3)

		_columns, rows = report({"lot_status": HOLD})
		row = next(r for r in rows if r["batch_no"] == held)
		self.assertEqual(
			(row["lot_status"], row["warehouse"], row["qty"], row["set_by"], row["reason"]),
			(HOLD, store(stores.HOLD), 4, self.qa, "label unreadable"),
		)
		self.assertFalse([r for r in rows if r["batch_no"] == approved])
		_columns, rows = report({"item_code": RAW, "warehouse": store("Stores")})
		self.assertEqual(
			[(r["batch_no"], r["lot_status"]) for r in rows if r["batch_no"] == approved],
			[(approved, APPROVED)],
		)

	def test_stock_by_lot_counts_old_style_and_bundled_lots_alike(self):
		lot = self.approved(5)
		move(RAW, lot, 2, store("Stores"), store("Work In Progress"))

		self.assertEqual(qty_in(lot, store("Stores")), 3)
		self.assertEqual(qty_in(lot, store("Work In Progress")), 2)


@verifies("URS-STK-007")
class TestInitialStatusAtCutover(LotTestCase):
	def test_lots_take_their_status_from_the_store_holding_them(self):
		in_stores = receive(RAW, 1, store("Stores"))
		in_quarantine = receive(RAW, 1, store(stores.QUARANTINE))
		in_hold = receive(RAW, 1, store(stores.HOLD))
		in_rejected = receive(RAW, 1, store(stores.REJECTED))
		empty = (
			frappe.get_doc({"doctype": "Batch", "item": RAW, "expiry_date": add_days(today(), 30)})
			.insert()
			.name
		)
		lots = (in_stores, in_quarantine, in_hold, in_rejected, empty)
		self.assertTrue(all(frappe.db.get_value("Batch", lot, "mds_status_set_at") is None for lot in lots))

		set_initial_lot_status.execute()

		self.assertEqual(
			[status_of([lot])[lot] for lot in lots], [APPROVED, QUARANTINE, HOLD, REJECTED, QUARANTINE]
		)
		for lot in lots:
			self.assertEqual(
				frappe.db.get_value("Batch", lot, "mds_status_reason"), set_initial_lot_status.REASON
			)
			self.assertIn("Updated", audit_actions("Batch", lot))

	def test_running_the_patch_again_changes_nothing(self):
		lot = receive(RAW, 1, store("Stores"))
		set_initial_lot_status.execute()
		set_at = frappe.db.get_value("Batch", lot, "mds_status_set_at")

		set_initial_lot_status.execute()

		self.assertEqual(frappe.db.get_value("Batch", lot, "mds_status_set_at"), set_at)

	def test_the_most_cautious_store_decides(self):
		rows = [frappe._dict(warehouse=store("Stores")), frappe._dict(warehouse=store(stores.HOLD))]
		self.assertEqual(initial_status(rows), HOLD)
		self.assertEqual(initial_status([*rows, frappe._dict(warehouse=store(stores.REJECTED))]), REJECTED)
		self.assertEqual(initial_status([frappe._dict(warehouse=store("Stores"))]), APPROVED)
		self.assertEqual(initial_status([]), QUARANTINE)
