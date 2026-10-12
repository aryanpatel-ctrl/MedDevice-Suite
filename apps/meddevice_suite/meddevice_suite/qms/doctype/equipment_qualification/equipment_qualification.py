# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import equipment_care, qualification


class EquipmentQualification(Document):
	"""An IQ, OQ or PQ (module 40); the rules are in `qms.qualification`."""

	def before_validate(self) -> None:
		qualification.only_by_approval(self)

	def validate(self) -> None:
		qualification.evaluate(self)

	def before_update_after_submit(self) -> None:
		frappe.throw(_("An approved qualification does not change."), frappe.PermissionError)

	def before_cancel(self) -> None:
		equipment_care.only_by_correction(self)

	def on_trash(self) -> None:
		frappe.throw(_("A qualification is never deleted."), frappe.PermissionError)
