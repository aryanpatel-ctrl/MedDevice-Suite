# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms import risk_management


class RiskAcceptabilityPolicy(Document):
	"""The risk acceptability policy (module 39, plan D-13, D-21); the rules are in
	`qms.risk_management`."""

	def before_insert(self) -> None:
		risk_management.new_policy(self)

	def before_validate(self) -> None:
		risk_management.guard_policy(self)

	def validate(self) -> None:
		risk_management.require_scales(self)

	def before_update_after_submit(self) -> None:
		risk_management.only_by_act(self)

	def before_cancel(self) -> None:
		frappe.throw(
			_("A policy in force is superseded by the next one, never cancelled."), frappe.PermissionError
		)

	def on_trash(self) -> None:
		frappe.throw(_("A risk acceptability policy is never deleted."), frappe.PermissionError)
