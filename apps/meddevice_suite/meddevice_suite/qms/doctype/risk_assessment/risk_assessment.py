# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import risk_management


class RiskAssessment(Document):
	"""One revision of a risk management file's analysis (module 39). Its life is driven by the
	signed acts in `qms.risk_management`; this controller keeps everyone else to the rules."""

	def autoname(self) -> None:
		risk_management.name_assessment(self)

	def before_insert(self) -> None:
		risk_management.new_assessment(self)

	def before_validate(self) -> None:
		risk_management.guard_assessment(self)

	def validate(self) -> None:
		risk_management.score(self)

	def before_update_after_submit(self) -> None:
		risk_management.only_by_act(self)

	def before_cancel(self) -> None:
		frappe.throw(_("An approved risk assessment is superseded, never cancelled."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A risk assessment is never deleted."), frappe.PermissionError)
