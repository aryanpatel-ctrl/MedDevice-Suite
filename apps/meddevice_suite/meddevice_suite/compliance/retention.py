"""Record retention (module 54; D3 "Retention & Archival").

How long each regulated record type is kept, and why. No regulated record can be
deleted at all (setup/roles.py, and the controllers of the compliance records),
so the schedule states the minimum period the records stay retrievable; the
archive export of module 54 copies them out for safekeeping.

ISO 13485 4.2.5 requires records to be kept for at least the lifetime of the
device and never less than two years from its release; EU MDR Article 10(8)
requires ten years. Ten years therefore covers a catheter or urine bag with a
shelf life of up to eight years: decided on the client's instruction of
2026-10-07.

Frappe clears its own login history (Activity Log) after 90 days. Login, logout,
failed-login and impersonation records show who had access to the records, so
they are kept for the same period instead.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from meddevice_suite.compliance.audit_scope import APP, REGULATED_DOCTYPES, SELF_RECORDING

QUALITY_BASIS = "ISO 13485 4.2.5; 21 CFR 820; EU MDR Art. 10(8); CDSCO MDR 2017"
ACCESS_BASIS = "21 CFR 11.10(d), (e), (g)"
EVIDENCE_BASIS = "21 CFR 11.10(c), (e); 11.70"

# category -> (years, legal basis, record types)
RETENTION_SCHEDULE: dict[str, tuple[int, str, tuple[str, ...]]] = {
	"Device and production records": (
		10,
		QUALITY_BASIS,
		(
			"Item",
			"BOM",
			"Routing",
			"Operation",
			"Workstation",
			"Warehouse",
			"Batch",
			"Quality Inspection Template",
			"Quality Inspection",
			"Lot Release",
			"Work Order",
			"Job Card",
			"Stock Entry",
			"Stock Reconciliation",
			"Serial and Batch Bundle",
			"Pick List",
		),
	),
	# Review of the customer's requirements before committing to supply (ISO 13485 7.2.2).
	"Sales records": (10, QUALITY_BASIS, ("Quotation",)),
	"Quality system records": (
		10,
		QUALITY_BASIS,
		# Quality Settings: the validated system's adjustable values and their history.
		("Controlled Document", "Document Revision", "Change Request", "Quality Settings"),
	),
	"Purchasing and supplier records": (
		10,
		QUALITY_BASIS,
		("Supplier", "Supplier Approval", "Material Request", "Purchase Order", "Purchase Receipt"),
	),
	"Distribution records": (
		10,
		QUALITY_BASIS,
		("Customer", "Customer Licence", "Customer Licence Type", "Delivery Note", "Packing Slip"),
	),
	"Equipment and personnel records": (
		10,
		QUALITY_BASIS,
		("Asset", "Asset Maintenance Log", "Asset Repair", "Employee"),
	),
	"Access control and system settings records": (
		10,
		ACCESS_BASIS,
		(
			"User",
			"Role",
			"Role Profile",
			"User Permission",
			"Custom DocPerm",
			"System Settings",
			"Stock Settings",
		),
	),
	"Compliance evidence": (
		10,
		EVIDENCE_BASIS,
		("Audit Event", "Electronic Signature", "Activity Log", "Record Archive"),
	),
}

LOGIN_HISTORY = "Activity Log"


def retention_years(doctype: str) -> int | None:
	for years, _basis, doctypes in RETENTION_SCHEDULE.values():
		if doctype in doctypes:
			return years
	return None


def scheduled_doctypes() -> list[str]:
	return [doctype for _years, _basis, doctypes in RETENTION_SCHEDULE.values() for doctype in doctypes]


def must_be_scheduled() -> set[str]:
	"""Every regulated record type, every record type this app defines, and the login history."""
	own = frappe.get_all(
		"DocType",
		filters={"module": ["in", frappe.get_module_list(APP)], "istable": 0, "custom": 0},
		pluck="name",
	)
	return set(REGULATED_DOCTYPES) | set(SELF_RECORDING) | set(own) | {LOGIN_HISTORY}


def login_history_days() -> int:
	return retention_years(LOGIN_HISTORY) * 366  # whole years, leap years included


def protect_login_history(doc: Document, method: str | None = None) -> None:
	"""Log Settings validate: Frappe's daily clean-up must never delete login history before its
	retention period ends. Frappe puts the 90-day default back if the entry is removed."""
	row = next((row for row in doc.logs_to_clear if row.ref_doctype == LOGIN_HISTORY), None)
	if not row or cint(row.days) < login_history_days():
		frappe.throw(
			_("The login history is kept for {0} days and cannot be cleared sooner.").format(
				login_history_days()
			),
			frappe.PermissionError,
		)


def apply_retention() -> None:
	"""Keep Frappe's login history for its scheduled period instead of 90 days."""
	days = login_history_days()
	settings = frappe.get_single("Log Settings")
	row = next((row for row in settings.logs_to_clear if row.ref_doctype == LOGIN_HISTORY), None)
	if row and row.days == days:
		return
	if row:
		row.days = days
	else:
		settings.append("logs_to_clear", {"ref_doctype": LOGIN_HISTORY, "days": days})
	settings.save(ignore_permissions=True)  # setup code
