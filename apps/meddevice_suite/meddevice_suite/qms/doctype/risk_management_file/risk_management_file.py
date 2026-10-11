# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import risk_management


class RiskManagementFile(Document):
	"""A product family's risk management file (module 39); the rules are in `qms.risk_management`."""

	def validate(self) -> None:
		risk_management.guard_file(self)

	def onload(self) -> None:
		# The form offers a new revision only when none is in progress (the server checks again).
		self.set_onload("revision_in_progress", risk_management.revision_in_progress(self.name))

	def on_trash(self) -> None:
		frappe.throw(_("A risk management file is never deleted."), frappe.PermissionError)
