# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import equipment_care, preventive_maintenance


class MaintenanceRecord(Document):
	"""Preventive maintenance (module 38); the rules are in `qms.preventive_maintenance`."""

	def before_validate(self) -> None:
		preventive_maintenance.only_by_signature(self)

	def validate(self) -> None:
		preventive_maintenance.evaluate(self)

	def before_update_after_submit(self) -> None:
		frappe.throw(_("A completed maintenance record does not change."), frappe.PermissionError)

	def before_cancel(self) -> None:
		equipment_care.only_by_correction(self)

	def copy_attachments_from_amended_from(self) -> None:
		equipment_care.copy_attachments(self)

	def on_trash(self) -> None:
		frappe.throw(_("A maintenance record is never deleted."), frappe.PermissionError)
