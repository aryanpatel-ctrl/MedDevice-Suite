"""The regulated record contract (21 CFR 11.10(d), 11.50; ISO 13485 4.2.4, 4.2.5; decided 2026-10-12,
plan D-29): every submittable record type this app builds is submitted, cancelled, changed after
submission and deleted only through its signed acts, or never.

Frappe's own Submit, Cancel and Delete buttons, a save after submission and the REST API reach a
record past every signed act. So each record type refuses them itself, whatever the caller's
permissions, unless the flag its signed act sets is there; and no role holds Frappe's permission
for them, so the desk does not offer them. The record types are found, not listed: a new
submittable record type that forgets the rule fails here. The intended exceptions are listed in
ALLOWED, each with its reason.

Each check runs the methods Frappe runs for the action, in its order (`Document.run_method`, which
also runs the hooks in hooks.py), on a record held in memory as Frappe holds it during the action.
Nothing is stored.
"""

import frappe
from frappe.model.document import Document
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.audit_scope import APP
from meddevice_suite.compliance.signing import SIGNING_ACTIONS
from meddevice_suite.qa import verifies

# Frappe's own actions on a record, and the permission Frappe asks for each (a change after
# submission needs the submit permission).
PERMISSION_FOR = {"submit": "submit", "cancel": "cancel", "update_after_submit": "submit", "delete": "delete"}

# (record type, Frappe action) -> why Frappe's own action is allowed on it. Nothing else is.
ALLOWED: dict[tuple[str, str], str] = {
	# QA changes a job's training matrix by cancelling it and amending it, approved again by
	# signature, and retires one by cancelling it; cancelling reconciles the job's training at once
	# (URS-TRN-004, plan D-17).
	("Training Matrix", "cancel"): "changed by cancel and amend, approved again (URS-TRN-004, plan D-17)",
}


def submittable_record_types() -> list[str]:
	"""Every submittable record type this app builds; child tables and types made on a site
	(custom) are not records of the app."""
	modules = frappe.get_all("Module Def", filters={"app_name": APP}, pluck="name")
	return frappe.get_all(
		"DocType",
		filters={"module": ["in", modules or [""]], "is_submittable": 1, "istable": 0, "custom": 0},
		pluck="name",
		order_by="name asc",
	)


def held(doctype: str, docstatus: int, action: str) -> Document:
	"""A record as Frappe holds it while running the action, never stored, with no act's flag."""
	doc = frappe.new_doc(doctype)
	doc.docstatus = docstatus
	doc._action = action
	return doc


def refused(doc: Document, *methods: str) -> bool:
	"""Whether running these methods of the record, as Frappe runs them for the action, refuses
	it. Any other error is not a refusal and is raised, for the record type to be looked at."""
	try:
		for method in methods:
			doc.run_method(method)
	except frappe.PermissionError:
		return True
	return False


def changeable_after_submit(doctype: str) -> bool:
	"""Whether any field of the record or of its rows may change after submission. Where none may,
	Frappe itself refuses every change to a submitted record."""
	meta = frappe.get_meta(doctype)
	return any(df.allow_on_submit for df in meta.fields) or any(
		df.allow_on_submit
		for table in meta.get_table_fields()
		for df in frappe.get_meta(table.options).fields
	)


@verifies("URS-CMP-015", "URS-CMP-016", "URS-CMP-030")
class TestRegulatedRecordContract(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.record_types = submittable_record_types()

	def setUp(self):
		frappe.set_user("Administrator")  # every role: a refusal is the record type's, not a role's

	def subject_to(self, action: str) -> list[str]:
		"""The record types the rule holds for, leaving out those allowed the action."""
		return [doctype for doctype in self.record_types if (doctype, action) not in ALLOWED]

	def test_the_record_types_are_found(self):
		# A search that found nothing would let every check below pass.
		self.assertLessEqual(
			{
				"Calibration Record",
				"Maintenance Record",
				"Equipment Qualification",
				"Lot Release",
				"Training Matrix",
				"Supplier Rating",
			},
			set(self.record_types),
		)

	def test_the_allowed_exceptions_are_record_types_of_the_app(self):
		for doctype, action in ALLOWED:
			self.assertIn(doctype, self.record_types)
			self.assertIn(action, PERMISSION_FOR)

	def test_no_role_holds_frappes_permission_for_the_actions(self):
		for action, permission in PERMISSION_FOR.items():
			for doctype in self.subject_to(action):
				with self.subTest(doctype=doctype, action=action):
					meta = frappe.get_meta(doctype)
					self.assertEqual([row.role for row in meta.permissions if row.get(permission)], [])

	def test_frappes_submit_is_refused(self):
		"""Refused before it is validated, or, for a record only the system makes (submitted), no
		record is made but by the system, so there is no draft to submit."""
		for doctype in self.subject_to("submit"):
			with self.subTest(doctype=doctype):
				self.assertTrue(
					refused(held(doctype, 1, "submit"), "before_validate", "before_submit")
					or refused(held(doctype, 0, "save"), "before_insert")
				)

	def test_frappes_cancel_is_refused(self):
		for doctype in self.subject_to("cancel"):
			with self.subTest(doctype=doctype):
				self.assertTrue(refused(held(doctype, 2, "cancel"), "before_cancel"))

	def test_frappes_change_after_submission_is_refused(self):
		for doctype in self.subject_to("update_after_submit"):
			with self.subTest(doctype=doctype):
				self.assertTrue(
					not changeable_after_submit(doctype)
					or refused(held(doctype, 1, "update_after_submit"), "before_update_after_submit")
				)

	def test_frappes_delete_is_refused_for_a_draft_and_a_cancelled_record(self):
		for doctype in self.subject_to("delete"):
			for docstatus in (0, 2):
				with self.subTest(doctype=doctype, docstatus=docstatus):
					self.assertTrue(refused(held(doctype, docstatus, "delete"), "on_trash"))

	def test_each_is_submitted_by_a_signed_act_or_made_only_by_the_system(self):
		signed = {doctype for doctype, _act in SIGNING_ACTIONS}
		for doctype in self.subject_to("submit"):
			with self.subTest(doctype=doctype):
				self.assertTrue(doctype in signed or refused(held(doctype, 0, "save"), "before_insert"))
