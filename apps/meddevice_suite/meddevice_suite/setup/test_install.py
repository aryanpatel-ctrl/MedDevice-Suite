import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.desk import DESKTOP_ICON_VISIBILITY
from meddevice_suite.setup.install import apply_configuration

ENTRY_POINT = "meddevice_suite.setup.install.apply_configuration"


class TestInstallHooks(UnitTestCase):
	@verifies("URS-PLT-002")
	def test_configuration_runs_on_install_and_every_migrate(self):
		hooks = frappe.get_hooks(app_name="meddevice_suite")

		self.assertEqual(hooks["after_install"], [ENTRY_POINT])
		self.assertEqual(hooks["after_migrate"], [ENTRY_POINT])


class TestApplyConfiguration(IntegrationTestCase):
	@verifies("URS-PLT-001", "URS-PLT-002")
	def test_entry_point_restores_declared_desk(self):
		for label, visible in DESKTOP_ICON_VISIBILITY.items():
			frappe.db.set_value("Desktop Icon", label, "hidden", int(visible))

		apply_configuration()

		for label, visible in DESKTOP_ICON_VISIBILITY.items():
			self.assertEqual(frappe.db.get_value("Desktop Icon", label, "hidden"), int(not visible), label)
