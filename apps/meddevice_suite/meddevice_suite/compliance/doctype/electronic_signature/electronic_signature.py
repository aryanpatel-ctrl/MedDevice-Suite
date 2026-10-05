# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ElectronicSignature(Document):
	"""A 21 CFR Part 11 signature. Created only by `meddevice_suite.compliance.esign.sign`.

	Once recorded it can never change: any save after the first, and any delete,
	is refused for every user, whatever their permissions.
	"""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("An electronic signature cannot be changed."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("An electronic signature cannot be deleted."), frappe.PermissionError)
