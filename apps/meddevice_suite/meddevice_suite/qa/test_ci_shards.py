"""CI runs the UI tests in parallel shards (scripts/ci/ui_shard.py): every spec in exactly one
shard, so splitting the queue for speed never leaves a UI test unrun."""

import importlib.util
from pathlib import Path

from frappe.tests import UnitTestCase

from meddevice_suite.qa import verifies

SHARDS = Path(__file__).resolve().parents[4] / "scripts" / "ci" / "ui_shard.py"


def shard_plan():
	spec = importlib.util.spec_from_file_location("ui_shard", SHARDS)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


@verifies("URS-PLT-003")
class TestEveryUITestRunsInCI(UnitTestCase):
	def test_every_spec_is_in_exactly_one_shard(self):
		plan = shard_plan()
		self.assertEqual(plan.problems(), [])
		run = [spec for shard in range(1, plan.SHARDS + 1) for spec in plan.specs_of(shard)]
		self.assertEqual(sorted(run), plan.all_specs())

	def test_a_new_spec_lands_in_the_last_shard(self):
		plan = shard_plan()
		named = {spec for specs in plan.NAMED.values() for spec in specs}
		self.assertTrue(set(plan.specs_of(plan.SHARDS)).isdisjoint(named))
