# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.work_management import work_task


class WorkTask(Document):
	"""A task with its approval (module 59, URS-PLT-016); the rules are in
	`work_management.work_task`."""

	def validate(self) -> None:
		work_task.check(self)

	def before_submit(self) -> None:
		work_task.guard_submit(self)

	def before_cancel(self) -> None:
		frappe.throw(
			_("A task is never cancelled; withdraw it before it is approved."), frappe.PermissionError
		)

	def on_trash(self) -> None:
		frappe.throw(_("A task is never deleted; withdraw it instead."), frappe.PermissionError)
