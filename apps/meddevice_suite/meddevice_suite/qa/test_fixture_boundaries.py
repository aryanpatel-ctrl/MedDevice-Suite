"""Tests do not depend on the pilot's demo code.

`meddevice_suite.pilot` loads demo data on the pilot site only and refuses every other site; the
builders tests and the browser journey share live in `meddevice_suite.setup.fixtures`. This check
reads the sources with `ast`, without a database, and fails if a test module outside `pilot/`, the
browser journey (`qa/ui_journey.py`) or a shared fixture module imports from the pilot.
"""

import ast
from pathlib import Path

from frappe.tests import UnitTestCase

PACKAGE = "meddevice_suite"
PILOT = f"{PACKAGE}.pilot"
APP = Path(__file__).resolve().parent.parent  # the meddevice_suite package


def guarded_modules() -> list[Path]:
	"""Every test module outside pilot/, the browser journey and the shared fixture modules."""
	tests = [path for path in APP.rglob("test_*.py") if path.relative_to(APP).parts[0] != "pilot"]
	return sorted({*tests, APP / "qa" / "ui_journey.py", *(APP / "setup" / "fixtures").glob("*.py")})


def module_name(path: Path) -> str:
	return ".".join((PACKAGE, *path.relative_to(APP).with_suffix("").parts))


def resolved(node: ast.ImportFrom, importer: str) -> str:
	"""The absolute module an import-from names, relative imports included."""
	if not node.level:
		return node.module or ""
	base = importer.split(".")[: -node.level]
	return ".".join([*base, node.module] if node.module else base)


def pilot_imports(source: str, importer: str) -> list[str]:
	"""Each import of the pilot package in the source, as `line: module`."""
	found = []
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.Import):
			names = [alias.name for alias in node.names]
		elif isinstance(node, ast.ImportFrom):
			module = resolved(node, importer)
			names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
		else:
			continue
		if any(name == PILOT or name.startswith(f"{PILOT}.") for name in names):
			found.append(f"{node.lineno}: {ast.unparse(node)}")
	return found


class TestFixtureBoundaries(UnitTestCase):
	def test_no_test_module_imports_the_pilot(self):
		modules = guarded_modules()
		self.assertIn(APP / "qa" / "ui_journey.py", modules)
		self.assertIn(APP / "setup" / "fixtures" / "records.py", modules)
		self.assertGreater(len(modules), 50)  # the scan found the app's tests
		offenders = [
			f"{path.relative_to(APP)}:{found}"
			for path in modules
			for found in pilot_imports(path.read_text(encoding="utf-8"), module_name(path))
		]
		self.assertEqual(offenders, [], "import shared builders from meddevice_suite.setup.fixtures")

	def test_every_form_of_pilot_import_is_caught(self):
		importer = f"{PACKAGE}.lot_control.test_example"
		for source in (
			"import meddevice_suite.pilot",
			"import meddevice_suite.pilot.demo as demo",
			"from meddevice_suite.pilot.flow import lot_of",
			"from meddevice_suite import pilot",
			"from ..pilot import flow",
			"from ..pilot.inspection import readings_for",
			"def f():\n\tfrom meddevice_suite.pilot.demo import ensure",
		):
			with self.subTest(source=source):
				self.assertEqual(len(pilot_imports(source, importer)), 1)

	def test_other_imports_pass(self):
		importer = f"{PACKAGE}.lot_control.test_example"
		for source in (
			"from meddevice_suite.setup.fixtures.records import lot_of",
			"import meddevice_suite.pilotage",
			"from meddevice_suite import pilotage",
			"from . import pilot",  # lot_control.pilot, not the pilot package
		):
			with self.subTest(source=source):
				self.assertEqual(pilot_imports(source, importer), [])
