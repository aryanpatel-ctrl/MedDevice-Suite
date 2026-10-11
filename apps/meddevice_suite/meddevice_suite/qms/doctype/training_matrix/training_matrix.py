# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import training


class TrainingMatrix(Document):
	"""Which documents a job is trained on (module 37); the rules are in `qms.training`."""

	def before_validate(self) -> None:
		training.validate_matrix(self)

	def on_cancel(self) -> None:
		training.on_matrix_cancel(self)

	def on_trash(self) -> None:
		frappe.throw(_("A training matrix is never deleted; cancel it."), frappe.PermissionError)
