# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AuditAnchor(Document):
	"""The head of the audit trail or of the signatures at one moment (URS-CMP-040). Written only by
	`meddevice_suite.compliance.audit_anchor`, which also appends it to the anchor file.

	Protected as the Audit Event is: no role may create one, and the service marks the anchor it
	inserts in the document's flags, which a request cannot set, so none is made any other way;
	once recorded it can never change or be deleted, by any user, whatever their permissions.
	"""

	def before_insert(self) -> None:
		# Runs for every insert, even one that skips permissions and validation.
		if not self.flags.recorded_by_audit_anchor:
			frappe.throw(
				_("An audit anchor is recorded only by the audit trail's anchoring."), frappe.PermissionError
			)

	def validate(self) -> None:
		if not self.is_new():
			frappe.throw(_("An audit anchor cannot be changed."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("An audit anchor cannot be deleted."), frappe.PermissionError)


def on_doctype_update() -> None:
	# Anchoring and verification read the newest anchor of each trail.
	frappe.db.add_index("Audit Anchor", ["ledger", "creation"])
