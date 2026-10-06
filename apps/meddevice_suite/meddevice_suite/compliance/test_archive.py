import csv
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_years, now_datetime, today

from meddevice_suite.compliance.archive import INDEX, LOGINS, MANIFEST, verify
from meddevice_suite.qa import verifies
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.test_roles import user_with_profile


def new_supplier(name: str):
	return frappe.get_doc(
		{"doctype": "Supplier", "supplier_name": name, "supplier_group": "Raw Material"}
	).insert()


def archive_of(from_date=None, to_date=None):
	return frappe.get_doc(
		{"doctype": "Record Archive", "from_date": from_date or today(), "to_date": to_date or today()}
	).insert()


def archive_path(archive) -> Path:
	return Path(frappe.get_site_path("private", "files", Path(archive.archive_file).name))


def read_index(path: Path) -> list[dict]:
	with zipfile.ZipFile(path) as archive:
		return list(csv.DictReader(io.StringIO(archive.read(INDEX).decode())))


def rewrite(path: Path, change) -> None:
	"""Rebuild the archive file with `change(entries)` applied, as someone tampering with it would."""
	with zipfile.ZipFile(path) as archive:
		entries = {name: archive.read(name) for name in archive.namelist()}
	change(entries)
	with zipfile.ZipFile(path, "w") as archive:
		for name, data in entries.items():
			archive.writestr(name, data)


class ArchiveTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")


@verifies("URS-CMP-025")
class TestArchiveContent(ArchiveTestCase):
	def test_every_record_changed_in_the_period_is_archived_with_its_history(self):
		supplier = new_supplier("MDS Archive Supplier A")
		supplier.supplier_group = "Local"
		supplier.save()

		archive = archive_of()
		archive.reload()

		self.assertEqual(archive.status, "Completed")
		row = next(row for row in read_index(archive_path(archive)) if row["name"] == supplier.name)
		with zipfile.ZipFile(archive_path(archive)) as file:
			content = json.loads(file.read(row["file"]))
		self.assertEqual(content["record"]["supplier_group"], "Local")
		self.assertEqual([event["action"] for event in content["audit_events"]], ["Created", "Updated"])
		self.assertIn("electronic_signatures", content)

	def test_records_last_changed_outside_the_period_are_left_out(self):
		old = new_supplier("MDS Archive Supplier Old")
		frappe.db.set_value(
			"Supplier", old.name, "modified", add_years(now_datetime(), -2), update_modified=False
		)

		archive = archive_of()
		archive.reload()

		self.assertNotIn(old.name, {row["name"] for row in read_index(archive_path(archive))})

	def test_the_login_history_and_manifest_are_included(self):
		frappe.get_doc(
			{
				"doctype": "Activity Log",
				"subject": "Archive test login",
				"operation": "Login",
				"status": "Success",
			}
		).insert(ignore_permissions=True)

		archive = archive_of()
		archive.reload()

		with zipfile.ZipFile(archive_path(archive)) as file:
			self.assertIn("Archive test login", file.read(LOGINS).decode())
			manifest = json.loads(file.read(MANIFEST))
		self.assertEqual(set(manifest["files"]), {INDEX, LOGINS})
		self.assertEqual(manifest["archive"], archive.name)
		self.assertEqual(sum(manifest["record_counts"].values()), archive.record_count)

	def test_large_periods_are_read_page_by_page_without_gaps_or_repeats(self):
		names = [new_supplier(f"MDS Archive Supplier Page {index}").name for index in range(5)]
		for index in range(3):
			frappe.get_doc(
				{
					"doctype": "Activity Log",
					"subject": f"Paged login {index}",
					"operation": "Login",
					"status": "Success",
				}
			).insert(ignore_permissions=True)

		with patch("meddevice_suite.compliance.archive.PAGE_SIZE", 2):
			archive = archive_of()
		archive.reload()

		archived = [
			row["name"] for row in read_index(archive_path(archive)) if row["record_type"] == "Supplier"
		]
		self.assertEqual(len(archived), len(set(archived)))
		self.assertLessEqual(set(names), set(archived))
		with zipfile.ZipFile(archive_path(archive)) as file:
			logins = file.read(LOGINS).decode()
		self.assertEqual([logins.count(f"Paged login {index}") for index in range(3)], [1, 1, 1])

	def test_records_stay_in_the_system(self):
		supplier = new_supplier("MDS Archive Supplier Kept")

		archive_of()

		self.assertTrue(frappe.db.exists("Supplier", supplier.name))


@verifies("URS-CMP-026")
class TestArchiveIntegrity(ArchiveTestCase):
	def setUp(self):
		super().setUp()
		new_supplier(f"MDS Archive Supplier {frappe.generate_hash(length=6)}")
		self.archive = archive_of()
		self.archive.reload()
		self.path = archive_path(self.archive)

	def test_a_new_archive_verifies(self):
		self.assertEqual(verify(self.archive.name), [])
		self.assertEqual(len(self.archive.archive_hash), 64)

	def test_a_changed_record_file_is_detected(self):
		target = read_index(self.path)[0]["file"]
		rewrite(
			self.path,
			lambda entries: entries.__setitem__(target, entries[target].replace(b"1", b"2", 1) + b" "),
		)

		problems = verify(self.archive.name)

		self.assertIn("The archive file has changed since it was made.", problems)
		self.assertIn(f"{target} has changed.", problems)

	def test_removed_and_added_files_are_detected(self):
		target = read_index(self.path)[0]["file"]

		def tamper(entries):
			del entries[target]
			entries["records/supplier/forged.json"] = b"{}"

		rewrite(self.path, tamper)
		problems = verify(self.archive.name)

		self.assertIn(f"{target} is missing from the archive.", problems)
		self.assertIn("records/supplier/forged.json was added to the archive.", problems)

	def test_a_changed_index_or_login_history_is_detected(self):
		rewrite(self.path, lambda entries: entries.__setitem__(LOGINS, entries[LOGINS] + b"forged,line\n"))

		self.assertIn(f"{LOGINS} has changed.", verify(self.archive.name))

	def test_a_missing_or_unreadable_file_is_reported(self):
		self.path.write_bytes(b"not a zip file")
		self.assertIn("The archive file cannot be read.", verify(self.archive.name))

		self.path.unlink()
		self.assertEqual(verify(self.archive.name), ["The archive file is missing."])

	def test_an_archive_cannot_be_changed_or_deleted(self):
		self.archive.to_date = add_days(today(), -1)
		with self.assertRaises(frappe.PermissionError):
			self.archive.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Record Archive", self.archive.name)


@verifies("URS-CMP-025")
class TestArchiveRequests(ArchiveTestCase):
	def test_only_qa_and_system_managers_make_and_verify_archives(self):
		frappe.set_user(user_with_profile("QA Manager"))
		archive = archive_of()

		frappe.set_user(user_with_profile("Sales"))
		with self.assertRaises(frappe.PermissionError):
			archive_of()
		with self.assertRaises(frappe.PermissionError):
			verify(archive.name)

	def test_the_period_must_be_in_the_past_and_in_order(self):
		with self.assertRaises(frappe.ValidationError):
			archive_of(from_date=today(), to_date=add_days(today(), -1))
		with self.assertRaises(frappe.ValidationError):
			archive_of(to_date=add_days(today(), 1))

	def test_an_unfinished_archive_cannot_be_verified(self):
		with patch("meddevice_suite.compliance.archive.build_archive"):
			archive = archive_of()

		with self.assertRaises(frappe.ValidationError):
			verify(archive.name)

	def test_a_failed_archive_says_why(self):
		with patch("meddevice_suite.compliance.archive.write_archive", side_effect=OSError("Disk full")):
			archive = archive_of()
		archive.reload()

		self.assertEqual(archive.status, "Failed")
		self.assertIn("Disk full", archive.error)
