"""Finished goods come into stock only from production (module 17; URS-STK-013; foundation hardening
plan D-28, decided 2026-10-14; DFD 4.6 Batch Creation; ISO 13485 7.5.1, 7.5.8, 7.5.9).

A finished good is made on a work order released on its Current device master record (modules 19,
31), and its lot comes into stock on that order's manufacture entry, with the materials it consumed
and the checks made on the way behind it. A material receipt would bring a finished lot into stock
with none of that, so a material receipt of a finished good is refused, on the form before it is
submitted (`guard_stock_entry`) and at the stock ledger, which every posting passes whatever made it
(`guard_stock_ledger_entry`), as the lot status rules are (`lot_status`).

The one exception is opening stock at migration, ERPNext's Is Opening = Yes on the stock entry:
finished goods already in the plant's stores when the system goes live were made before it and come
in once that way (`as_opening_stock`). They still come in as new lots in Quarantine, inspected on
that entry and released by QA, as any material receipt is (modules 16, 17).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from meddevice_suite.lot_control.lot_status import qty_precision
from meddevice_suite.master_data.item_master import FINISHED_GOOD, ITEM_TYPE

MATERIAL_RECEIPT = "Material Receipt"
OPENING = "Yes"  # ERPNext's Is Opening on a stock entry


class FinishedGoodNotProduced(frappe.ValidationError):
	"""A finished good was refused on a material receipt that is not opening stock."""


def guard_stock_entry(doc: Document, method: str | None = None) -> None:
	"""Stock Entry validate: a material receipt names no finished good, unless it is opening stock."""
	purpose = doc.get("purpose") or frappe.db.get_value(
		"Stock Entry Type", doc.get("stock_entry_type"), "purpose"
	)
	if purpose != MATERIAL_RECEIPT or doc.get("is_opening") == OPENING:
		return
	received = sorted({row.item_code for row in doc.get("items") or [] if row.get("item_code")})
	finished = frappe.get_all(
		"Item",
		filters={"name": ["in", received or [""]], ITEM_TYPE: FINISHED_GOOD},
		pluck="name",
		order_by="name asc",
	)
	if finished:
		refuse(finished)


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: no finished good comes in on a material receipt that is not opening
	stock, whatever posted it."""
	if (
		sle.voucher_type != "Stock Entry"
		or sle.is_cancelled
		or flt(sle.actual_qty, qty_precision()) <= 0
		or frappe.get_cached_value("Item", sle.item_code, ITEM_TYPE) != FINISHED_GOOD
	):
		return
	purpose, is_opening = frappe.db.get_value("Stock Entry", sle.voucher_no, ["purpose", "is_opening"]) or (
		None,
		None,
	)
	if purpose == MATERIAL_RECEIPT and is_opening != OPENING:
		refuse([sle.item_code])


def refuse(items: list[str]) -> None:
	frappe.throw(
		_(
			"{0}: a finished good comes into stock only from production, on its work order's manufacture entry. Only opening stock at migration is received on a material receipt, with Is Opening set to Yes."
		).format(", ".join(items)),
		FinishedGoodNotProduced,
		title=_("Finished goods come from production"),
	)


def as_opening_stock(entry: Document) -> Document:
	"""Make a material receipt opening stock at migration: Is Opening = Yes, with each line's
	difference posted to the company's temporary opening account, as ERPNext requires of an opening
	entry (an expense account would put the opening value in the profit and loss)."""
	account = opening_account(entry.company)
	entry.is_opening = OPENING
	for row in entry.items:
		row.expense_account = account
	return entry


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
