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
from frappe.email.doctype.notification.notification import Notification
from frappe.tests import IntegrationTestCase
from frappe.utils import CallbackManager, get_files_path, now_datetime

from meddevice_suite.compliance import audit_anchor
from meddevice_suite.compliance.alerts import apply_alerts
from meddevice_suite.compliance.audit import verify_events
from meddevice_suite.compliance.audit_anchor import (
	DOCTYPE,
	ENTRIES_MISSING,
	ENTRY_MISSING,
	FINDING,
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
		# The site's own anchors (taken on it before, from its screens or hourly) start the test's
		# file, as the site's file holds them: the test begins from an intact trail on any site.
		held = frappe.get_all(audit_anchor.DOCTYPE, pluck="name", order_by="creation asc, name asc")
		if held:
			self.file.write_text(
				"".join(
					audit_anchor.as_line(frappe.get_doc(audit_anchor.DOCTYPE, name)) + "\n" for name in held
				)
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
		# An anchor rolled back never reaches the file.
		filed_before = {line["anchor"] for line in self.lines()} if self.file.exists() else set()
		self.assertFalse({anchor.name for anchor in anchors} & filed_before)
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
		self.history()
		self.anchor()  # an older anchor, left in the database
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


@verifies("URS-CMP-042")
class TestProblemsAreAlerted(AnchorTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		apply_alerts()
		cls.qa = user_with_profile("QA Manager")

	def truncated(self, newest: int = 2):
		"""A record whose newest events were removed after the trail was anchored."""
		doc = self.history()
		self.anchor()
		events = events_for(AUDITED, doc.name)
		frappe.db.delete("Audit Event", {"name": ["in", [event.name for event in events[-newest:]]]})
		return doc, events

	def findings(self) -> list[frappe._dict]:
		return frappe.get_all(FINDING, fields=["name", "trail", "kind", "subject"], order_by="creation asc")

	def alerts_to(self, user: str) -> list[frappe._dict]:
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": user, "document_type": FINDING},
			fields=["subject", "description", "document_name"],
		)

	def test_a_removal_alerts_qa_and_the_system_administrator_with_what_to_do(self):
		_doc, events = self.truncated()

		self.anchor()

		found = {(finding.kind, finding.subject) for finding in self.findings()}
		self.assertIn((ENTRY_MISSING, events[-1].name), found)
		self.assertIn((ENTRIES_MISSING, "2"), found)
		for user in (self.qa, SIGNER):  # Quality Manager; System Manager
			with self.subTest(user=user):
				alerts = self.alerts_to(user)
				self.assertEqual(len(alerts), len(found))
				self.assertTrue(any("Audit Event" in alert.subject for alert in alerts))
				self.assertTrue(any(events[-1].name in alert.description for alert in alerts))
				self.assertTrue(any("2 of the" in alert.description for alert in alerts))
				self.assertTrue(all("data-integrity incident" in alert.description for alert in alerts))

	def test_the_same_finding_is_not_alerted_again(self):
		self.truncated()
		self.anchor()
		alerted, found = len(self.alerts_to(self.qa)), len(self.findings())
		self.assertTrue(found)

		failed = frappe.db.count(
			"Error Log", {"method": ["like", "Audit trail problem could not be alerted%"]}
		)
		self.anchor()  # the next hour: the removal is still there, nothing else has changed
		self.anchor()

		self.assertEqual(len(self.alerts_to(self.qa)), alerted)
		self.assertEqual(len(self.findings()), found)
		# Known already, it is not even tried again (and refused as a duplicate, every hour).
		self.assertEqual(
			frappe.db.count("Error Log", {"method": ["like", "Audit trail problem could not be alerted%"]}),
			failed,
		)

	def test_a_further_removal_is_a_new_finding_and_alerted(self):
		_doc, events = self.truncated(newest=1)
		self.anchor()
		alerted = len(self.alerts_to(self.qa))

		frappe.db.delete("Audit Event", events[-2].name)
		self.anchor()

		self.assertIn(
			(ENTRIES_MISSING, "2"), {(finding.kind, finding.subject) for finding in self.findings()}
		)
		self.assertGreater(len(self.alerts_to(self.qa)), alerted)

	def test_a_failed_alert_does_not_stop_the_anchor_and_is_sent_at_the_next_check(self):
		self.truncated()

		with patch.object(Notification, "send", side_effect=frappe.ValidationError("mail server down")):
			anchors = self.anchor()

		self.assertTrue(anchors)  # the trail was anchored all the same
		self.assertEqual(self.findings(), [])
		self.assertTrue(
			frappe.db.exists(
				"Error Log", {"method": f"Audit trail problem could not be alerted: {ENTRY_MISSING}"}
			)
		)
		self.anchor()
		self.assertTrue(self.findings())
		self.assertTrue(self.alerts_to(self.qa))

	def test_a_reviewers_check_alerts_too(self):
		doc, _events = self.truncated()
		frappe.set_user(SIGNER)  # a reviewer who may open the test record
		verify_record(AUDITED, doc.name)
		self.assertTrue(self.findings())
		frappe.set_user(self.qa)
		verify_audit_trail()  # finds the same again: alerted once

		frappe.set_user("Administrator")
		self.assertTrue(self.findings())
		self.assertEqual(len(self.alerts_to(self.qa)), len(self.findings()))

	def test_an_intact_trail_alerts_nobody(self):
		self.history()
		self.anchor()
		self.anchor()

		self.assertEqual(self.findings(), [])

	def test_a_finding_cannot_be_made_changed_or_deleted_by_hand(self):
		forged = frappe.get_doc(
			{
				"doctype": FINDING,
				"trail": "Audit Event",
				"kind": ENTRIES_MISSING,
				"finding": "forged",
				"what_to_do": "nothing",
				"finding_key": frappe.generate_hash(length=64),
				"detected_at": now_datetime(),
			}
		)
		with self.assertRaises(frappe.PermissionError):
			forged.insert(ignore_permissions=True)
		self.truncated()
		self.anchor()
		finding = frappe.get_doc(FINDING, self.findings()[0].name)

		finding.what_to_do = "ignore it"
		with self.assertRaises(frappe.PermissionError):
			finding.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc(FINDING, finding.name, ignore_permissions=True, force=True)


@verifies("URS-CMP-040")
class TestTheAnchorFileIsNeverServed(IntegrationTestCase):
	"""Found in the independent review of 2026-10-15: kept in `private/files`, the anchor file could be
	read by any desk user who made a File record naming it, as Frappe serves a private file to whoever
	may read a File record that names it."""

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def test_the_anchor_file_is_kept_outside_every_folder_frappe_serves(self):
		path = audit_anchor.anchor_file().resolve()
		self.assertTrue(path.is_relative_to(Path(frappe.get_site_path("private")).resolve()))
		for served in (
			get_files_path(is_private=1),  # private files, served through File records
			get_files_path(),  # public files
			frappe.get_site_path("private", "backups"),  # served to System Managers at /backups
		):
			with self.subTest(served=served):
				self.assertFalse(path.is_relative_to(Path(served).resolve()))

	def test_no_file_record_may_name_the_anchor_file_where_it_was_kept(self):
		former = audit_anchor.former_anchor_file()
		if not former.exists():  # as on a site not yet migrated, with the anchors still there
			former.parent.mkdir(parents=True, exist_ok=True)
			former.write_text(f'{{"test": "{frappe.generate_hash(length=12)}"}}\n')
			self.addCleanup(former.unlink, missing_ok=True)
		frappe.set_user(user_with_profile("Stores"))  # any desk user
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": "File", "file_url": f"/private/files/{audit_anchor.FILE_NAME}", "is_private": 1}
			).insert()
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("File", {"file_url": f"/private/files/{audit_anchor.FILE_NAME}"}))
		# Refused before Frappe reads it: no copy of it under another name either.
		self.assertEqual(list(former.parent.glob("audit-anchors?*.jsonl")), [])

	def test_no_file_record_may_name_the_anchor_file_where_it_is_kept(self):
		address = "/" + "/".join((*audit_anchor.FOLDER, audit_anchor.FILE_NAME))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "File", "file_url": address, "is_private": 1}).insert()
		self.assertFalse(frappe.db.exists("File", {"file_url": address}))

	def test_an_existing_file_record_cannot_be_pointed_at_the_anchor_file(self):
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"note-{frappe.generate_hash(length=8)}.txt",
				"is_private": 1,
				"content": "x",
			}
		).insert()
		doc.file_url = f"/private/files/{audit_anchor.FILE_NAME}"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_every_spelling_of_the_anchor_files_address_is_recognised(self):
		for address in (
			f"/private/files/{audit_anchor.FILE_NAME}",
			"/private/files/audit%2Danchors.jsonl",
			"/private/files/Audit-Anchors.JSONL",
			f"/private/files/{audit_anchor.FILE_NAME}?fid=1",
			f"/private/audit/{audit_anchor.FILE_NAME}",
			audit_anchor.FILE_NAME,
		):
			with self.subTest(address=address):
				self.assertTrue(audit_anchor.names_anchor_file(address))
		for address in (None, "", "/private/files/audit-report.pdf", "/files/anchors.jsonl"):
			with self.subTest(address=address):
				self.assertFalse(audit_anchor.names_anchor_file(address))

	def test_any_file_in_the_anchor_folder_is_recognised(self):
		"""The anchor folder holds only what the system keeps: a file of any name there is refused, so
		a rotated or renamed anchor file is not served either (mutation check, 2026-10-16)."""
		folder = "/" + "/".join(audit_anchor.FOLDER)
		for address in (f"{folder}/anchors-2026.jsonl", f"{folder}/notes.txt", f"{folder.upper()}/x"):
			with self.subTest(address=address):
				self.assertTrue(audit_anchor.names_anchor_file(address))

	def test_a_file_record_named_as_the_anchor_file_is_refused_whatever_its_address(self):
		"""A File is refused on its name too: Frappe makes a stored file's address from its name, so a
		record named as the anchor file must not be stored (mutation check, 2026-10-16)."""
		frappe.set_user(user_with_profile("Stores"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": audit_anchor.FILE_NAME,
					"is_private": 1,
					"content": "x",
				}
			).insert()
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("File", {"file_name": audit_anchor.FILE_NAME}))


@verifies("URS-CMP-040")
class TestTheAnchorFileIsMovedOutOfTheServedFolder(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		folder = Path(tempfile.mkdtemp())
		self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
		self.former, self.current = (
			folder / "files" / audit_anchor.FILE_NAME,
			folder / "audit" / audit_anchor.FILE_NAME,
		)
		self.former.parent.mkdir()
		self.enterContext(patch.object(audit_anchor, "former_anchor_file", return_value=self.former))
		self.enterContext(patch.object(audit_anchor, "anchor_file", return_value=self.current))

	def migrate(self) -> None:
		from meddevice_suite.patches.v0_1 import move_audit_anchor_file

		move_audit_anchor_file.execute()

	def test_the_anchors_are_moved_and_the_former_file_removed(self):
		self.former.write_text('{"anchor": 1}\n{"anchor": 2}\n')

		self.migrate()

		self.assertFalse(self.former.exists())
		self.assertEqual(self.current.read_text(), '{"anchor": 1}\n{"anchor": 2}\n')

	def test_anchors_written_at_the_new_place_before_the_migrate_follow_the_moved_ones(self):
		self.former.write_text('{"anchor": 1}')  # its last line cut short
		self.current.parent.mkdir()
		self.current.write_text('{"anchor": 2}\n')

		self.migrate()

		self.assertEqual(self.current.read_text(), '{"anchor": 1}\n{"anchor": 2}\n')

	def test_running_it_again_or_after_a_move_cut_short_copies_nothing_twice(self):
		self.current.parent.mkdir()
		self.current.write_text('{"anchor": 1}\n{"anchor": 2}\n')
		self.former.write_text('{"anchor": 1}\n')  # moved, but not yet removed

		self.migrate()
		self.migrate()

		self.assertFalse(self.former.exists())
		self.assertEqual(self.current.read_text(), '{"anchor": 1}\n{"anchor": 2}\n')

	def test_a_site_without_a_former_file_is_left_as_it_is(self):
		self.migrate()

		self.assertFalse(self.current.exists())
