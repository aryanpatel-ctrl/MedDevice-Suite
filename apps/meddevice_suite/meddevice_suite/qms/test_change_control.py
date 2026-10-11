from unittest.mock import patch

import frappe
from frappe.email.doctype.notification.notification import Notification
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today
from frappe.utils.password import update_password

from meddevice_suite.compliance.esign import is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import SIGNING_ACTIONS, sign_action
from meddevice_suite.compliance.test_alerts import alerts_for, failed_alerts
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.qa import verifies
from meddevice_suite.qms import change_control
from meddevice_suite.qms.change_control import (
	AFFECTED_TYPES,
	RESULT_TYPES,
	approved_changes_for,
	complete_action,
	query_conditions,
	send_change_alerts,
	submit_for_assessment,
	withdraw,
)
from meddevice_suite.setup.roles import BUSINESS_ROLES, apply_roles
from meddevice_suite.setup.test_roles import user_with_profile

DOCTYPE = "Change Request"
REFUSED = (frappe.ValidationError, frappe.PermissionError)  # Frappe or this app, whichever checks first
JOBS = ("Production Supervisor", "QA Manager", "Management", "Sales", "Stores", "System Administrator")


def signer(profile: str) -> str:
	user = user_with_profile(profile)
	update_password(user, PASSWORD)
	reset_lockout(user)
	return user


def dual_role_user() -> str:
	"""Someone who is both QA and Management, to show one person cannot approve a change twice."""
	email = "qa.and.management@change-control.example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "QA and Management",
				"send_welcome_email": 0,
				"roles": [{"role": "Quality Manager"}, {"role": "Manufacturing Manager"}],
			}
		).insert(ignore_permissions=True)
	update_password(email, PASSWORD)
	reset_lockout(email)
	return email


def approved_change(
	requester: str,
	qa: str,
	document: str,
	record_type: str = "Controlled Document",
	change_type: str = "Document",
) -> str:
	"""A Minor change request listing a record (a controlled document unless told otherwise),
	approved by QA. Leaves the session as QA."""
	frappe.set_user(requester)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"title": f"Revise {document}",
			"change_type": change_type,
			"description": "Update the procedure",
			"reason": "Found in an internal audit",
			"affected_records": [{"reference_doctype": record_type, "reference_name": document}],
			"actions": [
				{"description": f"New revision of {document}", "action_owner": requester, "due_date": today()}
			],
		}
	).insert()
	submit_for_assessment(doc.name)
	frappe.set_user(qa)
	doc = frappe.get_doc(DOCTYPE, doc.name)
	doc.update(assessment())
	doc.save()
	sign_action(DOCTYPE, doc.name, "Assess", PASSWORD)
	sign_action(DOCTYPE, doc.name, "Approve", PASSWORD)
	return doc.name


def assessment(classification: str = "Minor", regulatory: str = "No", days: int = 90) -> dict:
	return {
		**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
		"impact_regulatory": regulatory,
		"impact_summary": "No effect on the device; the procedure is clarified.",
		"classification": classification,
		"effectiveness_check_days": days,
	}


class ChangeControlTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		cls.requester, cls.qa, cls.manager, cls.outsider, cls.stores, cls.admin = (
			signer(job) for job in JOBS
		)
		frappe.set_user(cls.qa)
		cls.document = (
			frappe.get_doc(
				{
					"doctype": "Controlled Document",
					"title": "Incoming inspection of silicone",
					"document_type": "SOP",
					"department": "QC",
					"process_owner": cls.requester,
				}
			)
			.insert()
			.name
		)
		frappe.set_user("Administrator")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.flags.audit_cause = None
		self.failed_alerts = failed_alerts()

	def tearDown(self):
		self.assertEqual(failed_alerts(), self.failed_alerts, "an alert template failed")

	def as_user(self, user: str) -> None:
		frappe.set_user(user)

	def raise_change(self, user: str | None = None, **values):
		self.as_user(user or self.requester)
		return frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"title": "Clarify the silicone inspection",
				"change_type": "Document",
				"description": "Add the hardness check to the incoming inspection",
				"reason": "A supplier lot passed with the wrong hardness",
				"affected_records": [
					{"reference_doctype": "Controlled Document", "reference_name": self.document}
				],
				"actions": [
					{
						"description": "Issue the new revision",
						"action_owner": self.requester,
						"due_date": today(),
					},
					{
						"description": "Train the inspectors",
						"action_owner": self.stores,
						"due_date": add_days(today(), 10),
					},
				],
				**values,
			}
		).insert()

	def act(self, user: str, name: str, action: str, reason: str | None = None):
		self.as_user(user)
		sign_action(DOCTYPE, name, action, PASSWORD, reason)
		return frappe.get_doc(DOCTYPE, name)

	def submitted(self, **values):
		doc = self.raise_change(**values)
		submit_for_assessment(doc.name)
		return frappe.get_doc(DOCTYPE, doc.name)

	def assessed(self, classification: str = "Minor", regulatory: str = "No", days: int = 90, **values):
		doc = self.submitted(**values)
		self.as_user(self.qa)
		doc.update(assessment(classification, regulatory, days))
		doc.save()
		return self.act(self.qa, doc.name, "Assess")

	def approved(self, classification: str = "Minor", days: int = 90, **values):
		doc = self.assessed(classification, days=days, **values)
		doc = self.act(self.qa, doc.name, "Approve")
		if classification == "Major":
			doc = self.act(self.manager, doc.name, "Approve")
		return doc

	def implemented(self, days: int = 0):
		doc = self.approved(days=days)
		for row in doc.actions:
			self.as_user(row.action_owner)
			complete_action(doc.name, row.name, f"{row.description}: done")
		return frappe.get_doc(DOCTYPE, doc.name)

	def signatures(self, name: str) -> list[tuple[str, str, str]]:
		return [
			(row.signer, row.action, row.meaning)
			for row in frappe.get_all(
				"Electronic Signature",
				filters={"reference_doctype": DOCTYPE, "reference_name": name},
				fields=["signer", "action", "meaning"],
				order_by="signed_at asc, creation asc",
			)
		]


@verifies("URS-QMS-009")
class TestRaising(ChangeControlTestCase):
	def test_anyone_in_a_business_job_raises_a_numbered_draft(self):
		for user in (self.requester, self.outsider, self.stores, self.qa):
			with self.subTest(user=user):
				doc = self.raise_change(user)
				self.assertRegex(doc.name, rf"^CR-{getdate(today()).year}-\d{{4}}$")
				self.assertEqual(
					(doc.status, doc.requested_by, str(doc.requested_on)), ("Draft", user, today())
				)

	def test_every_job_finds_change_requests_on_its_desk(self):
		from meddevice_suite.setup.desk import DESK_AREAS

		self.assertEqual(DESK_AREAS["Documents"], ("Desk User",))
		self.assertTrue(
			frappe.db.exists(
				"Workspace Sidebar Item",
				{"parent": "Documents", "link_type": "DocType", "link_to": DOCTYPE},
			)
		)

	def test_requests_are_numbered_in_sequence(self):
		first, second = self.raise_change(), self.raise_change()
		self.assertEqual(int(second.name.rsplit("-", 1)[1]), int(first.name.rsplit("-", 1)[1]) + 1)

	def test_the_system_administrator_cannot_raise_a_change(self):
		with self.assertRaises(frappe.PermissionError):
			self.raise_change(self.admin)
		# Not even through code that skips the permission check.
		self.as_user(self.admin)
		doc = frappe.get_doc(
			{"doctype": DOCTYPE, "title": "x", "change_type": "System", "description": "x", "reason": "x"}
		)
		with self.assertRaisesRegex(frappe.PermissionError, "someone in a business job"):
			doc.insert(ignore_permissions=True)

	def test_what_the_system_sets_is_discarded_on_a_new_request(self):
		doc = self.raise_change(
			status="Approved",
			classification="Minor",
			qa_approver=self.qa,
			approved_on=today(),
			impact_regulatory="No",
			effectiveness_check_days=0,
		)
		doc.reload()
		self.assertEqual(doc.status, "Draft")
		self.assertFalse(any((doc.classification, doc.qa_approver, doc.approved_on, doc.impact_regulatory)))
		self.assertEqual(doc.effectiveness_check_days, 90)

	def test_an_action_is_not_marked_done_on_a_draft(self):
		doc = self.raise_change()
		doc.actions[0].done = 1
		with self.assertRaisesRegex(frappe.PermissionError, "only once the change is approved"):
			doc.save()

	def test_an_action_cannot_arrive_done(self):
		doc = self.raise_change(
			actions=[{"description": "x", "action_owner": self.requester, "due_date": today(), "done": 1}]
		)
		self.assertEqual(doc.actions[0].done, 0)

	def test_what_why_and_type_are_required(self):
		for field in ("title", "change_type", "description", "reason"):
			with self.subTest(field=field), self.assertRaises(frappe.MandatoryError):
				self.raise_change(**{field: None})

	def test_it_is_sent_for_assessment_only_with_the_records_it_affects(self):
		doc = self.raise_change(affected_records=[])
		with self.assertRaisesRegex(frappe.ValidationError, "List the records the change affects"):
			submit_for_assessment(doc.name)

		system = self.raise_change(change_type="System", affected_records=[])
		submit_for_assessment(system.name)
		self.assertEqual(frappe.db.get_value(DOCTYPE, system.name, "status"), "Under Assessment")

	def test_only_the_person_who_raised_it_sends_it_for_assessment(self):
		doc = self.raise_change()
		self.as_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			submit_for_assessment(doc.name)

	def test_only_a_draft_is_sent_for_assessment(self):
		doc = self.submitted()
		self.as_user(self.requester)
		with self.assertRaisesRegex(frappe.ValidationError, "Only a draft"):
			submit_for_assessment(doc.name)

	def test_the_person_who_raised_it_edits_the_draft_and_nothing_after(self):
		doc = self.raise_change()
		doc.description = "Add the hardness and the tensile check"
		doc.save()

		submit_for_assessment(doc.name)
		doc.reload()
		doc.description = "Something else"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_nobody_else_edits_a_draft(self):
		doc = self.raise_change()
		self.as_user(self.qa)
		doc.reload()
		doc.reason = "QA's own reason"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_the_affected_records_are_the_declared_record_types(self):
		meta = frappe.get_meta("Change Request Affected Record")
		self.assertEqual(tuple(meta.get_field("reference_doctype").options.split("\n")), AFFECTED_TYPES)
		action = frappe.get_meta("Change Request Action")
		self.assertEqual(tuple(action.get_field("target_doctype").options.split("\n")[1:]), AFFECTED_TYPES)
		self.assertEqual(tuple(action.get_field("result_doctype").options.split("\n")[1:]), RESULT_TYPES)
		for doctype in RESULT_TYPES:
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)

	def test_an_affected_record_must_exist(self):
		with self.assertRaises(frappe.LinkValidationError):
			self.raise_change(
				affected_records=[
					{"reference_doctype": "Controlled Document", "reference_name": "SOP-QC-999"}
				]
			)

	def test_nothing_is_done_under_it_before_approval(self):
		doc = self.submitted()
		self.as_user(self.requester)
		with self.assertRaisesRegex(frappe.ValidationError, "once the change is approved"):
			complete_action(doc.name, doc.actions[0].name, "done early")


@verifies("URS-QMS-010")
class TestAssessment(ChangeControlTestCase):
	def test_qa_assesses_and_signs(self):
		doc = self.assessed()
		self.assertEqual(
			(doc.status, doc.assessed_by, str(doc.assessed_on)), ("Awaiting Approval", self.qa, today())
		)
		self.assertEqual(self.signatures(doc.name), [(self.qa, "Assess", "Reviewed")])

	def test_every_area_of_impact_is_recorded_before_it_can_be_assessed(self):
		for field in change_control.IMPACT_AREAS:
			with self.subTest(field=field):
				doc = self.submitted()
				self.as_user(self.qa)
				doc.update({**assessment(), field: None})
				doc.save()
				with self.assertRaisesRegex(frappe.ValidationError, "Record the impact on"):
					self.act(self.qa, doc.name, "Assess")

	def test_a_summary_and_a_classification_are_needed(self):
		for missing in ("impact_summary", "classification"):
			with self.subTest(missing=missing):
				doc = self.submitted()
				self.as_user(self.qa)
				doc.update({**assessment(), missing: None})
				doc.save()
				with self.assertRaises(frappe.ValidationError):
					self.act(self.qa, doc.name, "Assess")

	def test_a_change_needing_regulatory_notification_is_always_major(self):
		with self.assertRaisesRegex(frappe.ValidationError, "regulatory notification is a Major change"):
			self.assessed("Minor", regulatory="Yes")
		self.assertEqual(self.assessed("Major", regulatory="Yes").classification, "Major")

	def test_the_check_is_due_within_the_allowed_days(self):
		with self.assertRaisesRegex(frappe.ValidationError, "within 730 days"):
			self.assessed(days=731)

	def test_the_plan_needs_an_action_owned_by_an_active_person_due_from_today(self):
		with self.assertRaisesRegex(frappe.ValidationError, "at least one action"):
			self.assessed(actions=[])

		self.as_user("Administrator")
		inactive = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"left.{frappe.generate_hash(length=6)}@change-control.example.com",
				"first_name": "Left",
			}
		).insert(ignore_permissions=True)
		inactive.db_set("enabled", 0)
		with self.assertRaisesRegex(frappe.ValidationError, "not an active user"):
			self.assessed(actions=[{"description": "x", "action_owner": inactive.name, "due_date": today()}])

		with self.assertRaisesRegex(frappe.ValidationError, "due in the past"):
			self.assessed(
				actions=[
					{"description": "x", "action_owner": self.requester, "due_date": add_days(today(), -1)}
				]
			)

	def test_only_qa_assesses(self):
		doc = self.submitted()
		self.as_user(self.qa)
		doc.update(assessment())
		doc.save()
		with self.assertRaises(frappe.PermissionError):
			self.act(self.manager, doc.name, "Assess")

	def test_only_a_request_under_assessment_is_assessed(self):
		doc = self.raise_change()
		with self.assertRaisesRegex(frappe.ValidationError, "under assessment"):
			self.act(self.qa, doc.name, "Assess")

	def test_qa_edits_only_the_assessment_and_the_plan(self):
		doc = self.submitted()
		self.as_user(self.qa)
		doc.update(assessment())
		doc.append(
			"actions", {"description": "Update the form", "action_owner": self.qa, "due_date": today()}
		)
		doc.save()

		doc.reason = "QA rewrites the reason"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_an_assessed_request_is_no_longer_edited(self):
		doc = self.assessed()
		self.as_user(self.qa)
		doc.classification = "Major"
		with self.assertRaises(frappe.PermissionError):
			doc.save()


@verifies("URS-QMS-011")
class TestApproval(ChangeControlTestCase):
	def test_qa_approves_a_minor_change(self):
		doc = self.approved()
		self.assertEqual(
			(doc.status, doc.docstatus, doc.qa_approver, str(doc.approved_on)),
			("Approved", 1, self.qa, today()),
		)
		self.assertIsNone(doc.management_approver)
		self.assertEqual(
			self.signatures(doc.name), [(self.qa, "Assess", "Reviewed"), (self.qa, "Approve", "Approved")]
		)
		self.assertTrue(verify_signatures(DOCTYPE, doc.name))

	def test_a_major_change_needs_management_after_qa(self):
		doc = self.act(self.qa, self.assessed("Major").name, "Approve")
		self.assertEqual((doc.status, doc.docstatus), ("Awaiting Management Approval", 0))

		doc = self.act(self.manager, doc.name, "Approve")
		self.assertEqual((doc.status, doc.docstatus, doc.management_approver), ("Approved", 1, self.manager))
		self.assertEqual(
			[meaning for _signer, _action, meaning in self.signatures(doc.name)],
			["Reviewed", "Approved", "Approved"],
		)

	def test_one_person_cannot_approve_a_major_change_twice(self):
		dual = dual_role_user()
		doc = self.act(dual, self.assessed("Major").name, "Approve")
		with self.assertRaisesRegex(frappe.ValidationError, "second approver"):
			self.act(dual, doc.name, "Approve")

	def test_the_person_who_raised_it_never_approves_it(self):
		doc = self.raise_change(self.qa)
		submit_for_assessment(doc.name)
		doc.reload()
		doc.update(assessment())
		doc.save()
		self.act(self.qa, doc.name, "Assess")
		with self.assertRaisesRegex(frappe.ValidationError, "cannot approve it"):
			self.act(self.qa, doc.name, "Approve")

	def test_each_approval_is_its_own_role(self):
		with self.assertRaises(frappe.PermissionError):
			self.act(self.manager, self.assessed().name, "Approve")
		awaiting_management = self.act(self.qa, self.assessed("Major").name, "Approve")
		with self.assertRaises(frappe.PermissionError):
			self.act(self.qa, awaiting_management.name, "Approve")

	def test_only_an_assessed_request_is_approved(self):
		with self.assertRaisesRegex(frappe.ValidationError, "awaiting approval can be approved"):
			self.act(self.qa, self.submitted().name, "Approve")

	def test_a_wrong_password_records_nothing(self):
		doc = self.assessed()
		self.as_user(self.qa)
		with self.assertRaises(frappe.ValidationError):
			sign_action(DOCTYPE, doc.name, "Approve", "wrong password")
		reset_lockout(self.qa)
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "status"), "Awaiting Approval")

	def test_qa_returns_it_with_a_reason_and_it_is_assessed_afresh(self):
		doc = self.act(self.qa, self.assessed().name, "Return", "State the hardness limits")
		self.assertEqual(
			(doc.status, doc.decision_reason, doc.assessed_by), ("Draft", "State the hardness limits", None)
		)

		self.as_user(self.requester)
		doc.description = "Add the hardness check, 50 to 60 Shore A"
		doc.save()
		submit_for_assessment(doc.name)
		self.assertEqual(frappe.db.get_value(DOCTYPE, doc.name, "status"), "Under Assessment")

	def test_returning_or_rejecting_needs_a_reason(self):
		for action in ("Return", "Reject"):
			with self.subTest(action=action), self.assertRaises(frappe.ValidationError):
				self.act(self.qa, self.assessed().name, action)

	def test_a_rejected_request_is_final(self):
		doc = self.act(self.qa, self.submitted().name, "Reject", "Not needed: covered by SOP-QC-002")
		self.assertEqual((doc.status, doc.docstatus), ("Rejected", 0))
		self.as_user(self.requester)
		with self.assertRaises(frappe.ValidationError):
			submit_for_assessment(doc.name)
		doc.title = "Try again"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_management_returns_or_rejects_only_a_major_change_awaiting_it(self):
		with self.assertRaises(frappe.PermissionError):
			self.act(self.manager, self.assessed().name, "Reject", "Too costly")
		awaiting = self.act(self.qa, self.assessed("Major").name, "Approve")
		doc = self.act(self.manager, awaiting.name, "Return", "Cost it first")
		self.assertEqual((doc.status, doc.qa_approver), ("Draft", None))

	def test_frappes_own_submit_never_approves_it(self):
		doc = self.assessed()
		self.as_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			doc.submit()
		self.as_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, doc.name).submit()

	def test_nobody_may_submit_cancel_delete_share_or_import_a_change_request(self):
		for perm in frappe.get_meta(DOCTYPE).permissions:
			with self.subTest(role=perm.role):
				self.assertFalse(
					any(perm.get(flag) for flag in ("submit", "cancel", "amend", "delete", "share", "import"))
				)

	def test_the_signed_acts_are_declared(self):
		acts = {action for doctype, action in SIGNING_ACTIONS if doctype == DOCTYPE}
		self.assertEqual(
			acts, {"Assess", "Approve", "Return", "Reject", "Verify Effective", "Verify Not Effective"}
		)


@verifies("URS-QMS-012")
class TestActionsWithoutARecord(ChangeControlTestCase):
	@verifies("URS-QMS-012")
	def test_an_action_that_changes_no_record_is_approved_and_done(self):
		"""Training the inspectors changes no record; the action needs none."""
		name = approved_change(self.requester, self.qa, self.document)
		actions = frappe.get_doc(DOCTYPE, name).actions
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "status"), "Approved")
		self.assertTrue(actions)
		self.assertTrue(all(not action.target_doctype and not action.target_name for action in actions))


class TestPrintout(ChangeControlTestCase):
	@verifies("URS-CMP-003")
	def test_the_printout_shows_the_assessment_and_approval_signatures(self):
		name = approved_change(self.requester, self.qa, self.document)
		html = frappe.get_print("Change Request", name, print_format="Change Request (Signed)")
		for text in ("Electronic signatures", "Reviewed", "Approved", frappe.utils.get_fullname(self.qa)):
			self.assertIn(text, html)


class TestImplementation(ChangeControlTestCase):
	def test_each_owner_records_their_action_and_the_change_is_implemented(self):
		doc = self.approved(days=30)
		first, second = doc.actions
		self.as_user(self.requester)
		complete_action(doc.name, first.name, "Revision 01 issued")
		doc.reload()
		self.assertEqual(
			(doc.status, doc.actions[0].done, doc.actions[0].done_by), ("Approved", 1, self.requester)
		)

		self.as_user(self.stores)
		complete_action(doc.name, second.name, "Inspectors trained on 8 October")
		doc.reload()
		self.assertEqual((doc.status, str(doc.implemented_on)), ("Implemented", today()))
		self.assertEqual(str(doc.effectiveness_due_on), add_days(today(), 30))

	def test_qa_may_record_any_action_and_nobody_else(self):
		doc = self.approved()
		self.as_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			complete_action(doc.name, doc.actions[0].name, "done")
		self.as_user(self.requester)
		with self.assertRaises(frappe.PermissionError):
			complete_action(doc.name, doc.actions[1].name, "not mine")
		self.as_user(self.qa)
		complete_action(doc.name, doc.actions[1].name, "Training checked by QA")

	def test_a_note_is_needed_and_an_action_is_done_once(self):
		doc = self.approved()
		self.as_user(self.requester)
		with self.assertRaisesRegex(frappe.ValidationError, "Say what was done"):
			complete_action(doc.name, doc.actions[0].name, " ")
		complete_action(doc.name, doc.actions[0].name, "done")
		with self.assertRaisesRegex(frappe.ValidationError, "already done"):
			complete_action(doc.name, doc.actions[0].name, "again")

	def test_a_resulting_record_must_exist(self):
		doc = self.approved()
		self.as_user(self.requester)
		with self.assertRaises(frappe.ValidationError):
			complete_action(doc.name, doc.actions[0].name, "done", "Document Revision", "SOP-QC-999-REV-01")
		with self.assertRaises(frappe.ValidationError):
			complete_action(doc.name, doc.actions[0].name, "done", "Sales Order", self.document)
		complete_action(doc.name, doc.actions[0].name, "done", "Controlled Document", self.document)
		doc.reload()
		self.assertEqual(
			(doc.actions[0].result_doctype, doc.actions[0].result_name),
			("Controlled Document", self.document),
		)

	def test_the_approved_plan_cannot_be_changed(self):
		doc = self.approved()
		self.as_user(self.qa)
		doc.actions[0].due_date = add_days(today(), 60)
		with self.assertRaises(REFUSED):
			doc.save()
		doc.reload()
		doc.append("actions", {"description": "Extra", "action_owner": self.qa, "due_date": today()})
		with self.assertRaises(REFUSED):
			doc.save()

	def test_doing_the_actions_keeps_what_was_signed(self):
		doc = self.implemented()
		approval = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": DOCTYPE, "reference_name": doc.name, "action": "Approve"},
		)
		self.assertTrue(is_unchanged_since(approval))
		self.assertTrue(verify_signatures(DOCTYPE, doc.name))

	def test_each_action_done_is_in_the_audit_trail_with_its_note(self):
		doc = self.implemented()
		reasons = frappe.get_all(
			"Audit Event",
			filters={
				"reference_doctype": DOCTYPE,
				"reference_name": doc.name,
				"action": "Updated after submission",
			},
			pluck="reason",
		)
		self.assertEqual(
			sorted(reasons),
			["Action 1 done: Issue the new revision: done", "Action 2 done: Train the inspectors: done"],
		)


@verifies("URS-QMS-013")
class TestEffectivenessCheck(ChangeControlTestCase):
	def test_qa_closes_it_with_a_signed_effectiveness_check(self):
		doc = self.act(
			self.qa, self.implemented().name, "Verify Effective", "No hardness rejects in 3 months"
		)
		self.assertEqual(
			(doc.status, doc.effectiveness_outcome, doc.effectiveness_note, str(doc.closed_on)),
			("Closed", "Effective", "No hardness rejects in 3 months", today()),
		)
		self.assertEqual(self.signatures(doc.name)[-1], (self.qa, "Verify Effective", "Verified"))
		self.assertTrue(verify_signatures(DOCTYPE, doc.name))

	def test_a_change_found_not_effective_is_closed_as_such(self):
		doc = self.act(
			self.qa, self.implemented().name, "Verify Not Effective", "Two rejects since; CAPA raised"
		)
		self.assertEqual((doc.status, doc.effectiveness_outcome), ("Closed", "Not effective"))

	def test_not_before_the_check_date(self):
		doc = self.approved(days=30)
		for row in doc.actions:
			self.as_user(self.qa)
			complete_action(doc.name, row.name, "done")
		with self.assertRaisesRegex(frappe.ValidationError, "checked on or after"):
			self.act(self.qa, doc.name, "Verify Effective", "Too early")

	def test_only_qa_checks_and_with_a_note(self):
		name = self.implemented().name
		with self.assertRaises(frappe.PermissionError):
			self.act(self.manager, name, "Verify Effective", "Looks fine")
		with self.assertRaisesRegex(frappe.ValidationError, "how the effectiveness was checked"):
			self.act(self.qa, name, "Verify Effective")

	def test_only_an_implemented_change_is_checked(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Only an implemented change"):
			self.act(self.qa, self.approved().name, "Verify Effective", "Early")

	def test_a_closed_change_is_never_changed(self):
		doc = self.act(self.qa, self.implemented().name, "Verify Effective", "Fine")
		self.as_user(self.qa)
		doc.effectiveness_note = "Changed"
		with self.assertRaises(REFUSED):
			doc.save()


@verifies("URS-QMS-014")
class TestRevisionsUnderChangeControl(ChangeControlTestCase):
	def effective_document(self) -> str:
		from meddevice_suite.qms.test_document_control import private_file

		self.as_user(self.qa)
		document = frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": "Release of finished goods",
				"document_type": "SOP",
				"department": "QC",
				"process_owner": self.requester,
			}
		).insert()
		reviewer = self.stores
		self.as_user(self.requester)
		revision = frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document.name,
				"file": private_file(),
				"reviewer": reviewer,
			}
		).insert()
		self.as_user(self.requester)
		sign_action("Document Revision", revision.name, "Author", PASSWORD)
		self.as_user(reviewer)
		sign_action("Document Revision", revision.name, "Review", PASSWORD)
		self.as_user(self.qa)
		sign_action("Document Revision", revision.name, "Approve", PASSWORD)
		return document.name

	def new_revision(self, document: str, change_request: str | None = None):
		from meddevice_suite.qms.test_document_control import private_file

		self.as_user(self.requester)
		return frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document,
				"file": private_file("Revision 01"),
				"reviewer": self.stores,
				"summary_of_change": "Hardness check added",
				"change_request": change_request,
			}
		).insert()

	def test_a_new_revision_of_a_document_in_effect_needs_an_approved_change(self):
		document = self.effective_document()
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			self.new_revision(document)

		change = approved_change(self.requester, self.qa, document)
		self.assertEqual(self.new_revision(document, change).change_request, change)

	def test_the_change_must_be_approved(self):
		document = self.effective_document()
		draft = self.raise_change(
			affected_records=[{"reference_doctype": "Controlled Document", "reference_name": document}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved for implementation"):
			self.new_revision(document, draft.name)

	def test_the_change_must_list_the_document(self):
		document = self.effective_document()
		other = approved_change(self.requester, self.qa, self.document)
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			self.new_revision(document, other)

	def test_the_revision_form_offers_only_approved_changes_listing_the_document(self):
		document = self.effective_document()
		approved = approved_change(self.requester, self.qa, document)
		approved_change(self.requester, self.qa, self.document)  # lists another document
		self.raise_change(
			affected_records=[{"reference_doctype": "Controlled Document", "reference_name": document}]
		)

		def offered(user: str, **filters) -> list[str]:
			self.as_user(user)
			return [
				name
				for name, _title in approved_changes_for(
					doctype=DOCTYPE, txt="", searchfield="name", start=0, page_len=20, filters=filters
				)
			]

		self.assertEqual(offered(self.requester, controlled_document=document), [approved])
		self.assertEqual(offered(self.outsider, controlled_document=document), [])  # may not see it
		self.assertEqual(offered(self.requester), [])

	def test_the_first_revision_of_a_new_document_needs_none(self):
		self.as_user(self.qa)
		document = frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": "New form",
				"document_type": "Form",
				"department": "QC",
				"process_owner": self.requester,
			}
		).insert()
		self.assertIsNone(self.new_revision(document.name).change_request)

	def test_the_change_request_of_a_revision_is_set_once(self):
		self.as_user(self.qa)
		document = frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": "Another form",
				"document_type": "Form",
				"department": "QC",
				"process_owner": self.requester,
			}
		).insert()
		revision = self.new_revision(document.name)
		self.assertEqual(frappe.get_meta("Document Revision").get_field("change_request").set_only_once, 1)
		self.assertIsNone(revision.change_request)


@verifies("URS-QMS-015")
class TestAlerts(ChangeControlTestCase):
	def subjects(self, user: str, name: str) -> list[str]:
		return alerts_for(user, DOCTYPE, name)

	def test_qa_is_alerted_to_assess_and_then_to_approve(self):
		doc = self.submitted()
		self.assertEqual(
			self.subjects(self.qa, doc.name), [f"Change request {doc.name} is waiting for your assessment"]
		)
		self.as_user(self.qa)
		doc.update(assessment())
		doc.save()
		self.act(self.qa, doc.name, "Assess")
		self.assertIn(
			f"Change request {doc.name} is waiting for your approval", self.subjects(self.qa, doc.name)
		)

	def test_management_is_alerted_to_approve_a_major_change(self):
		doc = self.act(self.qa, self.assessed("Major").name, "Approve")
		self.assertEqual(
			self.subjects(self.manager, doc.name),
			[f"Major change request {doc.name} is waiting for Management approval"],
		)

	def test_the_person_who_raised_it_hears_the_decision(self):
		approved = self.approved()
		returned = self.act(self.qa, self.assessed().name, "Return", "Say which lots")
		rejected = self.act(self.qa, self.assessed().name, "Reject", "Not needed")
		self.assertEqual(self.subjects(self.requester, approved.name), [f"{approved.name} was approved"])
		self.assertEqual(self.subjects(self.requester, returned.name), [f"{returned.name} was returned"])
		self.assertEqual(self.subjects(self.requester, rejected.name), [f"{rejected.name} was rejected"])

	def test_each_owner_is_alerted_once_when_their_action_falls_due(self):
		doc = self.approved()  # actions due today and in 10 days
		send_change_alerts()
		send_change_alerts()
		self.assertEqual(len(self.subjects(self.requester, doc.name)), 2)  # approved, and action 1 due
		self.assertEqual(self.subjects(self.stores, doc.name), [])  # due in 10 days: not yet

		frappe.db.set_value("Change Request Action", doc.actions[1].name, "due_date", add_days(today(), -3))
		send_change_alerts()
		self.assertEqual(
			self.subjects(self.stores, doc.name),
			[
				f"Action 2 of change request {doc.name} is due on {frappe.format_value(add_days(today(), -3), {'fieldtype': 'Date'})}"
			],
		)

	@verifies("URS-PLT-012")
	def test_the_action_alert_follows_quality_settings(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		doc = self.approved()  # actions due today and in 10 days
		with use_setting("change_action_warning_days", 5):
			send_change_alerts()
		self.assertEqual(self.subjects(self.stores, doc.name), [])  # due in 10 days: outside 5
		with use_setting("change_action_warning_days", 12):
			send_change_alerts()
		self.assertEqual(len(self.subjects(self.stores, doc.name)), 1)  # due in 10 days: within 12

	@verifies("URS-PLT-012")
	def test_a_new_request_takes_its_effectiveness_check_from_quality_settings(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		with use_setting("default_effectiveness_check_days", 120):
			doc = self.raise_change()
		self.assertEqual(frappe.db.get_value("Change Request", doc.name, "effectiveness_check_days"), 120)

	def test_done_actions_and_unapproved_changes_raise_no_due_alert(self):
		draft = self.raise_change()
		doc = self.approved()
		self.as_user(self.requester)
		complete_action(doc.name, doc.actions[0].name, "done")
		send_change_alerts()
		self.assertEqual(self.subjects(self.requester, draft.name), [])
		self.assertEqual(self.subjects(self.requester, doc.name), [f"{doc.name} was approved"])

	def test_qa_is_alerted_once_when_the_effectiveness_check_falls_due(self):
		doc = self.implemented(days=0)
		later = self.implemented(days=5)
		send_change_alerts()
		send_change_alerts()
		subject = f"The effectiveness of change {doc.name} is due to be checked"
		self.assertEqual(self.subjects(self.qa, doc.name).count(subject), 1)
		self.assertNotIn(
			f"The effectiveness of change {later.name} is due to be checked",
			self.subjects(self.qa, later.name),
		)

	def test_one_alert_failing_does_not_hold_back_the_others(self):
		failing, other = self.implemented(), self.implemented()
		real = Notification.send

		def send(notification, doc):
			if doc.name == failing.name:
				raise frappe.ValidationError("template broken")
			return real(notification, doc)

		with patch.object(Notification, "send", send):
			send_change_alerts()
		self.assertTrue(
			frappe.db.exists(
				"Error Log",
				{
					"method": f"Change control alert for effectiveness check of {failing.name} could not be sent"
				},
			)
		)
		self.assertIn(
			f"The effectiveness of change {other.name} is due to be checked",
			self.subjects(self.qa, other.name),
		)
		self.assertEqual(frappe.db.get_value(DOCTYPE, failing.name, "effectiveness_alerted"), 0)

	def test_the_alerts_run_daily_and_are_declared(self):
		from meddevice_suite.compliance.alerts import ALERTS

		self.assertIn(
			"meddevice_suite.qms.change_control.send_change_alerts",
			frappe.get_hooks("scheduler_events")["daily"],
		)
		for name in (
			change_control.AWAITING_ASSESSMENT,
			change_control.AWAITING_APPROVAL,
			change_control.AWAITING_MANAGEMENT,
			change_control.DECIDED,
			change_control.EFFECTIVENESS_DUE,
		):
			self.assertEqual(ALERTS.get(name), "QMS", name)


@verifies("URS-QMS-016")
class TestKeptAndSeen(ChangeControlTestCase):
	def test_a_change_request_is_never_deleted(self):
		for doc in (self.raise_change(), self.approved()):
			self.as_user("Administrator")
			with self.subTest(status=doc.status), self.assertRaises(REFUSED):
				frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
			self.assertTrue(frappe.db.exists(DOCTYPE, doc.name))

	def test_an_approved_change_is_never_cancelled(self):
		doc = self.approved()
		self.as_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, doc.name).cancel()

	def test_the_person_who_raised_it_withdraws_it_with_a_reason_until_approved(self):
		for doc in (self.raise_change(), self.submitted(), self.assessed()):
			with self.subTest(status=doc.status):
				self.as_user(self.requester)
				with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
					withdraw(doc.name, "")
				withdraw(doc.name, "Superseded by CR for the new supplier")
				self.assertEqual(
					frappe.db.get_value(DOCTYPE, doc.name, ["status", "decision_reason"]),
					("Withdrawn", "Superseded by CR for the new supplier"),
				)

		approved = self.approved()
		self.as_user(self.requester)
		with self.assertRaisesRegex(frappe.ValidationError, "can no longer be withdrawn"):
			withdraw(approved.name, "Changed my mind")

	def test_nobody_else_withdraws_it(self):
		doc = self.raise_change()
		self.as_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			withdraw(doc.name, "QA withdraws")

	def test_qa_management_and_the_administrator_see_every_request(self):
		doc = self.raise_change()
		for user in (self.qa, self.manager, self.admin):
			with self.subTest(user=user):
				self.as_user(user)
				self.assertIn(doc.name, frappe.get_list(DOCTYPE, pluck="name"))
				self.assertTrue(frappe.has_permission(DOCTYPE, "read", doc=doc.name))

	def test_others_see_only_what_they_raised_or_have_an_action_on(self):
		doc = self.raise_change()  # raised by production, an action for stores
		self.as_user(self.stores)
		self.assertIn(doc.name, frappe.get_list(DOCTYPE, pluck="name"))
		self.as_user(self.outsider)
		self.assertNotIn(doc.name, frappe.get_list(DOCTYPE, pluck="name"))
		self.assertFalse(frappe.has_permission(DOCTYPE, "read", doc=doc.name))
		with self.assertRaises(frappe.PermissionError):
			submit_for_assessment(doc.name)

	def test_the_query_conditions_are_safe_for_any_user_name(self):
		self.assertIn("\\'", query_conditions("o'brien@example.com"))

	def test_every_business_job_may_raise_one_and_the_administrator_only_reads(self):
		perms = {perm.role: perm for perm in frappe.get_meta(DOCTYPE).permissions if perm.permlevel == 0}
		self.assertEqual({role for role, perm in perms.items() if perm.create}, set(BUSINESS_ROLES))
		admin = perms["System Manager"]
		self.assertEqual((admin.read, admin.write, admin.create), (1, 0, 0))

	def test_it_is_kept_ten_years_and_audited(self):
		from meddevice_suite.compliance.audit_scope import is_regulated
		from meddevice_suite.compliance.retention import retention_years

		self.assertEqual(retention_years(DOCTYPE), 10)
		self.assertTrue(is_regulated(DOCTYPE, "QMS"))
		doc = self.raise_change()
		self.assertTrue(
			frappe.db.exists(
				"Audit Event", {"reference_doctype": DOCTYPE, "reference_name": doc.name, "action": "Created"}
			)
		)


@verifies("URS-QMS-024")
class TestRiskReviews(ChangeControlTestCase):
	"""Module 39: a change that affects risk names the risk management file, its approval raises a
	review of each file it lists, and it is not closed while one is open."""

	def risk_file(self) -> str:
		from meddevice_suite.qms.test_risk_management import risk_file_for

		return risk_file_for(self.qa, self.document)

	def listing(self, file: str) -> list[dict]:
		return [
			{"reference_doctype": "Controlled Document", "reference_name": self.document},
			{"reference_doctype": "Risk Management File", "reference_name": file},
		]

	def reviews(self, name: str) -> list[frappe._dict]:
		return frappe.get_all(
			"Risk Review",
			filters={"source_doctype": DOCTYPE, "source_name": name},
			fields=["name", "risk_management_file", "trigger", "status"],
		)

	def test_risk_impact_requires_file(self):
		doc = self.submitted()
		self.as_user(self.qa)
		doc.update({**assessment(), "impact_risk": "Yes"})
		doc.save()
		with self.assertRaisesRegex(frappe.ValidationError, "Risk Management File"):
			self.act(self.qa, doc.name, "Assess")

		file = self.risk_file()
		self.as_user(self.qa)
		doc.reload()
		doc.set("affected_records", self.listing(file))
		doc.save()  # QA completes the records while assessing
		self.assertEqual(self.act(self.qa, doc.name, "Assess").status, "Awaiting Approval")

	def test_approval_raises_review(self):
		file = self.risk_file()
		doc = self.approved(affected_records=self.listing(file))

		self.assertEqual(
			[(r.risk_management_file, r.trigger, r.status) for r in self.reviews(doc.name)],
			[(file, "Change Request", "Open")],
		)
		self.assertIn("Risk Management File", AFFECTED_TYPES)

	def test_a_major_change_raises_its_review_once_fully_approved(self):
		file = self.risk_file()
		doc = self.assessed("Major", affected_records=self.listing(file))
		doc = self.act(self.qa, doc.name, "Approve")
		self.assertEqual(self.reviews(doc.name), [])
		self.act(self.manager, doc.name, "Approve")
		self.assertEqual(len(self.reviews(doc.name)), 1)

	def test_open_review_blocks_close(self):
		file = self.risk_file()
		doc = self.approved(days=0, affected_records=self.listing(file))
		for row in doc.actions:
			self.as_user(row.action_owner)
			complete_action(doc.name, row.name, f"{row.description}: done")
		review = self.reviews(doc.name)[0].name

		with self.assertRaisesRegex(frappe.ValidationError, review):
			self.act(self.qa, doc.name, "Verify Effective", "Hardness checked on three lots")
		self.as_user(self.qa)
		sign_action(
			"Risk Review", review, "Close: No Change", PASSWORD, "The change adds a check; no new hazard"
		)
		self.assertEqual(
			self.act(self.qa, doc.name, "Verify Effective", "Hardness checked on three lots").status, "Closed"
		)
