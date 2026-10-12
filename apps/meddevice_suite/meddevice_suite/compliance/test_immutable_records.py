"""The evidence records are written only by their services, and say what was meant.

Found in the foundation review of 2026-10-12: a signature or an audit event could be
inserted past its service (the audit anchors added then are held to the same contract), a permission granted by hand could open them, a user in the
audit trail could be deleted and the ID reused, two acts were signed with a meaning that
was not the act's, and a clock set back read as a broken signature chain.
"""

import ast
import importlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.client import insert as insert_from_request
from frappe.model.document import Document
from frappe.permissions import add_permission
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, get_fullname, now_datetime

from meddevice_suite.compliance.audit import event_fingerprint, last_event, record, verify_events
from meddevice_suite.compliance.audit_scope import SELF_RECORDING
from meddevice_suite.compliance.esign import (
	MEANINGS,
	last_signature,
	record_fingerprint,
	sign,
	signature_fingerprint,
	verify_signatures,
)
from meddevice_suite.compliance.signing import SIGNING_ACTIONS, sign_action
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase, events_for
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, VIEWER, ensure_user, reset_lockout
from meddevice_suite.compliance.test_esign import SigningTestCase as ToDoSigningTestCase
from meddevice_suite.compliance.test_signing import SigningTestCase as DialogTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.setup.roles import remove_hand_added_permissions

# Every evidence record type, so that one added later (the audit anchors) is held to the same contract.
EVIDENCE = tuple(sorted(SELF_RECORDING))
WRITING = ("create", "write", "delete", "submit", "cancel", "amend", "import", "share")


def new_user(roles: list[str]) -> str:
	email = f"leaver.{frappe.generate_hash(length=8)}@example.com"
	ensure_user(email, "Leaver", roles)
	return email


def permission_row(parent: str, **flags: int) -> Document:
	return frappe.get_doc(
		{"doctype": "Custom DocPerm", "parent": parent, "role": "System Manager", "read": 1, **flags}
	)


class EvidenceTestCase(AuditTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])
		ensure_user(VIEWER, "Viewer", [])

	def setUp(self):
		super().setUp()
		reset_lockout(SIGNER)
		self.addCleanup(reset_lockout, SIGNER)
		self.addCleanup(frappe.set_user, "Administrator")
		self.doc = self.new_record()

	def forged_signature(self, **values) -> Document:
		"""A signature in SIGNER's name, chained and fingerprinted exactly as signing would."""
		previous = last_signature(AUDITED, self.doc.name)
		signature = frappe.get_doc(
			{
				"doctype": "Electronic Signature",
				"reference_doctype": AUDITED,
				"reference_name": self.doc.name,
				"document_name_at_signing": self.doc.name,
				"action": "Approve",
				"meaning": "Approved",
				"signer": SIGNER,
				"signer_name": get_fullname(SIGNER),
				"signed_at": now_datetime(),
				"record_hash": record_fingerprint(self.doc),
				"previous_signature": previous.name if previous else None,
				**values,
			}
		)
		signature.signature_hash = signature_fingerprint(
			signature, previous.signature_hash if previous else ""
		)
		return signature

	def forged_event(self) -> Document:
		"""An audit event in SIGNER's name, chained and fingerprinted exactly as the trail would."""
		previous = last_event(AUDITED, self.doc.name)
		event = frappe.get_doc(
			{
				"doctype": "Audit Event",
				"reference_doctype": AUDITED,
				"reference_name": self.doc.name,
				"document_name_at_event": self.doc.name,
				"action": "Updated",
				"user": SIGNER,
				"event_at": now_datetime(),
				"changes": {"changed": [["qty", 1.0, 100.0]]},
				"previous_event": previous.name if previous else None,
			}
		)
		event.event_hash = event_fingerprint(event, previous.event_hash if previous else "")
		return event

	def signatures(self) -> list[str]:
		return frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": AUDITED, "reference_name": self.doc.name},
			pluck="name",
		)


@verifies("URS-CMP-005")
class TestSignaturesComeOnlyFromSigning(EvidenceTestCase):
	def test_a_signature_inserted_past_signing_is_refused_whatever_is_skipped(self):
		for skipped in ({"ignore_permissions": True}, {"ignore_permissions": True, "ignore_mandatory": True}):
			with self.subTest(skipped=skipped):
				forged = self.forged_signature()
				forged.flags.ignore_validate = True
				with self.assertRaises(frappe.PermissionError):
					forged.insert(**skipped)
		self.assertEqual(self.signatures(), [])

	def test_a_request_cannot_mark_its_signature_as_signed(self):
		forged = self.forged_signature().as_dict()
		forged["flags"] = {"recorded_by_signing": 1}  # Frappe drops flags sent with a record
		with self.assertRaises(frappe.PermissionError):
			insert_from_request(forged)
		self.assertEqual(self.signatures(), [])

	def test_a_signature_under_another_persons_account_is_refused(self):
		frappe.set_user(VIEWER)
		forged = self.forged_signature()  # names SIGNER, made in VIEWER's session
		forged.flags.recorded_by_signing = True
		with self.assertRaises(frappe.PermissionError):
			forged.insert(ignore_permissions=True)
		self.assertEqual(self.signatures(), [])

	def test_signing_still_records_the_signers_own_signature(self):
		frappe.set_user(SIGNER)
		signature = sign(self.doc, "Approved", "Approve", PASSWORD)

		self.assertEqual((signature.owner, signature.signer), (SIGNER, SIGNER))
		self.assertEqual(self.signatures(), [signature.name])
		self.assertTrue(verify_signatures(AUDITED, self.doc.name))


@verifies("URS-CMP-009")
class TestAuditEventsComeOnlyFromTheTrail(EvidenceTestCase):
	def test_an_audit_event_inserted_past_the_trail_is_refused(self):
		before = [event.name for event in events_for(AUDITED, self.doc.name)]
		forged = self.forged_event()
		forged.flags.ignore_validate = True
		with self.assertRaises(frappe.PermissionError):
			forged.insert(ignore_permissions=True)

		self.assertEqual([event.name for event in events_for(AUDITED, self.doc.name)], before)
		self.assertEqual(verify_events(AUDITED, self.doc.name), [])

	def test_a_request_cannot_mark_its_event_as_recorded(self):
		forged = self.forged_event().as_dict()
		forged["flags"] = {"recorded_by_audit_trail": 1}
		with self.assertRaises(frappe.PermissionError):
			insert_from_request(forged)

	def test_the_trail_still_records(self):
		event = record(AUDITED, self.doc.name, "Updated", {"changed": [["qty", 1.0, 2.0]]})

		self.assertEqual(events_for(AUDITED, self.doc.name)[-1].name, event.name)
		self.assertEqual(verify_events(AUDITED, self.doc.name), [])


@verifies("URS-CMP-015")
class TestPermissionsOnTheAppsRecordTypes(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def assert_no_rows(self, parent: str) -> None:
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": parent}))

	def test_no_role_is_given_a_way_to_write_the_evidence_records_even_on_migrate(self):
		for in_migrate in (False, True):
			for parent in EVIDENCE:
				for flag in WRITING:
					with self.subTest(in_migrate=in_migrate, parent=parent, flag=flag):
						with (
							patch.dict(frappe.flags, {"in_migrate": in_migrate}),
							self.assertRaises(frappe.PermissionError),
						):
							permission_row(parent, **{flag: 1}).insert(ignore_permissions=True)
						self.assert_no_rows(parent)

	@verifies("URS-CMP-005", "URS-CMP-009", "URS-CMP-040")
	def test_every_evidence_record_type_is_read_only_for_every_role(self):
		self.assertIn("Audit Anchor", EVIDENCE)
		for parent in EVIDENCE:
			meta = frappe.get_meta(parent)
			with self.subTest(parent=parent):
				self.assertTrue(meta.in_create)
				for rule in meta.permissions:
					self.assertFalse([flag for flag in WRITING if rule.get(flag)], rule.role)

	def test_no_row_is_added_by_hand_to_the_apps_own_record_types(self):
		for parent in (*EVIDENCE, "Quality Settings", "Change Request"):
			with self.subTest(parent=parent):
				with self.assertRaises(frappe.PermissionError):
					permission_row(parent).insert(ignore_permissions=True)
				with self.assertRaises(frappe.PermissionError):  # as Role Permissions Manager adds one
					add_permission(parent, "Sales User")
				self.assert_no_rows(parent)

	def test_a_row_written_straight_to_the_database_is_removed_on_the_next_migrate(self):
		permission_row("Electronic Signature", create=1, write=1).db_insert()
		permission_row("Quality Settings", write=1).db_insert()

		remove_hand_added_permissions()  # as on every migrate

		for parent in ("Electronic Signature", "Quality Settings"):
			with self.subTest(parent=parent):
				self.assert_no_rows(parent)
		frappe.clear_cache(doctype="Electronic Signature")
		for rule in frappe.get_meta("Electronic Signature").permissions:
			with self.subTest(role=rule.role):
				self.assertFalse(rule.create or rule.write)

	def test_rows_on_other_record_types_are_still_written_by_the_configuration(self):
		from meddevice_suite.setup.roles import apply_roles, declared_rows
		from meddevice_suite.setup.test_roles import applied_rows

		frappe.db.delete("Custom DocPerm", {"parent": "Quality Inspection"})
		apply_roles()
		self.assertEqual(applied_rows("Quality Inspection"), declared_rows("Quality Inspection"))


@verifies("URS-CMP-006")
class TestUserIdsInTheTrailAreNeverDeleted(EvidenceTestCase):
	def assert_kept(self, user: str) -> None:
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):  # refused before Frappe's link check
			frappe.delete_doc("User", user)
		self.assertTrue(frappe.db.exists("User", user))

	def test_a_user_who_acted_on_a_regulated_record_is_kept(self):
		user = new_user(["System Manager"])
		frappe.set_user(user)
		self.new_record()

		self.assert_kept(user)

	def test_a_user_who_acted_for_someone_else_is_kept(self):
		user = new_user([])
		with patch.dict(frappe.session.data, {"impersonated_by": user}):
			self.new_record()

		self.assert_kept(user)

	def test_a_user_who_signed_is_kept(self):
		user = new_user(["System Manager"])
		reset_lockout(user)
		frappe.set_user(user)
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Not a regulated record"}).insert()
		sign(todo, "Approved", "Check", PASSWORD)

		self.assert_kept(user)

	def test_a_user_who_owns_a_regulated_record_from_before_the_trail_is_kept(self):
		user = new_user(["System Manager"])
		frappe.set_user(user)
		doc = self.new_record()
		frappe.db.delete("Audit Event", {"reference_doctype": AUDITED, "reference_name": doc.name})

		self.assert_kept(user)

	def test_a_user_with_no_history_can_still_be_deleted(self):
		user = new_user([])

		frappe.delete_doc("User", user)

		self.assertFalse(frappe.db.exists("User", user))

	def test_an_account_with_history_is_disabled_instead_and_signs_no_more(self):
		user = new_user(["System Manager"])
		frappe.set_user(user)
		self.new_record()
		frappe.set_user("Administrator")

		account = frappe.get_doc("User", user)
		account.enabled = 0
		account.save()

		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			sign(self.doc, "Approved", "Approve", PASSWORD)


def meaning_options() -> list[str]:
	path = Path(frappe.get_app_path("meddevice_suite", "compliance", "doctype", "electronic_signature"))
	fields = json.loads((path / "electronic_signature.json").read_text())["fields"]
	return next(field for field in fields if field["fieldname"] == "meaning")["options"].split("\n")


def meanings_explained_in_the_dialog() -> list[str]:
	source = Path(frappe.get_app_path("meddevice_suite", "public", "js", "esign.js")).read_text()
	block = source.split("const MEANING_TEXT = {", 1)[1].split("};", 1)[0]
	return re.findall(r"^\t(\w+): __\(", block, flags=re.MULTILINE)


def literal_signings(module: str) -> list[tuple[str, str, int]]:
	"""(meaning, action, line) of every `sign(...)` call in the module naming both as text."""
	tree = ast.parse(Path(importlib.import_module(module).__file__).read_text())
	found = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
		if name != "sign":
			continue
		given = dict(zip(("doc", "meaning", "action"), node.args, strict=False))
		given.update({keyword.arg: keyword.value for keyword in node.keywords})
		meaning, action = given.get("meaning"), given.get("action")
		if isinstance(meaning, ast.Constant) and isinstance(action, ast.Constant):
			found.append((meaning.value, action.value, node.lineno))
	return found


@verifies("URS-CMP-002")
class TestMeanings(UnitTestCase):
	def test_every_meaning_is_stored_and_explained_to_the_signer(self):
		self.assertIn("Withdrawn", MEANINGS)
		self.assertEqual(meaning_options(), list(MEANINGS))
		self.assertEqual(sorted(meanings_explained_in_the_dialog()), sorted(MEANINGS))

	def test_each_act_is_signed_with_the_meaning_of_the_act(self):
		from meddevice_suite.master_data import customer_licence

		self.assertEqual(SIGNING_ACTIONS[("Controlled Document", "Withdraw")].meaning, "Withdrawn")
		# Ending an exemption revokes QA's approval of it.
		self.assertEqual(SIGNING_ACTIONS[("Customer", "Require Licence")].meaning, "Rejected")
		self.assertEqual(SIGNING_ACTIONS[("Customer", "Exempt From Licence")].meaning, "Approved")
		self.assertEqual(customer_licence.EXEMPT_ACTS["Require Licence"], (0, "Rejected"))

	def test_every_declared_meaning_is_a_meaning(self):
		for (doctype, action), act in SIGNING_ACTIONS.items():
			with self.subTest(doctype=doctype, action=action):
				self.assertIn(act.meaning, MEANINGS)


@verifies("URS-CMP-030")
class TestDeclaredMeaningIsTheSignedMeaning(UnitTestCase):
	"""The meaning is written in the register, in the act and in the form's script; these keep the
	act in step with the register before anything runs, and `esign.sign` refuses a mismatch."""

	def test_each_act_signs_with_its_declared_meaning(self):
		for (doctype, action), act in SIGNING_ACTIONS.items():
			module = act.perform.rpartition(".")[0]
			self.assertTrue(callable(frappe.get_attr(act.perform)), act.perform)
			for meaning, signed, line in literal_signings(module):
				if signed == action:
					with self.subTest(doctype=doctype, action=action, at=f"{module}:{line}"):
						self.assertEqual(meaning, act.meaning)

	def test_the_meaning_tables_of_the_acts_match_the_register(self):
		from meddevice_suite.master_data import customer_licence, supplier_approval

		for doctype, acts in (
			("Customer Licence", customer_licence.ACTS),
			("Supplier Approval", supplier_approval.ACTS),
			("Customer", customer_licence.EXEMPT_ACTS),
		):
			for action, (_outcome, meaning) in acts.items():
				with self.subTest(doctype=doctype, action=action):
					self.assertEqual(meaning, SIGNING_ACTIONS[(doctype, action)].meaning)


@verifies("URS-CMP-030")
class TestDeclaredMeaningIsEnforced(DialogTestCase):
	def test_a_declared_act_is_never_signed_with_another_meaning(self):
		with self.assertRaises(frappe.ValidationError):
			sign(self.doc, "Rejected", "Approve", PASSWORD)
		self.assertEqual(self.signatures(), [])

	def test_the_dialog_signs_only_under_the_meaning_it_showed(self):
		with self.assertRaises(frappe.ValidationError):
			sign_action(AUDITED, self.doc.name, "Approve", PASSWORD, meaning="Released")
		self.assertEqual(self.signatures(), [])

		sign_action(AUDITED, self.doc.name, "Approve", PASSWORD, meaning="Approved")
		self.assertEqual([s.meaning for s in self.signatures()], ["Approved"])


@verifies("URS-CMP-004")
class TestChainFollowedByLinks(ToDoSigningTestCase):
	def forged(self, previous) -> None:
		"""A well-formed signature written past every check, claiming to follow `previous`."""
		signature = frappe.get_doc(
			{
				"doctype": "Electronic Signature",
				"reference_doctype": "ToDo",
				"reference_name": self.todo.name,
				"document_name_at_signing": self.todo.name,
				"action": "Stock Release",
				"meaning": "Approved",
				"signer": SIGNER,
				"signer_name": get_fullname(SIGNER),
				"signed_at": now_datetime(),
				"record_hash": record_fingerprint(self.todo),
				"previous_signature": previous.name if previous else None,
			}
		)
		signature.signature_hash = signature_fingerprint(
			signature, previous.signature_hash if previous else ""
		)
		signature.db_insert()

	def test_a_clock_set_back_between_signings_is_not_tampering(self):
		first = self.sign(meaning="Reviewed")
		with patch(
			"meddevice_suite.compliance.esign.now_datetime", return_value=add_days(now_datetime(), -1)
		):
			second = self.sign(meaning="Approved")

		self.assertLess(second.signed_at, first.signed_at)
		self.assertEqual(second.previous_signature, first.name)
		self.assertTrue(verify_signatures("ToDo", self.todo.name))

	def test_a_second_signature_claiming_the_same_place_is_detected(self):
		first = self.sign(meaning="Reviewed")
		self.sign(meaning="Approved")
		self.forged(first)

		self.assertFalse(verify_signatures("ToDo", self.todo.name))

	def test_a_second_first_signature_is_detected(self):
		self.sign(meaning="Reviewed")
		self.forged(None)

		self.assertFalse(verify_signatures("ToDo", self.todo.name))

	def test_a_record_without_signatures_verifies(self):
		self.assertTrue(verify_signatures("ToDo", self.todo.name))
