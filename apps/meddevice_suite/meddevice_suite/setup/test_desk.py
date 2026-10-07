import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.desk import (
	DESKTOP_ICONS_SHOWN,
	apply_desk_configuration,
	enforce_declared_visibility,
	set_desktop_icon_visibility,
)


def is_visible(label: str) -> bool:
	return frappe.db.get_value("Desktop Icon", label, "hidden") == 0


def icons() -> list[str]:
	return frappe.get_all("Desktop Icon", pluck="name")


def shipped_icon_files() -> dict[str, str]:
	"""Hash of each icon's definition file in its owning app's source tree."""
	hashes = {}
	for label, app in frappe.get_all("Desktop Icon", fields=["name", "app"], as_list=True):
		path = Path(frappe.get_app_path(app, "desktop_icon", f"{frappe.scrub(label)}.json"))
		if path.exists():
			hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
	return hashes


def invert_declared_visibility() -> None:
	for label in icons():
		frappe.db.set_value("Desktop Icon", label, "hidden", int(label in DESKTOP_ICONS_SHOWN))


class TestDeskConfiguration(IntegrationTestCase):
	def assert_declared_visibility(self):
		for label in icons():
			self.assertEqual(is_visible(label), label in DESKTOP_ICONS_SHOWN, label)

	@verifies("URS-PLT-001")
	def test_declared_visibility_is_applied(self):
		invert_declared_visibility()

		apply_desk_configuration()

		self.assert_declared_visibility()

	@verifies("URS-PLT-001")
	def test_an_area_a_future_release_adds_is_hidden(self):
		frappe.get_doc(
			{
				"doctype": "Desktop Icon",
				"label": "Future Area",
				"icon_type": "Link",
				"app": "erpnext",
				"hidden": 0,
			}
		).db_insert()

		apply_desk_configuration()

		self.assertFalse(is_visible("Future Area"))
		for label in ("Manufacturing", "Stock", "Quality", "Buying", "Selling", "Accounting"):
			self.assertTrue(is_visible(label), label)

	@verifies("URS-PLT-001")
	def test_every_declared_area_exists(self):
		# A release that renames an area would otherwise hide it without a word.
		self.assertEqual(DESKTOP_ICONS_SHOWN - set(icons()), set())

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


# A user's saved desk in which they re-showed Projects, hid CRM, and kept an area a
# later release added.
CUSTOMISED_LAYOUT = [
	{"label": "Stock", "hidden": 0, "idx": 1},
	{"label": "Projects", "hidden": 0, "idx": 2},
	{"label": "CRM", "hidden": 1, "idx": 3},
	{"label": "Future Area", "hidden": 0, "idx": 4},
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
				{"label": "Future Area", "hidden": 1, "idx": 4},
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
