# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.master_data.supplier_approval import is_qa

# Set only by the signed decision (master_data/supplier_approval.py).
DECISION_FIELDS = ("decision", "reason", "decided_by", "decided_at", "certificate_alerted")


class SupplierApproval(Document):
	"""QA's decision on a supplier (module 4). Decided only through the signed acts in
	`master_data.supplier_approval`; this controller keeps everyone else to the rules."""

	def before_insert(self) -> None:
		if not is_qa():
			frappe.throw(_("Only QA decides a supplier."), frappe.PermissionError)
		for field in DECISION_FIELDS:
			self.set(field, None)

	def validate(self) -> None:
		if self.is_new() or self.flags.supplier_approval:
			return
		if not is_qa():
			frappe.throw(_("Only QA prepares the decision on a supplier."), frappe.PermissionError)
		before = self.get_doc_before_save()
		if any(before.get(field) != self.get(field) for field in DECISION_FIELDS):
			frappe.throw(
				_("{0} is decided by signing it; its decision is set by the signature.").format(self.name),
				frappe.PermissionError,
			)

	def before_submit(self) -> None:
		self.only_by_decision()

	def before_update_after_submit(self) -> None:
		self.only_by_decision()

	def only_by_decision(self) -> None:
		# Frappe's Submit button and the API would otherwise decide a supplier unsigned.
		if not self.flags.supplier_approval:
			frappe.throw(_("{0} is decided only by signing it.").format(self.name), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A supplier approval is never deleted."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(
			_("A decision on a supplier is never cancelled; a new decision replaces it."),
			frappe.PermissionError,
		)


def on_doctype_update() -> None:
	# The decision in force is read on every purchase order and goods receipt.
	frappe.db.add_index("Supplier Approval", ["supplier", "docstatus", "decided_at"])
