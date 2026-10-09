"""Demo masters for incoming inspection (modules 6 and 16): an inspection template per
item and one measuring instrument, for the pilot site and the test sites.

Parameter values are demo values marked as such; the client's specifications replace
them as data under document control (decided 2026-10-08: ISO 20696 for Foley catheters,
ISO 8669-2 for urine bags, ISO 2859-1 sampling). Everything is created only if missing.
"""

import frappe
from frappe.utils import today

INSTRUMENT_ITEM = "INST-CALIPER"
INSTRUMENT_NAME = "Digital Caliper (Demo)"
ASSET_CATEGORY = "Measuring Instruments (Demo)"
LOCATION = "QC Laboratory (Demo)"
APPEARANCE, DIMENSION = "Appearance", "Dimension (mm)"

# item group -> (specification, acceptance value or (min, max) in mm)
PARAMETERS: dict[str, tuple[tuple[str, object], ...]] = {
	"Raw Material": ((APPEARANCE, "Conforms"), (DIMENSION, (9.5, 10.5))),
	"Products": ((APPEARANCE, "Conforms"), (DIMENSION, (5.0, 5.4))),
}
# item group -> the stage its demo specification is for (module 6)
STAGES = {"Raw Material": "Incoming", "Products": "Final"}
METHODS = {APPEARANCE: "Visual, under daylight (demo)", DIMENSION: "Digital caliper (demo)"}
SAMPLE_SIZE = 5  # demo; the client's sampling plans (ISO 2859-1) replace it


def ensure_inspection_masters(company: str) -> str:
	"""The parameters and the instrument; returns the instrument's asset name."""
	for specification, _acceptance in {p for params in PARAMETERS.values() for p in params}:
		if not frappe.db.exists("Quality Inspection Parameter", specification):
			frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": specification}).insert(
				ignore_permissions=True  # demo loader
			)
	return ensure_instrument(company)


def ensure_inspection_template(item: str, group: str) -> str:
	"""The item's demo specification (module 6), set on the item; returns its name. A template
	made before specifications were controlled is completed once, as its first issue."""
	name = f"{item} IQC (Demo)"
	rows = []
	for specification, acceptance in PARAMETERS[group]:
		row = {"specification": specification, "mds_method": METHODS[specification]}
		if isinstance(acceptance, tuple):
			row.update(numeric=1, min_value=acceptance[0], max_value=acceptance[1])
		else:
			row.update(numeric=0, value=acceptance)
		rows.append(row)
	stated = {"mds_item": item, "mds_stage": STAGES[group], "mds_sample_size": SAMPLE_SIZE}
	if not frappe.db.exists("Quality Inspection Template", name):
		frappe.get_doc(
			{
				"doctype": "Quality Inspection Template",
				"quality_inspection_template_name": name,
				"item_quality_inspection_parameter": rows,
				**stated,
			}
		).insert(ignore_permissions=True)  # demo loader
	elif not frappe.db.get_value("Quality Inspection Template", name, "mds_item"):
		template = frappe.get_doc("Quality Inspection Template", name)
		template.update(stated)
		for row in template.item_quality_inspection_parameter:
			row.mds_method = METHODS.get(row.specification)
		template.save(ignore_permissions=True)  # demo loader: the template's first issue
	# A purchased item is inspected when it arrives (ERPNext's "Inspection Required before
	# Purchase", which also lists it on the inspection form); with inspection after receipt
	# switched on (setup/stock.py) the flag never holds up the receipt itself. Saved through the
	# item, so the audit trail records it.
	incoming = int(group == "Raw Material")
	doc = frappe.get_doc("Item", item)
	if doc.quality_inspection_template != name or doc.inspection_required_before_purchase != incoming:
		doc.quality_inspection_template = name
		doc.inspection_required_before_purchase = incoming
		doc.save(ignore_permissions=True)  # demo loader
	return name


def ensure_instrument(company: str) -> str:
	"""One measuring instrument as an ERPNext asset, so a reading can name it."""
	existing = frappe.db.get_value("Asset", {"asset_name": INSTRUMENT_NAME, "company": company})
	if existing:
		return existing
	if not frappe.db.exists("Asset Category", ASSET_CATEGORY):
		account = lambda account_type: frappe.db.get_value(  # noqa: E731
			"Account", {"company": company, "account_type": account_type, "is_group": 0}, "name"
		)
		frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": ASSET_CATEGORY,
				"accounts": [
					{
						"company_name": company,
						"fixed_asset_account": account("Fixed Asset"),
						"accumulated_depreciation_account": account("Accumulated Depreciation"),
						"depreciation_expense_account": account("Depreciation"),
					}
				],
			}
		).insert(ignore_permissions=True)  # demo loader
	if not frappe.db.exists("Item", INSTRUMENT_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": INSTRUMENT_ITEM,
				"item_name": "Digital caliper, 0 to 150 mm",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"gst_hsn_code": "90183910",  # the site carries this HSN; the exact code is the client's
				"is_stock_item": 0,
				"is_fixed_asset": 1,
				"asset_category": ASSET_CATEGORY,
			}
		).insert(ignore_permissions=True)  # demo loader
	if not frappe.db.exists("Location", LOCATION):
		frappe.get_doc({"doctype": "Location", "location_name": LOCATION}).insert(ignore_permissions=True)
	asset = frappe.get_doc(
		{
			"doctype": "Asset",
			"asset_name": INSTRUMENT_NAME,
			"item_code": INSTRUMENT_ITEM,
			"company": company,
			"location": LOCATION,
			"purchase_date": today(),
			"available_for_use_date": today(),
			"asset_type": "Existing Asset",  # bought before the system; no purchase document to book
			"gross_purchase_amount": 15000,
			"net_purchase_amount": 15000,
			"asset_quantity": 1,
			"calculate_depreciation": 0,
		}
	).insert(ignore_permissions=True)  # demo loader
	return asset.name


def readings_for(template: str, instrument: str, within_limits: bool = True) -> list[dict]:
	"""Readings for an inspection against the template: each inside its limits, or the
	measurement outside them when `within_limits` is false."""
	from erpnext.stock.doctype.quality_inspection_template.quality_inspection_template import (
		get_template_details,
	)

	readings = []
	for parameter in get_template_details(template):
		reading = {
			"specification": parameter.specification,
			"numeric": parameter.numeric,
			"value": parameter.value,
			"min_value": parameter.min_value,
			"max_value": parameter.max_value,
		}
		if parameter.numeric:
			low, high = float(parameter.min_value), float(parameter.max_value)
			reading["reading_1"] = f"{(low + high) / 2 if within_limits else high + 1:.2f}"
			reading["mds_instrument"] = instrument
		else:
			reading["reading_value"] = parameter.value if within_limits else "Does not conform"
		readings.append(reading)
	return readings
