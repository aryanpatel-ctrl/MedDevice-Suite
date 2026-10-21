# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from meddevice_suite.compliance.audit import same
from meddevice_suite.lot_control import lot_status, stores
from meddevice_suite.lot_control.lot_status import QUARANTINE, STATUS_FIELD
from meddevice_suite.lot_control.receipt import origin_of, receipt_line_of
from meddevice_suite.lot_control.release import RELEASE_BY_CONCESSION, decided_stock, held_since
from meddevice_suite.qms.change_control import is_management

# What QA may set while the decision is a draft; everything else the signed act sets.
QA_FIELDS = frozenset({"batch"})
# Set only by the signed acts.
DECISION_FIELDS = (
	"decision",
	"reason",
	"decided_by",
	"decided_at",
	"stock_entry",
	"supplier_return",
	"concession_justification",
	"concession_approved_by",
	"concession_approved_at",
)


class LotRelease(Document):
	"""QA's decision on a lot (modules 16 and 17). Decided only through the signed acts in
	`lot_control.release`; this controller keeps everyone else to the rules."""

	def before_insert(self) -> None:
		if not lot_status.is_qa():
			frappe.throw(_("Only QA decides a lot."), frappe.PermissionError)
		self.describe_lot()
		for field in DECISION_FIELDS:
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
		# A draft QA approved a concession on is Management's to approve: QA no longer points it at
		# another lot (plan D-35).
		if changed - QA_FIELDS or not lot_status.is_qa() or (changed and before.get("decision")):
			frappe.throw(
				_("{0} is decided by signing it; its details are set by the decision.").format(self.name),
				frappe.PermissionError,
			)
		if "batch" in changed:
			self.describe_lot()

	def describe_lot(self) -> None:
		"""What QA decides on, read from the lot as stored: shown on the draft, and read again under the
		lot's lock when QA signs (`release.decide`), so a decision never rests on a draft made before
		the lot was held, moved or received again. The quantity is the stock a decision on the lot
		acts on, in the store of its status; stock rejected at the door and goods a customer sent back
		are not in it."""
		lot = frappe.get_doc("Batch", self.batch)
		status = lot.get(STATUS_FIELD) or QUARANTINE
		self.item = lot.item
		self.lot_status_before = status
		self.quantity = sum(flt(holding.qty) for holding in decided_stock(None, lot.name, status))
		# Where the stock came from, as the stock ledger says, not the batch's own reference.
		origin = origin_of(lot.name)
		self.source_doctype = origin.voucher_type if origin else None
		self.source_name = origin.voucher_no if origin else None
		line = receipt_line_of(lot.name) if self.source_doctype == "Purchase Receipt" else None
		self.supplier = line.supplier if line else lot.supplier
		self.held_since = held_since(lot)

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
					# Goods a customer sent back, waiting for QA's disposition (plan D-25).
					"returned": sum(
						flt(holding.qty) for holding in lot_status.holdings_of(lot.name, {stores.RETURNS})
					),
					# Plan D-35: a second person from Management approves a concession QA approved.
					"approves_concession": self.decision == RELEASE_BY_CONCESSION
					and frappe.session.user != self.decided_by
					and is_management(),
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
