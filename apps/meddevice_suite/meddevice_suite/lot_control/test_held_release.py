"""A held lot's release (plan D-35, decided 2026-10-20): a held lot made by production is released
only after a new accepted final inspection when its item says so, and any held lot may be released
without the inspection its hold requires only as a concession, approved by QA and then by a second
person from Management (URS-QC-003, URS-QC-005)."""

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from meddevice_suite.compliance.esign import verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.lot_status import APPROVED, HOLD, REJECTED, status_of
from meddevice_suite.lot_control.receipt import origin_of
from meddevice_suite.lot_control.release import RELEASE_BY_CONCESSION, pending_concessions
from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import execute as queue
from meddevice_suite.lot_control.test_lot_status import company, lot_item, qty_in, receive, store
from meddevice_suite.lot_control.test_release import ITEM, ReleaseTestCase, made_by_work_order
from meddevice_suite.master_data.item_master import FINISHED_GOOD, REINSPECT_AFTER_HOLD
from meddevice_suite.master_data.test_item_master import ItemMasterTestCase, new_item
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import dual_role_user
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.fixtures.inspection import ensure_inspection_template, readings_for
from meddevice_suite.setup.test_stock import approve_specification_for_test, stock_item
from meddevice_suite.work_management.my_work import waiting_on

# A product re-inspected after a hold (the default), and one QA set not to be.
REINSPECTED_ITEM = "MDS-REL-HELD-FINISHED"
NOT_REINSPECTED_ITEM = "MDS-REL-HELD-NO-REINSPECT"
CONCESSION = "Release by Concession"
APPROVE = "Approve Concession"
JUSTIFICATION = "Label print fault only; the product inside is sealed and conforms (deviation DV-0042)"


class HeldReleaseTestCase(ReleaseTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		lot_item(REINSPECTED_ITEM, "Products")
		# Its final specification (module 6), made before the item is approved for production.
		cls.final_specification = ensure_inspection_template(
			REINSPECTED_ITEM, "Products", approve_specification_for_test
		)
		stock_item(
			NOT_REINSPECTED_ITEM,
			item_group="Products",
			has_batch_no=1,
			create_new_batch=1,
			batch_number_series=f"{NOT_REINSPECTED_ITEM}-.####",
			has_expiry_date=1,
			shelf_life_in_days=365,
			item_defaults=[{"company": company(), "default_warehouse": store("Stores")}],
			**{REINSPECT_AFTER_HOLD: 0},
		)
		cls.management = signer("Management")

	def held_production_lot(self, item: str = REINSPECTED_ITEM) -> str:
		lot = made_by_work_order(item)
		self.decide(lot, "Hold", "Label print fault found at final packing")
		self.assertEqual(status_of([lot])[lot], HOLD)
		return lot

	def final_inspection(self, lot: str, accept: bool = True) -> Document:
		"""QC's final inspection of the lot against the item's final specification, on the manufacture
		entry that made it."""
		frappe.set_user(self.qc)
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Outgoing",
				"reference_type": "Stock Entry",
				"reference_name": origin_of(lot).voucher_no,
				"item_code": REINSPECTED_ITEM,
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": self.qc,
				"readings": readings_for(self.final_specification, self.instrument, within_limits=accept),
			}
		).insert()
		inspection.submit()
		return inspection

	def concession(
		self, lot: str, justification: str | None = JUSTIFICATION, user: str | None = None
	) -> Document:
		"""QA's signed approval of releasing the held lot by concession."""
		record = self.record(lot)
		frappe.set_user(user or self.qa)
		sign_action(
			"Lot Release",
			record.name,
			CONCESSION,
			PASSWORD,
			"Released under deviation DV-0042",
			values={"justification": justification},
		)
		return frappe.get_doc("Lot Release", record.name)

	def approve(self, record: str, user: str) -> Document:
		frappe.set_user(user)
		sign_action("Lot Release", record, APPROVE, PASSWORD, "Deviation reviewed; risk acceptable")
		return frappe.get_doc("Lot Release", record)


@verifies("URS-QC-003")
class TestAHeldProductionLotIsInspectedAgain(HeldReleaseTestCase):
	def test_a_held_production_lot_is_not_released_without_a_new_final_inspection(self):
		lot = self.held_production_lot()
		with self.assertRaisesRegex(frappe.ValidationError, "has no accepted final inspection on"):
			self.decide(lot, "Release", "Looked fine")
		self.assertEqual(status_of([lot])[lot], HOLD)

	def test_it_is_released_after_a_final_inspection_made_after_the_hold(self):
		lot = self.held_production_lot()
		inspection = self.final_inspection(lot)

		decision = self.decide(lot, "Release", "Final inspection after the hold accepted")

		self.assertEqual(
			(decision.decision, decision.quality_inspection, decision.lot_status_before),
			("Release", inspection.name, HOLD),
		)
		self.assertEqual(status_of([lot])[lot], APPROVED)

	def test_a_final_inspection_made_before_the_hold_does_not_count(self):
		lot = made_by_work_order(REINSPECTED_ITEM)
		self.final_inspection(lot)
		self.decide(lot, "Hold", "Label print fault found at final packing")
		with self.assertRaisesRegex(frappe.ValidationError, "made after it was put on hold"):
			self.decide(lot, "Release", "The earlier inspection was accepted")
		self.assertEqual(status_of([lot])[lot], HOLD)

	def test_a_rejected_final_inspection_after_the_hold_does_not_release_it(self):
		lot = self.held_production_lot()
		self.final_inspection(lot, accept=False)
		with self.assertRaisesRegex(frappe.ValidationError, "has no accepted final inspection on"):
			self.decide(lot, "Release", "Rejected, released anyway")

	def test_with_the_setting_off_the_held_lot_is_released_on_the_signature(self):
		lot = self.held_production_lot(NOT_REINSPECTED_ITEM)

		decision = self.decide(lot, "Release", "Label reprinted; no re-inspection set for this item")

		self.assertEqual((decision.decision, decision.quality_inspection), ("Release", None))
		self.assertEqual(status_of([lot])[lot], APPROVED)

	def test_the_queue_says_a_held_production_lot_waits_for_its_final_inspection(self):
		lot = self.held_production_lot()
		waiting = {row["batch_no"]: row["waiting_for"] for row in queue({"item_code": REINSPECTED_ITEM})[1]}
		self.assertEqual(waiting.get(lot), "Final inspection")
		self.final_inspection(lot)
		waiting = {row["batch_no"]: row["waiting_for"] for row in queue({"item_code": REINSPECTED_ITEM})[1]}
		self.assertEqual(waiting.get(lot), "QA release")


@verifies("URS-QC-005")
class TestReleaseByConcession(HeldReleaseTestCase):
	def test_a_concession_needs_its_justification(self):
		lot = self.held_production_lot()
		for justification in (None, "   "):
			with self.subTest(justification=justification):
				with self.assertRaisesRegex(frappe.ValidationError, "Write the justification"):
					self.concession(lot, justification)
		self.assertFalse(pending_concessions([lot]))

	def test_a_concession_is_refused_for_a_lot_not_on_hold(self):
		lot = made_by_work_order(REINSPECTED_ITEM)  # in quarantine
		with self.assertRaisesRegex(frappe.ValidationError, "only a lot on hold is released by concession"):
			self.concession(lot)

	def test_qas_signature_alone_does_not_release_the_lot(self):
		lot = self.held_production_lot()

		record = self.concession(lot)

		self.assertEqual(
			(record.docstatus, record.decision, record.decided_by, record.concession_justification),
			(0, RELEASE_BY_CONCESSION, self.qa, JUSTIFICATION),
		)
		self.assertFalse(record.concession_approved_by)
		self.assertEqual(status_of([lot])[lot], HOLD)
		self.assertEqual(qty_in(lot, store(stores.HOLD)), 1)
		# QA neither decides the draft otherwise nor points it at another lot.
		with self.assertRaisesRegex(frappe.ValidationError, "waits for Management's approval"):
			sign_action("Lot Release", record.name, "Release", PASSWORD, "Now released")
		frappe.set_user("Administrator")
		other = receive(ITEM, 1, store(stores.QUARANTINE))
		frappe.set_user(self.qa)
		record.batch = other
		with self.assertRaises(frappe.PermissionError):
			record.save()
		with self.assertRaisesRegex(frappe.ValidationError, "approved the release of"):
			sign_action(
				"Lot Release", record.name, CONCESSION, PASSWORD, "again", values={"justification": "again"}
			)

	def test_the_same_person_cannot_give_both_signatures(self):
		both = dual_role_user()
		lot = self.held_production_lot()
		record = self.concession(lot, user=both)
		with self.assertRaisesRegex(frappe.ValidationError, "needs a second approver from Management"):
			self.approve(record.name, both)
		self.assertEqual(status_of([lot])[lot], HOLD)
		# Management's My Work does not ask QA's signer to approve it.
		frappe.set_user(both)
		self.assertNotIn(record.name, {item.name for item in waiting_on(both)})

	def test_only_management_gives_the_second_signature(self):
		lot = self.held_production_lot()
		record = self.concession(lot)
		for user in (self.qa, self.qc, self.stores_user, self.purchase):
			with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
				self.approve(record.name, user)
		self.assertEqual(status_of([lot])[lot], HOLD)

	def test_both_signatures_release_the_lot_and_record_who_and_when(self):
		lot = self.held_production_lot()
		record = self.concession(lot)
		# Waiting for Management: in the queue and in Management's My Work.
		waiting = {row["batch_no"]: row["waiting_for"] for row in queue({"item_code": REINSPECTED_ITEM})[1]}
		self.assertEqual(waiting.get(lot), "Management approval of a concession")
		frappe.set_user(self.management)
		self.assertIn(record.name, {item.name for item in waiting_on(self.management)})

		released = self.approve(record.name, self.management)

		self.assertEqual(
			(released.docstatus, released.decision, released.concession_approved_by, released.decided_by),
			(1, RELEASE_BY_CONCESSION, self.management, self.qa),
		)
		self.assertGreaterEqual(
			get_datetime(released.concession_approved_at), get_datetime(released.decided_at)
		)
		self.assertIsNone(released.quality_inspection)
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(frappe.db.get_value("Batch", lot, "mds_lot_release"), released.name)
		self.assertEqual(qty_in(lot, store("Stores")), 1)
		self.assertEqual(qty_in(lot, store(stores.HOLD)), 0)
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Lot Release", "reference_name": released.name},
			fields=["signer", "meaning", "action"],
			order_by="signed_at asc, creation asc",
		)
		self.assertEqual(
			[(s.signer, s.meaning, s.action) for s in signatures],
			[(self.qa, "Approved", CONCESSION), (self.management, "Approved", APPROVE)],
		)
		self.assertTrue(verify_signatures("Lot Release", released.name))
		self.assertFalse(pending_concessions([lot]))
		frappe.set_user("Administrator")
		html = frappe.get_print("Lot Release", released.name, print_format="Lot Release (Signed)")
		self.assertIn("deviation DV-0042", html)

	def test_a_purchased_held_lot_is_released_by_concession_too(self):
		_receipt, lot = self.ready()
		self.decide(lot, "Hold", "Supplier notice on the lot")
		record = self.concession(lot)

		released = self.approve(record.name, self.management)

		self.assertEqual((released.docstatus, released.source_doctype), (1, "Purchase Receipt"))
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(qty_in(lot, store("Stores")), 6)

	def test_a_concession_no_longer_applies_once_the_lot_is_decided_otherwise(self):
		_receipt, lot = self.ready()
		self.decide(lot, "Hold", "Supplier notice on the lot")
		record = self.concession(lot)
		self.decide(lot, "Reject", "Supplier confirmed the lot is affected")
		self.assertFalse(pending_concessions([lot]))
		with self.assertRaisesRegex(frappe.ValidationError, "no longer applies"):
			self.approve(record.name, self.management)
		self.assertEqual(status_of([lot])[lot], REJECTED)

	def test_only_a_concession_qa_approved_is_approved(self):
		lot = self.held_production_lot()
		record = self.record(lot)
		with self.assertRaisesRegex(frappe.ValidationError, "no release by concession approved by QA"):
			self.approve(record.name, self.management)


@verifies("URS-QC-003")
class TestTheSettingIsControlled(ItemMasterTestCase):
	def test_an_item_is_reinspected_after_a_hold_unless_qa_says_otherwise(self):
		self.assertEqual(new_item(FINISHED_GOOD).get(REINSPECT_AFTER_HOLD), 1)

	def test_from_approval_the_setting_changes_only_under_an_approved_change_request(self):
		item = self.approved_item(FINISHED_GOOD)
		with self.assertRaisesRegex(frappe.ValidationError, "is in use: name the approved change request"):
			frappe.client.set_value("Item", item, REINSPECT_AFTER_HOLD, 0)
		self.assertEqual(frappe.db.get_value("Item", item, REINSPECT_AFTER_HOLD), 1)
		# The setting at its default changes nothing QA's approval signed.
		self.assertTrue(verify_signatures("Item", item))
