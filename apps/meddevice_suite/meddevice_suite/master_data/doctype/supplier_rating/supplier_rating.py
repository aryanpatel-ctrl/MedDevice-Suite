# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SupplierRating(Document):
	"""The system's monthly rating of a supplier (module 4, URS-PUR-002). Made only by
	`master_data.supplier_rating`, which computes it from QA's lot decisions and the receipts;
	nobody makes, changes, cancels or deletes one."""

	def before_insert(self) -> None:
		if not self.flags.supplier_rating:
			frappe.throw(
				_("A supplier rating is made by the system, from the month's records."),
				frappe.PermissionError,
			)

	def before_update_after_submit(self) -> None:
		# A rating is made submitted, so every later save comes here.
		if not self.flags.supplier_rating:
			frappe.throw(_("A supplier rating is never changed."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(_("A supplier rating is never cancelled."), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A supplier rating is never deleted."), frappe.PermissionError)
