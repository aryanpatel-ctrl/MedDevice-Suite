"""Anchors of the audit trail and the signatures (URS-CMP-040): tests derived from the requirement's clauses.

Found in the foundation review of 2026-10-12: deleting a record's newest audit events or signatures
at database level left a shorter chain that still verified.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.client import insert as insert_from_request
from frappe.utils import CallbackManager, now_datetime

from meddevice_suite.compliance import audit_anchor
from meddevice_suite.compliance.audit import verify_events
from meddevice_suite.compliance.audit_anchor import (
	DOCTYPE,
	anchor_now,
	anchor_trail,
	newest_anchor,
	verify_audit_trail,
	verify_trail,
)
from meddevice_suite.compliance.audit_timeline import verify_record
from meddevice_suite.compliance.esign import sign, verify_signatures
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase, events_for
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, ensure_user, reset_lockout
from meddevice_suite.qa import verifies
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile


class AnchorTestCase(AuditTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")
		# The anchor file of each test is its own, never the site's.
		folder = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
		self.file = folder / audit_anchor.FILE_NAME
		self.enterContext(
			patch("meddevice_suite.compliance.audit_anchor.anchor_file", return_value=self.file)
		)
		# Each test's anchors, entries and deletions are undone, so the next test starts from the same trail.
		frappe.db.savepoint("audit_anchor_test")
		self.addCleanup(frappe.db.rollback, save_point="audit_anchor_test")
		# What the test's transaction does once committed, kept apart from the rest of the run's.
		self.on_commit = CallbackManager()
		self.enterContext(patch.object(frappe.local.db, "after_commit", self.on_commit))

	def anchor(self) -> list:
		"""Anchor as the hourly job does, up to its commit, which writes the file."""
		anchors = anchor_trail()
		self.on_commit.run()
		return anchors

	def history(self, changes: int = 3):
		"""A record with a creation event and `changes` more."""
		doc = self.new_record()
		for qty in range(2, 2 + changes):
			doc.qty = qty
			doc.save()
		return doc

	def lines(self) -> list[dict]:
		return [json.loads(line) for line in self.file.read_text().splitlines()]


@verifies("URS-CMP-040")
class TestRemovedEntriesAreDetected(AnchorTestCase):
	def test_an_intact_trail_verifies(self):
		self.history()
		self.anchor()

		self.assertEqual(verify_trail(), [])

	def test_removing_a_records_newest_events_is_detected(self):
		doc = self.history()
		self.anchor()
		events = events_for(AUDITED, doc.name)
		anchored = newest_anchor("Audit Event")
		self.assertEqual(anchored.head_entry, events[-1].name)

		frappe.db.delete("Audit Event", {"name": ["in", [events[-1].name, events[-2].name]]})

		self.assertEqual(verify_events(AUDITED, doc.name), [])  # the record's chain alone cannot tell
		problems = verify_trail()
		self.assertTrue(any(events[-1].name in problem and "missing" in problem for problem in problems))
		self.assertIn(
			f"Audit Event: 2 of the {anchored.entry_count} entries recorded up to anchor {anchored.name} "
			"are missing.",
			problems,
		)

	def test_removing_a_records_whole_history_is_detected(self):
		removed = self.history(changes=0)
		self.history()
		self.anchor()

		frappe.db.delete("Audit Event", {"reference_doctype": AUDITED, "reference_name": removed.name})

		self.assertEqual(verify_events(AUDITED, removed.name), [])
		self.assertTrue(any("1 of the" in problem for problem in verify_trail()))

	def test_an_anchor_taken_after_the_removal_still_reports_it(self):
		doc = self.history()
		self.anchor()
		events = events_for(AUDITED, doc.name)
		frappe.db.delete("Audit Event", {"name": ["in", [events[-1].name, events[-2].name]]})

		self.history()  # the trail moves on
		self.anchor()

		newest = newest_anchor("Audit Event")
		self.assertNotEqual(newest.head_entry, events[-1].name)
		self.assertTrue(any(f"2 of the {newest.entry_count}" in problem for problem in verify_trail()))

	def test_removing_the_newest_signatures_is_detected(self):
		reset_lockout(SIGNER)
		self.addCleanup(reset_lockout, SIGNER)
		frappe.set_user(SIGNER)
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Release batch B-002"}).insert()
		sign(todo, "Reviewed", "Review", PASSWORD)
		newest = sign(todo, "Approved", "Approval", PASSWORD)
		frappe.set_user("Administrator")
		self.anchor()

		frappe.db.delete("Electronic Signature", newest.name)

		self.assertTrue(verify_signatures("ToDo", todo.name))  # the record's chain alone cannot tell
		problems = verify_trail()
		self.assertTrue(
			any(problem.startswith("Electronic Signature") and newest.name in problem for problem in problems)
		)

	def test_reviewers_are_told_on_the_record_they_verify(self):
		doc = self.history()
		self.anchor()
		self.assertEqual(verify_record(AUDITED, doc.name)["anchors"], [])
		events = events_for(AUDITED, doc.name)

		frappe.db.delete("Audit Event", events[-1].name)

		result = verify_record(AUDITED, doc.name)
		self.assertEqual(result["audit_trail"], [])
		self.assertTrue(result["anchors"])


@verifies("URS-CMP-040")
class TestAnchorsAreKeptOutsideTheDatabase(AnchorTestCase):
	def test_each_anchor_is_written_to_the_file_once_it_is_committed(self):
		self.history()

		anchors = anchor_trail()
		self.assertFalse(self.file.exists())  # an anchor rolled back never reaches the file
		self.on_commit.run()

		filed = {line["anchor"]: line for line in self.lines()}
		for anchor in anchors:
			with self.subTest(anchor=anchor.name):
				self.assertEqual(filed[anchor.name]["head_entry"], anchor.head_entry)
				self.assertEqual(filed[anchor.name]["entry_count"], anchor.entry_count)
				self.assertEqual(filed[anchor.name]["anchor_hash"], anchor.anchor_hash)

	def test_the_file_is_only_ever_appended_to(self):
		self.history()
		self.anchor()
		before = self.file.read_text()

		self.history()
		self.anchor()

		after = self.file.read_text()
		self.assertTrue(after.startswith(before))
		self.assertGreater(len(after.splitlines()), len(before.splitlines()))

	def test_an_anchor_removed_from_the_database_is_reported_from_the_file(self):
		doc = self.history()
		self.anchor()
		anchored = newest_anchor("Audit Event")
		events = events_for(AUDITED, doc.name)

		frappe.db.delete("Audit Event", events[-1].name)
		frappe.db.delete(DOCTYPE, anchored.name)  # removed with the entries it counted

		problems = verify_trail()
		self.assertIn(
			f"Anchor {anchored.name} of the Audit Event is in the anchor file but no longer in the database.",
			problems,
		)
		self.assertTrue(any(events[-1].name in problem and "missing" in problem for problem in problems))

	def test_an_anchor_changed_in_the_database_is_reported(self):
		self.history()
		self.anchor()
		anchored = newest_anchor("Audit Event")

		frappe.db.set_value(DOCTYPE, anchored.name, "entry_count", anchored.entry_count - 1)

		problems = verify_trail()
		self.assertIn(f"Anchor {anchored.name}: content does not match its fingerprint.", problems)
		self.assertIn(f"Anchor {anchored.name} differs from its copy in the anchor file.", problems)

	def test_a_missing_anchor_file_is_reported(self):
		self.history()
		self.anchor()

		self.file.unlink()

		self.assertIn(f"The anchor file {audit_anchor.FILE_NAME} is missing.", verify_trail())

	def test_a_damaged_line_is_reported_and_the_next_anchor_still_reads(self):
		self.history()
		self.anchor()
		with self.file.open("a") as file:
			file.write('{"anchor": "cut sho')  # a line a full disk cut short

		self.history()
		self.anchor()

		self.assertIn(f"The anchor file {audit_anchor.FILE_NAME} has a damaged line.", verify_trail())
		newest = json.loads(self.file.read_text().splitlines()[-1])
		self.assertEqual(newest["anchor"], newest_anchor(newest["ledger"]).name)


@verifies("URS-CMP-040")
class TestAnchoring(AnchorTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def test_the_trails_are_anchored_every_hour(self):
		self.assertIn(
			"meddevice_suite.compliance.audit_anchor.anchor_trail",
			frappe.get_hooks("scheduler_events")["hourly"],
		)

	def test_each_anchor_follows_the_one_before(self):
		self.history()
		self.anchor()
		first = newest_anchor("Audit Event")
		self.history()
		self.anchor()

		second = newest_anchor("Audit Event")
		self.assertEqual(second.previous_anchor, first.name)
		self.assertGreater(second.entry_count, first.entry_count)

	def test_a_trail_with_nothing_new_is_not_anchored_again(self):
		self.history()
		self.anchor()
		count = frappe.db.count(DOCTYPE)

		self.assertEqual(self.anchor(), [])
		self.assertEqual(frappe.db.count(DOCTYPE), count)

	def test_a_reviewer_anchors_and_verifies_on_request(self):
		self.history()
		frappe.set_user(user_with_profile("QA Manager"))

		anchored = {row["ledger"]: row for row in anchor_now()}
		self.on_commit.run()

		self.assertEqual(anchored["Audit Event"]["anchor"], newest_anchor("Audit Event").name)
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, anchored["Audit Event"]["anchor"], "trigger"), "On request"
		)
		self.assertEqual(verify_audit_trail(), [])

	def test_other_jobs_cannot_anchor_or_verify(self):
		frappe.set_user(user_with_profile("Sales"))

		with self.assertRaises(frappe.PermissionError):
			anchor_now()
		with self.assertRaises(frappe.PermissionError):
			verify_audit_trail()


@verifies("URS-CMP-040")
class TestAnchorsArePermanent(AnchorTestCase):
	def forged(self):
		return frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"ledger": "Audit Event",
				"head_entry": "forged",
				"head_fingerprint": "0" * 64,
				"head_created": now_datetime(),
				"entry_count": 1,
				"anchored_at": now_datetime(),
				"trigger": "Scheduled",
				"anchor_hash": frappe.generate_hash(length=64),
			}
		)

	def test_an_anchor_inserted_past_the_service_is_refused(self):
		forged = self.forged()
		forged.flags.ignore_validate = True
		with self.assertRaises(frappe.PermissionError):
			forged.insert(ignore_permissions=True, ignore_mandatory=True)

	def test_a_request_cannot_mark_its_anchor_as_recorded(self):
		forged = self.forged().as_dict()
		forged["flags"] = {"recorded_by_audit_anchor": 1}
		with self.assertRaises(frappe.PermissionError):
			insert_from_request(forged)

	def test_an_anchor_cannot_be_changed_or_deleted_even_by_administrator(self):
		self.history()
		self.anchor()
		anchor = frappe.get_doc(DOCTYPE, newest_anchor("Audit Event").name)

		anchor.entry_count += 1
		with self.assertRaises(frappe.PermissionError):
			anchor.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(DOCTYPE, anchor.name, ignore_permissions=True, force=True)
		self.assertTrue(frappe.db.exists(DOCTYPE, anchor.name))
