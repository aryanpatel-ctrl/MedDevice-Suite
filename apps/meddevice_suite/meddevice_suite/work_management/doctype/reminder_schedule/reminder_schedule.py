# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

MAX_LEAD_DAYS = 365


class ReminderSchedule(Document):
	"""A recurring activity QA plans (module 60, URS-PLT-015)."""

	def validate(self) -> None:
		from meddevice_suite.setup.roles import BUSINESS_ROLES  # the jobs that do the work

		if self.responsible_role not in BUSINESS_ROLES:
			frappe.throw(_("The responsible job is one of the business jobs' roles."))
		if not 0 <= cint(self.lead_days) <= MAX_LEAD_DAYS:
			frappe.throw(_("Raise it between 0 and {0} days before it is due.").format(MAX_LEAD_DAYS))
		before = self.get_doc_before_save()
		if self.is_new() or (
			before and before.first_due_date != self.first_due_date and not self.raised_any()
		):
			self.next_due_date = self.first_due_date
		elif before and before.next_due_date != self.next_due_date and not self.flags.reminder:
			frappe.throw(_("The next due date follows from the schedule."), frappe.PermissionError)

	def raised_any(self) -> bool:
		return bool(frappe.db.exists("Reminder", {"schedule": self.name}))

	def on_trash(self) -> None:
		frappe.throw(
			_("A recurring reminder is not deleted; clear Active to stop it."), frappe.PermissionError
		)
