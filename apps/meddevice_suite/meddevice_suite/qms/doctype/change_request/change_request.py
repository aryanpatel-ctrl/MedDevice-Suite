# Copyright (c) 2026, Demo Medical Devices Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.audit import same
from meddevice_suite.qms.change_control import IMPACT_AREAS, in_business_job, is_qa

# What the person who raised the change may edit while it is a draft.
REQUESTER_FIELDS = frozenset({"title", "change_type", "description", "reason", "affected_records", "actions"})
# What QA may edit while assessing it.
ASSESSOR_FIELDS = frozenset(
	{
		"change_type",
		"affected_records",
		"actions",
		*IMPACT_AREAS,
		"impact_summary",
		"classification",
		"effectiveness_check_days",
	}
)
# Set only by change control; whatever a new request arrives with is discarded.
SET_BY_CHANGE_CONTROL = (
	"classification",
	*IMPACT_AREAS,
	"impact_summary",
	"assessed_by",
	"assessed_on",
	"qa_approver",
	"management_approver",
	"approved_on",
	"implemented_on",
	"effectiveness_due_on",
	"effectiveness_outcome",
	"effectiveness_note",
	"closed_on",
	"decision_reason",
	"effectiveness_alerted",
)
# On each action: recorded only when it is done.
COMPLETION_FIELDS = (
	"done",
	"done_on",
	"done_by",
	"completion_note",
	"result_doctype",
	"result_name",
	"alerted_for",
)


class ChangeRequest(Document):
	"""A proposed change (module 36). Its life is driven by the acts in `qms.change_control`;
	this controller keeps everyone else to the rules."""

	def before_insert(self) -> None:
		if not in_business_job():
			frappe.throw(
				_("A change request is raised by someone in a business job."), frappe.PermissionError
			)
		self.requested_by = frappe.session.user
		self.requested_on = today()
		self.status = "Draft"
		for field in SET_BY_CHANGE_CONTROL:
			self.set(field, None)
		# Quality Settings (URS-PLT-012); the assessor may set another number of days.
		self.effectiveness_check_days = quality_settings.value("default_effectiveness_check_days")
		for row in self.actions:
			for field in COMPLETION_FIELDS:
				row.set(field, 0 if field == "done" else None)

	def validate(self) -> None:
		if self.is_new() or self.flags.change_control:
			return
		if any(row.get(field) for row in self.actions for field in COMPLETION_FIELDS):
			frappe.throw(
				_("An action is recorded as done only once the change is approved."), frappe.PermissionError
			)
		changed = changed_fields(self)
		user = frappe.session.user
		if self.status == "Draft" and user == self.requested_by and changed <= REQUESTER_FIELDS:
			return
		if self.status == "Under Assessment" and is_qa() and changed <= ASSESSOR_FIELDS:
			return
		if changed:
			frappe.throw(
				_("Change request {0} is {1}; it can no longer be changed this way.").format(
					self.name, _(self.status)
				),
				frappe.PermissionError,
			)

	def before_submit(self) -> None:
		self.only_by_change_control()

	def before_update_after_submit(self) -> None:
		self.only_by_change_control()

	def only_by_change_control(self) -> None:
		# Frappe's Submit button and the API would otherwise approve or change a request unsigned.
		if not self.flags.change_control:
			frappe.throw(
				_("Change request {0} is approved and moved on only through its acts.").format(self.name),
				frappe.PermissionError,
			)

	def on_trash(self) -> None:
		frappe.throw(_("A change request is never deleted."), frappe.PermissionError)

	def before_cancel(self) -> None:
		frappe.throw(
			_("An approved change is implemented and closed, never cancelled."), frappe.PermissionError
		)


def changed_fields(doc: Document) -> set[str]:
	"""The fields, tables included, that differ from the record as stored, compared as stored
	values: the desk sends a date as text where the stored record holds a date."""
	before = doc.get_doc_before_save()  # always loaded for a record already stored
	changed = set()
	for df in doc.meta.fields:
		if df.fieldtype in frappe.model.table_fields:
			if not same_rows(before.get(df.fieldname), doc.get(df.fieldname), frappe.get_meta(df.options)):
				changed.add(df.fieldname)
		elif df.fieldtype not in frappe.model.no_value_fields and not same(
			before.get(df.fieldname), doc.get(df.fieldname), df.fieldtype
		):
			changed.add(df.fieldname)
	return changed


def same_rows(old: list, new: list, meta) -> bool:
	if len(old) != len(new):
		return False
	fields = [df for df in meta.fields if df.fieldtype not in frappe.model.no_value_fields]
	return all(
		same(old_row.get(df.fieldname), new_row.get(df.fieldname), df.fieldtype)
		for old_row, new_row in zip(old, new, strict=True)
		for df in fields
	)
