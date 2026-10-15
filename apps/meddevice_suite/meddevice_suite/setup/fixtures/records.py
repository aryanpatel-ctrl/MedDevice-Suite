"""Site-neutral record builders shared by the pilot loader, the browser journey and the tests.

Each makes a record as the system makes it, only if missing, and never refuses a site: the pilot
loader (pilot/demo.py) calls them with its demo data, the browser journey (qa/ui_journey.py) and
other modules' tests with their own.
"""

import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, today

from meddevice_suite.master_data import customer_licence, item_master

LICENCE_TYPE = "Registration for the sale of medical devices (demo)"
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


def lot_of(row: Document) -> str:
	"""The lot of a stock line: ERPNext v16 keeps it in the line's Serial and Batch Bundle."""
	return row.batch_no or frappe.db.get_value(
		"Serial and Batch Entry", {"parent": row.serial_and_batch_bundle}, "batch_no"
	)
