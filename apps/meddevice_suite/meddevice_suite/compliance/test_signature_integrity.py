"""Signatures checked against the record they sign, and chains the database alone cannot rewrite.

Found in the independent review of 2026-10-15 (21 CFR 11.10(e), 11.70):

1. Verify Integrity checked the signatures' chain but never whether the record still held what each
   signature bound, and a signature pointed at another record verified there.
2. A record type with a signed act but no declared content bound the whole record, so a field added
   to the record type broke every signature taken before it.
3. The chains were unkeyed SHA-256: database access alone could rewrite an older entry and compute
   the chain after it again.
4. The audit trail accepted a second start of a record's chain, which the signatures refused.
"""

from unittest.mock import patch

import frappe
from frappe.model.meta import Meta
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from meddevice_suite.compliance import audit, esign, fingerprint
from meddevice_suite.compliance.audit import event_fingerprint, verify_events
from meddevice_suite.compliance.audit_timeline import verify_record
from meddevice_suite.compliance.esign import (
	SIGNED_AGAIN,
	SIGNED_CONTENT,
	UNACCOUNTED,
	UNCHANGED,
	record_fingerprint,
	sign,
	signature_fingerprint,
	signature_problems,
	verify_signatures,
)
from meddevice_suite.compliance.fingerprint import FIRST_VERSION, KEYED_SINCE, in_chain_order
from meddevice_suite.compliance.signing import SIGNING_ACTIONS
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase, events_for
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, VIEWER, ensure_user, reset_lockout
from meddevice_suite.qa import verifies

ADDED_LATER = "mds_field_added_later"


def stored(doctype: str, reference: str) -> list[frappe._dict]:
	return frappe.get_all(
		doctype,
		filters={"reference_doctype": AUDITED, "reference_name": reference},
		fields=["*"],
		order_by="creation asc",
	)


def rewrite_chain(doctype: str, entries: list[frappe._dict], fingerprint_of, **values) -> None:
	"""What someone with database access alone can do: change entries and compute every fingerprint
	of the chain again, in order, writing the rows directly."""
	trail = audit.TRAIL if doctype == "Audit Event" else esign.TRAIL
	previous_hash = ""
	for entry in in_chain_order(entries, trail.link):
		changed = values.get(entry.name, {})
		entry.update(changed)
		entry[trail.fingerprint] = fingerprint_of(entry, previous_hash)
		written = {**changed, trail.fingerprint: entry[trail.fingerprint]}
		frappe.db.set_value(doctype, entry.name, written, update_modified=False)
		previous_hash = entry[trail.fingerprint]


def with_a_guessed_key(fingerprint_of):
	"""The fingerprint function as an attacker without the site's key would compute it."""

	def guessed(entry, previous_hash):
		with patch.object(fingerprint, "trail_key", return_value=b"a guess"):
			return fingerprint_of(entry, previous_hash)

	return guessed


class IntegrityTestCase(AuditTestCase):
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
		# As read from the database, as Verify Integrity reads it.
		self.doc = frappe.get_doc(AUDITED, self.new_record().name)
		frappe.set_user(SIGNER)

	def sign(self, action: str = "Approve", meaning: str = "Approved"):
		self.doc = frappe.get_doc(AUDITED, self.doc.name)
		return sign(self.doc, meaning, action, PASSWORD)

	def report(self) -> dict:
		return verify_record(AUDITED, self.doc.name)


@verifies("URS-CMP-004")
class TestEachSignatureIsCheckedAgainstTheRecord(IntegrityTestCase):
	def test_an_intact_signature_is_reported_unchanged(self):
		self.sign()

		result = self.report()

		self.assertEqual([s["content"] for s in result["signatures"]], [UNCHANGED])
		self.assertTrue(result["signatures_intact"])

	def test_a_record_changed_outside_the_system_since_signing_is_a_finding(self):
		self.sign()
		frappe.db.set_value(AUDITED, self.doc.name, "qty", 99)  # past the audit trail

		result = self.report()

		self.assertEqual(result["signatures"][0]["content"], UNACCOUNTED)
		self.assertTrue(result["signatures"][0]["finding"])
		self.assertIn("changed since it was signed", result["signatures"][0]["message"])
		self.assertFalse(result["signatures_intact"])
		self.assertEqual(result["signature_chain"], [])  # the chain itself is intact

	def test_a_change_made_through_the_system_since_signing_is_a_finding_with_its_audit_entries(self):
		self.sign()
		frappe.set_user("Administrator")
		self.doc = frappe.get_doc(AUDITED, self.doc.name)
		self.doc.qty = 7
		self.doc.save()
		frappe.set_user(SIGNER)

		result = self.report()

		self.assertEqual(result["signatures"][0]["content"], esign.CHANGED)
		self.assertTrue(result["signatures"][0]["finding"])
		self.assertIn(
			"The audit trail records the change: Updated by Administrator", result["signatures"][0]["message"]
		)
		self.assertFalse(result["signatures_intact"])

	def test_a_record_changed_and_signed_again_is_not_a_finding(self):
		self.sign("Review", "Reviewed")
		frappe.set_user("Administrator")
		self.doc = frappe.get_doc(AUDITED, self.doc.name)
		self.doc.qty = 7
		self.doc.save()
		frappe.set_user(SIGNER)
		self.sign()

		result = self.report()

		self.assertEqual([s["content"] for s in result["signatures"]], [SIGNED_AGAIN, UNCHANGED])
		self.assertEqual([s["finding"] for s in result["signatures"]], [False, False])
		self.assertTrue(result["signatures_intact"])

	def test_a_signature_moved_to_another_record_is_detected(self):
		signature = self.sign()
		frappe.set_user("Administrator")
		other = self.new_record(title="Lot L-002")
		frappe.db.set_value("Electronic Signature", signature.name, "reference_name", other.name)

		problems = signature_problems(AUDITED, other.name)

		self.assertFalse(verify_signatures(AUDITED, other.name))
		self.assertTrue(any(signature.name in problem and self.doc.name in problem for problem in problems))

	def test_a_signature_still_belongs_to_a_renamed_record(self):
		self.sign()
		frappe.set_user("Administrator")

		renamed = frappe.rename_doc(AUDITED, self.doc.name, f"{self.doc.name}-renamed")

		self.assertEqual(signature_problems(AUDITED, renamed), [])


@verifies("URS-CMP-004")
class TestSignedContentIsDeclared(IntegrationTestCase):
	def test_every_record_type_with_a_signed_act_declares_what_its_signatures_bind(self):
		for doctype in sorted({doctype for doctype, _act in SIGNING_ACTIONS}):
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, SIGNED_CONTENT)
				self.assertTrue(callable(frappe.get_attr(SIGNED_CONTENT[doctype])))

	def test_a_field_added_to_a_signed_record_type_changes_no_signature(self):
		def with_the_added_field(meta):
			return [*meta._valid_fields, ADDED_LATER]

		for doctype, act in sorted(SIGNING_ACTIONS):
			with self.subTest(doctype=doctype, act=act):
				doc = frappe.new_doc(doctype)
				before = record_fingerprint(doc, act)
				with patch.object(Meta, "get_valid_fields", with_the_added_field):
					doc.set(ADDED_LATER, "filled in by the release that added it")
					self.assertEqual(record_fingerprint(doc, act), before)


@verifies("URS-CMP-010", "URS-CMP-004")
class TestChainsAreKeyed(IntegrityTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		for qty in (2, 3):
			self.doc.qty = qty
			self.doc.save()

	def test_an_older_event_rewritten_with_the_chain_computed_again_is_detected(self):
		events = events_for(AUDITED, self.doc.name)

		rewrite_chain(
			"Audit Event", events, with_a_guessed_key(event_fingerprint), **{events[0].name: {"user": VIEWER}}
		)

		self.assertTrue(verify_events(AUDITED, self.doc.name))

	def test_an_older_signature_rewritten_with_the_chain_computed_again_is_detected(self):
		frappe.set_user(SIGNER)
		first = self.sign("Review", "Reviewed")
		self.sign()

		rewrite_chain(
			"Electronic Signature",
			stored("Electronic Signature", self.doc.name),
			with_a_guessed_key(signature_fingerprint),
			**{first.name: {"meaning": "Rejected"}},
		)

		self.assertFalse(verify_signatures(AUDITED, self.doc.name))

	def test_a_chain_rewritten_as_unkeyed_version_1_is_detected(self):
		frappe.set_user(SIGNER)
		self.sign()
		events = events_for(AUDITED, self.doc.name)
		versions = {event.name: {"fingerprint_version": FIRST_VERSION} for event in events}
		versions[events[0].name]["user"] = VIEWER

		rewrite_chain("Audit Event", events, event_fingerprint, **versions)
		signatures = stored("Electronic Signature", self.doc.name)
		rewrite_chain(
			"Electronic Signature",
			signatures,
			signature_fingerprint,
			**{s.name: {"fingerprint_version": FIRST_VERSION, "meaning": "Rejected"} for s in signatures},
		)

		problems = verify_events(AUDITED, self.doc.name)
		self.assertTrue(any("version 1" in problem for problem in problems))
		self.assertTrue(any("version 1" in problem for problem in signature_problems(AUDITED, self.doc.name)))

	def test_a_version_1_entry_after_a_keyed_one_is_detected(self):
		events = events_for(AUDITED, self.doc.name)
		newest = events[-1]

		rewrite_chain("Audit Event", events, event_fingerprint, **{newest.name: {"fingerprint_version": 1}})

		problems = verify_events(AUDITED, self.doc.name)
		self.assertTrue(any(newest.name in problem and "follows" in problem for problem in problems))

	def test_the_key_comes_from_the_sites_configuration_not_the_database(self):
		self.assertEqual(verify_events(AUDITED, self.doc.name), [])

		with patch.dict(frappe.local.conf, {"encryption_key": frappe.generate_hash(length=44)}):
			self.assertTrue(verify_events(AUDITED, self.doc.name))
		self.assertIn(KEYED_SINCE, frappe.get_site_config())

	def test_entries_recorded_before_keyed_fingerprints_still_verify(self):
		"""Nothing recorded is rewritten: version-1 entries made before the site moved to version 2
		verify as they were made, and version-2 entries continue their chains."""
		frappe.set_user("Administrator")
		with (
			patch.object(audit, "CURRENT_VERSION", FIRST_VERSION),
			patch.object(esign, "CURRENT_VERSION", FIRST_VERSION),
		):
			before = self.new_record(title="Lot L-003")
			before.qty = 5
			before.save()
			frappe.set_user(SIGNER)
			sign(frappe.get_doc(AUDITED, before.name), "Reviewed", "Review", PASSWORD)
		frappe.set_user("Administrator")
		self.assertEqual({event.fingerprint_version for event in events_for(AUDITED, before.name)}, {1})

		later = {KEYED_SINCE: str(add_days(now_datetime(), 1))}  # the site moved to version 2 after them
		with patch.dict(frappe.local.conf, later):
			self.assertEqual(verify_events(AUDITED, before.name), [])
			self.assertTrue(verify_signatures(AUDITED, before.name))
			before = frappe.get_doc(AUDITED, before.name)
			before.qty = 6
			before.save()  # version 2 from here on
			frappe.set_user(SIGNER)
			sign(frappe.get_doc(AUDITED, before.name), "Approved", "Approve", PASSWORD)
			frappe.set_user("Administrator")
			self.assertEqual(verify_events(AUDITED, before.name), [])
			self.assertTrue(verify_signatures(AUDITED, before.name))
		# Dated after the moment the site moved to version 2, the same entries read as rewritten.
		self.assertTrue(verify_events(AUDITED, before.name))


@verifies("URS-CMP-010")
class TestOneStartPerChain(IntegrityTestCase):
	def test_a_second_start_of_an_events_chain_is_detected(self):
		frappe.set_user("Administrator")
		forged = frappe.get_doc(
			{
				"doctype": "Audit Event",
				"reference_doctype": AUDITED,
				"reference_name": self.doc.name,
				"document_name_at_event": self.doc.name,
				"action": "Updated",
				"user": "Administrator",
				"event_at": now_datetime(),
				"changes": {"changed": [["qty", 1.0, 100.0]]},
				"previous_event": None,
				"fingerprint_version": fingerprint.CURRENT_VERSION,
			}
		)
		# Fingerprinted exactly as the trail would, key included: only the second start gives it away.
		forged.event_hash = event_fingerprint(forged, "")
		forged.db_insert()

		problems = verify_events(AUDITED, self.doc.name)

		self.assertEqual(len(problems), 1)
		self.assertIn(forged.name, problems[0])

	def test_a_second_entry_following_the_same_entry_is_detected(self):
		"""An entry inserted after an earlier one, fingerprinted as the trail would with the key, makes
		a fork: two entries follow one. Only the fork gives it away (mutation check, 2026-10-16)."""
		frappe.set_user("Administrator")
		self.doc.qty = 2
		self.doc.save()
		first, follower = events_for(AUDITED, self.doc.name)[:2]
		self.assertEqual(follower.previous_event, first.name)
		forged = frappe.get_doc(
			{
				"doctype": "Audit Event",
				"reference_doctype": AUDITED,
				"reference_name": self.doc.name,
				"document_name_at_event": self.doc.name,
				"action": "Updated",
				"user": "Administrator",
				"event_at": now_datetime(),
				"changes": {"changed": [["qty", 2.0, 200.0]]},
				"previous_event": first.name,
				"fingerprint_version": fingerprint.CURRENT_VERSION,
			}
		)
		forged.event_hash = event_fingerprint(forged, first.event_hash)
		forged.db_insert()

		problems = verify_events(AUDITED, self.doc.name)

		self.assertTrue(
			any(first.name in problem and "already follows" in problem for problem in problems), problems
		)

	def test_an_event_moved_from_another_record_is_detected(self):
		frappe.set_user("Administrator")
		other = self.new_record(title="Lot L-004")
		other.qty = 8
		other.save()
		moved = events_for(AUDITED, other.name)[-1]
		frappe.db.set_value("Audit Event", moved.name, "reference_name", self.doc.name, update_modified=False)

		problems = verify_events(AUDITED, self.doc.name)

		self.assertTrue(any(moved.name in problem and other.name in problem for problem in problems))
