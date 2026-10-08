"""Demo data for the pilot site (Phase 2, task S1).

	bench --site pilot.localhost execute meddevice_suite.pilot.demo.load_demo_data

Loads the masters the pilot walk-through needs, for the site's default company:

- the quarantine, rejected and hold stores next to ERPNext's own stores (D2
  "Quarantine / Approved Stock"; Stores is the approved store);
- Foley catheters and urine bags with their raw materials, batch-tracked with an
  expiry date, received into quarantine;
- one routing per production line with the process steps of D3, and a BOM per
  product built on it;
- demo suppliers, customers and price lists;
- one demo user per job profile, whose password comes from the site_config key
  "pilot_user_password", never from the code.

Everything is fictional and marked "(Demo)". Sterilization, QC release and batch
release are not modelled here: they arrive as their own quality gates in Phases 3
to 9. Running the loader again adds only what is missing.
"""

import frappe
from frappe import _

from meddevice_suite.pilot import refuse_unless_pilot_site
from meddevice_suite.setup.roles import JOB_PROFILES

DEMO = "(Demo)"
EXTRA_STORES = ("Quarantine", "Rejected", "Hold")

SUPPLIERS = (
	"Silicone Compounds (Demo)",
	"Latex Supplies (Demo)",
	"Medical Films (Demo)",
	"Tubing and Connectors (Demo)",
	"Packaging Supplies (Demo)",
)
CUSTOMERS = ("City Hospital (Demo)", "Regional Medical Distributor (Demo)")

# code -> (name, unit, HSN, shelf life in days, supplier, buying rate)
RAW_MATERIALS: dict[str, tuple[str, str, str, int, str, float]] = {
	"RM-SILICONE": ("Medical-grade silicone compound", "Kg", "391000", 730, SUPPLIERS[0], 1450),
	"RM-LATEX": ("Natural rubber latex, prevulcanised", "Kg", "40011010", 365, SUPPLIERS[1], 260),
	"RM-PVC-FILM": ("Medical PVC film", "Kg", "39204900", 730, SUPPLIERS[2], 180),
	"RM-PVC-TUBE": ("PVC drainage tubing", "Meter", "39172390", 730, SUPPLIERS[3], 12),
	"RM-CONNECTOR": ("Funnel connector with valve", "Nos", "39269099", 1825, SUPPLIERS[3], 3.5),
	"RM-OUTLET-TAP": ("Urine bag outlet tap", "Nos", "39269099", 1825, SUPPLIERS[3], 2.2),
	"RM-POUCH": ("Peel pouch, sterile barrier", "Nos", "39232990", 1825, SUPPLIERS[4], 1.8),
	"RM-CARTON": ("Shipper carton", "Nos", "48191010", 1825, SUPPLIERS[4], 25),
}

CATHETER_LINE, URINE_BAG_LINE = "Foley Catheter Line", "Urine Bag Line"

# code -> (name, HSN, selling rate, production line, materials per BOM batch of 100 pieces)
PRODUCTS: dict[str, tuple[str, str, float, str, dict[str, float]]] = {
	"FC-2W-SIL-14": (
		"Foley catheter, 2-way, silicone, 14 Fr",
		"90183910",
		95,
		CATHETER_LINE,
		{"RM-SILICONE": 1.2, "RM-CONNECTOR": 100, "RM-POUCH": 100, "RM-CARTON": 1},
	),
	"FC-2W-SIL-16": (
		"Foley catheter, 2-way, silicone, 16 Fr",
		"90183910",
		98,
		CATHETER_LINE,
		{"RM-SILICONE": 1.4, "RM-CONNECTOR": 100, "RM-POUCH": 100, "RM-CARTON": 1},
	),
	"FC-2W-LTX-16": (
		"Foley catheter, 2-way, latex, 16 Fr",
		"90183910",
		32,
		CATHETER_LINE,
		{"RM-LATEX": 1.6, "RM-CONNECTOR": 100, "RM-POUCH": 100, "RM-CARTON": 1},
	),
	"FC-3W-SIL-18": (
		"Foley catheter, 3-way, silicone, 18 Fr",
		"90183910",
		145,
		CATHETER_LINE,
		{"RM-SILICONE": 1.8, "RM-CONNECTOR": 200, "RM-POUCH": 100, "RM-CARTON": 1},
	),
	"UB-2000-TAP": (
		"Urine bag, 2000 ml, with outlet tap",
		"90189099",
		38,
		URINE_BAG_LINE,
		{
			"RM-PVC-FILM": 4.5,
			"RM-PVC-TUBE": 90,
			"RM-CONNECTOR": 100,
			"RM-OUTLET-TAP": 100,
			"RM-POUCH": 100,
			"RM-CARTON": 2,
		},
	),
	"UB-2000": (
		"Urine bag, 2000 ml, without outlet",
		"90189099",
		30,
		URINE_BAG_LINE,
		{"RM-PVC-FILM": 4.5, "RM-PVC-TUBE": 90, "RM-CONNECTOR": 100, "RM-POUCH": 100, "RM-CARTON": 2},
	),
}
PRODUCT_SHELF_LIFE_DAYS = 1825
# Pouches and cartons are whole units, so BOMs are for a production batch of 100 pieces.
BOM_QUANTITY = 100

# The common processes and packing that both lines end with (D3 3.3 and lane 5).
FINISHING = [
	("Connector Assembly", "Assembly Bench", 30),
	("Leak Test (100%)", "Leak Test Station", 20),
	("Cleaning", "Washing and Drying Room", 10),
	("Drying", "Washing and Drying Room", 20),
	("Primary Packing", "Packing Line", 10),
	("Secondary Packing", "Packing Line", 5),
	("Labelling", "Packing Line", 5),
]
# line -> [(operation, workstation, minutes per batch of 100)], in process order (D3 3.1, 3.2)
ROUTINGS: dict[str, list[tuple[str, str, float]]] = {
	CATHETER_LINE: [
		("Tube Extrusion", "Extrusion Line", 50),
		("Balloon Forming", "Balloon Forming Station", 40),
		("Tip Formation", "Tipping Station", 20),
		("Eyelet Making", "Eyelet Punching Station", 10),
		("Printing and Marking", "Printing Machine", 10),
		*FINISHING,
	],
	URINE_BAG_LINE: [
		("Film Blowing", "Film Blowing Line", 30),
		("Bag Forming", "Bag Forming Welder", 40),
		("Port Fitting", "Port Fitting Station", 30),
		("Tube Welding", "Tube Welding Station", 30),
		("Printing and Marking", "Printing Machine", 10),
		*FINISHING,
	],
}


def load_demo_data() -> None:
	refuse_unless_pilot_site()
	password = frappe.conf.pilot_user_password
	if not password:
		frappe.throw(_("Set pilot_user_password in the pilot site's site_config before loading demo data."))

	company = frappe.defaults.get_global_default("company")
	abbr = frappe.db.get_value("Company", company, "abbr")

	def store(name: str) -> str:
		return f"{name} - {abbr}"  # ERPNext names a company's warehouses this way

	for name in EXTRA_STORES:
		ensure(
			"Warehouse",
			store(name),
			warehouse_name=name,
			company=company,
			parent_warehouse=store("All Warehouses"),
		)
	for name in SUPPLIERS:
		ensure("Supplier", name, supplier_name=name, supplier_group="Raw Material")
	for name in CUSTOMERS:
		ensure("Customer", name, customer_name=name, customer_group="Commercial", territory="India")

	for code, (name, unit, hsn, shelf_life, supplier, rate) in RAW_MATERIALS.items():
		ensure_item(
			code, name, "Raw Material", unit, hsn, shelf_life, company, store("Stores"), supplier=supplier
		)
		ensure_price(code, "Standard Buying", rate)
	for code, (name, hsn, rate, _line, _materials) in PRODUCTS.items():
		ensure_item(
			code, name, "Products", "Nos", hsn, PRODUCT_SHELF_LIFE_DAYS, company, store("Finished Goods")
		)
		ensure_price(code, "Standard Selling", rate)

	for line, steps in ROUTINGS.items():
		for operation, workstation, _minutes in steps:
			ensure("Workstation", workstation, workstation_name=workstation, production_capacity=1)
			ensure("Operation", operation, name=operation, workstation=workstation)  # named by the user
		ensure(
			"Routing",
			line,
			routing_name=line,
			operations=[
				{
					"operation": operation,
					"workstation": workstation,
					"time_in_mins": minutes,
					"sequence_id": index,
				}
				for index, (operation, workstation, minutes) in enumerate(steps, 1)
			],
		)
	for code, (_name, _hsn, _rate, line, materials) in PRODUCTS.items():
		ensure_bom(code, line, materials, company)

	for profile in JOB_PROFILES:
		ensure_demo_user(profile, password)


def ensure(doctype: str, existing: str, **values) -> None:
	"""Create the record unless one named `existing` is already there."""
	if not frappe.db.exists(doctype, existing):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)  # demo loader


def ensure_item(
	code: str,
	name: str,
	group: str,
	unit: str,
	hsn: str,
	shelf_life: int,
	company: str,
	warehouse: str,
	supplier: str | None = None,
) -> None:
	if frappe.db.exists("Item", code):
		# An item loaded before lot control defaulted to the quarantine store; its default is the
		# approved store a release moves the lot to, so the loader corrects it.
		item = frappe.get_doc("Item", code)
		default = next((row for row in item.item_defaults if row.company == company), None)
		if default and default.default_warehouse != warehouse:
			default.default_warehouse = warehouse
			item.save(ignore_permissions=True)  # demo loader
		return
	ensure(
		"Item",
		code,
		item_code=code,
		item_name=name,
		item_group=group,
		stock_uom=unit,
		gst_hsn_code=hsn,
		is_stock_item=1,
		is_purchase_item=int(group == "Raw Material"),
		is_sales_item=int(group == "Products"),
		has_batch_no=1,
		create_new_batch=1,
		batch_number_series=f"{code}-.YY..MM.-.####",
		has_expiry_date=1,
		shelf_life_in_days=shelf_life,
		item_defaults=[{"company": company, "default_warehouse": warehouse, "default_supplier": supplier}],
	)


def ensure_price(code: str, price_list: str, rate: float) -> None:
	if not frappe.db.exists("Item Price", {"item_code": code, "price_list": price_list}):
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": code, "price_list": price_list, "price_list_rate": rate}
		).insert(ignore_permissions=True)  # demo loader


def ensure_bom(code: str, line: str, materials: dict[str, float], company: str) -> None:
	if frappe.db.exists("BOM", {"item": code, "is_default": 1, "docstatus": 1}):
		return
	frappe.get_doc(
		{
			"doctype": "BOM",
			"item": code,
			"company": company,
			"quantity": BOM_QUANTITY,
			"is_active": 1,
			"is_default": 1,
			"with_operations": 1,
			"routing": line,
			"rm_cost_as_per": "Price List",
			"buying_price_list": "Standard Buying",
			"items": [{"item_code": item, "qty": qty} for item, qty in materials.items()],
		}
	).insert(ignore_permissions=True).submit()  # demo loader


def demo_user_email(profile: str) -> str:
	return f"{frappe.scrub(profile).replace('_', '.')}@demo.example.com"


def ensure_demo_user(profile: str, password: str) -> None:
	ensure(
		"User",
		demo_user_email(profile),
		email=demo_user_email(profile),
		first_name=profile,
		last_name=DEMO,
		send_welcome_email=0,
		new_password=password,
		role_profiles=[{"role_profile": profile}],
	)
