from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.qa.site_setup import TEST_COMPANY, prepare_test_site


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

		self.setup_complete.assert_called_once_with(TEST_COMPANY)

	def test_fails_when_the_wizard_does_not_complete(self):
		with patch("frappe.is_setup_complete", return_value=False), self.assertRaises(frappe.ValidationError):
			prepare_test_site()
