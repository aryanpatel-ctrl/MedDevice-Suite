"""What the browser test sheet of D3 Item / BOM / Routing Master starts from (URS-MST-001 to
URS-MST-016), on a test site only (cypress/integration/sheet_d3.js).

The sheet's tests act on the screens as QA, Production, Purchase, QC and Management: QA sets up items
and their specifications and approves them with signatures, Production drafts bills of materials,
workstations, operations and routings that QA approves, and the records that would use a draft
master are refused. The test site has none of the pilot's demo data, so this module makes, afresh
each run and named with the run's own suffix, what the sheet's pilot records stand for:

- the finished good FC-2W-SIL-14 and the raw materials RM-SILICONE, RM-CONNECTOR, RM-POUCH,
  RM-CARTON and RM-LATEX, each with its specification and its specification document, approved by
  QA with signatures, and the product's default bill of materials, approved by QA;
- a receipt of RM-SILICONE, and of a material inspected against a test method made only for the
  run, received by Stores and its certificates verified by QC, for the inspections the sheet makes;
- the drafts the sheet completes or approves: a raw material whose specification still lacks its
  test method, one whose specification QA has not approved, a sub-assembly;
- the controlled documents the masters name, issued through document control by the jobs with
  signatures: a test method (SOP), a work instruction, a specification, a form, and a specification
  still in draft;
- the measuring instrument a reading names, approved and calibrated.

D3-T26 and D3-T27 start from records of their own, so that they run whatever the rows before them
did: a finished good QA approved with a signature, its bill of materials QA approved, and its shelf
life changed by QA under an approved change request, for QA to read in the audit trail (21 CFR
11.10(e)); and a finished good with its approved specification and approved bill of materials, and
a second approved finished good with its own, which nobody merges, deletes or cancels (ISO 13485
4.2.4, 4.2.5).

Only the Administrator who runs the tests prepares the sheet. The approved change requests the
sheet names are made when its tests reach them (`approve_change`), raised by the Production
Supervisor and assessed and approved by QA with signatures.
"""

import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_fullname, today

from meddevice_suite.compliance.signing import signatures_of
from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.receipt import verify_certificate
from meddevice_suite.master_data import item_master, routing, specification, supplier_approval
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	acting_as,
	approve_as_qa,
	approve_specification_as_qa,
	approved_change_request,
	store,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.setup.fixtures.documents import ensure_work_instruction
from meddevice_suite.setup.fixtures.inspection import (
	SAMPLE_SIZE,
	calibrate_instrument,
	ensure_inspection_masters,
	ensure_inspection_template,
	parameter_rows,
)
from meddevice_suite.setup.fixtures.records import demo_gtin

QA, PRODUCTION, QC, STORES = "QA Manager", "Production Supervisor", "QC Inspector", "Stores"
STORAGE_CONDITION = "Store below 30 °C in a dry place, away from direct sunlight (browser test)"
TEST_METHOD_TITLE = "Incoming inspection test method (demo)"  # the pilot's, as the sheet names it
DRAFT_SPECIFICATION_TITLE = "Specification still in draft (browser test D3)"
# The records whose approved change request the sheet asks for (D3-T06, T18, T19, T22, T25).
CHANGED_TYPES = ("Item", "Operation", specification.DOCTYPE)
# The bill of materials of FC-2W-SIL-14 the sheet starts from, per batch of 100 pieces.
COMPONENTS = {"silicone": 1.2, "connector": 100, "pouch": 100, "carton": 1}
# Each material in its own unit: silicone compound is weighed, so its bill quantity of 1.2 is in Kg
# (a unit the test site allows fractions of; ui_journey's silicone is kept in it too); the rest are
# counted in whole Nos.
UNITS = {"silicone": "Kg"}
COUNTED = "Nos"
# The shelf life of the sheet's finished goods, in days, and what QA changes it to under an approved
# change request in D3-T26's precondition.
SHELF_LIFE, CHANGED_SHELF_LIFE = 1825, 1460


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The masters, documents and receipts the sheet starts from, made with the run's password of
	the job users (ui_users.prepare_job_users), and the names it works with."""
	require_administrator()
	if not password:
		frappe.throw(_("Give the run's password of the job users to prepare the sheet."))
	suffix = frappe.generate_hash(length=10).upper()
	approve_item, approve_bom = approve_as_qa(password), approve_as_qa(password, "BOM")
	approve_spec = approve_specification_as_qa(password)

	# The parameters the specifications read, and the instrument a measurement names (module 8),
	# calibrated (module 38).
	instrument = ensure_inspection_masters(COMPANY, approve_item, approve_as_qa(password, "Asset"))
	calibrate_instrument(instrument, password, lambda: act_as("Maintenance"))

	documents = {
		"test_method": ensure_work_instruction(TEST_METHOD_TITLE, act_as, email_for, password, "SOP"),
		"work_instruction": ensure_work_instruction(
			"Work instruction: D3 sheet (browser test)", act_as, email_for, password
		),
		"specification_document": ensure_work_instruction(
			"Specification (browser test)", act_as, email_for, password, "Specification"
		),
		"form_document": ensure_work_instruction(
			"Inspection record form: D3 sheet (browser test)", act_as, email_for, password, "Form"
		),
		"draft_specification_document": draft_specification_document(),
	}

	# FC-2W-SIL-14 and its raw materials, approved by QA with their specifications (D3-T03, T08 to
	# T13, T17, T19, T22).
	materials = {
		key: approved_material(
			f"RM-{key.upper()}-{suffix}",
			documents["specification_document"],
			approve_spec,
			UNITS.get(key, COUNTED),
		)
		for key in (*COMPONENTS, "latex")
	}
	for code in materials.values():
		approve_item(code)
	approved_product = finished_goods(documents["specification_document"], approve_item, approve_spec)
	product = approved_product(f"FC-2W-SIL-14-{suffix}")
	default_bom = approved_bom(product, materials, approve_bom)
	# D3-T13: a second approved bill of the product, not the default, so that the row runs on its
	# own (the sheet names D3-T10's).
	second_bom = new_bom(product, materials, is_default=0)
	approve_bom(second_bom)
	# D3-T17: a workstation recorded by Production and approved by QA, so that the row runs on its
	# own (the sheet names D3-T14's).
	approved_bench = approved_workstation(f"TEST-D3 Bench2 {suffix}", password)
	# D3-T02 and T03: a draft raw material (the sheet names D3-T01's), so that they run on their own.
	draft_item = new_material(f"TEST-D3-RMD-{suffix}")

	# D3-T25: a raw material inspected against a test method made only for this run, which QA
	# withdraws on the screen once the approved specification no longer names it.
	withdrawn_method = ensure_work_instruction(
		f"Test method withdrawn in D3-T25 {suffix} (browser test)", act_as, email_for, password, "SOP"
	)
	t25_item = new_material(f"TEST-D3-RM25-{suffix}", documents["specification_document"])
	t25_specification = new_specification(f"{t25_item} IQC", t25_item, withdrawn_method)
	set_item_specification(t25_item, t25_specification)
	approve_spec(t25_specification)
	approve_item(t25_item)

	receipt, lots = received([materials["silicone"], t25_item], suffix, password)

	# D3-T21 and T23: complete, but its specification has no test method yet and it names no
	# specification document.
	item_b = new_material(f"TEST-D3-RMB-{suffix}")
	specification_b = new_specification(f"{item_b} IQC", item_b)
	# D3-T24: complete, with its specification document, but QA has not approved its specification.
	item_rm2 = new_material(f"TEST-D3-RM2-{suffix}", documents["specification_document"])
	specification_rm2 = new_specification(f"{item_rm2} IQC", item_rm2, documents["test_method"])
	# D3-T23 step 5: a sub-assembly, lot-tracked with its shelf life and storage condition.
	sub_assembly = new_item(
		f"TEST-D3-SA-{suffix}", "Catheter shaft, extruded (browser test)", item_master.SUB_ASSEMBLY
	)

	# D3-T26 and D3-T27: records of their own.
	audited = audited_product(approved_product(f"TEST-D3-FG26-{suffix}"), materials, approve_bom, password)
	kept_item = approved_product(f"TEST-D3-FG27-{suffix}")
	duplicate = approved_product(f"TEST-D3-FG27B-{suffix}")
	kept = {
		"item": kept_item,
		"bom": approved_bom(kept_item, materials, approve_bom),
		"specification": frappe.db.get_value("Item", kept_item, "quality_inspection_template"),
		"duplicate": duplicate,
		"duplicate_bom": approved_bom(duplicate, materials, approve_bom),
		"duplicate_specification": frappe.db.get_value("Item", duplicate, "quality_inspection_template"),
	}

	valid_gtin, invalid_gtin = new_gtin_pair()
	return {
		"suffix": suffix,
		"company": COMPANY,
		"storage_condition": STORAGE_CONDITION,
		"stores": {
			"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
			"work_in_progress": store("Work In Progress"),
		},
		"instrument": instrument,
		**documents,
		# Made on the screens by the sheet's tests.
		"item": f"TEST-D3-RM-{suffix}",
		"specification": f"TEST-D3-RM-{suffix} IQC",
		"second_specification": f"TEST-D3-RM-{suffix} IQC 2",
		"finished_good": f"TEST-D3-FG-{suffix}",
		"second_finished_good": f"TEST-D3-FG2-{suffix}",
		"gtin": valid_gtin,
		"invalid_gtin": invalid_gtin,
		"bench": f"TEST-D3 Bench {suffix}",
		"operation": f"TEST-D3 Op {suffix}",
		"second_operation": f"TEST-D3 Op2 {suffix}",
		"routing": f"TEST-D3 Route {suffix}",
		"second_routing": f"TEST-D3 Route2 {suffix}",
		# Made here.
		"product": product,
		"materials": materials,
		"components": {materials[key]: qty for key, qty in COMPONENTS.items()},
		"silicone_specification": frappe.db.get_value(
			"Item", materials["silicone"], "quality_inspection_template"
		),
		"default_bom": default_bom,
		"second_bom": second_bom,
		"approved_bench": approved_bench,
		"draft_item": draft_item,
		"receipt": receipt,
		"silicone_lot": lots[materials["silicone"]],
		"item_b": item_b,
		"specification_b": specification_b,
		"item_rm2": item_rm2,
		"specification_rm2": specification_rm2,
		"sub_assembly": sub_assembly,
		"t25": {
			"test_method": withdrawn_method,
			"item": t25_item,
			"specification": t25_specification,
			"lot": lots[t25_item],
		},
		"audited": audited,
		"kept": kept,
		"full_names": {job: get_fullname(email_for(job)) for job in (QA, PRODUCTION)},
	}


@frappe.whitelist(methods=["POST"])
def approve_change(record_type: str, record: str, password: str) -> str:
	"""An approved change request listing the record, for the sheet's change under change control:
	raised by the Production Supervisor, assessed and approved by QA with signatures (module 36)."""
	require_administrator()
	if record_type not in CHANGED_TYPES:
		frappe.throw(_("The sheet changes no {0} under change control.").format(_(record_type)))
	return approved_change_request(record_type, record, password)


@frappe.whitelist(methods=["POST"])
def return_specification_to_draft(name: str, password: str) -> dict:
	"""D3-T22's precondition: the specification in use changed by QA under an approved change
	request, which returns it to draft until QA approves it again (D3-T19 steps 1 to 3, done again:
	the sheet's D3-T19 approves it once more in its step 4). Its numeric limit is widened by 0.1."""
	require_administrator()
	change = approved_change_request(specification.DOCTYPE, name, password)
	with act_as(QA):
		doc = frappe.get_doc(specification.DOCTYPE, name)
		row = next(row for row in doc.get(specification.PARAMETERS) if row.numeric)
		row.max_value = flt(row.max_value) + 0.1
		doc.set("mds_change_request", change)
		doc.save()
	return {"change_request": change, "max_value": row.max_value}


# --- Masters ------------------------------------------------------------------------------------


def new_item(
	code: str,
	name: str,
	item_type: str,
	group: str = "Raw Material",
	hsn: str = "39269099",
	shelf_life: int = 730,
	unit: str = COUNTED,
	**values,
) -> str:
	"""A draft item of the type, lot-tracked with its shelf life and storage condition, set up as QA
	sets one up; `values` adds what its type needs."""
	return (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": f"{code} (browser test)",
				"description": name,
				"item_group": group,
				"stock_uom": unit,
				"gst_hsn_code": hsn,  # India Compliance requires it on every item
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": f"{code}-.####",
				"has_expiry_date": 1,
				"shelf_life_in_days": shelf_life,
				item_master.ITEM_TYPE: item_type,
				item_master.STORAGE_CONDITION: STORAGE_CONDITION,
				**values,
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)


def finished_goods(
	specification_document: str, approve_item: Callable[[str], None], approve_spec: Callable[[str], None]
) -> Callable[[str], str]:
	"""Makes a finished good with its code: sterile, of device class B, in boxes of 10 and cartons of
	20, with its GTIN, the specification document that defines it and its final specification, QA
	approving the specification and the item with signatures."""

	def approved_product(code: str) -> str:
		product = new_item(
			code,
			"Foley catheter, 2-way, silicone, 14 Fr (browser test)",
			item_master.FINISHED_GOOD,
			group="Products",
			hsn="90183910",
			shelf_life=SHELF_LIFE,
			**{
				item_master.STERILE: "Yes",
				item_master.DEVICE_CLASS: "B",
				item_master.UNITS_PER_BOX: 10,
				item_master.BOXES_PER_CARTON: 20,
				item_master.UDI_DI: demo_gtin(code),
				item_master.SPECIFICATION_DOCUMENT: specification_document,
			},
		)
		ensure_inspection_template(product, "Products", approve_spec)  # its final specification
		approve_item(product)
		return product

	return approved_product


def approved_bom(product: str, materials: dict[str, str], approve_bom: Callable[[str], None]) -> str:
	"""The product's default bill of materials, drafted as Production drafts one and approved by QA
	with a signature."""
	bom = new_bom(product, materials, is_default=1)
	approve_bom(bom)
	return bom


def audited_product(
	product: str, materials: dict[str, str], approve_bom: Callable[[str], None], password: str
) -> dict:
	"""D3-T26: the approved product with its bill of materials approved by QA, and its shelf life
	changed by QA under an approved change request that lists it, raised by Production."""
	bom = approved_bom(product, materials, approve_bom)
	change = approved_change_request("Item", product, password)
	with act_as(QA):
		doc = frappe.get_doc("Item", product)
		doc.shelf_life_in_days = CHANGED_SHELF_LIFE
		doc.set(CHANGE_REQUEST, change)
		doc.save()
	return {
		"item": product,
		"bom": bom,
		"change_request": change,
		"change_reason": frappe.db.get_value("Change Request", change, "reason"),
		"shelf_life": {"before": SHELF_LIFE, "after": CHANGED_SHELF_LIFE},
		"item_approval_reason": approval_reason("Item", product),
		"bom_approval_reason": approval_reason("BOM", bom),
	}


def approval_reason(doctype: str, name: str) -> str:
	"""Why QA approved the record, as its one Approve signature records it."""
	[reason] = [row.reason for row in signatures_of(doctype, name) if row.action == "Approve"]
	return reason


def new_bom(product: str, materials: dict[str, str], is_default: int) -> str:
	"""A draft bill of materials of the product, per batch of 100 pieces, as Production drafts it."""
	return (
		frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": COMPANY,
				"quantity": 100,
				"is_active": 1,
				"is_default": is_default,
				"rm_cost_as_per": "Valuation Rate",
				"items": [
					{"item_code": materials[key], "qty": qty, "rate": 1} for key, qty in COMPONENTS.items()
				],
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)


def approved_workstation(name: str, password: str) -> str:
	"""A workstation recorded by the Production Supervisor and approved by QA with a signature."""
	with act_as(PRODUCTION):
		frappe.get_doc(
			{"doctype": "Workstation", "workstation_name": name, "production_capacity": 1}
		).insert()
	with act_as(QA):
		routing.approve(
			frappe.get_doc("Workstation", name), password=password, reason="Bench checked (browser test)"
		)
	return name


def new_material(code: str, specification_document: str | None = None, unit: str = COUNTED) -> str:
	"""A draft raw material in its unit, inspected when it arrives, naming its specification
	document if given."""
	return new_item(
		code,
		"Raw material (browser test)",
		item_master.RAW_MATERIAL,
		unit=unit,
		is_purchase_item=1,
		inspection_required_before_purchase=1,
		**{item_master.SPECIFICATION_DOCUMENT: specification_document},
	)


def approved_material(code: str, specification_document: str, approve_spec, unit: str = COUNTED) -> str:
	"""A raw material in its unit with its incoming specification approved by QA (named `<code> IQC
	(Demo)`, as the pilot names them), ready for QA's approval of the item."""
	new_material(code, specification_document, unit)
	ensure_inspection_template(code, "Raw Material", approve_spec)
	return code


def new_specification(name: str, item: str, test_method: str | None = None) -> str:
	"""A complete incoming specification of the item, in draft, naming its test method if given."""
	return (
		frappe.get_doc(
			{
				"doctype": specification.DOCTYPE,
				"quality_inspection_template_name": name,
				"mds_item": item,
				"mds_stage": specification.INCOMING,
				"mds_sample_size": SAMPLE_SIZE,
				specification.PARAMETERS: parameter_rows("Raw Material"),
				specification.TEST_METHOD: test_method,
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)


def set_item_specification(item: str, name: str) -> None:
	"""The specification the inspection form lays its readings out from (ERPNext's item field)."""
	doc = frappe.get_doc("Item", item)
	doc.quality_inspection_template = name
	doc.save(ignore_permissions=True)  # test-site set-up by the Administrator, while the item is in draft


def new_gtin_pair() -> tuple[str, str]:
	"""A GTIN-13 no item carries yet, in GS1's prefix 20 for restricted circulation (never another
	company's product), and the same digits with a wrong check digit."""
	while True:
		body = "20" + "".join(secrets.choice("0123456789") for _ in range(10))
		total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
		check = (10 - total % 10) % 10
		valid = f"{body}{check}"
		if not frappe.db.exists("Item", {item_master.GTIN_KEY: item_master.gtin_key(valid)}):
			return valid, f"{body}{(check + 1) % 10}"


# --- Documents and receipts ---------------------------------------------------------------------


def draft_specification_document() -> str:
	"""A Specification registered by QA with no revision in effect yet (D3-T23 step 3)."""
	existing = frappe.db.get_value(
		"Controlled Document",
		{"title": DRAFT_SPECIFICATION_TITLE, "document_type": "Specification", "status": "Draft"},
	)
	if existing:
		return existing
	with act_as(QA):
		return (
			frappe.get_doc(
				{
					"doctype": "Controlled Document",
					"title": DRAFT_SPECIFICATION_TITLE,
					"document_type": "Specification",
					"department": "PRD",
					"process_owner": email_for(PRODUCTION),
				}
			)
			.insert()
			.name
		)


def received(items: list[str], suffix: str, password: str) -> tuple[str, dict[str, str]]:
	"""A receipt of the items into quarantine from a supplier QA approved for them, each line with
	the supplier's lot number and certificate, the certificates verified by QC; the receipt and the
	lot of each item."""
	from meddevice_suite.setup.fixtures.records import lot_of

	supplier = (
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"D3 Supplier {suffix}",
				"supplier_group": "Raw Material",
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)
	with act_as(QA):
		certificate = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"ISO-13485-D3-{suffix}.txt",
				"content": f"ISO 13485 certificate (browser test)\nSupplier: {supplier}\n",
				"is_private": 1,
			}
		).insert()
		approval = frappe.get_doc(
			{
				"doctype": supplier_approval.DOCTYPE,
				"supplier": supplier,
				"certificate_number": f"QMS-D3-{suffix}",
				"certificate_expiry": add_days(today(), 365),
				"certificate": certificate.file_url,
				"items": [{"item_code": item} for item in items],
			}
		).insert()
		supplier_approval.approve(approval, password=password, reason="Supplier audited (browser test)")
	with act_as(STORES):
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": supplier,
				"company": COMPANY,
				"items": [
					{
						"item_code": item,
						"qty": 10,
						"rate": 10,
						"warehouse": stores.store_for(stores.QUARANTINE, COMPANY),
					}
					for item in items
				],
			}
		).insert()
		for row in receipt.items:
			row.mds_supplier_lot_no = f"SUP-{row.item_code}"
			row.mds_certificate = (
				frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"COA-{row.item_code}.txt",
						"content": f"Certificate of analysis (browser test)\nItem: {row.item_code}\n",
						"is_private": 1,
						"attached_to_doctype": "Purchase Receipt",
						"attached_to_name": receipt.name,
					}
				)
				.insert()
				.file_url
			)
		receipt.save()
		receipt.submit()
	with act_as(QC):
		for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
			verify_certificate(receipt.name, row.name)
	stored = frappe.get_doc("Purchase Receipt", receipt.name)
	return receipt.name, {row.item_code: lot_of(row) for row in stored.items}
