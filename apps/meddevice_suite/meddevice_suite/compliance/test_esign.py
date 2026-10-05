import frappe
from frappe.auth import get_login_attempt_tracker
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import now_datetime, time_diff_in_seconds

from meddevice_suite.compliance.esign import (
	is_unchanged_since,
	sha256,
	sign,
	verify_signatures,
	without_volatile_fields,
)
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
		with self.assertRaises(frappe.AuthenticationError):
			self.sign(password="wrong password")

		self.assertEqual(signatures_on(self.todo), [])
		self.assertEqual(
			get_login_attempt_tracker(SIGNER, raise_locked_exception=False).login_failed_count, 1
		)

	def test_locked_account_cannot_sign_even_with_the_correct_password(self):
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 3)
		tracker = get_login_attempt_tracker(SIGNER, raise_locked_exception=False)
		tracker.login_failed_time = now_datetime()
		tracker.login_failed_count = 4

		with self.assertRaises(frappe.SecurityException):
			self.sign()

		self.assertEqual(signatures_on(self.todo), [])


@verifies("URS-CMP-002")
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
