"""Expired lots and first expiry, first out (module 18; URS-STK-008, URS-STK-009; D3 "Putaway in
Finished Goods Warehouse (FIFO / FEFO)"; ISO 13485 7.5.11 preservation of product).

Checked on the stock ledger entry, the one place every movement passes, after the store and lot
status checks (master_data/warehouse.py, lot_status.py), so the form, the API, a pick list's
delivery and a production entry are held to the same rules:

- An expired lot (its expiry date before the day the stock moves) leaves its store only to be kept
  apart: into the hold, rejected or returns store, by QA's own decision, back to its
  supplier, or as counted. It is never issued, sent to production, consumed or delivered.
  ERPNext refuses an expired lot on a delivery and on some production entries; a material issue
  naming it by hand, or a move to another usable store, went through.
- When stock goes to production or to a customer from usable stock, the lot that expires first
  goes first: a lot is refused while an earlier-expiring Approved lot of the same item, not yet
  expired, would still be left in any usable store of the company after the document: an approved
  store, not one holding work in progress (`usable_stores`). A document may depart from that only by
  saying why (FEFO Exception Reason, for example a customer asking for one lot); the reason stays on
  the document and in its audit trail, and the Lots Taken Out of Expiry Order report lists every
  exception for QA. A move from one store to another is not a use of the stock and is not held to
  it; comparing every usable store is what keeps such a move from getting round the rule
  (independent review 2026-10-15). Stock taken out of a store holding work in progress was sent to
  production, and its lot chosen, when it went there; scrapped or returned stock is not a use.

The day the stock moves is the later of the document's posting date and the day it is submitted
(`checked_on`): goods physically move when the document is submitted, and a document dated back
(ERPNext's Set Posting Time, which finance needs) must not take out a lot that has expired since
(independent review 2026-10-15).
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
	stock_entry_line,
	submitted_now,
	voucher_header,
	vouchers_read,
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
	on = checked_on(sle)
	expiry = dict(
		frappe.get_all("Batch", filters={"name": ["in", lots]}, fields=["name", "expiry_date"], as_list=True)
	)
	for lot in lots:
		if is_expired(expiry.get(lot), on) and not movement.keeps_apart:
			frappe.throw(
				_(
					"Lot {0} expired on {1}; it cannot be issued, used or delivered. Move it to the hold "
					"store; QA decides it."
				).format(lot, frappe.format_value(expiry.get(lot), {"fieldtype": "Date"})),
				title=_("Lot expired"),
			)
	if movement.uses and not movement.reason and sle.warehouse in (usable := usable_stores(sle)):
		# Read once for the entry, not once per lot: an item made every day has hundreds of lots
		# that expire before the newest, and a query per lot or per such lot grew with the years.
		left = lots_left(sle, on, usable)
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
						", ".join(earlier.warehouses),
					),
					title=_("First expiry, first out"),
				)


def is_expired(expiry_date, on) -> bool:
	return bool(expiry_date) and getdate(expiry_date) < getdate(on)


def checked_on(sle: Document):
	"""The day the entry's stock moves: the later of its posting date and today while its document is
	being submitted, since goods move when it is submitted, whatever date it is posted on. A ledger
	ERPNext posts again later for a document submitted before (a reposting) keeps its posting date:
	what was allowed when it was submitted stays allowed."""
	posted = getdate(sle.posting_date)
	if submitted_now(sle.voucher_type, sle.voucher_no):
		return max(posted, getdate())
	return posted


def company_of(sle: Document) -> str:
	return sle.get("company") or frappe.get_cached_value("Warehouse", sle.warehouse, "company")


def usable_stores(sle: Document) -> list[str]:
	"""The usable stores of the entry's company, among which the lot expiring first goes first: every
	store holding approved stock (no store role), except those holding work in progress
	(`work_in_progress_stores`), whose stock was sent to production for a work order and is not there
	to be taken. Read once per document, not once per line, as its header is (lot_status)."""
	company = company_of(sle)
	read = vouchers_read()
	key = ("usable stores", sle.voucher_type, sle.voucher_no, company)
	if key not in read:
		wip = work_in_progress_stores(company)
		read[key] = sorted(
			name
			for name, role in frappe.get_all(
				"Warehouse",
				filters={"company": company, "is_group": 0},
				fields=["name", stores.ROLE_FIELD],
				as_list=True,
			)
			if not role and name not in wip
		)
	return read[key]


def work_in_progress_stores(company: str) -> set[str]:
	"""The stores the company's submitted work orders send materials to before they are consumed: their
	work-in-progress store, where the order transfers its materials (an order that consumes straight
	from its source store uses none)."""
	return set(
		frappe.get_all(
			"Work Order",
			filters={"company": company, "docstatus": 1, "skip_transfer": 0, "wip_warehouse": ["is", "set"]},
			pluck="wip_warehouse",
			distinct=True,
		)
	)


def describe(sle: Document) -> frappe._dict:
	"""What the movement is: whether it keeps the stock apart (an expired lot may go there), whether
	it uses the stock (production or a customer), and any FEFO exception reason it states."""
	keeps_apart = uses = False
	reason = None
	if sle.voucher_type == "Stock Reconciliation":
		keeps_apart = True  # a count, not a movement of the stock
	elif sle.voucher_type == "Stock Entry":
		# The header and lines read once per document, whatever its number of lines (lot_status).
		header = voucher_header(sle.voucher_type, sle.voucher_no)
		purpose, transfer, reason = (
			header.get("purpose"),
			header.get(TRANSFER_FIELD),
			header.get(REASON_FIELD),
		)
		target = stock_entry_line(sle.voucher_no, sle.voucher_detail_no).t_warehouse
		keeps_apart = bool(transfer) or (
			purpose == "Material Transfer" and stores.role_of(target) in KEPT_APART
		)
		uses = purpose in USE_PURPOSES
	else:
		# Read only where the record type has them (lot_status.voucher_header).
		header = voucher_header(sle.voucher_type, sle.voucher_no)
		is_return = bool(header.get("is_return"))
		keeps_apart = is_return and sle.voucher_type in ("Purchase Receipt", "Purchase Invoice")
		uses = sle.voucher_type in DELIVERIES and not is_return
		reason = header.get(REASON_FIELD)
	return frappe._dict(keeps_apart=keeps_apart, uses=uses, reason=(reason or "").strip())


def lots_left(sle: Document, on=None, usable: list[str] | None = None) -> list[frappe._dict]:
	"""The Approved lots of the entry's item, not expired on the day its stock moves (`on`), that
	would still be in a usable store of the company (`usable`) after the document, soonest expiry
	first, each with the stores holding it: their stock there apart from this document, less what the
	document takes of them from there. A lot that never expires is not among them; it goes after every
	lot that does.

	Read in a fixed number of queries whatever the item's history (URS-PLT-021): the stock comes
	from ERPNext's own lot quantities for the item in the stores, in one call, as each lot's was
	read before (`get_batch_qty`, so reserved stock and expired or disabled lots count as ERPNext
	counts them), and only the lots with stock left are read further."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	on = on or checked_on(sle)
	usable = usable or [sle.warehouse]
	precision = qty_precision()
	in_store: dict[str, float] = {}
	held_in: dict[str, set[str]] = {}
	for row in (
		get_batch_qty(item_code=sle.item_code, warehouse=usable, ignore_voucher_nos=[sle.voucher_no]) or []
	):
		if (lot := row.get("batch_no")) and row.get("warehouse") in usable:
			in_store[lot] = in_store.get(lot, 0) + flt(row.get("qty"))
			if flt(row.get("qty"), precision) > 0:
				held_in.setdefault(lot, set()).add(row.get("warehouse"))
	taken = taken_by_document(sle, usable) if in_store else {}
	left = [
		lot for lot, qty in in_store.items() if flt(flt(qty, precision) - taken.get(lot, 0), precision) > 0
	]
	if not left:
		return []
	statuses = status_of(left)
	return [
		frappe._dict(row, warehouses=sorted(held_in.get(row.batch_no, ())))
		for row in frappe.get_all(
			"Batch",
			filters={"name": ["in", left], "expiry_date": [">=", getdate(on)]},
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


def taken_by_document(sle: Document, usable: list[str] | None = None) -> dict[str, float]:
	"""What the document takes of each lot of the item out of the usable stores (the entry's store if
	not given), on all its lines."""
	bundle, entry = frappe.qb.DocType("Serial and Batch Bundle"), frappe.qb.DocType("Serial and Batch Entry")
	# The bundle's document is indexed by this app's configuration (setup/indexes.py): ERPNext indexes
	# only its type, which half the bundles share.
	rows = (
		frappe.qb.from_(entry)
		.join(bundle)
		.on(entry.parent == bundle.name)
		.select(entry.batch_no, entry.qty)
		.where(
			(bundle.voucher_type == sle.voucher_type)
			& (bundle.voucher_no == sle.voucher_no)
			& (bundle.item_code == sle.item_code)
			& (bundle.warehouse.isin(usable or [sle.warehouse]))
			& (bundle.type_of_transaction == "Outward")
			& (bundle.is_cancelled == 0)
			& (bundle.docstatus < 2)
		)
		.run(as_dict=True)
	)
	taken: dict[str, float] = {}
	for row in rows:
		taken[row.batch_no] = taken.get(row.batch_no, 0) + abs(flt(row.qty))
	return taken
