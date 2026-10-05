import hashlib
import json

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.sidebars import (
	SIDEBAR_ADDITIONS,
	SIDEBAR_SCOPE,
	apply_sidebar_scope,
	has_entries,
	sidebar_rows,
)


def shipped_path(sidebar: str) -> str:
	app = frappe.db.get_value("Workspace Sidebar", sidebar, "app")
	return frappe.get_app_path(app, "workspace_sidebar", f"{frappe.scrub(sidebar)}.json")


def shipped_labels(sidebar: str) -> set[str]:
	with open(shipped_path(sidebar)) as definition:
		return {item["label"] for item in json.load(definition)["items"] if item["type"] == "Link"}


def allowed(sidebar: str) -> set[str]:
	return set(SIDEBAR_SCOPE[sidebar]) | {label for label, _, _ in SIDEBAR_ADDITIONS.get(sidebar, ())}


@verifies("URS-PLT-001")
class TestSidebarScope(IntegrationTestCase):
	def setUp(self):
		apply_sidebar_scope()

	def test_every_kept_entry_exists_in_the_shipped_sidebar(self):
		for sidebar, keep in SIDEBAR_SCOPE.items():
			with self.subTest(sidebar=sidebar):
				self.assertEqual(set(keep) - shipped_labels(sidebar), set())

	def test_only_kept_entries_remain(self):
		for sidebar in SIDEBAR_SCOPE:
			links = {row.label for row in sidebar_rows(sidebar) if row.type == "Link"}
			with self.subTest(sidebar=sidebar):
				self.assertEqual(links, allowed(sidebar))

	def test_no_section_heading_is_left_empty(self):
		for sidebar in SIDEBAR_SCOPE:
			rows = sidebar_rows(sidebar)
			for position, row in enumerate(rows):
				if row.type == "Section Break":
					with self.subTest(sidebar=sidebar, section=row.label):
						self.assertTrue(has_entries(rows, position))

	def test_additions_appear_once(self):
		for sidebar, entries in SIDEBAR_ADDITIONS.items():
			labels = [row.label for row in sidebar_rows(sidebar)]
			for label, _, _ in entries:
				with self.subTest(sidebar=sidebar, entry=label):
					self.assertEqual(labels.count(label), 1)

	def test_additions_are_restored_when_an_update_drops_them(self):
		for sidebar, entries in SIDEBAR_ADDITIONS.items():
			for label, link_type, link_to in entries:
				frappe.db.delete("Workspace Sidebar Item", {"parent": sidebar, "label": label})

				apply_sidebar_scope()

				added = [row for row in sidebar_rows(sidebar) if row.label == label]
				with self.subTest(sidebar=sidebar, entry=label):
					self.assertEqual(len(added), 1)
					self.assertEqual(
						frappe.db.get_value(
							"Workspace Sidebar Item", added[0].name, ["link_type", "link_to"]
						),
						(link_type, link_to),
					)

	def test_entries_brought_back_by_an_update_are_removed_again(self):
		frappe.get_doc(
			{
				"doctype": "Workspace Sidebar Item",
				"parent": "Selling",
				"parenttype": "Workspace Sidebar",
				"parentfield": "items",
				"idx": 999,
				"type": "Link",
				"label": "Loyalty Program",
				"link_type": "DocType",
				"link_to": "Loyalty Program",
			}
		).db_insert()

		apply_sidebar_scope()

		self.assertNotIn("Loyalty Program", {row.label for row in sidebar_rows("Selling")})

	def test_a_section_brought_back_by_an_update_is_removed_with_its_heading(self):
		for idx, row in enumerate(
			({"type": "Section Break", "label": "POS"}, {"type": "Link", "label": "POS Profile", "child": 1}),
			start=900,
		):
			frappe.get_doc(
				{
					"doctype": "Workspace Sidebar Item",
					"parent": "Selling",
					"parenttype": "Workspace Sidebar",
					"parentfield": "items",
					"idx": idx,
					"link_type": "DocType" if row["type"] == "Link" else None,
					"link_to": row["label"] if row["type"] == "Link" else None,
					**row,
				}
			).db_insert()

		apply_sidebar_scope()

		self.assertFalse({"POS", "POS Profile"} & {row.label for row in sidebar_rows("Selling")})

	def test_reapplying_changes_nothing(self):
		before = {sidebar: sidebar_rows(sidebar) for sidebar in SIDEBAR_SCOPE}

		apply_sidebar_scope()

		self.assertEqual({sidebar: sidebar_rows(sidebar) for sidebar in SIDEBAR_SCOPE}, before)

	def test_shipped_definitions_and_timestamps_are_untouched(self):
		def snapshot():
			state = {}
			for sidebar in SIDEBAR_SCOPE:
				with open(shipped_path(sidebar), "rb") as definition:
					digest = hashlib.sha256(definition.read()).hexdigest()
				state[sidebar] = (digest, frappe.db.get_value("Workspace Sidebar", sidebar, "modified"))
			return state

		before = snapshot()
		frappe.get_doc(
			{
				"doctype": "Workspace Sidebar Item",
				"parent": "Stock",
				"parenttype": "Workspace Sidebar",
				"parentfield": "items",
				"idx": 999,
				"type": "Link",
				"label": "Brand",
				"link_type": "DocType",
				"link_to": "Brand",
			}
		).db_insert()

		apply_sidebar_scope()

		self.assertEqual(snapshot(), before)


@verifies("URS-PLT-001")
class TestSectionHeadings(UnitTestCase):
	def test_heading_followed_by_its_entry_has_entries(self):
		rows = [frappe._dict(type="Section Break"), frappe._dict(type="Link", child=1)]
		self.assertTrue(has_entries(rows, 0))

	def test_heading_followed_by_a_top_level_entry_or_nothing_is_empty(self):
		for rows in (
			[frappe._dict(type="Section Break"), frappe._dict(type="Link", child=0)],
			[frappe._dict(type="Section Break"), frappe._dict(type="Section Break")],
			[frappe._dict(type="Section Break")],
		):
			with self.subTest(rows=rows):
				self.assertFalse(has_entries(rows, 0))
