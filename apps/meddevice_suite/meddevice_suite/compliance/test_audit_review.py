from unittest.mock import patch

import frappe
from frappe.utils import add_days, today
from frappe.utils.user import UserPermissions

from meddevice_suite.compliance.audit_timeline import VALUE_LENGTH, change_lines, timeline_content
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase, because
from meddevice_suite.compliance.test_esign import SIGNER, VIEWER, ensure_user
from meddevice_suite.qa import verifies


class ReviewTestCase(AuditTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])
		ensure_user(VIEWER, "Viewer", [])

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def history(self):
		doc = self.new_record()
		doc.qty = 5
		doc.save()
		doc.submit()
		doc.remarks = "Label reprinted"
		because(doc, "Label damaged").save()
		because(doc, "Lot recalled by supplier").cancel()
		return doc


@verifies("URS-CMP-012")
class TestRecordTimeline(ReviewTestCase):
	def test_reviewer_sees_the_record_history_newest_first(self):
		doc = self.history()

		items = timeline_content(AUDITED, doc.name)

		contents = [item["content"] for item in items]
		self.assertEqual(len(items), 5)
		self.assertIn("<strong>Cancelled</strong>", contents[0])
		self.assertIn("Reason: Lot recalled by supplier", contents[0])
		self.assertIn("Remarks: empty → Label reprinted", contents[1])
		self.assertIn("Quantity: 1.0 → 5", contents[3])
		self.assertIn("<strong>Created</strong>", contents[4])
		self.assertTrue(all("/desk/audit-event/" in content for content in contents))

	def test_users_who_may_not_review_the_audit_trail_see_nothing(self):
		doc = self.history()

		frappe.set_user(VIEWER)

		self.assertEqual(timeline_content(AUDITED, doc.name), [])

	def test_record_types_that_are_not_regulated_show_nothing(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "Not a GMP record"}).insert()

		self.assertEqual(timeline_content("ToDo", todo.name), [])

	def test_a_long_history_shows_only_the_latest_events(self):
		doc = self.history()

		with patch("meddevice_suite.compliance.audit_timeline.TIMELINE_LIMIT", 2):
			items = timeline_content(AUDITED, doc.name)

		self.assertEqual(len(items), 2)
		self.assertIn("<strong>Cancelled</strong>", items[0]["content"])

	def test_many_changes_in_one_event_are_shortened(self):
		doc = self.new_record()
		doc.update({"title": "Lot L-009", "qty": 9, "remarks": "Recounted"})
		doc.save()

		with patch("meddevice_suite.compliance.audit_timeline.LINES_PER_EVENT", 1):
			content = timeline_content(AUDITED, doc.name)[0]["content"]

		self.assertIn("and 2 more", content)

	def test_recorded_values_are_shown_as_text_never_as_markup(self):
		doc = self.new_record()
		doc.title = "<img src=x onerror=alert(1)>"
		doc.save()

		content = timeline_content(AUDITED, doc.name)[0]["content"]

		# Frappe already strips the script on save; whatever markup remains stays text.
		self.assertIn("&lt;img", content)
		self.assertNotIn("<img", content)

	def test_the_real_user_is_shown_when_acting_for_someone_else(self):
		with patch.dict(frappe.session.data, {"impersonated_by": SIGNER}):
			doc = self.new_record()

		self.assertIn(
			"Signer Tester, acting as Administrator", timeline_content(AUDITED, doc.name)[0]["content"]
		)

	def test_long_values_are_shortened(self):
		doc = self.new_record()
		doc.remarks = "x" * (VALUE_LENGTH + 20)
		doc.save()

		content = timeline_content(AUDITED, doc.name)[0]["content"]

		self.assertIn("x" * VALUE_LENGTH + "…", content)
		self.assertNotIn("x" * (VALUE_LENGTH + 1), content)

	def test_actions_caused_by_another_record_name_it(self):
		doc, dependent = self.new_record(), self.new_record()
		doc.submit()
		dependent.submit()
		doc.on_cancel = lambda: frappe.get_doc(AUDITED, dependent.name).cancel()

		because(doc).cancel()

		self.assertIn(
			f"Caused by {AUDITED} {doc.name}", timeline_content(AUDITED, dependent.name)[0]["content"]
		)

	def test_renames_and_child_table_rows_are_described(self):
		user_meta = frappe.get_meta("User")
		roles = user_meta.get_translated_label("roles")
		row = {"role": "Stock User"}

		self.assertEqual(
			change_lines({"old_name": "L-1", "new_name": "L-2", "merged": False}, user_meta),
			["Renamed from L-1 to L-2"],
		)
		self.assertEqual(
			change_lines(
				{
					"added": [["roles", row], ["roles", row]],
					"removed": [["roles", row]],
					"row_changed": [["roles", "r1", []]],
				},
				user_meta,
			),
			[f"{roles}: 2 rows added, 1 changed, 1 removed"],
		)

	def test_the_form_offers_the_full_audit_trail_to_reviewers_only(self):
		doc = self.new_record()

		doc.run_method("onload")
		self.assertTrue(doc.get_onload().get("audit_trail"))

		frappe.set_user(VIEWER)
		other = frappe.get_doc(AUDITED, doc.name)
		other.run_method("onload")
		self.assertFalse(other.get_onload().get("audit_trail"))


@verifies("URS-CMP-012")
class TestSearchAndExport(ReviewTestCase):
	def test_reviewer_searches_by_record_user_action_and_date_range(self):
		doc = self.history()
		self.history()  # another record with the same actions

		frappe.set_user(SIGNER)
		found = frappe.get_list(
			"Audit Event",
			filters={
				"reference_doctype": AUDITED,
				"reference_name": doc.name,
				"user": "Administrator",
				"action": "Cancelled",
				"event_at": ["between", [add_days(today(), -1), today()]],
			},
			pluck="reason",
		)

		self.assertEqual(found, ["Lot recalled by supplier"])

	def test_reviewer_has_the_audit_trail_list(self):
		permissions = UserPermissions(SIGNER)
		permissions.build_permissions()

		self.assertIn("Audit Event", permissions.can_read)

	def test_reviewer_may_export_but_never_change(self):
		for permission, allowed in {
			"read": True,
			"report": True,
			"export": True,
			"write": False,
			"delete": False,
		}.items():
			with self.subTest(permission=permission):
				self.assertEqual(bool(frappe.has_permission("Audit Event", permission, user=SIGNER)), allowed)

	def test_users_without_the_reviewer_role_cannot_read_the_audit_trail(self):
		self.history()

		frappe.set_user(VIEWER)

		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Audit Event", limit=1)
