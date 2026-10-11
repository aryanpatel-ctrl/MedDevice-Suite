"""The Administrator account on a working site (URS-CMP-039): tests derived from the requirement's
clauses. A working site is one with none of the development, test or pilot flags; the tests set
the flags as such a site's configuration would."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_to_date, now_datetime

from meddevice_suite.compliance import administrator
from meddevice_suite.compliance.administrator import (
	WINDOW_KEY,
	refuse_administrator_login,
	refuse_administrator_requests,
)
from meddevice_suite.qa import verifies

WORKING_SITE = {"developer_mode": 0, "allow_tests": 0, "pilot_site": 0, WINDOW_KEY: None}


@contextmanager
def site(**config):
	with (
		patch.dict(frappe.conf, {**WORKING_SITE, **config}),
		patch.object(frappe, "is_setup_complete", return_value=True),
	):
		yield


@contextmanager
def session_of(user: str):
	with patch.object(frappe.local, "session", frappe._dict(user=user)):
		yield


def login(user: str) -> None:
	refuse_administrator_login(frappe._dict(user=user))


@verifies("URS-CMP-039")
class TestAdministratorOnAWorkingSite(UnitTestCase):
	def test_administrator_does_not_sign_in(self):
		with site(), self.assertRaisesRegex(frappe.AuthenticationError, "own System Administrator account"):
			login("Administrator")

	def test_a_named_account_signs_in_as_before(self):
		with site():
			login("it.admin@example.com")

	def test_a_session_or_api_key_of_administrator_is_refused_on_every_request(self):
		with site(), session_of("Administrator"), self.assertRaises(frappe.AuthenticationError):
			refuse_administrator_requests()
		with site(), session_of("it.admin@example.com"):
			refuse_administrator_requests()

	def test_a_maintenance_window_opens_it_until_its_time(self):
		future = str(add_to_date(now_datetime(), hours=2))
		past = str(add_to_date(now_datetime(), minutes=-1))
		with site(**{WINDOW_KEY: future}):
			login("Administrator")
		with site(**{WINDOW_KEY: past}), self.assertRaises(frappe.AuthenticationError):
			login("Administrator")
		with site(**{WINDOW_KEY: "next tuesday"}), self.assertRaises(frappe.AuthenticationError):
			login("Administrator")  # a window that cannot be read is closed

	def test_the_setup_wizard_runs_as_administrator(self):
		with (
			patch.dict(frappe.conf, WORKING_SITE),
			patch.object(frappe, "is_setup_complete", return_value=False),
		):
			login("Administrator")

	def test_development_test_and_pilot_sites_keep_it(self):
		for flag in ("developer_mode", "allow_tests", "pilot_site"):
			with self.subTest(flag=flag), site(**{flag: 1}):
				login("Administrator")
		with (
			site(),
			patch.object(administrator, "site_flag", return_value=False),
			self.assertRaises(frappe.AuthenticationError),
		):
			login("Administrator")


@verifies("URS-CMP-039")
class TestFrappeRunsTheChecks(UnitTestCase):
	"""Through Frappe's own login trigger and request authentication, not only the functions."""

	def test_frappes_login_trigger_refuses_administrator(self):
		from frappe.auth import LoginManager

		login_manager = LoginManager.__new__(LoginManager)  # as Frappe has it once the password is right
		login_manager.user = "Administrator"
		with site(), self.assertRaises(frappe.AuthenticationError):
			login_manager.run_trigger("on_login")

	def test_frappes_request_authentication_refuses_administrator(self):
		from frappe.auth import validate_auth_via_hooks

		with site(), session_of("Administrator"), self.assertRaises(frappe.AuthenticationError):
			validate_auth_via_hooks()
