"""The reminders' record type renamed "Due Reminder", giving Frappe back its own Reminder (rules section 4).

Tests of the two patches and of the audit trail reading a reminder's history under its former name.
A site where the reminders are still "Reminder" cannot be made inside a test (renaming a table
commits), so the rename itself is tested by its parts: it does nothing on a site where "Reminder" is
Frappe's, what names a reminder follows it, and the evidence does not.
"""

import frappe
from frappe.utils import add_days, today

from meddevice_suite.compliance.audit import event_fingerprint, verify_events
from meddevice_suite.compliance.audit_timeline import timeline_content
from meddevice_suite.lot_control.test_lot_status import receive, store
from meddevice_suite.patches.v0_1 import rename_reminder_to_due_reminder, set_notified_left_as_zero
from meddevice_suite.qa import verifies
from meddevice_suite.work_management import reminders
from meddevice_suite.work_management.reminders import DOCTYPE, ESCALATED, RAISED
from meddevice_suite.work_management.test_reminders import ITEM, ReminderTestCase, notices

FORMER = rename_reminder_to_due_reminder.OLD
FIELDS = [
	"name",
	"reference_doctype",
	"document_name_at_event",
	"action",
	"user",
	"impersonated_by",
	"event_at",
	"reason",
	"changes",
	"previous_event",
	"event_hash",
	"fingerprint_version",  # each event is fingerprinted again in the version it was made in
]


def raised(roles: tuple[str, ...] = ("Stock User",), schedule: str | None = None) -> str:
	"""A reminder of a category the reminder itself tells, or an occurrence of `schedule`."""
	tag = frappe.generate_hash(length=8)
	if schedule:
		category, reference = reminders.SCHEDULED, ("Reminder Schedule", schedule)
	else:
		category = next(name for name, kind in reminders.CATEGORIES.items() if not kind.told_by)
		reference = ("User", "Administrator")
	item = reminders.Due(*reference, add_days(today(), 5), f"Rename test {tag}", roles, part=tag)
	frappe.set_user("Administrator")
	return reminders.make(category, item, schedule=schedule).name


def events_of(doctype: str, name: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=FIELDS,
		order_by="creation asc",
	)


def recorded_under_former_name(reminder: str) -> list[str]:
	"""Make the reminder's audit events as they were recorded before the rename: under "Reminder",
	each fingerprinted with that name. The events' names, in their order."""
	previous_hash, names = "", []
	for event in events_of(DOCTYPE, reminder):
		event.reference_doctype = FORMER
		event.event_hash = event_fingerprint(event, previous_hash)
		frappe.db.set_value(
			"Audit Event",
			event.name,
			{"reference_doctype": FORMER, "event_hash": event.event_hash},
			update_modified=False,
		)
		previous_hash = event.event_hash
		names.append(event.name)
	return names


@verifies("URS-PLT-002")
class TestTheRenameRunsWhereNeededOnly(ReminderTestCase):
	def test_nothing_is_done_where_reminder_is_frappes(self):
		reminder = raised()
		count = frappe.db.count(DOCTYPE)
		for _run in range(2):
			rename_reminder_to_due_reminder.execute()
		self.assertEqual(frappe.db.get_value("DocType", FORMER, "module"), "Automation")
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "module"), "Work Management")
		self.assertTrue(frappe.db.table_exists(FORMER, cached=False))
		self.assertTrue(frappe.db.table_exists(DOCTYPE, cached=False))
		self.assertEqual(frappe.db.count(DOCTYPE), count)
		self.assertTrue(frappe.db.exists(DOCTYPE, reminder))

	def test_frappes_reminder_has_its_own_definition_and_table(self):
		"""Frappe's reminder fields are Frappe's again, and the reminders' fields are not in its table."""
		self.assertTrue(frappe.get_meta(FORMER).has_field("remind_at"))
		self.assertFalse(frappe.get_meta(FORMER).has_field("reminder_key"))
		self.assertNotIn("reminder_key", frappe.db.get_table_columns(FORMER))


@verifies("URS-PLT-002", "URS-CMP-012")
class TestWhatNamesAReminderFollowsIt(ReminderTestCase):
	def setUp(self):
		super().setUp()
		self.reminder = raised()
		doc = frappe.get_doc(DOCTYPE, self.reminder)
		self.comment = doc.add_comment("Comment", "Checked with stores").name
		self.foreign = doc.add_comment("Comment", "On a reminder of Frappe's").name
		self.error = (
			frappe.get_doc(
				{
					"doctype": "Error Log",
					"method": "rename test",
					"error": "test",
					"reference_doctype": DOCTYPE,
					"reference_name": self.reminder,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.lot = receive(ITEM, 5, store("Stores"))
		# As they were before the rename: each naming the record type "Reminder".
		frappe.db.set_value("Comment", self.comment, "reference_doctype", FORMER, update_modified=False)
		frappe.db.set_value(
			"Comment",
			self.foreign,
			{"reference_doctype": FORMER, "reference_name": "a-reminder-of-frappes"},
			update_modified=False,
		)
		frappe.db.set_value("Error Log", self.error, "reference_doctype", FORMER, update_modified=False)
		frappe.db.set_value(
			"Batch",
			self.lot,
			{"reference_doctype": FORMER, "reference_name": self.reminder},
			update_modified=False,
		)
		self.evidence = recorded_under_former_name(self.reminder)
		rename_reminder_to_due_reminder.move_record_references(FORMER, DOCTYPE)

	def test_records_naming_a_reminder_name_it_by_its_new_record_type(self):
		self.assertEqual(frappe.db.get_value("Comment", self.comment, "reference_doctype"), DOCTYPE)
		self.assertEqual(frappe.db.get_value("Error Log", self.error, "reference_doctype"), DOCTYPE)
		self.assertEqual(frappe.db.get_value("Batch", self.lot, "reference_doctype"), DOCTYPE)

	def test_what_names_a_record_of_frappes_reminder_is_left_alone(self):
		self.assertEqual(frappe.db.get_value("Comment", self.foreign, "reference_doctype"), FORMER)

	def test_a_regulated_record_changed_so_records_why(self):
		event = events_of("Batch", self.lot)[-1]
		self.assertEqual(event.reason, rename_reminder_to_due_reminder.REASON)
		self.assertEqual(
			frappe.parse_json(event.changes), {"changed": [["reference_doctype", FORMER, DOCTYPE]]}
		)
		self.assertEqual(verify_events("Batch", self.lot), [])

	def test_the_audit_trail_keeps_the_name_it_was_recorded_under(self):
		self.assertEqual([event.name for event in events_of(FORMER, self.reminder)], self.evidence)
		self.assertEqual(verify_events(DOCTYPE, self.reminder), [])


@verifies("URS-CMP-012")
class TestHistoryUnderTheFormerName(ReminderTestCase):
	def test_the_history_and_its_chain_go_on_from_the_former_name(self):
		reminder = raised()
		before = recorded_under_former_name(reminder)
		self.assertTrue(before)
		reminders.complete(reminder, "Done in the rename test")
		after = events_of(DOCTYPE, reminder)
		self.assertTrue(after)
		self.assertEqual(after[0].previous_event, before[-1])  # one chain across the rename
		self.assertEqual(verify_events(DOCTYPE, reminder), [])
		self.assertEqual(len(timeline_content(DOCTYPE, reminder)), len(before) + len(after))

	def test_an_altered_entry_under_the_former_name_is_still_found(self):
		reminder = raised()
		first = recorded_under_former_name(reminder)[0]
		frappe.db.set_value("Audit Event", first, "reason", "rewritten", update_modified=False)
		self.assertTrue(verify_events(DOCTYPE, reminder))


@verifies("URS-PLT-020")
class TestNotifiedLeftAsZero(ReminderTestCase):
	def schedule(self) -> str:
		frappe.set_user(self.qa)
		name = (
			frappe.get_doc(
				{
					"doctype": "Reminder Schedule",
					"title": f"Rename test {frappe.generate_hash(length=10)}",
					"description": "Check the traps",
					"responsible_role": "Stock User",
					"frequency": "Monthly",
					"first_due_date": add_days(today(), 3),
					"lead_days": 5,
				}
			)
			.insert()
			.name
		)
		frappe.set_user("Administrator")
		return name

	def setUp(self):
		super().setUp()
		self.open, self.escalated, self.completed, self.told = raised(), raised(), raised(), raised()
		self.occurrence = raised(schedule=self.schedule())
		frappe.db.set_value(DOCTYPE, self.escalated, "status", ESCALATED, update_modified=False)
		reminders.complete(self.completed, "Done in the rename test")
		reminders.update(self.told, notified=RAISED)
		for name in (self.open, self.escalated, self.completed, self.occurrence):  # as a migrate left them
			frappe.db.set_value(DOCTYPE, name, "notified", "0", update_modified=False)

	def notified(self, name: str) -> str | None:
		return frappe.db.get_value(DOCTYPE, name, "notified")

	def test_set_as_when_reminders_first_kept_it(self):
		set_notified_left_as_zero.execute()
		self.assertFalse(self.notified(self.open))  # told on the next run
		self.assertEqual(self.notified(self.escalated), ESCALATED)
		self.assertEqual(self.notified(self.occurrence), RAISED)
		self.assertFalse(self.notified(self.completed))
		self.assertEqual(self.notified(self.told), RAISED)  # a value it kept is left alone

	def test_each_change_is_audited_with_its_reason_and_a_second_run_changes_nothing(self):
		set_notified_left_as_zero.execute()
		event = events_of(DOCTYPE, self.escalated)[-1]
		self.assertEqual(event.reason, set_notified_left_as_zero.REASON)
		self.assertEqual(frappe.parse_json(event.changes), {"changed": [["notified", "0", ESCALATED]]})
		count = frappe.db.count("Audit Event", {"reference_doctype": DOCTYPE})
		set_notified_left_as_zero.execute()
		self.assertEqual(frappe.db.count("Audit Event", {"reference_doctype": DOCTYPE}), count)

	def test_its_people_are_then_told_once(self):
		set_notified_left_as_zero.execute()
		reminders.send_notices()
		reminders.send_notices()
		self.assertEqual(len(notices(self.open, self.stores_user)), 1)
		self.assertEqual(self.notified(self.open), RAISED)
