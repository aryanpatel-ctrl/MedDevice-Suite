"""Which record types are regulated and therefore audited (URS-CMP-013).

Standard record types are declared here by name. Every record type this app
builds is regulated by default, apart from the compliance records that are
themselves the evidence. Accounting documents (invoices, payments, journal
entries) are left to India Compliance's audit trail, which protects them for
the Companies Act; stock and asset postings are GMP records and audited here
as well.
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
		# GMP transactions
		"Material Request",
		"Purchase Order",
		"Purchase Receipt",
		"Quality Inspection",
		"Stock Entry",
		"Stock Reconciliation",
		"Work Order",
		"Job Card",
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
	}
)

# Compliance records that are the evidence themselves, each with its own chain.
SELF_RECORDING: frozenset[str] = frozenset({"Audit Event", "Electronic Signature"})


def is_regulated(doctype: str, module: str | None) -> bool:
	if doctype in SELF_RECORDING:
		return False
	if doctype in REGULATED_DOCTYPES:
		return True
	return bool(module) and frappe.local.module_app.get(frappe.scrub(module)) == APP
