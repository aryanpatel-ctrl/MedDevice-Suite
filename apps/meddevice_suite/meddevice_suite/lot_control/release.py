"""The signed decision on a lot (modules 16 and 17; URS-STK-004, URS-STK-010, URS-QC-003, URS-QC-004):
QA releases, rejects or holds a lot, or disposes of goods a customer sent back, through a Lot
Release record, each act an electronic signature (21 CFR Part 11).

- Release: a lot from a supplier needs its newest incoming inspection, made after any hold, to be
  accepted; the lot becomes Approved and its stock moves to the approved store in the same
  transaction. A lot a work order made is released on the same signature until final QC and batch
  release (modules 25, 26) take over.
- Reject: the lot becomes Rejected, its stock moves to the rejected store, and a draft return
  to the supplier of that stock is made for Purchase to send.
- Hold: the lot becomes Hold and its stock moves to the hold store; a held lot is released
  only after a new accepted inspection (decided 2026-10-08).
- Return to Stock and Reject Return (plan D-25, decided 2026-10-12): goods a customer sent back
  wait in the Returns store; QA sends them back to the approved store, once the lot is Approved
  and an inspection made after they came back is accepted, or to the Rejected store. The lot's
  status does not change: the decision is on the returned goods, not on the lot.

A decision acts only on the stock in the store of the lot's status (`decided_stock`): the
quantity rejected at the door is in the Rejected store under the same lot and goes back or is
scrapped by Stores on its own path; goods a customer sent back are disposed of on their own. What
a decision acts on, the quantity, where the lot came from and when it was held, is read again when
QA signs, under the lot's lock, never taken from the draft (found 2026-10-12).

The acts are registered in `compliance.signing.SIGNING_ACTIONS`; the record's controller
keeps everyone else to the rules. Nobody holds Frappe's submit permission on a Lot Release:
the signed act is the only submit.

The returns disposition is a decision on the Lot Release record rather than a record of its own:
it is the same signed act by the same person on the same lot, with the same reason, inspection,
transfer and never-cancelled rules, so one record type, one list and one printout hold every
QA decision on a lot's stock.
"""

from dataclasses import dataclass
from datetime import datetime

import frappe
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate, now_datetime

from meddevice_suite.compliance.checks import require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.lot_control import lot_status, stores
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	HOLD,
	QUARANTINE,
	REJECTED,
	STATUS_FIELD,
	STATUSES,
)
from meddevice_suite.lot_control.receipt import (
	INCOMING,
	OUTGOING,
	accepted_inspection,
	made_by_production,
	material_receipt_of,
	receipt_line_of,
)

DOCTYPE = "Lot Release"
RELEASE, REJECT, HOLD_DECISION = "Release", "Reject", "Hold"
RETURN_TO_STOCK, REJECT_RETURN = "Return to Stock", "Reject Return"
# Goods that came back are inspected against the item's incoming or final specification.
RETURN_INSPECTIONS = (INCOMING, OUTGOING)


@dataclass(frozen=True)
class Decision:
	starts: tuple[str, ...]  # the lot statuses it is taken from
	ends: str | None  # the lot's status after it; None: unchanged (a disposition of returned goods)
	role: str | None  # the store role its stock goes to; None: the item's approved store
	returns: bool = False  # it acts on the stock in the Returns store, not on the lot's own


DECISIONS = {
	RELEASE: Decision((QUARANTINE, HOLD), APPROVED, None),
	REJECT: Decision((QUARANTINE, HOLD), REJECTED, stores.REJECTED),
	HOLD_DECISION: Decision((QUARANTINE, APPROVED), HOLD, stores.HOLD),
	RETURN_TO_STOCK: Decision((APPROVED,), None, None, returns=True),
	REJECT_RETURN: Decision(STATUSES, None, stores.REJECTED, returns=True),
}


def release(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, RELEASE, "Released", password, reason)


def reject(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, REJECT, "Rejected", password, reason)


def hold(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, HOLD_DECISION, "Held", password, reason)


def return_to_stock(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, RETURN_TO_STOCK, "Released", password, reason)


def reject_return(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, REJECT_RETURN, "Rejected", password, reason)


def decide(doc: Document, decision: str, meaning: str, password: str, reason: str | None) -> None:
	doc = locked(doc.name)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(lot_status.is_qa(), _("Only QA decides a lot."), frappe.PermissionError)
	rule = DECISIONS[decision]
	require(
		reason,
		_("Say why the lot is {0}.").format(_(rule.ends).lower())
		if rule.ends
		else _("Say why the goods that came back go there."),
	)
	lot = lot_status.locked(doc.batch)
	status = lot.get(STATUS_FIELD) or QUARANTINE
	require(
		status in rule.starts,
		_("Lot {0} is {1}; it cannot be {2}.").format(lot.name, _(status), _(rule.ends).lower())
		if rule.ends
		else _("Lot {0} is {1}; goods that came back go back to stock only once the lot is Approved.").format(
			lot.name, _(status)
		),
	)
	# The draft may have been made before the lot was held, moved or received again: what the
	# decision rests on is read again now, under the lot's lock, and recorded with the signature.
	doc.describe_lot()
	stock = decided_stock(decision, lot.name, status)
	doc.quantity = sum(flt(holding.qty) for holding in stock)
	# A lot not made by production is released only after an accepted incoming inspection on the
	# document that brought it in, a goods receipt or a material receipt (decided 2026-10-09); a lot
	# that came in any other way is not released at all.
	if decision == RELEASE and not made_by_production(lot.name):
		require(
			doc.source_doctype == "Purchase Receipt" or material_receipt_of(lot.name),
			_(
				"Lot {0} came in on {1} {2}; only stock received on a goods receipt or a material receipt is released."
			).format(lot.name, _(doc.source_doctype or "an unknown document"), doc.source_name or ""),
		)
		inspection = accepted_inspection(lot.name, doc.held_since, doc.source_doctype, doc.source_name)
		missing = (
			_(
				"Lot {0} has no accepted incoming inspection made after it was put on hold; it cannot be released."
			)
			if status == HOLD
			else _("Lot {0} has no accepted incoming inspection; it cannot be released.")
		)
		require(inspection, missing.format(lot.name))
		doc.quality_inspection = inspection
	if rule.returns:
		require(stock, _("Lot {0} has no stock in the Returns store to decide on.").format(lot.name))
	if decision == RETURN_TO_STOCK:
		require(
			not lot.expiry_date or getdate(lot.expiry_date) >= getdate(),
			_("Lot {0} has expired; goods that came back do not go back to stock.").format(lot.name),
		)
		came_back = lot_status.last_arrival(lot.name, {holding.warehouse for holding in stock})
		inspection = accepted_inspection(lot.name, came_back, None, None, RETURN_INSPECTIONS)
		require(
			inspection,
			_(
				"Lot {0} has no accepted inspection made after its goods came back; they cannot go back to stock."
			).format(lot.name),
		)
		doc.quality_inspection = inspection
	doc.lot_status_before = status
	doc.decision = decision
	doc.reason = reason
	doc.decided_by = frappe.session.user
	doc.decided_at = now_datetime()
	sign(doc, meaning, decision, password, reason)

	moved = lot_status.move_to_role(lot, rule.role, reason, stock)
	if rule.ends:
		lot_status.set_status(lot.name, rule.ends, reason, release=doc.name)
	doc.stock_entry = moved.name if moved else None
	if decision == REJECT and doc.source_doctype == "Purchase Receipt" and flt(doc.quantity):
		doc.supplier_return = supplier_return_for(lot.name, doc.quantity).name
	doc.flags.lot_release = True
	# No role holds Frappe's submit permission on a lot release; the signed act is the submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def decided_stock(decision: str | None, lot: str, status: str) -> list[frappe._dict]:
	"""The stock a decision acts on (found 2026-10-12): for a decision on the lot, its stock in the
	store of its status (`lot_status.decided_kinds`), never what is in the Rejected or Returns store;
	for a disposition of returned goods, its stock in the Returns store. None is a decision on the
	lot, as a draft shows it."""
	if decision and DECISIONS[decision].returns:
		return lot_status.holdings_of(lot, {stores.RETURNS})
	return lot_status.holdings_of(lot, lot_status.decided_kinds(status))


def held_since(lot: Document) -> datetime | None:
	"""When the lot was put on hold, if it is on hold now: the later of the time its status was set
	and its latest signed Hold decision, as stored. A held lot is released only on an inspection made
	after it."""
	if lot.get(STATUS_FIELD) != HOLD:
		return None
	decided = frappe.get_all(
		DOCTYPE,
		filters={"batch": lot.name, "decision": HOLD_DECISION, "docstatus": 1},
		pluck="decided_at",
		order_by="decided_at desc",
		limit=1,
	)
	times = [get_datetime(time) for time in (lot.get("mds_status_set_at"), *decided) if time]
	return max(times) if times else None


def supplier_return_for(lot: str, quantity: float) -> Document:
	"""A draft return to the supplier of the rejected lot's decided quantity, from the Rejected store;
	Purchase checks it and submits it (module 15). Only what QA decided goes back on it: the quantity
	rejected at the door is in the Rejected store already and goes back, or is scrapped, by Stores on
	its own. So the line returns accepted quantity only, nothing as rejected, and received equals
	accepted, as ERPNext's return checks require: received = accepted + rejected, and no more back
	than the receipt line accepted."""
	line = receipt_line_of(lot)
	require(line, _("Lot {0} came in on no goods receipt line to return it on.").format(lot))
	# The return belongs to Purchase; QA, who may not create receipts, drafts it from the signed
	# decision, so the mapper is told not to ask QA for that permission.
	target = frappe.new_doc("Purchase Receipt")
	target.flags.ignore_permissions = True
	back = make_purchase_return(line.receipt, target_doc=target)
	back.items = [row for row in back.items if row.get("purchase_receipt_item") == line.row]
	require(back.items, _("The receipt line of lot {0} was not found for the return.").format(lot))
	rejected_store = stores.store_for(stores.REJECTED, back.company)
	back.rejected_warehouse = None
	for row in back.items:
		# In the line's unit; ERPNext works out the stock quantities from it.
		qty = -flt(abs(flt(quantity)) / (flt(row.conversion_factor) or 1), row.precision("qty"))
		row.update(
			{
				"qty": qty,
				"received_qty": qty,
				"rejected_qty": 0,
				"rejected_warehouse": None,
				"return_qty_from_rejected_warehouse": 0,
				"warehouse": rejected_store,
				"batch_no": lot,
				"use_serial_batch_fields": 1,
				"serial_and_batch_bundle": None,
				"rejected_serial_and_batch_bundle": None,
			}
		)
	back.flags.lot_release = True
	# The return belongs to Purchase, who submits it; QA makes the draft from the decision.
	back.insert(ignore_permissions=True)
	return back


def locked(name: str) -> Document:
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc
