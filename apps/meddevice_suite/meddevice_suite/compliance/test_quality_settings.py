"""Quality Settings (URS-PLT-012, URS-PLT-013): tests derived from the requirements' clauses."""

from contextlib import contextmanager

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.quality_settings import DOCTYPE, SETTINGS, value
from meddevice_suite.master_data.test_versioning import draft_change
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.setup.install import apply_configuration

REFUSED = (frappe.ValidationError, frappe.PermissionError)


@contextmanager
def use_setting(field: str, number: int):
	"""A setting as QA would have saved it, for another module's test of what reads it; put back
	afterwards. Saving it under change control is tested here."""
	before = quality_settings.stored(field)
	frappe.db.set_single_value(DOCTYPE, field, number)  # test fixture only
	try:
		yield
	finally:
		if before is None:
			frappe.db.delete("Singles", {"doctype": DOCTYPE, "field": field})
		else:
			frappe.db.set_single_value(DOCTYPE, field, before)


class QualitySettingsTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.qa = signer("QA Manager")
		cls.requester = signer("Production Supervisor")

	def setUp(self):
		frappe.db.delete("Singles", {"doctype": DOCTYPE})  # as on a site where QA never changed them
		frappe.clear_document_cache(DOCTYPE, DOCTYPE)
		frappe.set_user(self.qa)

	def tearDown(self):
		frappe.set_user("Administrator")

	def change_request(self) -> str:
		change = approved_change(self.requester, self.qa, DOCTYPE, DOCTYPE, "System")
		frappe.set_user(self.qa)
		return change

	def settings(self, **values) -> frappe.model.document.Document:
		doc = frappe.get_doc(DOCTYPE)
		doc.update(values)
		return doc


@verifies("URS-PLT-012", "URS-PLT-013")
class TestValues(QualitySettingsTestCase):
	def test_until_qa_changes_them_the_decided_values_apply(self):
		self.assertEqual(
			{field: value(field) for field in SETTINGS},
			{
				"lot_expiry_warning_days": 30,
				"supplier_certificate_warning_days": 30,
				"document_review_warning_days": 30,
				"change_action_warning_days": 7,
				"default_effectiveness_check_days": 90,
				"login_lockout_attempts": 5,
				"login_lockout_minutes": 15,
			},
		)

	def test_each_value_stays_within_its_bounds(self):
		change = self.change_request()
		for field, setting in SETTINGS.items():
			for number in (setting.low - 1, setting.high + 1):
				with self.subTest(field=field, number=number):
					doc = self.settings(**{field: number, "change_request": change})
					with self.assertRaisesRegex(
						frappe.ValidationError, f"between {setting.low} and {setting.high}"
					):
						doc.save()
		for field, setting in SETTINGS.items():  # the bounds themselves are allowed
			with self.subTest(field=field, edge="low and high"):
				self.settings(**{field: setting.low, "change_request": self.change_request()}).save()
				self.settings(**{field: setting.high, "change_request": self.change_request()}).save()
				self.assertEqual(value(field), setting.high)


@verifies("URS-PLT-012", "URS-PLT-013")
class TestChangeControl(QualitySettingsTestCase):
	def test_a_change_needs_an_approved_change_request_listing_the_settings(self):
		with self.assertRaisesRegex(frappe.ValidationError, "only under an approved change request"):
			self.settings(lot_expiry_warning_days=10).save()
		draft = draft_change(self.requester, DOCTYPE, DOCTYPE)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved for implementation"):
			self.settings(lot_expiry_warning_days=10, change_request=draft).save()
		other = approved_change(
			self.requester,
			self.qa,
			frappe.db.get_value("Warehouse", {"warehouse_name": "Stores"}),
			"Warehouse",
			"System",
		)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			self.settings(lot_expiry_warning_days=10, change_request=other).save()
		self.assertEqual(value("lot_expiry_warning_days"), 30)

	def test_a_change_is_recorded_with_its_request_and_a_new_version(self):
		change = self.change_request()
		self.settings(lot_expiry_warning_days=10, change_request=change).save()
		doc = frappe.get_doc(DOCTYPE)
		self.assertEqual(value("lot_expiry_warning_days"), 10)
		self.assertEqual(
			(doc.changed_under, doc.version, str(doc.effective_from)), (change, 2, frappe.utils.today())
		)
		self.assertFalse(doc.change_request)  # named afresh for the next change
		with self.assertRaisesRegex(frappe.ValidationError, "only under an approved change request"):
			self.settings(lot_expiry_warning_days=12).save()  # the last request does not cover another

	def test_saving_without_a_change_needs_no_request(self):
		self.settings().save()
		self.assertEqual(value("lot_expiry_warning_days"), 30)

	def test_the_record_of_changes_is_set_by_the_rule_only(self):
		change = self.change_request()
		for field, number in (("version", 9), ("changed_under", change), ("effective_from", "2026-01-01")):
			with self.subTest(field=field):
				with self.assertRaises(frappe.PermissionError):
					self.settings(**{field: number}).save()

	def test_the_audit_trail_keeps_the_old_and_new_values_and_the_request(self):
		change = self.change_request()
		self.settings(document_review_warning_days=45, change_request=change).save()
		frappe.set_user("Administrator")
		event = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": DOCTYPE},
			fields=["changes", "user"],
			order_by="event_at desc, creation desc",
			limit=1,
		)[0]
		self.assertEqual(event.user, self.qa)
		self.assertIn("document_review_warning_days", event.changes)
		self.assertIn("45", event.changes)
		self.assertIn(change, event.changes)


@verifies("URS-PLT-012", "URS-PLT-013")
class TestWhoChangesThem(QualitySettingsTestCase):
	def test_only_qa_changes_them_and_the_administrator_reads_them(self):
		admin, stores = signer("System Administrator"), signer("Stores")
		self.assertTrue(frappe.has_permission(DOCTYPE, "write", user=self.qa))
		self.assertTrue(frappe.has_permission(DOCTYPE, "read", user=admin))
		self.assertFalse(frappe.has_permission(DOCTYPE, "write", user=admin))
		self.assertFalse(frappe.has_permission(DOCTYPE, "read", user=stores))
		change = self.change_request()
		frappe.set_user(admin)
		with self.assertRaises(REFUSED):
			frappe.client.set_value(DOCTYPE, DOCTYPE, "lot_expiry_warning_days", 10)
		frappe.set_user(admin)
		with self.assertRaises(REFUSED):
			self.settings(lot_expiry_warning_days=10, change_request=change).save()
		self.assertEqual(value("lot_expiry_warning_days"), 30)

	def test_a_change_request_may_list_the_settings(self):
		from meddevice_suite.qms.change_control import AFFECTED_TYPES

		self.assertIn(DOCTYPE, AFFECTED_TYPES)


@verifies("URS-PLT-013")
class TestLockout(QualitySettingsTestCase):
	def test_login_and_signing_follow_the_lockout_at_once(self):
		change = self.change_request()
		self.settings(login_lockout_attempts=3, login_lockout_minutes=10, change_request=change).save()
		self.assertEqual(frappe.db.get_single_value("System Settings", "allow_consecutive_login_attempts"), 3)
		self.assertEqual(frappe.db.get_single_value("System Settings", "allow_login_after_fail"), 600)

	def test_a_hand_edit_of_the_site_settings_does_not_last(self):
		change = self.change_request()
		self.settings(login_lockout_attempts=4, change_request=change).save()
		frappe.db.set_single_value("System Settings", "allow_consecutive_login_attempts", 0)  # by hand
		apply_configuration()  # as on every migrate
		self.assertEqual(frappe.db.get_single_value("System Settings", "allow_consecutive_login_attempts"), 4)
