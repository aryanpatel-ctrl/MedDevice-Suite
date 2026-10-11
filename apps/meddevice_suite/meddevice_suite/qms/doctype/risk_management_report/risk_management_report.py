# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import risk_management


class RiskManagementReport(Document):
	"""QA's risk management report on an approved assessment (module 39); the rules are in
	`qms.risk_management`."""

	def before_insert(self) -> None:
		risk_management.new_report(self)

	def before_validate(self) -> None:
		risk_management.guard_report(self)

	def before_update_after_submit(self) -> None:
		risk_management.only_by_act(self)

	def before_cancel(self) -> None:
		frappe.throw(_("An approved report is superseded, never cancelled."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A risk management report is never deleted."), frappe.PermissionError)
