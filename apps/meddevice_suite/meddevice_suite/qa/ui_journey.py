"""Masters for the browser test of the standard flow (URS-PLT-007), on a test site only.

The browser walks the standard flow through the real screens, signed in as each job
(cypress/integration/standard_flow.js), as the server test does through code
(pilot/test_standard_flow.py). The test site has the fixed test company of
qa/site_setup.py but none of the pilot's demo data, and the pilot's loader never runs
there; these helpers make what the journey needs instead, every record named "UIJ": a
product and its raw materials, batch-tracked with a shelf life, a supplier, a customer, the
quarantine store, a short routing with its workstations and the product's default BOM. Each
item is approved by the run's QA Manager user with a signature (module 1), with the password
the browser was given for the run. The site's defaults (company, price lists, stores) are left
as they are.

Every run starts from the same stock. A run that was interrupted can leave lots of the
journey's items in a store, and they would be picked before the run's own, so they are
written off first. Then a lot of a raw material and a lot of the product are put in store
that expire later than anything the run receives or makes: the browser shows that the lot
that expires first is taken first, although another lot was in store before it
(URS-STK-002).

Only the Administrator who runs the tests prepares the journey. Any signed-in user may ask
for the lots of a stock transaction they may read, which the browser checks after each step.
"""

import frappe
from frappe import _
from frappe.utils import add_days, today

from meddevice_suite.lot_control.lot_status import APPROVED, REJECTED, set_status, stock_by_lot
from meddevice_suite.master_data import item_master
from meddevice_suite.pilot.demo import BOM_QUANTITY, ensure, ensure_bom, ensure_item, ensure_price
from meddevice_suite.pilot.inspection import ensure_inspection_masters, ensure_inspection_template
from meddevice_suite.qa.site_setup import TEST_COMPANY
from meddevice_suite.qa.ui_users import email_for, ensure_test_site

COMPANY = TEST_COMPANY["company_name"]
CUSTOMER = "UIJ City Hospital"
SUPPLIER = "UIJ Medical Components"
QUARANTINE = "Quarantine"  # the pilot's name for it (pilot/demo.py EXTRA_STORES)

PRODUCT = "UIJ-FC-2W-SIL-16"
# name, HSN, selling rate, shelf life in days
PRODUCT_DETAILS = ("Foley catheter, 2-way, silicone, 16 Fr (browser test)", "90183910", 98, 1825)
# code -> (name, unit, HSN, shelf life in days, buying rate, quantity per BOM batch of 100 pieces)
RAW_MATERIALS: dict[str, tuple[str, str, str, int, float, float]] = {
	"UIJ-RM-SILICONE": ("Medical-grade silicone compound (browser test)", "Kg", "391000", 730, 1450, 1.4),
	"UIJ-RM-CONNECTOR": ("Funnel connector with valve (browser test)", "Nos", "39269099", 1825, 3.5, 100),
	"UIJ-RM-POUCH": ("Peel pouch, sterile barrier (browser test)", "Nos", "39232990", 1825, 1.8, 100),
}
ROUTING = "UIJ Catheter Line"
# (operation, workstation, minutes per batch of 100), in process order
OPERATIONS = (
	("UIJ Moulding", "UIJ Moulding Station", 20),
	("UIJ Leak Test", "UIJ Leak Test Station", 10),
	("UIJ Packing", "UIJ Packing Line", 10),
)
# One production batch: the run buys what it needs and delivers all it makes.
QUANTITY = BOM_QUANTITY

# The lots already in store when the run starts, and how long after today they expire:
# later than every shelf life above.
LATER_EXPIRY_DAYS = 3650
LOTS_IN_STORE = {"UIJ-RM-SILICONE": ("Stores", 1, 1450), PRODUCT: ("Finished Goods", 10, 60)}

STOCK_TRANSACTIONS = ("Purchase Receipt", "Stock Entry", "Delivery Note")


def journey_items() -> list[str]:
	return [PRODUCT, *RAW_MATERIALS]


def store(name: str) -> str:
	return f"{name} - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"  # as ERPNext names them


@frappe.whitelist(methods=["POST"])
def prepare_journey(password: str | None = None) -> dict:
	"""Make the journey's masters, start it from known stock, and say what it works with.
	`password` is the run's password of the job users (ui_users.prepare_job_users), with which
	the QA Manager user approves the items."""
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(_("The browser journey is prepared only by the Administrator."), frappe.PermissionError)

	ensure_masters(approve_as_qa(password))
	write_off_leftover_stock()
	return {
		"instrument": ensure_inspection_masters(COMPANY, approve_as_qa(password)),
		"company": COMPANY,
		"customer": CUSTOMER,
		"supplier": SUPPLIER,
		"product": PRODUCT,
		"product_shelf_life_days": PRODUCT_DETAILS[3],
		"quantity": QUANTITY,
		"rate": PRODUCT_DETAILS[2],
		"materials": {code: details[5] * QUANTITY / BOM_QUANTITY for code, details in RAW_MATERIALS.items()},
		"stores": {
			"quarantine": store(QUARANTINE),
			"approved": store("Stores"),
			"work_in_progress": store("Work In Progress"),
			"finished_goods": store("Finished Goods"),
		},
		"bom": frappe.db.get_value("BOM", {"item": PRODUCT, "is_default": 1, "docstatus": 1}),
		"operations": [operation for operation, _workstation, _minutes in OPERATIONS],
		"lots_in_store": put_later_expiring_lots(),
	}


def approve_as_qa(password: str | None):
	"""QA's signed approval of a journey item (module 1), as the run's QA Manager user."""

	def approve(code: str) -> None:
		if item_master.approved(code):
			return
		if not password:
			frappe.throw(_("Give the run's password of the job users to approve {0}.").format(code))
		administrator = frappe.session.user
		frappe.set_user(
			email_for("QA Manager")
		)  # nosemgrep: frappe-setuser - test site, Administrator only, checked by the caller
		try:
			item_master.approve(
				frappe.get_doc("Item", code), password=password, reason="Set up for the browser journey"
			)
		finally:
			frappe.set_user(administrator)  # nosemgrep: frappe-setuser - back to the Administrator

	return approve


def ensure_masters(approve) -> None:
	ensure_inspection_masters(COMPANY, approve)  # the parameters the templates below name
	ensure(
		"Warehouse",
		store(QUARANTINE),
		warehouse_name=QUARANTINE,
		company=COMPANY,
		parent_warehouse=store("All Warehouses"),
	)
	ensure("Supplier", SUPPLIER, supplier_name=SUPPLIER, supplier_group="Raw Material")
	ensure("Customer", CUSTOMER, customer_name=CUSTOMER, customer_group="Commercial", territory="India")
	for code, (name, unit, hsn, shelf_life, rate, _per_batch) in RAW_MATERIALS.items():
		ensure_item(
			code, name, "Raw Material", unit, hsn, shelf_life, COMPANY, store("Stores"), supplier=SUPPLIER
		)
		ensure_price(code, "Standard Buying", rate)
		ensure_inspection_template(code, "Raw Material")
	name, hsn, rate, shelf_life = PRODUCT_DETAILS
	ensure_item(PRODUCT, name, "Products", "Nos", hsn, shelf_life, COMPANY, store("Finished Goods"))
	ensure_price(PRODUCT, "Standard Selling", rate)
	ensure_inspection_template(PRODUCT, "Products")
	for code in journey_items():  # each with its specification, approved before it is used
		approve(code)

	for operation, workstation, _minutes in OPERATIONS:
		ensure("Workstation", workstation, workstation_name=workstation, production_capacity=1)
		ensure("Operation", operation, name=operation, workstation=workstation)  # named by the user
	ensure(
		"Routing",
		ROUTING,
		routing_name=ROUTING,
		operations=[
			{
				"operation": operation,
				"workstation": workstation,
				"time_in_mins": minutes,
				"sequence_id": index,
			}
			for index, (operation, workstation, minutes) in enumerate(OPERATIONS, 1)
		],
	)
	ensure_bom(PRODUCT, ROUTING, {code: details[5] for code, details in RAW_MATERIALS.items()}, COMPANY)


def write_off_leftover_stock() -> None:
	"""Scrap whatever stock of the journey's items an earlier run left in any store: each
	leftover lot is rejected first, because only a rejected lot can be scrapped (module 17)."""
	leftovers = [row for item in journey_items() for row in stock_by_lot(item)]
	for lot in {row.batch_no for row in leftovers}:
		set_status(lot, REJECTED, "Browser test: leftover of an earlier run, scrapped")
	if leftovers:
		post_stock_entry(
			"Material Issue",
			[
				{
					"item_code": row.item_code,
					"s_warehouse": row.warehouse,
					"qty": row.qty,
					"batch_no": row.batch_no,
					"use_serial_batch_fields": 1,
				}
				for row in leftovers
			],
		)


def put_later_expiring_lots() -> dict[str, str]:
	"""A lot of each item in LOTS_IN_STORE, in its store, expiring after anything the run makes."""
	lots, rows = {}, []
	for item, (store_name, qty, rate) in LOTS_IN_STORE.items():
		lot = frappe.get_doc(
			{
				"doctype": "Batch",
				"item": item,
				"manufacturing_date": today(),
				"expiry_date": add_days(today(), LATER_EXPIRY_DAYS),
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		lots[item] = lot.name
		rows.append(
			{
				"item_code": item,
				"t_warehouse": store(store_name),
				"qty": qty,
				"basic_rate": rate,
				"batch_no": lot.name,
				"use_serial_batch_fields": 1,
			}
		)
	post_stock_entry("Material Receipt", rows)
	# Stock that was already in an approved store before the run is approved stock.
	for lot in lots.values():
		set_status(lot, APPROVED, "Browser test: approved stock already in store before the run")
	return lots


def post_stock_entry(purpose: str, rows: list[dict]) -> None:
	entry = frappe.get_doc(
		{"doctype": "Stock Entry", "stock_entry_type": purpose, "company": COMPANY, "items": rows}
	)
	# test-site set-up by the Administrator, checked by the caller
	entry.insert(ignore_permissions=True)
	entry.submit()


# Any signed-in user may ask: the answer describes only a record the user may read.
@frappe.whitelist()
def lots_of(doctype: str, name: str) -> list[dict]:
	"""The lots each line of a stock transaction moved, with their expiry dates, in line order:
	[{item_code, from_store, to_store, batch_no, qty, expiry_date}]."""
	ensure_test_site()
	if doctype not in STOCK_TRANSACTIONS:
		frappe.throw(_("{0} is not a stock transaction.").format(_(doctype)))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	bundles = [row.serial_and_batch_bundle for row in doc.items if row.serial_and_batch_bundle]
	entries: dict[str, list[dict]] = {}
	for entry in frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": ["in", bundles or [""]]},
		fields=["parent", "batch_no", "qty"],
		order_by="idx",
	):
		entries.setdefault(entry.parent, []).append(entry)

	lots = []
	for row in doc.items:
		moved = entries.get(row.serial_and_batch_bundle) or [{"batch_no": row.batch_no, "qty": row.qty}]
		for entry in moved:
			lots.append(
				{
					"item_code": row.item_code,
					"from_store": row.get("s_warehouse")
					or (row.warehouse if doctype == "Delivery Note" else None),
					"to_store": row.get("t_warehouse")
					or (row.warehouse if doctype == "Purchase Receipt" else None),
					"batch_no": entry["batch_no"],
					"qty": abs(entry["qty"]),
				}
			)
	expiry = dict(
		frappe.get_all(
			"Batch",
			filters={"name": ["in", [lot["batch_no"] for lot in lots if lot["batch_no"]] or [""]]},
			fields=["name", "expiry_date"],
			as_list=True,
		)
	)
	for lot in lots:
		lot["expiry_date"] = expiry.get(lot["batch_no"])
	return lots
