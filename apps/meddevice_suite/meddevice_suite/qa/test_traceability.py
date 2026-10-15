import io
import tempfile
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from frappe.tests import UnitTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.qa.traceability import (
	Module,
	Registry,
	Requirement,
	TraceabilityError,
	VerifyingTest,
	collect_python_tests,
	collect_ui_tests,
	find_problems,
	load_junit_results,
	load_registry,
	main,
	render_markdown,
)

VALID_MODULE = '[[module]]\nnumber = 1\ntitle = "Item master"\napproach = "customise"\n'


def requirement_toml(id="URS-ABC-001", module=1, status="implemented", source="DFD 9F F3"):
	return (
		f'[[requirement]]\nid = "{id}"\nmodule = {module}\ntitle = "t"\n'
		f'source = "{source}"\nstatus = "{status}"\n'
	)


def registry(*requirements: Requirement) -> Registry:
	return Registry({1: Module(1, "Item master", "customise")}, {r.id: r for r in requirements})


def requirement(id="URS-ABC-001", status="implemented") -> Requirement:
	return Requirement(id, 1, "t", "s", status)


class TempTree:
	def __init__(self):
		self._dir = tempfile.TemporaryDirectory()
		self.root = Path(self._dir.name)

	def write(self, relative: str, content: str) -> Path:
		path = self.root / relative
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(textwrap.dedent(content))
		return path

	def cleanup(self):
		self._dir.cleanup()


@verifies("URS-PLT-003")
class TestVerifiesMarker(UnitTestCase):
	def test_attaches_requirement_ids(self):
		@verifies("URS-ABC-001", "URS-ABC-002")
		def test():
			pass

		self.assertEqual(test.verifies, ("URS-ABC-001", "URS-ABC-002"))

	def test_markers_accumulate(self):
		@verifies("URS-ABC-002")
		@verifies("URS-ABC-001")
		def test():
			pass

		self.assertEqual(test.verifies, ("URS-ABC-001", "URS-ABC-002"))

	def test_requires_an_id(self):
		with self.assertRaises(ValueError):
			verifies()


@verifies("URS-PLT-003")
class TestRegistry(UnitTestCase):
	def setUp(self):
		self.tree = TempTree()
		self.addCleanup(self.tree.cleanup)

	def load(self, content: str) -> Registry:
		return load_registry(self.tree.write("requirements.toml", content))

	def test_project_registry_lists_the_61_modules_in_scope(self):
		# Module 58, the eCommerce storefront, was withdrawn on 2026-10-09; its number is not reused.
		self.assertEqual(sorted(load_registry().modules), [n for n in range(1, 63) if n != 58])

	def test_loads_valid_registry(self):
		loaded = self.load(VALID_MODULE + requirement_toml() + requirement_toml("URS-PLT-001", 0))

		self.assertEqual(set(loaded.requirements), {"URS-ABC-001", "URS-PLT-001"})

	def test_rejects_malformed_entries(self):
		cases = {
			"malformed id": requirement_toml("REQ-1"),
			"duplicate id": requirement_toml() + requirement_toml(),
			"unknown module": requirement_toml(module=99),
			"unknown status": requirement_toml(status="done"),
			"duplicate module": VALID_MODULE,
			"unknown approach": VALID_MODULE.replace("customise", "buy").replace("1", "2"),
			"source citing nothing traceable": requirement_toml(source="Engineering rules (README)"),
			"source naming a regulation without its clause": requirement_toml(source="ISO 13485"),
			"source citing a DFD step the diagram does not have": requirement_toml(source="DFD 2.7"),
			"source citing a DFD system the diagram does not have": requirement_toml(source="DFD 9G"),
			"source citing a DFD data store the diagram does not have": requirement_toml(source="DFD D13"),
		}
		for case, extra in cases.items():
			with self.subTest(case), self.assertRaises(TraceabilityError):
				self.load(VALID_MODULE + extra)

	def test_untraceable_source_is_reported_with_its_id(self):
		with self.assertRaises(TraceabilityError) as raised:
			self.load(VALID_MODULE + requirement_toml("URS-ABC-007", source="Master plan: real links"))

		self.assertIn("URS-ABC-007: source must cite a DFD step", str(raised.exception))

	def test_accepts_each_kind_of_source(self):
		sources = {
			"DFD step": "DFD 3.4 IQC Inspection",
			"DFD process": "DFD box 2 (Vendor Management)",
			"DFD cross-functional system": "DFD 9F F3 Action / Data Logging",
			"DFD data store": "DFD D12 Audit Logs",
			"client diagram": "D2 Change Control: Impact Assessment",
			"dated decision": "decided 2026-10-11 (plan D-14)",
			"ISO 13485 clause": "ISO 13485 7.6",
			"ISO 14971 clause": "ISO 14971 7.1",
			"Part 11 clause": "21 CFR 11.10(e)",
			"CDSCO MDR": "CDSCO MDR 2017 Fourth Schedule",
		}
		for case, source in sources.items():
			with self.subTest(case):
				loaded = self.load(VALID_MODULE + requirement_toml(source=source))
				self.assertEqual(loaded.requirements["URS-ABC-001"].source, source)


@verifies("URS-PLT-003")
class TestCollectors(UnitTestCase):
	def setUp(self):
		self.tree = TempTree()
		self.addCleanup(self.tree.cleanup)

	def test_class_marker_covers_each_test_and_method_markers_add(self):
		self.tree.write(
			"pkg/area/test_thing.py",
			"""
			@verifies("URS-ABC-001")
			class TestThing:
				def test_a(self): ...

				@verifies("URS-ABC-002")
				def test_b(self): ...

				def helper(self): ...
			""",
		)

		tests = collect_python_tests(self.tree.root / "pkg")

		self.assertEqual(
			tests,
			[
				VerifyingTest("pkg.area.test_thing.TestThing.test_a", "python", ("URS-ABC-001",)),
				VerifyingTest(
					"pkg.area.test_thing.TestThing.test_b", "python", ("URS-ABC-001", "URS-ABC-002")
				),
			],
		)

	def test_function_markers_and_unmarked_tests(self):
		self.tree.write(
			"pkg/test_functions.py",
			"""
			@qa.verifies("URS-ABC-001")
			@patch("x")
			@decorators[0]
			def test_marked(): ...

			@staticmethod
			def test_unmarked(): ...
			""",
		)

		tests = collect_python_tests(self.tree.root / "pkg")

		self.assertEqual(tests, [VerifyingTest("pkg.test_functions.test_marked", "python", ("URS-ABC-001",))])

	def test_rejects_non_literal_ids(self):
		self.tree.write("pkg/test_bad.py", "@verifies(SOME_ID)\ndef test_x(): ...\n")

		with self.assertRaises(TraceabilityError):
			collect_python_tests(self.tree.root / "pkg")

	def test_ui_tests_are_traced_by_title_tag(self):
		self.tree.write(
			"cypress/integration/desk.js",
			"""
			context("Desk", () => {
				it("[URS-PLT-001] hides out-of-scope areas", () => {});
				it('shows the login page', () => {});
			});
			""",
		)

		tests = collect_ui_tests(self.tree.root / "cypress" / "integration")

		self.assertEqual(
			tests,
			[
				VerifyingTest(
					"cypress/integration/desk.js::[URS-PLT-001] hides out-of-scope areas",
					"ui",
					("URS-PLT-001",),
				)
			],
		)

	def test_missing_ui_test_folder_means_no_ui_tests(self):
		self.assertEqual(collect_ui_tests(self.tree.root / "absent"), [])


@verifies("URS-PLT-003")
class TestFindProblems(UnitTestCase):
	PASSING = VerifyingTest("pkg.test_x.T.test_a", "python", ("URS-ABC-001",))

	def test_complete_traceability_has_no_problems(self):
		problems = find_problems(registry(requirement()), [self.PASSING], {self.PASSING.id: "passed"})

		self.assertEqual(problems, [])

	def test_reports_each_gap(self):
		cases = {
			"unknown requirement": (
				registry(),
				[self.PASSING],
				None,
				"verifies unknown requirement URS-ABC-001",
			),
			"withdrawn requirement": (
				registry(requirement(status="withdrawn")),
				[self.PASSING],
				None,
				"verifies withdrawn requirement",
			),
			"implemented without test": (
				registry(requirement()),
				[],
				None,
				"implemented but not verified by any test",
			),
			"failed test": (
				registry(requirement()),
				[self.PASSING],
				{self.PASSING.id: "failed"},
				"failed",
			),
			"skipped test": (
				registry(requirement()),
				[self.PASSING],
				{self.PASSING.id: "skipped"},
				"skipped",
			),
			"test missing from report": (
				registry(requirement()),
				[self.PASSING],
				{},
				"has no result in the test report",
			),
		}
		for case, (reg, tests, results, expected) in cases.items():
			with self.subTest(case):
				problems = find_problems(reg, tests, results)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(expected, problems[0])

	def test_planned_requirement_needs_no_test_yet(self):
		self.assertEqual(find_problems(registry(requirement(status="planned")), []), [])

	def test_ui_tests_are_not_judged_by_the_python_report(self):
		ui_test = VerifyingTest("cypress/integration/desk.js::x", "ui", ("URS-ABC-001",))

		self.assertEqual(find_problems(registry(requirement()), [ui_test], {}), [])


@verifies("URS-PLT-003")
class TestReports(UnitTestCase):
	def setUp(self):
		self.tree = TempTree()
		self.addCleanup(self.tree.cleanup)

	def test_reads_junit_outcomes(self):
		report = self.tree.write(
			"junit.xml",
			"""
			<testsuites><testsuite>
				<testcase classname="pkg.test_x.T" name="test_pass"/>
				<testcase classname="pkg.test_x.T" name="test_fail"><failure message="x"/></testcase>
				<testcase classname="pkg.test_x.T" name="test_error"><error message="x"/></testcase>
				<testcase classname="pkg.test_x.T" name="test_skip"><skipped message="x"/></testcase>
			</testsuite></testsuites>
			""",
		)

		self.assertEqual(
			load_junit_results(report),
			{
				"pkg.test_x.T.test_pass": "passed",
				"pkg.test_x.T.test_fail": "failed",
				"pkg.test_x.T.test_error": "failed",
				"pkg.test_x.T.test_skip": "skipped",
			},
		)

	def test_a_failing_subtest_fails_its_test(self):
		report = self.tree.write(
			"junit.xml",
			"""
			<testsuites><testsuite>
				<testcase classname="pkg.test_x.T" name="test_roles"/>
				<testcase classname="pkg.test_x.T" name="test_roles (role='System Manager')"><failure message="x"/></testcase>
				<testcase classname="pkg.test_x.T" name="test_other (case=1)"><skipped message="x"/></testcase>
				<testcase classname="pkg.test_x.T" name="test_other"/>
			</testsuite></testsuites>
			""",
		)

		self.assertEqual(
			load_junit_results(report),
			{"pkg.test_x.T.test_roles": "failed", "pkg.test_x.T.test_other": "skipped"},
		)

	def test_reads_every_document_of_a_multi_category_report(self):
		document = (
			'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites><testsuite>{}</testsuite></testsuites>\n'
		)
		report = self.tree.write(
			"junit.xml",
			document.format('<testcase classname="pkg.test_unit.T" name="test_a"/>')
			+ document.format('<testcase classname="pkg.test_db.T" name="test_b"/>'),
		)

		self.assertEqual(
			load_junit_results(report),
			{"pkg.test_unit.T.test_a": "passed", "pkg.test_db.T.test_b": "passed"},
		)

	def test_matrix_has_one_row_per_requirement_with_all_evidence(self):
		tests = [
			VerifyingTest("pkg.test_x.T.test_a", "python", ("URS-ABC-001",)),
			VerifyingTest("cypress/integration/desk.js::x", "ui", ("URS-ABC-001",)),
		]

		matrix = render_markdown(registry(requirement()), tests, {"pkg.test_x.T.test_a": "passed"})

		self.assertIn(
			"| **URS-ABC-001** t | 1 | implemented | `pkg.test_x.T.test_a` (python): passed"
			"<br>`cypress/integration/desk.js::x` (ui): UI job |",
			matrix,
		)
		self.assertIn("| 1 | Item master | customise | 1 / 1 |", matrix)

	def test_results_marked_not_run_without_a_report(self):
		test = VerifyingTest("pkg.test_x.T.test_a", "python", ("URS-ABC-001",))

		self.assertIn("(python): not run |", render_markdown(registry(requirement()), [test]))

	def test_unverified_requirement_is_visible_in_matrix(self):
		matrix = render_markdown(registry(requirement(status="planned")), [])

		self.assertIn("| **URS-ABC-001** t | 1 | planned | — |", matrix)


@verifies("URS-PLT-003")
class TestCommandLine(UnitTestCase):
	def setUp(self):
		self.tree = TempTree()
		self.addCleanup(self.tree.cleanup)

	def run_main(self, registry_content: str, *argv: str) -> tuple[int, str, str]:
		registry_path = self.tree.write("requirements.toml", registry_content)
		(self.tree.root / "pkg").mkdir(exist_ok=True)
		stdout, stderr = io.StringIO(), io.StringIO()
		with (
			patch("meddevice_suite.qa.traceability.REGISTRY_PATH", registry_path),
			patch("meddevice_suite.qa.traceability.PACKAGE_ROOT", self.tree.root / "pkg"),
			patch("meddevice_suite.qa.traceability.UI_TEST_ROOT", self.tree.root / "ui"),
			redirect_stdout(stdout),
			redirect_stderr(stderr),
		):
			exit_code = main(list(argv))
		return exit_code, stdout.getvalue(), stderr.getvalue()

	def test_prints_matrix_by_default(self):
		exit_code, stdout, _ = self.run_main(VALID_MODULE)

		self.assertEqual(exit_code, 0)
		self.assertIn("# Requirement traceability matrix", stdout)

	def test_check_fails_and_explains_gaps(self):
		exit_code, stdout, stderr = self.run_main(VALID_MODULE + requirement_toml(), "--check")

		self.assertEqual(exit_code, 1)
		self.assertEqual(stdout, "")
		self.assertIn("URS-ABC-001: implemented but not verified by any test", stderr)

	def test_gaps_are_reported_without_failing_when_not_checking(self):
		exit_code, _, stderr = self.run_main(VALID_MODULE + requirement_toml())

		self.assertEqual(exit_code, 0)
		self.assertIn("implemented but not verified", stderr)

	def test_malformed_registry_fails(self):
		exit_code, _, stderr = self.run_main(VALID_MODULE + requirement_toml("bad"), "--check")

		self.assertEqual(exit_code, 1)
		self.assertIn("id must look like", stderr)


@verifies("URS-PLT-003")
class TestProjectTraceability(UnitTestCase):
	def test_every_implemented_requirement_is_verified(self):
		tests = collect_python_tests() + collect_ui_tests()

		self.assertEqual(find_problems(load_registry(), tests), [])

	def test_cli_writes_matrix_and_passes_check(self):
		tree = TempTree()
		self.addCleanup(tree.cleanup)
		output = tree.root / "reports" / "traceability.md"

		self.assertEqual(main(["--check", "--output", str(output)]), 0)
		self.assertIn("# Requirement traceability matrix", output.read_text())
