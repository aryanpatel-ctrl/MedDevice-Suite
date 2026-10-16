from unittest.mock import patch

import frappe
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return
from frappe.model.document import Document
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, today

from meddevice_suite.compliance.audit_reason import stage_reason
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
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.fixtures.records import lot_of
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import (
	approve_equipment_for_test,
	approve_for_test,
	approve_specification_for_test,
	licensed_for_test,
	stock_item,
	submit_bom_for_test,
)

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
	"""A new lot of `item` into `warehouse` by a material receipt; returns the lot. A new lot is
	Quarantine and comes only into a store of a role (URS-STK-010): asked for in an approved store,
	the lot is made and approved first, as QA's release leaves a lot (module 16), so a test that needs
	approved stock there need not inspect and release it; the release is tested in test_release."""
	row = {"item_code": item, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10}
	if stores.role_of(warehouse) or not frappe.get_cached_value("Item", item, "has_batch_no"):
		return lot_of(stock_entry("Material Receipt", [row]).items[0])
	approve_for_test(item)  # module 1: a lot is made only of an approved item
	lot = frappe.get_doc({"doctype": "Batch", "item": item}).insert().name
	set_status(lot, APPROVED, "Approved for the test, as QA's release leaves it")
	stock_entry("Material Receipt", [{**row, "batch_no": lot, "use_serial_batch_fields": 1}])
	return lot


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


def left_in_quarantine_by_an_earlier_version(lot: str) -> None:
	"""A lot in an approved store that is still Quarantine, as a version before URS-STK-010 let new
	stock in there; such stock is on sites today."""
	frappe.db.set_value("Batch", lot, STATUS_FIELD, QUARANTINE)


def before_lot_control(*lots: str) -> None:
	"""The lots as a site holds them before lot status is installed: no status set by anyone."""
	for lot in lots:
		frappe.db.set_value("Batch", lot, dict.fromkeys(lot_status.STATUS_FIELDS))


def deliver(lot: str, qty: float, warehouse: str, submit: bool = True) -> Document:
	"""A delivery note of the lot to the test customer, from `warehouse`."""
	note = frappe.get_doc(
		{
			"doctype": "Delivery Note",
			"customer": CUSTOMER,
			"company": company(),
			"items": [
				{
					"item_code": RAW,
					"qty": qty,
					"rate": 10,
					"warehouse": warehouse,
					"batch_no": lot,
					"use_serial_batch_fields": 1,
				}
			],
		}
	).insert()
	if submit:
		note.submit()
	return note


def customer_return(note: Document, qty: float, warehouse: str | None = None) -> Document:
	"""The customer's return of `qty` of the note's lot, as ERPNext makes it from the note, into
	`warehouse` (by default the store ERPNext takes: the company's store for sales returns)."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

	back = make_sales_return(note.name)
	if warehouse:
		back.set_warehouse = warehouse
	for row in back.items:
		row.update(
			{
				"qty": -qty,
				"stock_qty": -qty * flt(row.conversion_factor or 1),
				"warehouse": warehouse or row.warehouse,
				"batch_no": note.items[0].batch_no,
				"use_serial_batch_fields": 1,
				"serial_and_batch_bundle": None,
			}
		)
	back.insert()
	back.submit()
	return back


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
		from meddevice_suite.setup.fixtures.inspection import (
			ensure_inspection_masters,
			ensure_inspection_template,
		)

		cls.instrument = ensure_inspection_masters(company(), approve_for_test, approve_equipment_for_test)
		# Module 6 (plan D-33): inspected only against a specification QA has approved.
		cls.template = ensure_inspection_template(RAW, "Raw Material", approve_specification_for_test)

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
		from meddevice_suite.setup.fixtures.inspection import readings_for

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
		with self.assertRaises(LotNotApproved):
			lot_status.guard_stock_ledger_entry(entry)  # a count does not change unreleased stock (D-24)
		set_status(lot, APPROVED, "test: released without the record")
		entry.warehouse = store("Stores")
		lot_status.guard_stock_ledger_entry(entry)  # a counted loss of approved stock

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

		for kind in ("Stores", stores.RETURNS):
			with self.subTest(store=kind), self.assertRaises(LotNotApproved):
				move(RAW, lot, 1, store(stores.REJECTED), store(kind))
		issue(RAW, lot, 1, store(stores.REJECTED))
		self.assertEqual(qty_in(lot, store(stores.REJECTED)), 5)

		# A supplier's lot, once Rejected, goes back on a purchase return from the rejected store. QA's
		# rejection moves it there; Stores cannot move stock out of quarantine (URS-STK-010).
		receipt = purchase_receive(RAW, 6)
		supplier_lot = lot_of(receipt.items[0])
		with self.assertRaises(LotNotApproved):
			move(RAW, supplier_lot, 6, store(stores.QUARANTINE), store(stores.REJECTED))
		self.decide(supplier_lot, "Reject", "certificate does not match")
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
			).insert()
		)
		bom = bom if isinstance(bom, str) else submit_bom_for_test(bom).name

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
		# Opening stock an earlier version let into the approved store is still Quarantine until QA
		# says so; it is released where it is. (New stock no longer comes in there: URS-STK-010.)
		lot = receive(RAW, 2, store("Stores"))
		left_in_quarantine_by_an_earlier_version(lot)

		decision = self.decide(lot, "Release", "opening stock, certificates checked")

		self.assertIsNone(decision.stock_entry)
		self.assertEqual(decision.quantity, 2)
		self.assertEqual(status_of([lot])[lot], APPROVED)

	def test_a_hold_or_rejection_collects_unreleased_stock_from_an_approved_store(self):
		lot = receive(RAW, 3, store("Stores"))
		left_in_quarantine_by_an_earlier_version(lot)

		held = self.decide(lot, "Hold", "found in the approved store unreleased")

		self.assertEqual((held.quantity, qty_in(lot, store(stores.HOLD))), (3, 3))
		self.assertEqual(qty_in(lot, store("Stores")), 0)

	def test_release_needs_an_approved_default_store_on_the_item(self):
		code = stock_item(
			"MDS-LOT-NO-STORE",
			has_batch_no=1,
			create_new_batch=1,
			has_expiry_date=1,
			shelf_life_in_days=30,
			item_defaults=[{"company": company(), "default_warehouse": store(stores.QUARANTINE)}],
		)
		from meddevice_suite.setup.fixtures.inspection import ensure_inspection_template

		# Inspected first, against its approved specification, so the store is what is checked.
		ensure_inspection_template(code, "Raw Material", approve_specification_for_test)
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
			frappe._dict(batch_no=lot, warehouse=store(stores.QUARANTINE), qty=5),
			frappe._dict(batch_no=lot, warehouse=elsewhere, qty=5),
		]
		with (
			patch(
				"meddevice_suite.lot_control.lot_status.frappe.get_cached_value",
				side_effect=lambda doctype, name, field: ("A" if name == store(stores.QUARANTINE) else "B")
				if field == "company"
				else frappe.get_cached_value(doctype, name, field),
			),
			self.assertRaises(frappe.ValidationError) as refused,
		):
			lot_status.move_to_role(frappe.get_doc("Batch", lot), None, "accepted", holdings)
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
		before_lot_control(*lots)
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
		before_lot_control(lot)
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


# --- The stock path matrix (URS-STK-010) -----------------------------------------------------------
#
# Written out here from the matrix of docs/features/lot-status-control.md section 2.3, independently
# of the code: what may take stock out of each kind of store, by lot status, and where new stock and
# returns may come in. A path is (voucher type, stock entry purpose, a return, the kind of store the
# stock goes to: None is out of the stores).

TRANSFERS = {
	f"transfer to {kind}": ("Stock Entry", "Material Transfer", False, kind) for kind in stores.KINDS
}
USES = {
	"material issue": ("Stock Entry", "Material Issue", False, None),
	"transfer for manufacture": ("Stock Entry", "Material Transfer for Manufacture", False, stores.APPROVED),
	"consumption for manufacture": ("Stock Entry", "Material Consumption for Manufacture", False, None),
	"consumed by manufacture": ("Stock Entry", "Manufacture", False, None),
	"consumed by repack": ("Stock Entry", "Repack", False, None),
	"sent to subcontractor": ("Stock Entry", "Send to Subcontractor", False, stores.APPROVED),
	"disassembled": ("Stock Entry", "Disassemble", False, None),
	"raw material back to customer": ("Stock Entry", "Return Raw Material to Customer", False, None),
	"subcontracting delivery": ("Stock Entry", "Subcontracting Delivery", False, None),
	"delivery": ("Delivery Note", None, False, None),
	"invoice delivery": ("Sales Invoice", None, False, None),
	"pick": ("Pick List", None, False, None),
	"pack": ("Packing Slip", None, False, None),
}
OUT_OF_A_STORE = {
	**TRANSFERS,
	**USES,
	"supplier return": ("Purchase Receipt", None, True, None),
	"count": ("Stock Reconciliation", None, False, None),
}
# Stock entry purposes that only bring stock in; every other purpose takes stock out of a store and
# must be among the paths above, so a purpose ERPNext adds is classified before it is trusted.
INWARD_PURPOSES = frozenset({"Material Receipt", "Receive from Customer", "Subcontracting Return"})

NOTHING: frozenset[str] = frozenset()
# Rejected stock goes back to its supplier or is scrapped; never into the returns store, which receives
# only a customer's goods coming back (independent review 2026-10-15).
FROM_REJECTED = frozenset({"supplier return", "material issue"})
# kind of store -> lot status -> the paths stock may leave it by, without QA's signature
MATRIX = {
	stores.APPROVED: {
		APPROVED: frozenset(
			{
				*USES,
				"supplier return",
				"count",
				f"transfer to {stores.APPROVED}",
				f"transfer to {stores.HOLD}",
				f"transfer to {stores.REJECTED}",
			}
		),
		QUARANTINE: NOTHING,
		HOLD: NOTHING,
		REJECTED: NOTHING,
	},
	stores.QUARANTINE: dict.fromkeys(lot_status.STATUSES, NOTHING),
	stores.HOLD: dict.fromkeys(lot_status.STATUSES, NOTHING),
	stores.RETURNS: dict.fromkeys(lot_status.STATUSES, NOTHING),
	stores.REJECTED: dict.fromkeys(lot_status.STATUSES, FROM_REJECTED),
}
# QA's signed transfer moves stock out of any store but the rejected one, and into any store but the
# returns store, which receives only a customer's goods coming back.
SIGNED = {kind: frozenset(TRANSFERS) - {f"transfer to {stores.RETURNS}"} for kind in stores.KINDS} | {
	stores.REJECTED: NOTHING
}
INTO_A_STORE = {
	"material receipt": ("Stock Entry", "Material Receipt", False),
	"made by manufacture": ("Stock Entry", "Manufacture", False),
	"made by repack": ("Stock Entry", "Repack", False),
	"goods receipt": ("Purchase Receipt", None, False),
	"count": ("Stock Reconciliation", None, False),
	"customer return on a delivery note": ("Delivery Note", None, True),
	"customer return on an invoice": ("Sales Invoice", None, True),
	"customer return at the point of sale": ("POS Invoice", None, True),
}
CUSTOMER_RETURNS = frozenset(name for name in INTO_A_STORE if name.startswith("customer return"))


def comes_in(path: str, status: str, kind: str) -> bool:
	"""Where new stock and returns may come in: a customer's goods into the returns store only, whatever
	the lot's status (plan D-25), and nothing else into it; a lot not Approved into any store but an
	approved one."""
	if path in CUSTOMER_RETURNS or kind == stores.RETURNS:
		return path in CUSTOMER_RETURNS and kind == stores.RETURNS
	return status == APPROVED or kind != stores.APPROVED


@verifies("URS-STK-005", "URS-STK-010")
class TestTheStockPathMatrix(IntegrationTestCase):
	"""The guardrail: every path out of every kind of store, for every lot status, is allowed or
	refused as the matrix says; a path that breaks it fails here (found 2026-10-12: approved stock
	could be moved into the rejected store and used from there, and a release took stock rejected at
	the door with it)."""

	def test_every_stock_entry_purpose_is_classified(self):
		purposes = set(frappe.get_meta("Stock Entry").get_field("purpose").options.split("\n")) - {""}
		classified = {purpose for _type, purpose, _return, _target in OUT_OF_A_STORE.values() if purpose}
		self.assertEqual(purposes - INWARD_PURPOSES, classified)

	def test_what_leaves_each_kind_of_store(self):
		for kind, by_status in MATRIX.items():
			for status, allowed in by_status.items():
				for path, (voucher_type, purpose, is_return, target) in OUT_OF_A_STORE.items():
					movement = lot_status.Movement(voucher_type, kind, target, purpose, is_return)
					with self.subTest(store=kind, status=status, path=path):
						self.assertEqual(lot_status.movement_allowed(status, movement), path in allowed)

	def test_what_qas_signed_transfer_moves(self):
		for kind, allowed in SIGNED.items():
			for status in lot_status.STATUSES:
				for path, (voucher_type, purpose, is_return, target) in TRANSFERS.items():
					movement = lot_status.Movement(
						voucher_type, kind, target, purpose, is_return, signed=True
					)
					with self.subTest(store=kind, status=status, path=path):
						self.assertEqual(lot_status.movement_allowed(status, movement), path in allowed)

	def test_where_new_stock_and_returns_come_in(self):
		for path, (voucher_type, purpose, is_return) in INTO_A_STORE.items():
			for status in lot_status.STATUSES:
				for kind in stores.KINDS:
					movement = lot_status.Movement(voucher_type, None, kind, purpose, is_return)
					with self.subTest(path=path, status=status, store=kind):
						self.assertEqual(
							lot_status.movement_allowed(status, movement), comes_in(path, status, kind)
						)


@verifies("URS-STK-005", "URS-STK-010")
class TestEveryVoucherTypeAsksTheOneRule(LotTestCase):
	"""Each form check and the stock ledger decide by the matrix's one function, so no voucher type
	keeps a rule of its own: with the rule refusing everything, each refuses."""

	def test_each_guard_refuses_what_the_rule_refuses(self):
		lot = self.approved()
		named = {"item_code": RAW, "qty": 1, "batch_no": lot, "use_serial_batch_fields": 1}
		documents = {
			lot_status.guard_stock_entry: frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"purpose": "Material Transfer",
					"items": [
						{**named, "s_warehouse": store("Stores"), "t_warehouse": store("Work In Progress")}
					],
				}
			),
			lot_status.guard_sales_document: frappe.get_doc(
				{"doctype": "Delivery Note", "items": [{**named, "warehouse": store("Stores")}]}
			),
			lot_status.guard_pick_list: frappe.get_doc(
				{"doctype": "Pick List", "locations": [{**named, "warehouse": store("Stores")}]}
			),
			lot_status.guard_packing_slip: frappe.get_doc({"doctype": "Packing Slip", "items": [named]}),
			lot_status.guard_stock_ledger_entry: frappe.get_doc(
				{
					"doctype": "Stock Ledger Entry",
					"item_code": RAW,
					"warehouse": store("Stores"),
					"batch_no": lot,
					"actual_qty": -1,
					"voucher_type": "Delivery Note",
					"voucher_no": "DN-TEST",
					"is_cancelled": 0,
				}
			),
		}
		for guard, doc in documents.items():
			guard(doc)  # approved stock in an approved store: allowed
			with (
				self.subTest(guard=guard.__name__),
				patch.object(lot_status, "movement_allowed", return_value=False),
				self.assertRaises(LotNotApproved),
			):
				guard(doc)


@verifies("URS-STK-005", "URS-STK-010")
class TestStockKeptApart(LotTestCase):
	"""Approved stock set apart in the hold, rejected or returns store is not used from there; new
	stock does not come into an approved store unreleased; a count changes approved stock only."""

	def test_approved_stock_set_apart_in_the_hold_store_is_not_used_from_there(self):
		lot = self.approved()
		move(RAW, lot, 2, store("Stores"), store(stores.HOLD))  # Stores sets suspect stock apart

		for refused in (
			lambda: issue(RAW, lot, 1, store(stores.HOLD)),
			lambda: deliver(lot, 1, store(stores.HOLD)),
			lambda: move(RAW, lot, 1, store(stores.HOLD), store("Stores")),
		):
			with self.assertRaises(LotNotApproved) as refusal:
				refused()
			self.assertIn("kept apart", str(refusal.exception))
		self.assertEqual(qty_in(lot, store(stores.HOLD)), 2)

	def test_approved_stock_in_the_rejected_store_is_only_scrapped_returned_or_kept_apart(self):
		lot = self.approved()
		move(RAW, lot, 3, store("Stores"), store(stores.REJECTED))

		with self.assertRaises(LotNotApproved):
			deliver(lot, 1, store(stores.REJECTED))
		with self.assertRaises(LotNotApproved):
			move(RAW, lot, 1, store(stores.REJECTED), store("Stores"))
		issue(RAW, lot, 1, store(stores.REJECTED))  # scrapped
		self.assertEqual((qty_in(lot, store(stores.REJECTED)), qty_in(lot, store(stores.RETURNS))), (2, 0))

	def test_approved_stock_does_not_go_into_the_quarantine_or_returns_store(self):
		lot = self.approved()
		for kind in (stores.QUARANTINE, stores.RETURNS):
			with self.subTest(store=kind), self.assertRaises(LotNotApproved):
				move(RAW, lot, 1, store("Stores"), store(kind))

	def test_a_new_lot_does_not_come_into_an_approved_store(self):
		with self.assertRaises(LotNotApproved) as refusal:
			stock_entry(
				"Material Receipt",
				[{"item_code": RAW, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 10}],
			)
		self.assertIn("cannot come into", str(refusal.exception))
		named = frappe.get_doc({"doctype": "Batch", "item": RAW}).insert().name  # Quarantine, no stock
		with self.assertRaises(LotNotApproved):
			stock_entry(
				"Material Receipt",
				[
					{
						"item_code": RAW,
						"qty": 1,
						"t_warehouse": store("Stores"),
						"basic_rate": 10,
						"batch_no": named,
						"use_serial_batch_fields": 1,
					}
				],
			)

	def test_a_count_does_not_change_unreleased_stock(self):
		"""Plan D-24: a stock count corrects approved stock in an approved store only."""
		lot = self.quarantined(5)
		count = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company(),
				"purpose": "Stock Reconciliation",
				"items": [
					{
						"item_code": RAW,
						"warehouse": store(stores.QUARANTINE),
						"qty": 4,
						"valuation_rate": 10,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		).insert()
		with self.assertRaises(LotNotApproved):
			count.submit()
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 5)


@verifies("URS-STK-010")
class TestCustomerReturns(LotTestCase):
	"""Goods a customer sends back come into the Returns store, whatever their lot's status, and
	leave it only by QA's signed disposition (plan D-25, decided 2026-10-12)."""

	def test_a_return_comes_only_into_the_returns_store_and_is_not_used_from_there(self):
		lot = self.approved()
		note = deliver(lot, 3, store("Stores"))

		with self.assertRaises(LotNotApproved) as refusal:
			customer_return(note, 1, store("Stores"))
		self.assertIn("Returns store", str(refusal.exception))
		back = customer_return(note, 2)  # ERPNext takes the company's store for sales returns

		self.assertEqual(back.items[0].warehouse, store(stores.RETURNS))
		self.assertEqual(qty_in(lot, store(stores.RETURNS)), 2)
		self.assertEqual(status_of([lot])[lot], APPROVED)
		with self.assertRaises(LotNotApproved):
			deliver(lot, 1, store(stores.RETURNS))
		with self.assertRaises(LotNotApproved):
			issue(RAW, lot, 1, store(stores.RETURNS))

	def test_a_recalled_lot_on_hold_comes_back_into_the_returns_store(self):
		lot = self.approved()
		note = deliver(lot, 2, store("Stores"))
		self.decide(lot, "Hold", "recall: field complaint")

		customer_return(note, 2)

		self.assertEqual(qty_in(lot, store(stores.RETURNS)), 2)
		self.assertEqual(status_of([lot])[lot], HOLD)

	def test_qa_returns_the_goods_to_stock_only_after_an_inspection_made_since(self):
		lot = self.approved()
		note = deliver(lot, 2, store("Stores"))
		customer_return(note, 2)

		self.as_qa()
		record = frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
		record.run_method("onload")
		self.assertEqual(record.get_onload("lot")["returned"], 2)
		with self.assertRaisesRegex(
			frappe.ValidationError, "no accepted inspection made after its goods came back"
		):
			release.return_to_stock(record, password=PASSWORD, reason="returned unopened")
		frappe.set_user("Administrator")
		self.inspected_on_its_entry(lot)
		self.as_qa()
		release.return_to_stock(record, password=PASSWORD, reason="returned unopened, inspected")

		decided = frappe.get_doc("Lot Release", record.name)
		self.assertEqual((decided.decision, decided.quantity), ("Return to Stock", 2))
		self.assertTrue(decided.quality_inspection)
		self.assertEqual((qty_in(lot, store(stores.RETURNS)), qty_in(lot, store("Stores"))), (0, 10))
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(
			frappe.get_all(
				"Electronic Signature",
				filters={"reference_doctype": "Lot Release", "reference_name": record.name},
				pluck="meaning",
			),
			["Released"],
		)

	def test_qa_rejects_returned_goods_into_the_rejected_store(self):
		lot = self.approved()
		note = deliver(lot, 2, store("Stores"))
		customer_return(note, 2)

		self.decide_returned(lot, "Reject Return", "packaging damaged in transit")

		self.assertEqual((qty_in(lot, store(stores.RETURNS)), qty_in(lot, store(stores.REJECTED))), (0, 2))
		self.assertEqual(status_of([lot])[lot], APPROVED)  # the lot itself is not decided

	def test_returned_goods_of_a_held_lot_do_not_go_back_to_stock(self):
		lot = self.approved()
		note = deliver(lot, 1, store("Stores"))
		self.decide(lot, "Hold", "recall")
		customer_return(note, 1)
		self.inspected_on_its_entry(lot)
		with self.assertRaisesRegex(frappe.ValidationError, "only once the lot is Approved"):
			self.decide_returned(lot, "Return to Stock", "looks fine")
		self.assertEqual(qty_in(lot, store(stores.RETURNS)), 1)

	def decide_returned(self, lot: str, act: str, reason: str) -> Document:
		self.as_qa()
		try:
			record = frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
			{"Return to Stock": release.return_to_stock, "Reject Return": release.reject_return}[act](
				record, password=PASSWORD, reason=reason
			)
			return frappe.get_doc("Lot Release", record.name)
		finally:
			frappe.set_user("Administrator")


@verifies("URS-STK-004", "URS-STK-010")
class TestAnExpiredLotIsStillDecided(LotTestCase):
	def test_holding_an_expired_lot_moves_its_stock(self):
		"""ERPNext's batch quantity leaves an expired lot out; the hold must still move it (H3)."""
		lot = self.approved(4)
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), -1))

		held = self.decide(lot, "Hold", "expired in store")

		self.assertEqual((held.quantity, qty_in(lot, store(stores.HOLD))), (4, 4))
		self.assertEqual(qty_in(lot, store("Stores")), 0)


@verifies("URS-STK-010")
class TestTheReturnsStoreReceivesOnlyCustomerReturns(LotTestCase):
	"""Goods rejected at the door, moved by hand into the returns store, reached usable stock by QA's
	Return to Stock (independent review 2026-10-15): the returns store receives a customer's goods
	coming back, nothing else."""

	def test_rejected_stock_is_not_moved_into_the_returns_store(self):
		lot = self.approved()
		move(RAW, lot, 2, store("Stores"), store(stores.REJECTED))

		with self.assertRaises(LotNotApproved) as refusal:
			move(RAW, lot, 1, store(stores.REJECTED), store(stores.RETURNS))

		self.assertIn("receives only goods a customer sends back", str(refusal.exception))
		self.assertEqual((qty_in(lot, store(stores.REJECTED)), qty_in(lot, store(stores.RETURNS))), (2, 0))

	def test_new_stock_does_not_come_into_the_returns_store(self):
		with self.assertRaises(LotNotApproved) as refusal:
			stock_entry(
				"Material Receipt",
				[{"item_code": RAW, "qty": 1, "t_warehouse": store(stores.RETURNS), "basic_rate": 10}],
			)
		self.assertIn("receives only goods a customer sends back", str(refusal.exception))


@verifies("URS-STK-014")
class TestCancellingFollowsTheStockPath(LotTestCase):
	"""Cancelling a document posts the reverse of its movement; stock kept apart for QA does not leave
	that way (independent review 2026-10-15: a transfer of damaged stock into the rejected store,
	cancelled, put it back in approved stock; a customer's return, cancelled, left QA's queue)."""

	def cancel(self, doctype: str, name: str) -> None:
		# A request starts with no action in progress; in the test process the cause of a cancellation
		# refused earlier would stay in the flags and the bundles this one cancels would not find its
		# reason (as the audit tests reset it, compliance/test_audit.py).
		frappe.flags.audit_cause = None
		self.addCleanup(frappe.flags.pop, "audit_cause", None)
		stage_reason(doctype, name, "cancel", "Entered by mistake (test)")
		frappe.get_doc(doctype, name).cancel()

	def assert_refused(self, doctype: str, name: str) -> None:
		with self.assertRaises(LotNotApproved) as refusal:
			self.cancel(doctype, name)
		self.assertIn("QA's signed disposition", str(refusal.exception))
		self.assertEqual(frappe.db.get_value(doctype, name, "docstatus"), 1)

	def test_cancelling_a_move_into_the_rejected_or_hold_store_is_refused(self):
		lot = self.approved()
		for kind in (stores.REJECTED, stores.HOLD):
			with self.subTest(store=kind):
				moved = move(RAW, lot, 2, store("Stores"), store(kind))
				self.assert_refused("Stock Entry", moved.name)
				self.assertEqual(qty_in(lot, store(kind)), 2)

	def test_cancelling_a_customer_return_is_refused(self):
		lot = self.approved()
		note = deliver(lot, 3, store("Stores"))
		back = customer_return(note, 2)

		self.assert_refused("Delivery Note", back.name)

		self.assertEqual(qty_in(lot, store(stores.RETURNS)), 2)

	def test_cancelling_what_brought_in_stock_of_a_held_lot_is_refused(self):
		lot = self.approved()
		moved = move(RAW, lot, 2, store("Stores"), store("Finished Goods"))
		self.decide(lot, "Hold", "customer complaint under investigation")

		self.assert_refused("Stock Entry", moved.name)

	def test_other_cancellations_stay_allowed(self):
		lot = self.approved()
		moved = move(RAW, lot, 2, store("Stores"), store("Finished Goods"))
		issued = issue(RAW, lot, 1, store("Stores"))
		move(RAW, lot, 2, store("Stores"), store(stores.REJECTED))
		scrapped = issue(RAW, lot, 1, store(stores.REJECTED))  # stock comes back into the rejected store
		quarantined = self.quarantined(3)
		received = frappe.db.get_value("Batch", quarantined, "reference_name")  # a receipt into quarantine

		for doctype, name in (
			("Stock Entry", moved.name),
			("Stock Entry", issued.name),
			("Stock Entry", scrapped.name),
			("Stock Entry", received),
		):
			with self.subTest(document=name):
				self.cancel(doctype, name)
				self.assertEqual(frappe.db.get_value(doctype, name, "docstatus"), 2)
		self.assertEqual(qty_in(lot, store(stores.REJECTED)), 2)

	def test_every_cancellation_is_checked(self):
		self.assertIn(
			"meddevice_suite.lot_control.lot_status.guard_cancellation",
			frappe.get_hooks("doc_events")["*"]["before_cancel"],
		)
		lot_status.guard_cancellation(frappe.get_doc({"doctype": "ToDo", "description": "not stock"}))
