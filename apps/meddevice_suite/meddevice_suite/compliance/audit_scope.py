"""Which record types are regulated and therefore audited (URS-CMP-013, URS-CMP-041).

Standard record types are declared here by name. Every record type this app
builds is regulated by default, apart from the compliance records that are
themselves the evidence. Accounting documents (invoices, payments, journal
entries) are left to India Compliance's audit trail, which protects them for
the Companies Act; stock and asset postings are GMP records and audited here
as well.

Every standard record type this app hooks into, adds fields to, or links to from
its own record types is either declared here or named in NOT_AUDITED with the
reason it is left out (compliance/test_audit_scope.py holds the two lists to that),
so a new module cannot leave a regulated record unaudited without a reviewed reason.
"""

import frappe

APP = "meddevice_suite"

REGULATED_DOCTYPES: frozenset[str] = frozenset(
	{
		# Master data
		"Item",
		"BOM",
		"Routing",
		"Operation",
		"Workstation",
		"Supplier",
		"Customer",
		"Warehouse",
		"Batch",
		"Quality Inspection Template",
		"Asset",
		"Employee",
		"Designation",  # what a job is trained on hangs on it (module 37)
		# GMP transactions
		"Quotation",
		# The customer's order: what was ordered, how many and by when, and how far it is
		# delivered; the order end of order -> dispatch -> lot traceability (ISO 13485 7.2.2,
		# 7.5.9; found in the foundation review of 2026-10-12).
		"Sales Order",
		"Material Request",
		"Purchase Order",
		"Purchase Receipt",
		"Quality Inspection",
		"Stock Entry",
		"Stock Reconciliation",
		"Work Order",
		"Job Card",
		"Serial and Batch Bundle",
		"Pick List",
		"Delivery Note",
		"Packing Slip",
		"Asset Maintenance Log",
		"Asset Repair",
		# Access control and system settings (21 CFR 11.10(d), (g))
		"User",
		"Role",
		"Role Profile",
		"User Permission",
		"Custom DocPerm",
		"System Settings",
		"Stock Settings",
		# Configuration of the site (URS-CMP-038; master plan section 10: the audit trail covers
		# masters, records and configuration)
		"Custom Field",
		"Property Setter",
		"Client Script",
		"Server Script",
		"Print Format",
		"Workflow",
		"Notification",
		"Manufacturing Settings",
		"Buying Settings",
		"Selling Settings",
		"Print Settings",
		# The legal manufacturer's name and the defaults every stock and production record takes
		# (stores, accounts); set up by the administrator (found in the foundation review of 2026-10-12).
		"Company",
	}
)

# Compliance records that are the evidence themselves, written only by their services: the audit
# events and signatures, each with its own chain, and the anchors of those chains and what they found.
SELF_RECORDING: frozenset[str] = frozenset(
	{"Audit Event", "Electronic Signature", "Audit Anchor", "Audit Trail Finding"}
)

# Standard record types this app touches that are not audited here, and why (URS-CMP-041).
NOT_AUDITED: dict[str, str] = {
	"Sales Invoice": "Accounting document: India Compliance's audit trail keeps it (Companies Act).",
	"POS Invoice": "Accounting document: India Compliance's audit trail keeps it (Companies Act).",
	"Purchase Invoice": "Accounting document: India Compliance's audit trail keeps it (Companies Act).",
	"Stock Ledger Entry": (
		"A posting ERPNext makes from a submitted stock transaction, which is audited with its lines; "
		"no person enters or edits one."
	),
	"Asset Maintenance": (
		"Refused by this app (qms/calibration.py): equipment is maintained on its own signed records."
	),
	"Supplier Scorecard": (
		"Refused by this app (master_data/supplier_rating.py): suppliers are rated by the Supplier Rating."
	),
	"BOM Update Log": (
		"A run of ERPNext's BOM Update Tool: replacing a bill inside approved ones is refused "
		"(master_data/versioning.py); a cost update changes costs only, which are accounting."
	),
	"Log Settings": (
		"Its one regulated value, how long the login history is kept, cannot be shortened and is "
		"restored on every migrate (compliance/retention.py)."
	),
	"File": (
		"The register of an attachment: the record it is attached to is audited, and an evidence "
		"file cannot be changed or deleted (compliance/evidence_files.py)."
	),
	"Desktop Layout": "A person's arrangement of the desk icons; the desk areas themselves are code (setup/desk.py).",
	"DocType": (
		"Names which record type an entry refers to; record types change only with a release of "
		"this app or of ERPNext, under change control."
	),
}


def is_regulated(doctype: str, module: str | None) -> bool:
	if doctype in SELF_RECORDING:
		return False
	if doctype in REGULATED_DOCTYPES:
		return True
	return bool(module) and frappe.local.module_app.get(frappe.scrub(module)) == APP
