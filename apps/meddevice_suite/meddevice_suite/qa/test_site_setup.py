from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.qa.site_setup import (
	TEST_COMPANY,
	ensure_fiscal_years,
	fiscal_year_of,
	prepare_test_site,
	with_fiscal_year,
)
from meddevice_suite.setup.site_flags import site_flag


@verifies("URS-PLT-004")
class TestPrepareTestSite(UnitTestCase):
	def setUp(self):
		self.setup_complete = self.enterContext(patch("meddevice_suite.qa.site_setup.setup_complete"))
		self.enterContext(patch.dict(frappe.local.conf, {"allow_tests": 1}))

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}), self.assertRaises(frappe.ValidationError):
			prepare_test_site()

		self.setup_complete.assert_not_called()

	def test_leaves_a_completed_site_alone(self):
		with patch("frappe.is_setup_complete", return_value=True):
			prepare_test_site()

		self.setup_complete.assert_not_called()

	def test_completes_setup_with_the_test_company(self):
		with patch("frappe.is_setup_complete", side_effect=[False, True]):
			prepare_test_site()

		self.setup_complete.assert_called_once_with(with_fiscal_year(TEST_COMPANY))

	def test_the_financial_year_is_the_one_containing_the_day_the_site_is_made(self):
		"""A fixed year stops every stock posting on a fresh site once it has passed."""
		from datetime import date

		self.assertEqual(fiscal_year_of("2027-04-02"), (date(2027, 4, 1), date(2028, 3, 31)))
		self.assertEqual(fiscal_year_of("2027-03-31"), (date(2026, 4, 1), date(2027, 3, 31)))
		self.assertEqual(fiscal_year_of("2026-04-01"), (date(2026, 4, 1), date(2027, 3, 31)))
		company = with_fiscal_year(TEST_COMPANY, "2027-04-02")
		self.assertEqual((company["fy_start_date"], company["fy_end_date"]), ("2027-04-01", "2028-03-31"))
		self.assertNotIn("fy_start_date", TEST_COMPANY)  # nothing fixed left behind

	def test_fails_when_the_wizard_does_not_complete(self):
		with patch("frappe.is_setup_complete", return_value=False), self.assertRaises(frappe.ValidationError):
			prepare_test_site()


@verifies("URS-PLT-004", "URS-PLT-006")
class TestSiteFlags(UnitTestCase):
	def test_a_flag_counts_only_as_exactly_on(self):
		for value, on in (
			(1, True),
			(True, True),
			(0, False),
			("1", False),
			("0", False),
			("yes", False),
			(None, False),
		):
			with self.subTest(value=value), patch.dict(frappe.local.conf, {"pilot_site": value}):
				self.assertEqual(site_flag("pilot_site"), on)

	def test_a_bench_wide_setting_never_marks_a_site(self):
		with (
			patch.dict(frappe.local.conf, {"allow_tests": 1}),
			patch("os.path.exists", return_value=True),
			patch("frappe.get_file_json", return_value={"allow_tests": 1}),
		):
			self.assertFalse(site_flag("allow_tests"))


@verifies("URS-PLT-004")
class TestFinancialYears(IntegrationTestCase):
	def test_this_year_and_the_next_exist_before_tests(self):
		from frappe.utils import add_years, today

		ensure_fiscal_years()
		ensure_fiscal_years()  # once only
		for day in (today(), add_years(today(), 1)):
			start, _end = fiscal_year_of(day)
			self.assertEqual(frappe.db.count("Fiscal Year", {"year_start_date": start}), 1)

	def test_a_year_ahead_is_made_when_its_day_comes(self):
		from frappe.tests.classes.context_managers import freeze_time

		with freeze_time("2031-06-15"):
			ensure_fiscal_years()
		self.assertTrue(frappe.db.exists("Fiscal Year", {"year_start_date": "2031-04-01"}))
		self.assertTrue(frappe.db.exists("Fiscal Year", {"year_start_date": "2032-04-01"}))
