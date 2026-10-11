from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.desk.search import search_link
from frappe.email.doctype.notification.notification import Notification
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_years, getdate, today
from frappe.utils.password import update_password
from frappe.www.printview import get_rendered_template
from pypdf import PdfWriter

from meddevice_suite.compliance.esign import verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.qa import verifies
from meddevice_suite.qms.doctype.document_revision.document_revision import next_revision_number
from meddevice_suite.qms.document_control import (
	make_due_revisions_effective,
	make_effective,
	revision_query_conditions,
	send_review_alerts,
)
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile

JOBS = ("QA Manager", "Production Supervisor", "Stores", "Sales")


def signer(profile: str) -> str:
	user = user_with_profile(profile)
	update_password(user, PASSWORD)
	reset_lockout(user)
	return user


def pdf(text: str) -> bytes:
	"""A real one-page PDF (Frappe validates uploaded PDFs) whose content depends on `text`."""
	writer = PdfWriter()
	writer.add_blank_page(width=595, height=842)
	writer.add_metadata({"/Title": text})
	buffer = BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def private_file(text: str = "SOP: incoming inspection of silicone") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{frappe.generate_hash(length=8)}.pdf",
				"is_private": 1,
				"content": pdf(text),
			}
		)
		.insert(ignore_permissions=True)
		.file_url
	)


class DocumentControlTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		cls.qa, cls.author, cls.reviewer, cls.outsider = (signer(job) for job in JOBS)

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.flags.audit_cause = None

	def as_user(self, user: str) -> None:
		frappe.set_user(user)

	def register(
		self,
		title: str = "Incoming inspection of silicone",
		document_type: str = "SOP",
		department: str = "QA",
	):
		self.as_user(self.qa)
		return frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": title,
				"document_type": document_type,
				"department": department,
				"process_owner": self.author,
			}
		).insert()

	def draft(self, document, summary: str | None = None, reviewer: str | None = None):
		# A document in effect is revised only under an approved change request (URS-QMS-014).
		change_request = None
		if frappe.db.get_value("Controlled Document", document.name, "current_revision"):
			from meddevice_suite.qms.test_change_control import approved_change

			change_request = approved_change(self.author, self.qa, document.name)
		self.as_user(self.author)
		return frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document.name,
				"file": private_file(),
				"reviewer": reviewer or self.reviewer,
				"summary_of_change": summary,
				"change_request": change_request,
			}
		).insert()

	def act(self, user: str, revision, action: str, reason: str | None = None):
		self.as_user(user)
		sign_action("Document Revision", revision.name, action, PASSWORD, reason)
		return frappe.get_doc("Document Revision", revision.name)

	def issue(self, document, summary: str | None = None, effective_date: str | None = None):
		"""Draft, author, review and approve a revision of `document`."""
		revision = self.draft(document, summary)
		self.act(self.author, revision, "Author")
		revision = self.act(self.reviewer, revision, "Review")
		if effective_date:
			self.as_user(self.qa)
			revision.effective_date = effective_date
			revision.save()
		return self.act(self.qa, revision, "Approve")

	def signed(self, revision) -> list[tuple[str, str]]:
		return [
			(row.signer, row.meaning)
			for row in frappe.get_all(
				"Electronic Signature",
				filters={"reference_doctype": "Document Revision", "reference_name": revision.name},
				fields=["signer", "meaning"],
				order_by="signed_at asc, creation asc",
			)
		]


@verifies("URS-QMS-001")
class TestRegisterAndRevise(DocumentControlTestCase):
	def test_documents_are_numbered_by_type_and_department(self):
		first, second = self.register(), self.register("Release of finished goods")
		instruction = self.register("Balloon forming", "Work Instruction", "PRD")

		number = int(first.name.rsplit("-", 1)[1])
		self.assertRegex(first.name, r"^SOP-QA-\d{3}$")
		self.assertEqual(second.name, f"SOP-QA-{number + 1:03d}")
		self.assertRegex(instruction.name, r"^WI-PRD-\d{3}$")

	def test_revisions_are_numbered_from_00_one_at_a_time(self):
		document = self.register()
		self.issue(document)
		revision = self.draft(document, summary="Sampling plan changed to AQL 1.0")

		self.assertEqual(revision.name, f"{document.name}-REV-01")
		with self.assertRaises(frappe.ValidationError):
			self.draft(document, summary="A second revision at the same time")

	def test_status_and_effective_revision_change_only_through_revisions(self):
		document = self.register()
		self.issue(document)
		self.as_user(self.qa)
		document = frappe.get_doc("Controlled Document", document.name)

		document.title = "Incoming inspection of silicone tubing"
		document.save()
		document.status = "Draft"
		with self.assertRaises(frappe.ValidationError):
			document.save()

	def test_only_the_process_owner_or_qa_starts_a_revision(self):
		document = self.register()

		self.as_user(self.outsider)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "Document Revision", "controlled_document": document.name}).insert()


@verifies("URS-QMS-002", "URS-CMP-030")
class TestReviewAndApproval(DocumentControlTestCase):
	def test_authored_reviewed_and_approved_by_three_people_with_signatures(self):
		revision = self.issue(self.register())

		self.assertEqual(
			self.signed(revision),
			[(self.author, "Authored"), (self.reviewer, "Reviewed"), (self.qa, "Approved")],
		)
		self.assertEqual((revision.status, revision.docstatus, revision.approver), ("Effective", 1, self.qa))

	def test_the_author_cannot_be_the_reviewer(self):
		revision = self.draft(self.register(), reviewer=self.author)

		with self.assertRaises(frappe.ValidationError):
			self.act(self.author, revision, "Author")

	def test_only_the_assigned_reviewer_reviews(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")

		# Someone outside the revision does not even see it; QA sees it but is not its reviewer.
		with self.assertRaises(frappe.PermissionError):
			self.act(self.outsider, revision, "Review")
		with self.assertRaises(frappe.ValidationError):
			self.act(self.qa, revision, "Review")

	def test_only_qa_approves_and_never_its_own_revision(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		revision = self.act(self.reviewer, revision, "Review")
		with self.assertRaises(frappe.PermissionError):
			self.act(self.reviewer, revision, "Approve")

		document = self.register("QA's own procedure")
		self.as_user(self.qa)
		own = frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document.name,
				"file": private_file(),
				"reviewer": self.reviewer,
			}
		).insert()
		self.act(self.qa, own, "Author")
		own = self.act(self.reviewer, own, "Review")
		with self.assertRaises(frappe.ValidationError):
			self.act(self.qa, own, "Approve")

	def test_a_returned_revision_goes_back_to_its_author_with_the_reason(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		with self.assertRaises(frappe.ValidationError):
			self.act(self.reviewer, revision, "Return")

		revision = self.act(self.reviewer, revision, "Return", "Step 4 misses the sampling plan")

		self.assertEqual(revision.status, "Draft")
		self.as_user(self.author)
		revision.file = private_file("SOP with sampling plan")
		revision.save()
		revision = self.act(self.author, revision, "Author")
		self.assertEqual(revision.status, "In Review")

	def test_every_revision_after_the_first_says_what_changed(self):
		document = self.register()
		self.issue(document)
		revision = self.draft(document)

		with self.assertRaises(frappe.ValidationError):
			self.act(self.author, revision, "Author")


@verifies("URS-QMS-003")
class TestSignedContentIsLocked(DocumentControlTestCase):
	def test_approval_is_refused_if_the_file_changed_after_the_author_signed(self):
		revision = self.draft(self.register())
		revision = self.act(self.author, revision, "Author")
		revision = self.act(self.reviewer, revision, "Review")
		file = frappe.get_doc("File", {"file_url": revision.file})
		Path(file.get_full_path()).write_bytes(pdf("A different SOP"))

		with self.assertRaises(frappe.ValidationError):
			self.act(self.qa, revision, "Approve")

	def test_a_signed_revision_cannot_be_edited(self):
		revision = self.act(self.author, self.draft(self.register()), "Author")

		self.as_user(self.author)
		revision.file = private_file("Swapped after signing")
		with self.assertRaises(frappe.PermissionError):
			revision.save()

	def test_the_file_must_be_private(self):
		document = self.register()
		self.as_user(self.author)
		public = frappe.get_doc(
			{"doctype": "File", "file_name": "public.pdf", "is_private": 0, "content": pdf("Public copy")}
		).insert(ignore_permissions=True)
		revision = frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document.name,
				"file": public.file_url,
				"reviewer": self.reviewer,
			}
		).insert()

		with self.assertRaises(frappe.ValidationError):
			self.act(self.author, revision, "Author")


@verifies("URS-QMS-004")
class TestTakingEffect(DocumentControlTestCase):
	def test_an_approved_revision_takes_effect_and_supersedes_the_previous_one(self):
		document = self.register()
		first = self.issue(document)
		second = self.issue(document, summary="Sampling plan changed to AQL 1.0")

		first.reload()
		document.reload()
		self.assertEqual((first.status, first.superseded_by), ("Superseded", second.name))
		self.assertEqual((document.status, document.current_revision), ("Effective", second.name))
		self.assertEqual(getdate(document.next_review_date), getdate(add_years(today(), 2)))

	def test_a_future_effective_date_waits_for_the_daily_run(self):
		document = self.register()
		revision = self.issue(document, effective_date=add_days(today(), 5))
		self.assertEqual(revision.status, "Approved")

		with patch("meddevice_suite.qms.document_control.nowdate", return_value=add_days(today(), 5)):
			make_due_revisions_effective()

		self.assertEqual(frappe.db.get_value("Document Revision", revision.name, "status"), "Effective")

	def test_one_failing_revision_does_not_hold_back_the_others(self):
		failing = self.issue(self.register(), effective_date=add_days(today(), 5))
		due = self.issue(self.register("Release of finished goods"), effective_date=add_days(today(), 5))
		real = make_effective

		def fail_first(name):
			if name == failing.name:
				raise frappe.ValidationError("broken")
			real(name)

		with (
			patch("meddevice_suite.qms.document_control.nowdate", return_value=add_days(today(), 5)),
			patch("meddevice_suite.qms.document_control.make_effective", side_effect=fail_first),
		):
			make_due_revisions_effective()

		self.assertEqual(frappe.db.get_value("Document Revision", failing.name, "status"), "Approved")
		self.assertEqual(frappe.db.get_value("Document Revision", due.name, "status"), "Effective")
		self.assertTrue(
			frappe.db.exists(
				"Error Log", {"method": f"Document revision {failing.name} could not take effect"}
			)
		)

	def test_a_revision_never_takes_effect_before_its_approval(self):
		revision = self.issue(self.register(), effective_date=add_days(today(), -30))

		self.assertEqual(getdate(revision.effective_date), getdate(today()))


@verifies("URS-QMS-005")
class TestPointOfUse(DocumentControlTestCase):
	def test_outside_qa_only_effective_revisions_are_seen(self):
		document = self.register()
		old = self.issue(document)
		current = self.issue(document, summary="Clarified step 3")
		draft = self.draft(document, summary="Work in progress")

		self.as_user(self.outsider)
		visible = set(
			frappe.get_list("Document Revision", filters={"controlled_document": document.name}, pluck="name")
		)
		self.assertEqual(visible, {current.name})
		self.assertFalse(frappe.has_permission("Document Revision", "read", doc=old.name, user=self.outsider))
		self.assertFalse(
			frappe.has_permission("Document Revision", "read", doc=draft.name, user=self.outsider)
		)

		self.as_user(self.author)
		self.assertIn(draft.name, frappe.get_list("Document Revision", pluck="name"))
		self.assertEqual(revision_query_conditions(self.qa), "")

	def test_nothing_is_deleted_or_cancelled(self):
		document = self.register()
		revision = self.issue(document)
		draft = self.draft(document)

		self.as_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Document Revision", draft.name)
		with self.assertRaises(frappe.ValidationError):  # Frappe itself refuses to delete a submitted record
			frappe.delete_doc("Document Revision", revision.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Controlled Document", document.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Document Revision", revision.name).cancel()


@verifies("URS-QMS-006")
class TestWithdrawal(DocumentControlTestCase):
	def withdraw(self, user: str, document, reason: str | None = "Replaced by SOP-QA-010"):
		self.as_user(user)
		sign_action("Controlled Document", document.name, "Withdraw", PASSWORD, reason)

	def test_qa_withdraws_a_document_with_a_signed_reason(self):
		document = self.register()
		revision = self.issue(document)

		self.withdraw(self.qa, document)

		document.reload()
		self.assertEqual((document.status, document.withdrawn_reason), ("Obsolete", "Replaced by SOP-QA-010"))
		self.assertEqual(frappe.db.get_value("Document Revision", revision.name, "status"), "Obsolete")
		with self.assertRaises(frappe.ValidationError):
			self.draft(document, summary="Revising a withdrawn document")
		self.as_user(self.qa)
		document.title = "Changed after withdrawal"
		with self.assertRaises(frappe.ValidationError):
			document.save()

	def test_only_qa_withdraws_and_only_with_a_reason_and_nothing_in_progress(self):
		document = self.register()
		self.issue(document)
		with self.assertRaises(frappe.PermissionError):
			self.withdraw(self.author, document)
		with self.assertRaises(frappe.ValidationError):
			self.withdraw(self.qa, document, reason=None)

		self.draft(document, summary="In progress")
		with self.assertRaises(frappe.ValidationError):
			self.withdraw(self.qa, document)


@verifies("URS-QMS-002")
class TestSeparationOfSigners(DocumentControlTestCase):
	def test_the_reviewer_cannot_also_approve(self):
		revision = self.draft(self.register(), reviewer=self.qa)
		self.act(self.author, revision, "Author")
		revision = self.act(self.qa, revision, "Review")

		with self.assertRaises(frappe.ValidationError):
			self.act(self.qa, revision, "Approve")
		self.assertEqual(
			frappe.db.get_value("Document Revision", revision.name, "status"), "Awaiting Approval"
		)

	def test_a_reason_is_kept_exactly_as_written(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")

		with self.assertRaises(frappe.ValidationError):
			self.act(self.reviewer, revision, "Return", reason="Step <4> is unclear")
		self.act(self.reviewer, revision, "Return", reason="Steps 4 & 5: say 'Fr' sizes; ±0.5 mm")

		reasons = frappe.get_all(
			"Electronic Signature",
			filters={"reference_name": revision.name, "meaning": "Rejected"},
			pluck="reason",
		)
		self.assertEqual(reasons, ["Steps 4 & 5: say 'Fr' sizes; ±0.5 mm"])
		self.assertTrue(verify_signatures("Document Revision", revision.name))


@verifies("URS-QMS-003")
class TestNoWayAroundTheSignedActs(DocumentControlTestCase):
	def test_frappes_submit_cannot_approve_a_revision(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		self.act(self.reviewer, revision, "Review")

		self.as_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Document Revision", revision.name).submit()
		self.assertEqual(frappe.db.get_value("Document Revision", revision.name, "docstatus"), 0)

	def test_even_code_skipping_permissions_cannot_submit_or_change_a_revision_unsigned(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		self.act(self.reviewer, revision, "Review")

		self.as_user(self.qa)
		unsigned = frappe.get_doc("Document Revision", revision.name)
		unsigned.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			unsigned.submit()

		approved = self.act(self.qa, revision, "Approve")
		approved.flags.ignore_permissions = True
		approved.superseded_by = approved.name
		with self.assertRaises(frappe.PermissionError):
			approved.save()

	def test_an_approved_revision_cannot_be_changed_through_the_api(self):
		revision = self.issue(self.register())

		self.as_user(self.qa)
		for field, value in (("status", "Draft"), ("superseded_by", revision.name)):
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				frappe.client.set_value("Document Revision", revision.name, field, value)
		self.assertEqual(frappe.db.get_value("Document Revision", revision.name, "status"), "Effective")

	def test_what_only_the_signed_acts_set_is_discarded_on_creation(self):
		document = self.register()
		earlier = self.issue(self.register("Release of finished goods"))
		self.as_user(self.author)
		revision = frappe.get_doc(
			{
				"doctype": "Document Revision",
				"controlled_document": document.name,
				"file": private_file(),
				"reviewer": self.reviewer,
				"file_sha256": "0" * 64,
				"approver": self.qa,
				"effective_date": today(),
				"superseded_by": earlier.name,
			}
		).insert()

		self.assertEqual(
			(revision.file_sha256, revision.approver, revision.effective_date, revision.superseded_by),
			(None, None, None, None),
		)

	def test_a_fingerprinted_file_cannot_be_deleted_made_public_or_replaced(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		file = frappe.get_doc("File", {"file_url": revision.file})

		for user in (self.author, self.reviewer, self.outsider):
			self.as_user(user)
			with self.subTest(user=user, action="delete"), self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("File", file.name)
			with self.subTest(user=user, action="make public"), self.assertRaises(frappe.PermissionError):
				frappe.client.set_value("File", file.name, "is_private", 0)

		self.as_user("Administrator")
		public = frappe.get_doc("File", file.name)
		public.is_private = 0
		with self.assertRaises(frappe.PermissionError):
			public.save()
		self.assertEqual(
			frappe.db.get_value("File", file.name, ["is_private", "file_url"]), (1, revision.file)
		)
		self.assertTrue(Path(file.get_full_path()).is_file())

	def test_a_draft_file_can_still_be_replaced_by_its_author(self):
		revision = self.draft(self.register())
		file = frappe.db.get_value("File", {"file_url": revision.file}, "name")

		self.as_user(self.author)
		frappe.delete_doc("File", file)

		self.assertFalse(frappe.db.exists("File", file))

	def test_a_missing_file_is_reported_not_crashed_on(self):
		revision = self.draft(self.register())
		self.act(self.author, revision, "Author")
		revision = self.act(self.reviewer, revision, "Review")
		Path(frappe.get_doc("File", {"file_url": revision.file}).get_full_path()).unlink()

		with self.assertRaises(frappe.ValidationError):
			self.act(self.qa, revision, "Approve")


@verifies("URS-QMS-004")
class TestTakingEffectIsGuarded(DocumentControlTestCase):
	def test_only_an_approved_revision_of_a_live_document_takes_effect(self):
		draft = self.draft(self.register())
		with self.assertRaises(frappe.ValidationError):
			make_effective(draft.name)

		approved = self.issue(self.register("Release of finished goods"), effective_date=add_days(today(), 5))
		frappe.db.set_value("Controlled Document", approved.controlled_document, "status", "Obsolete")
		with self.assertRaises(frappe.ValidationError):
			make_effective(approved.name)
		self.assertEqual(frappe.db.get_value("Document Revision", approved.name, "status"), "Approved")

	def test_every_earlier_effective_revision_is_superseded(self):
		document = self.register()
		first = self.issue(document)
		# A second effective revision, as only a fault could leave behind.
		stray = self.issue(document, summary="Second")
		frappe.db.set_value("Document Revision", first.name, {"status": "Effective", "superseded_by": None})

		latest = self.issue(document, summary="Third")

		for revision in (first, stray):
			self.assertEqual(
				frappe.db.get_value("Document Revision", revision.name, ["status", "superseded_by"]),
				("Superseded", latest.name),
			)

	def test_a_document_is_reviewed_at_least_every_year(self):
		for years in (0, -1):
			with self.subTest(years=years), self.assertRaises(frappe.ValidationError):
				self.as_user(self.qa)
				frappe.get_doc(
					{
						"doctype": "Controlled Document",
						"title": "Never reviewed",
						"document_type": "SOP",
						"department": "QA",
						"process_owner": self.author,
						"review_period_years": years,
					}
				).insert()

	def test_revision_numbers_continue_past_99(self):
		with patch("frappe.get_all", return_value=["00", "98", "99"]):
			self.assertEqual(next_revision_number("SOP-QA-001"), "100")


@verifies("URS-QMS-005")
class TestOutsidersCannotReachDrafts(DocumentControlTestCase):
	def test_no_api_route_shows_an_outsider_a_draft_or_superseded_revision(self):
		document = self.register()
		old = self.issue(document)
		current = self.issue(document, summary="Clarified step 3")
		draft = self.draft(document, summary="Work in progress")

		self.as_user(self.outsider)
		for name in (old.name, draft.name):
			with self.subTest(name=name), self.assertRaises(frappe.PermissionError):
				frappe.client.get("Document Revision", name)
		self.assertEqual(
			{row["value"] for row in search_link("Document Revision", document.name)}, {current.name}
		)


@verifies("URS-QMS-008", "URS-CMP-003")
class TestControlledCopy(DocumentControlTestCase):
	def printout(self, revision) -> str:
		# The printout's own content, with the person's print permission checked, as the print
		# page renders it; without the page around it, which needs built assets.
		self.as_user(self.qa)
		return get_rendered_template(
			frappe.get_doc("Document Revision", revision.name),
			print_format=frappe.get_doc("Print Format", "Controlled Copy"),
			no_letterhead=1,
		)

	def test_an_effective_revision_prints_as_a_controlled_copy_with_its_signatures(self):
		revision = self.issue(self.register())

		html = self.printout(revision)

		self.assertIn("CONTROLLED COPY", html)
		self.assertNotIn("UNCONTROLLED", html)
		self.assertIn(revision.file_sha256, html)
		for user in (self.author, self.reviewer, self.qa):
			self.assertIn(user, html)
		for meaning in ("Authored", "Reviewed", "Approved"):
			self.assertIn(meaning, html)

	def test_any_other_revision_prints_as_uncontrolled(self):
		document = self.register()
		first = self.issue(document)
		self.issue(document, summary="Clarified step 3")
		draft = self.draft(document, summary="Work in progress")

		for revision, status in ((first, "Superseded"), (draft, "Draft")):
			with self.subTest(status=status):
				html = self.printout(frappe.get_doc("Document Revision", revision.name))
				self.assertIn("UNCONTROLLED", html)
				self.assertNotIn("CONTROLLED COPY", html)
				self.assertIn(status, html)

	def test_it_is_the_revisions_default_printout(self):
		self.assertEqual(frappe.get_meta("Document Revision").default_print_format, "Controlled Copy")

	def test_nobody_may_submit_a_revision_except_through_the_signed_approval(self):
		for user in (self.qa, self.author, self.reviewer):
			with self.subTest(user=user):
				self.assertFalse(frappe.has_permission("Document Revision", "submit", user=user))

	def test_the_form_knows_whether_a_revision_is_in_progress(self):
		document = self.register()
		self.issue(document)
		self.as_user(self.qa)
		loaded = frappe.get_doc("Controlled Document", document.name)
		loaded.run_method("onload")
		self.assertIsNone(loaded.get_onload().get("revision_in_progress"))

		draft = self.draft(document, summary="Next")
		self.as_user(self.qa)
		loaded = frappe.get_doc("Controlled Document", document.name)
		loaded.run_method("onload")
		self.assertEqual(loaded.get_onload().get("revision_in_progress"), draft.name)


@verifies("URS-QMS-007")
class TestPeriodicReviewAlerts(DocumentControlTestCase):
	def due_in(self, days: int):
		document = self.register()
		self.issue(document)
		frappe.db.set_value("Controlled Document", document.name, "next_review_date", add_days(today(), days))
		return document

	def alerts(self, user: str, document) -> list[str]:
		return frappe.get_all(
			"Notification Log",
			filters={
				"for_user": user,
				"document_type": "Controlled Document",
				"document_name": document.name,
			},
			pluck="subject",
		)

	def test_the_owner_and_qa_are_alerted_once_when_the_review_falls_due(self):
		document = self.due_in(30)

		send_review_alerts()
		send_review_alerts()

		for user in (self.author, self.qa):
			with self.subTest(user=user):
				self.assertEqual(len(self.alerts(user, document)), 1)
		self.assertEqual(self.alerts(self.outsider, document), [])

	@verifies("URS-PLT-012")
	def test_the_review_alert_follows_quality_settings(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		inside, outside = self.due_in(40), self.due_in(50)
		with use_setting("document_review_warning_days", 45):
			send_review_alerts()
		self.assertEqual(len(self.alerts(self.qa, inside)), 1)
		self.assertEqual(self.alerts(self.qa, outside), [])

	def test_an_overdue_review_is_alerted_and_a_later_one_waits(self):
		overdue, later = self.due_in(-10), self.due_in(31)

		send_review_alerts()

		self.assertEqual(len(self.alerts(self.qa, overdue)), 1)
		self.assertEqual(self.alerts(self.qa, later), [])

	def test_a_new_review_date_is_alerted_afresh(self):
		document = self.due_in(5)
		send_review_alerts()
		frappe.db.set_value("Controlled Document", document.name, "next_review_date", add_days(today(), 20))

		send_review_alerts()

		self.assertEqual(len(self.alerts(self.qa, document)), 2)

	def test_withdrawn_documents_raise_no_review_alert(self):
		document = self.due_in(5)
		frappe.db.set_value("Controlled Document", document.name, "status", "Obsolete")

		send_review_alerts()

		self.assertEqual(self.alerts(self.qa, document), [])

	def test_one_document_failing_does_not_hold_back_the_others(self):
		failing, other = self.due_in(5), self.due_in(5)
		real = Notification.send

		def send(notification, doc):
			if doc.name == failing.name:
				raise frappe.ValidationError("template broken")
			return real(notification, doc)

		with patch.object(Notification, "send", send):
			send_review_alerts()

		self.assertEqual(self.alerts(self.qa, failing), [])
		self.assertEqual(len(self.alerts(self.qa, other)), 1)
		self.assertTrue(
			frappe.db.exists(
				"Error Log", {"method": f"Review alert for document {failing.name} could not be sent"}
			)
		)

	def test_the_review_alerts_run_daily(self):
		self.assertIn(
			"meddevice_suite.qms.document_control.send_review_alerts",
			frappe.get_hooks("scheduler_events").get("daily", []),
		)
