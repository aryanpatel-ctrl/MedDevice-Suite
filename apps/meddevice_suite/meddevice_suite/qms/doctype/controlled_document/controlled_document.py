# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import add_days, cint, getdate, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.qms.document_control import IN_PROGRESS

# Numbering TYPE-DEPARTMENT-###, decided on the client's instruction of 2026-10-07.
TYPE_CODES = {
	"SOP": "SOP",
	"Work Instruction": "WI",
	"Form": "FRM",
	"Specification": "SPEC",
	"Packaging and Labelling": "PKG",
	"Quality Manual": "QM",
	"External Document": "EXT",
}
# Changed only by document control itself: revisions taking effect, withdrawal.
LIFECYCLE_FIELDS = ("status", "current_revision", "effective_since", "next_review_date", "withdrawn_reason")


class ControlledDocument(Document):
	"""A document of the quality system (module 30). QA registers it; its content lives in
	its revisions (`Document Revision`), and `qms.document_control` moves it through its life."""

	def autoname(self) -> None:
		self.name = make_autoname(f"{TYPE_CODES[self.document_type]}-{self.department}-.###", doc=self)

	def validate(self) -> None:
		if cint(self.review_period_years) < 1:
			frappe.throw(_("A document is reviewed at least every year."))
		if self.is_new() or self.flags.document_control:
			return
		before = self.get_doc_before_save()
		changed = [field for field in LIFECYCLE_FIELDS if before and before.get(field) != self.get(field)]
		if changed:
			frappe.throw(
				_("The status and effective revision of a document change only through its revisions.")
			)
		if self.status == "Obsolete":
			frappe.throw(_("{0} has been withdrawn and cannot be changed.").format(self.name))

	def onload(self) -> None:
		# The form offers a new revision only when none is in progress (the server checks again).
		self.set_onload(
			"revision_in_progress",
			frappe.db.get_value(
				"Document Revision", {"controlled_document": self.name, "status": ["in", IN_PROGRESS]}, "name"
			),
		)
		# The form puts the periodic review first once it falls within the alert's lead time, as the
		# review alert and the reminder do (Quality Settings).
		warning = add_days(today(), quality_settings.value("document_review_warning_days"))
		self.set_onload(
			"review_due",
			bool(
				self.status == "Effective"
				and self.next_review_date
				and getdate(self.next_review_date) <= getdate(warning)
			),
		)

	def on_trash(self) -> None:
		frappe.throw(_("A controlled document is withdrawn, never deleted."), frappe.PermissionError)
