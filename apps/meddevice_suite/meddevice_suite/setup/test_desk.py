import hashlib
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.desk import (
	DESKTOP_ICON_VISIBILITY,
	apply_desk_configuration,
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
