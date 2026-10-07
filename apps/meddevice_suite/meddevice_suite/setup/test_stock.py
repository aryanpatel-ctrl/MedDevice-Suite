import frappe
from erpnext.stock.stock_ledger import NegativeStockError
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.stock import STOCK_SETTINGS


def stock_item(code: str, **values) -> str:
	if not frappe.db.exists("Item", code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "Raw Material",
				"stock_uom": "Nos",
				"gst_hsn_code": "39269099",
				**values,
			}
		).insert()
	return code


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
