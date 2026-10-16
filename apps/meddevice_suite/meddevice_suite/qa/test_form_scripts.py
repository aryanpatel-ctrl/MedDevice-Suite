"""The form scripts read a record's fields from `frm.doc`: Frappe's form object has no `docstatus`,
`status` or `name` of its own, so `frm.docstatus` is always undefined and a check on it never holds
(found in the browser walk-through of 2026-10-15: the Lot Release form never showed QA's decision)."""

import re
from pathlib import Path

from frappe.tests import UnitTestCase

from meddevice_suite.qa import verifies

APP = Path(__file__).resolve().parents[1]
MISREAD = re.compile(r"\bfrm\.(docstatus|status|name)\b")


@verifies("URS-QC-003")
class TestFormScriptsReadTheRecord(UnitTestCase):
	def test_no_form_script_reads_a_field_off_the_form_object(self):
		found = [
			f"{path.relative_to(APP)}:{number}: {line.strip()}"
			for path in sorted(APP.rglob("*.js"))
			if "node_modules" not in path.parts and "dist" not in path.parts
			for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
			if MISREAD.search(line)
		]
		self.assertEqual(found, [], "read these from frm.doc:\n" + "\n".join(found))
