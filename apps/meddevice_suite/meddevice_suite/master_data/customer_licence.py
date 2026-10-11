"""Customer licences (module 5; URS-MST-008; DFD D1 Customer Master, sales order intake; CDSCO MDR
2017, sale of medical devices to licensed parties; ISO 13485 7.2, 7.5.8; 21 CFR 11.50, 11.70).

A customer is sold to only under a licence QA has verified. Sales records each licence of a
customer on a Customer Licence: its type, number, issuing authority, validity dates and a copy.
QA verifies or rejects it with an electronic signature and a reason, and withdraws a verified
licence with a signature when the authority suspends or cancels it. A licence is never cancelled
or deleted; a renewed licence is a new record.

- A sales order, a delivery note, or a sales or POS invoice that delivers stock is refused when it
  is submitted for a customer that holds no verified licence valid both on the document's date and
  on the day it is submitted: a backdated record does not reach past an expired licence. A draft
  may be saved. The check runs at `before_validate`, which Frappe runs even for a record whose
  validation a caller skips. Lines added or raised on a submitted order (ERPNext's Update Items)
  are checked too.
- A customer holding a licence QA has decided, or QA's decision that it buys without one, is not
  renamed or merged: the licence would follow the name to another party unsigned.
- QA may decide, with a signature and a reason, that a customer buys without a licence, such as
  a hospital buying for its own use, and reverse that the same way. Each such customer is
  decided on its own, not by its group.
- Sales and QA are alerted once, the days set in Quality Settings before a verified licence
  expires, unless a renewed licence of that customer is already verified.

Licence types are a list QA keeps (Customer Licence Type), not code: the authorities' forms
change. The acts are registered in `compliance.signing.SIGNING_ACTIONS`; nobody holds Frappe's
submit permission on a licence, so the signed act is the only submit. Decided 2026-10-11 under
the client's instruction to follow the DFD.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.esign import sign

DOCTYPE = "Customer Licence"
TYPE_DOCTYPE = "Customer Licence Type"
QA_ROLE = "Quality Manager"
VERIFIED, REJECTED, WITHDRAWN = "Verified", "Rejected", "Withdrawn"
# act -> (status it records, signature meaning)
ACTS = {
	"Verify": (VERIFIED, "Verified"),
	"Reject": (REJECTED, "Rejected"),
	"Withdraw": (WITHDRAWN, "Rejected"),
}
# Set only by the signed acts.
STATUS_FIELDS = ("status", "reason", "decided_by", "decided_at", "expiry_alerted")
EXPIRY_ALERT = "Customer Licence Expiring"
SAVEPOINT = "licence_alert"  # a savepoint name has no spaces
# On a customer: it buys without a licence. Set only by QA's signed decision.
EXEMPT_FIELD = "mds_buys_without_licence"
EXEMPT_ACTS = {"Exempt From Licence": 1, "Require Licence": 0}
CUSTOM_FIELDS = {
	"Customer": [
		{
			"fieldname": EXEMPT_FIELD,
			"label": "Buys Without a Licence",
			"fieldtype": "Check",
			"insert_after": "customer_group",
			"read_only": 1,
			"no_copy": 1,
			"description": (
				"Set by QA's signed decision only, for a buyer that needs no licence to buy medical "
				"devices, such as a hospital buying for its own use."
			),
		}
	]
}


def apply_customer_licence_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)


def require(condition: object, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


def date_text(value: object) -> str:
	return frappe.format_value(value, {"fieldtype": "Date"})


# --- The signed acts --------------------------------------------------------------------------


def verify(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, "Verify", password, reason)


def reject(doc: Document, password: str, reason: str | None = None) -> None:
	decide(doc, "Reject", password, reason)


def decide(doc: Document, act: str, password: str, reason: str | None) -> None:
	"""QA verifies or rejects a licence Sales recorded: its submit."""
	doc = locked(doc.name)
	status, meaning = ACTS[act]
	require(is_qa(), _("Only QA verifies a customer licence."), frappe.PermissionError)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(reason, _("Say why the licence is {0}.").format(_(status).lower()))
	if status == VERIFIED:
		require(
			doc.licence_type and doc.licence_number and doc.issuing_authority and doc.licence_copy,
			_("Record the licence's type, number, issuing authority and a copy before it is verified."),
		)
		require(
			doc.valid_from and doc.valid_until and getdate(doc.valid_from) <= getdate(doc.valid_until),
			_("The licence is valid from a day up to a later one; check its dates."),
		)
		require(
			getdate(doc.valid_until) >= getdate(today()),
			_("The licence expired on {0}; it cannot be verified.").format(date_text(doc.valid_until)),
		)
	record(doc, status, reason)
	sign(doc, meaning, act, password, reason, permission="read")
	doc.flags.customer_licence = True
	# No role holds Frappe's submit permission on a licence; the signed act is the submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def withdraw(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA withdraws a verified licence the authority has suspended or cancelled."""
	doc = locked(doc.name)
	status, meaning = ACTS["Withdraw"]
	require(is_qa(), _("Only QA withdraws a customer licence."), frappe.PermissionError)
	require(
		doc.docstatus == 1 and doc.status == VERIFIED,
		_("Only a verified licence is withdrawn; {0} is not one.").format(doc.name),
	)
	require(reason, _("Say why the licence is withdrawn."))
	record(doc, status, reason)
	sign(doc, meaning, "Withdraw", password, reason, permission="read")
	doc.flags.customer_licence = True
	doc.flags.audit_reason = reason  # the audit trail records the withdrawal under QA's reason
	doc.flags.ignore_permissions = True  # as for the submit: the signed act is the only change
	doc.save()


def record(doc: Document, status: str, reason: str) -> None:
	doc.status = status
	doc.reason = reason
	doc.decided_by = frappe.session.user
	doc.decided_at = now_datetime()


def locked(name: str) -> Document:
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)  # one act at a time
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc


# --- Licensed to buy --------------------------------------------------------------------------


def licences_valid_on(customer: str, on: object) -> list[frappe._dict]:
	"""The customer's verified licences valid on a day, latest expiry first."""
	day = getdate(on)
	return frappe.get_all(
		DOCTYPE,
		filters={
			"customer": customer,
			"docstatus": 1,
			"status": VERIFIED,
			"valid_from": ["<=", day],
			"valid_until": [">=", day],
		},
		fields=["name", "licence_type", "licence_number", "valid_until"],
		order_by="valid_until desc",
	)


def buys_without_licence(customer: str) -> bool:
	"""Whether QA has decided the customer buys without a licence."""
	return bool(frappe.db.get_value("Customer", customer, EXEMPT_FIELD))


def require_licence(customer: str | None, on: object, record_type: str) -> None:
	"""Refuse unless the customer is licensed on the record's date and on the day it is made."""
	if not customer or buys_without_licence(customer):
		return
	for day in sorted({getdate(on or today()), getdate(today())}):
		if not licences_valid_on(customer, day):
			frappe.throw(
				_(
					"Customer {0} holds no licence verified by QA that is valid on {1}, so no {2} is made "
					"for it. Sales records the customer's licence and QA verifies it."
				).format(customer, date_text(day), _(record_type).lower()),
				title=_("Customer not licensed"),
			)


def is_submit(doc: Document) -> bool:
	return getattr(doc, "_action", None) == "submit"


def guard_sales_order(doc: Document, method: str | None = None) -> None:
	"""Sales Order before_validate: submitted only for a customer licensed on its date."""
	if is_submit(doc):
		require_licence(doc.customer, doc.transaction_date, "Sales Order")


def guard_order_update(doc: Document, method: str | None = None) -> None:
	"""Sales Order before_update_after_submit: ERPNext's Update Items changes the lines of a
	submitted order past its validation (it marks the order ignore_validate_update_after_submit,
	having written the lines already, so they cannot be compared); the customer then needs its
	licence today, as for the supplier side (supplier_approval.guard_order_update). Otherwise only a
	line added or raised sells more; any other change of a submitted order sells nothing new."""
	if doc.flags.ignore_validate_update_after_submit:
		require_licence(doc.customer, today(), "Sales Order")
		return
	before = doc.get_doc_before_save()
	old = {row.name: (row.item_code, flt(row.qty)) for row in (before.items if before else [])}
	sells_more = any(
		row.name not in old or old[row.name][0] != row.item_code or flt(row.qty) > old[row.name][1]
		for row in doc.items
	)
	if sells_more:
		require_licence(doc.customer, today(), "Sales Order")


def guard_sales_invoice(doc: Document, method: str | None = None) -> None:
	"""Sales Invoice and POS Invoice before_validate: an invoice that updates stock delivers the
	goods without a delivery note, so it needs the licence a delivery needs."""
	if is_submit(doc) and doc.get("update_stock") and not doc.get("is_return"):
		require_licence(doc.customer, doc.posting_date, doc.doctype)


def guard_customer_rename(doc: Document, method: str, old: str, new: str, merge: bool = False) -> None:
	"""Customer before_rename: a customer with a decided licence or exemption keeps its name, so a
	verified licence never passes to another party, or a renamed one, without QA's signature."""
	decided = frappe.db.exists(DOCTYPE, {"customer": old, "docstatus": 1}) or buys_without_licence(old)
	if decided:
		frappe.throw(
			_(
				"{0} holds a licence or an exemption QA has decided, so it is not renamed or merged. "
				"Record a new customer and its licence instead."
			).format(old),
			frappe.PermissionError,
		)


def guard_delivery_note(doc: Document, method: str | None = None) -> None:
	"""Delivery Note before_validate: delivered only to a customer licensed on its date. A return
	takes goods back and is not a sale."""
	if is_submit(doc) and not doc.get("is_return"):
		require_licence(doc.customer, doc.posting_date, "Delivery Note")


# --- Buying without a licence -----------------------------------------------------------------


def exempt_from_licence(doc: Document, password: str, reason: str | None = None) -> None:
	decide_exemption(doc, "Exempt From Licence", password, reason)


def require_licence_again(doc: Document, password: str, reason: str | None = None) -> None:
	decide_exemption(doc, "Require Licence", password, reason)


def decide_exemption(doc: Document, act: str, password: str, reason: str | None) -> None:
	"""QA's signed decision that a customer buys without a licence, or needs one again."""
	frappe.db.get_value("Customer", doc.name, "name", for_update=True)  # one decision at a time
	doc = frappe.get_doc("Customer", doc.name)
	doc.check_permission("read")
	exempt = EXEMPT_ACTS[act]
	require(is_qa(), _("Only QA decides which customers buy without a licence."), frappe.PermissionError)
	require(
		bool(doc.get(EXEMPT_FIELD)) != bool(exempt),
		_("{0} already buys without a licence.").format(doc.name)
		if exempt
		else _("{0} already needs a licence.").format(doc.name),
	)
	require(reason, _("Say why."))
	doc.set(EXEMPT_FIELD, exempt)
	sign(doc, "Approved", act, password, reason, permission="read")
	doc.flags.customer_licence = True
	# Only this signed decision sets the field; Sales, who edit the customer, may not.
	doc.save(ignore_permissions=True)


def guard_customer(doc: Document, method: str | None = None) -> None:
	"""Customer validate: whether it buys without a licence is set only by QA's signed decision."""
	if doc.flags.customer_licence:
		return
	before = doc.get_doc_before_save()
	was = bool(before.get(EXEMPT_FIELD)) if before else False
	if bool(doc.get(EXEMPT_FIELD)) != was:
		frappe.throw(
			_("Whether a customer buys without a licence is decided by QA's signature."),
			frappe.PermissionError,
		)


def customer_signed_content(doc: Document) -> dict:
	"""What QA's signature on a customer binds (compliance.esign.SIGNED_CONTENT): whether it buys
	without a licence; not the addresses and contacts Sales keeps up to date. The name is left out,
	as esign leaves it out everywhere (the signature names the record)."""
	return {EXEMPT_FIELD: int(bool(doc.get(EXEMPT_FIELD)))}


# --- The customer's form ----------------------------------------------------------------------


def customer_onload(doc: Document, method: str | None = None) -> None:
	"""Customer onload: the licences that let it buy today, for the form to show."""
	doc.set_onload(
		"customer_licence",
		{
			"exempt": buys_without_licence(doc.name),
			"valid": licences_valid_on(doc.name, today()),
		},
	)


# --- Expiry alert -----------------------------------------------------------------------------


def send_expiry_alerts() -> None:
	"""Daily: alert Sales and QA once when a verified licence expires within the days set in
	Quality Settings, including a day the alerts did not run, unless a renewed licence of the
	customer is verified already."""
	notification = frappe.get_doc("Notification", EXPIRY_ALERT)
	soon = add_days(today(), quality_settings.value("customer_licence_warning_days"))
	for row in frappe.get_all(
		DOCTYPE,
		filters={"docstatus": 1, "status": VERIFIED, "expiry_alerted": 0, "valid_until": ["<=", soon]},
		fields=["name", "customer", "valid_until"],
		order_by="valid_until asc",
	):
		if frappe.db.exists(
			DOCTYPE,
			{
				"customer": row.customer,
				"docstatus": 1,
				"status": VERIFIED,
				# A renewal carries on without a gap: valid by the day after this one ends.
				"valid_from": ["<=", add_days(row.valid_until, 1)],
				"valid_until": [">", row.valid_until],
			},
		):
			continue  # renewed: the customer's next licence is verified
		alert(notification, row.name)


def alert(notification: Document, name: str) -> None:
	# One licence's failure is logged and retried the next day; it does not hold back the others.
	frappe.db.savepoint(SAVEPOINT)
	try:
		notification.send(frappe.get_doc(DOCTYPE, name))
		# Bookkeeping of the alert, kept out of the signed record (esign.SYSTEM_MAINTAINED).
		frappe.db.set_value(DOCTYPE, name, "expiry_alerted", 1, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"Licence expiry alert for {name} could not be sent")
	else:
		frappe.db.release_savepoint(SAVEPOINT)
