# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AuditEvent(Document):
	"""One entry of the audit trail. Written only by `meddevice_suite.compliance.audit`.

	Once recorded it can never change: any save after the first, and any delete,
	is refused for every user, whatever their permissions.
	"""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("An audit event cannot be changed."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("An audit event cannot be deleted."), frappe.PermissionError)


def on_doctype_update():
	# A record's history, a user's actions and a date range are the reviews an
	# auditor runs; each stays an index lookup at millions of entries.
	frappe.db.add_index("Audit Event", ["reference_doctype", "reference_name", "event_at"])
	frappe.db.add_index("Audit Event", ["user", "event_at"])
	frappe.db.add_index("Audit Event", ["event_at"])
	# The newest event of a record is the one no other event follows.
	frappe.db.add_index("Audit Event", ["previous_event"])
