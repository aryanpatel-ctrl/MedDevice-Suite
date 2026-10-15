"""One definition of the shared checks (QA_ROLE, require, is_qa).

They live in meddevice_suite.compliance.checks. A module that defined its own copy could drift from
it unnoticed: a different role, a different exception. This reads the app's source, without a
database, and fails when any other module binds one of these names itself. Importing them from the
shared module is fine.
"""

import ast
from pathlib import Path

from frappe.tests import UnitTestCase

APP = Path(__file__).resolve().parent.parent
SHARED = APP / "compliance" / "checks.py"
NAMES = frozenset({"QA_ROLE", "require", "is_qa"})

# Deliberate differences, as (path relative to the app package, name). Each behaves differently
# from the shared definition, so merging it would change behaviour.
ALLOWED = frozenset(
	{
		# Raises WorkflowPermissionError titled "Approval required", not ValidationError.
		("compliance/approvals.py", "require"),
	}
)


def bound_names(tree: ast.Module) -> list[tuple[str, int]]:
	"""The listed names a module binds itself: defined as a function or class, or assigned."""
	found = []
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name in NAMES:
			found.append((node.name, node.lineno))
		elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in NAMES:
			found.append((node.id, node.lineno))
	return found


class TestOneDefinition(UnitTestCase):
	def test_shared_module_defines_every_name(self):
		names = {name for name, _line in bound_names(ast.parse(SHARED.read_text()))}
		self.assertEqual(names, NAMES)

	def test_no_other_module_defines_its_own_copy(self):
		copies = []
		for path in sorted(APP.rglob("*.py")):
			if path == SHARED:
				continue
			relative = path.relative_to(APP).as_posix()
			for name, line in bound_names(ast.parse(path.read_text(), filename=str(path))):
				if (relative, name) not in ALLOWED:
					copies.append(f"{relative}:{line} defines {name}")
		self.assertEqual(copies, [], "import these from meddevice_suite.compliance.checks instead")

	def test_allow_list_has_no_stale_entries(self):
		for relative, name in ALLOWED:
			names = {found for found, _line in bound_names(ast.parse((APP / relative).read_text()))}
			self.assertIn(name, names, f"{relative} no longer defines {name}: remove it from ALLOWED")
