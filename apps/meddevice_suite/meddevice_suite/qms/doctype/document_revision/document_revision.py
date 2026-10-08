# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.qms.change_control import require_change_for_revision
from meddevice_suite.qms.document_control import IN_PROGRESS, is_qa, lock_document

# What the author may change while the revision is a draft; everything else is
# set by document control as the revision moves through its life.
AUTHOR_FIELDS = ("file", "summary_of_change", "reviewer")
# Set only by the signed acts; whatever a new revision arrives with is discarded.
SET_BY_DOCUMENT_CONTROL = ("file_sha256", "approver", "effective_date", "superseded_by")


class DocumentRevision(Document):
	"""One issue of a controlled document (module 30). Its life is driven by the signed acts
	in `qms.document_control`; this controller keeps everyone else to the rules."""

	def autoname(self) -> None:
		self.revision = next_revision_number(self.controlled_document)
		self.name = f"{self.controlled_document}-REV-{self.revision}"

	def before_insert(self) -> None:
		lock_document(self.controlled_document)
		document = frappe.get_doc("Controlled Document", self.controlled_document)
		if document.status == "Obsolete":
			frappe.throw(_("{0} has been withdrawn; it cannot be revised.").format(document.name))
		if not (is_qa() or frappe.session.user == document.process_owner):
			frappe.throw(
				_("Only the process owner of {0} or QA can start a new revision.").format(document.name),
				frappe.PermissionError,
			)
		if frappe.db.exists(
			"Document Revision", {"controlled_document": document.name, "status": ["in", IN_PROGRESS]}
		):
			frappe.throw(_("{0} already has a revision in progress.").format(document.name))
		require_change_for_revision(self, document)
		self.author = frappe.session.user
		self.status = "Draft"
		for field in SET_BY_DOCUMENT_CONTROL:
			self.set(field, None)

	def validate(self) -> None:
		if self.is_new() or self.flags.document_control:
			return
		before = self.get_doc_before_save()
		# Fields copied from the controlled document follow it and are not edits.
		changed = {
			df.fieldname
			for df in self.meta.fields
			if not df.fetch_from and before and before.get(df.fieldname) != self.get(df.fieldname)
		}
		if self.status == "Draft" and frappe.session.user == self.author and changed <= set(AUTHOR_FIELDS):
			return
		if self.status == "Awaiting Approval" and is_qa() and changed <= {"effective_date"}:
			return
		if changed:
			frappe.throw(
				_("Revision {0} is {1}; it can no longer be changed this way.").format(
					self.name, _(self.status)
				),
				frappe.PermissionError,
			)

	def before_submit(self) -> None:
		self.only_by_document_control()

	def before_update_after_submit(self) -> None:
		self.only_by_document_control()

	def only_by_document_control(self) -> None:
		# Frappe's Submit button and the API would otherwise approve or change a revision unsigned.
		if not self.flags.document_control:
			frappe.throw(
				_("Revision {0} is approved and moved on only by signing it.").format(self.name),
				frappe.PermissionError,
			)

	def on_trash(self) -> None:
		frappe.throw(_("A document revision is never deleted."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(
			_("An approved revision is superseded or withdrawn, never cancelled."), frappe.PermissionError
		)


def next_revision_number(document: str) -> str:
	"""00, 01, … compared as numbers, so revision 100 follows 99."""
	numbers = [
		int(r)
		for r in frappe.get_all(
			"Document Revision", filters={"controlled_document": document}, pluck="revision"
		)
	]
	return f"{max(numbers) + 1:02d}" if numbers else "00"
