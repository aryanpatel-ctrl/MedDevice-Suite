"""Which lookups in the forms a job fills in that job may not use (URS-CMP-036).

A form's link field searches the record type it points to, which needs at least
the select permission. A lookup the job may not use shows the person a permission
error when they click it. The permission matrix leaves some closed on purpose; this
lists those still closed, so that the test can hold them to the reviewed list, and
the browser test of each job (URS-PLT-009) leaves exactly those unsearched.
"""

import frappe

from meddevice_suite.setup.desk import DESK_AREAS
from meddevice_suite.setup.roles import JOB_PROFILES, PERMISSIONS, SYSTEM_ADMINISTRATION, fills_in_by_hand

AUTOMATIC_ROLES = ("All", "Desk User")

# Lookups left closed on purpose, reviewed on 2026-10-07: features the client's process
# does not use (coupons, loyalty, shipping rules, delivery trips, sales partners, projects,
# serial numbers, tax withholding, subcontracting) and optional fields ERPNext fills in
# itself (cost centre, price list and tax templates on stock documents). Each is reviewed
# again when the module that would use it is built; a new one fails setup/test_lookups.py.
# Added the same day, when the forms of each job's desk were included (URS-PLT-009): auto
# repeat, drivers, brands, drop shipping and the market segment are not used; the bill of
# materials and the material request of an order line are production's and purchasing's,
# and the planning customer only narrows which orders a plan fetches; stock moves and is
# inspected on receipts and deliveries, never on invoices (no lots or inspections there).
REVIEWED_CLOSED: dict[str, set[tuple[str, str]]] = {
	"Accounts": {
		("Driver", "Purchase Invoice.driver"),
		("Driver", "Sales Invoice.driver"),
		("Loyalty Program", "Sales Invoice.loyalty_program"),
		("Quality Inspection", "Purchase Invoice Item.quality_inspection"),
		("Quality Inspection", "Sales Invoice Item.quality_inspection"),
		("Serial and Batch Bundle", "Packed Item.serial_and_batch_bundle"),
		("Serial and Batch Bundle", "Purchase Invoice Item.rejected_serial_and_batch_bundle"),
		("Serial and Batch Bundle", "Purchase Invoice Item.serial_and_batch_bundle"),
		("Serial and Batch Bundle", "Sales Invoice Item.serial_and_batch_bundle"),
	},
	"Maintenance": {
		("Purchase Invoice", "Asset Repair Purchase Invoice.purchase_invoice"),
		("Serial and Batch Bundle", "Asset Repair Consumed Item.serial_and_batch_bundle"),
	},
	"Management": {
		("Auto Repeat", "Sales Order.auto_repeat"),
		("Brand", "Pricing Rule Brand.brand"),
		("Brand", "Pricing Rule.other_brand"),
		("Delivery Trip", "Delivery Note.delivery_trip"),
		("Loyalty Program", "Customer.loyalty_program"),
	},
	"Operator": {
		("Project", "Job Card.project"),
		("Quality Inspection", "Job Card.quality_inspection"),
		("Quality Inspection Template", "Job Card.quality_inspection_template"),
	},
	"Production Supervisor": {
		("Customer", "Production Plan.customer"),
		("Supplier", "Production Plan Sub Assembly Item.supplier"),
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
		("Manufacturer", "Supplier Quotation Item.manufacturer"),
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
		("Auto Repeat", "Sales Order.auto_repeat"),
		("BOM", "Sales Order Item.bom_no"),
		("Coupon Code", "Sales Order.coupon_code"),
		("Market Segment", "Lead.market_segment"),
		("Market Segment", "Opportunity.market_segment"),
		("Material Request", "Sales Order Item.material_request"),
		("Supplier", "Sales Order Item.supplier"),
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


def closed_lookups() -> dict[str, set[tuple[str, str]]]:
	"""job profile -> {(record type looked up, "Form.field")} the job cannot search."""
	closed: dict[str, set[tuple[str, str]]] = {}
	for profile, roles in JOB_PROFILES.items():
		if SYSTEM_ADMINISTRATION in roles:
			continue
		user_roles = (*roles, *AUTOMATIC_ROLES)
		for doctype in sorted(job_forms(user_roles)):
			meta = frappe.get_meta(doctype)
			for form in (meta, *(frappe.get_meta(df.options) for df in meta.get_table_fields())):
				for df in form.fields:
					if df.fieldtype != "Link" or df.read_only or df.hidden or df.options == "DocType":
						continue
					if frappe.get_meta(df.options).istable:
						continue  # a child table's rows are searched through their parent by the form's own query
					if not granted(df.options, user_roles, "select", "read"):
						closed.setdefault(profile, set()).add((df.options, f"{form.name}.{df.fieldname}"))
	return closed


def job_forms(roles: tuple[str, ...]) -> set[str]:
	"""The forms a job fills in: the regulated record types it may write or create, and the
	record types the sidebars of its desk areas list that it may create, which are the new
	forms the browser test of each job opens (URS-PLT-009)."""
	regulated = {doctype for doctype in PERMISSIONS if fills_in(doctype, roles)}
	areas = [area for area, area_roles in DESK_AREAS.items() if set(area_roles) & set(roles)]
	listed = frappe.get_all(
		"Workspace Sidebar Item",
		filters={"parenttype": "Workspace Sidebar", "parent": ["in", areas], "link_type": "DocType"},
		pluck="link_to",
		distinct=True,
	)
	return regulated | {doctype for doctype in listed if is_form(doctype) and creates_by_hand(doctype, roles)}


def fills_in(doctype: str, roles: tuple[str, ...]) -> bool:
	"""Whether any of the roles fills in a regulated record type's form by hand."""
	rows = PERMISSIONS[doctype]
	return any(fills_in_by_hand(doctype, role, rows.get(role, ())) for role in roles)


def creates_by_hand(doctype: str, roles: tuple[str, ...]) -> bool:
	"""A listed record type the job opens a new form for: by the matrix when it is regulated,
	by ERPNext's own create permission otherwise."""
	if doctype in PERMISSIONS:
		return fills_in(doctype, roles)
	return granted(doctype, roles, "create")


def is_form(doctype: str | None) -> bool:
	"""A record type with a form of its own: not a table row, not a single settings record."""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return False
	meta = frappe.get_meta(doctype)
	return not (meta.istable or meta.issingle)


def granted(doctype: str, roles: tuple[str, ...], *flags: str) -> bool:
	"""Whether any of the roles has any of the flags on the record type, at permission level 0."""
	table = "Custom DocPerm" if frappe.db.exists("Custom DocPerm", {"parent": doctype}) else "DocPerm"
	return bool(
		frappe.get_all(
			table,
			filters={"parent": doctype, "permlevel": 0, "role": ["in", roles]},
			or_filters={flag: 1 for flag in flags},
			limit=1,
		)
	)
