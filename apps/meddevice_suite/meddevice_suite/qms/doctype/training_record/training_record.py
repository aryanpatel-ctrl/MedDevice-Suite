# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import training


class TrainingRecord(Document):
	"""One employee's training on one revision of a document (module 37); the rules are in
	`qms.training`."""

	def before_insert(self) -> None:
		training.before_insert(self)

	def before_validate(self) -> None:
		training.validate_record(self)

	def before_update_after_submit(self) -> None:
		training.guard_after_completion(self)

	def before_cancel(self) -> None:
		frappe.throw(
			_("A training record is never cancelled; QA revokes a competence."), frappe.PermissionError
		)

	def on_trash(self) -> None:
		training.refuse_delete(self)
