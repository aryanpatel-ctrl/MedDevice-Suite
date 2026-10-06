from collections import Counter

import frappe
from frappe.desk.query_report import run as run_report
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from meddevice_suite.compliance.retention import (
	LOGIN_HISTORY,
	RETENTION_SCHEDULE,
	apply_retention,
	must_be_scheduled,
	retention_years,
	scheduled_doctypes,
)
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile


def login_history_days() -> int | None:
	return frappe.db.get_value(
		"Logs To Clear", {"parent": "Log Settings", "ref_doctype": LOGIN_HISTORY}, "days"
	)


@verifies("URS-CMP-023")
class TestRetentionSchedule(IntegrationTestCase):
	def test_every_regulated_record_type_and_the_evidence_is_scheduled_once(self):
		scheduled = scheduled_doctypes()

		self.assertEqual([doctype for doctype, count in Counter(scheduled).items() if count > 1], [])
		self.assertEqual(set(scheduled), must_be_scheduled())

	def test_record_types_outside_the_schedule_have_no_period(self):
		self.assertIsNone(retention_years("ToDo"))

	def test_every_period_is_at_least_ten_years_with_a_legal_basis(self):
		for category, (years, basis, _) in RETENTION_SCHEDULE.items():
			with self.subTest(category=category):
				self.assertGreaterEqual(years, 10)
				self.assertTrue(basis.strip())


@verifies("URS-CMP-023")
class TestRetentionScheduleReport(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")

	def test_qa_sees_the_period_and_basis_of_every_scheduled_record_type(self):
		frappe.set_user(user_with_profile("QA Manager"))

		rows = run_report("Retention Schedule")["result"]

		self.assertEqual({row["record_type"] for row in rows}, set(scheduled_doctypes()))
		for row in rows:
			with self.subTest(record_type=row["record_type"]):
				self.assertEqual(row["years"], retention_years(row["record_type"]))
				self.assertTrue(row["basis"])

	def test_jobs_outside_qa_cannot_open_it(self):
		frappe.set_user(user_with_profile("Sales"))

		with self.assertRaises(frappe.PermissionError):
			run_report("Retention Schedule")


@verifies("URS-CMP-024")
class TestLoginHistoryIsKept(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def test_login_history_is_kept_for_its_retention_period(self):
		self.assertGreaterEqual(login_history_days(), retention_years(LOGIN_HISTORY) * 365)

	def test_a_year_old_login_survives_frappes_log_clean_up(self):
		login = frappe.get_doc(
			{
				"doctype": "Activity Log",
				"subject": "Administrator logged in",
				"operation": "Login",
				"status": "Success",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Activity Log", login.name, "creation", add_days(now_datetime(), -400), update_modified=False
		)

		frappe.get_single("Log Settings").clear_logs()

		self.assertTrue(frappe.db.exists("Activity Log", login.name))

	def test_a_manual_change_is_undone_on_the_next_migrate(self):
		frappe.db.set_value(
			"Logs To Clear", {"parent": "Log Settings", "ref_doctype": LOGIN_HISTORY}, "days", 90
		)

		apply_configuration()

		self.assertGreaterEqual(login_history_days(), retention_years(LOGIN_HISTORY) * 365)

	def test_a_missing_entry_is_added_back_and_an_unchanged_one_left_alone(self):
		frappe.db.delete("Logs To Clear", {"parent": "Log Settings", "ref_doctype": LOGIN_HISTORY})

		apply_retention()
		saved = frappe.db.get_single_value("Log Settings", "modified")
		apply_retention()

		self.assertGreaterEqual(login_history_days(), retention_years(LOGIN_HISTORY) * 365)
		self.assertEqual(frappe.db.get_single_value("Log Settings", "modified"), saved)

	def test_the_audit_trail_and_signatures_are_not_logs_frappe_clears(self):
		cleared = frappe.get_all("Logs To Clear", filters={"parent": "Log Settings"}, pluck="ref_doctype")

		self.assertNotIn("Audit Event", cleared)
		self.assertNotIn("Electronic Signature", cleared)
