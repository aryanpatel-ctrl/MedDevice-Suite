"""Approved suppliers (module 4; URS-PUR-001; ISO 13485 7.4.1).

QA decides each supplier on a Supplier Approval record with an electronic signature and a
reason: Approved, Conditional (until a date) or Not Approved, with the items it may supply
and its quality system certificate. The supplier's newest decision is the one in force; a
decision is never cancelled, a new one replaces it.

- A purchase order is refused for a supplier with no decision, one not approved, a
  conditional approval that has ended, a certificate that has expired, or an item it is not
  approved for. Goods enter only on a goods receipt, never on a purchase invoice that updates
  stock, and each line only from a supplier approved for its item at receipt, whatever order it
  was placed under (decided 2026-10-09).
- An order under a conditional approval says why it is placed, and each lot received
  against it is inspected in full: the incoming inspection's sample is the whole lot.
- QA and Purchase are alerted once, 30 days before the certificate of a supplier's
  decision in force expires; a supplier decided again is alerted for its new certificate.

The acts are registered in `compliance.signing.SIGNING_ACTIONS`; the record's controller keeps
everyone else to the rules. Nobody holds Frappe's submit permission on a Supplier Approval:
the signed act is the only submit. Conditional approval rules and the 30-day alert were
decided under the client's instruction of 2026-10-08.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign

DOCTYPE = "Supplier Approval"
APPROVED, CONDITIONAL, NOT_APPROVED = "Approved", "Conditional", "Not Approved"
# act -> (decision it records, signature meaning)
ACTS = {
	"Approve": (APPROVED, "Approved"),
	"Approve Conditionally": (CONDITIONAL, "Approved"),
	"Not Approve": (NOT_APPROVED, "Rejected"),
}
CERTIFICATE_ALERT = "Supplier Certificate Expiring"
SAVEPOINT = "certificate_alert"  # a savepoint name has no spaces
# On a purchase order: the decision it was placed under, and why a conditional supplier.
APPROVAL_FIELD = "mds_supplier_approval"
CONDITIONAL_REASON_FIELD = "mds_conditional_reason"
# On a goods receipt line: its lots are inspected in full.
FULL_INSPECTION_FIELD = "mds_full_inspection"
CUSTOM_FIELDS = {
	"Purchase Order": [
		{
			"fieldname": APPROVAL_FIELD,
			"label": "Supplier Approval",
			"fieldtype": "Link",
			"options": DOCTYPE,
			"insert_after": "supplier",
			"read_only": 1,
			"no_copy": 1,
			"description": "QA's decision on the supplier this order was placed under.",
		},
		{
			"fieldname": CONDITIONAL_REASON_FIELD,
			"label": "Why This Conditionally Approved Supplier",
			"fieldtype": "Small Text",
			"insert_after": APPROVAL_FIELD,
			"no_copy": 1,
			"description": "Needed when the supplier is approved only conditionally.",
		},
	],
	"Purchase Receipt Item": [
		{
			"fieldname": FULL_INSPECTION_FIELD,
			"label": "Inspect Each Lot in Full",
			"fieldtype": "Check",
			"insert_after": "mds_certificate_verified_at",
			"read_only": 1,
			"no_copy": 1,
			"description": "Ordered from a conditionally approved supplier: each lot is inspected in full.",
		}
	],
}


def apply_supplier_approval_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


# --- The signed decision ------------------------------------------------------------------


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, "Approve", password, reason)


def approve_conditionally(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, "Approve Conditionally", password, reason)


def not_approve(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, "Not Approve", password, reason)


def decide(doc: Document, act: str, password: str, reason: str | None) -> None:
	doc = locked(doc.name)
	decision, meaning = ACTS[act]
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(is_qa(), _("Only QA decides a supplier."), frappe.PermissionError)
	require(reason, _("Say why the supplier is decided {0}.").format(_(decision).lower()))
	items = [row.item_code for row in doc.items]
	if decision == NOT_APPROVED:
		require(
			not items,
			_("A supplier that is not approved is approved for no items; remove the items first."),
		)
	else:
		require(items, _("List the items the supplier is approved for."))
		repeated = sorted({item for item in items if items.count(item) > 1})
		require(not repeated, _("Each item is listed once; listed again: {0}.").format(", ".join(repeated)))
		require(
			doc.certificate_number and doc.certificate_expiry and doc.certificate,
			_("Record the supplier's quality system certificate: its number, its expiry and the file."),
		)
		require(
			getdate(doc.certificate_expiry) > getdate(today()),
			_("The supplier's certificate expired on {0}; it cannot be approved on it.").format(
				frappe.format_value(doc.certificate_expiry, {"fieldtype": "Date"})
			),
		)
	if decision == CONDITIONAL:
		require(doc.valid_until, _("Say until when the conditional approval holds."))
		require(
			getdate(doc.valid_until) > getdate(today()),
			_("A conditional approval holds until a day still to come."),
		)
	else:
		require(
			not doc.valid_until,
			_("Only a conditional approval has an end date; clear Conditional Until."),
		)
	doc.decision = decision
	doc.reason = reason
	doc.decided_by = frappe.session.user
	doc.decided_at = now_datetime()
	sign(doc, meaning, act, password, reason)
	doc.flags.supplier_approval = True
	# No role holds Frappe's submit permission on a supplier approval; the signed act is the submit.
	doc.flags.ignore_permissions = True
	doc.submit()
	# QA's decision is the re-evaluation a low supplier rating asked for (URS-PUR-002).
	from meddevice_suite.master_data.supplier_rating import close_re_evaluations  # it reads approvals

	close_re_evaluations(doc)


def locked(name: str) -> Document:
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc


# --- The decision in force ------------------------------------------------------------------


def current_approval(supplier: str) -> frappe._dict | None:
	"""The supplier's newest signed decision, which replaces every earlier one."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"supplier": supplier, "docstatus": 1},
		fields=["name", "decision", "valid_until", "certificate_expiry"],
		order_by="decided_at desc, creation desc",
		limit=1,
	)
	if not rows:
		return None
	approval = rows[0]
	approval.item_codes = frozenset(
		frappe.get_all(
			"Supplier Approval Item",
			filters={"parenttype": DOCTYPE, "parent": approval.name},
			pluck="item_code",
		)
	)
	return approval


def newest_decisions(suppliers: list[str]) -> dict[str, str]:
	"""supplier -> the name of its newest signed decision (`current_approval`), for many suppliers in
	one query: the daily alerts and reminders ask it of every approval they look at, and a query per
	approval grew with every decision ever replaced."""
	wanted = sorted({supplier for supplier in suppliers if supplier})
	if not wanted:
		return {}
	newest: dict[str, str] = {}
	for row in frappe.get_all(
		DOCTYPE,
		filters={"supplier": ["in", wanted], "docstatus": 1},
		fields=["name", "supplier"],
		order_by="decided_at desc, creation desc",
	):
		newest.setdefault(row.supplier, row.name)
	return newest


def has_ended(approval: frappe._dict) -> bool:
	return approval.decision == CONDITIONAL and getdate(approval.valid_until) < getdate(today())


def approval_in_force(supplier: str | None, items: list[str]) -> frappe._dict:
	"""QA's decision in force on the supplier, if it lets the items be bought now: approved or
	conditionally approved (and not ended), on a certificate that has not expired, for each item."""
	approval = current_approval(supplier) if supplier else None
	if approval is None or approval.decision == NOT_APPROVED:
		frappe.throw(
			_("Supplier {0} is not approved by QA; nothing can be bought from it.").format(supplier),
			title=_("Supplier not approved"),
		)
	if has_ended(approval):
		frappe.throw(
			_(
				"The conditional approval of supplier {0} ended on {1}; QA decides the supplier again "
				"before anything is bought from it."
			).format(supplier, frappe.format_value(approval.valid_until, {"fieldtype": "Date"})),
			title=_("Supplier not approved"),
		)
	if getdate(approval.certificate_expiry) < getdate(today()):
		frappe.throw(
			_(
				"The quality system certificate of supplier {0} expired on {1}; QA decides the supplier "
				"again on its renewed certificate before anything is bought from it."
			).format(supplier, frappe.format_value(approval.certificate_expiry, {"fieldtype": "Date"})),
			title=_("Supplier certificate expired"),
		)
	unapproved = sorted({item for item in items if item not in approval.item_codes})
	if unapproved:
		frappe.throw(
			_("Supplier {0} is not approved for {1}; QA approves it for them before they are bought.").format(
				supplier, ", ".join(unapproved)
			),
			title=_("Supplier not approved"),
		)
	return approval


def guard_purchase_order(doc: Document, method: str | None = None) -> None:
	"""Purchase Order validate: only from a supplier QA approved for every item ordered."""
	approval = approval_in_force(doc.supplier, [row.item_code for row in doc.items])
	doc.set(APPROVAL_FIELD, approval.name)
	if approval.decision != CONDITIONAL:
		doc.set(CONDITIONAL_REASON_FIELD, None)
	elif not (doc.get(CONDITIONAL_REASON_FIELD) or "").strip():
		frappe.throw(
			_(
				"Supplier {0} is approved only conditionally, until {1}; say why this order is placed with it."
			).format(doc.supplier, frappe.format_value(approval.valid_until, {"fieldtype": "Date"})),
			title=_("Reason needed"),
		)


def ordered_lines(doc: Document) -> dict[str, frappe._dict]:
	"""The order lines the receipt names that are lines of this supplier's submitted orders: each
	with its item, its order and the approval the order was placed under. An order line of another
	supplier, or of a draft, orders nothing for this receipt."""
	named = [row.purchase_order_item for row in doc.items if row.get("purchase_order_item")]
	if not named:
		return {}
	line, order = frappe.qb.DocType("Purchase Order Item"), frappe.qb.DocType("Purchase Order")
	rows = (
		frappe.qb.from_(line)
		.join(order)
		.on(line.parent == order.name)
		.select(line.name, line.item_code, order[APPROVAL_FIELD].as_("approval"))
		.where(line.name.isin(named) & (line.docstatus == 1) & (order.supplier == doc.supplier))
	).run(as_dict=True)
	return {row.name: row for row in rows}


def guard_order_update(doc: Document, method: str | None = None) -> None:
	"""Purchase Order before_update_after_submit: ERPNext's Update Items adds or changes lines of a
	submitted order past its validation (it marks the order ignore_validate_update_after_submit,
	having written the lines already); each line then needs the supplier approved for its item. Any
	other change of a submitted order (its delivery date, a confirmation number) buys nothing new,
	so only a line whose item, quantity or rate changed is checked."""
	if doc.flags.ignore_validate_update_after_submit:
		approval_in_force(doc.supplier, [row.item_code for row in doc.items])
		return
	before = doc.get_doc_before_save()
	old = {row.name: (row.item_code, flt(row.qty), flt(row.rate)) for row in (before.items if before else [])}
	changed = [
		row.item_code
		for row in doc.items
		if old.get(row.name) != (row.item_code, flt(row.qty), flt(row.rate))
	]
	if changed:
		approval_in_force(doc.supplier, changed)


def mark_full_inspection(doc: Document, method: str | None = None) -> None:
	"""Purchase Receipt validate: goods received without an order come only from a supplier QA
	approved for them (an order was checked when it was placed); a line ordered under a
	conditional approval, or received from a supplier approved only conditionally, has each of
	its lots inspected in full. What a line was ordered under is read from the order line it
	names, as ERPNext checks the line by it."""
	if doc.is_return:
		return
	# Received only from a supplier approved for each item now, whatever order it was placed under:
	# a supplier QA has since stopped is decided again first (decided 2026-10-09).
	approval_in_force(doc.supplier, [row.item_code for row in doc.items])
	lines = ordered_lines(doc)

	def ordered(row: Document) -> frappe._dict | None:
		found = lines.get(row.get("purchase_order_item"))
		return found if found and found.item_code == row.item_code else None

	approvals = list({found.approval for found in lines.values() if found.approval})
	conditional = set(
		frappe.get_all(DOCTYPE, filters={"name": ["in", approvals], "decision": CONDITIONAL}, pluck="name")
		if approvals
		else []
	)
	approval = current_approval(doc.supplier) if doc.supplier else None
	supplier_conditional = bool(approval and approval.decision == CONDITIONAL)
	for row in doc.items:
		found = ordered(row)
		full = bool(found and found.approval in conditional) or supplier_conditional
		row.set(FULL_INSPECTION_FIELD, cint(full))


def refuse_stock_on_invoice(doc: Document, method: str | None = None) -> None:
	"""Purchase Invoice validate: goods enter the store only on a goods receipt, where the supplier,
	the certificate and the incoming inspection are checked; an invoice that updates stock would
	take them past all three (decided 2026-10-09). The invoice is booked against the receipt."""
	if not doc.get("update_stock") or doc.get("is_return"):
		return
	items = [row.item_code for row in doc.items if row.item_code]
	if items and frappe.get_all("Item", filters={"name": ["in", items], "is_stock_item": 1}, limit=1):
		frappe.throw(
			_(
				"Goods are received on a goods receipt, not on a purchase invoice; untick Update Stock and book the invoice against the receipt."
			),
			title=_("Receive on a goods receipt"),
		)


def check_full_inspection(inspection: Document, line: frappe._dict) -> None:
	"""For an incoming inspection of a lot that is inspected in full: the sample is the lot."""
	if not line.full_inspection:
		return
	if flt(inspection.sample_size) < flt(line.quantity):
		frappe.throw(
			_(
				"Lot {0} was ordered from a conditionally approved supplier and is inspected in full: "
				"the sample size is the lot's {1}."
			).format(inspection.batch_no, frappe.format_value(line.quantity, {"fieldtype": "Float"})),
			title=_("Inspect the whole lot"),
		)


# --- The supplier's form ---------------------------------------------------------------------


def supplier_onload(doc: Document, method: str | None = None) -> None:
	"""Supplier onload: QA's decision in force, for the form to show."""
	approval = current_approval(doc.name)
	if approval:
		approval.item_codes = sorted(approval.item_codes)
		approval.ended = has_ended(approval)
	doc.set_onload("supplier_approval", approval)
	from meddevice_suite.master_data.supplier_rating import recent  # the ratings build on approvals

	doc.set_onload("supplier_ratings", recent(doc.name))


# --- Certificate alert -----------------------------------------------------------------------


def send_certificate_alerts() -> None:
	"""Daily: alert QA and Purchase once when the certificate of a supplier's decision in force
	expires within 30 days, including a day the alerts did not run."""
	notification = frappe.get_doc("Notification", CERTIFICATE_ALERT)
	soon = add_days(today(), quality_settings.value("supplier_certificate_warning_days"))
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"docstatus": 1,
			"decision": ["in", [APPROVED, CONDITIONAL]],
			"certificate_alerted": 0,
			"certificate_expiry": ["<=", soon],
		},
		fields=["name", "supplier"],
		order_by="certificate_expiry asc",
	)
	in_force = newest_decisions([row.supplier for row in rows])
	for row in rows:
		if in_force.get(row.supplier) != row.name:
			continue  # a later decision replaced it, with its own certificate
		alert(notification, row.name)


def alert(notification: Document, name: str) -> None:
	# One supplier's failure is logged and retried the next day; it does not hold back the others.
	frappe.db.savepoint(SAVEPOINT)
	try:
		notification.send(frappe.get_doc(DOCTYPE, name))
		# Bookkeeping of the alert, kept out of the signed record (esign.SYSTEM_MAINTAINED).
		frappe.db.set_value(DOCTYPE, name, "certificate_alerted", 1, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"Certificate alert for {name} could not be sent")
	else:
		frappe.db.release_savepoint(SAVEPOINT)
