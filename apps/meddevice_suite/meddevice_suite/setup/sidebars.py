"""Desk sidebars: inside each area the client uses, keep only the entries the
requirement uses and add the ones it needs that the owning app does not list.

Sidebars belong to Frappe, ERPNext and India Compliance, and `bench migrate`
re-syncs one whenever its app ships a newer version, so this runs on install
and after every migrate. Rows are written directly: saving a standard sidebar
in developer mode would export it into the owning app's source tree.

Every area the desk shows has its list, and the lists are allowlists: an entry
a future release adds stays out until it is checked against the requirement and
added here. A declared sidebar that a release renames or removes fails the tests
instead of being skipped. Module numbers refer to the
agreed 62-module scope in qa/requirements.toml.
"""

import frappe

SIDEBAR_SCOPE: dict[str, tuple[str, ...]] = {
	# Accounting (29 accounts and costing, 28 GST)
	"Accounts Setup": (
		"Chart of Accounts",
		"Chart of Cost Centers",
		"Company",
		"Fiscal Year",
		"Mode of Payment",
		"Payment Term",
		"Terms and Conditions",
		"Accounting Period",
		"Period Closing Voucher",
		"COA Importer",
		"Opening Invoice Tool",
		"Accounts Settings",
	),
	"Invoicing": (
		"Home",
		"Dashboard",
		"Customer",
		"Sales Invoice",
		"Credit Note",
		"Accounts Receivable",
		"Supplier",
		"Purchase Invoice",
		"Debit Note",
		"Accounts Payable",
		"Payment Entry",
		"Journal Entry",
		"Payment Reconciliation",
		"General Ledger",
		"Trial Balance",
		"Financial Reports",
		"Settings",
	),
	"Financial Reports": (
		"Balance Sheet",
		"Profit and Loss",
		"Cash Flow",
		"Trial Balance",
		"General Ledger",
		"Customer Ledger",
		"Supplier Ledger",
		"Accounts Receivable",
		"Accounts Payable",
		"AR Summary",
		"AP Summary",
		"Sales Register",
		"Purchase Register",
		"Item-wise sales Register",
		"Item-wise Purchase Register",
		"Gross Profit",
	),
	"Taxes": (
		"Sales Tax Template",
		"Purchase Tax Template",
		"Item Tax Template",
		"Tax Category",
		"Tax Withholding Category",
		"TDS Computation Summary",
		"Tax Withholding Details",
	),
	"Banking": (
		"Bank",
		"Bank Account",
		"Bank Reconciliation",
		"Bank Clearance",
		"Reconciliation Statement",
	),
	"GST India": (
		"Home",
		"Purchase Reconciliation Tool",
		"IMS",
		"GSTR-1",
		"GSTR-3B",
		"GST Settings",
		"India Compliance Account",
	),
	"Income Tax India": (
		"Income Tax India",
		"Tax Withholding Details-India",
		"TDS Computation Summary-India",
	),
	# Sales (10 enquiry, 11 quotation, 12 sales order and pricing)
	"CRM": (
		"Home",
		"Lead",
		"Opportunity",
		"Customer",
		"Contact",
		"Customer Group",
		"Sales Stage",
		"Lead Source",
		"Lead Details",
		"Sales Pipeline Analytics",
		"Opportunity Summary by Sales Stage",
		"CRM Settings",
	),
	"Selling": (
		"Home",
		"Dashboard",
		"Quotation",
		"Sales Order",
		"Sales Invoice",
		"Item",
		"Item Group",
		"Price List",
		"Item Price",
		"Pricing Rule",
		"Customer",
		"Customer Group",
		"Address",
		"Contact",
		"Terms Template",
		"Tax Template",
		"Sales Register",
		"Item-wise Sales Register",
		"Sales Analytics",
		"Pending SO Items For Purchase Request",
		"Sales Order Analysis",
		"Quotation Trends",
		"Sales Order Trends",
		"Item-wise Sales History",
		"Settings",
	),
	# Plan & source (4 vendors and rating, 13 planning, 14 purchasing, 15 returns)
	"Buying": (
		"Home",
		"Dashboard",
		"Material Request",
		"Request for Quotation",
		"Supplier Quotation",
		"Purchase Order",
		"Purchase Invoice",
		"Supplier",
		"Supplier Group",
		"Item",
		"Price List",
		"Address",
		"Contacts",
		"Supplier Quotation Comparison",
		"Purchase Order Analysis",
		"Requested Items to Order and Receive",
		"Items To Be Requested",
		"Procurement Tracker",
		"Purchase Analytics",
		"Item Wise Consumption",
		"Settings",
	),
	# Receiving, stores and dispatch (1, 7, 16-18, 27, 53, 55)
	"Stock": (
		"Home",
		"Dashboard",
		"Stock Entry",
		"Purchase Receipt",
		"Delivery Note",
		"Material Request",
		"Pick List",
		"Stock Reconciliation",
		"Packing Slip",
		"Quality Inspection",
		"Item",
		"Item Group",
		"Warehouse",
		"Unit of Measure (UOM)",
		"UOM Conversion Factor",
		"Batch No",
		"Serial and Batch Bundle",
		"Quality Inspection Template",
		"Delivery Trip",
		"Stock Ledger",
		"Stock Balance",
		"Quick Stock Balance",
		"Stock Projected Qty",
		"Stock Analytics",
		"Stock Ageing",
		"Warehouse Wise Stock Balance",
		"Item Shortage Report",
		"Serial No and Batch Traceability",
		"Batch-Wise Balance History",
		"Batch Item Expiry Status",
		"Requested Items To Be Transferred",
		"Itemwise Recommended Reorder Level",
		"Stock Settings",
	),
	# Production (2, 3, 13, 19-21, downtime for 38)
	"Manufacturing": (
		"Home",
		"Dashboard",
		"BOM",
		"Work Order",
		"Job Card",
		"Stock Entry",
		"Item Lead Time",
		"Production Plan",
		"Forecasting",
		"Master Production Schedule",
		"Sales Forecast",
		"Production Planning Report",
		"BOM Update Tool",
		"BOM Comparison Tool",
		"Downtime Entry",
		"Work Order Summary",
		"Quality Inspection Summary",
		"Downtime Analysis",
		"Job Card Summary",
		"BOM Search",
		"Production Analytics",
		"Work Order Consumed Materials",
		"Item",
		"Warehouse",
		"Operation",
		"Workstation",
		"Workstation Type",
		"Plant Floor",
		"Routing",
		"Settings",
	),
	# Inspection and feedback (6, 20, 25, 42); NCR and CAPA are built in this app
	"Quality": (
		"Home",
		"Quality Inspection",
		"Quality Feedback",
		"Feedback Template",
		"Quality Inspection Template",
	),
	# Equipment master and maintenance (8, 38)
	"Assets": (
		"Home",
		"Dashboard",
		"Asset",
		"Asset Category",
		"Location",
		"Asset Movement",
		"Asset Maintenance Team",
		"Asset Maintenance",
		"Asset Maintenance Log",
		"Asset Repair",
		"Fixed Asset Register",
	),
	"Organization": (
		"Company",
		"Letter Head",
		"Department",
	),
	"ERPNext Settings": (
		"Global Defaults",
		"System Settings",
		"Accounts Settings",
		"Selling Settings",
		"Buying Settings",
		"Stock Settings",
		"Manufacturing Settings",
		"CRM Settings",
	),
	# Administration, readable by system managers only (47, 48, 51, 52, print formats for 23, 26)
	"Users": (
		"Home",
		"User",
		"Role",
		"User Permission",
		"Permission Manager",
		"Permission Inspector",
		"Activity Log",
		"Access Log",
		"Permission Log",
	),
	"Printing": (
		"Print Format",
		"Print Heading",
		"Print Format Builder",
		"Print Settings",
	),
	"Email": (
		"Email Account",
		"Email Domain",
		"Email Template",
		"Email Queue",
	),
	"Data": (
		"Data Import",
		"Data Export",
		"Backups",
	),
	"System": (
		"Health Report",
		"Notification",
		"Notification Settings",
		"Scheduled Job Type",
		"Scheduled Job Log",
		"Error Log",
		"Log Settings",
	),
}

# Entries the requirement needs that the shipped sidebar does not list:
# (label, link_type, link_to).
SIDEBAR_ADDITIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
	"Organization": (("Employee", "DocType", "Employee"),),  # employee master, module 9
	# lot status and release (modules 16, 17)
	"Stock": (
		("Lot Release", "DocType", "Lot Release"),
		("Lots Awaiting Release", "Report", "Lots Awaiting Release"),
		("Stock by Lot Status", "Report", "Stock by Lot Status"),
		("Lots Taken Out of Expiry Order", "Report", "Lots Taken Out of Expiry Order"),  # module 18
	),
	"Quality": (
		("Lot Release", "DocType", "Lot Release"),
		("Lots Awaiting Release", "Report", "Lots Awaiting Release"),
		("Lots Taken Out of Expiry Order", "Report", "Lots Taken Out of Expiry Order"),  # module 18
		("Supplier Approval", "DocType", "Supplier Approval"),  # approved suppliers (module 4)
		("Supplier Rating", "DocType", "Supplier Rating"),  # supplier rating (module 4)
		("Quality Settings", "DocType", "Quality Settings"),  # adjustable values (URS-PLT-012)
		("Reminder", "DocType", "Reminder"),  # reminders and next due (module 60)
		("Reminder Schedule", "DocType", "Reminder Schedule"),
		("File Category", "DocType", "File Category"),
		("Customer Licence", "DocType", "Customer Licence"),  # customer licences (module 5)
		("Equipment History Card", "Report", "Equipment History Card"),  # module 62
		("Training Matrix", "DocType", "Training Matrix"),  # training (module 37)
		("Training Record", "DocType", "Training Record"),
		("Training Status", "Report", "Training Status"),
		("Risk Management File", "DocType", "Risk Management File"),  # risk management (module 39)
		("Risk Assessment", "DocType", "Risk Assessment"),
		("Risk Management Report", "DocType", "Risk Management Report"),
		("Risk Review", "DocType", "Risk Review"),
		("Risk Acceptability Policy", "DocType", "Risk Acceptability Policy"),
		("Risk Register", "Report", "Risk Register"),
		("Regulatory Licence", "DocType", "Regulatory Licence"),  # regulatory licences (module 41)
		("Regulatory Licence Type", "DocType", "Regulatory Licence Type"),
		("Device Master Record", "DocType", "Device Master Record"),  # device master record (module 31)
	),
	# The device master record production works to (module 31)
	"Manufacturing": (("Device Master Record", "DocType", "Device Master Record"),),
	"Buying": (
		("Supplier Approval", "DocType", "Supplier Approval"),
		("Supplier Rating", "DocType", "Supplier Rating"),
	),
	# customer licences (module 5)
	"Selling": (
		("Customer Licence", "DocType", "Customer Licence"),
		("Customer Licence Type", "DocType", "Customer Licence Type"),
	),
	"Assets": (
		("Equipment History Card", "Report", "Equipment History Card"),  # module 62
		("Calibration Record", "DocType", "Calibration Record"),  # module 38
		("Maintenance Record", "DocType", "Maintenance Record"),
		("Equipment Qualification", "DocType", "Equipment Qualification"),  # module 40
		("Equipment Care Schedule", "Report", "Equipment Care Schedule"),
	),
}

# Each job's own work list and its tasks (module 59), in every business area.
WORK_ENTRIES = (
	("My Work", "Report", "My Work"),
	("Work Task", "DocType", "Work Task"),
	("Important File", "DocType", "Important File"),  # important files repository (module 61)
)
BUSINESS_AREAS = (
	"Selling",
	"Buying",
	"Stock",
	"Manufacturing",
	"Quality",
	"Assets",
	"Organization",
	"Invoicing",
)
SIDEBAR_ADDITIONS.update({area: (*WORK_ENTRIES, *SIDEBAR_ADDITIONS.get(area, ())) for area in BUSINESS_AREAS})


def apply_sidebar_scope() -> None:
	changed = False
	for sidebar, keep in SIDEBAR_SCOPE.items():
		if frappe.db.exists("Workspace Sidebar", sidebar):
			additions = SIDEBAR_ADDITIONS.get(sidebar, ())
			changed |= scope_sidebar(sidebar, set(keep) | {label for label, _, _ in additions})
			changed |= add_entries(sidebar, additions)
	if changed:
		frappe.cache.delete_key("bootinfo")


def scope_sidebar(sidebar: str, keep: set[str]) -> bool:
	"""Remove link entries outside `keep`, then section headings left without entries."""
	rows = sidebar_rows(sidebar)
	remove = {row.name for row in rows if row.type == "Link" and row.label not in keep}
	remaining = [row for row in rows if row.name not in remove]

	for position, row in enumerate(remaining):
		if row.type == "Section Break" and not has_entries(remaining, position):
			remove.add(row.name)

	if remove:
		frappe.db.delete("Workspace Sidebar Item", {"name": ("in", list(remove))})
	return bool(remove)


def has_entries(rows: list, section_position: int) -> bool:
	"""Whether the section heading at `section_position` is followed by any of its entries."""
	following = rows[section_position + 1 : section_position + 2]
	return bool(following) and following[0].type == "Link" and bool(following[0].child)


def add_entries(sidebar: str, entries: tuple[tuple[str, str, str], ...]) -> bool:
	rows = sidebar_rows(sidebar)
	present = {row.label for row in rows}
	idx = max((row.idx for row in rows), default=0)
	added = False
	for label, link_type, link_to in entries:
		if label in present:
			continue
		idx += 1
		frappe.get_doc(
			{
				"doctype": "Workspace Sidebar Item",
				"parent": sidebar,
				"parenttype": "Workspace Sidebar",
				"parentfield": "items",
				"idx": idx,
				"type": "Link",
				"label": label,
				"link_type": link_type,
				"link_to": link_to,
			}
		).db_insert()
		added = True
	return added


def sidebar_rows(sidebar: str) -> list:
	return frappe.get_all(
		"Workspace Sidebar Item",
		filters={"parent": sidebar, "parenttype": "Workspace Sidebar"},
		fields=["name", "idx", "type", "label", "child"],
		order_by="idx",
	)
