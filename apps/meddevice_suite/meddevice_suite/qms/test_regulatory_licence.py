"""Regulatory licences (module 41, URS-REG-001 to URS-REG-006): tests derived from the requirements'
clauses."""

import frappe
from frappe.utils import add_days, today

from meddevice_suite.compliance.esign import is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.files_repository.test_files import FilesTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.qms import regulatory_licence
from meddevice_suite.work_management import reminders

DOCTYPE = "Regulatory Licence"


class LicenceTestCase(FilesTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.type = self.licence_type("Manufacturing licence (MD-5)", expires=1)

	def licence_type(self, name: str, expires: int) -> str:
		name = f"{name} {frappe.generate_hash(length=10)}"
		frappe.get_doc(
			{"doctype": "Regulatory Licence Type", "licence_type_name": name, "has_expiry": expires}
		).insert(ignore_permissions=True)
		return name

	def copy(self) -> str:
		name = self.upload()
		frappe.set_user("Administrator")
		return name

	def draft(self, **values) -> str:
		frappe.set_user(self.qa)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": DOCTYPE,
						"licence_type": self.type,
						"licence_number": f"MFG/MD-5/{frappe.generate_hash(length=10)}",
						"issuing_authority": "State Licensing Authority",
						"site": "Unit 1",
						"valid_from": add_days(today(), -30),
						"valid_until": add_days(today(), 365 * 5),
						"important_file": self.copy(),
						**values,
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def act(
		self, user: str, name: str, act: str, reason: str | None = "Checked against the authority's copy"
	) -> None:
		frappe.set_user(user)
		try:
			sign_action(DOCTYPE, name, act, PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def verified(self, **values) -> str:
		name = self.draft(**values)
		self.act(self.qa, name, "Verify")
		return name


@verifies("URS-REG-001", "URS-REG-003")
class TestVerification(LicenceTestCase):
	def test_qa_verifies_it_with_a_signature_pinning_the_copy(self):
		name = self.verified()
		doc = frappe.get_doc(DOCTYPE, name)
		version = frappe.get_all(
			"Important File Version",
			filters={"parent": doc.important_file},
			fields=["version", "file_sha256"],
		)[0]
		self.assertEqual((doc.docstatus, doc.status), (1, "Verified"))
		self.assertEqual(
			(doc.verified_file_version, doc.verified_file_sha256), (version.version, version.file_sha256)
		)
		self.assertTrue(verify_signatures(DOCTYPE, name))

	def test_only_qa_verifies(self):
		name = self.draft()
		with self.assertRaises(frappe.PermissionError):
			self.act(self.purchase, name, "Verify")
		from meddevice_suite.qms.test_change_control import signer

		with self.assertRaisesRegex(frappe.PermissionError, "Only QA verifies"):
			self.act(signer("Management"), name, "Verify")  # reads the register, never verifies
		doc = frappe.get_doc(DOCTYPE, name)
		doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			doc.submit()

	def test_what_verification_needs(self):
		with self.assertRaisesRegex(frappe.ValidationError, "copy"):
			self.act(self.qa, self.draft(important_file=None), "Verify")
		with self.assertRaisesRegex(frappe.ValidationError, "expired"):
			self.act(
				self.qa,
				self.draft(valid_from=add_days(today(), -400), valid_until=add_days(today(), -1)),
				"Verify",
			)
		with self.assertRaisesRegex(frappe.ValidationError, "expires: record"):
			self.act(self.qa, self.draft(valid_until=None), "Verify")
		without_expiry = self.licence_type("Registration certificate", expires=0)
		self.assertTrue(self.verified(licence_type=without_expiry, valid_until=None))
		with self.assertRaisesRegex(frappe.ValidationError, "later one"):
			self.draft(valid_until=add_days(today(), -60))

	def test_a_newer_copy_is_not_the_verified_one(self):
		name = self.verified()
		doc = frappe.get_doc(DOCTYPE, name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
		)
		self.new_version(doc.important_file, "Corrected scan")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Important File", doc.important_file, "current_version"), 2)
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, "verified_file_version"), 1
		)  # still the one verified
		self.assertTrue(is_unchanged_since(signature))

	def test_an_archived_copy_is_refused(self):
		name = self.draft()
		frappe.db.set_value(
			"Important File", frappe.db.get_value(DOCTYPE, name, "important_file"), "status", "Archived"
		)
		with self.assertRaisesRegex(frappe.ValidationError, "archived"):
			self.act(self.qa, name, "Verify")


@verifies("URS-REG-002")
class TestNeverChangedAndRenewal(LicenceTestCase):
	def test_reject_and_withdraw_need_a_reason(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			self.act(self.qa, self.draft(), "Reject", None)
		name = self.verified()
		verification = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			self.act(self.qa, name, "Withdraw", None)
		self.act(self.qa, name, "Withdraw", "Suspended by the authority")
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, ["status", "reason"]),
			("Withdrawn", "Suspended by the authority"),
		)
		self.assertTrue(verify_signatures(DOCTYPE, name))
		self.assertTrue(is_unchanged_since(verification))  # what QA verified still stands as signed

	def test_a_verified_licence_is_never_edited_cancelled_or_deleted(self):
		name = self.verified()
		doc = frappe.get_doc(DOCTYPE, name)
		doc.licence_number = "Changed afterwards"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, name).cancel()
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.delete_doc(DOCTYPE, name, ignore_permissions=True)

	def test_a_verified_renewal_supersedes_the_old_one(self):
		old = self.verified()
		new = self.verified(renews=old, valid_from=today())
		self.assertEqual(frappe.db.get_value(DOCTYPE, old, ["status", "superseded_by"]), ("Superseded", new))
		self.assertTrue(verify_signatures(DOCTYPE, old))

	def test_a_rejected_renewal_keeps_the_original(self):
		old = self.verified()
		renewal = self.draft(renews=old)
		self.act(self.qa, renewal, "Reject", "Number does not match the authority's letter")
		self.assertEqual(frappe.db.get_value(DOCTYPE, old, "status"), "Verified")

	def test_a_withdrawn_or_already_renewed_licence_is_not_superseded_by_a_renewal(self):
		"""The licence renewed is checked when the renewal is verified, not only when it is drafted."""
		withdrawn = self.verified()
		late = self.draft(renews=withdrawn)  # drafted while it was verified
		self.act(self.qa, withdrawn, "Withdraw", "Suspended by the authority")
		with self.assertRaisesRegex(frappe.ValidationError, "only a verified licence is renewed"):
			self.act(self.qa, late, "Verify")
		self.assertEqual(frappe.db.get_value(DOCTYPE, withdrawn, "status"), "Withdrawn")
		original = self.verified()
		first, second = self.draft(renews=original), self.draft(renews=original)
		self.act(self.qa, first, "Verify")
		with self.assertRaisesRegex(frappe.ValidationError, "only a verified licence is renewed"):
			self.act(self.qa, second, "Verify")
		self.assertEqual(frappe.db.get_value(DOCTYPE, original, "superseded_by"), first)

	def test_a_draft_never_carries_what_qas_later_acts_record(self):
		frappe.set_user(self.qa)
		try:
			doc = frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"licence_type": self.type,
					"licence_number": "MFG/TYPED",
					"issuing_authority": "State Licensing Authority",
					"valid_from": today(),
					"valid_until": add_days(today(), 365),
					"expiry_alerted": 1,
					"decided_by": self.purchase,
					"verified_file_sha256": "0" * 64,
					"obligations": [
						{
							"obligation": "Retention fee",
							"due_date": today(),
							"done_on": today(),
							"done_by": self.purchase,
							"alerted": 1,
						}
					],
				}
			).insert()
			self.assertEqual((doc.expiry_alerted, doc.decided_by, doc.verified_file_sha256), (0, None, None))
			self.assertEqual(
				(doc.obligations[0].done_on, doc.obligations[0].done_by, doc.obligations[0].alerted),
				(None, None, 0),
			)
			doc.append(
				"submissions",
				{
					"submission_type": "Other",
					"submitted_on": today(),
					"authority_reference": "X",
					"important_file": self.copy(),
				},
			)
			frappe.set_user(self.qa)
			with self.assertRaisesRegex(frappe.ValidationError, "recorded on a verified licence"):
				doc.save()
		finally:
			frappe.set_user("Administrator")

	def test_only_a_verified_licence_of_the_same_type_is_renewed(self):
		with self.assertRaisesRegex(frappe.ValidationError, "only a verified licence is renewed"):
			self.draft(renews=self.draft())
		other = self.licence_type("ISO 13485 certificate", expires=1)
		with self.assertRaisesRegex(frappe.ValidationError, "same type"):
			self.draft(renews=self.verified(), licence_type=other)


@verifies("URS-REG-004")
class TestAlertsAndObligations(LicenceTestCase):
	def sent(self, name: str) -> int:
		return frappe.db.count(
			"Notification Log", {"document_type": DOCTYPE, "document_name": name, "for_user": self.qa}
		)

	def test_alerted_once_within_the_lead_time_or_overdue_until_renewed(self):
		soon = self.verified(valid_until=add_days(today(), 90))
		later = self.verified(valid_until=add_days(today(), 91))
		regulatory_licence.send_expiry_alerts()
		regulatory_licence.send_expiry_alerts()
		self.assertEqual((self.sent(soon), self.sent(later)), (1, 0))
		self.assertIn(soon, {d.reference_name for d in reminders.regulatory_licences_due()})
		self.assertNotIn(later, {d.reference_name for d in reminders.regulatory_licences_due()})
		self.verified(renews=soon, valid_from=today())
		self.assertNotIn(soon, {d.reference_name for d in reminders.regulatory_licences_due()})
		with use_setting("regulatory_licence_warning_days", 120):
			self.assertIn(later, {d.reference_name for d in reminders.regulatory_licences_due()})

	def test_an_obligation_is_due_until_qa_records_it_done_with_evidence(self):
		name = self.verified(obligations=[{"obligation": "Retention fee", "due_date": add_days(today(), -1)}])
		row = frappe.get_doc(DOCTYPE, name).obligations[0].name
		regulatory_licence.send_expiry_alerts()
		self.assertEqual(self.sent(name), 1)
		self.assertIn(row, {d.part for d in reminders.regulatory_licences_due() if d.reference_name == name})
		frappe.set_user(self.qa)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "evidence"):
				regulatory_licence.complete_obligation(name, row, "")
			evidence = self.copy()
			frappe.set_user(self.qa)
			regulatory_licence.complete_obligation(name, row, evidence)
		finally:
			frappe.set_user("Administrator")
		self.assertNotIn(
			row, {d.part for d in reminders.regulatory_licences_due() if d.reference_name == name}
		)
		self.assertTrue(
			is_unchanged_since(
				frappe.get_last_doc(
					"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
				)
			)
		)
		evidence = self.copy()
		frappe.set_user(self.purchase)
		try:
			with self.assertRaises(frappe.PermissionError):
				regulatory_licence.complete_obligation(name, row, evidence)
		finally:
			frappe.set_user("Administrator")


@verifies("URS-REG-004")
class TestObligationsAfterVerification(LicenceTestCase):
	def test_qa_adds_the_next_obligation_and_the_verification_still_stands(self):
		name = self.verified(
			obligations=[{"obligation": "Retention fee 2026", "due_date": add_days(today(), 200)}]
		)
		verification = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
		)
		frappe.set_user(self.purchase)
		try:
			with self.assertRaises(frappe.PermissionError):
				regulatory_licence.add_obligation(name, "Surveillance audit", add_days(today(), 30))
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(self.qa)
		try:
			regulatory_licence.add_obligation(name, "Surveillance audit", add_days(today(), 30))
		finally:
			frappe.set_user("Administrator")
		rows = {row.obligation: row for row in frappe.get_doc(DOCTYPE, name).obligations}
		self.assertEqual(
			(
				rows["Surveillance audit"].added_after_verification,
				rows["Retention fee 2026"].added_after_verification,
			),
			(1, 0),
		)
		self.assertIn(
			rows["Surveillance audit"].name,
			{d.part for d in reminders.regulatory_licences_due() if d.reference_name == name},
		)
		self.assertTrue(is_unchanged_since(verification))  # what QA verified is unchanged


@verifies("URS-REG-001", "URS-REG-004")
class TestRegister(LicenceTestCase):
	def test_the_register_works_out_each_licences_standing_from_its_dates(self):
		from meddevice_suite.qms.report.licence_register.licence_register import execute

		valid = self.verified()
		due = self.verified(valid_until=add_days(today(), 30))
		expired = self.verified(valid_until=add_days(today(), 1))
		frappe.db.set_value(DOCTYPE, expired, "valid_until", add_days(today(), -1))  # time has passed
		frappe.set_user(self.qa)
		try:
			rows = {row.name: row.standing for row in execute()[1]}
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((rows[valid], rows[due], rows[expired]), ("Valid", "Due for renewal", "Expired"))
		self.assertEqual(frappe.db.get_value(DOCTYPE, expired, "status"), "Verified")  # derived, not stored


@verifies("URS-REG-005")
class TestSubmissions(LicenceTestCase):
	def test_qa_records_a_submission_with_its_reference_and_copy(self):
		name, copy = self.verified(), self.copy()
		frappe.set_user(self.qa)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "reference"):
				regulatory_licence.add_submission(name, "Device Master File", today(), " ", copy)
			regulatory_licence.add_submission(name, "Device Master File", today(), "CDSCO/DMF/0042", copy)
		finally:
			frappe.set_user("Administrator")
		(row,) = frappe.get_doc(DOCTYPE, name).submissions
		self.assertEqual(
			(row.submission_type, row.authority_reference, row.recorded_by),
			("Device Master File", "CDSCO/DMF/0042", self.qa),
		)
		self.assertTrue(verify_signatures(DOCTYPE, name))


@verifies("URS-REG-001")
class TestPermissions(LicenceTestCase):
	def test_qa_records_management_reads_others_nothing(self):
		self.assertTrue(frappe.has_permission(DOCTYPE, "create", user=self.qa))
		self.assertFalse(frappe.has_permission(DOCTYPE, "create", user=self.purchase))
		self.assertFalse(frappe.has_permission(DOCTYPE, "read", user=self.sales))
		for user in (self.qa, self.purchase):
			self.assertFalse(frappe.has_permission(DOCTYPE, "submit", user=user))
			self.assertFalse(frappe.has_permission(DOCTYPE, "delete", user=user))
			self.assertFalse(frappe.has_permission(DOCTYPE, "cancel", user=user))
