# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AuditTrailFinding(Document):
	"""A problem the audit anchors showed, and the alert QA was sent about it (URS-CMP-042). Written
	only by `meddevice_suite.compliance.audit_anchor.alert`; one per distinct finding, which is what
	keeps the same finding from being alerted again.

	It is evidence of when the problem was found, protected as the anchors are: no role may create
	one, the service marks the one it inserts in the document's flags, which a request cannot set,
	and once recorded it can never change or be deleted, by any user.
	"""

	def before_insert(self) -> None:
		if not self.flags.recorded_by_audit_anchor:
			frappe.throw(
				_("An audit trail finding is recorded only by the audit trail's anchoring."),
				frappe.PermissionError,
			)

	def validate(self) -> None:
		if not self.is_new():
			frappe.throw(_("An audit trail finding cannot be changed."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("An audit trail finding cannot be deleted."), frappe.PermissionError)
