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


def licensed_for_test(customer: str) -> str:
	"""A verified licence for a customer other modules' tests sell to (module 5), recorded as QA's
	signed verification records it, so that those tests need not sign one; the signed acts are
	tested in master_data/test_customer_licence.py."""
	from meddevice_suite.master_data import customer_licence

	held = customer_licence.licences_valid_on(customer, "2099-12-31")
	if held:
		return held[0].name
	licence_type = "Licence for tests"
	if not frappe.db.exists(customer_licence.TYPE_DOCTYPE, licence_type):
		frappe.get_doc({"doctype": customer_licence.TYPE_DOCTYPE, "licence_type": licence_type}).insert()
	doc = frappe.get_doc(
		{
			"doctype": customer_licence.DOCTYPE,
			"customer": customer,
			"licence_type": licence_type,
			"licence_number": f"TEST-{customer}",
			"issuing_authority": "Licensing authority for tests",
			"valid_from": "2000-01-01",
			"valid_until": "2099-12-31",
			"licence_copy": "/private/files/licence-for-tests.pdf",
		}
	).insert()
	customer_licence.record(doc, customer_licence.VERIFIED, "Licensed for tests")
	doc.flags.customer_licence = True
	doc.submit()
	return doc.name


def submit_bom_for_test(bom):
	"""Submit a bill of materials as QA's signed approval would (module 2), for other modules'
	tests; the signed act itself is tested in master_data/test_bom.py."""
	bom.flags.bom_approval = True
	bom.submit()
	return bom


def approve_equipment_for_test(asset: str) -> None:
	"""QA's approval of a piece of equipment other modules' tests use (module 8), recorded as the
	signed act records it; the act itself is tested in master_data/test_equipment.py."""
	from meddevice_suite.master_data import equipment

	frappe.db.set_value(
		"Asset",
		asset,
		{
			equipment.APPROVAL_STATUS: equipment.APPROVED,
			equipment.APPROVED_BY: "Administrator",
			equipment.APPROVED_AT: now_datetime(),
			equipment.STATUS: equipment.IN_SERVICE,
		},
	)


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
