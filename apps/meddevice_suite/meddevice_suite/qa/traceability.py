"""Generate and check the requirement traceability matrix.

Reads the registry (requirements.toml) and the test sources without importing
them, so it needs only the standard library and runs the same way locally, in
pre-commit and in CI.

	python -m meddevice_suite.qa.traceability --check
	python -m meddevice_suite.qa.traceability --junit reports/junit.xml --output reports/traceability.md --check
"""

import argparse
import ast
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = APP_ROOT / "meddevice_suite"
UI_TEST_ROOT = APP_ROOT / "cypress" / "integration"
REGISTRY_PATH = Path(__file__).with_name("requirements.toml")

REQUIREMENT_ID = re.compile(r"^URS-[A-Z]{2,5}-\d{3}$")
UI_TEST_TITLE = re.compile(r"""\bit\(\s*(["'`])(?P<title>.*?)\1""")
UI_REQUIREMENT_TAG = re.compile(r"\[(URS-[A-Z]{2,5}-\d{3})\]")
XML_DECLARATION = re.compile(r"(?=<\?xml)")
OUTCOME_SEVERITY = {"passed": 0, "skipped": 1, "failed": 2}
APPROACHES = {"ready", "customise", "build"}
STATUSES = {"planned", "implemented", "withdrawn"}
PLATFORM = 0


class TraceabilityError(ValueError):
	"""The registry or a test marker is malformed."""


@dataclass(frozen=True)
class Module:
	number: int
	title: str
	approach: str


@dataclass(frozen=True)
class Requirement:
	id: str
	module: int
	title: str
	source: str
	status: str


@dataclass(frozen=True)
class Registry:
	modules: dict[int, Module]
	requirements: dict[str, Requirement]


@dataclass(frozen=True)
class VerifyingTest:
	id: str
	kind: str  # "python" or "ui"
	requirement_ids: tuple[str, ...]


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
	data = tomllib.loads(path.read_text())
	problems: list[str] = []

	modules: dict[int, Module] = {}
	for entry in data.get("module", []):
		module = Module(entry["number"], entry["title"], entry["approach"])
		if module.number in modules:
			problems.append(f"module {module.number}: listed more than once")
		if module.approach not in APPROACHES:
			problems.append(f"module {module.number}: unknown approach {module.approach!r}")
		modules[module.number] = module

	requirements: dict[str, Requirement] = {}
	for entry in data.get("requirement", []):
		requirement = Requirement(
			entry["id"], entry["module"], entry["title"], entry["source"], entry["status"]
		)
		if not REQUIREMENT_ID.match(requirement.id):
			problems.append(f"{requirement.id}: id must look like URS-ABC-001")
		if requirement.id in requirements:
			problems.append(f"{requirement.id}: listed more than once")
		if requirement.module != PLATFORM and requirement.module not in modules:
			problems.append(f"{requirement.id}: unknown module {requirement.module}")
		if requirement.status not in STATUSES:
			problems.append(f"{requirement.id}: unknown status {requirement.status!r}")
		requirements[requirement.id] = requirement

	if problems:
		raise TraceabilityError("\n".join(problems))
	return Registry(modules, requirements)


def collect_python_tests(package_root: Path = PACKAGE_ROOT) -> list[VerifyingTest]:
	"""Tests marked with ``@verifies``; a marker on a class covers all its tests."""
	tests: list[VerifyingTest] = []
	for path in sorted(package_root.rglob("test_*.py")):
		module_name = ".".join(path.relative_to(package_root.parent).with_suffix("").parts)
		tree = ast.parse(path.read_text(), filename=str(path))

		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
				if ids := _verified_ids(node, path):
					tests.append(VerifyingTest(f"{module_name}.{node.name}", "python", ids))

			elif isinstance(node, ast.ClassDef):
				class_ids = _verified_ids(node, path)
				for item in node.body:
					if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
						if ids := class_ids + _verified_ids(item, path):
							test_id = f"{module_name}.{node.name}.{item.name}"
							tests.append(VerifyingTest(test_id, "python", ids))
	return tests


def _verified_ids(node: ast.FunctionDef | ast.ClassDef, path: Path) -> tuple[str, ...]:
	ids: list[str] = []
	for decorator in node.decorator_list:
		if not (isinstance(decorator, ast.Call) and _callable_name(decorator.func) == "verifies"):
			continue
		for argument in decorator.args:
			if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
				raise TraceabilityError(
					f"{path}:{decorator.lineno}: verifies() arguments must be string literals"
				)
			ids.append(argument.value)
	return tuple(ids)


def _callable_name(node: ast.expr) -> str | None:
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		return node.attr
	return None


def collect_ui_tests(ui_test_root: Path = UI_TEST_ROOT) -> list[VerifyingTest]:
	"""UI tests whose title carries requirement tags, e.g. ``it("[URS-PLT-001] ...")``."""
	tests: list[VerifyingTest] = []
	if not ui_test_root.is_dir():
		return tests

	for path in sorted(ui_test_root.rglob("*.js")):
		for match in UI_TEST_TITLE.finditer(path.read_text()):
			title = match["title"]
			if ids := tuple(UI_REQUIREMENT_TAG.findall(title)):
				test_id = f"{path.relative_to(ui_test_root.parent.parent)}::{title}"
				tests.append(VerifyingTest(test_id, "ui", ids))
	return tests


def load_junit_results(path: Path) -> dict[str, str]:
	"""Map ``module.Class.test`` to passed, failed or skipped.

	``bench run-tests`` writes one XML document per test category (unit,
	integration) into the same file, so every document in it is read. A failing
	subtest is reported as its own case, named ``test_x (param=value)``; it
	counts against ``test_x``, and the worst outcome of a test's cases wins.
	"""
	documents = [doc for doc in XML_DECLARATION.split(path.read_text()) if doc.strip()]
	cases = [case for doc in documents for case in ET.fromstring(doc.encode()).iter("testcase")]

	results: dict[str, str] = {}
	for case in cases:
		test_id = f"{case.get('classname')}.{case.get('name').split(' (', 1)[0]}"
		if case.find("failure") is not None or case.find("error") is not None:
			outcome = "failed"
		elif case.find("skipped") is not None:
			outcome = "skipped"
		else:
			outcome = "passed"
		if OUTCOME_SEVERITY[outcome] > OUTCOME_SEVERITY.get(results.get(test_id), -1):
			results[test_id] = outcome
	return results


def find_problems(
	registry: Registry,
	tests: list[VerifyingTest],
	results: dict[str, str] | None = None,
) -> list[str]:
	problems: list[str] = []

	for test in tests:
		for requirement_id in test.requirement_ids:
			requirement = registry.requirements.get(requirement_id)
			if requirement is None:
				problems.append(f"{test.id}: verifies unknown requirement {requirement_id}")
			elif requirement.status == "withdrawn":
				problems.append(f"{test.id}: verifies withdrawn requirement {requirement_id}")

	for requirement in registry.requirements.values():
		if requirement.status != "implemented":
			continue
		verifying = [test for test in tests if requirement.id in test.requirement_ids]
		if not verifying:
			problems.append(f"{requirement.id}: implemented but not verified by any test")
			continue
		if results is None:
			continue
		for test in verifying:
			if test.kind != "python":
				continue  # UI results are reported by the UI test job
			outcome = results.get(test.id)
			if outcome is None:
				problems.append(f"{requirement.id}: {test.id} has no result in the test report")
			elif outcome != "passed":
				problems.append(f"{requirement.id}: {test.id} {outcome}")

	return problems


def render_markdown(
	registry: Registry,
	tests: list[VerifyingTest],
	results: dict[str, str] | None = None,
) -> str:
	approaches = Counter(module.approach for module in registry.modules.values())
	modules_by_approach = ", ".join(f"{name} {approaches[name]}" for name in ("ready", "customise", "build"))
	statuses = Counter(requirement.status for requirement in registry.requirements.values())
	requirements_by_status = ", ".join(
		f"{name} {statuses[name]}" for name in ("implemented", "planned", "withdrawn")
	)

	lines = [
		"# Requirement traceability matrix",
		"",
		f"Modules in scope: {len(registry.modules)} ({modules_by_approach})  ",
		f"Requirements: {len(registry.requirements)} ({requirements_by_status})  ",
		f"Test results: {'included' if results is not None else 'not included'}",
		"",
		"## Requirements",
		"",
		"| Requirement | Module | Status | Verified by (result) |",
		"| --- | --- | --- | --- |",
	]

	for requirement in registry.requirements.values():
		module = "platform" if requirement.module == PLATFORM else str(requirement.module)
		verifying = [test for test in tests if requirement.id in test.requirement_ids]
		evidence = "<br>".join(f"`{test.id}` ({test.kind}): {_result(test, results)}" for test in verifying)
		lines.append(
			f"| **{requirement.id}** {requirement.title} | {module} | {requirement.status} "
			f"| {evidence or '—'} |"
		)

	lines += ["", "## Modules", "", "| # | Module | Approach | Requirements implemented |"]
	lines.append("| --- | --- | --- | --- |")
	for module in registry.modules.values():
		owned = [r for r in registry.requirements.values() if r.module == module.number]
		implemented = sum(r.status == "implemented" for r in owned)
		lines.append(
			f"| {module.number} | {module.title} | {module.approach} | {implemented} / {len(owned)} |"
		)

	return "\n".join(lines) + "\n"


def _result(test: VerifyingTest, results: dict[str, str] | None) -> str:
	if test.kind != "python":
		return "UI job"
	if results is None:
		return "not run"
	return results.get(test.id, "missing")


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("--junit", type=Path, help="JUnit XML report of the Python test run")
	parser.add_argument("--output", type=Path, help="write the Markdown matrix here")
	parser.add_argument("--check", action="store_true", help="exit 1 if traceability is incomplete")
	args = parser.parse_args(argv)

	try:
		registry = load_registry(REGISTRY_PATH)
		tests = collect_python_tests(PACKAGE_ROOT) + collect_ui_tests(UI_TEST_ROOT)
	except TraceabilityError as error:
		print(error, file=sys.stderr)
		return 1
	results = load_junit_results(args.junit) if args.junit else None

	matrix = render_markdown(registry, tests, results)
	if args.output:
		args.output.parent.mkdir(parents=True, exist_ok=True)
		args.output.write_text(matrix)
	elif not args.check:
		print(matrix, end="")

	problems = find_problems(registry, tests, results)
	for problem in problems:
		print(f"traceability: {problem}", file=sys.stderr)
	return 1 if args.check and problems else 0


if __name__ == "__main__":
	sys.exit(main())
