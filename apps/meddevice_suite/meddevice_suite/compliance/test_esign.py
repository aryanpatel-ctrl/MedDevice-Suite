import frappe
from frappe.auth import get_login_attempt_tracker
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_to_date, now_datetime, time_diff_in_seconds
from frappe.utils.user import UserPermissions

from meddevice_suite.compliance.esign import (
	LOCKOUT_ATTEMPTS,
	LOCKOUT_SECONDS,
	SigningRefused,
	apply_lockout,
	is_unchanged_since,
	record_fingerprint,
	sign,
	verify_signatures,
)
from meddevice_suite.compliance.fingerprint import sha256, without_volatile_fields
from meddevice_suite.qa import verifies

SIGNER = "esign.signer@example.com"
VIEWER = "esign.viewer@example.com"
PASSWORD = "Ld9#vQ2!pZ7wR4xt"


def ensure_user(email: str, first_name: str, roles: list[str]) -> None:
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": "Tester",
			"send_welcome_email": 0,
			"new_password": PASSWORD,
			"roles": [{"role": role} for role in roles],
		}
	).insert(ignore_permissions=True)


def signatures_on(doc) -> list[str]:
	return frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doc.doctype, "reference_name": doc.name},
		pluck="name",
	)


def reset_lockout(user: str) -> None:
	tracker = get_login_attempt_tracker(user, raise_locked_exception=False)
	tracker.add_success_attempt()


class SigningTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])
		ensure_user(VIEWER, "Viewer", [])

	def setUp(self):
		reset_lockout(SIGNER)
		frappe.set_user(SIGNER)
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": "Release batch B-001"}).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		reset_lockout(SIGNER)

	def sign(self, **overrides):
		arguments = {"meaning": "Approved", "action": "Stock Release", "password": PASSWORD} | overrides
		return sign(self.todo, **arguments)


@verifies("URS-CMP-001")
class TestReauthentication(SigningTestCase):
	def test_correct_password_records_a_signature(self):
		signature = self.sign()

		self.assertEqual(signatures_on(self.todo), [signature.name])

	def test_wrong_password_records_nothing_and_counts_as_a_failed_login(self):
		with self.assertRaises(SigningRefused) as refused:
			self.sign(password="wrong password")

		# Frappe signs the user out on its own authentication error; a typo must not.
		self.assertNotIsInstance(refused.exception, frappe.AuthenticationError)
		self.assertEqual(signatures_on(self.todo), [])
		self.assertEqual(
			get_login_attempt_tracker(SIGNER, raise_locked_exception=False).login_failed_count, 1
		)

	def test_a_wrong_password_is_kept_in_the_login_history(self):
		with self.assertRaises(SigningRefused):
			self.sign(password="wrong password")

		self.assertTrue(
			frappe.db.exists(
				"Activity Log",
				{"user": SIGNER, "status": "Failed", "subject": ["like", f"%ToDo {self.todo.name}"]},
			)
		)

	def test_the_lockout_counts_signing_failures_up_to_its_limit(self):
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 3)
		for _attempt in range(2):
			with self.assertRaises(SigningRefused):
				self.sign(password="wrong password")

		# Below the limit the correct password still signs, and clears the count.
		self.sign()
		self.assertFalse(get_login_attempt_tracker(SIGNER, raise_locked_exception=False).login_failed_count)

		# As for logging in, the setting is the number of failures allowed: one more locks.
		for _attempt in range(3):
			with self.assertRaises(SigningRefused):
				self.sign(password="wrong password")
		self.assertTrue(get_login_attempt_tracker(SIGNER, raise_locked_exception=False).is_user_allowed())
		with self.assertRaises(SigningRefused):
			self.sign(password="wrong password")
		with self.assertRaises(frappe.SecurityException):
			self.sign()
		self.assertEqual(len(signatures_on(self.todo)), 1)  # only the signature made before the lock

	def test_the_lock_lifts_after_the_lockout_period(self):
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 3)
		frappe.db.set_single_value("System Settings", "allow_login_after_fail", 60)
		tracker = get_login_attempt_tracker(SIGNER, raise_locked_exception=False)
		tracker.login_failed_count = 3
		tracker.login_failed_time = add_to_date(now_datetime(), seconds=-61)

		self.sign()

	def test_a_disabled_account_cannot_sign(self):
		frappe.db.set_value("User", SIGNER, "enabled", 0)
		self.addCleanup(frappe.db.set_value, "User", SIGNER, "enabled", 1)

		with self.assertRaises(frappe.PermissionError):
			self.sign()
		self.assertEqual(signatures_on(self.todo), [])

	def test_locked_account_cannot_sign_even_with_the_correct_password(self):
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 3)
		tracker = get_login_attempt_tracker(SIGNER, raise_locked_exception=False)
		tracker.login_failed_time = now_datetime()
		tracker.login_failed_count = 4

		with self.assertRaises(frappe.SecurityException):
			self.sign()

		self.assertEqual(signatures_on(self.todo), [])


@verifies("URS-CMP-002")
class TestLockoutConfiguration(SigningTestCase):
	@verifies("URS-CMP-001")
	def test_the_site_locks_an_account_after_wrong_passwords(self):
		"""A site allowing unlimited attempts would never lock; the configuration gives it the
		standard limit, and keeps a stricter one."""
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 0)
		frappe.db.set_single_value("System Settings", "allow_login_after_fail", 0)
		frappe.clear_cache(doctype="System Settings")
		apply_lockout()
		self.assertEqual(
			frappe.db.get_single_value("System Settings", "allow_consecutive_login_attempts"),
			LOCKOUT_ATTEMPTS,
		)
		self.assertEqual(
			frappe.db.get_single_value("System Settings", "allow_login_after_fail"), LOCKOUT_SECONDS
		)
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 3)
		frappe.clear_cache(doctype="System Settings")
		apply_lockout()
		self.assertEqual(frappe.db.get_single_value("System Settings", "allow_consecutive_login_attempts"), 3)


class TestSignatureContent(SigningTestCase):
	def test_records_who_when_what_and_meaning(self):
		signature = self.sign(meaning="Released", action="Batch Release", reason="All checks passed")

		self.assertEqual(signature.signer, SIGNER)
		self.assertEqual(signature.signer_name, "Signer Tester")
		self.assertEqual(signature.action, "Batch Release")
		self.assertEqual(signature.meaning, "Released")
		self.assertEqual(signature.reason, "All checks passed")
		self.assertLess(abs(time_diff_in_seconds(now_datetime(), signature.signed_at)), 60)

	def test_unknown_meaning_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.sign(meaning="Liked")

	def test_action_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			self.sign(action="")

	def test_the_reason_is_stored_exactly_as_signed(self):
		for reason in ('Lot 7 & 8 checked; seal "OK"', "Prüfung bestanden ✓ 合格", "x" * 2000):
			with self.subTest(reason=reason[:20]):
				signature = self.sign(reason=reason)
				self.assertEqual(
					frappe.db.get_value("Electronic Signature", signature.name, "reason"), reason
				)
		self.assertTrue(verify_signatures(self.todo.doctype, self.todo.name))

	def test_a_reason_frappe_would_rewrite_is_refused_rather_than_altered(self):
		with self.assertRaises(frappe.ValidationError):
			self.sign(reason="Lot changed from <L-001> to <L-002>")
		self.assertEqual(signatures_on(self.todo), [])


@verifies("URS-CMP-004")
class TestBinding(SigningTestCase):
	def test_signatures_on_a_record_form_a_verified_chain(self):
		first = self.sign(meaning="Reviewed")
		second = self.sign(meaning="Approved")

		self.assertIsNone(first.previous_signature)
		self.assertEqual(second.previous_signature, first.name)
		self.assertTrue(verify_signatures(self.todo.doctype, self.todo.name))

	def test_an_altered_signature_is_detected(self):
		signature = self.sign(meaning="Approved")
		frappe.db.set_value("Electronic Signature", signature.name, "meaning", "Rejected")

		self.assertFalse(verify_signatures(self.todo.doctype, self.todo.name))

	def test_a_signature_removed_from_the_chain_is_detected(self):
		first = self.sign(meaning="Reviewed")
		self.sign(meaning="Approved")
		frappe.db.delete("Electronic Signature", first.name)

		self.assertFalse(verify_signatures(self.todo.doctype, self.todo.name))

	def test_a_change_to_the_signed_record_is_detected(self):
		signature = self.sign()
		self.assertTrue(is_unchanged_since(signature))

		self.todo.description = "Release batch B-002"
		self.todo.save()

		self.assertFalse(is_unchanged_since(signature))


@verifies("URS-CMP-006")
class TestSignerIdentity(SigningTestCase):
	def test_administrator_cannot_sign(self):
		frappe.set_user("Administrator")

		with self.assertRaises(frappe.PermissionError):
			self.sign()

		self.assertEqual(signatures_on(self.todo), [])

	def test_signer_needs_permission_on_the_record(self):
		frappe.set_user(VIEWER)

		with self.assertRaises(frappe.PermissionError):
			self.sign()

		self.assertEqual(signatures_on(self.todo), [])


@verifies("URS-CMP-004")
class TestFingerprint(UnitTestCase):
	def test_same_content_in_any_key_order_gives_the_same_fingerprint(self):
		self.assertEqual(sha256({"a": 1, "b": [1, 2]}), sha256({"b": [1, 2], "a": 1}))

	def test_any_content_change_changes_the_fingerprint(self):
		self.assertNotEqual(sha256({"qty": 10.0}), sha256({"qty": 10.5}))

	def test_a_rename_does_not_change_what_a_signature_binds_child_rows_included(self):
		def profile(name: str):
			doc = frappe.get_doc(
				{
					"doctype": "Role Profile",
					"role_profile": "Lab",
					"roles": [{"role": "Sales User", "name": "r1"}],
				}
			)
			doc.name = name
			for row in doc.roles:
				row.parent = name
			return doc

		self.assertEqual(record_fingerprint(profile("Lab")), record_fingerprint(profile("Laboratory")))
		changed = profile("Lab")
		changed.roles[0].role = "Stock User"
		self.assertNotEqual(record_fingerprint(profile("Lab")), record_fingerprint(changed))

	def test_bookkeeping_fields_are_ignored_at_every_level(self):
		record = {
			"qty": 1,
			"modified": "2026-10-05",
			"items": [{"item": "X", "modified_by": "a@b.c", "_seen": "[]"}],
		}

		self.assertEqual(without_volatile_fields(record), {"qty": 1, "items": [{"item": "X"}]})


@verifies("URS-CMP-005")
class TestSignaturesAreImmutable(SigningTestCase):
	def setUp(self):
		super().setUp()
		self.signature = self.sign()
		frappe.set_user("Administrator")

	def test_cannot_be_changed_even_by_administrator(self):
		signature = frappe.get_doc("Electronic Signature", self.signature.name)
		signature.reason = "edited later"

		with self.assertRaises(frappe.PermissionError):
			signature.save(ignore_permissions=True)

	def test_cannot_be_deleted_even_by_administrator(self):
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(
				"Electronic Signature", self.signature.name, ignore_permissions=True, force=True
			)

		self.assertTrue(frappe.db.exists("Electronic Signature", self.signature.name))

	def test_no_role_may_create_or_change_signatures(self):
		meta = frappe.get_meta("Electronic Signature")

		self.assertTrue(meta.in_create)
		for rule in meta.permissions:
			with self.subTest(role=rule.role):
				self.assertFalse(rule.create or rule.write or rule.delete or rule.submit or rule.amend)

	def test_users_who_may_read_signatures_can_list_and_search_them(self):
		permissions = UserPermissions(SIGNER)
		permissions.build_permissions()

		self.assertIn("Electronic Signature", permissions.can_read)


@verifies("URS-CMP-004")
class TestSignatureLookupsStayFastAtScale(IntegrationTestCase):
	def test_signatures_are_indexed_by_record_and_by_signer(self):
		indexes = {}
		for row in frappe.db.sql("SHOW INDEX FROM `tabElectronic Signature`", as_dict=True):
			indexes.setdefault(row.Key_name, []).append(row.Column_name)

		for columns in (["reference_doctype", "reference_name", "signed_at"], ["signer", "signed_at"]):
			with self.subTest(columns=columns):
				self.assertIn(columns, indexes.values())
