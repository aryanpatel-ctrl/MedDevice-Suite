"""Training and competence (module 37, URS-TRN-001 to URS-TRN-006): tests derived from the
requirements' clauses, on real document revisions issued through document control."""

import frappe
from frappe.utils import add_days, add_months, getdate, now_datetime, today
from frappe.utils.password import update_password

from meddevice_suite.compliance.esign import SigningRefused, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.lot_control.test_lot_status import company
from meddevice_suite.master_data import routing
from meddevice_suite.master_data.test_routing import RoutingTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.qms import training
from meddevice_suite.qms.test_document_control import DocumentControlTestCase, private_file
from meddevice_suite.work_management import reminders

RECORD = "Training Record"


def person(*roles: str) -> str:
	"""A new user of their own (every operator signs on their own account, plan D-08)."""
	email = f"trainee-{frappe.generate_hash(length=8)}@training.example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Trainee",
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)
	update_password(email, PASSWORD)
	reset_lockout(email)
	return email


def designation(name: str | None = None) -> str:
	name = name or f"MDS Job {frappe.generate_hash(length=6)}"
	if not frappe.db.exists("Designation", name):
		frappe.get_doc({"doctype": "Designation", "designation_name": name}).insert(ignore_permissions=True)
	return name


def employee(job: str | None, user: str | None = None, status: str = "Active") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": f"Operator {frappe.generate_hash(length=4)}",
				"gender": "Female",
				"date_of_birth": "1990-01-01",
				"date_of_joining": add_days(today(), -400),
				"company": company(),
				"status": status,
				"designation": job,
				"user_id": user,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def records(emp: str, **filters) -> list[frappe._dict]:
	return frappe.get_all(
		RECORD,
		filters={"employee": emp, **filters},
		fields=["name", "document_revision", "status", "trigger", "docstatus", "due_date", "valid_until"],
		order_by="creation asc",
	)


class TrainingTestCase(DocumentControlTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.assessor = cls.author  # the Production Supervisor (D-09)

	def sign(
		self, user: str, doctype: str, name: str, act: str, reason: str | None = "Done to the SOP"
	) -> None:
		frappe.set_user(user)
		try:
			sign_action(doctype, name, act, PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def sop(self, document_type: str = "SOP", department: str = "QA"):
		document = self.register(f"Training test {frappe.generate_hash(length=6)}", document_type, department)
		revision = self.issue(document)
		frappe.set_user("Administrator")
		return document, revision

	def matrix(self, job: str, rows: list[dict]) -> str:
		frappe.set_user(self.qa)
		try:
			name = (
				frappe.get_doc({"doctype": "Training Matrix", "designation": job, "items": rows})
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")
		self.sign(self.qa, "Training Matrix", name, "Approve", "Job's documents checked")
		return name

	def job_with(self, *documents, reading: bool = False, months: int = 0) -> str:
		job = designation()
		self.matrix(
			job,
			[
				{
					"controlled_document": d.name,
					"read_and_understand_allowed": int(reading),
					"validity_months": months,
				}
				for d in documents
			],
		)
		return job

	def operator(self, job: str) -> tuple[str, str]:
		user = person("Operator")
		return employee(job, user), user

	def plan(self, name: str, **values) -> None:
		doc = frappe.get_doc(RECORD, name)
		doc.update(
			{
				"training_method": "Instructor-led",
				"training_date": today(),
				"trainer": "QA trainer",
				"assessor": self.assessor,
				"assessment_method": "Practical Demonstration",
				**values,
			}
		)
		doc.save()

	def competent(self, name: str, user: str) -> None:
		self.plan(name)
		self.sign(user, RECORD, name, "Trained")
		self.sign(self.assessor, RECORD, name, "Competent")

	def open_record(self, emp: str) -> str:
		rows = records(emp, status="Open")
		self.assertEqual(len(rows), 1, rows)
		return rows[0].name


@verifies("URS-TRN-001")
class TestNeedsOpen(TrainingTestCase):
	def test_the_matrix_approval_opens_a_record_on_the_revision_in_effect(self):
		document, revision = self.sop()
		job = designation()
		emp, _user = self.operator(job)
		self.assertFalse(records(emp))  # nothing until QA approves the job's matrix
		self.matrix(job, [{"controlled_document": document.name}])
		(row,) = records(emp)
		self.assertEqual(
			(row.document_revision, row.status, row.trigger), (revision.name, "Open", "New Assignment")
		)
		self.assertEqual(getdate(row.due_date), getdate(add_days(today(), 14)))

	def test_a_new_employee_gets_the_jobs_records_and_others_none(self):
		document, _revision = self.sop()
		job = self.job_with(document)
		emp, _user = self.operator(job)
		self.assertEqual(len(records(emp)), 1)
		self.assertFalse(records(employee(designation(), person("Operator"))))  # another job
		self.assertFalse(records(employee(job, person("Operator"), status="Inactive")))

	def test_a_revision_approved_to_take_effect_later_is_trained_before_it(self):
		document, current = self.sop()
		job = self.job_with(document)
		emp, user = self.operator(job)
		self.competent(self.open_record(emp), user)
		later = add_days(today(), 20)
		upcoming = self.issue(document, "Clearer steps", effective_date=later)
		frappe.set_user("Administrator")
		row = records(emp, document_revision=upcoming.name)[0]
		self.assertEqual((row.status, row.trigger), ("Open", "New Revision"))
		self.assertEqual(getdate(row.due_date), getdate(later))  # due by its effective date (D-07)
		self.assertEqual(records(emp, document_revision=current.name)[0].status, "Competent")  # still works

	def test_open_records_on_a_superseded_revision_are_closed(self):
		document, first = self.sop()
		job = self.job_with(document)
		emp, _user = self.operator(job)
		self.issue(document, "Second issue")
		frappe.set_user("Administrator")
		self.assertEqual(records(emp, document_revision=first.name)[0].status, "Superseded")
		self.assertEqual(len(records(emp, status="Open")), 1)

	def test_a_new_job_opens_its_records_and_withdraws_the_old(self):
		old_doc, _r = self.sop()
		new_doc, _r2 = self.sop()
		old_job, new_job = self.job_with(old_doc), self.job_with(new_doc)
		emp, _user = self.operator(old_job)
		staff = frappe.get_doc("Employee", emp)
		staff.designation = new_job
		staff.save(ignore_permissions=True)
		by_document = {
			frappe.db.get_value(RECORD, r.name, "controlled_document"): r.status for r in records(emp)
		}
		self.assertEqual(by_document, {old_doc.name: "Withdrawn", new_doc.name: "Open"})

	def test_a_leavers_open_records_are_withdrawn(self):
		document, _r = self.sop()
		emp, _user = self.operator(self.job_with(document))
		staff = frappe.get_doc("Employee", emp)
		staff.status = "Left"
		staff.relieving_date = today()
		staff.save(ignore_permissions=True)
		self.assertEqual([r.status for r in records(emp)], ["Withdrawn"])

	def test_never_opened_twice_and_not_by_hand(self):
		document, _r = self.sop()
		emp, _user = self.operator(self.job_with(document))
		training.reconcile([emp])
		training.daily()
		self.assertEqual(len(records(emp)), 1)
		frappe.set_user(self.reviewer)  # outside QA
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{
						"doctype": RECORD,
						"employee": emp,
						"controlled_document": document.name,
						"document_revision": records(emp)[0].document_revision,
						"due_date": today(),
						"trigger": "New Assignment",
					}
				).insert()
		finally:
			frappe.set_user("Administrator")

	def test_a_withdrawn_document_closes_its_open_records(self):
		document, _r = self.sop()
		emp, _user = self.operator(self.job_with(document))
		self.as_user(self.qa)
		sign_action("Controlled Document", document.name, "Withdraw", PASSWORD, "Replaced")
		frappe.set_user("Administrator")
		self.assertEqual([r.status for r in records(emp)], ["Superseded"])

	def test_a_failure_does_not_hold_back_the_change_and_the_daily_run_repairs_it(self):
		from unittest.mock import patch

		document, _r = self.sop()
		job = designation()
		emp, _user = self.operator(job)
		with patch.object(training, "open_record", side_effect=RuntimeError("database busy")):
			self.matrix(job, [{"controlled_document": document.name}])
		self.assertEqual(frappe.db.get_value("Training Matrix", {"designation": job}, "docstatus"), 1)
		self.assertFalse(records(emp))
		training.daily()
		self.assertEqual(len(records(emp)), 1)


@verifies("URS-TRN-002")
class TestSignedCompletion(TrainingTestCase):
	def test_the_trainee_then_another_assessor_completes_it(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.competent(name, user)
		doc = frappe.get_doc(RECORD, name)
		self.assertEqual(
			(doc.docstatus, doc.status, doc.trained_by, doc.assessed_by),
			(1, "Competent", user, self.assessor),
		)
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": RECORD, "reference_name": name},
			fields=["meaning", "signer"],
			order_by="signed_at asc, creation asc",
		)
		self.assertEqual(
			[(s.meaning, s.signer) for s in signatures], [("Trained", user), ("Competent", self.assessor)]
		)
		self.assertTrue(verify_signatures(RECORD, name))

	def test_only_the_trainee_signs_trained_and_only_on_their_account(self):
		document, _r = self.sop()
		emp, _user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.plan(name)
		with self.assertRaisesRegex(frappe.PermissionError, "Only the trainee"):
			self.sign(self.assessor, RECORD, name, "Trained")
		job = frappe.db.get_value("Employee", emp, "designation")
		without_account = self.open_record(employee(job, None))
		self.plan(without_account)
		with self.assertRaisesRegex(frappe.ValidationError, "has no user account"):
			self.sign(self.assessor, RECORD, without_account, "Trained")

	def test_only_the_named_assessor_with_the_role_and_never_the_trainee(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		with self.assertRaisesRegex(frappe.ValidationError, "does not assess their own"):
			self.plan(name, assessor=user)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot assess"):
			self.plan(name, assessor=self.reviewer)  # Stores: neither supervisor nor QA
		self.plan(name)
		with self.assertRaisesRegex(frappe.ValidationError, "assessed once the trainee has signed"):
			self.sign(self.assessor, RECORD, name, "Competent")
		self.sign(user, RECORD, name, "Trained")
		with self.assertRaisesRegex(frappe.PermissionError, "named assessor"):
			self.sign(self.qa, RECORD, name, "Competent")

	def test_the_assessor_and_assessment_are_named_before_the_trainee_signs(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.plan(name, assessor=None, assessment_method=None)
		with self.assertRaisesRegex(frappe.ValidationError, "Name the assessor"):
			self.sign(user, RECORD, name, "Trained")

	def test_a_wrong_password_records_no_signature(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.plan(name)
		frappe.set_user(user)
		try:
			with self.assertRaises((SigningRefused, frappe.AuthenticationError)):
				sign_action(RECORD, name, "Trained", "wrong password", None)
		finally:
			frappe.set_user("Administrator")
			reset_lockout(user)
		self.assertEqual(frappe.db.get_value(RECORD, name, "status"), "Open")

	def test_reading_completes_it_only_where_the_matrix_allows_it(self):
		document, _r = self.sop()
		emp, _user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		with self.assertRaisesRegex(frappe.ValidationError, "Reading is not enough"):
			self.plan(name, training_method="Read and Understand")
		allowed, _r2 = self.sop()
		emp2, user2 = self.operator(self.job_with(allowed, reading=True))
		name2 = self.open_record(emp2)
		self.plan(name2, training_method="Read and Understand", assessor=None, assessment_method=None)
		self.sign(user2, RECORD, name2, "Trained")
		self.assertEqual(frappe.db.get_value(RECORD, name2, ["docstatus", "status"]), (1, "Competent"))

	def test_not_competent_needs_a_reason_and_opens_retraining(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.plan(name)
		self.sign(user, RECORD, name, "Trained")
		with self.assertRaisesRegex(frappe.ValidationError, "could not yet do"):
			self.sign(self.assessor, RECORD, name, "Not Competent", reason=None)
		self.sign(self.assessor, RECORD, name, "Not Competent", "Could not set the cutting length")
		self.assertEqual(frappe.db.get_value(RECORD, name, ["docstatus", "status"]), (1, "Not Competent"))
		retraining = records(emp, status="Open")
		self.assertEqual([r.trigger for r in retraining], ["Retraining"])

	def test_completed_only_by_signatures_never_edited_cancelled_or_deleted(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		doc = frappe.get_doc(RECORD, name)
		doc.status = "Competent"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc = frappe.get_doc(RECORD, name)
		doc.flags.ignore_validate = True
		doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			doc.submit()
		self.competent(name, user)
		done = frappe.get_doc(RECORD, name)
		done.outcome_reason = "Changed afterwards"
		with self.assertRaises(frappe.PermissionError):
			done.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(RECORD, name).cancel()
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.delete_doc(RECORD, name, ignore_permissions=True)

	def test_what_was_planned_is_fixed_once_the_trainee_signs(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.plan(name)
		self.sign(user, RECORD, name, "Trained")
		doc = frappe.get_doc(RECORD, name)
		doc.training_date = add_days(today(), -3)
		with self.assertRaisesRegex(frappe.PermissionError, "Fixed once the trainee signed"):
			doc.save()

	def test_qa_alone_enters_paper_history_with_a_private_certificate(self):
		document, revision = self.sop()
		emp, user = self.operator(designation())

		def history(by: str, certificate: str | None) -> str:
			frappe.set_user(by)
			try:
				return (
					frappe.get_doc(
						{
							"doctype": RECORD,
							"employee": emp,
							"controlled_document": document.name,
							"document_revision": revision.name,
							"due_date": today(),
							"trigger": "Historical",
							"training_method": "Instructor-led",
							"training_date": add_days(today(), -200),
							"certificate": certificate,
						}
					)
					.insert()
					.name
				)
			finally:
				frappe.set_user("Administrator")

		with self.assertRaises(frappe.PermissionError):
			history(self.reviewer, None)
		name = history(self.qa, None)
		with self.assertRaisesRegex(frappe.ValidationError, "certificate"):
			self.sign(self.qa, RECORD, name, "Record History", "From the paper training file")
		frappe.db.set_value(RECORD, name, "certificate", private_file("Training certificate"))
		self.sign(self.qa, RECORD, name, "Record History", "From the paper training file")
		doc = frappe.get_doc(RECORD, name)
		self.assertEqual((doc.docstatus, doc.status, doc.is_historical), (1, "Competent", 1))
		with self.assertRaisesRegex(frappe.ValidationError, "is not open"):
			self.sign(user, RECORD, name, "Trained")

	def test_people_see_their_own_records_and_the_jobs_that_manage_training_all(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		other, _other_user = self.operator(self.job_with(document))
		mine, theirs = self.open_record(emp), self.open_record(other)
		frappe.set_user(user)
		try:
			visible = set(frappe.get_list(RECORD, pluck="name"))
			self.assertIn(mine, visible)
			self.assertNotIn(theirs, visible)
			self.assertFalse(frappe.has_permission(RECORD, "read", doc=frappe.get_doc(RECORD, theirs)))
			self.assertFalse(frappe.has_permission(RECORD, "write", doc=frappe.get_doc(RECORD, mine)))
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(self.qa)
		try:
			self.assertTrue({mine, theirs} <= set(frappe.get_list(RECORD, pluck="name", limit=0)))
		finally:
			frappe.set_user("Administrator")


@verifies("URS-TRN-003")
class TestValidityAndRefresher(TrainingTestCase):
	def test_valid_only_as_long_as_the_matrix_says(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document, months=12))
		name = self.open_record(emp)
		self.competent(name, user)
		self.assertEqual(
			getdate(frappe.db.get_value(RECORD, name, "valid_until")), getdate(add_months(today(), 12))
		)
		forever, _r2 = self.sop()
		emp2, user2 = self.operator(self.job_with(forever))
		name2 = self.open_record(emp2)
		self.competent(name2, user2)
		self.assertIsNone(frappe.db.get_value(RECORD, name2, "valid_until"))

	def test_a_refresher_opens_once_within_the_warning_days(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document, months=12))
		name = self.open_record(emp)
		self.competent(name, user)
		frappe.db.set_value(RECORD, name, "valid_until", add_days(today(), 31))
		training.daily()
		self.assertFalse(records(emp, status="Open"))  # not yet: 30 days' warning
		frappe.db.set_value(RECORD, name, "valid_until", add_days(today(), 30))
		training.daily()
		training.daily()
		(refresher,) = records(emp, status="Open")
		self.assertEqual(
			(refresher.trigger, getdate(refresher.due_date)), ("Refresher", getdate(add_days(today(), 30)))
		)
		self.assertEqual(frappe.db.get_value(RECORD, name, "status"), "Competent")  # until it expires

	def test_the_warning_days_are_qas_setting(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document, months=12))
		name = self.open_record(emp)
		self.competent(name, user)
		frappe.db.set_value(RECORD, name, "valid_until", add_days(today(), 20))
		with use_setting("training_expiry_warning_days", 10):
			training.daily()
			self.assertFalse(records(emp, status="Open"))

	def test_open_training_is_a_reminder_to_the_trainee_then_the_assessor(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		due = {d.reference_name: d for d in reminders.training_due()}
		self.assertEqual((due[name].user, due[name].part), (user, "Open"))
		self.plan(name)
		self.sign(user, RECORD, name, "Trained")
		due = {d.reference_name: d for d in reminders.training_due()}
		self.assertEqual((due[name].user, due[name].part), (self.assessor, "Trained"))
		self.sign(self.assessor, RECORD, name, "Competent")
		self.assertNotIn(name, {d.reference_name for d in reminders.training_due()})


@verifies("URS-TRN-004")
class TestMatrixApproval(TrainingTestCase):
	def test_only_qa_approves_it_with_a_signature_and_a_change_is_cancel_and_amend(self):
		document, _r = self.sop()
		job = designation()
		frappe.set_user(self.qa)
		try:
			name = (
				frappe.get_doc(
					{
						"doctype": "Training Matrix",
						"designation": job,
						"items": [{"controlled_document": document.name}],
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			self.sign(self.author, "Training Matrix", name, "Approve", "Checked")
		matrix = frappe.get_doc("Training Matrix", name)
		matrix.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			matrix.submit()
		self.sign(self.qa, "Training Matrix", name, "Approve", "Checked")
		self.assertEqual(
			frappe.db.get_value("Training Matrix", name, ["docstatus", "approved_by"]), (1, self.qa)
		)
		second = frappe.get_doc(
			{
				"doctype": "Training Matrix",
				"designation": job,
				"items": [{"controlled_document": document.name}],
			}
		)
		second.insert(ignore_permissions=True)
		with self.assertRaisesRegex(frappe.ValidationError, "cancel it first"):
			self.sign(self.qa, "Training Matrix", second.name, "Approve", "Checked")

	def test_a_document_is_listed_once_and_never_a_withdrawn_one(self):
		document, _r = self.sop()
		with self.assertRaisesRegex(frappe.ValidationError, "listed twice"):
			frappe.get_doc(
				{
					"doctype": "Training Matrix",
					"designation": designation(),
					"items": [{"controlled_document": document.name}, {"controlled_document": document.name}],
				}
			).insert(ignore_permissions=True)

	def test_a_cancelled_matrix_withdraws_what_it_needed(self):
		document, _r = self.sop()
		job = designation()
		name = self.matrix(job, [{"controlled_document": document.name}])
		emp, _user = self.operator(job)
		frappe.set_user(self.qa)
		try:
			matrix = frappe.get_doc("Training Matrix", name)
			matrix.flags.audit_reason = "The job is no longer staffed"  # the audit trail's question
			matrix.cancel()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual([r.status for r in records(emp)], ["Withdrawn"])


@verifies("URS-TRN-006")
class TestRevocation(TrainingTestCase):
	def test_qa_revokes_with_a_reason_and_retraining_opens(self):
		document, _r = self.sop()
		emp, user = self.operator(self.job_with(document))
		name = self.open_record(emp)
		self.competent(name, user)
		with self.assertRaises(frappe.PermissionError):
			self.sign(self.assessor, RECORD, name, "Revoke", "Not following the SOP")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			self.sign(self.qa, RECORD, name, "Revoke", None)
		self.sign(self.qa, RECORD, name, "Revoke", "NC-0042: steps skipped")
		self.assertEqual(frappe.db.get_value(RECORD, name, ["status", "revoked_by"]), ("Revoked", self.qa))
		self.assertEqual([r.trigger for r in records(emp, status="Open")], ["Retraining"])
		self.assertTrue(verify_signatures(RECORD, name))


@verifies("URS-TRN-005")
class TestJobCardGate(TrainingTestCase, RoutingTestCase):
	def line(self) -> tuple[str, str, object, object]:
		"""An approved workstation and operation whose work instruction has a revision in effect."""
		instruction = self.register(f"WI {frappe.generate_hash(length=6)}", "Work Instruction", "PRD")
		revision = self.issue(instruction)
		frappe.set_user("Administrator")
		ws = self.workstation()
		self.approve("Workstation", ws)
		op = self.operation(ws, **{routing.WORK_INSTRUCTION: instruction.name})
		self.approve("Operation", op)
		return ws, op, instruction, revision

	def card(self, ws: str, op: str, emp: str | None, **log):
		card = frappe.get_doc(
			{
				"doctype": "Job Card",
				"operation": op,
				"workstation": ws,
				"company": company(),
				"for_quantity": 1,
				"time_logs": [
					{
						"employee": emp,
						"from_time": now_datetime(),
						"to_time": frappe.utils.add_to_date(now_datetime(), minutes=10),
						"completed_qty": 1,
						**log,
					}
				],
			}
		)
		card.flags.ignore_mandatory = True  # no work order: the operator's training is what is tested
		return card

	def trained(self, instruction) -> tuple[str, str]:
		emp, user = self.operator(self.job_with(instruction))
		self.competent(self.open_record(emp), user)
		return emp, user

	def test_a_competent_operator_logs_time(self):
		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		self.assertTrue(self.card(ws, op, emp).insert().name)

	def test_untrained_trained_but_unassessed_and_unnamed_operators_are_refused(self):
		ws, op, instruction, _rev = self.line()
		emp, user = self.operator(self.job_with(instruction))
		with self.assertRaisesRegex(frappe.ValidationError, "is not competent in"):
			self.card(ws, op, emp).insert()
		name = self.open_record(emp)
		self.plan(name)
		self.sign(user, RECORD, name, "Trained")
		with self.assertRaisesRegex(frappe.ValidationError, "is not competent in"):
			self.card(ws, op, emp).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "without the operator"):
			self.card(ws, op, None).insert()

	def test_competence_on_the_previous_revision_is_not_enough(self):
		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		self.issue(instruction, "New fixture")
		frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.ValidationError, "revision 01"):
			self.card(ws, op, emp).insert()

	def test_expired_revoked_or_inactive_is_refused(self):
		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		name = records(emp, status="Competent")[0].name
		frappe.db.set_value(RECORD, name, "valid_until", add_days(today(), -1))
		with self.assertRaisesRegex(frappe.ValidationError, "is not competent"):
			self.card(ws, op, emp).insert()
		frappe.db.set_value(RECORD, name, "valid_until", None)
		frappe.db.set_value("Employee", emp, "status", "Inactive")
		with self.assertRaisesRegex(frappe.ValidationError, "not an active employee"):
			self.card(ws, op, emp).insert()

	def test_paper_history_on_the_revision_in_effect_counts(self):
		ws, op, instruction, revision = self.line()
		emp, _user = self.operator(designation())
		frappe.set_user(self.qa)
		try:
			name = (
				frappe.get_doc(
					{
						"doctype": RECORD,
						"employee": emp,
						"controlled_document": instruction.name,
						"document_revision": revision.name,
						"due_date": today(),
						"trigger": "Historical",
						"training_method": "On-the-job",
						"training_date": add_days(today(), -30),
						"certificate": private_file("Paper training record"),
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")
		self.sign(self.qa, RECORD, name, "Record History", "From the paper training file")
		self.assertTrue(self.card(ws, op, emp).insert().name)

	def test_refused_when_saved_without_validation_and_after_submit(self):
		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		stranger, _u = self.operator(designation())
		card = self.card(ws, op, emp).insert()
		card.append("time_logs", {"employee": stranger, "from_time": now_datetime(), "completed_qty": 0})
		card.flags.ignore_validate = True
		with self.assertRaisesRegex(frappe.ValidationError, "is not competent"):
			card.save()
		events = frappe.get_hooks("doc_events")["Job Card"]
		self.assertIn("meddevice_suite.qms.training.guard_job_card", events["before_update_after_submit"])

	def test_finished_time_is_not_judged_again(self):
		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		card = self.card(ws, op, emp).insert()
		name = records(emp, status="Competent")[0].name
		frappe.db.set_value(RECORD, name, "valid_until", add_days(today(), -1))  # expired since
		again = frappe.get_doc("Job Card", card.name)
		again.flags.ignore_mandatory = True
		again.save()  # the finished log was right when it was made

	def test_refused_through_erpnexts_time_log_method(self):
		from meddevice_suite.qms import equipment_care

		ws, op, instruction, _rev = self.line()
		emp, _user = self.trained(instruction)
		stranger, _u = self.operator(designation())
		card = self.card(ws, op, emp).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "is not competent"):
			equipment_care.make_time_log(
				{"job_card_id": card.name, "from_time": now_datetime(), "employees": [{"employee": stranger}]}
			)
		with self.assertRaisesRegex(frappe.ValidationError, "without the operator"):
			equipment_care.make_time_log({"job_card_id": card.name, "from_time": now_datetime()})

	def test_every_whitelisted_time_log_path_is_guarded(self):
		"""ERPNext's job card methods that log time: each either saves the card (its hooks run) or
		is overridden. A new one from an upgrade fails this test until it is looked at."""
		import inspect

		from erpnext.manufacturing.doctype.job_card import job_card

		saving = {"start_timer", "pause_job", "resume_job", "complete_job_card"}
		overridden = set(frappe.get_hooks("override_whitelisted_methods"))
		self.assertIn("erpnext.manufacturing.doctype.job_card.job_card.make_time_log", overridden)
		found = {
			name
			for name, fn in inspect.getmembers(job_card.JobCard, inspect.isfunction)
			if fn in frappe.whitelisted and "time_log" in inspect.getsource(fn)
		}
		self.assertEqual(found - saving, set(), "a new ERPNext path that logs time")
		import re

		def saves(name: str, depth: int = 3) -> bool:
			"""Whether the method, or one it calls on the card, reaches add_time_logs (which saves)."""
			source = inspect.getsource(getattr(job_card.JobCard, name))
			if "self.add_time_logs(" in source or "self.save()" in source:
				return True
			calls = set(re.findall(r"self\.(\w+)\(", source)) - {name}
			return depth > 0 and any(
				saves(call, depth - 1)
				for call in calls
				if inspect.isfunction(getattr(job_card.JobCard, call, None))
			)

		for name in saving:
			self.assertTrue(saves(name), name)
		self.assertIn("self.save()", inspect.getsource(job_card.JobCard.add_time_logs))
