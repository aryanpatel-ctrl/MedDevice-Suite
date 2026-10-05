import hashlib
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.desk import (
	DESKTOP_ICON_VISIBILITY,
	apply_desk_configuration,
	enforce_declared_visibility,
	set_desktop_icon_visibility,
)


def is_visible(label: str) -> bool:
	return frappe.db.get_value("Desktop Icon", label, "hidden") == 0


def shipped_icon_files() -> dict[str, str]:
	"""Hash of each declared icon's definition file in its owning app's source tree."""
	hashes = {}
	for label in DESKTOP_ICON_VISIBILITY:
		app = frappe.db.get_value("Desktop Icon", label, "app")
		path = frappe.get_app_path(app, "desktop_icon", f"{frappe.scrub(label)}.json")
		with open(path, "rb") as definition:
			hashes[path] = hashlib.sha256(definition.read()).hexdigest()
	return hashes


def invert_declared_visibility() -> None:
	for label, visible in DESKTOP_ICON_VISIBILITY.items():
		frappe.db.set_value("Desktop Icon", label, "hidden", int(visible))


class TestDeskConfiguration(IntegrationTestCase):
	def assert_declared_visibility(self):
		for label, visible in DESKTOP_ICON_VISIBILITY.items():
			self.assertEqual(is_visible(label), visible, label)

	@verifies("URS-PLT-001")
	def test_declared_visibility_is_applied(self):
		invert_declared_visibility()

		apply_desk_configuration()

		self.assert_declared_visibility()

	@verifies("URS-PLT-001")
	def test_undeclared_icons_keep_shipped_visibility(self):
		apply_desk_configuration()

		for label in ("Manufacturing", "Stock", "Quality", "Buying", "Selling", "Accounting"):
			self.assertTrue(is_visible(label), label)
		self.assertFalse(is_visible("Support"))

	@verifies("URS-PLT-002")
	def test_reapplying_is_idempotent(self):
		apply_desk_configuration()
		apply_desk_configuration()

		self.assert_declared_visibility()

	@verifies("URS-PLT-002")
	def test_owning_apps_source_is_never_modified(self):
		before = shipped_icon_files()
		invert_declared_visibility()

		with patch.dict(frappe.local.conf, {"developer_mode": 1}):
			apply_desk_configuration()

		self.assertEqual(shipped_icon_files(), before)

	@verifies("URS-PLT-002")
	def test_change_does_not_touch_modified(self):
		modified = frappe.db.get_value("Desktop Icon", "Projects", "modified")
		frappe.db.set_value("Desktop Icon", "Projects", "hidden", 0, update_modified=False)

		set_desktop_icon_visibility("Projects", visible=False)

		self.assertEqual(frappe.db.get_value("Desktop Icon", "Projects", "modified"), modified)

	@verifies("URS-PLT-002")
	def test_unknown_icon_is_ignored(self):
		set_desktop_icon_visibility("No Such Icon", visible=False)


# A user's saved desk in which they re-showed Projects and hid CRM.
CUSTOMISED_LAYOUT = [
	{"label": "Stock", "hidden": 0, "idx": 1},
	{"label": "Projects", "hidden": 0, "idx": 2},
	{"label": "CRM", "hidden": 1, "idx": 3},
]


def saved_layout(user: str = "Administrator") -> list[dict]:
	return json.loads(frappe.db.get_value("Desktop Layout", user, "layout"))


@verifies("URS-PLT-001")
class TestSavedDesktopLayouts(IntegrationTestCase):
	def setUp(self):
		frappe.delete_doc_if_exists("Desktop Layout", "Administrator")

	def assert_enforced(self, layout: list[dict]):
		self.assertEqual(
			layout,
			[
				{"label": "Stock", "hidden": 0, "idx": 1},
				{"label": "Projects", "hidden": 1, "idx": 2},
				{"label": "CRM", "hidden": 0, "idx": 3},
			],
		)

	def test_saving_a_layout_enforces_declared_visibility(self):
		frappe.get_doc(
			{"doctype": "Desktop Layout", "user": "Administrator", "layout": json.dumps(CUSTOMISED_LAYOUT)}
		).insert()

		self.assert_enforced(saved_layout())

	def test_migrate_enforces_on_layouts_saved_earlier(self):
		frappe.get_doc({"doctype": "Desktop Layout", "user": "Administrator", "layout": "[]"}).insert()
		frappe.db.set_value("Desktop Layout", "Administrator", "layout", json.dumps(CUSTOMISED_LAYOUT))
		modified = frappe.db.get_value("Desktop Layout", "Administrator", "modified")

		apply_desk_configuration()

		self.assert_enforced(saved_layout())
		self.assertEqual(frappe.db.get_value("Desktop Layout", "Administrator", "modified"), modified)

	def test_layouts_without_declared_changes_are_left_alone(self):
		for layout in (None, "", "{}", "[]", "not json", '[1, {"label": "Stock", "hidden": 0}]'):
			with self.subTest(layout=layout):
				self.assertIsNone(enforce_declared_visibility(layout))
