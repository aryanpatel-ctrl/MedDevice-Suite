"""Finished goods come into stock only from production (module 17; URS-STK-013; foundation hardening
plan D-28, decided 2026-10-14; DFD 4.6 Batch Creation; ISO 13485 7.5.1, 7.5.8, 7.5.9).

A finished good is made on a work order released on its Current device master record (modules 19,
31), and its lot comes into stock on that order's manufacture entry, with the materials it consumed
and the checks made on the way behind it. The plant makes its own finished goods: one bought in
would be trading, which is not in the DFD, and one received or counted into stock by hand would
have none of that record behind it.

One rule answers, for every voucher type and stock entry purpose that can post stock, whether stock
of a finished good may come in on it (`may_enter`, from the declarations in `WAYS_IN`): from
production, moved between stores, or a customer's goods coming back, yes; opening stock at migration
on a material receipt (ERPNext's Is Opening = Yes), yes; a goods receipt, a purchase invoice, a
subcontractor's receipt, a material receipt that is not opening stock, and a stock reconciliation
that brings a new lot in, of whatever purpose, no. A path not declared is refused, so a voucher
type ERPNext adds is classified before it is trusted. The rule is held on the forms that bring stock
in, before they are submitted (`guard_document`), and at the stock ledger, which every posting
passes whatever made it (`guard_stock_ledger_entry`), as the lot status rules are (`lot_status`). A
stock count of a lot already in stock is not new stock and is left to `lot_status.counted_back`.

Opening stock comes in only as a material receipt (`as_opening_stock`), the path QA inspects and
releases: opening stock is loaded as material receipts and inspected on them (decided 2026-10-09,
DFD 3.1-3.5). It comes in as new lots in Quarantine (modules 16, 17). A stock reconciliation of
purpose Opening Stock would bring a lot in that QA's release does not take, so it never brings a
finished lot in.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from meddevice_suite.lot_control.lot_status import lots_of_bundle, lots_of_rows, qty_precision, voucher_header
from meddevice_suite.lot_control.receipt import origins_of
from meddevice_suite.master_data.item_master import FINISHED_GOOD, ITEM_TYPE

STOCK_ENTRY, STOCK_RECONCILIATION = "Stock Entry", "Stock Reconciliation"
MATERIAL_RECEIPT = "Material Receipt"
OPENING = "Yes"  # ERPNext's Is Opening on a stock entry

# How stock of a finished good may come in on a voucher.
PRODUCED = "made by production"
MOVED = "moved from another store"
RETURNED = "a customer's goods coming back, when a return"
OPENING_ONLY = "opening stock at migration only"
NEVER = "never"
NO_STOCK_IN = "brings no stock in"
ALLOWED = frozenset({PRODUCED, MOVED})

# (voucher type, stock entry purpose) -> how a finished good may come in on it. Every stock entry
# purpose and every record type that is a stock voucher in ERPNext is declared (the tests hold this to
# ERPNext's own lists); one not declared is refused.
WAYS_IN: dict[tuple[str, str | None], str] = {
	(STOCK_ENTRY, "Manufacture"): PRODUCED,
	# A finished good is made on a repack only on a work order of it too (qms.device_master_record).
	(STOCK_ENTRY, "Repack"): PRODUCED,
	(STOCK_ENTRY, MATERIAL_RECEIPT): OPENING_ONLY,
	(STOCK_ENTRY, "Material Transfer"): MOVED,
	(STOCK_ENTRY, "Material Transfer for Manufacture"): MOVED,
	(STOCK_ENTRY, "Send to Subcontractor"): MOVED,
	(STOCK_ENTRY, "Material Issue"): NO_STOCK_IN,
	(STOCK_ENTRY, "Material Consumption for Manufacture"): NO_STOCK_IN,
	(STOCK_ENTRY, "Return Raw Material to Customer"): NO_STOCK_IN,
	(STOCK_ENTRY, "Subcontracting Delivery"): NO_STOCK_IN,
	# Taking a product apart makes its parts, never a finished good.
	(STOCK_ENTRY, "Disassemble"): NEVER,
	# Subcontracting for a customer (their material in, their goods back): not in the DFD.
	(STOCK_ENTRY, "Receive from Customer"): NEVER,
	(STOCK_ENTRY, "Subcontracting Return"): NEVER,
	# Never a new lot, Opening Stock included: opening stock comes in on a material receipt, which QA
	# inspects and releases. A count of a lot in stock is not new stock (lot_status.counted_back).
	(STOCK_RECONCILIATION, None): NEVER,
	# Bought in, or made by another plant: trading, not in the DFD.
	("Purchase Receipt", None): NEVER,
	("Purchase Invoice", None): NEVER,
	("Subcontracting Receipt", None): NEVER,
	("Asset Capitalization", None): NEVER,
	# Goods a customer sends back are the same stock, into the Returns store (plan D-25).
	("Delivery Note", None): RETURNED,
	("Sales Invoice", None): RETURNED,
	("POS Invoice", None): RETURNED,
	# ERPNext stock controllers that post no stock.
	**dict.fromkeys(
		(
			(doctype, None)
			for doctype in (
				"Lead",
				"Quotation",
				"Sales Order",
				"Supplier Quotation",
				"Request for Quotation",
				"Purchase Order",
				"Material Request",
				"Subcontracting Order",
				"Subcontracting Inward Order",
				"Serial No",
			)
		),
		NO_STOCK_IN,
	),
}


class FinishedGoodNotProduced(frappe.ValidationError):
	"""Stock of a finished good was refused on a voucher that does not bring finished goods in."""


def may_enter(
	voucher_type: str, purpose: str | None = None, opening: bool = False, is_return: bool = False
) -> bool:
	"""May stock of a finished good come into the stores on this voucher (URS-STK-013)?"""
	way = WAYS_IN.get((voucher_type, purpose if voucher_type == STOCK_ENTRY else None))
	if way == OPENING_ONLY:
		return opening
	if way == RETURNED:
		return is_return
	return way in ALLOWED


def way_in(voucher_type: str, values: Document | frappe._dict) -> tuple[str | None, bool, bool]:
	"""(stock entry purpose, whether opening stock, whether a return) of a voucher, from the voucher
	itself or its stored values."""
	if voucher_type == STOCK_ENTRY:
		purpose = values.get("purpose") or frappe.db.get_value(
			"Stock Entry Type", values.get("stock_entry_type"), "purpose"
		)
		return purpose, values.get("is_opening") == OPENING, False
	return None, False, bool(values.get("is_return"))


def guard_document(doc: Document, method: str | None = None) -> None:
	"""Stock Entry, Purchase Receipt and Stock Reconciliation validate: refused on the form, before
	submit, when a finished good would come in on it."""
	purpose, opening, is_return = way_in(doc.doctype, doc)
	if may_enter(doc.doctype, purpose, opening, is_return):
		return
	coming_in = sorted({row.item_code for row in rows_bringing_stock_in(doc) if row.get("item_code")})
	finished = frappe.get_all(
		"Item",
		filters={"name": ["in", coming_in or [""]], ITEM_TYPE: FINISHED_GOOD},
		pluck="name",
		order_by="name asc",
	)
	if finished:
		refuse(finished, doc.doctype)


def rows_bringing_stock_in(doc: Document) -> list[Document]:
	"""The lines of a document that bring stock into a store: a stock entry's lines into one; every
	line of a goods receipt that is not a return; a count's lines of a lot not yet in stock, or of no
	lot named, which come in as a new lot."""
	if doc.doctype == STOCK_ENTRY:
		return [row for row in doc.items if row.get("t_warehouse")]
	if doc.get("is_return"):
		return []
	if doc.doctype != STOCK_RECONCILIATION:
		return list(doc.items)
	rows = list(zip(doc.items, lots_of_rows(doc.items), strict=True))
	origins = origins_of(sorted({lot for _row, lots in rows for lot in lots}))
	return [row for row, lots in rows if is_new(lots, origins, doc.name)]


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: no stock of a finished good comes in on a voucher that does not
	bring finished goods in, whatever posted it. A cancelled posting's reversal undoes, it does not
	bring stock in."""
	if (
		sle.is_cancelled
		or flt(sle.actual_qty, qty_precision()) <= 0
		or frappe.get_cached_value("Item", sle.item_code, ITEM_TYPE) != FINISHED_GOOD
	):
		return
	values = voucher_header(sle.voucher_type, sle.voucher_no)  # once per document (lot_status)
	if sle.voucher_type == STOCK_RECONCILIATION and not brings_new_lot(sle):
		return  # a count of a lot in stock (lot_status.counted_back)
	if not may_enter(sle.voucher_type, *way_in(sle.voucher_type, values)):
		refuse([sle.item_code], sle.voucher_type)


def brings_new_lot(sle: Document) -> bool:
	lots = [sle.batch_no] if sle.batch_no else lots_of_bundle(sle.serial_and_batch_bundle)
	return is_new(lots, origins_of(lots), sle.voucher_no)


def is_new(lots: list[str], origins: dict[str, frappe._dict], voucher_no: str | None) -> bool:
	"""Whether a line brings a lot into stock for the first time: no lot named yet (one is made for
	it), or a lot that no other document brought in."""
	return not lots or any(lot not in origins or origins[lot].voucher_no == voucher_no for lot in lots)


def refuse(items: list[str], voucher_type: str) -> None:
	frappe.throw(
		_(
			"{0}: a finished good comes into stock only from production, on its work order's manufacture entry, not on a {1}. Only opening stock at migration comes in otherwise, on a material receipt with Is Opening set to Yes, which QA inspects and releases."
		).format(", ".join(items), _(voucher_type)),
		FinishedGoodNotProduced,
		title=_("Finished goods come from production"),
	)


def as_opening_stock(doc: Document) -> Document:
	"""Make a material receipt opening stock at migration: Is Opening = Yes, with each line's difference
	posted to the company's temporary opening account, as ERPNext requires of an opening entry (an
	expense account would put the opening value in the profit and loss). Opening stock comes in on a
	material receipt only, the path QA inspects and releases."""
	if doc.doctype != STOCK_ENTRY:
		frappe.throw(_("Opening stock comes in on a material receipt with Is Opening set to Yes."))
	account = opening_account(doc.company)
	doc.is_opening = OPENING
	for row in doc.items:
		row.expense_account = account
	return doc


def opening_account(company: str) -> str:
	accounts = frappe.get_all(
		"Account",
		filters={"company": company, "account_type": "Temporary", "is_group": 0, "disabled": 0},
		pluck="name",
		order_by="creation asc",
		limit=1,
	)
	if not accounts:
		frappe.throw(_("{0} has no temporary opening account to post opening stock against.").format(company))
	return accounts[0]
