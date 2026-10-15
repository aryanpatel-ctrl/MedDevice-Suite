"""Checks over the app's form and list scripts, for defects found in the browser walk-through.

The form scripts read a record's fields from `frm.doc`: Frappe's form object has no `docstatus`,
`status` or `name` of its own, so `frm.docstatus` is always undefined and a check on it never holds
(walk-through of 2026-10-15: the Lot Release form never showed QA's decision).

Every server call a script makes with `frappe.xcall` handles the server's refusal. Frappe shows the
refusal's message and then rejects the promise; a chain without a handler leaves the rejection
unhandled ("An unknown error has occurred" in the console, a failed UI test) and a dialog that
disabled its button stays stuck (walk-through of 2026-10-16: Send for Assessment, Withdraw and
Record Action Done on a change request, Start New Revision, Record Training History)."""

import re
from pathlib import Path

from frappe.tests import UnitTestCase

from meddevice_suite.qa import verifies

APP = Path(__file__).resolve().parents[1]
MISREAD = re.compile(r"\bfrm\.(docstatus|status|name)\b")
XCALL = re.compile(r"\bfrappe\s*\.\s*xcall\s*\(")
CATCH = re.compile(r"\.\s*catch\s*\(")
TRY = re.compile(r"\btry\s*$")
AWAIT = re.compile(r"\bawait\s*$")
# A promise handed back to its caller is the caller's to handle; the comment above the call says so.
CALLER_HANDLES = "the caller handles the refusal"
OPENERS, CLOSERS = "([{", ")]}"


def scripts() -> list[Path]:
	return [
		path
		for path in sorted(APP.rglob("*.js"))
		if "node_modules" not in path.parts and "dist" not in path.parts
	]


def code_only(source: str) -> str:
	"""The source with comments and the text of strings blanked to spaces, keeping every offset and
	line break, so that brackets, semicolons and calls are seen only where they are code. The
	expressions inside a template literal's `${…}` stay code."""
	out = list(source)
	modes = ["code"]  # "code", "template"; a `${` inside a template pushes "code" again
	depth = [0]  # brace depth of each code level, to find the `}` that closes a `${`
	i, length = 0, len(source)

	def blank(start: int, end: int) -> None:
		for k in range(start, min(end, length)):
			if out[k] != "\n":
				out[k] = " "

	while i < length:
		char, pair = source[i], source[i : i + 2]
		if modes[-1] == "template":
			if char == "\\":
				blank(i, i + 2)
				i += 2
			elif char == "`":
				modes.pop()
				i += 1
			elif pair == "${":
				modes.append("code")
				depth.append(0)
				i += 2
			else:
				blank(i, i + 1)
				i += 1
			continue
		if pair == "//":
			end = source.find("\n", i)
			end = length if end == -1 else end
			blank(i, end)
			i = end
		elif pair == "/*":
			end = source.find("*/", i + 2)
			end = length if end == -1 else end + 2
			blank(i, end)
			i = end
		elif char in "'\"":
			k = i + 1
			while k < length and source[k] != char and source[k] != "\n":
				k += 2 if source[k] == "\\" else 1
			blank(i + 1, k)
			i = k + 1
		elif char == "`":
			modes.append("template")
			i += 1
		elif char == "{":
			depth[-1] += 1
			i += 1
		elif char == "}" and depth[-1] == 0 and len(modes) > 1:
			modes.pop()  # the `}` closing a template's `${`
			depth.pop()
			i += 1
		else:
			if char == "}":
				depth[-1] -= 1
			i += 1
	return "".join(out)


def chain_end(code: str, start: int) -> int:
	"""Where the expression beginning at `start` ends: a `;` or `,` outside its brackets, or the
	bracket that closes the one it stands in (an arrow function's body passed as an argument)."""
	level = 0
	for k in range(start, len(code)):
		char = code[k]
		if char in OPENERS:
			level += 1
		elif char in CLOSERS:
			if level == 0:
				return k
			level -= 1
		elif char in ";," and level == 0:
			return k
	return len(code)


def outermost(code: str, start: int, end: int) -> str:
	"""The text of the expression outside its own brackets: the chain of calls itself, so that a
	`.catch(` inside a callback does not count for the chain."""
	kept, level = [], 0
	for char in code[start:end]:
		if level == 0:
			kept.append(char)
		if char in OPENERS:
			level += 1
		elif char in CLOSERS:
			level -= 1
	return "".join(kept)


def in_try_block(code: str, position: int) -> bool:
	"""Whether `position` lies inside the block of a `try`."""
	blocks = []  # for each open `{`: whether it opens a try block
	for k in range(position):
		if code[k] == "{":
			blocks.append(bool(TRY.search(code[max(0, k - 16) : k])))
		elif code[k] == "}" and blocks:
			blocks.pop()
	return any(blocks)


def caller_handles(source: str, line: int) -> bool:
	"""Whether the comment lines just above line `line` (1-based) say the caller handles the refusal."""
	lines = source.splitlines()
	comment = []
	for text in reversed(lines[: line - 1]):
		if not text.strip().startswith("//"):
			break
		comment.append(text.strip().lstrip("/").strip())
	return CALLER_HANDLES in " ".join(reversed(comment)).lower()


def unhandled_xcalls(source: str) -> list[int]:
	"""The lines of the `frappe.xcall` calls in `source` whose refusal nothing handles."""
	code = code_only(source)
	found = []
	for match in XCALL.finditer(code):
		start = match.start()
		line = code.count("\n", 0, start) + 1
		if AWAIT.search(code[max(0, start - 16) : start]):
			handled = in_try_block(code, start)
		else:
			handled = bool(CATCH.search(outermost(code, start, chain_end(code, start))))
		if not handled and not caller_handles(source, line):
			found.append(line)
	return found


@verifies("URS-QC-003")
class TestFormScriptsReadTheRecord(UnitTestCase):
	def test_no_form_script_reads_a_field_off_the_form_object(self):
		found = [
			f"{path.relative_to(APP)}:{number}: {line.strip()}"
			for path in scripts()
			for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
			if MISREAD.search(line)
		]
		self.assertEqual(found, [], "read these from frm.doc:\n" + "\n".join(found))


@verifies("URS-QMS-009")
class TestFormScriptsHandleRefusals(UnitTestCase):
	def test_every_server_call_handles_the_refusal(self):
		found = [
			f"{path.relative_to(APP)}:{line}"
			for path in scripts()
			for line in unhandled_xcalls(path.read_text(encoding="utf-8"))
		]
		self.assertEqual(
			found,
			[],
			"end these frappe.xcall chains with .catch(...), await them inside try, or say above the "
			f'call that "{CALLER_HANDLES.capitalize()}":\n' + "\n".join(found),
		)

	def test_the_check_tells_a_handled_call_from_an_unhandled_one(self):
		unhandled = """
			frm.add_custom_button(__("Send"), () =>
				frappe
					.xcall("app.send", { name: frm.doc.name })
					.then(() => frm.reload_doc())
			);
			frappe.xcall(`${APP}.withdraw`, { reason }).then(() => {
				dialog.hide();
				other().catch(() => {});
			});
		"""
		handled = """
			frappe.xcall("app.send", { label: "a ) ; , string" }).then(() => frm.reload_doc()).catch(() => {});
			async function verify() {
				try {
					await frappe.xcall(`${APP}.verify`, { text: `x ${"}"} y` });
				} catch (error) {
					dialog.enable_primary_action();
				}
			}
			const begin = () =>
				// The caller handles the refusal: the dialog stays open.
				frappe.xcall("app.begin");
			// frappe.xcall("app.commented_out");
		"""
		self.assertEqual(unhandled_xcalls(unhandled), [3, 7])
		self.assertEqual(unhandled_xcalls(handled), [])
		self.assertEqual(unhandled_xcalls("async function f() { await frappe.xcall('app.x'); }"), [1])
