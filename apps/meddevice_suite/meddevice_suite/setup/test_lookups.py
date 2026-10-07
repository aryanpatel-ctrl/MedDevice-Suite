import frappe
from frappe.desk.search import search_link
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.lookups import closed_lookups
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile

# Lookups left closed on purpose, reviewed on 2026-10-07: features the client's process
# does not use (coupons, loyalty, shipping rules, delivery trips, sales partners, projects,
# serial numbers, tax withholding, subcontracting) and optional fields ERPNext fills in
# itself (cost centre, price list and tax templates on stock documents). Each is reviewed
# again when the module that would use it is built; a new one fails this test.
REVIEWED_CLOSED: dict[str, set[tuple[str, str]]] = {
	"Maintenance": {
		("Purchase Invoice", "Asset Repair Purchase Invoice.purchase_invoice"),
		("Serial and Batch Bundle", "Asset Repair Consumed Item.serial_and_batch_bundle"),
	},
	"Management": {
		("Delivery Trip", "Delivery Note.delivery_trip"),
		("Loyalty Program", "Customer.loyalty_program"),
	},
	"Operator": {
		("Project", "Job Card.project"),
		("Quality Inspection", "Job Card.quality_inspection"),
		("Quality Inspection Template", "Job Card.quality_inspection_template"),
	},
	"Production Supervisor": {
		("Asset", "Material Request Item.wip_composite_asset"),
		("Customer", "Material Request.customer"),
		("Customer", "Pick List.customer"),
		("Item Tax Template", "Stock Entry Detail.item_tax_template"),
		("Manufacturer", "Material Request Item.manufacturer"),
		("Purchase Order", "Stock Entry.purchase_order"),
		("Sales Taxes and Charges Template", "Stock Entry.taxes_and_charges"),
		("Serial No", "Serial and Batch Entry.serial_no"),
		("Supplier", "Stock Entry.supplier"),
		("Supplier", "Stock Entry.transporter"),
		("Tax Category", "Stock Entry.tax_category"),
		("Terms and Conditions", "Material Request.tc_name"),
	},
	"Purchase": {
		("Asset", "Material Request Item.wip_composite_asset"),
		("Asset", "Purchase Order Item.wip_composite_asset"),
		("Bank Account", "Supplier.default_bank_account"),
		("Customer", "Material Request.customer"),
		("Manufacturer", "Material Request Item.manufacturer"),
		("Manufacturer", "Purchase Order Item.manufacturer"),
		("Sales Order", "Purchase Order Item.sales_order"),
		("Tax Withholding Category", "Supplier.tax_withholding_category"),
		("Tax Withholding Group", "Supplier.tax_withholding_group"),
	},
	"QA Manager": {
		("Customs Tariff Number", "Item.customs_tariff_number"),
	},
	"QC Inspector": {
		("Serial No", "Quality Inspection.item_serial_no"),
	},
	"Sales": {
		("Coupon Code", "Quotation.coupon_code"),
		("Delivery Trip", "Delivery Note.delivery_trip"),
		("Market Segment", "Customer.market_segment"),
		("Material Request", "Delivery Note Item.material_request"),
		("Purchase Receipt", "Delivery Note.inter_company_reference"),
		("Quality Inspection", "Delivery Note Item.quality_inspection"),
		("Serial No", "Serial and Batch Entry.serial_no"),
		("Supplier", "Delivery Note.transporter"),
		("Supplier Quotation", "Quotation.supplier_quotation"),
	},
	"Stores": {
		("Asset", "Material Request Item.wip_composite_asset"),
		("Asset", "Purchase Receipt Item.wip_composite_asset"),
		("Cost Center", "Delivery Note Item.cost_center"),
		("Cost Center", "Delivery Note.cost_center"),
		("Cost Center", "Landed Cost Taxes and Charges.cost_center"),
		("Cost Center", "Material Request Item.cost_center"),
		("Cost Center", "Purchase Receipt Item.cost_center"),
		("Cost Center", "Purchase Receipt.cost_center"),
		("Cost Center", "Purchase Taxes and Charges.cost_center"),
		("Cost Center", "Sales Taxes and Charges.cost_center"),
		("Cost Center", "Stock Entry Detail.cost_center"),
		("Cost Center", "Stock Entry.cost_center"),
		("Price List", "Delivery Note.selling_price_list"),
		("Price List", "Material Request.buying_price_list"),
		("Price List", "Purchase Receipt.buying_price_list"),
		("Purchase Taxes and Charges Template", "Purchase Receipt.taxes_and_charges"),
		("Sales Partner", "Delivery Note.sales_partner"),
		("Sales Person", "Sales Team.sales_person"),
		("Sales Taxes and Charges Template", "Delivery Note.taxes_and_charges"),
		("Sales Taxes and Charges Template", "Stock Entry.taxes_and_charges"),
		("Shipping Rule", "Delivery Note.shipping_rule"),
		("Shipping Rule", "Purchase Receipt.shipping_rule"),
	},
}


@verifies("URS-CMP-036")
class TestEveryJobCanUseItsForms(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_only_the_reviewed_lookups_stay_closed(self):
		self.assertEqual(closed_lookups(), REVIEWED_CLOSED)

	def test_the_lookups_found_closed_in_the_pilot_now_search(self):
		for profile, doctype, reference in (
			("Operator", "Employee", "Job Card Time Log"),
			("Operator", "Item", "Job Card Item"),
			("Production Supervisor", "Stock Entry Type", "Stock Entry"),
			("QC Inspector", "Quality Inspection Parameter", "Quality Inspection Reading"),
			("Maintenance", "Asset Maintenance", "Asset Maintenance Log"),
		):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile, doctype=doctype):
				search_link(doctype, "", reference_doctype=reference)

	def test_maintenance_picks_a_task_through_the_forms_own_query(self):
		from erpnext.assets.doctype.asset_maintenance_log.asset_maintenance_log import get_maintenance_tasks

		frappe.set_user(user_with_profile("Maintenance"))
		get_maintenance_tasks("Asset Maintenance Task", "", "name", 0, 10, {"asset_maintenance": "none"})

	def test_picking_never_opens_the_record(self):
		frappe.set_user(user_with_profile("Operator"))

		self.assertFalse(frappe.has_permission("Employee", "read"))
		self.assertFalse(frappe.has_permission("Item", "read"))

	def test_stock_and_production_forms_can_read_the_stock_settings(self):
		for profile in ("Stores", "Production Supervisor"):
			frappe.set_user(user_with_profile(profile))
			with self.subTest(profile=profile):
				frappe.client.get_single_value("Stock Settings", "disable_serial_no_and_batch_selector")
				self.assertFalse(frappe.has_permission("Stock Settings", "write"))
