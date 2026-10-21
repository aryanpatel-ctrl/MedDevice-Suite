"""The data the browser test sheet of D9's risk management, licence and training tests starts from
(qa/sheets/d9b.py), proven on its own.

The sheet's Cypress spec (cypress/integration/sheet_d9b.js) calls these set-ups as the Administrator
of a test site, after ui_journey.prepare_journey, and its tests start from what they make. These
tests make the same calls the same way and check that every record is there, in the status the
sheet starts from, with the jobs' signatures as each set-up states; and that every set-up is refused
off a test site, to anyone but the Administrator, without the run's password where it signs, and
for a stage it does not prepare.
"""

from collections.abc import Callable
from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_months, get_fullname, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d9b
from meddevice_suite.qa.test_sheets import SheetTestCase, signed
from meddevice_suite.qa.ui_journey import OPERATIONS, PRODUCT, prepare_journey
from meddevice_suite.qa.ui_users import email_for
from meddevice_suite.qms import regulatory_licence, training
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.setup.fixtures.risk import SECOND_QA
from meddevice_suite.setup.fixtures.training import OPERATOR_JOB

QA, PRODUCTION, MANAGEMENT = "QA Manager", "Production Supervisor", "Management"


def set_ups(password: str) -> list[tuple[Callable, dict]]:
	"""Every set-up the sheet offers the browser, with arguments of the kind it sends."""
	return [
		(d9b.prepare_sheet, {"password": password}),
		(d9b.prepare_draft_policy, {"password": password}),
		(d9b.prepare_risk_file, {"password": password, "stage": "in_force"}),
		(d9b.run_periodic_reviews, {"file": "Any"}),
		(d9b.prepare_licence, {"password": password, "stage": "copy"}),
		(d9b.prepare_regulatory_change, {"password": password}),
		(
			d9b.run_daily_reminders,
			{"category": "Training Due", "reference_doctype": "Training Record", "reference_name": "Any"},
		),
		(d9b.prepare_training, {"password": password, "stage": "new_instruction"}),
		(d9b.prepare_revocation, {"password": password}),
		(d9b.restore_operator_training, {"password": password}),
	]


@verifies("URS-PLT-007")
class TestSheetD9bSetUpRefusals(SheetTestCase):
	def test_every_set_up_the_sheet_offers_is_checked_here(self):
		offered = {
			value
			for value in vars(d9b).values()
			if callable(value)
			and getattr(value, "__module__", None) == d9b.__name__
			and value in frappe.whitelisted
		}
		self.assertEqual(offered, {set_up for set_up, _arguments in set_ups(self.password)})
		self.assertEqual(offered, set(d9b.SET_UPS))

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for set_up, arguments in set_ups(self.password):
				with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
					set_up(**arguments)

	def test_only_the_administrator_prepares_the_sheet(self):
		frappe.set_user(email_for(QA))

		for set_up, arguments in set_ups(self.password):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
				set_up(**arguments)

	def test_refuses_to_sign_without_the_runs_password(self):
		made = (
			"Risk Management File",
			"Regulatory Licence",
			"Important File",
			"Training Matrix",
			"Work Order",
		)
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up, arguments in set_ups(self.password):
			if "password" not in arguments:
				continue
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(**{**arguments, "password": ""})
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_the_sheet_does_not_prepare(self):
		files = frappe.db.count(rm.FILE)
		for set_up, stage in (
			(d9b.prepare_risk_file, "retired"),
			(d9b.prepare_licence, "expired"),
			(d9b.prepare_training, "competent"),
		):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(self.password, stage)
		self.assertEqual(frappe.db.count(rm.FILE), files)
		with self.assertRaises(frappe.ValidationError):
			d9b.run_daily_reminders("Lot Expiry", "Batch", "Any lot")  # not a reminder the sheet runs


@verifies("URS-PLT-007")
class TestSheetD9bQualityRecords(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d9b.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def assertFirstRevisionApproved(self, risk: dict) -> None:
		"""The file's first revision, authored by QA, reviewed by the Production Supervisor and
		approved by the second QA person, in force."""
		revision = frappe.get_doc(rm.ASSESSMENT, risk["revision"])
		self.assertEqual((revision.status, revision.docstatus), (rm.APPROVED, 1))
		self.assertEqual(
			signed(rm.ASSESSMENT, revision.name),
			[("Author", email_for(QA)), ("Review", email_for(PRODUCTION)), ("Approve", email_for(SECOND_QA))],
		)
		self.assertEqual(
			frappe.db.get_value(rm.FILE, risk["file"], ["status", "current_revision"]),
			(rm.IN_FORCE, revision.name),
		)
		self.assertEqual(
			frappe.get_all(rm.FILE_ITEM, filters={"parent": risk["file"]}, pluck="item"), [risk["item"]]
		)

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(sheet["second_qa"], email_for(SECOND_QA))
		self.assertTrue(rm.current_policy())
		self.assertEqual((sheet["product"], sheet["operator_job"]), (PRODUCT, OPERATOR_JOB))
		self.assertEqual(
			frappe.db.get_value("Employee", sheet["operator"], ["user_id", "designation", "status"]),
			(email_for("Operator"), OPERATOR_JOB, "Active"),
		)
		self.assertEqual(
			frappe.db.get_value(regulatory_licence.TYPE_DOCTYPE, sheet["licence_type"], "has_expiry"), 1
		)
		self.assertEqual(frappe.db.get_value("File Category", sheet["file_category"], "is_group"), 0)
		self.assertEqual(
			sheet["full_names"],
			{
				QA: get_fullname(email_for(QA)),
				PRODUCTION: get_fullname(email_for(PRODUCTION)),
				SECOND_QA: get_fullname(email_for(SECOND_QA)),
			},
		)
		self.assertEqual(sheet["training_due_days"], 14)
		self.assertGreaterEqual(sheet["licence_warning_days"], 60)
		types = {
			"plan": "SOP",
			"sop": "SOP",
			"specification": "Specification",
			"labelling": "Packaging and Labelling",
		}
		for key, document_type in types.items():
			with self.subTest(document=key):
				self.assertEqual(
					frappe.db.get_value(
						"Controlled Document", sheet["documents"][key], ["status", "document_type"]
					),
					("Effective", document_type),
				)

	def test_a_draft_policy_with_its_scales_and_no_acceptability_decided(self):
		policy = frappe.get_doc(rm.POLICY, d9b.prepare_draft_policy(self.password)["name"])

		self.assertEqual((policy.status, policy.docstatus, policy.owner), (rm.DRAFT, 0, email_for(QA)))
		self.assertEqual([row.level_name for row in policy.severity_levels], list(rm.D13_SEVERITY))
		self.assertEqual([row.level_name for row in policy.probability_levels], list(rm.D13_PROBABILITY))
		self.assertEqual(len(policy.matrix), 25)
		self.assertFalse([row for row in policy.matrix if row.acceptability])

	def test_a_file_in_force_without_a_report(self):
		risk = d9b.prepare_risk_file(self.password, "in_force")

		self.assertFirstRevisionApproved(risk)
		self.assertFalse(frappe.db.get_value(rm.FILE, risk["file"], "current_report"))
		self.assertFalse(rm.revision_in_progress(risk["file"]))
		self.assertNotIn("change_request", risk)

	def test_a_file_whose_periodic_review_has_fallen_due(self):
		risk = d9b.prepare_risk_file(self.password, "periodic_due")

		self.assertFirstRevisionApproved(risk)
		self.assertEqual(
			str(frappe.db.get_value(rm.FILE, risk["file"], "next_periodic_review")), risk["due_on"]
		)
		self.assertEqual(risk["due_on"], str(add_days(today(), -1)))
		self.assertEqual(
			(risk["review_months"], risk["review_due_days"]),
			(
				quality_settings.value("risk_periodic_review_months"),
				quality_settings.value("risk_review_due_days"),
			),
		)
		self.assertFalse(frappe.db.exists(rm.REVIEW, {"risk_management_file": risk["file"]}))

	def test_the_daily_run_raises_the_periodic_review_once_and_reminds_qa(self):
		risk = d9b.prepare_risk_file(self.password, "periodic_due")

		[review] = d9b.run_periodic_reviews(risk["file"])

		self.assertEqual(
			(review.status, review.docstatus, review.source_doctype, review.source_name),
			(rm.OPEN, 0, rm.FILE, risk["file"]),
		)
		self.assertEqual(str(review.due_date), str(add_days(today(), risk["review_due_days"])))
		self.assertEqual([row.name for row in d9b.run_periodic_reviews(risk["file"])], [review.name])
		reminders = d9b.run_daily_reminders("Risk Review", rm.REVIEW, review.name)
		self.assertEqual(
			[(r.status, r.subject) for r in reminders],
			[("Open", f"Risk review {review.name} of {risk['family']}: Periodic")],
		)

	def test_a_file_with_its_report_and_an_approved_change_that_raised_a_review(self):
		risk = d9b.prepare_risk_file(self.password, "change_approved")

		self.assertFirstRevisionApproved(risk)
		report = frappe.get_doc(rm.REPORT, risk["report"])
		self.assertEqual((report.status, report.docstatus), (rm.APPROVED, 1))
		self.assertEqual(frappe.db.get_value(rm.FILE, risk["file"], "current_report"), report.name)
		request = frappe.get_doc("Change Request", risk["change_request"])
		self.assertEqual((request.status, request.impact_risk), ("Approved", "Yes"))
		self.assertEqual(request.requested_by, email_for(PRODUCTION))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[(rm.FILE, risk["file"])],
		)
		review = frappe.get_doc(rm.REVIEW, risk["review"])
		self.assertEqual(
			(review.status, review.trigger, review.source_name, review.risk_management_file),
			(rm.OPEN, rm.CHANGE_REQUEST_TRIGGER, request.name, risk["file"]),
		)
		self.assertFalse(rm.revision_in_progress(risk["file"]))

	def test_a_new_revision_qa_started_under_the_review(self):
		risk = d9b.prepare_risk_file(self.password, "revision_in_progress")

		draft = frappe.get_doc(rm.ASSESSMENT, risk["draft"])
		self.assertEqual(draft.name, f"{risk['file']}-R02")
		self.assertEqual((draft.status, draft.docstatus, draft.author), (rm.DRAFT, 0, email_for(QA)))
		self.assertEqual((draft.change_request, draft.risk_review), (risk["change_request"], risk["review"]))
		self.assertEqual(rm.revision_in_progress(risk["file"]), draft.name)
		self.assertEqual([row.hazard_id for row in draft.hazards], ["H-01"])

	def test_a_licence_copy_in_the_repository(self):
		copy = frappe.get_doc("Important File", d9b.prepare_licence(self.password, "copy")["copy"])

		self.assertEqual(
			(copy.status, copy.current_version, copy.category, copy.owner),
			("Active", 1, d9b.LICENCE_CATEGORY, email_for(QA)),
		)

	def test_a_licence_qa_saved_and_did_not_verify(self):
		lic = d9b.prepare_licence(self.password, "draft")

		licence = frappe.get_doc(regulatory_licence.DOCTYPE, lic["licence"])
		self.assertEqual(
			(licence.status, licence.docstatus, licence.important_file, licence.owner),
			(regulatory_licence.DRAFT, 0, lic["copy"], email_for(QA)),
		)
		self.assertEqual(str(licence.valid_until), lic["valid_until"])
		self.assertEqual(signed(regulatory_licence.DOCTYPE, licence.name), [])
		self.assertNotIn("reason", lic)

	def test_a_licence_qa_verified_against_its_copy(self):
		lic = d9b.prepare_licence(self.password, "verified")

		licence = frappe.get_doc(regulatory_licence.DOCTYPE, lic["licence"])
		self.assertEqual(
			(licence.status, licence.docstatus, licence.important_file, licence.licence_type),
			(regulatory_licence.VERIFIED, 1, lic["copy"], d9b.LICENCE_TYPE),
		)
		self.assertEqual(licence.verified_file_version, 1)
		self.assertEqual(str(licence.valid_until), lic["valid_until"])
		self.assertEqual(signed(regulatory_licence.DOCTYPE, licence.name), [("Verify", email_for(QA))])
		self.assertEqual(licence.reason, lic["reason"])
		for key in ("evidence", "submission_copy"):
			with self.subTest(file=key):
				self.assertEqual(frappe.db.get_value("Important File", lic[key], "status"), "Active")

	def test_a_change_under_assessment_that_does_not_list_the_licence(self):
		change = d9b.prepare_regulatory_change(self.password)

		request = frappe.get_doc("Change Request", change["change_request"])
		self.assertEqual((request.status, request.requested_by), ("Under Assessment", email_for(PRODUCTION)))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[("Item", change["item"])],
		)
		self.assertEqual(
			frappe.db.get_value(regulatory_licence.DOCTYPE, change["licence"], "status"),
			regulatory_licence.VERIFIED,
		)

	def test_the_daily_run_reminds_qa_of_an_obligation_due(self):
		lic = d9b.prepare_licence(self.password, "verified")
		with d9b.act_as(QA):
			regulatory_licence.add_obligation(lic["licence"], "Retention fee", add_days(today(), 60))

		reminders = d9b.run_daily_reminders("Regulatory Licence", regulatory_licence.DOCTYPE, lic["licence"])

		self.assertEqual(
			[(r.status, str(r.due_date)) for r in reminders if r.subject.startswith("Retention fee")],
			[("Open", str(add_days(today(), 60)))],
		)

	def assertOnMatrix(self, made: dict, validity: int) -> None:
		matrix = frappe.get_doc(training.MATRIX, made["matrix"])
		self.assertEqual((matrix.docstatus, matrix.designation), (1, OPERATOR_JOB))
		self.assertEqual(matrix.approved_by, email_for(QA))
		[row] = [row for row in matrix.items if row.controlled_document == made["instruction"]]
		self.assertEqual((row.read_and_understand_allowed, row.validity_months), (0, validity))
		self.assertEqual(signed(training.MATRIX, matrix.name), [("Approve", email_for(QA))])

	def test_a_new_instruction_on_no_matrix(self):
		made = d9b.prepare_training(self.password, "new_instruction")

		self.assertEqual(made["matrix"], d9b.approved_matrix())
		self.assertFalse(
			frappe.db.exists(
				"Training Matrix Item", {"parent": made["matrix"], "controlled_document": made["instruction"]}
			)
		)
		self.assertEqual(
			frappe.db.get_value("Controlled Document", made["instruction"], "current_revision"),
			made["revision"],
		)
		self.assertFalse(frappe.db.exists(training.RECORD, {"controlled_document": made["instruction"]}))

	def test_an_open_training_with_its_reminder(self):
		made = d9b.prepare_training(self.password, "open")

		self.assertOnMatrix(made, 12)
		record = frappe.get_doc(training.RECORD, made["record"])
		self.assertEqual(
			(
				record.status,
				record.trigger,
				record.employee,
				record.document_revision,
				record.validity_months,
			),
			(training.OPEN, training.NEW_ASSIGNMENT, made["operator"], made["revision"], 12),
		)
		self.assertFalse(record.training_method)
		self.assertEqual([r.status for r in made["reminders"]], ["Open"])

	def test_a_training_planned_without_an_assessor(self):
		made = d9b.prepare_training(self.password, "planned_without_assessor")

		self.assertOnMatrix(made, 0)
		record = frappe.get_doc(training.RECORD, made["record"])
		self.assertEqual(
			(record.status, record.training_method, str(record.training_date)),
			(training.OPEN, "On-the-job", today()),
		)
		self.assertFalse(record.assessor)

	def test_a_training_signed_trained_and_competent(self):
		made = d9b.prepare_training(self.password, "signed_competent")

		self.assertOnMatrix(made, 12)
		record = frappe.get_doc(training.RECORD, made["record"])
		self.assertEqual(
			(record.status, record.docstatus, record.employee, record.document_revision),
			(training.COMPETENT, 1, made["operator"], made["revision"]),
		)
		self.assertEqual(str(record.valid_until), str(add_months(today(), 12)))
		self.assertEqual(
			signed(training.RECORD, record.name),
			[("Trained", email_for("Operator")), ("Competent", email_for(PRODUCTION))],
		)

	def test_a_training_for_qa_to_record_from_paper(self):
		made = d9b.prepare_training(self.password, "history")

		self.assertOnMatrix(made, 24)
		self.assertEqual(frappe.db.get_value(training.RECORD, made["record"], "status"), training.OPEN)

	def test_what_paper_history_is_refused_for(self):
		made = d9b.prepare_training(self.password, "history_refusals")

		self.assertOnMatrix(made, 0)
		self.assertEqual(frappe.db.get_value("Controlled Document", made["outside"], "status"), "Effective")
		self.assertFalse(frappe.db.exists("Training Matrix Item", {"controlled_document": made["outside"]}))
		self.assertEqual(frappe.db.get_value("Employee", made["qa_employee"], "user_id"), email_for(QA))
		# Without the user permission ERPNext would add, which would hide every other employee from QA.
		self.assertFalse(frappe.db.exists("User Permission", {"user": email_for(QA), "allow": "Employee"}))

	def test_a_competence_on_a_journey_operation_and_its_job_card(self):
		made = d9b.prepare_revocation(self.password)

		self.assertEqual(made["operation"], OPERATIONS[0][0])
		record = frappe.get_doc(training.RECORD, made["record"])
		self.assertEqual(
			(record.status, record.docstatus, record.employee, record.document_revision),
			(training.COMPETENT, 1, made["operator"], made["revision"]),
		)
		card = frappe.get_doc("Job Card", made["job_card"])
		self.assertEqual(
			(card.docstatus, card.operation, card.work_order), (0, made["operation"], made["work_order"])
		)
		self.assertFalse(card.time_logs)

	def test_restores_a_revoked_competence(self):
		made = d9b.prepare_revocation(self.password)
		with d9b.act_as(QA):
			training.revoke(
				frappe.get_doc(training.RECORD, made["record"]), password=self.password, reason="Test"
			)
		self.assertFalse(d9b.competence_of(made["operator"], made["revision"]))

		d9b.restore_operator_training(self.password)

		self.assertTrue(d9b.competence_of(made["operator"], made["revision"]))
