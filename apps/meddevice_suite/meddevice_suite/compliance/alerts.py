"""Alerts and notifications (module 51; D2 "Alerts & Notifications").

The alerts are Frappe's standard Notifications, shipped in compliance/notification
and synced on every migrate, so they cannot be edited on a site. They notify in
the desk (the bell and the notification list); email is added once the client's
mail server is set up with hosting (F11). Alerts for calibration, training,
licences, CAPA and certificates come with the modules that own those records.
"""

import frappe

ALERTS = (
	"Lot Expiring Soon",
	"Lot Expired",
	"Quotation Awaiting Price Approval",
	"Purchase Order Awaiting Approval",
	"Quotation Price Approval Decided",
	"Purchase Order Approval Decided",
)


def apply_alerts() -> None:
	# The daily expiry alerts look batches up by expiry date, which ERPNext does not index.
	frappe.db.add_index("Batch", ["expiry_date"])
