import frappe
from erpnext.stock.stock_ledger import NegativeStockError
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from meddevice_suite.master_data import item_master
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.stock import STOCK_SETTINGS


def stock_item(code: str, **values) -> str:
	"""An item for another module's tests, in draft like any new item (module 1); a record that
	uses it approves it first (approve_for_test)."""
	if not frappe.db.exists("Item", code):
		group = values.get("item_group", "Raw Material")
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "Raw Material",
				"stock_uom": "Nos",
				"gst_hsn_code": "39269099",
				item_master.ITEM_TYPE: item_master.FINISHED_GOOD
				if group == "Products"
				else item_master.RAW_MATERIAL,
				item_master.STORAGE_CONDITION: "Below 30 °C (test)",
				**values,
			}
		).insert()
	return code


def approve_for_test(*codes: str) -> None:
	"""QA's approval of items that other modules' tests use (module 1), recorded as the signed act
	records it, so that those tests need not set up everything an approval checks. The signed act
	itself, with its checks and its signature, is tested in master_data/test_item_master.py. The
	items it marks need not hold everything an approval checks, so the rule that an approved item
	keeps what its approval needed is not exercised through them; it is in test_item_master.py."""
	for code in codes:
		if code and not item_master.approved(code):
			frappe.db.set_value(  # test fixture only
				"Item",
				code,
				{
					item_master.APPROVAL_STATUS: item_master.APPROVED,
					item_master.APPROVED_BY: "Administrator",
					item_master.APPROVED_AT: now_datetime(),
				},
			)


class StockSettingsTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()


@verifies("URS-STK-001", "URS-STK-002", "URS-STK-003")
class TestStockSettings(StockSettingsTestCase):
	def test_the_settings_are_applied(self):
		for field, value in STOCK_SETTINGS.items():
			with self.subTest(field=field):
				self.assertEqual(frappe.db.get_single_value("Stock Settings", field), value)

	def test_a_manual_change_is_undone_on_the_next_migrate(self):
		frappe.db.set_single_value("Stock Settings", "pick_serial_and_batch_based_on", "FIFO")
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

		apply_configuration()

		self.assertEqual(
			frappe.db.get_single_value("Stock Settings", "pick_serial_and_batch_based_on"), "Expiry"
		)
		self.assertEqual(frappe.db.get_single_value("Stock Settings", "allow_negative_stock"), 0)


@verifies("URS-STK-001")
class TestLotsAreBatches(StockSettingsTestCase):
	def test_a_batch_tracked_item_can_be_created(self):
		code = stock_item("MDS-STK-LOT-ITEM", has_batch_no=1, has_expiry_date=1, shelf_life_in_days=1825)

		self.assertEqual(frappe.db.get_value("Item", code, "has_batch_no"), 1)


@verifies("URS-STK-003")
class TestStockNeverGoesNegative(StockSettingsTestCase):
	def test_issuing_more_than_is_in_stock_is_refused(self):
		company = frappe.defaults.get_global_default("company")
		warehouse = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": "Stores"})
		code = stock_item("MDS-STK-NEGATIVE-ITEM", valuation_rate=10)
		approve_for_test(code)  # module 1: only approved items move

		issue = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Issue",
				"company": company,
				"items": [{"item_code": code, "qty": 5, "s_warehouse": warehouse, "basic_rate": 10}],
			}
		).insert()

		with self.assertRaises(NegativeStockError):
			issue.submit()


@verifies("URS-STK-003")
class TestNoItemOrLotMayGoNegative(StockSettingsTestCase):
	def test_an_item_cannot_be_allowed_negative_stock(self):
		item = frappe.get_doc("Item", stock_item("MDS-STK-FLAG-ITEM"))
		item.allow_negative_stock = 1

		with self.assertRaises(frappe.ValidationError):
			item.save()

	def test_a_lot_cannot_be_allowed_negative_stock(self):
		code = stock_item("MDS-STK-FLAG-LOT", has_batch_no=1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": "MDS-NEG-LOT",
					"item": code,
					"allow_negative_stock_for_batch": 1,
				}
			).insert()


@verifies("URS-STK-002", "URS-STK-003")
class TestStockSettingsAreLocked(StockSettingsTestCase):
	def test_the_declared_settings_cannot_be_changed_on_a_site(self):
		for field, value in (
			("allow_negative_stock", 1),
			("pick_serial_and_batch_based_on", "FIFO"),
			("auto_create_serial_and_batch_bundle_for_outward", 0),
		):
			settings = frappe.get_single("Stock Settings")
			settings.set(field, value)
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				settings.save()

	def test_other_stock_settings_stay_editable(self):
		settings = frappe.get_single("Stock Settings")
		settings.item_naming_by = "Item Code"
		settings.save()
