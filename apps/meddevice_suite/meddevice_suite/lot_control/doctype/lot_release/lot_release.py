# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from meddevice_suite.compliance.audit import same
from meddevice_suite.lot_control import lot_status
from meddevice_suite.lot_control.lot_status import HOLD, STATUS_FIELD
from meddevice_suite.lot_control.receipt import origin_of, receipt_line_of

# What QA may set while the decision is a draft; everything else the signed act sets.
QA_FIELDS = frozenset({"batch"})


class LotRelease(Document):
	"""QA's decision on a lot (modules 16 and 17). Decided only through the signed acts in
	`lot_control.release`; this controller keeps everyone else to the rules."""

	def before_insert(self) -> None:
		if not lot_status.is_qa():
			frappe.throw(_("Only QA decides a lot."), frappe.PermissionError)
		self.describe_lot()
		for field in ("decision", "reason", "decided_by", "decided_at", "stock_entry", "supplier_return"):
			self.set(field, None)

	def validate(self) -> None:
		if self.is_new() or self.flags.lot_release:
			return
		before = self.get_doc_before_save()
		# Compared as stored values: the desk sends a date and time as text.
		changed = {
			df.fieldname
			for df in self.meta.fields
			if not df.fetch_from and not same(before.get(df.fieldname), self.get(df.fieldname), df.fieldtype)
		}
		if changed - QA_FIELDS or not lot_status.is_qa():
			frappe.throw(
				_("{0} is decided by signing it; its details are set by the decision.").format(self.name),
				frappe.PermissionError,
			)
		if "batch" in changed:
			self.describe_lot()

	def describe_lot(self) -> None:
		"""What the form shows QA about the lot before they decide."""
		lot = frappe.get_doc("Batch", self.batch)
		self.item = lot.item
		self.lot_status_before = lot.get(STATUS_FIELD)
		self.quantity = sum(flt(holding.get("qty")) for holding in lot_status.holdings_of(lot.name))
		# Where the stock came from, as the stock ledger says, not the batch's own reference.
		origin = origin_of(lot.name)
		self.source_doctype = origin.voucher_type if origin else None
		self.source_name = origin.voucher_no if origin else None
		line = receipt_line_of(lot.name) if self.source_doctype == "Purchase Receipt" else None
		self.supplier = line.supplier if line else lot.supplier
		self.held_since = lot.mds_status_set_at if lot.get(STATUS_FIELD) == HOLD else None

	def onload(self) -> None:
		if self.docstatus == 0 and self.batch:
			lot = frappe.get_doc("Batch", self.batch)
			line = receipt_line_of(lot.name)
			self.set_onload(
				"lot",
				{
					"status": lot.get(STATUS_FIELD),
					"from_supplier": bool(line),
					"certificate_verified": bool(line and line.verified),
				},
			)

	def before_submit(self) -> None:
		self.only_by_decision()

	def before_update_after_submit(self) -> None:
		self.only_by_decision()

	def only_by_decision(self) -> None:
		# Frappe's Submit button and the API would otherwise decide a lot unsigned.
		if not self.flags.lot_release:
			frappe.throw(_("{0} is decided only by signing it.").format(self.name), frappe.PermissionError)

	def on_trash(self) -> None:
		frappe.throw(_("A lot release is never deleted."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(
			_("A decision on a lot is never cancelled; a new decision puts the lot on hold or releases it."),
			frappe.PermissionError,
		)


def on_doctype_update() -> None:
	# A lot's decisions are read whenever it is shown or decided again.
	frappe.db.add_index("Lot Release", ["batch", "decided_at"])
