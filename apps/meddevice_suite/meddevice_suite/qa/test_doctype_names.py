"""No record type of this app has the name of one of Frappe, ERPNext or India Compliance (rules section 4).

A record type's name is its table's name. One that repeats an official app's makes the two share a
table and a definition, each migrate syncing one over the other: this app's reminders were the
record type "Reminder", Frappe's too, until 2026-10-16, and every migrate reset what their people
had been told to Frappe's default. This reads the definitions from the source, without a database,
so the clash fails here before it reaches a site.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from frappe.tests import UnitTestCase

from meddevice_suite.compliance.audit_scope import FORMER_NAMES
from meddevice_suite.qa import verifies

APP = "meddevice_suite"
OFFICIAL_APPS = ("frappe", "erpnext", "india_compliance")
APPS_FOLDER = Path(__file__).resolve().parents[3]  # apps/, beside this app's folder

# A record type each official app has, so a missing or moved app fails instead of passing unread.
KNOWN = {"frappe": "DocType", "erpnext": "Item", "india_compliance": "GST Settings"}


def definitions(package: Path) -> Iterator[tuple[str, Path]]:
	"""(name, file) of each record type defined in an app's package, found as Frappe's sync finds
	them: <module>/doctype/<folder>/<folder>.json."""
	for path in sorted(package.glob("**/doctype/*/*.json")):
		if path.stem != path.parent.name:
			continue
		data = json.loads(path.read_text(encoding="utf-8"))
		if isinstance(data, dict) and data.get("doctype") == "DocType" and data.get("name"):
			yield data["name"], path


def names_of(app: str, apps_folder: Path = APPS_FOLDER) -> dict[str, Path]:
	return dict(definitions(apps_folder / app / app))


def clashes(apps_folder: Path = APPS_FOLDER) -> list[str]:
	"""This app's record types named as an official app's, with both definitions."""
	own = names_of(APP, apps_folder)
	found = []
	for app in OFFICIAL_APPS:
		official = names_of(app, apps_folder)
		for name in sorted(own.keys() & official.keys()):
			found.append(
				f"{name}: {own[name].relative_to(apps_folder)} and {official[name].relative_to(apps_folder)}"
			)
	return found


@verifies("URS-PLT-002")
class TestDocTypeNames(UnitTestCase):
	def test_every_official_app_is_read(self):
		for app, name in KNOWN.items():
			with self.subTest(app=app):
				self.assertIn(name, names_of(app))
		self.assertIn("Due Reminder", names_of(APP))

	def test_no_record_type_has_the_name_of_an_official_one(self):
		self.assertEqual(clashes(), [])

	def test_a_former_name_is_no_longer_one_of_this_apps_record_types(self):
		"""The audit trail reads a renamed record type's history under its former names
		(compliance/audit_scope.FORMER_NAMES): each is a record type of this app, and a former name
		names none of them now, or two record types would share one history."""
		own = names_of(APP)
		for doctype, former in FORMER_NAMES.items():
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, own)
				self.assertFalse(set(former) & own.keys())
