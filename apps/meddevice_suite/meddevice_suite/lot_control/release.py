"""The signed decision on a lot (modules 16 and 17; URS-STK-004, URS-QC-003, URS-QC-004):
QA releases, rejects or holds a lot through a Lot Release record, each act an electronic
signature (21 CFR Part 11).

- Release: a lot from a supplier needs an accepted incoming inspection, made after any hold;
  the lot becomes Approved and its stock moves to the approved store in the same transaction.
  A lot a work order made is released on the same signature until final QC and batch
  release (modules 25, 26) take over.
- Reject: the lot becomes Rejected, its stock moves to the rejected store, and a draft return
  to the supplier is made for Purchase to send.
- Hold: the lot becomes Hold and its stock moves to the hold store; a held lot is released
  only after a new accepted inspection (decided 2026-10-08).

The acts are registered in `compliance.signing.SIGNING_ACTIONS`; the record's controller
keeps everyone else to the rules. Nobody holds Frappe's submit permission on a Lot Release:
the signed act is the only submit.
"""

import frappe
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from meddevice_suite.compliance.esign import sign
from meddevice_suite.lot_control import lot_status, stores
from meddevice_suite.lot_control.lot_status import APPROVED, HOLD, QUARANTINE, REJECTED, STATUS_FIELD
from meddevice_suite.lot_control.receipt import accepted_inspection, receipt_line_of

DOCTYPE = "Lot Release"
RELEASE, REJECT, HOLD_DECISION = "Release", "Reject", "Hold"
# decision -> (statuses it starts from, status it ends in, store role it moves to)
DECISIONS = {
	RELEASE: ((QUARANTINE, HOLD), APPROVED, None),
	REJECT: ((QUARANTINE, HOLD), REJECTED, stores.REJECTED),
	HOLD_DECISION: ((QUARANTINE, APPROVED), HOLD, stores.HOLD),
}


def release(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, RELEASE, "Released", password, reason)


def reject(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, REJECT, "Rejected", password, reason)


def hold(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, HOLD_DECISION, "Approved", password, reason)


def decide(doc: Document, decision: str, meaning: str, password: str, reason: str | None) -> None:
	doc = locked(doc.name)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(lot_status.is_qa(), _("Only QA decides a lot."), frappe.PermissionError)
	require(reason, _("Say why the lot is {0}.").format(_(DECISIONS[decision][1]).lower()))
	lot = lot_status.locked(doc.batch)
	status = lot.get(STATUS_FIELD) or QUARANTINE
	starts, ends, role = DECISIONS[decision]
	require(
		status in starts,
		_("Lot {0} is {1}; it cannot be {2}.").format(lot.name, _(status), _(ends).lower()),
	)
	if decision == RELEASE and doc.source_doctype == "Purchase Receipt":
		after = doc.held_since if status == HOLD else None
		inspection = accepted_inspection(lot.name, after)
		missing = (
			_(
				"Lot {0} has no accepted incoming inspection made after it was put on hold; it cannot be released."
			)
			if status == HOLD
			else _("Lot {0} has no accepted incoming inspection; it cannot be released.")
		)
		require(inspection, missing.format(lot.name))
		doc.quality_inspection = inspection
	doc.lot_status_before = status
	doc.decision = decision
	doc.reason = reason
	doc.decided_by = frappe.session.user
	doc.decided_at = now_datetime()
	sign(doc, meaning, decision, password, reason)

	moved = lot_status.move_to_role(lot, role, reason)
	lot_status.set_status(lot.name, ends, reason, release=doc.name)
	doc.stock_entry = moved.name if moved else None
	if decision == REJECT and doc.source_doctype == "Purchase Receipt":
		doc.supplier_return = supplier_return_for(lot.name, doc.quantity).name
	doc.flags.lot_release = True
	# No role holds Frappe's submit permission on a lot release; the signed act is the submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def supplier_return_for(lot: str, quantity: float) -> Document:
	"""A draft return to the supplier for the rejected lot's quantity, from the rejected store;
	Purchase checks it and submits it (module 15)."""
	line = receipt_line_of(lot)
	# The return belongs to Purchase; QA, who may not create receipts, drafts it from the signed
	# decision, so the mapper is told not to ask QA for that permission.
	target = frappe.new_doc("Purchase Receipt")
	target.flags.ignore_permissions = True
	back = make_purchase_return(line.receipt, target_doc=target)
	back.items = [row for row in back.items if row.get("purchase_receipt_item") == line.row]
	require(back.items, _("The receipt line of lot {0} was not found for the return.").format(lot))
	rejected_store = stores.store_for(stores.REJECTED, back.company)
	for row in back.items:
		row.qty = -abs(flt(quantity))
		row.received_qty = -abs(flt(quantity))
		row.stock_qty = -abs(flt(quantity)) * flt(row.conversion_factor or 1)
		row.warehouse = rejected_store
		row.batch_no = lot
		row.use_serial_batch_fields = 1
		row.serial_and_batch_bundle = None
	back.flags.lot_release = True
	# The return belongs to Purchase, who submits it; QA makes the draft from the decision.
	back.insert(ignore_permissions=True)
	return back


def locked(name: str) -> Document:
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc


def require(condition, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)
