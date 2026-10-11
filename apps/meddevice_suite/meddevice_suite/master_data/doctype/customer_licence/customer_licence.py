# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from meddevice_suite.master_data.customer_licence import STATUS_FIELDS

SALES_ROLES = ("Sales User", "Sales Manager")


class CustomerLicence(Document):
	"""A licence of a customer (module 5). Recorded by Sales; verified, rejected and withdrawn only
	through the signed acts in `master_data.customer_licence`; this controller keeps everyone else
	to the rules."""

	def before_insert(self) -> None:
		if not set(SALES_ROLES) & set(frappe.get_roles()):
			frappe.throw(_("Sales records a customer's licence."), frappe.PermissionError)
		for field in STATUS_FIELDS:
			self.set(field, None)

	def before_validate(self) -> None:
		# At before_validate, which Frappe runs even when a caller skips validation: Frappe's Submit
		# button and the API would otherwise verify a licence unsigned.
		if getattr(self, "_action", None) == "submit" and not self.flags.customer_licence:
			frappe.throw(
				_("{0} is verified only by QA's signature.").format(self.name), frappe.PermissionError
			)

	def validate(self) -> None:
		if self.valid_from and self.valid_until and getdate(self.valid_from) > getdate(self.valid_until):
			frappe.throw(_("A licence is valid from a day up to a later one; check its dates."))
		if self.is_new() or self.flags.customer_licence:
			return
		before = self.get_doc_before_save()
		if any(before.get(field) != self.get(field) for field in STATUS_FIELDS):
			frappe.throw(
				_("{0} is decided by QA's signature; its status is set by the signature.").format(self.name),
				frappe.PermissionError,
			)

	def before_update_after_submit(self) -> None:
		if not self.flags.customer_licence:
			frappe.throw(
				_("{0} is changed only by QA's signature.").format(self.name), frappe.PermissionError
			)

	def on_trash(self) -> None:
		frappe.throw(_("A customer licence is never deleted."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(
			_(
				"A customer licence is never cancelled; QA withdraws it, and a renewed licence is a new record."
			),
			frappe.PermissionError,
		)


def on_doctype_update() -> None:
	# The licences in force are read on every sales order and delivery note.
	frappe.db.add_index("Customer Licence", ["customer", "docstatus", "status", "valid_until"])
