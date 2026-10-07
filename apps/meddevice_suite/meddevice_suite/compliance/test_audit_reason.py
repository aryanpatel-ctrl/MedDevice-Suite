import json
from unittest.mock import patch

import frappe
from frappe.desk.form.linked_with import cancel_all_linked_docs

from meddevice_suite.compliance.audit import verify_events
from meddevice_suite.compliance.audit_reason import (
	CANCEL_ALL_LINKED,
	MAX_LENGTH,
	reason_for,
	requires_reason,
	stage_reason,
	staged_key,
)
from meddevice_suite.compliance.test_audit import AUDITED, AuditTestCase, because, events_for
from meddevice_suite.compliance.test_esign import SIGNER, VIEWER, ensure_user
from meddevice_suite.qa import verifies


class ReasonTestCase(AuditTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_user(SIGNER, "Signer", ["System Manager"])
		ensure_user(VIEWER, "Viewer", [])

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")

	def submitted_record(self, **values):
		doc = self.new_record(**values)
		doc.submit()
		return doc

	def last_event(self, doc):
		return events_for(AUDITED, doc.name)[-1]


@verifies("URS-CMP-011")
class TestReasonIsRequired(ReasonTestCase):
	def test_cancelling_without_a_reason_is_refused(self):
		doc = self.submitted_record()

		with self.assertRaises(frappe.ValidationError):
			doc.cancel()

		self.assertEqual(frappe.db.get_value(AUDITED, doc.name, "docstatus"), 1)
		self.assertNotIn("Cancelled", [event.action for event in events_for(AUDITED, doc.name)])

	def test_changing_after_submission_without_a_reason_is_refused(self):
		doc = self.submitted_record()
		doc.remarks = "Label reprinted"

		with self.assertRaises(frappe.ValidationError):
			doc.save()

		self.assertIsNone(frappe.db.get_value(AUDITED, doc.name, "remarks"))

	def test_amending_without_a_reason_is_refused(self):
		doc = self.submitted_record()
		because(doc).cancel()
		amended = frappe.copy_doc(doc)
		amended.update({"docstatus": 0, "amended_from": doc.name})

		with self.assertRaises(frappe.ValidationError):
			amended.insert()

		self.assertFalse(frappe.db.exists(AUDITED, {"amended_from": doc.name}))

	def test_a_blank_reason_is_refused(self):
		doc = self.submitted_record()

		with self.assertRaises(frappe.ValidationError):
			because(doc, "   ").cancel()

	def test_skipping_validation_does_not_skip_the_reason(self):
		doc = self.submitted_record()
		doc.flags.ignore_validate = True

		# Without its start hook the action is refused once written, and the request rolls back.
		frappe.db.savepoint("refused_cancel")
		with self.assertRaises(frappe.ValidationError):
			doc.cancel()
		frappe.db.rollback(save_point="refused_cancel")
		self.assertEqual(frappe.db.get_value(AUDITED, doc.name, "docstatus"), 1)

		doc = frappe.get_doc(AUDITED, doc.name)
		doc.flags.ignore_validate = True
		because(doc).cancel()
		self.assertEqual(self.last_event(doc).reason, "Wrong lot entered")

	def test_the_reason_is_stored_in_the_event_and_protected_by_its_fingerprint(self):
		doc = self.submitted_record()
		doc.remarks = "Label reprinted"
		because(doc, "Label damaged").save()
		because(doc, "Lot recalled by supplier").cancel()

		events = events_for(AUDITED, doc.name)
		self.assertEqual(
			[(event.action, event.reason) for event in events[-2:]],
			[("Updated after submission", "Label damaged"), ("Cancelled", "Lot recalled by supplier")],
		)
		self.assertEqual(verify_events(AUDITED, doc.name), [])

		frappe.db.set_value("Audit Event", events[-1].name, "reason", "Routine", update_modified=False)
		self.assertEqual(len(verify_events(AUDITED, doc.name)), 1)

	def test_a_reason_explains_one_action_only(self):
		doc = self.submitted_record()
		doc.remarks = "Label reprinted"
		because(doc).save()

		doc.remarks = "Label reprinted again"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_other_actions_need_no_reason(self):
		doc = self.new_record()
		doc.qty = 3
		doc.save()
		doc.submit()
		self.new_record().discard()

		self.assertEqual([event.reason for event in events_for(AUDITED, doc.name)], [None, None, None])


@verifies("URS-CMP-011")
class TestReasonGivenOnTheForm(ReasonTestCase):
	"""The desk stages the reason with `stage_reason` just before it sends the action."""

	def test_staged_reason_is_used_for_the_cancellation_once(self):
		doc = self.submitted_record()

		stage_reason(AUDITED, doc.name, "cancel", "  Damaged in transit  ")
		doc.cancel()

		self.assertEqual(self.last_event(doc).reason, "Damaged in transit")
		self.assertIsNone(frappe.cache.get_value(staged_key("cancel", AUDITED, doc.name)))
		self.assertIsNone(frappe.cache.get_value(staged_key("cancelling")))

	def test_staged_reason_for_a_change_after_submission(self):
		doc = self.submitted_record()

		stage_reason(AUDITED, doc.name, "update_after_submit", "Label damaged")
		doc.remarks = "Label reprinted"
		doc.save()

		self.assertEqual(self.last_event(doc).reason, "Label damaged")

	def test_staged_reason_for_an_amendment_is_staged_against_the_cancelled_record(self):
		doc = self.submitted_record()
		because(doc).cancel()

		stage_reason(AUDITED, doc.name, "amend", "Quantity corrected")
		amended = frappe.copy_doc(doc)
		amended.update({"docstatus": 0, "amended_from": doc.name, "qty": 2})
		amended.insert()

		self.assertEqual(self.last_event(amended).reason, "Quantity corrected")

	def test_a_reason_staged_for_another_record_does_not_apply(self):
		doc, other = self.submitted_record(), self.submitted_record()

		stage_reason(AUDITED, other.name, "cancel", "Damaged in transit")

		with self.assertRaises(frappe.ValidationError):
			doc.cancel()

	def test_a_reason_staged_by_another_user_does_not_apply(self):
		doc = self.submitted_record()
		stage_reason(AUDITED, doc.name, "cancel", "Damaged in transit")

		frappe.set_user(SIGNER)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(AUDITED, doc.name).cancel()

	def test_staging_needs_permission_for_the_action(self):
		doc = self.submitted_record()

		frappe.set_user(VIEWER)
		with self.assertRaises(frappe.PermissionError):
			stage_reason(AUDITED, doc.name, "cancel", "Damaged in transit")

	def test_staging_refuses_bad_input(self):
		doc = self.submitted_record()
		refused = {
			"unknown action": (AUDITED, doc.name, "delete", "Damaged"),
			"record type that needs no reason": ("Item", "any", "cancel", "Damaged"),
			"blank reason": (AUDITED, doc.name, "cancel", " "),
			"reason too long": (AUDITED, doc.name, "cancel", "x" * (MAX_LENGTH + 1)),
		}
		for case, arguments in refused.items():
			with self.subTest(case=case), self.assertRaises(frappe.ValidationError):
				stage_reason(*arguments)

	def test_only_submittable_regulated_record_types_ask_for_a_reason(self):
		self.assertTrue(requires_reason(AUDITED))
		self.assertFalse(requires_reason("Item"))  # regulated, but never cancelled or amended
		self.assertFalse(requires_reason("ToDo"))  # not regulated


@verifies("URS-CMP-011")
class TestActionsCausedByAnother(ReasonTestCase):
	def test_records_cancelled_by_a_cancellation_take_its_reason(self):
		doc, dependent = self.submitted_record(), self.submitted_record()
		doc.on_cancel = lambda: frappe.get_doc(AUDITED, dependent.name).cancel()

		because(doc, "Lot recalled by supplier").cancel()

		caused = self.last_event(dependent)
		self.assertEqual((caused.action, caused.reason), ("Cancelled", "Lot recalled by supplier"))
		self.assertEqual(caused.changes["caused_by"], [AUDITED, doc.name])
		self.assertNotIn("caused_by", self.last_event(doc).changes)

	def test_a_change_made_while_another_record_is_saved_needs_no_reason_and_names_it(self):
		# As ERPNext does when a job card rolls its completed quantity up to the work order.
		work_order, job_card = self.submitted_record(), self.new_record()

		def roll_up():
			target = frappe.get_doc(AUDITED, work_order.name)
			target.remarks = "Completed quantity rolled up"
			target.save()

		job_card.on_update = roll_up
		job_card.qty = 2
		job_card.save()

		event = self.last_event(work_order)
		self.assertEqual((event.action, event.reason), ("Updated after submission", None))
		self.assertEqual(event.changes["caused_by"], [AUDITED, job_card.name])

	def test_changing_a_submitted_record_by_hand_still_needs_a_reason(self):
		doc = self.submitted_record()
		self.new_record()  # another record saved earlier in the request is no excuse
		doc.remarks = "Changed by hand"

		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_the_cause_ends_with_its_action(self):
		doc, unrelated = self.submitted_record(), self.submitted_record()
		because(doc).cancel()

		with self.assertRaises(frappe.ValidationError):
			unrelated.cancel()

	def test_a_status_command_is_recorded_with_the_command_used(self):
		doc = self.submitted_record()
		self.enterContext(patch.dict(frappe.form_dict, {"cmd": "erpnext.example.update_status"}))

		doc.db_set("remarks", "Closed")

		self.assertEqual(
			events_for(AUDITED, doc.name)[-1].changes["command"], "erpnext.example.update_status"
		)

	def test_a_record_updated_by_another_records_save_names_it_not_the_request(self):
		# A receipt's save marks its purchase order received with db_set, in a request whose
		# command is the receipt's save: the order's event names the receipt, not a command.
		order, receipt = self.submitted_record(), self.submitted_record()
		self.enterContext(patch.dict(frappe.form_dict, {"cmd": "frappe.desk.form.save.savedocs"}))
		receipt.on_update_after_submit = lambda: order.db_set("remarks", "Received")
		because(receipt).save()

		changes = events_for(AUDITED, order.name)[-1].changes
		self.assertEqual(changes["caused_by"], [AUDITED, receipt.name])
		self.assertNotIn("command", changes)

	def test_a_failed_action_leaves_its_reason_to_no_other_record(self):
		# As in a bulk cancel: the first record's cancellation takes its reason and then fails;
		# the request carries on with the next record, which was given no reason.
		failing, next_record = self.submitted_record(), self.submitted_record()
		reason_for(because(failing), "Cancelled")

		with self.assertRaises(frappe.ValidationError):
			next_record.cancel()
		self.assertEqual(frappe.db.get_value(AUDITED, next_record.name, "docstatus"), 1)

	def test_cancel_all_linked_takes_the_reason_staged_for_the_record_being_cancelled(self):
		doc, linked = self.submitted_record(), self.submitted_record()
		stage_reason(AUDITED, doc.name, "cancel", "Supplier lot rejected")

		# As the desk does: the linked records in one request, then the record itself.
		with patch.dict(frappe.local.form_dict, {"cmd": CANCEL_ALL_LINKED}):
			cancel_all_linked_docs(json.dumps([{"doctype": AUDITED, "name": linked.name, "docstatus": 1}]))
		frappe.get_doc(AUDITED, doc.name).cancel()

		caused = self.last_event(linked)
		self.assertEqual(
			(caused.reason, caused.changes["caused_by"]), ("Supplier lot rejected", [AUDITED, doc.name])
		)
		self.assertEqual(self.last_event(doc).reason, "Supplier lot rejected")

	def test_cancel_all_linked_without_a_reason_is_refused(self):
		linked = self.submitted_record()

		with (
			patch.dict(frappe.local.form_dict, {"cmd": CANCEL_ALL_LINKED}),
			self.assertRaises(frappe.ValidationError),
		):
			cancel_all_linked_docs(json.dumps([{"doctype": AUDITED, "name": linked.name, "docstatus": 1}]))
