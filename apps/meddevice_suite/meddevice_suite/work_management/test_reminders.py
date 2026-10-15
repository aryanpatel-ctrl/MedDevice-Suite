"""Reminders and next due (module 60; URS-PLT-014, URS-PLT-015, URS-PLT-020): tests derived from the
requirements' clauses."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, getdate, today

from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.lot_control.lot_status import APPROVED, HOLD, set_status
from meddevice_suite.lot_control.test_lot_status import ensure_parties, lot_item, receive, store
from meddevice_suite.master_data.test_supplier_approval import approved_for, decide, ensure_supplier
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import licensed_for_test, work_instruction_for_test
from meddevice_suite.work_management import reminders
from meddevice_suite.work_management.reminders import COMPLETED, ESCALATED, OPEN, OVERDUE, QA_ROLE, RAISED

ITEM = "MDS-REM-RAW"


def reminder_of(category: str, reference_doctype: str, reference_name: str) -> frappe._dict | None:
	rows = frappe.get_all(
		"Reminder",
		filters={
			"category": category,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		fields=[
			"name",
			"status",
			"due_date",
			"responsible_user",
			"responsible_roles",
			"completion",
			"completed_on",
		],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def notices(reminder: str, user: str) -> list[str]:
	"""What the person was told about the reminder, oldest first."""
	return frappe.get_all(
		"Notification Log",
		filters={"document_type": "Reminder", "document_name": reminder, "for_user": user},
		pluck="subject",
		order_by="creation asc",
	)


def told_of_escalation(reminder: str) -> list[str]:
	"""Who was told the reminder was escalated (the "Reminder Overdue" alert), once per notice."""
	return frappe.get_all(
		"Notification Log",
		filters={"document_type": "Reminder", "document_name": reminder, "subject": ["like", "Overdue:%"]},
		pluck="for_user",
	)


class ReminderTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()
		lot_item(ITEM)
		cls.qa = signer("QA Manager")
		cls.stores_user = signer("Stores")
		cls.purchase = signer("Purchase")
		cls.requester = signer("Production Supervisor")

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def run_reminders(self) -> None:
		frappe.set_user("Administrator")
		reminders.run()


@verifies("URS-PLT-014")
class TestEachCategoryIsTrackedUntilItsRecordShowsItDone(ReminderTestCase):
	def test_a_lot_expiring_is_tracked_until_it_is_kept_apart(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		raised = reminder_of("Lot Expiry", "Batch", lot)
		self.assertEqual((raised.status, getdate(raised.due_date)), (OPEN, getdate(add_days(today(), 10))))
		self.assertEqual(raised.responsible_roles.split("\n"), ["Stock User", "Quality Manager"])
		set_status(lot, HOLD, "Kept apart for the test")
		self.run_reminders()
		done = reminder_of("Lot Expiry", "Batch", lot)
		self.assertEqual(done.status, COMPLETED)
		self.assertIn(lot, done.completion)
		self.assertTrue(done.completed_on)

	def test_a_lot_outside_its_lead_time_is_not_raised(self):
		lot = receive(ITEM, 5, store("Stores"))  # expires a year out
		self.run_reminders()
		self.assertIsNone(reminder_of("Lot Expiry", "Batch", lot))

	def test_a_supplier_certificate_is_tracked_until_a_new_decision(self):
		supplier = f"MDS Reminded Supplier {frappe.generate_hash(length=6)}"
		ensure_supplier(supplier)
		approval = approved_for(supplier, [ITEM])
		frappe.db.set_value("Supplier Approval", approval, "certificate_expiry", add_days(today(), 10))
		self.run_reminders()
		self.assertEqual(reminder_of("Supplier Certificate", "Supplier Approval", approval).status, OPEN)
		frappe.set_user(self.qa)
		decide(supplier, [ITEM])  # QA decides again, on the renewed certificate
		self.run_reminders()
		self.assertEqual(reminder_of("Supplier Certificate", "Supplier Approval", approval).status, COMPLETED)

	def test_a_customer_licence_is_tracked_until_its_renewal_is_verified(self):
		customer = f"MDS Reminded Customer {frappe.generate_hash(length=6)}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer,
				"customer_group": "Commercial",
				"territory": "India",
			}
		).insert()
		licence = licensed_for_test(customer)
		frappe.db.set_value("Customer Licence", licence, "valid_until", add_days(today(), 10))
		self.run_reminders()
		self.assertEqual(reminder_of("Customer Licence", "Customer Licence", licence).status, OPEN)
		licensed_for_test(customer)  # the renewal, verified
		self.run_reminders()
		self.assertEqual(reminder_of("Customer Licence", "Customer Licence", licence).status, COMPLETED)

	def test_a_document_review_is_tracked_until_the_review_moves_its_date_on(self):
		document = work_instruction_for_test()
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_days(today(), 5))
		self.run_reminders()
		raised = reminder_of("Document Review", "Controlled Document", document)
		self.assertEqual((raised.status, raised.responsible_user), (OPEN, "Administrator"))
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_months(today(), 24))
		self.run_reminders()
		self.assertEqual(reminder_of("Document Review", "Controlled Document", document).status, COMPLETED)

	def test_a_change_action_is_tracked_for_its_owner_until_done(self):
		change = approved_change(self.requester, self.qa, work_instruction_for_test())
		self.run_reminders()
		raised = reminder_of("Change Action", "Change Request", change)
		self.assertEqual((raised.status, raised.responsible_user), (OPEN, self.requester))
		action = frappe.get_all("Change Request Action", filters={"parent": change}, pluck="name")[0]
		frappe.db.set_value("Change Request Action", action, "done", 1)  # as the owner records it
		self.run_reminders()
		self.assertEqual(reminder_of("Change Action", "Change Request", change).status, COMPLETED)

	def test_an_effectiveness_check_is_tracked_until_the_change_is_closed(self):
		change = approved_change(self.requester, self.qa, work_instruction_for_test())
		frappe.db.set_value(
			"Change Request", change, {"status": "Implemented", "effectiveness_due_on": today()}
		)
		self.run_reminders()
		self.assertEqual(reminder_of("Effectiveness Check", "Change Request", change).status, OPEN)
		frappe.db.set_value("Change Request", change, "status", "Closed")
		self.run_reminders()
		self.assertEqual(reminder_of("Effectiveness Check", "Change Request", change).status, COMPLETED)

	def test_raised_once_while_it_stays_due(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		self.run_reminders()
		self.assertEqual(frappe.db.count("Reminder", {"reference_name": lot, "category": "Lot Expiry"}), 1)


@verifies("URS-PLT-014")
class TestOverdueAndEscalation(ReminderTestCase):
	def test_past_its_due_date_it_is_overdue_and_after_the_days_qa_set_escalated_once(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), -2))
		with use_setting("reminder_escalation_days", 3):
			self.run_reminders()
			self.assertEqual(reminder_of("Lot Expiry", "Batch", lot).status, OVERDUE)
			frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), -3))
			self.run_reminders()  # a new due date: a new reminder, the old one completed
			escalated = reminder_of("Lot Expiry", "Batch", lot)
			self.assertEqual(escalated.status, ESCALATED)
			alerted = told_of_escalation(escalated.name)
			self.assertIn(self.qa, alerted)
			self.assertNotIn(self.stores_user, alerted)  # told it was raised; escalation goes to QA
			self.run_reminders()
			self.assertEqual(told_of_escalation(escalated.name).count(self.qa), 1)

	def test_one_categorys_failure_leaves_its_reminders_and_the_others_go_on(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		document = work_instruction_for_test()
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_days(today(), 5))

		def broken():
			raise RuntimeError("source unreadable")
			yield  # a generator, as the categories are

		with patch.dict(reminders.CATEGORIES, {"Lot Expiry": reminders.Category(broken)}):
			self.run_reminders()
		self.assertEqual(reminder_of("Lot Expiry", "Batch", lot).status, OPEN)  # not completed by the failure
		self.assertEqual(reminder_of("Document Review", "Controlled Document", document).status, OPEN)


@verifies("URS-PLT-014")
class TestTheRecord(ReminderTestCase):
	def test_nobody_makes_changes_or_deletes_a_reminder(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		name = reminder_of("Lot Expiry", "Batch", lot).name
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "Reminder", "subject": "By hand", "category": "X"}).insert(
				ignore_permissions=True
			)
		doc = frappe.get_doc("Reminder", name)
		doc.status = COMPLETED
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Reminder", name, ignore_permissions=True)

	def test_a_system_reminder_is_not_completed_by_hand(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "work in its record"):
			reminders.complete_occurrence(reminder_of("Lot Expiry", "Batch", lot).name, "Done")

	def test_each_job_sees_its_own_reminders_qa_sees_all(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		supplier = f"MDS Reminded Supplier {frappe.generate_hash(length=6)}"
		ensure_supplier(supplier)
		approval = approved_for(supplier, [ITEM])
		frappe.db.set_value("Supplier Approval", approval, "certificate_expiry", add_days(today(), 10))
		self.run_reminders()
		lot_reminder = reminder_of("Lot Expiry", "Batch", lot).name
		certificate_reminder = reminder_of("Supplier Certificate", "Supplier Approval", approval).name
		for user, sees, not_sees in (
			(self.stores_user, lot_reminder, certificate_reminder),
			(self.purchase, certificate_reminder, lot_reminder),
		):
			with self.subTest(user=user):
				frappe.set_user(user)
				listed = frappe.get_list("Reminder", pluck="name", limit=0)
				self.assertIn(sees, listed)
				self.assertNotIn(not_sees, listed)
				self.assertFalse(frappe.has_permission("Reminder", "read", doc=not_sees))
		frappe.set_user(self.qa)
		self.assertTrue(
			{lot_reminder, certificate_reminder} <= set(frappe.get_list("Reminder", pluck="name", limit=0))
		)


@verifies("URS-PLT-015")
class TestRecurringReminders(ReminderTestCase):
	def schedule(self, **values) -> str:
		frappe.set_user(self.qa)
		return (
			frappe.get_doc(
				{
					"doctype": "Reminder Schedule",
					"title": f"Pest control check {frappe.generate_hash(length=10)}",
					"description": "Check the traps and record the findings",
					"responsible_role": "Stock User",
					"frequency": "Monthly",
					"first_due_date": add_days(today(), 3),
					"lead_days": 5,
					**values,
				}
			)
			.insert()
			.name
		)

	def test_an_occurrence_is_raised_within_its_lead_time_and_its_job_told(self):
		name = self.schedule()
		self.run_reminders()
		raised = reminder_of("Recurring", "Reminder Schedule", name)
		self.assertEqual((raised.status, getdate(raised.due_date)), (OPEN, getdate(add_days(today(), 3))))
		self.assertIn(
			self.stores_user,
			frappe.get_all("Notification Log", filters={"document_name": raised.name}, pluck="for_user"),
		)

	def test_not_before_its_lead_time(self):
		name = self.schedule(first_due_date=add_days(today(), 30))
		self.run_reminders()
		self.assertIsNone(reminder_of("Recurring", "Reminder Schedule", name))

	def test_its_job_completes_it_with_a_remark_and_the_next_follows_the_calendar(self):
		name = self.schedule()
		self.run_reminders()
		occurrence = reminder_of("Recurring", "Reminder Schedule", name).name
		for user in (self.purchase, signer("Management")):  # one cannot see it; one sees all reminders
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(frappe.PermissionError):
					reminders.complete_occurrence(occurrence, "Not my job")
		frappe.set_user(self.stores_user)
		with self.assertRaisesRegex(frappe.ValidationError, "Say what was done"):
			reminders.complete_occurrence(occurrence, "  ")
		reminders.complete_occurrence(occurrence, "Traps checked, none triggered")
		done = frappe.get_doc("Reminder", occurrence)
		self.assertEqual((done.status, done.completed_by), (COMPLETED, self.stores_user))
		self.assertEqual(
			getdate(frappe.db.get_value("Reminder Schedule", name, "next_due_date")),
			getdate(add_months(add_days(today(), 3), 1)),
		)
		with self.assertRaisesRegex(frappe.ValidationError, "completed already"):
			reminders.complete_occurrence(occurrence, "Again")

	def test_a_plan_names_a_business_job_and_is_not_deleted(self):
		with self.assertRaisesRegex(frappe.ValidationError, "business jobs"):
			self.schedule(responsible_role="System Manager")
		name = self.schedule()
		frappe.set_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Reminder Schedule", name)
		doc = frappe.get_doc("Reminder Schedule", name)
		doc.next_due_date = add_days(today(), 100)
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertFalse(frappe.has_permission("Reminder Schedule", "create", user=self.stores_user))


# --- Found in the foundation review of 2026-10-12 ---------------------------------------------------


# Category -> who tells its people as an item falls due: "module" where the module that owns the
# record alerts them (and the reminder must stay silent), "reminder" where only the reminder
# does. A category missing here fails the test, so adding one forces this decision.
RAISE_NOTICE_FROM = {
	"Lot Expiry": "module",  # compliance.alerts: Lot Expiring Soon, Lot Expired
	"Supplier Certificate": "module",  # Supplier Certificate Expiring
	"Customer Licence": "module",  # Customer Licence Expiring
	"Document Review": "module",  # Document Review Due
	"Change Action": "module",  # change_control.alert_action_owner
	"Effectiveness Check": "module",  # Change Effectiveness Check Due
	"Work Task": "reminder",  # the task's own steps tell nobody of the due date
	"File Validity": "reminder",  # the repository has no alert of its own
	"Calibration Due": "reminder",  # equipment care has no due-date alert
	"Maintenance Due": "reminder",
	"Requalification Due": "reminder",
	"Calibration Impact": "module",  # Calibration Out of Tolerance, sent on completion
	"Training Due": "reminder",  # training opens records and tells nobody
	"Risk Review": "reminder",  # a review is raised without an alert
	"Regulatory Licence": "module",  # Regulatory Licence Expiring, licences and obligations
	"DMR Re-approval": "module",  # Device Master Record Out of Date
}


@verifies("URS-PLT-014", "URS-PLT-020")
class TestEveryCategoryKeepsTheContract(ReminderTestCase):
	"""Data-driven over reminders.CATEGORIES, so a category added later is held to the contract
	without a test of its own: what it shows due within its lead time is part of what it shows
	still to be done; its reminder is raised within the lead time and its people told once; it stays
	open while its item is still to be done, whatever the lead time; it is settled only when the
	item is done; and an item due again is raised again. Its people get one notice of the raise,
	from one source: the module's alert where the category declares one, the reminder otherwise.
	Each category's reading of its own records is tested with its module."""

	def test_each_category_keeps_the_contract(self):
		for category, kind in reminders.CATEGORIES.items():
			with self.subTest(category=category):
				self.check_reading(category, kind.due)
				self.check_lifecycle(category, told_by_module=bool(kind.told_by))

	def test_each_category_declares_who_tells_its_people_on_raise(self):
		self.assertEqual(set(reminders.CATEGORIES), set(RAISE_NOTICE_FROM))
		for category, kind in reminders.CATEGORIES.items():
			with self.subTest(category=category):
				self.assertEqual("module" if kind.told_by else "reminder", RAISE_NOTICE_FROM[category])
				if kind.told_by:
					self.assertTrue(callable(frappe.get_attr(kind.told_by)))  # names a real alert

	def check_reading(self, category: str, due_now) -> None:
		due = {item.key(category) for item in due_now()}
		with reminders.whole_horizon():
			still_to_do = {item.key(category) for item in due_now()}
		self.assertFalse(frappe.flags.reminders_whole_horizon)
		self.assertLessEqual(due, still_to_do)

	def check_lifecycle(self, category: str, told_by_module: bool) -> None:
		tag = frappe.generate_hash(length=8)  # its own item on every run of the tests
		subject = f"Contract of {category} {tag}"
		item = frappe._dict(done=False, due=getdate(add_days(today(), 20)))

		def due_now():
			# As a category reads its records: the item while not done, due within reminders.within.
			if not item.done and item.due <= getdate(reminders.within("lot_expiry_warning_days")):
				yield reminders.Due("User", self.qa, item.due, subject, (QA_ROLE,), part=tag)

		def run(lead_days: int) -> list[frappe._dict]:
			with use_setting("lot_expiry_warning_days", lead_days):
				reminders.track(category, due_now)
				reminders.send_notices()
			return frappe.get_all(
				"Reminder",
				filters={"category": category, "subject": subject},
				fields=["name", "status", "completion", "notified"],
				order_by="creation asc",
			)

		# One notice of the raise, from one source: none from the reminder where the module alerts.
		told = [] if told_by_module else [subject]
		run(30)
		raised = run(30)
		self.assertEqual([(r.status, r.notified) for r in raised], [(OPEN, RAISED)])  # raised once
		self.assertEqual(notices(raised[0].name, self.qa), told)
		# QA shortens the lead time: the item, 20 days out, is outside it but still to be done.
		self.assertEqual([r.status for r in run(10)], [OPEN])
		item.done = True
		settled = run(30)
		self.assertEqual([r.status for r in settled], [COMPLETED])
		self.assertIn("no longer shows this due", settled[0].completion)
		item.done = False  # due again: a lot back from hold, a renewal withdrawn
		again = run(30)
		self.assertEqual([r.status for r in again], [COMPLETED, OPEN])
		self.assertEqual(notices(again[1].name, self.qa), told)


@verifies("URS-PLT-014")
class TestSettledOnlyWhenTheWorkIsDone(ReminderTestCase):
	def test_a_shorter_lead_time_does_not_complete_a_reminder_still_to_be_done(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 20))
		with use_setting("lot_expiry_warning_days", 30):
			self.run_reminders()
		self.assertEqual(reminder_of("Lot Expiry", "Batch", lot).status, OPEN)
		with use_setting("lot_expiry_warning_days", 10):
			self.run_reminders()
		self.assertEqual(reminder_of("Lot Expiry", "Batch", lot).status, OPEN)

	def test_a_lot_back_from_hold_is_raised_again_and_the_first_reminder_kept(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		first = reminder_of("Lot Expiry", "Batch", lot).name
		set_status(lot, HOLD, "Kept apart for the test")
		self.run_reminders()
		self.assertEqual(frappe.db.get_value("Reminder", first, "status"), COMPLETED)
		set_status(lot, APPROVED, "Back from hold for the test")
		self.run_reminders()
		again = reminder_of("Lot Expiry", "Batch", lot)
		self.assertNotEqual(again.name, first)
		self.assertEqual(again.status, OPEN)
		self.assertEqual(frappe.db.get_value("Reminder", first, "status"), COMPLETED)

	def test_a_due_date_moved_in_the_record_says_so(self):
		document = work_instruction_for_test()
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_days(today(), 5))
		self.run_reminders()
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_months(today(), 24))
		self.run_reminders()
		done = reminder_of("Document Review", "Controlled Document", document)
		self.assertEqual(done.status, COMPLETED)
		self.assertIn("moved to", done.completion)


@verifies("URS-PLT-020")
class TestNotices(ReminderTestCase):
	def raised(self, roles: tuple[str, ...], user: str | None = None) -> str:
		"""A reminder of a category the reminder itself tells, raised for an item of the test."""
		category = next(name for name, kind in reminders.CATEGORIES.items() if not kind.told_by)
		tag = frappe.generate_hash(length=8)
		item = reminders.Due("User", self.qa, add_days(today(), 5), f"Notice test {tag}", roles, user, tag)
		frappe.set_user("Administrator")
		reminders.track(category, lambda: iter([item]))
		return frappe.db.get_value("Reminder", {"category": category, "subject": item.subject})

	def test_raised_its_jobs_are_told_once_when_it_names_no_person(self):
		name = self.raised(("Stock User", QA_ROLE))
		reminders.send_notices()
		reminders.send_notices()
		for user in (self.stores_user, self.qa):
			with self.subTest(user=user):
				self.assertEqual(len(notices(name, user)), 1)
		self.assertEqual(notices(name, self.purchase), [])  # not one of its jobs

	def test_raised_its_person_is_told_not_its_jobs(self):
		name = self.raised((QA_ROLE,), self.requester)
		reminders.send_notices()
		self.assertEqual(len(notices(name, self.requester)), 1)
		self.assertEqual(notices(name, self.qa), [])

	def test_where_the_module_alerts_its_people_are_told_once_by_the_module_alone(self):
		from meddevice_suite.compliance.alerts import send_expiry_alerts

		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		for _day in range(2):
			frappe.set_user("Administrator")
			send_expiry_alerts()  # the daily job runs the module's alerts before the reminders
			self.run_reminders()
		raised = reminder_of("Lot Expiry", "Batch", lot)
		self.assertEqual(frappe.db.get_value("Reminder", raised.name, "notified"), RAISED)
		for user in (self.stores_user, self.qa):
			with self.subTest(user=user):
				lot_alerts = frappe.get_all(
					"Notification Log",
					filters={"document_type": "Batch", "document_name": lot, "for_user": user},
					pluck="name",
				)
				self.assertEqual(len(lot_alerts), 1)  # Lot Expiring Soon, once
				self.assertEqual(notices(raised.name, user), [])  # and nothing more from the reminder

	def test_escalated_qa_management_and_its_person_are_told_once(self):
		change = approved_change(self.requester, self.qa, work_instruction_for_test())
		action = frappe.get_all("Change Request Action", filters={"parent": change}, pluck="name")[0]
		frappe.db.set_value("Change Request Action", action, "due_date", add_days(today(), -5))
		manager = signer("Management")
		with use_setting("reminder_escalation_days", 3):
			self.run_reminders()  # raised overdue and escalated in the same run
			self.run_reminders()
		escalated = reminder_of("Change Action", "Change Request", change)
		self.assertEqual(escalated.status, ESCALATED)
		self.assertEqual(frappe.db.get_value("Reminder", escalated.name, "notified"), ESCALATED)
		# Its module told the owner of the action as it fell due; the reminder tells the escalation.
		self.assertEqual(len(notices(escalated.name, self.requester)), 1)
		for user in (self.requester, self.qa, manager):
			with self.subTest(user=user):
				self.assertEqual(told_of_escalation(escalated.name).count(user), 1)

	def test_escalated_its_person_is_told_of_the_escalation_not_raised_again(self):
		name = self.raised((QA_ROLE,), self.requester)
		reminders.send_notices()
		frappe.db.set_value("Reminder", name, "status", ESCALATED)  # as the daily run escalates it
		reminders.send_notices()
		told = notices(name, self.requester)
		self.assertEqual(
			len([subject for subject in told if not subject.startswith("Overdue:")]), 1
		)  # raised
		self.assertEqual(told_of_escalation(name).count(self.requester), 1)

	def test_a_notice_that_could_not_be_sent_is_sent_on_the_next_run(self):
		name = self.raised(("Stock User",))
		with patch.object(reminders, "enqueue_create_notification", side_effect=RuntimeError("queue down")):
			reminders.send_notices()
		self.assertEqual(frappe.db.get_value("Reminder", name, "status"), OPEN)  # raised, nobody told
		self.assertEqual(notices(name, self.stores_user), [])
		self.assertFalse(frappe.db.get_value("Reminder", name, "notified"))
		reminders.send_notices()
		self.assertEqual(len(notices(name, self.stores_user)), 1)

	def test_the_upgrade_records_what_was_told_before_it(self):
		from meddevice_suite.patches.v0_1 import record_reminders_already_told

		overdue, soon = receive(ITEM, 5, store("Stores")), receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", overdue, "expiry_date", add_days(today(), -2))
		frappe.db.set_value("Batch", soon, "expiry_date", add_days(today(), 10))
		with use_setting("reminder_escalation_days", 1):
			self.run_reminders()
		escalated = reminder_of("Lot Expiry", "Batch", overdue).name
		untold = reminder_of("Lot Expiry", "Batch", soon).name
		for name in (escalated, untold):  # as reminders kept before the upgrade
			frappe.db.set_value("Reminder", name, "notified", None, update_modified=False)
		record_reminders_already_told.execute()
		self.assertEqual(frappe.db.get_value("Reminder", escalated, "notified"), ESCALATED)
		self.assertFalse(frappe.db.get_value("Reminder", untold, "notified"))  # told on the next run
