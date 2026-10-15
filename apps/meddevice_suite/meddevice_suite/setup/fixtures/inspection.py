"""Demo masters for inspection (modules 6 and 16): an inspection template per item, a product's
in-process specification, and one measuring instrument, for the pilot site and the test sites.

Parameter values are demo values marked as such; the client's specifications replace
them as data under document control (decided 2026-10-08: ISO 20696 for Foley catheters,
ISO 8669-2 for urine bags, ISO 2859-1 sampling). Everything is created only if missing. QA
approves each specification through the caller's `approve` (plan D-33): with a signature on the
pilot and in the browser journey (setup/fixtures/documents.py), as the signed act records it in other
modules' tests (setup/test_stock.py).
"""

from collections.abc import Callable

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from meddevice_suite.master_data import equipment, item_master, master_approval

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
IN_PROCESS_STAGE = "In Process"
METHODS = {APPEARANCE: "Visual, under daylight (demo)", DIMENSION: "Digital caliper (demo)"}
SAMPLE_SIZE = 5  # demo; the client's sampling plans (ISO 2859-1) replace it
# The instrument's equipment master (module 8), demo values.
INSTRUMENT_EQUIPMENT = {
	equipment.TAG: "QC-CAL-001 (demo)",
	equipment.CLASS: "Measuring instrument",
	equipment.MAKER: "Demo Instruments",
	equipment.MODEL: "DC-150 (demo)",
	equipment.SERIAL: "DEMO-0001",
	equipment.QUALITY_IMPACT: 1,
	equipment.CALIBRATION: 1,
	equipment.CALIBRATION_DAYS: 365,
}


def ensure_inspection_masters(
	company: str,
	approve: Callable[[str], None] | None = None,
	approve_equipment: Callable[[str], None] | None = None,
) -> str:
	"""The parameters and the instrument; returns the instrument's asset name. `approve` is QA's
	signed approval of the instrument's item (module 1), `approve_equipment` of the instrument
	itself (module 8), each needed the first time only."""
	for specification, _acceptance in {p for params in PARAMETERS.values() for p in params}:
		if not frappe.db.exists("Quality Inspection Parameter", specification):
			frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": specification}).insert(
				ignore_permissions=True  # demo loader
			)
	return ensure_instrument(company, approve, approve_equipment)


def parameter_rows(group: str) -> list[dict]:
	"""The demo parameters of an item group's specification, with their method and limits."""
	rows = []
	for specification, acceptance in PARAMETERS[group]:
		row = {"specification": specification, "mds_method": METHODS[specification]}
		if isinstance(acceptance, tuple):
			row.update(numeric=1, min_value=acceptance[0], max_value=acceptance[1])
		else:
			row.update(numeric=0, value=acceptance)
		rows.append(row)
	return rows


def ensure_in_process_specification(item: str, approve: Callable[[str], None] | None = None) -> str:
	"""The product's demo in-process specification (module 6): the checks the operations of its
	routing carry, which its device master record names (module 31); returns its name. A product
	that has one keeps it. `approve` is QA's signed approval of it (plan D-33), given when it is
	not approved yet; without it the specification is left as it is."""
	name = frappe.db.get_value(
		"Quality Inspection Template", {"mds_item": item, "mds_stage": IN_PROCESS_STAGE}
	)
	if not name:
		name = new_in_process_specification(item)
	if approve and not master_approval.approved("Quality Inspection Template", name):
		approve(name)
	return name


def new_in_process_specification(item: str) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Quality Inspection Template",
				"quality_inspection_template_name": f"{item} IPQC (Demo)",
				"item_quality_inspection_parameter": parameter_rows("Products"),
				"mds_item": item,
				"mds_stage": IN_PROCESS_STAGE,
				"mds_sample_size": SAMPLE_SIZE,
			}
		)
		.insert(ignore_permissions=True)  # demo loader: a new specification, its first issue
		.name
	)


def ensure_inspection_template(item: str, group: str, approve: Callable[[str], None] | None = None) -> str:
	"""The item's demo specification (module 6), set on the item; returns its name. A template
	made before specifications were controlled is completed once, as its first issue. `approve` is
	QA's signed approval of it (plan D-33), given when it is not approved yet; without it the
	specification is left as it is."""
	name = f"{item} IQC (Demo)"
	rows = parameter_rows(group)
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
	if approve and not master_approval.approved("Quality Inspection Template", name):
		approve(name)
	return name


def approve_instrument_item(approve: Callable[[str], None] | None) -> None:
	"""QA's approval of the instrument's item (module 1), given by `approve` the first time."""
	if item_master.approved(INSTRUMENT_ITEM):
		return
	item = frappe.get_doc("Item", INSTRUMENT_ITEM)
	if not item.get(item_master.ITEM_TYPE):  # loaded before the item master
		item.set(item_master.ITEM_TYPE, item_master.EQUIPMENT)
		item.save(ignore_permissions=True)  # demo loader, while the item is in draft
	if approve is None:
		frappe.throw(_("QA approves {0} before an asset is made of it.").format(INSTRUMENT_ITEM))
	approve(INSTRUMENT_ITEM)


def ensure_instrument(
	company: str,
	approve: Callable[[str], None] | None = None,
	approve_equipment: Callable[[str], None] | None = None,
) -> str:
	"""One measuring instrument as an ERPNext asset, so a reading can name it. Its item is approved
	by QA (module 1) before the asset is made of it, and the instrument itself as equipment
	(module 8) before a reading names it."""
	existing = frappe.db.get_value("Asset", {"asset_name": INSTRUMENT_NAME, "company": company})
	if existing:  # made before the item master: its item is approved now
		approve_instrument_item(approve)
		return approved_instrument(existing, approve_equipment)
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
				item_master.ITEM_TYPE: item_master.EQUIPMENT,
			}
		).insert(ignore_permissions=True)  # demo loader
	approve_instrument_item(approve)
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
			**INSTRUMENT_EQUIPMENT,
		}
	).insert(ignore_permissions=True)  # demo loader
	return approved_instrument(asset.name, approve_equipment)


def approved_instrument(asset: str, approve_equipment: Callable[[str], None] | None) -> str:
	"""The instrument with its equipment details, approved as equipment by QA (module 8)."""
	if equipment.approved(asset):
		if approve_equipment:
			approve_equipment(asset)  # each is idempotent; a test fixture also brings its care in date
		return asset
	doc = frappe.get_doc("Asset", asset)
	missing = {field: value for field, value in INSTRUMENT_EQUIPMENT.items() if not doc.get(field)}
	if missing or not doc.get("location"):
		values = {**missing, "location": doc.location or LOCATION}
		if doc.docstatus == 1:
			# A demo instrument submitted before the equipment master: its details are filled in
			# once, as data the module did not have yet (pilot and test sites only).
			frappe.db.set_value("Asset", asset, values)
		else:
			doc.update(values)
			doc.save(ignore_permissions=True)  # demo loader, while the instrument is in draft
	if approve_equipment is None:
		frappe.throw(_("QA approves {0} before a reading names it.").format(asset))
	approve_equipment(asset)
	return asset


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


# --- The instrument's calibration (module 38) -------------------------------------------------------

LABORATORY = "Demo Calibration Laboratory"


def draft_calibration(asset: str) -> Document:
	"""A calibration of the instrument by an external laboratory, in tolerance, drafted by the
	signed-in user (the Maintenance job) for their signature."""
	from meddevice_suite.qms import calibration

	if not frappe.db.exists("Supplier", LABORATORY):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": LABORATORY,
				"supplier_group": "Services"
				if frappe.db.exists("Supplier Group", "Services")
				else "All Supplier Groups",
			}
		).insert(ignore_permissions=True)  # demo loader
	certificate = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"calibration-certificate-{asset}.txt",
			"content": f"Calibration certificate (demo) for {asset}: all points within tolerance.",
			"is_private": 1,
		}
	).insert()
	return frappe.get_doc(
		{
			"doctype": "Calibration Record",
			"asset": asset,
			"performed_on": today(),
			"calibrated_by": calibration.EXTERNAL,
			"laboratory": LABORATORY,
			"certificate_number": f"DEMO-{asset}-{today()}",
			"certificate": certificate.file_url,
			"points": [
				{
					"parameter": "Length, gauge block 100 mm",
					"unit": "mm",
					"nominal": 100,
					"lower_limit": 99.98,
					"upper_limit": 100.02,
					"as_found": 100.01,
					"as_left": 100.0,
				}
			],
		}
	).insert()


def calibrate_instrument(asset: str, password: str, as_maintenance: Callable) -> None:
	"""The instrument calibrated by an external laboratory, completed with the Maintenance job's
	signature (module 38), unless it is fit already. `as_maintenance()` is a context in which the
	Maintenance job's user acts."""
	from meddevice_suite.qms import calibration
	from meddevice_suite.qms.equipment_care import unfit

	if not unfit([asset]).get(asset):
		return
	with as_maintenance():
		record = draft_calibration(asset)
		calibration.complete(record, password=password, reason="Calibrated by the laboratory (demo)")
