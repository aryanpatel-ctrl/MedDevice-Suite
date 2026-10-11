# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import calibration


class CalibrationRecord(Document):
	"""A calibration (module 38); the rules are in `qms.calibration`."""

	def before_validate(self) -> None:
		calibration.only_by_signature(self)

	def validate(self) -> None:
		calibration.evaluate(self)

	def before_update_after_submit(self) -> None:
		calibration.guard_after_completion(self)

	def on_trash(self) -> None:
		frappe.throw(_("A calibration record is never deleted."), frappe.PermissionError)
