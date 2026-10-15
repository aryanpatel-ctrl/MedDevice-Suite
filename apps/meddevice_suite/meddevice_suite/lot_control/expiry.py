"""Expired lots and first expiry, first out (module 18; URS-STK-008, URS-STK-009; D3 "Putaway in
Finished Goods Warehouse (FIFO / FEFO)"; ISO 13485 7.5.11 preservation of product).

Checked on the stock ledger entry, the one place every movement passes, after the store and lot
status checks (master_data/warehouse.py, lot_status.py), so the form, the API, a pick list's
delivery and a production entry are held to the same rules:

- An expired lot (its expiry date before the posting date, as ERPNext counts it) leaves its store
  only to be kept apart: into the hold, rejected or returns store, by QA's own decision, back to its
  supplier, or as counted. It is never issued, sent to production, consumed or delivered.
  ERPNext refuses an expired lot on a delivery and on some production entries; a material issue
  naming it by hand, or a move to another usable store, went through.
- When stock goes to production or to a customer, the lot that expires first goes first: a lot is
  refused while an earlier-expiring Approved lot of the same item, not yet expired, would still be
  left in the same store after the document. A document may depart from that only by saying why
  (FEFO Exception Reason, for example a customer asking for one lot); the reason stays on the
  document and in its audit trail, and the Lots Taken Out of Expiry Order report lists every
  exception for QA. A move from one store to another is not a use of the stock and is not held to
  it.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import flt, getdate

from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	TRANSFER_FIELD,
	lots_of_bundle,
	qty_precision,
	status_of,
)

REASON_FIELD = "mds_fefo_reason"
# Stock entries that take stock for use: issued, sent to production or consumed in it.
USE_PURPOSES = frozenset(
	{
		"Material Issue",
		"Material Transfer for Manufacture",
		"Manufacture",
		"Material Consumption for Manufacture",
		"Repack",
		"Send to Subcontractor",
	}
)
# Documents that deliver stock to a customer (when not a return).
DELIVERIES = frozenset({"Delivery Note", "Sales Invoice", "POS Invoice"})
# Where an expired lot may still go: kept apart from usable stock.
KEPT_APART = frozenset({stores.HOLD, stores.REJECTED, stores.RETURNS})

REASON = {
	"fieldname": REASON_FIELD,
	"label": "FEFO Exception Reason",
	"fieldtype": "Small Text",
	"no_copy": 1,
	"description": (
		"Only when a lot is taken while one that expires earlier is still in the store: why "
		"(for example, the customer asked for this lot). Kept in the audit trail and reported to QA."
	),
}
CUSTOM_FIELDS = {
	"Stock Entry": [{**REASON, "insert_after": "remarks"}],
	"Delivery Note": [{**REASON, "insert_after": "instructions"}],
	"Sales Invoice": [{**REASON, "insert_after": "remarks"}],
	"POS Invoice": [{**REASON, "insert_after": "remarks"}],
}


def apply_expiry_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: what leaves a store is not expired and goes first by expiry."""
	if sle.is_cancelled or flt(sle.actual_qty, qty_precision()) >= 0:
		return
	lots = [sle.batch_no] if sle.batch_no else lots_of_bundle(sle.serial_and_batch_bundle)
	if not lots:
		return
	movement = describe(sle)
	expiry = dict(
		frappe.get_all("Batch", filters={"name": ["in", lots]}, fields=["name", "expiry_date"], as_list=True)
	)
	for lot in lots:
		if is_expired(expiry.get(lot), sle.posting_date) and not movement.keeps_apart:
			frappe.throw(
				_(
					"Lot {0} expired on {1}; it cannot be issued, used or delivered. Move it to the hold "
					"store; QA decides it."
				).format(lot, frappe.format_value(expiry.get(lot), {"fieldtype": "Date"})),
				title=_("Lot expired"),
			)
	if movement.uses and not movement.reason:
		# Read once for the entry, not once per lot: an item made every day has hundreds of lots
		# that expire before the newest, and a query per lot or per such lot grew with the years.
		left = lots_left(sle)
		for lot in lots:
			earlier = earlier_lot_left(lot, expiry.get(lot), left)
			if earlier:
				frappe.throw(
					_(
						"Lot {0} expires on {1}, before lot {2}, and would still be in {3}; the lot that "
						"expires first goes first. Take lot {0}, or state the FEFO Exception Reason."
					).format(
						earlier.batch_no,
						frappe.format_value(earlier.expiry_date, {"fieldtype": "Date"}),
						lot,
						sle.warehouse,
					),
					title=_("First expiry, first out"),
				)


def is_expired(expiry_date, posting_date) -> bool:
	return bool(expiry_date) and getdate(expiry_date) < getdate(posting_date)


def describe(sle: Document) -> frappe._dict:
	"""What the movement is: whether it keeps the stock apart (an expired lot may go there), whether
	it uses the stock (production or a customer), and any FEFO exception reason it states."""
	keeps_apart = uses = False
	reason = None
	if sle.voucher_type == "Stock Reconciliation":
		keeps_apart = True  # a count, not a movement of the stock
	elif sle.voucher_type == "Stock Entry":
		purpose, transfer, reason = frappe.db.get_value(
			"Stock Entry", sle.voucher_no, ["purpose", TRANSFER_FIELD, REASON_FIELD]
		)
		target = frappe.db.get_value("Stock Entry Detail", sle.voucher_detail_no, "t_warehouse")
		keeps_apart = bool(transfer) or (
			purpose == "Material Transfer" and stores.role_of(target) in KEPT_APART
		)
		uses = purpose in USE_PURPOSES
	else:
		meta = frappe.get_meta(sle.voucher_type)
		is_return = meta.has_field("is_return") and bool(
			frappe.db.get_value(sle.voucher_type, sle.voucher_no, "is_return")
		)
		keeps_apart = is_return and sle.voucher_type in ("Purchase Receipt", "Purchase Invoice")
		uses = sle.voucher_type in DELIVERIES and not is_return
		if meta.has_field(REASON_FIELD):
			reason = frappe.db.get_value(sle.voucher_type, sle.voucher_no, REASON_FIELD)
	return frappe._dict(keeps_apart=keeps_apart, uses=uses, reason=(reason or "").strip())


def lots_left(sle: Document) -> list[frappe._dict]:
	"""The Approved lots of the entry's item, not expired on its posting date, that would still be
	in its store after the document, soonest expiry first: their stock there apart from this
	document, less what the document takes of them from there. A lot that never expires is not
	among them; it goes after every lot that does.

	Read in a fixed number of queries whatever the item's history (URS-PLT-021): the stock comes
	from ERPNext's own lot quantities for the item in the store, in one call, as each lot's was
	read before (`get_batch_qty`, so reserved stock and expired or disabled lots count as ERPNext
	counts them), and only the lots with stock left are read further."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	precision = qty_precision()
	in_store: dict[str, float] = {}
	for row in (
		get_batch_qty(item_code=sle.item_code, warehouse=sle.warehouse, ignore_voucher_nos=[sle.voucher_no])
		or []
	):
		if lot := row.get("batch_no"):
			in_store[lot] = in_store.get(lot, 0) + flt(row.get("qty"))
	taken = taken_by_document(sle) if in_store else {}
	left = [
		lot for lot, qty in in_store.items() if flt(flt(qty, precision) - taken.get(lot, 0), precision) > 0
	]
	if not left:
		return []
	statuses = status_of(left)
	return [
		row
		for row in frappe.get_all(
			"Batch",
			filters={"name": ["in", left], "expiry_date": [">=", getdate(sle.posting_date)]},
			fields=["name as batch_no", "expiry_date"],
			order_by="expiry_date asc, name asc",
		)
		if statuses.get(row.batch_no) == APPROVED
	]


def earlier_lot_left(lot: str, expiry_date, left: list[frappe._dict]) -> frappe._dict | None:
	"""Of `left` (`lots_left`), the first lot that expires before `lot`; any, for a lot that
	never expires."""
	for row in left:
		if row.batch_no != lot and (not expiry_date or getdate(row.expiry_date) < getdate(expiry_date)):
			return row
	return None


# The lines of each voucher type that uses stock (`describe`): a lot bundle names the line it is for,
# a packed item's bundle the packed item or its parent line.
LINE_TABLES: dict[str, tuple[str, ...]] = {
	"Stock Entry": ("Stock Entry Detail",),
	"Delivery Note": ("Delivery Note Item", "Packed Item"),
	"Sales Invoice": ("Sales Invoice Item", "Packed Item"),
	"POS Invoice": ("POS Invoice Item", "Packed Item"),
}


def taken_by_document(sle: Document) -> dict[str, float]:
	"""What the document takes of each lot of the item out of the entry's store, on all its lines."""
	bundle, entry = frappe.qb.DocType("Serial and Batch Bundle"), frappe.qb.DocType("Serial and Batch Entry")
	query = (
		frappe.qb.from_(entry)
		.join(bundle)
		.on(entry.parent == bundle.name)
		.select(entry.batch_no, entry.qty)
		.where(
			(bundle.voucher_type == sle.voucher_type)
			& (bundle.voucher_no == sle.voucher_no)
			& (bundle.item_code == sle.item_code)
			& (bundle.warehouse == sle.warehouse)
			& (bundle.type_of_transaction == "Outward")
			& (bundle.is_cancelled == 0)
			& (bundle.docstatus < 2)
		)
	)
	if tables := LINE_TABLES.get(sle.voucher_type):
		# ERPNext indexes a bundle by its line, not by its document (only the document's type, shared
		# by half the bundles): the document's lines are read by their parent, which is indexed, so the
		# bundles are found through the index on their line whatever the size of the table.
		lines = [
			name
			for table in tables
			for name in frappe.get_all(
				table, filters={"parent": sle.voucher_no, "parenttype": sle.voucher_type}, pluck="name"
			)
		]
		if not lines:
			return {}
		query = query.where(bundle.voucher_detail_no.isin(lines))
	rows = query.run(as_dict=True)
	taken: dict[str, float] = {}
	for row in rows:
		taken[row.batch_no] = taken.get(row.batch_no, 0) + abs(flt(row.qty))
	return taken
