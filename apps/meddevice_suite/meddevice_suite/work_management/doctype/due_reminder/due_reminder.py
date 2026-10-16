# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DueReminder(Document):
	"""A tracked due item (module 60, URS-PLT-014, URS-PLT-015). Made and changed only by
	`work_management.reminders`; nobody makes, edits or deletes one by hand."""

	def before_insert(self) -> None:
		if not self.flags.reminder:
			frappe.throw(_("A reminder is raised by the system."), frappe.PermissionError)

	def validate(self) -> None:
		if not self.is_new() and not self.flags.reminder:
			frappe.throw(_("A reminder changes only as its work is done."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A reminder is never deleted."), frappe.PermissionError)
