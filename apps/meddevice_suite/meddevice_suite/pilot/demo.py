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
  "pilot_user_password", never from the code;
- every item approved by the QA Manager's demo user with a signature (module 1), with a
  fictional GTIN in the GS1 range for restricted circulation (20…), never a real one.

Everything is fictional and marked "(Demo)". Sterilization, QC release and batch
release are not modelled here: they arrive as their own quality gates in Phases 3
to 9. Running the loader again adds only what is missing.
"""

import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.utils import add_days, today

from meddevice_suite.master_data import customer_licence, item_master
from meddevice_suite.pilot import refuse_unless_pilot_site
from meddevice_suite.pilot.inspection import ensure_inspection_masters, ensure_inspection_template
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
# Module 5: the hospital buys for its own use without a licence, as QA decides; the distributor
# holds a licence QA has verified.
HOSPITAL, DISTRIBUTOR = CUSTOMERS
LICENCE_TYPE = "Registration for the sale of medical devices (demo)"

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
# The item master's data for the demo (module 1). Foley catheters and urine bags are class B
# under the Medical Devices Rules 2017; the client's own values replace these.
ITEM_TYPES = {"Raw Material": item_master.RAW_MATERIAL, "Products": item_master.FINISHED_GOOD}
STORAGE_CONDITION = "Store below 30 °C in a dry place, away from direct sunlight (demo)"
PRODUCT_DEVICE_DATA = {
	item_master.STERILE: "Yes",
	item_master.DEVICE_CLASS: "B",
	item_master.UNITS_PER_BOX: 10,
	item_master.BOXES_PER_CARTON: 20,
}
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

	for profile in JOB_PROFILES:  # QA's demo user approves the items below
		ensure_demo_user(profile, password)
	ensure_customers_may_buy(password)
	approve = approve_as("QA Manager", password)
	ensure_inspection_masters(company, approve, approve_as("QA Manager", password, "Asset"))
	for code, (name, unit, hsn, shelf_life, supplier, rate) in RAW_MATERIALS.items():
		ensure_item(
			code, name, "Raw Material", unit, hsn, shelf_life, company, store("Stores"), supplier=supplier
		)
		ensure_price(code, "Standard Buying", rate)
		ensure_inspection_template(code, "Raw Material")
	for code, (name, hsn, rate, _line, _materials) in PRODUCTS.items():
		ensure_item(
			code, name, "Products", "Nos", hsn, PRODUCT_SHELF_LIFE_DAYS, company, store("Finished Goods")
		)
		ensure_price(code, "Standard Selling", rate)
		ensure_inspection_template(code, "Products")
	# Each item set up with its specification, QA approves it before any record uses it.
	for code in (*RAW_MATERIALS, *PRODUCTS):
		approve(code)

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
	approve_bom = approve_as("QA Manager", password, "BOM")
	for code, (_name, _hsn, _rate, line, materials) in PRODUCTS.items():
		ensure_bom(code, line, materials, company, approve_bom)


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
	master_data = item_master_data(code, group)
	if frappe.db.exists("Item", code):
		# An item loaded before lot control defaulted to the quarantine store; its default is the
		# approved store a release moves the lot to, so the loader corrects it. One loaded before
		# the item master (module 1) is given its data, while still in draft.
		item = frappe.get_doc("Item", code)
		default = next((row for row in item.item_defaults if row.company == company), None)
		changed = bool(default and default.default_warehouse != warehouse)
		if changed:
			default.default_warehouse = warehouse
		if not item_master.approved(item):
			for field, value in master_data.items():
				if not item.get(field):
					item.set(field, value)
					changed = True
		if changed:
			item.save(ignore_permissions=True)  # demo loader
		return
	ensure(
		"Item",
		code,
		**master_data,
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


def demo_gtin(code: str) -> str:
	"""A fictional GTIN-13 for a demo item: GS1's prefix 20 is for restricted circulation, so it
	can never be another company's product."""
	body = "20" + f"{zlib.crc32(code.encode()):010d}"[-10:]
	total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
	return body + str((10 - total % 10) % 10)


def item_master_data(code: str, group: str) -> dict:
	"""The item master's fields of a demo item (module 1)."""
	data = {item_master.ITEM_TYPE: ITEM_TYPES[group], item_master.STORAGE_CONDITION: STORAGE_CONDITION}
	if group == "Products":
		data.update(PRODUCT_DEVICE_DATA)
		data[item_master.UDI_DI] = demo_gtin(code)
	return data


def approve_as(profile: str, password: str, doctype: str = "Item") -> Callable[[str], None]:
	"""QA's signed approval of a demo item (module 1), bill of materials (module 2) or piece of
	equipment (module 8), by the demo user of the job; one approved already is left as it is."""
	approved, act, reason = signed_approval(doctype)

	def approve(name: str) -> None:
		if approved(name):
			return
		from meddevice_suite.pilot.flow import as_job  # the flow builds on this module

		with as_job(profile):
			act(frappe.get_doc(doctype, name), password=password, reason=reason)

	return approve


def signed_approval(doctype: str) -> tuple[Callable[[str], bool], Callable, str]:
	"""Whether a record of the type is approved, QA's signed act that approves it, and the reason
	the demo gives."""
	from meddevice_suite.master_data import bom as bill_of_materials
	from meddevice_suite.master_data import equipment

	return {
		"Item": (item_master.approved, item_master.approve, "Item master and specification checked (demo)"),
		"BOM": (
			lambda name: frappe.db.get_value("BOM", name, "docstatus") == 1,
			bill_of_materials.approve,
			"Components and quantities checked (demo)",
		),
		"Asset": (equipment.approved, equipment.approve, "Equipment details checked (demo)"),
	}[doctype]


def ensure_customers_may_buy(password: str) -> None:
	"""Module 5, as the system records it: Sales records the distributor's licence and QA verifies
	it with a signature; QA decides with a signature that the hospital buys without one."""
	from meddevice_suite.pilot.flow import as_job  # the flow builds on this module

	ensure_licence(
		DISTRIBUTOR,
		lambda: as_job("Sales"),
		lambda: as_job("QA Manager"),
		password,
		"Licence copy checked (demo)",
	)
	if not customer_licence.buys_without_licence(HOSPITAL):
		with as_job("QA Manager"):
			customer_licence.exempt_from_licence(
				frappe.get_doc("Customer", HOSPITAL),
				password=password,
				reason="Hospital buying for its own use (demo)",
			)


def ensure_licence(
	customer: str,
	as_sales: Callable[[], AbstractContextManager],
	as_qa: Callable[[], AbstractContextManager],
	password: str,
	reason: str,
) -> None:
	"""A verified licence of a demo or journey customer, made as the system makes one: recorded by
	Sales, verified by QA with a signature. A customer licensed today is left as it is."""
	if customer_licence.licences_valid_on(customer, today()):
		return
	ensure(
		customer_licence.TYPE_DOCTYPE,
		LICENCE_TYPE,
		licence_type=LICENCE_TYPE,
		description="A registration to sell, stock or distribute medical devices (demo).",
	)
	with as_sales():
		licence = frappe.get_doc(
			{
				"doctype": customer_licence.DOCTYPE,
				"customer": customer,
				"licence_type": LICENCE_TYPE,
				"licence_number": f"DEMO-{zlib.crc32(customer.encode()):08X}",
				"issuing_authority": "State Licensing Authority (demo)",
				"valid_from": add_days(today(), -30),
				"valid_until": add_days(today(), 730),
				"licence_copy": licence_copy(customer),
			}
		).insert()
	with as_qa():
		customer_licence.verify(licence, password=password, reason=reason)


def licence_copy(customer: str) -> str:
	"""A placeholder copy of a demo customer's licence, as a private file."""
	name = f"licence-{frappe.scrub(customer)}.pdf"
	existing = frappe.db.get_value("File", {"file_name": name, "is_private": 1}, "file_url")
	if existing:
		return existing
	return (
		frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1, "content": blank_pdf()})
		.insert(ignore_permissions=True)  # demo loader
		.file_url
	)


def blank_pdf() -> bytes:
	"""A one-page PDF standing in for a licence copy on the pilot and test sites."""
	from io import BytesIO

	from pypdf import PdfWriter  # Frappe's own PDF library

	writer, out = PdfWriter(), BytesIO()
	writer.add_blank_page(width=595, height=842)
	writer.write(out)
	return out.getvalue()


def ensure_price(code: str, price_list: str, rate: float) -> None:
	if not frappe.db.exists("Item Price", {"item_code": code, "price_list": price_list}):
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": code, "price_list": price_list, "price_list_rate": rate}
		).insert(ignore_permissions=True)  # demo loader


def ensure_bom(
	code: str, line: str, materials: dict[str, float], company: str, approve: Callable[[str], None]
) -> None:
	"""The product's bill of materials, approved by QA with a signature (module 2)."""
	if frappe.db.exists("BOM", {"item": code, "is_default": 1, "docstatus": 1}):
		return
	if draft := frappe.db.get_value("BOM", {"item": code, "docstatus": 0}):  # left by an earlier run
		approve(draft)
		return
	bom = frappe.get_doc(
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
	).insert(ignore_permissions=True)  # demo loader
	approve(bom.name)


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
