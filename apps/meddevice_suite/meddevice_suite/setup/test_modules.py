from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.modules import APP, sync_module_defs


def app_modules() -> set[str]:
	return set(frappe.get_all("Module Def", filters={"app_name": APP}, pluck="name"))


@verifies("URS-PLT-005")
class TestModuleSync(IntegrationTestCase):
	def tearDown(self):
		# Some tests change the module list; rebuild Frappe's cached map from the real files.
		frappe.cache.delete_value("app_modules")
		frappe.setup_module_map()

	def test_site_matches_modules_txt(self):
		sync_module_defs()

		self.assertEqual(app_modules(), set(frappe.get_module_list(APP)))

	def test_a_newly_declared_module_is_created(self):
		real_module_list = frappe.get_module_list

		def module_list(app):
			return [*real_module_list(app), "Lot Control"] if app == APP else real_module_list(app)

		with patch("frappe.get_module_list", side_effect=module_list):
			sync_module_defs()

		self.assertIn("Lot Control", app_modules())

	def test_an_undeclared_empty_module_is_removed(self):
		frappe.get_doc({"doctype": "Module Def", "module_name": "Placeholder", "app_name": APP}).db_insert()

		sync_module_defs()

		self.assertNotIn("Placeholder", app_modules())

	def test_an_undeclared_module_that_still_owns_records_is_kept(self):
		frappe.get_doc({"doctype": "Module Def", "module_name": "Placeholder", "app_name": APP}).db_insert()

		with patch("meddevice_suite.setup.modules.owns_anything", return_value=True):
			sync_module_defs()

		self.assertIn("Placeholder", app_modules())

	def test_a_stale_cached_module_map_is_rebuilt(self):
		frappe.local.app_modules = {**frappe.local.app_modules, APP: ["removed_module"]}

		sync_module_defs()

		self.assertEqual(
			frappe.local.app_modules[APP], [frappe.scrub(module) for module in frappe.get_module_list(APP)]
		)

	def test_other_apps_modules_are_untouched(self):
		before = frappe.get_all("Module Def", filters={"app_name": ["!=", APP]}, pluck="name")

		sync_module_defs()

		self.assertEqual(
			frappe.get_all("Module Def", filters={"app_name": ["!=", APP]}, pluck="name"), before
		)
