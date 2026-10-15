"""A lot's dates (module 18; URS-STK-011, URS-STK-012; foundation hardening plan H6, decided
2026-10-14; ISO 13485 4.2.5, 7.5.8, 7.5.11; 21 CFR 11.10(e), 11.50).

A lot's manufacturing and expiry dates decide where its stock may go: the expiry date is what first
expiry, first out and the refusal of an expired lot read (`expiry.py`), what the expiry alerts and
reminders follow, and what the label and the certificate of the lot carry. ERPNext lets anyone who
may write a lot change them at any time, so a lot could be made to look fresh, or a lot taken out of
expiry order, by editing it.

Before the lot has stock its dates are what ERPNext sets on the receipt or the work order and may be
edited there, as the lot is being made. From its first stock posting (a stock ledger entry or a
submitted bundle naming it, cancelled or not: the dates were in force while that stock was on the
books) they are fixed (`guard_batch`), on the form and through the API. A wrong date found later is
corrected only by QA's signed act Correct Dates, which takes the reason and the new dates in the
signing dialog (`correct_dates`): the signature binds the lot and the dates QA approved
(`signed_content`), the lot shows who corrected it, when and why, and the audit trail records the
old and new dates with that reason.

A corrected expiry date takes effect at once, as any expiry date does: the stock ledger reads it
when the lot next moves, so a lot corrected to a date already past is refused for use from that
moment and leaves its store only to be kept apart, as `expiry.py` rules for an expired lot; and its
expiry alert is sent at once (`compliance.alerts`), so Stores moves it to the hold store and QA
decides it, without waiting for the next daily run.

The shelf life on the item is not a lot's date: it is controlled with the item (module 1, change
control) and only sets a new lot's expiry date.
"""

from datetime import date

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime, today

from meddevice_suite.compliance import alerts
from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.esign import sign
from meddevice_suite.lot_control import lot_status

ACT = "Correct Dates"
# The dates of the lot that its use and its records rest on.
DATE_FIELDS = ("manufacturing_date", "expiry_date")
CORRECTED_BY, CORRECTED_AT, CORRECTION_REASON = (
	"mds_dates_corrected_by",
	"mds_dates_corrected_at",
	"mds_dates_correction_reason",
)
# Set only by the signed act.
CORRECTION_FIELDS = (CORRECTED_BY, CORRECTED_AT, CORRECTION_REASON)

CUSTOM_FIELDS = {
	"Batch": [
		{
			"fieldname": "mds_dates_correction_section",
			"label": "Date Correction",
			"fieldtype": "Section Break",
			"insert_after": lot_status.STATUS_FIELDS[-1],
			"depends_on": f"eval:doc.{CORRECTED_AT}",
		},
		{
			"fieldname": CORRECTED_BY,
			"label": "Dates Corrected By",
			"fieldtype": "Link",
			"options": "User",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "mds_dates_correction_section",
		},
		{
			"fieldname": "mds_dates_correction_column",
			"fieldtype": "Column Break",
			"insert_after": CORRECTED_BY,
		},
		{
			"fieldname": CORRECTED_AT,
			"label": "Dates Corrected At",
			"fieldtype": "Datetime",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "mds_dates_correction_column",
		},
		{
			"fieldname": CORRECTION_REASON,
			"label": "Reason for Date Correction",
			"fieldtype": "Small Text",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": CORRECTED_AT,
			"description": "Set only by QA's signed Correct Dates.",
		},
	]
}


class LotDatesFixed(frappe.ValidationError):
	"""A change of the dates of a lot that has stock was refused."""


def apply_lot_dates_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def has_stock_history(lot: str) -> bool:
	"""Whether stock of the lot has ever been posted: a stock ledger entry naming it (an old-style
	lot), or a row of a submitted or cancelled bundle naming it (ERPNext v16 keeps the lot on the
	bundle, not on the entry). A goods receipt that names the lot is submitted with its bundle."""
	if frappe.db.exists("Stock Ledger Entry", {"batch_no": lot}):
		return True
	entry, bundle = frappe.qb.DocType("Serial and Batch Entry"), frappe.qb.DocType("Serial and Batch Bundle")
	return bool(
		frappe.qb.from_(entry)
		.join(bundle)
		.on(entry.parent == bundle.name)
		.select(entry.name)
		.where((entry.batch_no == lot) & (bundle.docstatus > 0))
		.limit(1)
		.run()
	)


def guard_batch(doc: Document, method: str | None = None) -> None:
	"""Batch validate: a lot's dates are fixed from its first stock posting (URS-STK-011), and the
	correction fields are set only by the signed act (URS-STK-012)."""
	if doc.is_new():
		for field in CORRECTION_FIELDS:
			doc.set(field, None)
		return
	if doc.flags.lot_dates_correction:
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	if any(
		not same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype)
		for field in CORRECTION_FIELDS
	):
		frappe.throw(
			_("A correction of a lot's dates is recorded only by QA's signed {0}.").format(_(ACT)),
			frappe.PermissionError,
		)
	changed = any(not same(before.get(field), doc.get(field), "Date") for field in DATE_FIELDS)
	if changed and has_stock_history(doc.name):
		frappe.throw(
			_(
				"Lot {0} has stock, so its manufacturing and expiry dates can no longer be edited; QA corrects a wrong date with the signed act {1}, giving the reason."
			).format(doc.name, _(ACT)),
			LotDatesFixed,
			title=_("Lot dates fixed"),
		)


def batch_onload(doc: Document, method: str | None = None) -> None:
	# The form shows the dates read-only and offers QA the signed correction once they are fixed.
	if not doc.is_new():
		doc.set_onload("dates_fixed", has_stock_history(doc.name))


def correct_dates(
	doc: Document,
	password: str,
	reason: str | None = None,
	manufacturing_date: str | None = None,
	expiry_date: str | None = None,
) -> None:
	"""Signed act (QA): correct the dates of a lot, saying why (URS-STK-012). A date not given stays
	as it is. The lot is row-locked, so a movement posted at the same moment reads the dates before or
	after the correction, never half of it."""
	lot = lot_status.locked(doc.name)
	require(lot_status.is_qa(), _("Only QA corrects the dates of a lot."), frappe.PermissionError)
	reason = lot_status.clean_reason(reason)
	made = as_date(manufacturing_date or lot.manufacturing_date)
	expires = as_date(expiry_date or lot.expiry_date)
	require(
		not (made and made > getdate(today())),
		_("Lot {0} cannot have been made after today.").format(lot.name),
	)
	require(
		not (made and expires and expires < made),
		_("Lot {0} cannot expire before it was made.").format(lot.name),
	)
	require(
		not same(lot.manufacturing_date, made, "Date") or not same(lot.expiry_date, expires, "Date"),
		_("Lot {0} already has these dates; there is nothing to correct.").format(lot.name),
	)
	lot.update(
		{
			"manufacturing_date": made,
			"expiry_date": expires,
			CORRECTED_BY: frappe.session.user,
			CORRECTED_AT: now_datetime(),
			CORRECTION_REASON: reason,
		}
	)
	# Signed with the corrected dates on the lot, so the signature binds what QA approved.
	sign(lot, "Approved", ACT, password, reason, permission="read")
	lot.flags.lot_dates_correction = True
	# The audit trail's update event carries the reason, as every change to a regulated record does.
	lot.flags.audit_reason = reason
	# QA's role reads lots and does not write them; this change is the signed act itself, made after
	# the checks above and recorded in the audit trail under QA, with the reason on the lot.
	lot.save(ignore_permissions=True)
	# An expiry date already past, or within the warning, is alerted now, not at the next daily run.
	alerts.send_expiry_alerts([lot.name])


def signed_content(doc: Document) -> dict:
	"""What QA's signature on a lot binds (compliance.esign.SIGNED_CONTENT): the lot and its dates,
	not its quantity or status, which move on with its stock and QA's decisions."""
	dates = {field: as_date(doc.get(field)) for field in DATE_FIELDS}
	return {
		"item": doc.get("item"),
		**{field: str(value) if value else None for field, value in dates.items()},
	}


def as_date(value: object) -> date | None:
	# Frappe's getdate gives today for no value; a lot without a date has none.
	return getdate(value) if value else None


def require(condition: object, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)
