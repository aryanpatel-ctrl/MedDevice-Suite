"""Configuration of a site (URS-CMP-038): tests derived from the requirement's clauses."""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.roles import CONFIGURATION_PERMISSIONS

LOCKED = ("Custom Field", "Property Setter", "Client Script", "Server Script")


def audit_events(doctype: str, name: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["action", "user", "changes"],
		order_by="event_at",
	)


def remove_field_added_by_hand(doctype: str, fieldname: str) -> None:
	"""The record type's form back to what the app defines: the custom field gone, and the cached
	form with it."""
	frappe.db.delete("Custom Field", {"dt": doctype, "fieldname": fieldname})
	frappe.clear_cache(doctype=doctype)


@verifies("URS-CMP-038")
class TestNobodyConfiguresBySiteHand(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.administrator = signer("System Administrator")
		cls.qa = signer("QA Manager")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_no_role_creates_changes_or_deletes_fields_form_changes_or_scripts(self):
		for doctype in LOCKED:
			for user in (self.administrator, self.qa):
				for ptype in ("create", "write", "delete"):
					with self.subTest(doctype=doctype, user=user, ptype=ptype):
						self.assertFalse(frappe.has_permission(doctype, ptype, user=user))
			with self.subTest(doctype=doctype, looks=True):
				self.assertTrue(frappe.has_permission(doctype, "read", user=self.administrator))

	def test_a_field_added_by_hand_is_refused(self):
		# Should the refusal ever fail, the field must not outlive the test: adding it alters the
		# Item table, which commits at once, and every later test and screen would see it.
		self.addCleanup(remove_field_added_by_hand, "Item", "mds_by_hand")
		frappe.set_user(self.administrator)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Custom Field",
					"dt": "Item",
					"fieldname": "mds_by_hand",
					"label": "By Hand",
					"fieldtype": "Data",
				}
			).insert()

	def test_the_customize_form_screen_is_closed(self):
		frappe.set_user(self.administrator)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Customize Form", check_permission=True)  # as the screen's save loads it

	def test_the_apps_own_configuration_is_still_applied_on_migrate(self):
		name = frappe.db.get_value("Custom Field", {"dt": "Stock Entry", "fieldname": "mds_fefo_reason"})
		frappe.db.delete("Custom Field", name)  # lost behind the screens
		frappe.clear_cache(doctype="Stock Entry")
		apply_configuration()  # as on every migrate
		self.assertTrue(
			frappe.db.exists("Custom Field", {"dt": "Stock Entry", "fieldname": "mds_fefo_reason"})
		)

	def test_a_permission_added_by_hand_does_not_last(self):
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Custom Field",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": "System Manager",
				"permlevel": 1,
				"read": 1,
				"write": 1,
			}
		).insert(ignore_permissions=True)
		apply_configuration()
		rows = frappe.get_all("Custom DocPerm", filters={"parent": "Custom Field"}, fields=["role", "write"])
		self.assertEqual(
			sorted((row.role, row.write) for row in rows),
			sorted((role, 0) for role in CONFIGURATION_PERMISSIONS["Custom Field"]),
		)


@verifies("URS-CMP-038")
class TestConfigurationIsAudited(IntegrationTestCase):
	def test_a_form_change_made_anyway_is_recorded(self):
		setter = make_property_setter(
			"Item", "description", "bold", 1, "Check", validate_fields_for_doctype=False
		)
		events = audit_events("Property Setter", setter.name)
		self.assertTrue(events)
		self.assertEqual(events[0].user, frappe.session.user)

	def test_a_settings_change_keeps_its_old_and_new_values(self):
		settings = frappe.get_doc("Manufacturing Settings")
		before = settings.material_consumption
		settings.material_consumption = 0 if before else 1
		settings.save()
		events = audit_events("Manufacturing Settings", "Manufacturing Settings")
		self.assertIn("material_consumption", events[-1].changes)
