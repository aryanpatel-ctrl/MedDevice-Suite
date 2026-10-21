"""The signed decision on a lot (modules 16 and 17; URS-STK-004, URS-STK-010, URS-QC-003 to URS-QC-005):
QA releases, rejects or holds a lot, or disposes of goods a customer sent back, through a Lot
Release record, each act an electronic signature (21 CFR Part 11).

- Release: a lot from a supplier needs its newest incoming inspection, made after any hold, to be
  accepted; the lot becomes Approved and its stock moves to the approved store in the same
  transaction. A lot a work order made is released on the same signature until final QC and batch
  release (modules 25, 26) take over; once held, it is released only after a new accepted final
  inspection on its manufacture entry, unless its item says otherwise (plan D-35, below).
- Reject: the lot becomes Rejected, its stock moves to the rejected store, and a draft return
  to the supplier of that stock is made for Purchase to send.
- Hold: the lot becomes Hold and its stock moves to the hold store; a held lot is released
  only after a new accepted inspection (decided 2026-10-08): incoming for a lot that came in, final
  for a lot production made whose item is re-inspected after a hold (the default; plan D-35,
  decided 2026-10-20 after SAP QM's inspection type per material and its usage decision).
- Release by Concession (plan D-35; ISO 13485 8.3.2): a held lot, of any origin, released without
  that inspection. QA signs it Approved with a written justification besides the reason; a second
  person holding a Management role (as for a Major change) then signs it Approved, and only then is
  the lot released, Approved and moved as a release moves it. A concession whose lot was decided or
  held again in between no longer applies.
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
from meddevice_suite.compliance.fingerprint import plain_text
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
from meddevice_suite.master_data.item_master import reinspected_after_hold

DOCTYPE = "Lot Release"
RELEASE, REJECT, HOLD_DECISION = "Release", "Reject", "Hold"
RETURN_TO_STOCK, REJECT_RETURN = "Return to Stock", "Reject Return"
# Plan D-35: QA's signed approval of a release by concession, then Management's, which releases.
RELEASE_BY_CONCESSION, APPROVE_CONCESSION = "Release by Concession", "Approve Concession"
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
	RELEASE_BY_CONCESSION: Decision((HOLD,), APPROVED, None),
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
	require_no_concession(doc)
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
	if decision == RELEASE:
		doc.quality_inspection = inspection_releasing(doc, lot, status)
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


def inspection_releasing(doc: Document, lot: Document, status: str) -> str | None:
	"""The accepted inspection a release rests on, or the refusal saying which is missing.

	A lot not made by production is released only after an accepted incoming inspection on the
	document that brought it in, a goods receipt or a material receipt (decided 2026-10-09); a lot that
	came in any other way is not released at all. A lot production made is released on QA's signature
	until final QC and batch release (modules 25, 26) take over; once held, only after an accepted final
	inspection against the item's final specification on its manufacture entry, made after the hold,
	unless its item is not re-inspected after a hold (plan D-35)."""
	if made_by_production(lot.name):
		if status != HOLD or not reinspected_after_hold([lot.item]):
			return None
		inspection = accepted_inspection(
			lot.name, doc.held_since, "Stock Entry", doc.source_name, (OUTGOING,)
		)
		require(
			inspection,
			_(
				"Lot {0} has no accepted final inspection on {1} made after it was put on hold; it cannot be released."
			).format(lot.name, doc.source_name),
		)
		return inspection
	require_known_origin(doc, lot)
	inspection = accepted_inspection(lot.name, doc.held_since, doc.source_doctype, doc.source_name)
	missing = (
		_("Lot {0} has no accepted incoming inspection made after it was put on hold; it cannot be released.")
		if status == HOLD
		else _("Lot {0} has no accepted incoming inspection; it cannot be released.")
	)
	require(inspection, missing.format(lot.name))
	return inspection


def require_known_origin(doc: Document, lot: Document) -> None:
	"""Stock is released only if it came in on a goods receipt, a material receipt or production."""
	if made_by_production(lot.name):
		return
	require(
		doc.source_doctype == "Purchase Receipt" or material_receipt_of(lot.name),
		_(
			"Lot {0} came in on {1} {2}; only stock received on a goods receipt or a material receipt is released."
		).format(lot.name, _(doc.source_doctype or "an unknown document"), doc.source_name or ""),
	)


def require_no_concession(doc: Document) -> None:
	"""A draft on which QA approved a concession is Management's to approve; the lot is decided
	otherwise on a new Lot Release, which makes the concession stale."""
	require(
		doc.decision != RELEASE_BY_CONCESSION,
		_(
			"QA has approved the release of {0} by concession; it waits for Management's approval. Decide the lot otherwise on a new Lot Release."
		).format(doc.name),
	)


# --- Release by concession (plan D-35) ----------------------------------------------------------


def release_by_concession(
	doc: Document, password: str, reason: str | None = None, justification: str | None = None
) -> None:
	"""QA's signed approval of releasing a held lot without the inspection its hold requires, with a
	written justification besides the reason (ISO 13485 8.3.2). Nothing moves yet: Management's
	approval releases it (`approve_concession`)."""
	doc = locked(doc.name)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require_no_concession(doc)
	require(lot_status.is_qa(), _("Only QA decides a lot."), frappe.PermissionError)
	require(reason, _("Say why the lot is approved."))
	justification = plain_text((justification or "").strip())
	require(
		justification,
		_(
			"Write the justification for releasing the lot without the inspection its hold requires: a concession is justified in writing."
		),
	)
	require(
		len(justification) <= lot_status.MAX_REASON,
		_("Keep the justification within {0} characters.").format(lot_status.MAX_REASON),
	)
	lot = lot_status.locked(doc.batch)
	status = lot.get(STATUS_FIELD) or QUARANTINE
	require(
		status == HOLD,
		_("Lot {0} is {1}; only a lot on hold is released by concession.").format(lot.name, _(status)),
	)
	doc.describe_lot()
	require_known_origin(doc, lot)
	doc.quantity = sum(flt(holding.qty) for holding in decided_stock(RELEASE_BY_CONCESSION, lot.name, status))
	doc.lot_status_before = status
	doc.decision = RELEASE_BY_CONCESSION
	doc.reason = reason
	doc.concession_justification = justification
	doc.decided_by = frappe.session.user
	doc.decided_at = now_datetime()
	sign(doc, "Approved", "Release by Concession", password, reason)
	doc.flags.lot_release = True
	doc.save()


def approve_concession(doc: Document, password: str, reason: str | None = None) -> None:
	"""Management's signed approval of a release by concession QA approved: a second person, holding a
	Management role, never QA's signer. Only now is the lot released: Approved and moved to the item's
	approved store as a release moves it, the decision submitted."""
	from meddevice_suite.qms.change_control import is_management  # the roles of a Major change

	doc = locked(doc.name)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(
		doc.decision == RELEASE_BY_CONCESSION,
		_("{0} has no release by concession approved by QA to approve.").format(doc.name),
	)
	require(is_management(), _("Only Management approves a release by concession."), frappe.PermissionError)
	user = frappe.session.user
	require(
		user != doc.decided_by,
		_("{0} needs a second approver from Management; you have approved it for QA.").format(doc.name),
	)
	require(reason, _("Say why Management approves the concession."))
	lot = lot_status.locked(doc.batch)
	status = lot.get(STATUS_FIELD) or QUARANTINE
	require(
		status == HOLD,
		_("Lot {0} is {1} now; the concession QA approved while it was on hold no longer applies.").format(
			lot.name, _(status)
		),
	)
	since = held_since(lot)
	require(
		not since or since <= get_datetime(doc.decided_at),
		_(
			"Lot {0} was put on hold again after QA approved the concession; QA decides it afresh on a new Lot Release."
		).format(lot.name),
	)
	# What the release acts on is read again now, under the lot's lock, as a decision's is.
	doc.describe_lot()
	stock = decided_stock(RELEASE_BY_CONCESSION, lot.name, status)
	doc.quantity = sum(flt(holding.qty) for holding in stock)
	doc.concession_approved_by = user
	doc.concession_approved_at = now_datetime()
	# Management reads lot releases and writes none; the signature is its act on the record.
	sign(doc, "Approved", "Approve Concession", password, reason, permission="read")
	moved = lot_status.move_to_role(lot, None, doc.reason, stock, by_service=True)
	lot_status.set_status(lot.name, APPROVED, doc.reason, release=doc.name)
	doc.stock_entry = moved.name if moved else None
	doc.flags.lot_release = True
	# No role holds Frappe's submit permission on a lot release; the signed act is the submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def pending_concessions(lots: list[str] | None = None) -> dict[str, frappe._dict]:
	"""lot -> the release by concession QA approved and Management has not yet, for each lot still on
	hold since before QA's approval; one whose lot was decided or held again since waits on nobody.
	`lots` None: every lot. Two queries."""
	filters = {"docstatus": 0, "decision": RELEASE_BY_CONCESSION}
	if lots is not None:
		filters["batch"] = ["in", list(lots) or [""]]
	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=["name", "batch", "item", "decided_by", "decided_at"],
		order_by="decided_at asc",
	)
	held = {
		row.name: row
		for row in frappe.get_all(
			"Batch",
			filters={"name": ["in", [row.batch for row in rows] or [""]], STATUS_FIELD: HOLD},
			fields=["name", "mds_status_set_at"],
		)
	}
	pending = {}
	for row in rows:
		lot = held.get(row.batch)
		if lot and (
			not lot.mds_status_set_at or get_datetime(lot.mds_status_set_at) <= get_datetime(row.decided_at)
		):
			pending[row.batch] = row
	return pending


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
