import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.boot import get_bootinfo
from frappe.desk.desk_views import DeskViews
from frappe.desk.desktop import Workspace, get_desktop_page, get_workspaces
from frappe.permissions import get_doctypes_with_read
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.desk import (
	DESK_AREAS,
	DESKTOP_ICONS_SHOWN,
	apply_desk_configuration,
	enforce_declared_visibility,
	getpage,
	set_desktop_icon_visibility,
)
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.roles import JOB_PROFILES
from meddevice_suite.setup.test_roles import user_with_profile


def is_visible(label: str) -> bool:
	return frappe.db.get_value("Desktop Icon", label, "hidden") == 0


def icons() -> list[str]:
	return frappe.get_all("Desktop Icon", pluck="name")


def shipped_icon_files() -> dict[str, str]:
	"""Hash of each icon's definition file in its owning app's source tree."""
	hashes = {}
	for label, app in frappe.get_all("Desktop Icon", fields=["name", "app"], as_list=True):
		path = Path(frappe.get_app_path(app, "desktop_icon", f"{frappe.scrub(label)}.json"))
		if path.exists():
			hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
	return hashes


def invert_declared_visibility() -> None:
	for label in icons():
		frappe.db.set_value("Desktop Icon", label, "hidden", int(label in DESKTOP_ICONS_SHOWN))


class TestDeskConfiguration(IntegrationTestCase):
	def assert_declared_visibility(self):
		for label in icons():
			self.assertEqual(is_visible(label), label in DESKTOP_ICONS_SHOWN, label)

	@verifies("URS-PLT-001")
	def test_declared_visibility_is_applied(self):
		invert_declared_visibility()

		apply_desk_configuration()

		self.assert_declared_visibility()

	@verifies("URS-PLT-001")
	def test_an_area_a_future_release_adds_is_hidden(self):
		frappe.get_doc(
			{
				"doctype": "Desktop Icon",
				"label": "Future Area",
				"icon_type": "Link",
				"app": "erpnext",
				"hidden": 0,
			}
		).db_insert()

		apply_desk_configuration()

		self.assertFalse(is_visible("Future Area"))
		for label in ("Manufacturing", "Stock", "Quality", "Buying", "Selling", "Accounting"):
			self.assertTrue(is_visible(label), label)

	@verifies("URS-PLT-001")
	def test_every_declared_area_exists(self):
		# A release that renames an area would otherwise hide it without a word.
		self.assertEqual(DESKTOP_ICONS_SHOWN - set(icons()), set())

	@verifies("URS-PLT-002")
	def test_reapplying_is_idempotent(self):
		apply_desk_configuration()
		apply_desk_configuration()

		self.assert_declared_visibility()

	@verifies("URS-PLT-002")
	def test_owning_apps_source_is_never_modified(self):
		before = shipped_icon_files()
		invert_declared_visibility()

		with patch.dict(frappe.local.conf, {"developer_mode": 1}):
			apply_desk_configuration()

		self.assertEqual(shipped_icon_files(), before)

	@verifies("URS-PLT-002")
	def test_change_does_not_touch_modified(self):
		modified = frappe.db.get_value("Desktop Icon", "Projects", "modified")
		frappe.db.set_value("Desktop Icon", "Projects", "hidden", 0, update_modified=False)

		set_desktop_icon_visibility("Projects", visible=False)

		self.assertEqual(frappe.db.get_value("Desktop Icon", "Projects", "modified"), modified)

	@verifies("URS-PLT-002")
	def test_unknown_icon_is_ignored(self):
		set_desktop_icon_visibility("No Such Icon", visible=False)


# A user's saved desk in which they re-showed Projects, hid CRM, and kept an area a
# later release added.
CUSTOMISED_LAYOUT = [
	{"label": "Stock", "hidden": 0, "idx": 1},
	{"label": "Projects", "hidden": 0, "idx": 2},
	{"label": "CRM", "hidden": 1, "idx": 3},
	{"label": "Future Area", "hidden": 0, "idx": 4},
]


def saved_layout(user: str = "Administrator") -> list[dict]:
	return json.loads(frappe.db.get_value("Desktop Layout", user, "layout"))


@verifies("URS-PLT-001")
class TestSavedDesktopLayouts(IntegrationTestCase):
	def setUp(self):
		frappe.delete_doc_if_exists("Desktop Layout", "Administrator")

	def assert_enforced(self, layout: list[dict]):
		self.assertEqual(
			layout,
			[
				{"label": "Stock", "hidden": 0, "idx": 1},
				{"label": "Projects", "hidden": 1, "idx": 2},
				{"label": "CRM", "hidden": 0, "idx": 3},
				{"label": "Future Area", "hidden": 1, "idx": 4},
			],
		)

	def test_saving_a_layout_enforces_declared_visibility(self):
		frappe.get_doc(
			{"doctype": "Desktop Layout", "user": "Administrator", "layout": json.dumps(CUSTOMISED_LAYOUT)}
		).insert()

		self.assert_enforced(saved_layout())

	def test_migrate_enforces_on_layouts_saved_earlier(self):
		frappe.get_doc({"doctype": "Desktop Layout", "user": "Administrator", "layout": "[]"}).insert()
		frappe.db.set_value("Desktop Layout", "Administrator", "layout", json.dumps(CUSTOMISED_LAYOUT))
		modified = frappe.db.get_value("Desktop Layout", "Administrator", "modified")

		apply_desk_configuration()

		self.assert_enforced(saved_layout())
		self.assertEqual(frappe.db.get_value("Desktop Layout", "Administrator", "modified"), modified)

	def test_layouts_without_declared_changes_are_left_alone(self):
		for layout in (None, "", "{}", "[]", "not json", '[1, {"label": "Stock", "hidden": 0}]'):
			with self.subTest(layout=layout):
				self.assertIsNone(enforce_declared_visibility(layout))


@verifies("URS-PLT-008")
class TestEachJobsDesk(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_every_area_shown_says_which_jobs_work_in_it(self):
		self.assertEqual(set(DESK_AREAS), set(DESKTOP_ICONS_SHOWN))
		for area, roles in DESK_AREAS.items():
			with self.subTest(area=area):
				self.assertEqual({role for role in roles if not frappe.db.exists("Role", role)}, set())

	def test_each_job_sees_only_the_areas_of_its_work(self):
		for profile, roles in JOB_PROFILES.items():
			frappe.set_user(user_with_profile(profile))
			for area, area_roles in DESK_AREAS.items():
				if not frappe.db.exists("Workspace", area):
					continue
				with self.subTest(profile=profile, area=area):
					self.assertEqual(can_open(area), bool(set(roles) & set(area_roles)))

	def test_each_jobs_desk_loads_only_the_areas_of_its_work(self):
		# What the desk itself loads for the person: an area outside this list cannot be reached.
		for profile, roles in JOB_PROFILES.items():
			frappe.set_user(user_with_profile(profile))
			loaded = {page["name"] for page in get_workspaces()["pages"]}
			declared = {area for area, area_roles in DESK_AREAS.items() if set(roles) & set(area_roles)}
			if not loaded & declared:
				# Frappe's empty start page for someone whose areas are sidebars, not dashboards.
				declared.add("Welcome Workspace")
			with self.subTest(profile=profile):
				self.assertLessEqual(loaded, declared)

	def test_each_jobs_desk_icons_are_the_areas_of_its_work(self):
		# The icons the desk shows the person, as Frappe builds them when they sign in.
		for profile, roles in JOB_PROFILES.items():
			frappe.set_user(user_with_profile(profile))
			frappe.cache.delete_key("desktop_icons")
			# Areas as the person sees them: not the hidden app and folder icons that group them.
			icons = {
				icon.label
				for icon in get_bootinfo().desktop_icons
				if icon.icon_type not in ("Folder", "App") and not icon.hidden
			}
			declared = {
				area for area, area_roles in DESK_AREAS.items() if {*roles, "Desk User"} & set(area_roles)
			}
			with self.subTest(profile=profile):
				self.assertLessEqual(icons, declared)
				self.assertIn("Documents", icons)
				self.assertEqual(
					"Compliance" in icons, bool({"Quality Manager", "System Manager"} & set(roles))
				)

	def test_everything_in_a_jobs_areas_opens_for_that_job(self):
		# Frappe shows a System Manager every chart, whether or not they may run its report;
		# for everyone, a chart, card or link they cannot open would greet them with an error.
		for profile, roles in JOB_PROFILES.items():
			user = user_with_profile(profile)
			frappe.set_user(user)
			reports = set(DeskViews.get_allowed_report_names(user=user))
			readable = set(get_doctypes_with_read())
			for area, area_roles in DESK_AREAS.items():
				if not (set(roles) & set(area_roles)) or not frappe.db.exists("Workspace", area):
					continue
				page = get_desktop_page(json.dumps({"name": area, "title": area, "public": 1}))
				for target in workspace_targets(page):
					with self.subTest(profile=profile, area=area, target=target):
						kind, name = target
						self.assertIn(name, reports if kind == "Report" else readable)

	def test_area_roles_changed_on_a_site_are_restored_on_migrate(self):
		# A QC inspector reads records of the buying module, so only the area's roles keep it out.
		frappe.db.delete("Has Role", {"parent": "Buying", "parenttype": "Workspace"})
		frappe.clear_document_cache("Workspace", "Buying")
		frappe.set_user(user_with_profile("QC Inspector"))
		self.assertTrue(can_open("Buying"))

		frappe.set_user("Administrator")
		apply_configuration()

		frappe.set_user(user_with_profile("QC Inspector"))
		self.assertFalse(can_open("Buying"))

	def test_a_record_type_one_may_not_open_is_refused_plainly(self):
		frappe.set_user(user_with_profile("Sales"))

		with self.assertRaisesRegex(frappe.PermissionError, "not permitted to open Stock Entry"):
			getpage("stock-entry")

	def test_a_guest_gets_frappes_own_answer(self):
		frappe.set_user("Guest")

		with self.assertRaises(frappe.DoesNotExistError):
			getpage("stock-entry")

	def test_real_pages_still_load(self):
		frappe.set_user(user_with_profile("Sales"))
		getpage("print")


def can_open(area: str) -> bool:
	"""Whether the session user may open an area's page; Frappe refuses some outright."""
	try:
		return bool(Workspace({"name": area, "title": area, "public": 1}).is_permitted())
	except frappe.PermissionError:
		return False


def workspace_targets(page: dict) -> set[tuple[str, str]]:
	"""What each chart, number card, shortcut and link of an area needs: a report or a record type."""
	targets = set()
	for row in page["charts"]["items"]:
		chart = frappe.get_doc("Dashboard Chart", row.chart_name)
		targets.add(
			("Report", chart.report_name)
			if chart.chart_type == "Report"
			else ("DocType", chart.document_type)
		)
	for row in page["number_cards"]["items"]:
		card = frappe.get_doc("Number Card", row.number_card_name)
		targets.add(
			("Report", card.report_name) if card.type == "Report" else ("DocType", card.document_type)
		)
	links = [
		*page["shortcuts"]["items"],
		*(link for card in page["cards"]["items"] for link in card.get("links", [])),
	]
	for link in links:
		kind, name = link.get("link_type") or link.get("type"), link.get("link_to")
		if kind in ("DocType", "Report") and name:
			targets.add((kind, name))
	return {(kind, name) for kind, name in targets if name}
