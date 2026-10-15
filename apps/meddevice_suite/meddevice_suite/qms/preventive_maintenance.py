"""Preventive maintenance (module 38; URS-EQP-004; DFD 9D D3 maintenance schedule, D4 maintenance
execution; ISO 13485 6.3).

A Maintenance Record holds its checks, each OK or not, what was done and the downtime. It is
satisfactory only when every check is OK; only a satisfactory record sets the next due date, from
the interval QA approved on the equipment. Completed only by its performer's signature (Maintenance
or QA), never deleted, and corrected only by QA's signed Correct (plan D-29,
`equipment_care.correct`), which cancels it and opens its amendment.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, today

from meddevice_suite.compliance.checks import require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import equipment

DOCTYPE = "Maintenance Record"
SATISFACTORY, NOT_SATISFACTORY = "Satisfactory", "Not satisfactory"
PERFORMERS = frozenset({"Maintenance User", "Quality Manager"})
# Set only by the signed act. A draft never holds them, though Frappe's Amend copies them from the
# cancelled record.
SET_BY_ACTS = ("interval_days", "next_due_date", "completed_by")


def only_by_signature(doc: Document, method: str | None = None) -> None:
	"""before_validate: completed (submitted) only by the signed act."""
	if doc.get("_action") == "submit" and not doc.flags.maintenance_signed:
		frappe.throw(_("Maintenance is completed by its performer's signature."), frappe.PermissionError)


def evaluate(doc: Document, method: str | None = None) -> None:
	if doc.docstatus != 0:
		return
	for field in SET_BY_ACTS:
		doc.set(field, None)
	asset = frappe.db.get_value(
		"Asset", doc.asset, [equipment.APPROVAL_STATUS, equipment.STATUS, equipment.MAINTENANCE], as_dict=True
	)
	require(asset, _("Equipment {0} does not exist.").format(doc.asset))
	require(
		asset.get(equipment.APPROVAL_STATUS) == equipment.APPROVED,
		_("{0} is not approved equipment.").format(doc.asset),
	)
	require(asset.get(equipment.STATUS) != equipment.RETIRED, _("{0} is retired.").format(doc.asset))
	require(
		cint(asset.get(equipment.MAINTENANCE)),
		_("{0} does not need preventive maintenance.").format(doc.asset),
	)
	require(getdate(doc.performed_on) <= getdate(today()), _("Maintenance is recorded once it is done."))
	require(flt(doc.downtime_hours) >= 0, _("Downtime is a number of hours."))
	require(doc.checks, _("Record the checks made."))
	require(all(row.outcome for row in doc.checks), _("Each check is OK or not."))
	doc.result = SATISFACTORY if all(row.outcome == "OK" for row in doc.checks) else NOT_SATISFACTORY


def complete(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the performer completes the maintenance."""
	require(doc.docstatus == 0, _("{0} is completed already.").format(doc.name))
	require(
		PERFORMERS & set(frappe.get_roles()),
		_("Maintenance or QA completes maintenance."),
		frappe.PermissionError,
	)
	evaluate(doc)
	doc.interval_days = cint(frappe.db.get_value("Asset", doc.asset, equipment.MAINTENANCE_DAYS))
	require(doc.interval_days > 0, _("{0} has no maintenance interval.").format(doc.asset))
	doc.next_due_date = add_days(doc.performed_on, doc.interval_days) if doc.result == SATISFACTORY else None
	doc.completed_by = frappe.session.user
	sign(doc, "Verified", "Complete", password, reason)
	doc.flags.maintenance_signed = True
	doc.flags.ignore_permissions = True  # the signed act is the only submit
	doc.submit()
