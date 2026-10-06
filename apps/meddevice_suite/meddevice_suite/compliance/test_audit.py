import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.doctype.test_doctype import new_doctype
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import now_datetime, time_diff_in_seconds

from meddevice_suite.compliance.audit import event_fingerprint, verify_events
from meddevice_suite.compliance.audit_scope import REGULATED_DOCTYPES, SELF_RECORDING, is_regulated
from meddevice_suite.compliance.doctype.audit_event.audit_event import on_doctype_update
from meddevice_suite.compliance.esign import sign, verify_signatures
from meddevice_suite.compliance.test_esign import PASSWORD, SIGNER, VIEWER, ensure_user, reset_lockout
from meddevice_suite.qa import verifies

# A submittable, renamable record type that exists only in the test database.
AUDITED = "Test MDS Audited Record"


def ensure_audited_doctype() -> None:
	if frappe.db.exists("DocType", AUDITED):
		return
	new_doctype(
		AUDITED,
		is_submittable=1,
		allow_rename=1,
		fields=[
			{"fieldname": "title", "label": "Title", "fieldtype": "Data"},
			{"fieldname": "qty", "label": "Quantity", "fieldtype": "Float"},
			{"fieldname": "remarks", "label": "Remarks", "fieldtype": "Small Text", "allow_on_submit": 1},
		],
		permissions=[
			{
				"role": "System Manager",
				**dict.fromkeys(("read", "write", "create", "delete", "submit", "cancel", "amend"), 1),
			}
		],
	).insert()
	frappe.db.commit()  # creating a table ends the transaction anyway


def events_for(doctype: str, name: str) -> list[frappe._dict]:
	events = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["*"],
		order_by="event_at asc, creation asc",
	)
	for event in events:
		event.changes = json.loads(event.changes) if event.changes else {}
	return events


def actions_for(doc) -> list[str]:
	return [event.action for event in events_for(doc.doctype, doc.name)]


class AuditTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_audited_doctype()

	def setUp(self):
		frappe.set_user("Administrator")
		self.enterContext(
			patch("meddevice_suite.compliance.audit_scope.REGULATED_DOCTYPES", REGULATED_DOCTYPES | {AUDITED})
		)

	def new_record(self, **values):
		return frappe.get_doc({"doctype": AUDITED, "title": "Lot L-001", "qty": 1, **values}).insert()


@verifies("URS-CMP-007")
class TestEveryActionIsRecorded(AuditTestCase):
	def test_create_change_submit_update_after_submit_and_cancel(self):
		doc = self.new_record()
		doc.qty = 5
		doc.save()
		doc.submit()
		doc.remarks = "Label reprinted"
		doc.save()
		doc.cancel()

		self.assertEqual(
			actions_for(doc), ["Created", "Updated", "Submitted", "Updated after submission", "Cancelled"]
		)

	def test_amendment(self):
		doc = self.new_record()
		doc.submit()
		doc.cancel()

		amended = frappe.copy_doc(doc)
		amended.docstatus = 0  # as the Amend button does
		amended.amended_from = doc.name
		amended.insert()

		self.assertEqual(actions_for(amended), ["Amended"])
		self.assertEqual(events_for(AUDITED, amended.name)[0].changes["snapshot"]["amended_from"], doc.name)

	def test_rename(self):
		doc = self.new_record()
		old_name = doc.name

		new_name = frappe.rename_doc(AUDITED, old_name, f"{old_name}-renamed")

		events = events_for(AUDITED, new_name)
		self.assertEqual([event.action for event in events], ["Created", "Renamed"])
		self.assertEqual(events[-1].changes, {"old_name": old_name, "new_name": new_name, "merged": False})

	def test_deletion(self):
		doc = self.new_record()

		frappe.delete_doc(AUDITED, doc.name)

		self.assertEqual(actions_for(doc), ["Created", "Deleted"])

	def test_a_record_created_already_submitted_is_recorded_as_submitted(self):
		doc = self.new_record(docstatus=1)

		self.assertEqual(actions_for(doc), ["Submitted"])

	def test_saving_without_a_change_records_nothing(self):
		doc = self.new_record()
		doc.save()

		self.assertEqual(actions_for(doc), ["Created"])

	def test_record_types_that_are_not_regulated_are_not_audited(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Not a GMP record"}).insert()

		self.assertEqual(actions_for(todo), [])


@verifies("URS-CMP-008")
class TestEventContent(AuditTestCase):
	def test_records_who_when_and_old_and_new_values(self):
		doc = self.new_record(qty=1)
		doc.qty = 5
		doc.save()

		event = events_for(AUDITED, doc.name)[-1]
		self.assertEqual(event.user, "Administrator")
		self.assertLess(abs(time_diff_in_seconds(now_datetime(), event.event_at)), 60)
		self.assertIn(["qty", 1.0, 5.0], event.changes["changed"])

	def test_creation_and_deletion_keep_the_whole_record(self):
		doc = self.new_record(title="Lot L-777", qty=3)
		frappe.delete_doc(AUDITED, doc.name)

		created, deleted = events_for(AUDITED, doc.name)
		for event in (created, deleted):
			with self.subTest(action=event.action):
				self.assertEqual(event.changes["snapshot"]["title"], "Lot L-777")
				self.assertEqual(event.changes["snapshot"]["qty"], 3.0)

	def test_child_table_rows_added_changed_and_removed_are_recorded(self):
		email = f"audit.rows.{frappe.generate_hash(length=6)}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Rows", "send_welcome_email": 0}
		).insert()

		user.append("roles", {"role": "Stock User"})
		user.save()
		row = user.roles[0]
		row.role = "Purchase User"
		user.save()
		user.set("roles", [])
		user.save()

		changes = [event.changes for event in events_for("User", email)]
		added = [row for entry in changes for table, row in entry.get("added", []) if table == "roles"]
		row_changed = [
			diff for entry in changes for table, _, diff in entry.get("row_changed", []) if table == "roles"
		]
		removed = [row for entry in changes for table, row in entry.get("removed", []) if table == "roles"]
		self.assertEqual([row["role"] for row in added], ["Stock User"])
		self.assertEqual(row_changed, [[["role", "Stock User", "Purchase User"]]])
		self.assertEqual([row["role"] for row in removed], ["Purchase User"])

	def test_the_real_user_is_recorded_when_acting_for_someone_else(self):
		ensure_user(VIEWER, "Viewer", [])
		with patch.dict(frappe.session.data, {"impersonated_by": VIEWER}):
			doc = self.new_record()

		self.assertEqual(events_for(AUDITED, doc.name)[0].impersonated_by, VIEWER)


@verifies("URS-CMP-009")
class TestEventsAreImmutable(AuditTestCase):
	def setUp(self):
		super().setUp()
		self.event_name = events_for(AUDITED, self.new_record().name)[0].name

	def test_cannot_be_changed_even_by_administrator(self):
		event = frappe.get_doc("Audit Event", self.event_name)
		event.reason = "edited later"

		with self.assertRaises(frappe.PermissionError):
			event.save(ignore_permissions=True)

	def test_cannot_be_deleted_even_by_administrator(self):
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Audit Event", self.event_name, ignore_permissions=True, force=True)

		self.assertTrue(frappe.db.exists("Audit Event", self.event_name))

	def test_no_role_may_create_or_change_events(self):
		meta = frappe.get_meta("Audit Event")

		self.assertTrue(meta.in_create)
		for rule in meta.permissions:
			with self.subTest(role=rule.role):
				self.assertFalse(rule.create or rule.write or rule.delete or rule.submit or rule.amend)


@verifies("URS-CMP-010")
class TestChain(AuditTestCase):
	def setUp(self):
		super().setUp()
		self.doc = self.new_record()
		for qty in (2, 3, 4):
			self.doc.qty = qty
			self.doc.save()
		self.events = events_for(AUDITED, self.doc.name)

	def test_each_event_links_to_the_one_before_and_the_chain_verifies(self):
		self.assertIsNone(self.events[0].previous_event)
		for previous, event in zip(self.events, self.events[1:], strict=False):
			self.assertEqual(event.previous_event, previous.name)
		self.assertEqual(verify_events(AUDITED, self.doc.name), [])

	def test_an_altered_event_is_detected(self):
		frappe.db.set_value("Audit Event", self.events[1].name, "user", VIEWER)

		self.assertTrue(verify_events(AUDITED, self.doc.name))

	def test_a_removed_event_is_detected(self):
		frappe.db.delete("Audit Event", self.events[1].name)

		self.assertTrue(verify_events(AUDITED, self.doc.name))

	def test_an_inserted_event_is_detected(self):
		forged = frappe.get_doc(
			{
				"doctype": "Audit Event",
				"reference_doctype": AUDITED,
				"reference_name": self.doc.name,
				"document_name_at_event": self.doc.name,
				"action": "Updated",
				"user": "Administrator",
				"event_at": now_datetime(),
				"changes": {"changed": [["qty", 4.0, 400.0]]},
				"previous_event": self.events[1].name,
			}
		)
		forged.event_hash = event_fingerprint(forged, self.events[1].event_hash)
		forged.db_insert()

		self.assertTrue(verify_events(AUDITED, self.doc.name))

	def test_renaming_the_record_keeps_its_chain_verifiable(self):
		new_name = frappe.rename_doc(AUDITED, self.doc.name, f"{self.doc.name}-renamed")

		self.assertEqual(len(events_for(AUDITED, new_name)), len(self.events) + 1)
		self.assertEqual(verify_events(AUDITED, new_name), [])


@verifies("URS-CMP-013")
class TestScope(AuditTestCase):
	def test_every_declared_record_type_exists(self):
		for doctype in sorted(REGULATED_DOCTYPES):
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype))

	def test_a_record_type_is_audited_from_the_moment_it_is_declared(self):
		with patch("meddevice_suite.compliance.audit_scope.REGULATED_DOCTYPES", REGULATED_DOCTYPES):
			doc = self.new_record()
		self.assertEqual(actions_for(doc), [])

		doc.qty = 9
		doc.save()

		self.assertEqual(actions_for(doc), ["Updated"])


@verifies("URS-CMP-013")
class TestScopeRules(UnitTestCase):
	def test_record_types_this_app_builds_are_regulated(self):
		self.assertTrue(is_regulated("Any Future Record", "Compliance"))

	def test_compliance_evidence_records_are_not_audited_as_changes(self):
		for doctype in SELF_RECORDING:
			with self.subTest(doctype=doctype):
				self.assertFalse(is_regulated(doctype, "Compliance"))

	def test_accounting_documents_are_left_to_india_compliance(self):
		for doctype in ("Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry"):
			with self.subTest(doctype=doctype):
				self.assertFalse(is_regulated(doctype, "Accounts"))


@verifies("URS-CMP-004")
class TestSignaturesSurviveRename(AuditTestCase):
	def test_signatures_still_verify_after_the_record_is_renamed(self):
		ensure_user(SIGNER, "Signer", ["System Manager"])
		doc = self.new_record()
		reset_lockout(SIGNER)
		frappe.set_user(SIGNER)
		sign(doc, meaning="Reviewed", action="Review", password=PASSWORD)
		sign(doc, meaning="Approved", action="Approval", password=PASSWORD)
		frappe.set_user("Administrator")

		new_name = frappe.rename_doc(AUDITED, doc.name, f"{doc.name}-renamed")

		self.assertTrue(verify_signatures(AUDITED, new_name))


@verifies("URS-CMP-012")
class TestReviewStaysFastAtScale(IntegrationTestCase):
	def test_record_user_and_date_searches_are_indexed(self):
		on_doctype_update()
		indexes = {}
		for row in frappe.db.sql("SHOW INDEX FROM `tabAudit Event`", as_dict=True):
			indexes.setdefault(row.Key_name, []).append(row.Column_name)

		for columns in (
			["reference_doctype", "reference_name", "event_at"],
			["user", "event_at"],
			["event_at"],
		):
			with self.subTest(columns=columns):
				self.assertIn(columns, indexes.values())
