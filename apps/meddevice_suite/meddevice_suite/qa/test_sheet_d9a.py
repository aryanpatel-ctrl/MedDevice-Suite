"""The data the browser test sheet of D9 Documents starts from, for its controlled documents,
important files, change control and device master record (qa/sheets/d9a.py), proven on its own.

The sheet's Cypress spec (cypress/integration/sheet_d9a.js) calls these set-ups as the Administrator
of a test site, and its tests start from what they make. These tests make the same calls the same
way and check that every record is there, in the status the test starts from, with the jobs'
signatures as each set-up states; and that every set-up is refused off a test site, to anyone but
the Administrator, and without the run's password where it signs.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from meddevice_suite.compliance.signing import signatures_of
from meddevice_suite.files_repository import files
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d9a
from meddevice_suite.qa.ui_users import email_for, prepare_job_users
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.qms import document_control, risk_management
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.work_management.reminders import DOCTYPE as REMINDER

QA, QC, PRODUCTION, PURCHASE = d9a.QA, d9a.QC, d9a.PRODUCTION, d9a.PURCHASE


def signed(doctype: str, name: str) -> list[tuple[str, str]]:
	"""The signatures on a record: (action, signer), in the order they were made."""
	return [(row.action, row.signer) for row in signatures_of(doctype, name)]


def set_ups(password: str) -> list[tuple]:
	"""Every set-up the sheet offers the browser, with arguments of the kind it sends."""
	return [
		(d9a.prepare_sheet, {"password": password}),
		(d9a.prepare_document, {"password": password, "stage": "effective"}),
		(d9a.prepare_revision_rules, {"password": password}),
		(d9a.prepare_review_due, {"password": password}),
		(d9a.take_effect_now, {"revision": "Any revision"}),
		(d9a.prepare_changes, {"password": password, "stages": '["approved"]'}),
		(d9a.prepare_risk_change, {"password": password}),
		(d9a.prepare_spent_change, {"password": password}),
		(d9a.prepare_file_categories, {"with_file": 1}),
		(d9a.prepare_dmr, {"password": password}),
		(d9a.issue_pinned_document_revision, {"password": password}),
	]


@verifies("URS-PLT-007")
class TestSheetD9DocumentsChangesAndDeviceMasterRecord(IntegrationTestCase):
	"""The set-up of test_sheets: the job roles, a test site, the run's password of the job users,
	the Administrator who runs the tests, and the sheet prepared as its `before` prepares it."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		frappe.set_user("Administrator")
		with patch.dict(frappe.local.conf, {"allow_tests": 1}):
			cls.sheet = d9a.prepare_sheet(prepare_job_users()["password"])

	def setUp(self):
		self.enterContext(patch.dict(frappe.local.conf, {"allow_tests": 1}))
		self.password = prepare_job_users()["password"]  # the run's, as the browser is given it
		# Each test leaves the sheet as it found it: what one test makes, the next does not read.
		frappe.db.savepoint("sheet_d9a_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		try:
			frappe.db.rollback(save_point="sheet_d9a_test")
		except frappe.db.OperationalError:
			pass  # a refusal rolled the whole transaction back, the savepoint with it: nothing is left

	def assertRevision(self, revision: str, status: str, signers: list[tuple[str, str]]) -> None:
		self.assertEqual(frappe.db.get_value("Document Revision", revision, "status"), status)
		self.assertEqual(signed("Document Revision", revision), signers)

	def assertChange(self, name: str, status: str, document: str) -> None:
		request = frappe.get_doc("Change Request", name)
		self.assertEqual(request.status, status)
		self.assertEqual(request.requested_by, email_for(PRODUCTION))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[("Controlled Document", document)],
		)
		self.assertEqual([row.action_owner for row in request.actions], [email_for(PRODUCTION)])

	# --- Refusals ------------------------------------------------------------------------------

	def test_every_set_up_the_sheet_offers_is_checked_here(self):
		offered = {
			value
			for value in vars(d9a).values()
			if callable(value)
			and getattr(value, "__module__", None) == d9a.__name__
			and value in frappe.whitelisted
		}
		self.assertEqual(offered, {set_up for set_up, _arguments in set_ups(self.password)})

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for set_up, arguments in set_ups(self.password):
				with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
					set_up(**arguments)

	def test_only_the_administrator_prepares_the_sheet(self):
		frappe.set_user(prepare_job_users()["users"]["Stores"])

		for set_up, arguments in set_ups(self.password):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
				set_up(**arguments)

	def test_refuses_to_sign_without_the_runs_password(self):
		made = ("Controlled Document", "Change Request", "Device Master Record")
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up, arguments in set_ups(self.password):
			if "password" not in arguments:
				continue
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(**{**arguments, "password": ""})
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_the_sheet_does_not_prepare(self):
		documents = frappe.db.count("Controlled Document")
		with self.assertRaises(frappe.ValidationError):
			d9a.prepare_document(self.password, "withdrawn")  # no such stage
		with self.assertRaises(frappe.ValidationError):
			d9a.prepare_document(self.password, "drafted", in_progress=1)  # a next revision once in effect
		with self.assertRaises(frappe.ValidationError):
			d9a.prepare_changes(self.password, '["closed"]')
		with self.assertRaises(frappe.ValidationError):
			d9a.prepare_dmr(self.password, "superseded")
		self.assertEqual(frappe.db.count("Controlled Document"), documents)
		draft = d9a.prepare_document(self.password, "drafted")["revision"]
		with self.assertRaises(frappe.ValidationError):
			d9a.take_effect_now(draft)  # only an approved revision waits for its date

	# --- The sheet's masters -------------------------------------------------------------------

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(sheet["product"], d9a.PRODUCT)
		self.assertEqual(dmr.current_record(d9a.PRODUCT), sheet["device_master_record"])
		self.assertFalse(dmr.revision_in_progress(d9a.PRODUCT))
		self.assertEqual(sheet["risk_file"], dmr.risk_file_of(d9a.PRODUCT)[0])
		self.assertEqual(
			frappe.db.get_value("Controlled Document", sheet["labelling"], ["document_type", "status"]),
			("Packaging and Labelling", "Effective"),
		)
		self.assertTrue(
			frappe.db.exists(
				dmr.ELEMENT,
				{
					"parent": sheet["device_master_record"],
					"reference_doctype": "Controlled Document",
					"reference_name": sheet["labelling"],
				},
			)
		)
		for key in ("approved", "work_in_progress", "quarantine"):
			with self.subTest(store=key):
				self.assertTrue(frappe.db.exists("Warehouse", sheet["stores"][key]))

	# --- Controlled documents ------------------------------------------------------------------

	def test_a_document_taken_to_each_stage_by_its_jobs(self):
		author, reviewer, approver = (
			("Author", email_for(PRODUCTION)),
			("Review", email_for(QC)),
			("Approve", email_for(QA)),
		)
		expected = {
			"drafted": ("Draft", []),
			"in_review": ("In Review", [author]),
			"awaiting_approval": ("Awaiting Approval", [author, reviewer]),
			"effective": ("Effective", [author, reviewer, approver]),
		}
		for stage, (status, signers) in expected.items():
			with self.subTest(stage=stage):
				issued = d9a.prepare_document(self.password, stage)
				self.assertEqual(issued["revision"], f"{issued['document']}-REV-00")
				self.assertRevision(issued["revision"], status, signers)
				self.assertEqual(
					frappe.db.get_value("Document Revision", issued["revision"], ["author", "reviewer"]),
					(email_for(PRODUCTION), email_for(QC)),
				)
		registered = d9a.prepare_document(self.password, "registered")
		self.assertIsNone(registered["revision"])
		self.assertEqual(
			frappe.db.get_value(
				"Controlled Document", registered["document"], ["status", "process_owner", "document_type"]
			),
			("Draft", email_for(PRODUCTION), "Work Instruction"),
		)

	def test_an_effective_document_with_its_next_revision_in_progress(self):
		issued = d9a.prepare_document(self.password, "effective", in_progress=1)

		self.assertEqual(issued["in_progress"], f"{issued['document']}-REV-01")
		revision = frappe.get_doc("Document Revision", issued["in_progress"])
		self.assertEqual((revision.status, revision.author), ("Draft", email_for(PRODUCTION)))
		self.assertChange(revision.change_request, "Approved", issued["document"])
		self.assertEqual(
			frappe.db.get_value("Controlled Document", issued["document"], "status"), "Effective"
		)

	def test_the_rules_of_a_second_revision(self):
		rules = d9a.prepare_revision_rules(self.password)

		self.assertRevision(
			rules["revision"],
			"Effective",
			[("Author", email_for(PRODUCTION)), ("Review", email_for(QC)), ("Approve", email_for(QA))],
		)
		self.assertChange(rules["listing_change"], "Approved", rules["document"])
		draft_document = frappe.db.get_value("Document Revision", rules["draft"], "controlled_document")
		self.assertChange(rules["other_change"], "Approved", draft_document)
		self.assertRevision(rules["draft"], "Draft", [])

	def test_a_document_whose_periodic_review_is_due_with_its_reminder(self):
		due = d9a.prepare_review_due(self.password)

		self.assertEqual(
			frappe.db.get_value("Controlled Document", due["document"], ["status", "current_revision"]),
			("Effective", due["revision"]),
		)
		self.assertEqual(due["next_review"], str(add_days(today(), 10)))
		reminder = frappe.get_doc(REMINDER, due["reminder"])
		self.assertEqual(
			(reminder.category, reminder.reference_name, reminder.status, getdate(reminder.due_date)),
			("Document Review", due["document"], "Open", getdate(due["next_review"])),
		)
		self.assertChange(due["change_request"], "Approved", due["document"])

	def test_an_approved_revision_takes_effect_when_its_day_comes(self):
		issued = d9a.prepare_document(self.password, "awaiting_approval")
		revision = frappe.get_doc("Document Revision", issued["revision"])
		revision.effective_date = add_days(today(), 10)
		with d9a.act_as(QA):
			revision.save()
			document_control.approve(revision, password=self.password, reason="Approved for later (test)")
		self.assertEqual(frappe.db.get_value("Document Revision", revision.name, "status"), "Approved")

		self.assertEqual(d9a.take_effect_now(revision.name), "Effective")
		self.assertEqual(
			frappe.db.get_value("Controlled Document", issued["document"], "current_revision"), revision.name
		)

	# --- Change requests -----------------------------------------------------------------------

	def test_change_requests_taken_to_each_stage(self):
		prepared = d9a.prepare_changes(
			self.password, ["under_assessment", "awaiting_approval", "approved", "implemented"]
		)
		names = prepared["changes"]

		for name, status in zip(
			names, ("Under Assessment", "Awaiting Approval", "Approved", "Implemented"), strict=True
		):
			with self.subTest(status=status):
				self.assertChange(name, status, prepared["document"])
		self.assertEqual(signed("Change Request", names[0]), [])
		self.assertEqual(signed("Change Request", names[1]), [("Assess", email_for(QA))])
		for name in names[2:]:
			self.assertEqual(
				signed("Change Request", name), [("Assess", email_for(QA)), ("Approve", email_for(QA))]
			)
		request = frappe.get_doc("Change Request", names[1])
		self.assertEqual((request.classification, request.effectiveness_check_days), ("Minor", 0))
		implemented = frappe.get_doc("Change Request", names[3])
		self.assertEqual(getdate(implemented.effectiveness_due_on), getdate(today()))
		self.assertEqual(implemented.actions[0].done_by, email_for(PRODUCTION))

	def test_a_change_with_a_risk_impact_under_review_and_one_checked_later(self):
		risk = d9a.prepare_risk_change(self.password)

		request = frappe.get_doc("Change Request", risk["change_request"])
		self.assertEqual((request.status, request.impact_risk), ("Implemented", "Yes"))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[(risk_management.FILE, self.sheet["risk_file"])],
		)
		self.assertEqual(risk_management.open_reviews_of("Change Request", request.name), [risk["review"]])
		self.assertEqual(
			frappe.db.get_value("Risk Review", risk["review"], ["trigger", "risk_management_file"]),
			("Change Request", self.sheet["risk_file"]),
		)
		self.assertEqual(frappe.db.get_value("Change Request", risk["later_change"], "status"), "Implemented")
		self.assertEqual(risk["later_due"], str(add_days(today(), 30)))

	def test_a_request_that_has_changed_its_document_once(self):
		spent = d9a.prepare_spent_change(self.password)

		row = frappe.get_doc("Change Request", spent["change_request"]).affected_records[0]
		self.assertEqual(
			(row.change_record_type, row.change_record), ("Document Revision", spent["revision"])
		)
		self.assertEqual(spent["revision"], f"{spent['document']}-REV-01")
		self.assertEqual(frappe.db.get_value("Document Revision", spent["revision"], "status"), "Effective")

	# --- The important files repository --------------------------------------------------------

	def test_a_group_and_its_sub_group_with_a_file_purchase_uploaded(self):
		prepared = d9a.prepare_file_categories(with_file=1)

		group, sub_group = (frappe.get_doc(files.CATEGORY, prepared[key]) for key in ("group", "sub_group"))
		self.assertEqual((group.is_group, sub_group.parent_file_category), (1, group.name))
		self.assertEqual(
			files.access()[sub_group.name],
			{files.READERS: {d9a.READING_JOB}, files.EDITORS: {d9a.UPLOADING_JOB}},
		)
		important = frappe.get_doc(files.DOCTYPE, prepared["file"])
		self.assertEqual(
			(important.title, important.category, important.status, important.current_version),
			(prepared["title"], sub_group.name, files.ACTIVE, 1),
		)
		self.assertEqual(important.versions[0].uploaded_by, email_for(PURCHASE))
		self.assertIsNone(d9a.prepare_file_categories()["file"])

	# --- The device master record --------------------------------------------------------------

	def test_the_current_record_and_a_revision_its_author_signed(self):
		current = d9a.prepare_dmr(self.password)
		self.assertEqual(current["record"], dmr.current_record(d9a.PRODUCT))
		self.assertIsNone(current["revision"])

		authored = d9a.prepare_dmr(self.password, "authored")
		self.assertEqual(authored["record"], current["record"])
		revision = frappe.get_doc(dmr.DOCTYPE, authored["revision"])
		self.assertEqual(
			(revision.status, revision.author, revision.supersedes),
			(dmr.AWAITING_APPROVAL, email_for(PRODUCTION), current["record"]),
		)
		self.assertEqual(signed(dmr.DOCTYPE, revision.name), [("Author", email_for(PRODUCTION))])

		# The next preparation withdraws the revision left in progress.
		self.assertEqual(d9a.prepare_dmr(self.password)["record"], current["record"])
		self.assertEqual(frappe.db.get_value(dmr.DOCTYPE, revision.name, "status"), dmr.WITHDRAWN)

	def test_a_new_revision_of_the_pinned_document_puts_the_record_out_of_date(self):
		record = d9a.prepare_dmr(self.password)["record"]

		issued = d9a.issue_pinned_document_revision(self.password)
		self.assertEqual(issued["record"], record)
		self.assertEqual(issued["document"], self.sheet["labelling"])
		self.assertEqual(frappe.db.get_value("Document Revision", issued["revision"], "status"), "Effective")
		self.assertEqual(frappe.db.get_value(dmr.DOCTYPE, record, "status"), dmr.OUT_OF_DATE)
		self.assertIsNone(dmr.current_record(d9a.PRODUCT))

		# Prepared again, the record is revised and approved, Current once more.
		again = d9a.prepare_dmr(self.password)["record"]
		self.assertNotEqual(again, record)
		self.assertEqual(dmr.current_record(d9a.PRODUCT), again)
