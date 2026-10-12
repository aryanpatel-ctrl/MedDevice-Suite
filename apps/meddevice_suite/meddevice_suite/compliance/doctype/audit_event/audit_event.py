# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AuditEvent(Document):
	"""One entry of the audit trail. Written only by `meddevice_suite.compliance.audit`.

	Once recorded it can never change: any save after the first, and any delete,
	is refused for every user, whatever their permissions.

	Nor can one be inserted any other way (21 CFR 11.10(e)). No role may create audit events,
	but a system manager can grant that on a site, and code can skip permissions: either could
	add an entry attributing an act to another person. The audit trail marks the event it
	inserts in the document's flags, which a request cannot set.
	"""

	def before_insert(self) -> None:
		# Runs for every insert, even one that skips permissions and validation.
		if not self.flags.recorded_by_audit_trail:
			frappe.throw(_("An audit event is recorded only by the audit trail."), frappe.PermissionError)

	def validate(self) -> None:
		if not self.is_new():
			frappe.throw(_("An audit event cannot be changed."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("An audit event cannot be deleted."), frappe.PermissionError)


def on_doctype_update() -> None:
	# A record's history, a user's actions and a date range are the reviews an
	# auditor runs; each stays an index lookup at millions of entries.
	frappe.db.add_index("Audit Event", ["reference_doctype", "reference_name", "event_at"])
	frappe.db.add_index("Audit Event", ["user", "event_at"])
	frappe.db.add_index("Audit Event", ["event_at"])
	# The newest event of a record is the one no other event follows.
	frappe.db.add_index("Audit Event", ["previous_event"])
