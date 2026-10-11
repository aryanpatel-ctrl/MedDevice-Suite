"""Access policy (URS-CMP-037): tests derived from the requirement's clauses."""

import frappe
from frappe.auth import LoginManager
from frappe.utils import add_days, today

from meddevice_suite.compliance.access_policy import FIXED, apply_access_policy, system_values
from meddevice_suite.compliance.test_quality_settings import REFUSED, QualitySettingsTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.install import apply_configuration


def system(field: str):
	return frappe.db.get_single_value("System Settings", field)


@verifies("URS-CMP-037")
class TestThePolicyOnTheSite(QualitySettingsTestCase):
	def test_until_qa_changes_them_the_decided_rules_apply(self):
		apply_configuration()
		self.assertEqual(system("minimum_password_score"), "3")
		self.assertEqual(system("force_user_to_reset_password"), 90)
		self.assertEqual(system("session_expiry"), "00:30")
		for field, wanted in FIXED.items():
			with self.subTest(field=field):
				self.assertEqual(system(field), wanted)
		self.assertEqual(system("login_with_email_link"), 0)  # no sign-in without the password

	def test_qa_changes_them_under_change_control_and_the_site_follows_at_once(self):
		self.settings(
			password_strength=4,
			password_expiry_days=60,
			session_idle_minutes=90,
			change_request=self.change_request(),
		).save()
		self.assertEqual(
			(
				system("minimum_password_score"),
				system("force_user_to_reset_password"),
				system("session_expiry"),
			),
			("4", 60, "01:30"),
		)
		self.assertEqual(frappe.get_system_settings("session_expiry"), "01:30")  # not a stale cache

	def test_a_change_needs_an_approved_change_request(self):
		with self.assertRaisesRegex(frappe.ValidationError, "approved change request"):
			self.settings(session_idle_minutes=240).save()

	def test_each_value_stays_within_bounds_that_keep_the_policy_safe(self):
		for field, refused in (
			("password_strength", (2, 5)),  # 2 is guessable
			("password_expiry_days", (29, 366, 0)),  # 0 would mean never
			("session_idle_minutes", (4, 481, 0)),
		):
			for number in refused:
				with self.subTest(field=field, value=number), self.assertRaises(frappe.ValidationError):
					self.settings(**{field: number}, change_request=self.change_request()).save()


@verifies("URS-CMP-037")
class TestNoHandEdit(QualitySettingsTestCase):
	def test_a_hand_edit_of_any_rule_in_system_settings_is_refused(self):
		frappe.set_user("Administrator")  # who may open System Settings
		for field, weaker in (
			("session_expiry", "170:00"),
			("minimum_password_score", "1"),
			("force_user_to_reset_password", 0),
			("login_with_email_link", 1),
			("enable_password_policy", 0),
			("logout_on_password_reset", 0),
			("allow_login_using_user_name", 1),
			("allow_consecutive_login_attempts", 0),
		):
			with self.subTest(field=field):
				settings = frappe.get_doc("System Settings")
				settings.set(field, weaker)
				with self.assertRaisesRegex(frappe.PermissionError, "access policy"):
					settings.save()
		settings = frappe.get_doc("System Settings")
		settings.save()  # the rest of System Settings still saves

	def test_a_hand_edit_past_the_form_does_not_last(self):
		frappe.db.set_single_value("System Settings", "session_expiry", "170:00")
		frappe.db.set_single_value("System Settings", "login_with_email_link", 1)
		apply_configuration()  # as on every migrate
		self.assertEqual((system("session_expiry"), system("login_with_email_link")), ("00:30", 0))


@verifies("URS-CMP-037")
class TestFrappeEnforcesThem(QualitySettingsTestCase):
	"""What the values do at sign-in, as Frappe applies them."""

	def setUp(self):
		super().setUp()
		apply_access_policy()

	def test_a_guessable_password_is_refused(self):
		"""Frappe's own check, the one a password change and the reset page run (it skips the user
		form's copy of it under tests), against the strength QA set."""
		from frappe.core.doctype.user.user import test_password_strength

		def passes(password: str) -> bool:
			return test_password_strength(password)["feedback"]["password_policy_validation_passed"]

		self.assertFalse(passes("stores123"))
		self.assertTrue(passes("Harbour-Lantern-Quietly-82-Orbit"))

	def test_after_the_days_qa_set_a_new_password_is_required_at_sign_in(self):
		user = signer("Stores")
		login = LoginManager.__new__(LoginManager)  # the check Frappe runs once the password is right
		login.user = user
		frappe.db.set_value("User", user, "last_password_reset_date", add_days(today(), -90))
		self.assertFalse(login.force_user_to_reset_password())
		frappe.db.set_value("User", user, "last_password_reset_date", add_days(today(), -91))
		self.assertTrue(login.force_user_to_reset_password())

	def test_a_session_ends_after_the_minutes_of_inactivity_qa_set(self):
		from frappe.sessions import get_expiry_in_seconds

		self.assertEqual(get_expiry_in_seconds(), 30 * 60)

	def test_no_sign_in_link_is_mailed(self):
		from unittest.mock import patch

		from frappe.www.login import send_login_link

		with patch("frappe.sendmail") as sendmail:
			send_login_link(signer("Stores"))
		sendmail.assert_not_called()

	def test_the_values_written_are_the_ones_the_policy_decides(self):
		for field, wanted in system_values().items():
			with self.subTest(field=field):
				self.assertEqual(str(system(field)), str(wanted))
