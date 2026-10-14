from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from meddevice_suite.master_data.test_supplier_approval import approved_for
from meddevice_suite.qa import verifies
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	CUSTOMER,
	LATER_EXPIRY_DAYS,
	LOTS_IN_STORE,
	OPERATIONS,
	PRODUCT,
	PRODUCT_DETAILS,
	QUANTITY,
	RAW_MATERIALS,
	ROUTING,
	SUPPLIER,
	journey_items,
	lots_of,
	post_stock_entry,
	prepare_effective_document,
	prepare_journey,
	store,
)
from meddevice_suite.qa.ui_users import prepare_job_users
from meddevice_suite.setup.roles import apply_roles


def stock(item: str, warehouse: str) -> float:
	return frappe.db.get_value("Bin", {"item_code": item, "warehouse": warehouse}, "actual_qty") or 0


def latest_stock_entry() -> str:
	return frappe.get_all(
		"Stock Entry", filters={"docstatus": 1}, order_by="creation desc", limit=1, pluck="name"
	)[0]


@verifies("URS-PLT-007")
class TestBrowserJourney(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def setUp(self):
		self.enterContext(patch.dict(frappe.local.conf, {"allow_tests": 1}))
		self.password = prepare_job_users()["password"]  # the run's, as the browser is given it

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			with self.assertRaises(frappe.PermissionError):
				prepare_journey(self.password)
			with self.assertRaises(frappe.PermissionError):
				lots_of("Stock Entry", "anything")

	def test_only_the_administrator_prepares_the_journey(self):
		frappe.set_user(prepare_job_users()["users"]["Stores"])

		with self.assertRaises(frappe.PermissionError):
			prepare_journey(self.password)

	def test_an_effective_document_is_issued_for_the_browser_to_name(self):
		"""Plan D-34: the browser names a test method on a specification and a specification on an item."""
		name = prepare_effective_document(self.password, "Specification")
		self.assertEqual(
			frappe.db.get_value("Controlled Document", name, ["document_type", "status"]),
			("Specification", "Effective"),
		)
		self.assertEqual(prepare_effective_document(self.password, "Specification"), name)  # made once
		frappe.set_user(prepare_job_users()["users"]["Stores"])
		with self.assertRaises(frappe.PermissionError):
			prepare_effective_document(self.password, "SOP")

	def test_preparing_keeps_the_callers_session(self):
		"""The items are approved as the run's QA user inside the browser's request; the browser's own
		session (its sid and data) must come out of it untouched, or the browser is signed out."""
		for item in journey_items():  # approved afresh, so the QA user signs in this request
			frappe.db.set_value("Item", item, "mds_approval_status", "Draft")
		# The object Frappe saves for the browser's sid when the request ends is this one
		# (session_obj.data): it must be the same object afterwards, unchanged.
		live = frappe.local.session
		live.sid = "a-browser-session-id"
		live.data = frappe._dict(kept=1)
		prepare_journey(self.password)
		self.assertIs(frappe.local.session, live)
		self.assertEqual(live.user, "Administrator")
		self.assertEqual(live.sid, "a-browser-session-id")
		self.assertEqual(live.data.get("kept"), 1)

	def test_makes_the_masters_the_journey_needs(self):
		journey = prepare_journey(self.password)

		self.assertEqual(
			(journey["company"], journey["customer"], journey["supplier"], journey["product"]),
			(COMPANY, CUSTOMER, SUPPLIER, PRODUCT),
		)
		self.assertTrue(frappe.db.exists("Warehouse", journey["stores"]["quarantine"]))
		self.assertTrue(frappe.db.exists("Customer", CUSTOMER))
		self.assertTrue(frappe.db.exists("Supplier", SUPPLIER))
		for code in journey_items():
			with self.subTest(item=code):
				item = frappe.db.get_value(
					"Item",
					code,
					[
						"has_batch_no",
						"create_new_batch",
						"has_expiry_date",
						"shelf_life_in_days",
						"gst_hsn_code",
					],
					as_dict=True,
				)
				self.assertEqual((item.has_batch_no, item.create_new_batch, item.has_expiry_date), (1, 1, 1))
				self.assertGreater(item.shelf_life_in_days, 0)
				self.assertTrue(item.gst_hsn_code)

		bom = frappe.get_doc("BOM", journey["bom"])
		self.assertEqual((bom.item, bom.docstatus, bom.is_default, bom.routing), (PRODUCT, 1, 1, ROUTING))
		self.assertEqual([row.operation for row in bom.operations], journey["operations"])
		self.assertEqual(journey["operations"], [operation for operation, _ws, _minutes in OPERATIONS])
		self.assertEqual(
			journey["materials"],
			{code: details[5] * QUANTITY / bom.quantity for code, details in RAW_MATERIALS.items()},
		)
		self.assertEqual(journey["product_shelf_life_days"], PRODUCT_DETAILS[3])
		# Module 31: production of the product runs on its Current device master record, which pins
		# the bill the work order uses, drafted by the Production Supervisor and approved by QA.
		record = frappe.get_doc("Device Master Record", journey["device_master_record"])
		self.assertEqual((record.item, record.status, record.docstatus), (PRODUCT, "Current", 1))
		self.assertIn(
			("BOM", journey["bom"]), [(row.reference_doctype, row.reference_name) for row in record.elements]
		)

		# Preparing again makes no second set of masters, nor a second record.
		again = prepare_journey(self.password)
		self.assertEqual(
			(again["bom"], again["device_master_record"]), (journey["bom"], journey["device_master_record"])
		)

	def test_starts_from_the_lots_put_in_store_whatever_an_earlier_run_left(self):
		prepare_journey(self.password)
		material = next(iter(RAW_MATERIALS))
		post_stock_entry(
			"Material Receipt",
			[{"item_code": material, "t_warehouse": store("Quarantine"), "qty": 3, "basic_rate": 10}],
		)

		journey = prepare_journey(self.password)

		self.assertEqual(stock(material, store("Quarantine")), 0)
		for item in journey_items():
			lot = journey["lots_in_store"].get(item)
			with self.subTest(item=item):
				on_hand = frappe.get_all(
					"Bin", filters={"item_code": item, "actual_qty": [">", 0]}, pluck="warehouse"
				)
				if item not in LOTS_IN_STORE:
					self.assertEqual(on_hand, [])
					continue
				store_name, qty, _rate = LOTS_IN_STORE[item]
				self.assertEqual(on_hand, [store(store_name)])
				self.assertEqual(stock(item, store(store_name)), qty)
				expiry = getdate(frappe.db.get_value("Batch", lot, "expiry_date"))
				self.assertEqual(expiry, getdate(add_days(today(), LATER_EXPIRY_DAYS)))
				# later than anything the run receives or makes
				shelf_lives = [details[3] for details in RAW_MATERIALS.values()] + [PRODUCT_DETAILS[3]]
				self.assertGreater(expiry, getdate(add_days(today(), max(shelf_lives))))

	def test_tells_the_lots_a_stock_transaction_moved_with_their_expiry(self):
		journey = prepare_journey(self.password)
		receipt = latest_stock_entry()  # the lots put in store

		lots = lots_of("Stock Entry", receipt)

		self.assertEqual(
			lots,
			[
				{
					"item_code": item,
					"from_store": None,
					"to_store": store(store_name),
					"batch_no": journey["lots_in_store"][item],
					"qty": qty,
					"expiry_date": getdate(add_days(today(), LATER_EXPIRY_DAYS)),
				}
				for item, (store_name, qty, _rate) in LOTS_IN_STORE.items()
			],
		)

	def test_tells_the_lots_of_receipts_and_deliveries_by_their_store(self):
		prepare_journey(self.password)
		material = next(iter(RAW_MATERIALS))
		approved_for(SUPPLIER, [material])  # goods come only from an approved supplier (module 4)
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": COMPANY,
				"supplier": SUPPLIER,
				"items": [
					{"item_code": material, "qty": 2, "rate": 5, "warehouse": store("Quarantine")},
				],
			}
		).insert()
		receipt.submit()

		[lot] = lots_of("Purchase Receipt", receipt.name)

		self.assertEqual((lot["from_store"], lot["to_store"], lot["qty"]), (None, store("Quarantine"), 2))
		self.assertTrue(lot["batch_no"])
		self.assertTrue(lot["expiry_date"])

		delivery = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"company": COMPANY,
				"customer": CUSTOMER,
				"items": [
					{"item_code": PRODUCT, "qty": 1, "rate": 5, "warehouse": store("Finished Goods")},
				],
			}
		).insert()
		delivery.submit()

		self.assertEqual(
			[
				(lot["from_store"], lot["to_store"], lot["qty"])
				for lot in lots_of("Delivery Note", delivery.name)
			],
			[(store("Finished Goods"), None, 1)],
		)

	def test_refuses_other_record_types_and_users_who_may_not_read_the_record(self):
		prepare_journey(self.password)
		receipt = latest_stock_entry()

		with self.assertRaises(frappe.ValidationError):
			lots_of("Sales Order", receipt)

		frappe.set_user(prepare_job_users()["users"]["Operator"])  # no access to stock entries
		with self.assertRaises(frappe.PermissionError):
			lots_of("Stock Entry", receipt)
