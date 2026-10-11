"""Reminders and next due (module 60; URS-PLT-014, URS-PLT-015): tests derived from the
requirements' clauses."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, getdate, today

from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.lot_control.lot_status import HOLD, set_status
from meddevice_suite.lot_control.test_lot_status import ensure_parties, lot_item, receive, store
from meddevice_suite.master_data.test_supplier_approval import approved_for, decide, ensure_supplier
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import licensed_for_test, work_instruction_for_test
from meddevice_suite.work_management import reminders
from meddevice_suite.work_management.reminders import COMPLETED, ESCALATED, OPEN, OVERDUE

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
			alerted = frappe.get_all(
				"Notification Log",
				filters={"document_type": "Reminder", "document_name": escalated.name},
				pluck="for_user",
			)
			self.assertIn(self.qa, alerted)
			self.assertNotIn(self.stores_user, alerted)
			self.run_reminders()
			self.assertEqual(
				len(
					frappe.get_all(
						"Notification Log",
						filters={
							"document_type": "Reminder",
							"document_name": escalated.name,
							"for_user": self.qa,
						},
					)
				),
				1,
			)

	def test_one_categorys_failure_leaves_its_reminders_and_the_others_go_on(self):
		lot = receive(ITEM, 5, store("Stores"))
		frappe.db.set_value("Batch", lot, "expiry_date", add_days(today(), 10))
		self.run_reminders()
		document = work_instruction_for_test()
		frappe.db.set_value("Controlled Document", document, "next_review_date", add_days(today(), 5))

		def broken():
			raise RuntimeError("source unreadable")
			yield  # a generator, as the categories are

		with patch.dict(reminders.CATEGORIES, {"Lot Expiry": broken}):
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
					"title": f"Pest control check {frappe.generate_hash(length=5)}",
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
