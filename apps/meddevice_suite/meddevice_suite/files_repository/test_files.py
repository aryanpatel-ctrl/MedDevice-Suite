"""Important files repository (module 61; URS-PLT-018, URS-PLT-019): tests derived from the
requirements' clauses."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.files_repository import files
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.qms.test_document_control import private_file
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.work_management import reminders


class FilesTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.qa = signer("QA Manager")
		cls.purchase = signer("Purchase")  # uploads licences
		cls.stores_user = signer("Stores")  # reads them
		cls.sales = signer("Sales")  # has no part in them
		cls.administrator = signer("System Administrator")

	def setUp(self):
		frappe.set_user(self.qa)
		tag = frappe.generate_hash(length=5)
		self.group = self.category(
			f"Licences {tag}", is_group=1, readers=["Stock User"], editors=["Purchase User"]
		)
		self.licences = self.category(f"CDSCO {tag}", parent=self.group)  # takes its group's jobs

	def tearDown(self):
		frappe.set_user("Administrator")

	def category(
		self, name: str, parent: str | None = None, is_group: int = 0, readers=(), editors=()
	) -> str:
		frappe.set_user(self.qa)
		return (
			frappe.get_doc(
				{
					"doctype": "File Category",
					"category_name": name,
					"parent_file_category": parent,
					"is_group": is_group,
					"readers": [{"role": role} for role in readers],
					"editors": [{"role": role} for role in editors],
				}
			)
			.insert()
			.name
		)

	def upload(self, user: str | None = None, category: str | None = None, valid_until=None) -> str:
		frappe.set_user(user or self.purchase)
		return (
			frappe.get_doc(
				{
					"doctype": "Important File",
					"title": "Manufacturing licence MD-5",
					"category": category or self.licences,
					"new_file": private_file("Licence, version 1"),
					"new_valid_until": valid_until,
				}
			)
			.insert()
			.name
		)

	def new_version(self, name: str, note: str | None = "Renewed", valid_until=None, user: str | None = None):
		frappe.set_user(user or self.purchase)
		doc = frappe.get_doc("Important File", name)
		doc.new_file = private_file(f"Licence, {note}")
		doc.new_note = note
		doc.new_valid_until = valid_until
		doc.save()
		return doc


@verifies("URS-PLT-018")
class TestCategoriesAndAccess(FilesTestCase):
	def test_qa_keeps_the_categories(self):
		frappe.set_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "File Category", "category_name": "By hand"}).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "not one of the business jobs"):
			self.category(f"Admin {frappe.generate_hash(length=4)}", readers=["System Manager"])

	def test_a_sub_group_takes_its_groups_jobs_unless_it_names_its_own(self):
		known = files.access()
		self.assertEqual(known[self.licences][files.EDITORS], {"Purchase User"})
		own = self.category(
			f"Own {frappe.generate_hash(length=4)}", parent=self.group, readers=["Sales User"]
		)
		self.assertEqual(files.access()[own][files.READERS], {"Sales User"})

	def test_people_see_only_the_files_their_jobs_read(self):
		name = self.upload()
		for user, sees in (
			(self.purchase, True),
			(self.stores_user, True),
			(self.sales, False),
			(self.qa, True),
			(self.administrator, True),
		):
			with self.subTest(user=user):
				frappe.set_user(user)
				self.assertEqual(name in frappe.get_list("Important File", pluck="name", limit=0), sees)
				self.assertEqual(frappe.has_permission("Important File", "read", doc=name), sees)

	def test_only_the_uploading_jobs_or_qa_add_to_a_category(self):
		with self.assertRaises(frappe.PermissionError):
			self.upload(user=self.stores_user)  # reads, does not upload
		self.upload(user=self.qa)
		frappe.set_user(self.administrator)
		self.assertFalse(frappe.has_permission("Important File", "create", user=self.administrator))

	def test_files_are_kept_in_a_category_not_a_group(self):
		with self.assertRaisesRegex(frappe.ValidationError, "not in the group"):
			self.upload(category=self.group)
		self.upload()
		frappe.set_user(self.qa)
		category = frappe.get_doc("File Category", self.licences)
		category.is_group = 1
		with self.assertRaisesRegex(frappe.ValidationError, "holds files"):
			category.save()
		with self.assertRaisesRegex(
			frappe.PermissionError, "holds files"
		):  # past permissions: the category refuses
			frappe.delete_doc("File Category", self.licences, ignore_permissions=True)


@verifies("URS-PLT-019")
class TestVersions(FilesTestCase):
	def test_the_first_upload_is_version_one_fingerprinted_and_attributed(self):
		doc = frappe.get_doc("Important File", self.upload(valid_until=add_days(today(), 400)))
		self.assertEqual((doc.current_version, len(doc.versions)), (1, 1))
		row = doc.versions[0]
		self.assertEqual(len(row.file_sha256), 64)
		self.assertEqual((row.uploaded_by, row.file, doc.current_file), (self.purchase, row.file, row.file))
		self.assertEqual(str(doc.valid_until), str(row.valid_until))

	def test_a_public_file_is_refused(self):
		public = frappe.get_doc(
			{"doctype": "File", "file_name": "public.txt", "content": "x", "is_private": 0}
		).insert(ignore_permissions=True)
		frappe.set_user(self.purchase)
		with self.assertRaisesRegex(frappe.ValidationError, "private"):
			frappe.get_doc(
				{
					"doctype": "Important File",
					"title": "Public",
					"category": self.licences,
					"new_file": public.file_url,
				}
			).insert()

	def test_each_upload_is_the_next_version_and_says_what_changed(self):
		name = self.upload()
		with self.assertRaisesRegex(frappe.ValidationError, "Say what changed"):
			self.new_version(name, note=None)
		doc = self.new_version(name, "Renewed for 2027", valid_until=add_days(today(), 700))
		self.assertEqual([row.version for row in doc.versions], [1, 2])
		self.assertEqual((doc.current_version, doc.current_file), (2, doc.versions[1].file))
		self.assertNotEqual(doc.versions[0].file_sha256, doc.versions[1].file_sha256)

	def test_earlier_versions_and_what_follows_from_them_never_change_by_hand(self):
		name = self.upload()
		self.new_version(name)
		frappe.set_user(self.purchase)
		doc = frappe.get_doc("Important File", name)
		doc.versions[0].note = "Rewritten history"
		with self.assertRaisesRegex(frappe.PermissionError, "kept as they were"):
			doc.save()
		doc = frappe.get_doc("Important File", name)
		doc.versions = doc.versions[1:]
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc = frappe.get_doc("Important File", name)
		doc.current_version = 7
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_a_versions_file_is_evidence_nobody_changes_or_deletes(self):
		name = self.upload()
		file_url = frappe.get_doc("Important File", name).versions[0].file
		file = frappe.get_doc("File", {"file_url": file_url})
		frappe.set_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("File", file.name)

	def test_archived_with_a_reason_never_deleted_and_then_unchanged(self):
		name = self.upload()
		frappe.set_user(self.stores_user)
		with self.assertRaises(frappe.PermissionError):
			files.archive(name, "Not mine to archive")
		frappe.set_user(self.purchase)
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			files.archive(name, " ")
		with self.assertRaisesRegex(frappe.PermissionError, "never deleted"):  # past permissions
			frappe.delete_doc("Important File", name, ignore_permissions=True)
		frappe.set_user(self.administrator)  # may look at every file, not archive one
		with self.assertRaises(frappe.PermissionError):
			files.archive(name, "Tidying up")
		frappe.set_user(self.purchase)
		files.archive(name, "Licence surrendered")
		self.assertEqual(frappe.db.get_value("Important File", name, "status"), files.ARCHIVED)
		with self.assertRaises(frappe.PermissionError):
			self.new_version(name)


@verifies("URS-PLT-019")
class TestValidity(FilesTestCase):
	def reminder(self, name: str) -> frappe._dict | None:
		rows = frappe.get_all(
			"Reminder",
			filters={"reference_doctype": "Important File", "reference_name": name},
			fields=["status", "responsible_roles"],
			order_by="creation desc",
		)
		return rows[0] if rows else None

	def run_reminders(self) -> None:
		frappe.set_user("Administrator")
		reminders.run()

	def test_tracked_by_the_uploading_jobs_until_a_new_version_renews_it(self):
		name = self.upload(valid_until=add_days(today(), 30))
		self.run_reminders()
		raised = self.reminder(name)
		self.assertEqual((raised.status, raised.responsible_roles), ("Open", "Purchase User"))
		self.new_version(name, "Renewed", valid_until=add_days(today(), 800))
		self.run_reminders()
		self.assertEqual(self.reminder(name).status, "Completed")

	def test_the_days_before_are_qas(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		name = self.upload(valid_until=add_days(today(), 30))
		with use_setting("file_validity_warning_days", 20):
			self.run_reminders()
		self.assertIsNone(self.reminder(name))
		with use_setting("file_validity_warning_days", 45):
			self.run_reminders()
		self.assertEqual(self.reminder(name).status, "Open")

	def test_outside_the_days_qa_set_it_is_not_raised_and_archiving_completes_it(self):
		far = self.upload(valid_until=add_days(today(), 200))
		self.run_reminders()
		self.assertIsNone(self.reminder(far))
		near = self.upload(valid_until=add_days(today(), 10))
		self.run_reminders()
		frappe.set_user(self.purchase)
		files.archive(near, "Replaced by a new licence file")
		self.run_reminders()
		self.assertEqual(self.reminder(near).status, "Completed")
