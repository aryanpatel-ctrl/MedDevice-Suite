"""Alerts and notifications (module 51; D2 "Alerts & Notifications").

The alerts are Frappe's standard Notifications, shipped in compliance/notification.
They are configuration as code: every migrate re-imports them from the files, and
on a site they cannot be edited, switched off or deleted. They notify in the desk
(the bell and the notification list); email is added once the client's mail server
is set up with hosting (F11). Alerts for calibration, training, licences, CAPA and
certificates come with the modules that own those records.

Lot expiry is alerted by a daily job of this module rather than by Frappe's
"Days Before" event, which fires only on one exact date: a lot received already
inside the warning period, or a day the scheduler did not run, would never be
alerted. Each lot is alerted once as its expiry approaches and once when it has
expired; the lot remembers which alert it has had, and a changed expiry date
clears that so the new date is alerted again.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.modules.import_file import import_file
from frappe.utils import add_days, today

from meddevice_suite.compliance import quality_settings

MODULE = "Compliance"
EXPIRING_SOON = "Lot Expiring Soon"
EXPIRED = "Lot Expired"
# alert -> the module that ships it
ALERTS: dict[str, str] = {
	EXPIRING_SOON: MODULE,
	EXPIRED: MODULE,
	"Quotation Awaiting Price Approval": MODULE,
	"Purchase Order Awaiting Approval": MODULE,
	"Quotation Price Approval Decided": MODULE,
	"Purchase Order Approval Decided": MODULE,
	"Document Review Due": "QMS",  # periodic review of controlled documents (module 30)
	# Change control (module 36)
	"Change Request Awaiting Assessment": "QMS",
	"Change Request Awaiting Approval": "QMS",
	"Change Request Awaiting Management Approval": "QMS",
	"Change Request Decided": "QMS",
	"Change Effectiveness Check Due": "QMS",
	"Supplier Certificate Expiring": "Master Data",  # approved suppliers (module 4)
	"Customer Licence Expiring": "Master Data",  # customer licences (module 5)
	"Supplier Rating Below Threshold": "Master Data",  # supplier rating (module 4)
	"Reminder Overdue": "Work Management",  # reminders and next due (module 60)
	"Calibration Out of Tolerance": "QMS",  # calibration (module 38)
	"Regulatory Licence Expiring": "QMS",  # regulatory licences (module 41)
	"Device Master Record Out of Date": "QMS",  # device master record (module 31)
	"Audit Trail Problem Found": MODULE,  # removed audit entries found by the anchors (URS-CMP-042)
}
# On the lot: the last expiry alert it has had. Bookkeeping of this module only.
ALERT_FIELD = "mds_expiry_alert"
CUSTOM_FIELDS = {
	"Batch": [
		{
			"fieldname": ALERT_FIELD,
			"label": "Expiry Alert Sent",
			"fieldtype": "Select",
			"options": f"\n{EXPIRING_SOON}\n{EXPIRED}",
			"insert_after": "expiry_date",
			"read_only": 1,
			"no_copy": 1,
		}
	]
}


def apply_alerts() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	for name, module in ALERTS.items():
		# As shipped, whatever happened on the site since the last migrate. Frappe's import
		# keeps a site's choice to switch a standard alert off; a declared alert is always on.
		import_file(module, "Notification", name, force=True)
		frappe.db.set_value("Notification", name, "enabled", 1, update_modified=False)
	# The daily expiry lookup reads lots by expiry date, which ERPNext does not index.
	frappe.db.add_index("Batch", ["expiry_date"])


def send_expiry_alerts() -> None:
	"""Daily: alert each lot with stock once as its expiry approaches, and once when expired."""
	soon = add_days(today(), quality_settings.value("lot_expiry_warning_days"))
	for alert, expiry, already in (
		(EXPIRED, ["<", today()], [EXPIRED]),
		(EXPIRING_SOON, ["between", [today(), soon]], [EXPIRING_SOON, EXPIRED]),
	):
		notification = frappe.get_doc("Notification", alert)
		for lot in frappe.get_all(
			"Batch",
			filters=[
				# Frappe compares a missing date as the earliest one: a lot without one never expires.
				["expiry_date", "is", "set"],
				["expiry_date", *expiry],
				[ALERT_FIELD, "not in", already],
				["batch_qty", ">", 0],
				["disabled", "=", 0],
			],
			pluck="name",
			order_by="expiry_date asc",
		):
			send(notification, lot, alert)


def send(notification: Document, lot: str, alert: str) -> None:
	# One lot's failure is logged for the administrator and retried the next day;
	# it does not hold back the others.
	frappe.db.savepoint(ALERT_FIELD)
	try:
		notification.send(frappe.get_doc("Batch", lot))
		# Bookkeeping of the alert itself, not a change to the lot: not audited.
		frappe.db.set_value("Batch", lot, ALERT_FIELD, alert, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=ALERT_FIELD)
		frappe.log_error(title=f"Expiry alert for lot {lot} could not be sent")
	else:
		frappe.db.release_savepoint(ALERT_FIELD)


def reset_expiry_alert(doc: Document, method: str | None = None) -> None:
	"""Batch validate: a new expiry date is alerted afresh."""
	before = doc.get_doc_before_save()
	if before and before.expiry_date != doc.expiry_date:
		doc.set(ALERT_FIELD, None)


def protect(doc: Document, method: str | None = None) -> None:
	"""Notification before_validate and on_trash: the declared alerts change only by migrate."""
	if doc.name in ALERTS and not (
		frappe.flags.in_import or frappe.flags.in_install or frappe.flags.in_migrate
	):
		frappe.throw(
			_("The alert {0} is part of the validated configuration and cannot be changed here.").format(
				doc.name
			),
			frappe.PermissionError,
		)
