"""Warehouses (module 7; URS-MST-013; master plan section 3: masters approved before use, the raw,
quarantine, approved, WIP, finished goods, rejected and returns areas; DFD D4 Inventory & Batch
Ledger; ISO 13485 7.5.11, 6.4).

QA approves each store with an electronic signature before stock moves into or out of it
(master_data/master_approval.py): once it is a store that holds stock (not a group) of a company.
The check is on the stock ledger, the one place every movement passes, so a receipt, an issue, a
transfer, a delivery, production and a stock count (opening stock included) are refused alike for
a store QA has not approved. Reversing a movement made before the store needed approval (a
cancellation) is not refused. A store's company and role are part of what QA approved. What QA
approved changes once approved only under an approved change request (URS-MST-001). Which role a
store has (quarantine, hold, rejected, returns, or none: approved stock) is module 17's
(lot_control/stores.py); bin locations within a store are module 18's putaway.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cint, flt

from meddevice_suite.master_data import master_approval

CUSTOM_FIELDS = {"Warehouse": master_approval.approval_fields("company")}


def apply_warehouse_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def missing_for_approval(doc: Document) -> list[str]:
	missing = []
	if cint(doc.get("is_group")):
		missing.append(_("to be a store that holds stock (it is a group of stores)"))
	if not doc.get("company"):
		missing.append(_("its company"))
	return missing


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA's signed approval of a store (compliance.signing.SIGNING_ACTIONS)."""
	master_approval.approve(doc, password, reason, missing_for_approval)


def guard(doc: Document, method: str | None = None) -> None:
	"""Warehouse validate: approval by signature only; an approved store keeps what its approval
	needed."""
	master_approval.guard_approval(doc)
	if (
		master_approval.configured("Warehouse")
		and doc.get(master_approval.APPROVAL_STATUS) == master_approval.APPROVED
		and (missing := missing_for_approval(doc))
	):
		frappe.throw(
			_("{0} is approved and keeps what its approval needed; it lacks {1}.").format(
				doc.name, "; ".join(missing)
			)
		)


def onload(doc: Document, method: str | None = None) -> None:
	master_approval.onload(doc, missing_for_approval)


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: stock moves into or out of a store only once QA has approved it.
	A cancelled entry, and one that moves nothing, are not a movement."""
	if sle.get("is_cancelled") or not master_approval.configured("Warehouse"):
		return
	# A stock count sets its quantity after this check (ERPNext writes it in later, past validate),
	# so a count's entry is checked whatever quantity it shows now.
	if not flt(sle.get("actual_qty")) and sle.get("voucher_type") != "Stock Reconciliation":
		return
	if (
		frappe.get_cached_value("Warehouse", sle.warehouse, master_approval.APPROVAL_STATUS)
		!= master_approval.APPROVED
	):
		frappe.throw(
			_(
				"Store {0} is not approved by QA; no stock moves into or out of it. QA approves it on the "
				"store's form (Approve)."
			).format(sle.warehouse),
			title=_("Store not approved"),
		)
