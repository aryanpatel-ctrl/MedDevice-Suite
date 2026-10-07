from unittest.mock import patch

import frappe
from frappe.utils import get_fullname

from meddevice_suite.compliance.esign import sign
from meddevice_suite.compliance.signing import (
	SignedAct,
	onload,
	sign_action,
	signatures_for_print,
	signing_actions,
)
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, VIEWER, ensure_user, reset_lockout
from meddevice_suite.qa import verifies

APPROVE = {(AUDITED, "Approve"): SignedAct("Approved", "meddevice_suite.compliance.test_signing.approve")}


def approve(doc, password: str, reason: str | None = None) -> None:
	"""The signed act registered for the tests: sign as approved, nothing else."""
	sign(doc, "Approved", "Approve", password, reason)


class SigningTestCase(AuditTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])
		ensure_user(VIEWER, "Viewer", [])

	def setUp(self):
		super().setUp()
		self.enterContext(patch.dict("meddevice_suite.compliance.signing.SIGNING_ACTIONS", APPROVE))
		reset_lockout(SIGNER)
		self.addCleanup(reset_lockout, SIGNER)
		self.addCleanup(frappe.set_user, "Administrator")
		self.doc = self.new_record()
		frappe.set_user(SIGNER)

	def signatures(self) -> list[frappe._dict]:
		return frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": AUDITED, "reference_name": self.doc.name},
			fields=["signer", "meaning", "action", "reason"],
		)


@verifies("URS-CMP-030")
class TestSigningDialog(SigningTestCase):
	def test_a_declared_act_is_signed_with_the_signers_password(self):
		sign_action(AUDITED, self.doc.name, "Approve", PASSWORD, "  Released for use  ")

		self.assertEqual(
			self.signatures(),
			[{"signer": SIGNER, "meaning": "Approved", "action": "Approve", "reason": "Released for use"}],
		)

	def test_an_act_that_is_not_declared_cannot_be_signed(self):
		with self.assertRaises(frappe.PermissionError):
			sign_action(AUDITED, self.doc.name, "Release", PASSWORD)
		self.assertEqual(self.signatures(), [])

	def test_a_wrong_password_records_nothing(self):
		with self.assertRaises(frappe.AuthenticationError):
			sign_action(AUDITED, self.doc.name, "Approve", "not my password")
		self.assertEqual(self.signatures(), [])

	def test_someone_who_cannot_open_the_record_cannot_sign_it(self):
		frappe.set_user(VIEWER)
		with self.assertRaises(frappe.PermissionError):
			sign_action(AUDITED, self.doc.name, "Approve", PASSWORD)

	def test_the_dialog_is_offered_only_the_declared_acts(self):
		self.assertEqual(signing_actions(AUDITED), {"Approve": "Approved"})
		self.assertEqual(signing_actions("ToDo"), {})


@verifies("URS-CMP-003")
class TestSignaturesShownWithTheRecord(SigningTestCase):
	def test_the_form_shows_who_signed_when_and_what_it_means(self):
		sign_action(AUDITED, self.doc.name, "Approve", PASSWORD)

		doc = frappe.get_doc(AUDITED, self.doc.name)
		onload(doc)

		[shown] = doc.get_onload().signatures
		self.assertEqual(
			(shown.signer_name, shown.meaning, shown.action), (get_fullname(SIGNER), "Approved", "Approve")
		)
		self.assertTrue(shown.signed_at)

	def test_a_record_without_signatures_shows_none(self):
		doc = frappe.get_doc(AUDITED, self.doc.name)
		onload(doc)

		self.assertIsNone(doc.get_onload().get("signatures"))

	def test_record_types_that_are_not_regulated_show_no_signature_panel(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Not a GMP record"}).insert()
		sign(todo, "Approved", "Approve", PASSWORD)

		onload(todo)

		self.assertIsNone(todo.get_onload().get("signatures"))

	def test_printouts_carry_the_signatures(self):
		sign_action(AUDITED, self.doc.name, "Approve", PASSWORD)

		printed = frappe.render_template(
			"{% for s in signatures_for_print(doc) %}{{ s.meaning }} by {{ s.signer_name }} at {{ s.signed_at }}{% endfor %}",
			{"doc": frappe.get_doc(AUDITED, self.doc.name)},
		)

		self.assertIn(f"Approved by {get_fullname(SIGNER)} at ", printed)
		self.assertEqual(len(signatures_for_print(self.doc)), 1)
