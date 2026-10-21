"""The data the browser test sheet of D11 Task, Approval & Reminders starts from (qa/sheets/d11.py),
proven on its own.

The sheet's Cypress spec (cypress/integration/sheet_d11.js) calls these set-ups as the Administrator
of a test site, after ui_journey.prepare_journey, and its tests start from what they make. These
tests make the same calls the same way and check that every record is there, in the state the sheet
starts from, made by the job that makes it with its signatures; that the daily run the set-up starts
raises, tells and escalates as the scheduler's would, and tells nobody twice; and that every set-up
is refused off a test site, to anyone but the Administrator, and without the run's password where
it signs.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_days, getdate, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.lot_control import lot_dates, stores
from meddevice_suite.lot_control.lot_status import QUARANTINE, status_of, stock_by_lot
from meddevice_suite.master_data import equipment, master_approval, supplier_approval
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d11
from meddevice_suite.qa.test_sheets import SheetTestCase, signed
from meddevice_suite.qa.ui_journey import COMPANY, SUPPLIER, prepare_journey
from meddevice_suite.qa.ui_users import email_for, prepare_job_users
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.work_management import reminders, work_task

QA, PRODUCTION, OPERATOR, SALES = "QA Manager", "Production Supervisor", "Operator", "Sales"
LEAD = d11.LOT_EXPIRY_SETTING


def set_ups(password: str) -> list[tuple]:
	"""Every set-up the sheet offers the browser, with arguments of the kind it sends."""
	return [
		(d11.prepare_sheet, {"password": password}),
		(d11.unique_tag, {}),
		(d11.prepare_task, {"password": password}),
		(d11.prepare_lot_expiring, {"password": password}),
		(d11.prepare_calibration_due, {"password": password}),
		(d11.prepare_schedule, {"title": "Internal audit"}),
		(d11.daily_run, {"categories": '["Work Task"]'}),
		(d11.prepare_shorter_lead, {"password": password}),
		(d11.restore_lead_time, {"password": password}),
		(d11.prepare_my_work, {"password": password}),
	]


def open_reminders(category: str, doctype: str, name: str) -> list[frappe._dict]:
	return frappe.get_all(
		reminders.DOCTYPE,
		filters={
			"category": category,
			"reference_doctype": doctype,
			"reference_name": name,
			"status": ["in", reminders.STILL_OPEN],
		},
		fields=["name", "status", "responsible_user", "notified", "escalated_on"],
	)


@verifies("URS-PLT-007")
class TestSheetD11SetUpRefusals(SheetTestCase):
	def test_every_set_up_the_sheet_offers_is_checked_here(self):
		offered = {
			value
			for value in vars(d11).values()
			if callable(value)
			and getattr(value, "__module__", None) == d11.__name__
			and value in frappe.whitelisted
		}
		self.assertEqual(offered, {set_up for set_up, _arguments in set_ups(self.password)})

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for set_up, arguments in set_ups(self.password):
				with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
					set_up(**arguments)

	def test_only_the_administrator_prepares_the_sheet(self):
		frappe.set_user(prepare_job_users()["users"][QA])

		for set_up, arguments in set_ups(self.password):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
				set_up(**arguments)

	def test_refuses_to_sign_without_the_runs_password(self):
		without = [
			d11.prepare_sheet,
			d11.prepare_task,
			d11.prepare_lot_expiring,
			d11.prepare_calibration_due,
			d11.prepare_shorter_lead,
			d11.restore_lead_time,
			d11.prepare_my_work,
		]
		made = ("Work Task", "Purchase Receipt", "Asset", "Change Request", "Quotation")
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up in without:
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(password="")
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_the_sheet_does_not_prepare(self):
		tasks = frappe.db.count("Work Task")
		for arguments in ({"stage": "approved"}, {"raised_by": "Nobody"}, {"approver": "Administrator"}):
			with self.subTest(arguments=arguments), self.assertRaises(frappe.ValidationError):
				d11.prepare_task(password=self.password, **arguments)
		self.assertEqual(frappe.db.count("Work Task"), tasks)
		with self.assertRaises(frappe.ValidationError):
			d11.daily_run(["Work Task", "No such category"])


@verifies("URS-PLT-007")
class TestSheetD11TasksApprovalsReminders(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d11.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(
			(sheet["company"], sheet["supplier"], sheet["material"]), (COMPANY, SUPPLIER, d11.MATERIAL)
		)
		self.assertEqual(sheet["stores"]["returns"], stores.store_for(stores.RETURNS, COMPANY))
		self.assertEqual(sheet["lot_expiry_default"], quality_settings.SETTINGS[LEAD].default)
		approval = supplier_approval.current_approval(SUPPLIER)
		self.assertEqual(approval.decision, supplier_approval.APPROVED)
		self.assertIn(d11.MATERIAL, approval.item_codes)
		self.assertEqual(
			frappe.get_all("User Role Profile", filters={"parent": sheet["second_qa"]}, pluck="role_profile"),
			[QA],
		)

	def test_an_open_task_raised_by_the_supervisor_for_the_operator(self):
		made = d11.prepare_task(self.password)

		task = frappe.get_doc(work_task.DOCTYPE, made["task"])
		self.assertEqual(
			(task.status, task.owner, task.assigned_to, task.approver, task.docstatus, task.result),
			(work_task.OPEN, email_for(PRODUCTION), email_for(OPERATOR), email_for(QA), 0, None),
		)
		self.assertEqual(
			(task.subject, getdate(task.due_date)), (d11.TASK_SUBJECT, getdate(add_days(today(), 3)))
		)
		self.assertEqual(made["due_date"], str(getdate(task.due_date)))

	def test_a_task_submitted_by_the_person_assigned(self):
		task = frappe.get_doc(work_task.DOCTYPE, d11.prepare_task(self.password, "submitted")["task"])

		self.assertEqual((task.status, task.result), (work_task.SUBMITTED, "Die cleaned, no damage found"))
		self.assertTrue(task.submitted_on)
		self.assertEqual(signed(work_task.DOCTYPE, task.name), [])

	def test_a_task_returned_by_its_approver_with_a_signature_and_submitted_again(self):
		task = frappe.get_doc(work_task.DOCTYPE, d11.prepare_task(self.password, "resubmitted")["task"])

		self.assertEqual(
			(task.status, task.result, task.return_reason, task.docstatus),
			(work_task.SUBMITTED, "Die cleaned; die lip inspected, no nicks", None, 0),
		)
		self.assertEqual(signed(work_task.DOCTYPE, task.name), [("Return", email_for(QA))])

	def test_a_task_for_another_job_and_overdue(self):
		task = frappe.get_doc(
			work_task.DOCTYPE,
			d11.prepare_task(self.password, assigned_to=SALES, due_in_days=-10)["task"],
		)

		self.assertEqual((task.assigned_to, task.status), (email_for(SALES), work_task.OPEN))
		self.assertEqual(getdate(task.due_date), getdate(add_days(today(), -10)))

	def test_a_lot_in_quarantine_its_expiry_corrected_by_qa_and_alerted(self):
		made = d11.prepare_lot_expiring(self.password, 5)

		lot = frappe.get_doc("Batch", made["lot"])
		self.assertEqual((lot.item, getdate(lot.expiry_date)), (d11.MATERIAL, getdate(add_days(today(), 5))))
		self.assertEqual(status_of([lot.name])[lot.name], QUARANTINE)
		self.assertGreater(sum(row.qty for row in stock_by_lot(lot=lot.name)), 0)
		self.assertEqual(signed("Batch", lot.name), [(lot_dates.ACT, email_for(QA))])
		self.assertEqual(lot.get("mds_expiry_alert"), "Lot Expiring Soon")  # Stores and QA told at once

	def test_an_instrument_approved_never_calibrated_whose_calibration_the_run_raises(self):
		made = d11.prepare_calibration_due(self.password)

		self.assertTrue(equipment.approved(made["asset"]))
		self.assertEqual(signed("Asset", made["asset"]), [("Approve", email_for(QA))])
		d11.daily_run(["Calibration Due"])
		self.assertEqual(len(open_reminders("Calibration Due", "Asset", made["asset"])), 1)

	def test_the_daily_run_tells_a_raised_reminder_once(self):
		task = d11.prepare_task(self.password)["task"]

		told = d11.daily_run(["Work Task"])["told"]
		[reminder] = open_reminders("Work Task", work_task.DOCTYPE, task)
		self.assertIn(reminder.name, told)
		self.assertEqual(
			(reminder.status, reminder.responsible_user, reminder.notified),
			(reminders.OPEN, email_for(OPERATOR), reminders.RAISED),
		)
		self.assertNotIn(reminder.name, d11.daily_run(["Work Task"])["told"])

	def test_the_daily_run_escalates_a_task_overdue_beyond_the_escalation_days(self):
		task = d11.prepare_task(self.password, due_in_days=-10)["task"]

		d11.daily_run(["Work Task"])
		[reminder] = open_reminders("Work Task", work_task.DOCTYPE, task)
		self.assertEqual((reminder.status, reminder.notified), (reminders.ESCALATED, reminders.ESCALATED))
		self.assertTrue(reminder.escalated_on)
		self.assertNotIn(reminder.name, d11.daily_run(["Work Task"])["told"])

	def test_a_schedule_qa_plans_whose_occurrence_the_run_raises(self):
		made = d11.prepare_schedule("Internal audit", first_due_in_days=3, lead_days=7)

		schedule = frappe.get_doc(reminders.SCHEDULE, made["schedule"])
		self.assertEqual(
			(schedule.owner, schedule.responsible_role, schedule.active, getdate(schedule.next_due_date)),
			(email_for(QA), "Quality Manager", 1, getdate(add_days(today(), 3))),
		)
		d11.daily_run([])
		self.assertEqual(
			frappe.get_all(reminders.DOCTYPE, filters={"schedule": schedule.name}, pluck="category"),
			[reminders.SCHEDULED],
		)

	def test_a_lot_expiry_reminder_open_and_a_change_request_listing_the_settings(self):
		made = d11.prepare_shorter_lead(self.password)

		self.assertEqual(made["lead_days"], quality_settings.value(LEAD))
		self.assertEqual(getdate(made["expiry_date"]), getdate(add_days(today(), 20)))
		self.assertEqual(
			[row.name for row in open_reminders("Lot Expiry", "Batch", made["lot"])], [made["reminder"]]
		)
		request = frappe.get_doc("Change Request", made["change_request"])
		self.assertEqual((request.status, request.requested_by), ("Approved", email_for(PRODUCTION)))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[(quality_settings.DOCTYPE, quality_settings.DOCTYPE)],
		)
		self.assertEqual(
			signed("Change Request", request.name), [("Assess", email_for(QA)), ("Approve", email_for(QA))]
		)

	def test_the_lead_time_put_back_under_change_control(self):
		d11.set_lot_expiry_lead(10, self.password)
		self.assertEqual(quality_settings.value(LEAD), 10)

		self.assertEqual(d11.restore_lead_time(self.password), quality_settings.SETTINGS[LEAD].default)
		self.assertEqual(
			frappe.db.get_value(
				"Change Request", quality_settings.raw(quality_settings.CHANGED_UNDER), "status"
			),
			"Approved",
		)

	def test_what_my_work_lists_for_qa_the_author_and_management(self):
		made = d11.prepare_my_work(self.password)

		specification = made["specification"]
		self.assertEqual(
			frappe.db.get_value(
				"Quality Inspection Template",
				specification["name"],
				[master_approval.APPROVAL_STATUS, "mds_item"],
			),
			(master_approval.DRAFT, specification["item"]),
		)
		record = frappe.get_doc(dmr.DOCTYPE, made["device_master_record"]["name"])
		self.assertEqual(
			(record.status, record.author, record.docstatus),
			(dmr.AWAITING_APPROVAL, email_for(PRODUCTION), 0),
		)
		self.assertIn(("Author", email_for(PRODUCTION)), signed(dmr.DOCTYPE, record.name))
		returned = made["returned"]
		self.assertEqual(
			sum(row.qty for row in stock_by_lot(lot=returned["lot"], warehouse=[returned["store"]])), 2
		)
		quotation = frappe.get_doc("Quotation", made["quotation"])
		self.assertEqual(
			(quotation.workflow_state, quotation.docstatus, quotation.owner),
			("Awaiting Price Approval", 0, email_for(SALES)),
		)
		change = frappe.get_doc("Change Request", made["change_request"]["name"])
		self.assertEqual(
			(change.status, change.requested_by, change.assessed_by),
			("Awaiting Approval", email_for(QA), made["change_request"]["assessed_by"]),
		)
		self.assertNotEqual(change.assessed_by, email_for(QA))
