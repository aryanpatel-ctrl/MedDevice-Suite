"""What the browser test sheet of D4 Inventory & Batch Ledger starts from (modules 7, 16, 17, 18;
URS-QC-001 to URS-QC-003, URS-STK-002, URS-STK-004 to URS-STK-013, URS-MST-013, URS-PLT-017), on a
test site only (cypress/integration/sheet_d4.js).

The sheet's tests act on the screens as Stores, QC, QA, Production, Purchase, Management and the
System Administrator: goods are received into quarantine, certificates verified, lots inspected,
released, rejected and held by QA with signatures, customers' returns decided, expired lots and
first expiry, first out refused, a lot's dates corrected, and finished goods kept to production.
Each test starts from records of its own, made here afresh through the code's own services, each
step as the job that does it (`acting_as`), so a test never depends on another having run:

- a lot received by Stores on a goods receipt, with the supplier's lot number and certificate, and
  as far along as the test needs: verified by QC, inspected (accepted or rejected), released by QA;
- an approved purchase order, for the receipt the sheet makes from it;
- an approved lot of the finished good in the finished-goods store, delivered to the customer and
  partly sent back into the Returns store: a raw material is not a sales item, so goods a customer
  sends back are the product's, which comes into stock as opening stock on a material receipt
  (`finished_goods.as_opening_stock`), approved as the journey's stock already in store is
  (ui_journey.put_later_expiring_lots);
- two approved lots of a material whose dates QA corrected with signatures, for first expiry,
  first out, after the material's earlier stock is written off;
- a material of the sheet's own, approved with its specification, for the lot made expired: an
  expired lot can only be kept apart or sent back, so it is never one of the journey's materials,
  whose leftovers each run scraps (ui_journey.write_off_leftover_stock).

The journey's masters (ui_journey.prepare_journey) are made first: its raw materials, product,
supplier, customer, stores and calibrated instrument. Only the Administrator who runs the tests
prepares the sheet.
"""

from contextlib import AbstractContextManager

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
from frappe import _
from frappe.utils import add_days, flt, getdate, today

from meddevice_suite.lot_control import lot_dates, release, stores
from meddevice_suite.lot_control.finished_goods import opening_account
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	REJECTED,
	holdings_of,
	move_to_role,
	set_status,
	stock_by_lot,
)
from meddevice_suite.lot_control.receipt import verify_certificate
from meddevice_suite.master_data import supplier_approval
from meddevice_suite.master_data.specification import FINAL, INCOMING, specification_for
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	CUSTOMER,
	PRODUCT,
	PRODUCT_DETAILS,
	RAW_MATERIALS,
	SUPPLIER,
	acting_as,
	approve_as_qa,
	approve_specification_as_qa,
	ensure_item_document,
	post_stock_entry,
	store,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.setup.fixtures.inspection import (
	INSTRUMENT_NAME,
	SAMPLE_SIZE,
	ensure_inspection_template,
	readings_for,
)
from meddevice_suite.setup.fixtures.records import ensure_item, ensure_price, lot_of

QA, QC, STORES, PURCHASE, MANAGEMENT = "QA Manager", "QC Inspector", "Stores", "Purchase", "Management"
# The sheet's RM-SILICONE, and the material of its first expiry, first out test (D4-T13).
MATERIAL = "UIJ-RM-SILICONE"
FEFO_MATERIAL = "UIJ-RM-CONNECTOR"
# The material of the lot the sheet makes expired (D4-T12): the sheet's own, never written off.
EXPIRY_MATERIAL = "UIJ-D4-RM-POUCH"
EXPIRY_MATERIAL_DETAILS = (
	"Peel pouch, kept for the expiry test (browser test D4)",
	"Nos",
	"39232990",
	1825,
	1.8,
)
# The dates QA gives the two lots of D4-T13, in days from today: the newer lot expires first.
FEFO_EXPIRY_DAYS = {"older": 600, "newer": 200}
REASON = "Browser test sheet D4"
# How far along a received lot is prepared (`prepare_lot`).
STAGES = ("received", "verified", "rejected_inspection", "ready", "approved")


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


# --- The sheet's masters ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The sheet's own material and the supplier's approval for what the sheet receives, after the
	journey's masters (ui_journey.prepare_journey); and what the tests work with."""
	require_administrator()
	require_password(password)
	ensure_expiry_material(password)
	ensure_supplier_approved(password, [*RAW_MATERIALS, EXPIRY_MATERIAL])
	return {
		"company": COMPANY,
		"material": MATERIAL,
		"fefo_material": FEFO_MATERIAL,
		"expiry_material": EXPIRY_MATERIAL,
		"product": PRODUCT,
		"supplier": SUPPLIER,
		"customer": CUSTOMER,
		"shelf_life_days": RAW_MATERIALS[MATERIAL][3],
		"specification": specification_for(MATERIAL, INCOMING),
		"sample_size": SAMPLE_SIZE,
		"instrument": instrument(),
		"opening_account": opening_account(COMPANY),
		"stores": {
			"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
			"hold": stores.store_for(stores.HOLD, COMPANY),
			"rejected": stores.store_for(stores.REJECTED, COMPANY),
			"returns": stores.store_for(stores.RETURNS, COMPANY),
			"approved": store("Stores"),
			"work_in_progress": store("Work In Progress"),
			"finished_goods": store("Finished Goods"),
			"root": stores.root_store(COMPANY),
		},
	}


def ensure_expiry_material(password: str) -> None:
	"""The sheet's own raw material, made, specified and approved as the journey's are
	(ui_journey.ensure_masters): with its specification issued through document control and QA's
	signed approvals."""
	name, unit, hsn, shelf_life, rate = EXPIRY_MATERIAL_DETAILS
	ensure_item(
		EXPIRY_MATERIAL,
		name,
		"Raw Material",
		unit,
		hsn,
		shelf_life,
		COMPANY,
		store("Stores"),
		supplier=SUPPLIER,
	)
	ensure_price(EXPIRY_MATERIAL, "Standard Buying", rate)
	ensure_inspection_template(EXPIRY_MATERIAL, "Raw Material", approve_specification_as_qa(password))
	ensure_item_document(EXPIRY_MATERIAL, password)
	approve_as_qa(password)(EXPIRY_MATERIAL)


def ensure_supplier_approved(password: str, items: list[str]) -> None:
	"""QA's signed approval of the journey's supplier for `items`, unless the decision in force
	already approves it, without condition, for all of them on a certificate still valid: another
	sheet may have decided the supplier otherwise since."""
	current = supplier_approval.current_approval(SUPPLIER)
	if (
		current
		and current.decision == supplier_approval.APPROVED
		and set(items) <= current.item_codes
		and getdate(current.certificate_expiry) > getdate(today())
	):
		return
	with act_as(QA):
		certificate = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"ISO-13485-{frappe.generate_hash(length=10)}.txt",
				"content": f"ISO 13485 certificate (browser test D4)\nSupplier: {SUPPLIER}\n",
				"is_private": 1,
			}
		).insert()
		record = frappe.get_doc(
			{
				"doctype": "Supplier Approval",
				"supplier": SUPPLIER,
				"certificate_number": f"ISO13485-D4-{frappe.generate_hash(length=10).upper()}",
				"certificate_expiry": add_days(today(), 365),
				"certificate": certificate.file_url,
				"items": [{"item_code": item} for item in sorted(set(items))],
			}
		).insert()
		supplier_approval.approve(record, password=password, reason=f"{REASON}: supplier audited")


def instrument() -> str:
	return frappe.db.get_value("Asset", {"asset_name": INSTRUMENT_NAME, "company": COMPANY})


# --- Lots through the jobs ----------------------------------------------------------------------


def received(item: str, qty: float) -> dict:
	"""A goods receipt by Stores into quarantine, with the supplier's lot number and certificate on
	its line, submitted; ERPNext makes the lot."""
	supplier_lot = f"SUP-{frappe.generate_hash(length=10).upper()}"
	with act_as(STORES):
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": SUPPLIER,
				"company": COMPANY,
				"items": [
					{
						"item_code": item,
						"qty": qty,
						"rate": RAW_MATERIALS.get(item, EXPIRY_MATERIAL_DETAILS)[4],
						"warehouse": stores.store_for(stores.QUARANTINE, COMPANY),
					}
				],
			}
		).insert()
		row = receipt.items[0]
		row.mds_supplier_lot_no = supplier_lot
		row.mds_certificate = (
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"COA-{item}-{receipt.name}.txt",
					"content": f"Certificate of analysis (browser test D4)\nItem: {item}\nResult: conforms\n",
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
	receipt = frappe.get_doc("Purchase Receipt", receipt.name)
	return {
		"item": item,
		"qty": qty,
		"receipt": receipt.name,
		"row": receipt.items[0].name,
		"lot": lot_of(receipt.items[0]),
		"supplier_lot": supplier_lot,
	}


def verified(lot: dict) -> None:
	with act_as(QC):
		verify_certificate(lot["receipt"], lot["row"])


def inspected(
	lot: dict, accept: bool = True, inspection_type: str = "Incoming", reference: tuple | None = None
) -> str:
	"""QC's submitted inspection of the lot against the item's specification for the stage, on the
	document that brought the lot in: accepted, or rejected on its measurement (12.00 mm, the sheet's
	reading out of limits)."""
	stage = INCOMING if inspection_type == "Incoming" else FINAL
	readings = readings_for(specification_for(lot["item"], stage), instrument())
	if not accept:
		for reading in readings:
			if reading.get("numeric"):
				reading["reading_1"] = "12.00"
	reference_type, reference_name = reference or ("Purchase Receipt", lot["receipt"])
	with act_as(QC):
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": inspection_type,
				"reference_type": reference_type,
				"reference_name": reference_name,
				"item_code": lot["item"],
				"batch_no": lot["lot"],
				"sample_size": SAMPLE_SIZE,
				"inspected_by": frappe.session.user,
				"readings": readings,
			}
		).insert()
		inspection.submit()
		return inspection.name


def decided(lot: str, decision: str, reason: str, password: str) -> frappe._dict:
	"""QA's signed decision on the lot (Release, Reject, Hold, Return to Stock, Reject Return)."""
	acts = {
		release.RELEASE: release.release,
		release.REJECT: release.reject,
		release.HOLD_DECISION: release.hold,
		release.RETURN_TO_STOCK: release.return_to_stock,
		release.REJECT_RETURN: release.reject_return,
	}
	with act_as(QA):
		record = frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
		acts[decision](record, password=password, reason=reason)
	return frappe._dict(
		frappe.db.get_value("Lot Release", record.name, ["name", "stock_entry"], as_dict=True)
	)


def lot_up_to(stage: str, password: str, item: str, qty: float) -> dict:
	if stage not in STAGES:
		frappe.throw(_("Unknown stage {0}.").format(stage))
	lot = received(item, qty)
	if stage == "received":
		return lot
	verified(lot)
	if stage == "verified":
		return lot
	lot["inspection"] = inspected(lot, accept=stage != "rejected_inspection")
	if stage in ("rejected_inspection", "ready"):
		return lot
	decision = decided(lot["lot"], release.RELEASE, f"{REASON}: incoming inspection accepted", password)
	lot.update(release=decision.name, transfer=decision.stock_entry)
	return lot


@frappe.whitelist(methods=["POST"])
def prepare_lot(password: str, stage: str, item: str | None = None, qty: float = 10) -> dict:
	"""A new lot of a raw material received on a goods receipt and taken as far as `stage`: received
	(its certificate not yet verified), verified, rejected_inspection (verified, its incoming
	inspection rejected), ready (inspection accepted, not yet decided) or approved (released by QA
	to the approved store). Returns {item, qty, receipt, row, lot, supplier_lot, inspection?,
	release?, transfer?}."""
	require_administrator()
	item = item or MATERIAL
	if item not in (*RAW_MATERIALS, EXPIRY_MATERIAL):
		frappe.throw(_("{0} is not one of the sheet's materials.").format(item))
	return lot_up_to(stage, require_password(password), item, flt(qty))


@frappe.whitelist(methods=["POST"])
def prepare_purchase_order(password: str, qty: float = 10) -> dict:
	"""A purchase order of the sheet's material from the journey's supplier, raised by Purchase and
	approved by Management, for Stores to receive from on the screen (D4-T01)."""
	require_administrator()
	require_password(password)
	from frappe.model.workflow import apply_workflow

	with act_as(PURCHASE):
		order = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": SUPPLIER,
				"company": COMPANY,
				"schedule_date": add_days(today(), 7),
				"set_warehouse": stores.store_for(stores.QUARANTINE, COMPANY),
				"items": [
					{
						"item_code": MATERIAL,
						"qty": flt(qty),
						"schedule_date": add_days(today(), 7),
						"warehouse": stores.store_for(stores.QUARANTINE, COMPANY),
					}
				],
			}
		).insert()
		apply_workflow(order, "Send for Approval")
	with act_as(MANAGEMENT):
		apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")
	return {"order": order.name, "item": MATERIAL, "qty": flt(qty)}


# --- Finished goods, delivered and sent back -----------------------------------------------------


def approved_product_lot(qty: float) -> dict:
	"""An approved lot of the finished good in the finished-goods store, come in as opening stock on
	a material receipt, as the journey's stock already in store comes in."""
	shelf_life = PRODUCT_DETAILS[3]
	lot = (
		frappe.get_doc(
			{
				"doctype": "Batch",
				"item": PRODUCT,
				"manufacturing_date": today(),
				"expiry_date": add_days(today(), shelf_life),
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)
	set_status(lot, APPROVED, f"{REASON}: approved finished goods already in store")
	entry = post_stock_entry(
		"Material Receipt",
		[
			{
				"item_code": PRODUCT,
				"t_warehouse": store("Finished Goods"),
				"qty": qty,
				"basic_rate": PRODUCT_DETAILS[2],
				"batch_no": lot,
				"use_serial_batch_fields": 1,
			}
		],
		opening=True,
	)
	return {"item": PRODUCT, "qty": qty, "lot": lot, "entry": entry.name}


def delivery_of(lot: str, qty: float) -> str:
	with act_as(STORES):
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": CUSTOMER,
				"company": COMPANY,
				"items": [
					{
						"item_code": PRODUCT,
						"qty": qty,
						"rate": PRODUCT_DETAILS[2],
						"warehouse": store("Finished Goods"),
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		).insert()
		note.submit()
		return note.name


def sales_return_of(delivery: str, lot: str, qty: float) -> str:
	"""The customer's return of `qty` of the delivered lot, made by Stores from the delivery note as
	ERPNext makes it, into the store ERPNext takes: the company's store for sales returns."""
	with act_as(STORES):
		back = make_sales_return(delivery)
		for row in back.items:
			row.update(
				{
					"qty": -flt(qty),
					"stock_qty": -flt(qty) * flt(row.conversion_factor or 1),
					"batch_no": lot,
					"use_serial_batch_fields": 1,
					"serial_and_batch_bundle": None,
				}
			)
		back.insert()
		back.submit()
		return back.name


@frappe.whitelist(methods=["POST"])
def prepare_delivery(qty: float = 10, delivered: float = 3, returned: float = 0) -> dict:
	"""An approved lot of the finished good in the finished-goods store, `delivered` of it delivered
	to the customer by Stores and `returned` of that sent back into the Returns store. Returns
	{item, qty, lot, entry, delivery, sales_return?}."""
	require_administrator()
	lot = approved_product_lot(flt(qty))
	lot["delivery"] = delivery_of(lot["lot"], flt(delivered))
	if flt(returned):
		lot["sales_return"] = sales_return_of(lot["delivery"], lot["lot"], flt(returned))
	return lot


@frappe.whitelist(methods=["POST"])
def customer_returns(delivery: str, qty: float) -> str:
	"""Stores takes `qty` more of the delivered lot back from the customer (D4-T10)."""
	require_administrator()
	note = frappe.get_doc("Delivery Note", delivery)
	lot = note.items[0].batch_no or lot_of(note.items[0])
	return sales_return_of(delivery, lot, flt(qty))


@frappe.whitelist(methods=["POST"])
def inspect_returned(lot: str, entry: str) -> str:
	"""QC's accepted final inspection of a finished lot whose goods came back, made on the material
	receipt that brought the lot in (D4-T10): the product has a final specification, not an incoming
	one, and its lot came in as opening stock."""
	require_administrator()
	item = frappe.db.get_value("Batch", lot, "item")
	return inspected({"item": item, "lot": lot}, inspection_type="Outgoing", reference=("Stock Entry", entry))


@frappe.whitelist(methods=["POST"])
def prepare_quarantined_product(qty: float = 10) -> dict:
	"""A new lot of the finished good in quarantine, come in as opening stock on a material receipt
	(D4-T11): a lot that is not approved, which the sheet tries to move, issue, deliver and pick."""
	require_administrator()
	entry = post_stock_entry(
		"Material Receipt",
		[
			{
				"item_code": PRODUCT,
				"t_warehouse": stores.store_for(stores.QUARANTINE, COMPANY),
				"qty": flt(qty),
				"basic_rate": PRODUCT_DETAILS[2],
			}
		],
		opening=True,
	)
	return {
		"item": PRODUCT,
		"qty": flt(qty),
		"lot": lot_of(frappe.get_doc("Stock Entry", entry.name).items[0]),
	}


@frappe.whitelist(methods=["POST"])
def prepare_new_product_lot() -> dict:
	"""A new lot of the finished good with no stock yet (D4-T25), for a stock count to bring in."""
	require_administrator()
	lot = (
		frappe.get_doc({"doctype": "Batch", "item": PRODUCT})
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked above
		.name
	)
	return {"item": PRODUCT, "lot": lot}


# --- First expiry, first out --------------------------------------------------------------------


def write_off(item: str) -> None:
	"""Scrap whatever stock of `item` is in any store, as ui_journey.write_off_leftover_stock does for
	the journey's items: each lot is rejected and its stock moved to the Rejected store first, as
	QA's rejection moves it, because stock is scrapped only from there (module 17)."""
	reason = f"{REASON}: earlier stock of the material, scrapped"
	for lot in {row.batch_no for row in stock_by_lot(item)}:
		set_status(lot, REJECTED, reason)
		move_to_role(frappe.get_doc("Batch", lot), stores.REJECTED, reason, holdings_of(lot))
	leftovers = stock_by_lot(item)
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


@frappe.whitelist(methods=["POST"])
def prepare_fefo(password: str, qty: float = 10) -> dict:
	"""Two approved lots of the first-expiry material in the approved store, and nothing else of it
	(D4-T13): received, inspected and released through the jobs, then their expiry dates corrected by
	QA with signatures, the lots having stock: the older lot expires after the newer one. Returns
	{item, older, newer, older_expiry, newer_expiry}."""
	require_administrator()
	require_password(password)
	write_off(FEFO_MATERIAL)
	lots = {}
	for which, days in FEFO_EXPIRY_DAYS.items():
		lot = lot_up_to("approved", password, FEFO_MATERIAL, flt(qty))["lot"]
		with act_as(QA):
			lot_dates.correct_dates(
				frappe.get_doc("Batch", lot),
				password=password,
				reason=f"{REASON}: expiry date checked against the certificate",
				expiry_date=add_days(today(), days),
			)
		lots[which] = lot
	return {
		"item": FEFO_MATERIAL,
		"older": lots["older"],
		"newer": lots["newer"],
		**{
			f"{which}_expiry": str(frappe.db.get_value("Batch", lot, "expiry_date"))
			for which, lot in lots.items()
		},
	}


@frappe.whitelist(methods=["POST"])
def unique_tag() -> str:
	"""A tag that names the records a test makes on the screen apart from any earlier run's."""
	require_administrator()
	return frappe.generate_hash(length=10)
