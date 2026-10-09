# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class RecordArchive(Document):
	"""A sealed archive copy of a period's regulated records (module 54).

	QA requests it with a period; `meddevice_suite.compliance.archive` builds the file
	in the background and records its fingerprint. Users can never change or delete it:
	the write permission only lets the desk show the period fields on a new archive,
	and `validate` refuses every save after the first.
	"""

	def validate(self) -> None:
		if not self.is_new():
			frappe.throw(_("A record archive cannot be changed."), frappe.PermissionError)
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The period must end on or after the day it starts."))
		if getdate(self.to_date) > getdate(today()):
			frappe.throw(_("The period cannot end in the future."))

	def after_insert(self) -> None:
		frappe.enqueue(
			"meddevice_suite.compliance.archive.build_archive",
			queue="long",
			timeout=6 * 60 * 60,
			enqueue_after_commit=True,
			now=frappe.in_test,
			archive=self.name,
		)

	def on_trash(self) -> None:
		frappe.throw(_("A record archive cannot be deleted."), frappe.PermissionError)
