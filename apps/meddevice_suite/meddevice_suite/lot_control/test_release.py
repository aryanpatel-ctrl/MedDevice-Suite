import frappe
from frappe.model.document import Document
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.audit_reason import stage_reason
from meddevice_suite.compliance.esign import verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control import release, stores
from meddevice_suite.lot_control.doctype.lot_release.lot_release import on_doctype_update
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	HOLD,
	QUARANTINE,
	REJECTED,
	STATUS_FIELD,
	set_status,
	status_of,
)
from meddevice_suite.lot_control.receipt import (
	accepted_inspection,
	guard_incoming_inspection,
	receipt_line_of,
	verify_certificate,
)
from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import execute as queue
from meddevice_suite.lot_control.report.lots_awaiting_release.lots_awaiting_release import receipt_lines
from meddevice_suite.lot_control.test_lot_status import (
	SUPPLIER,
	audit_actions,
	company,
	ensure_parties,
	lot_item,
	purchase_receive,
	qty_in,
	receive,
	stock_entry,
	store,
)
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.pilot.inspection import (
	ensure_inspection_masters,
	ensure_inspection_template,
	readings_for,
)
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.install import apply_configuration

ITEM = "MDS-REL-RAW"
FINISHED_ITEM = "MDS-REL-FINISHED"
NO_TEMPLATE_ITEM = "MDS-REL-NO-TEMPLATE"


def certificate(receipt: str, version: str = "") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"COA-{receipt}{version}.txt",
				"content": f"Certificate of analysis (test){version}",
				"is_private": 1,
				"attached_to_doctype": "Purchase Receipt",
				"attached_to_name": receipt,
			}
		)
		.insert()
		.file_url
	)


class ReleaseTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_parties()
		lot_item(ITEM)
		lot_item(NO_TEMPLATE_ITEM)
		cls.instrument = ensure_inspection_masters(company())
		cls.template = ensure_inspection_template(ITEM, "Raw Material")
		cls.qa = signer("QA Manager")
		cls.qc = signer("QC Inspector")
		cls.stores_user = signer("Stores")
		cls.purchase = signer("Purchase")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def received(self, qty: float = 6, with_certificate: bool = True) -> tuple[Document, str]:
		"""A receipt by Stores with the supplier's lot number and certificate on its line."""
		frappe.set_user("Administrator")
		receipt = purchase_receive(ITEM, qty, submit=False)
		receipt.items[0].mds_supplier_lot_no = "SUP-4711"
		if with_certificate:
			receipt.items[0].mds_certificate = certificate(receipt.name)
		receipt.save()
		receipt.submit()
		receipt = frappe.get_doc("Purchase Receipt", receipt.name)
		return receipt, lot_of(receipt.items[0])

	def verified(self, receipt: Document) -> None:
		frappe.set_user(self.qc)
		verify_certificate(receipt.name, receipt.items[0].name)

	def inspected(self, receipt: Document, lot: str, accept: bool = True) -> Document:
		frappe.set_user(self.qc)
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": receipt.name,
				"item_code": ITEM,
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": frappe.session.user,
				"readings": readings_for(self.template, self.instrument, within_limits=accept),
			}
		)
		inspection.insert()
		inspection.submit()
		return inspection

	def ready(self) -> tuple[Document, str]:
		receipt, lot = self.received()
		self.verified(receipt)
		self.inspected(receipt, lot)
		return receipt, lot

	def record(self, lot: str) -> Document:
		frappe.set_user(self.qa)
		return frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()

	def decide(
		self, lot: str, act: str, reason: str = "decided in a test", password: str = PASSWORD
	) -> Document:
		record = self.record(lot)
		sign_action("Lot Release", record.name, act, password, reason)
		return frappe.get_doc("Lot Release", record.name)


@verifies("URS-QC-001")
class TestSupplierCertificate(ReleaseTestCase):
	def test_stores_records_the_suppliers_lot_number_and_certificate_on_the_line(self):
		receipt, _lot = self.received()
		line = receipt.items[0]
		self.assertEqual(line.mds_supplier_lot_no, "SUP-4711")
		self.assertTrue(line.mds_certificate)
		self.assertFalse(line.mds_certificate_verified)

	def test_qc_verifies_the_certificate_and_the_audit_trail_says_so(self):
		receipt, lot = self.received()
		self.verified(receipt)

		line = receipt_line_of(lot)
		self.assertTrue(line.verified)
		stored = frappe.get_doc("Purchase Receipt", receipt.name).items[0]
		self.assertEqual(stored.mds_certificate_verified_by, self.qc)
		self.assertTrue(stored.mds_certificate_verified_at)
		self.assertIn("Updated after submission", audit_actions("Purchase Receipt", receipt.name))
		event = frappe.get_all(
			"Audit Event",
			filters={
				"reference_doctype": "Purchase Receipt",
				"reference_name": receipt.name,
				"action": "Updated after submission",
			},
			fields=["reason", "user"],
			order_by="event_at desc",
			limit=1,
		)[0]
		self.assertEqual((event.user, event.reason), (self.qc, "Supplier certificate verified for line 1"))

	def test_only_qc_verifies_and_only_once_and_only_with_a_certificate(self):
		receipt, _lot = self.received()
		frappe.set_user(self.stores_user)
		with self.assertRaises(frappe.PermissionError):
			verify_certificate(receipt.name, receipt.items[0].name)
		self.verified(receipt)
		with self.assertRaises(frappe.ValidationError) as refused:
			verify_certificate(receipt.name, receipt.items[0].name)
		self.assertIn("already verified", str(refused.exception))
		bare, _lot = self.received(with_certificate=False)
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.ValidationError) as refused:
			verify_certificate(bare.name, bare.items[0].name)
		self.assertIn("no supplier certificate attached", str(refused.exception))

	def test_verification_names_a_real_line_of_a_submitted_receipt(self):
		receipt, _lot = self.received()
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.ValidationError) as refused:
			verify_certificate(receipt.name, "no-such-row")
		self.assertIn("has no line", str(refused.exception))
		frappe.set_user("Administrator")
		draft = purchase_receive(ITEM, 1, submit=False)
		draft.items[0].mds_certificate = certificate(draft.name)
		draft.save()
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.ValidationError) as refused:
			verify_certificate(draft.name, draft.items[0].name)
		self.assertIn("once the receipt is submitted", str(refused.exception))

	def test_a_lot_that_no_supplier_delivered_has_no_receipt_line(self):
		frappe.set_user("Administrator")
		lot = receive(ITEM, 1, store(stores.QUARANTINE))
		self.assertIsNone(receipt_line_of(lot))
		self.assertEqual(receipt_lines([]), {})

	def test_a_lot_naming_a_receipt_that_does_not_carry_it_has_no_receipt_line(self):
		receipt, _lot = self.received()
		frappe.set_user("Administrator")
		stray = receive(ITEM, 1, store(stores.QUARANTINE))
		frappe.db.set_value(
			"Batch", stray, {"reference_doctype": "Purchase Receipt", "reference_name": receipt.name}
		)
		self.assertIsNone(receipt_line_of(stray))

	def stores_changes(self, receipt: Document, **values) -> None:
		frappe.set_user(self.stores_user)
		doc = frappe.get_doc("Purchase Receipt", receipt.name)
		doc.items[0].update(values)
		doc.flags.audit_reason = "changed in a test"  # the reason the audit trail asks for
		doc.save()

	def test_only_the_qc_verification_marks_a_line_verified(self):
		receipt, _lot = self.received()
		for values in (
			{"mds_certificate_verified": 1},
			{"mds_certificate_verified_by": self.stores_user},
		):
			with self.subTest(values=values), self.assertRaises(frappe.PermissionError):
				self.stores_changes(receipt, **values)
		self.assertEqual(receipt_line_of(lot_of(receipt.items[0])).verified, 0)

	def test_a_verified_certificate_and_lot_number_no_longer_change(self):
		receipt, _lot = self.received()
		# Before verification Stores may still correct them.
		self.stores_changes(receipt, mds_supplier_lot_no="SUP-4712")
		self.verified(receipt)
		for values in (
			{"mds_certificate": certificate(receipt.name, "-replaced")},
			{"mds_supplier_lot_no": "SUP-9999"},
		):
			with self.subTest(values=values), self.assertRaises(frappe.PermissionError):
				self.stores_changes(receipt, **values)

	def test_a_verified_certificate_file_is_evidence_nobody_deletes(self):
		receipt, _lot = self.received()
		file = frappe.get_value("File", {"file_url": receipt.items[0].mds_certificate}, "name")
		self.verified(receipt)
		frappe.set_user(self.stores_user)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("File", file)
		self.assertTrue(frappe.db.exists("File", file))

	def test_inspection_cannot_start_until_the_certificate_is_verified(self):
		receipt, lot = self.received()
		with self.assertRaises(frappe.ValidationError) as refused:
			self.inspected(receipt, lot)
		self.assertIn("Verify the supplier certificate", str(refused.exception))
		self.verified(receipt)
		self.assertEqual(self.inspected(receipt, lot).status, "Accepted")


@verifies("URS-QC-002")
class TestIncomingInspection(ReleaseTestCase):
	def test_the_inspection_takes_its_parameters_from_the_template_and_names_the_lot_and_line(self):
		receipt, lot = self.received()
		self.verified(receipt)

		inspection = self.inspected(receipt, lot)

		self.assertEqual([r.specification for r in inspection.readings], ["Appearance", "Dimension (mm)"])
		self.assertEqual(inspection.child_row_reference, receipt.items[0].name)
		self.assertEqual(inspection.inspected_by, self.qc)
		self.assertEqual(accepted_inspection(lot), inspection.name)

	def test_a_rejected_reading_rejects_the_inspection(self):
		receipt, lot = self.received()
		self.verified(receipt)

		inspection = self.inspected(receipt, lot, accept=False)

		self.assertEqual(inspection.status, "Rejected")
		self.assertIsNone(accepted_inspection(lot))

	def test_a_measurement_names_its_instrument(self):
		receipt, lot = self.received()
		self.verified(receipt)
		readings = readings_for(self.template, self.instrument)
		readings[1]["mds_instrument"] = None
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc(
				{
					"doctype": "Quality Inspection",
					"inspection_type": "Incoming",
					"reference_type": "Purchase Receipt",
					"reference_name": receipt.name,
					"item_code": ITEM,
					"batch_no": lot,
					"sample_size": 5,
					"inspected_by": frappe.session.user,
					"readings": readings,
				}
			).insert()
		self.assertIn("name the instrument", str(refused.exception))

	def test_the_inspection_must_name_its_lot_and_the_lot_must_have_arrived_on_that_receipt(self):
		receipt, lot = self.received()
		self.verified(receipt)
		other, _other_lot = self.received()
		frappe.set_user(self.qc)
		base = {
			"doctype": "Quality Inspection",
			"inspection_type": "Incoming",
			"reference_type": "Purchase Receipt",
			"item_code": ITEM,
			"sample_size": 5,
			"inspected_by": frappe.session.user,
			"readings": readings_for(self.template, self.instrument),
		}
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc({**base, "reference_name": receipt.name}).insert()
		self.assertIn("Name the lot", str(refused.exception))
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc({**base, "reference_name": other.name, "batch_no": lot}).insert()
		self.assertIn("did not arrive on receipt", str(refused.exception))

	def test_an_item_without_a_template_cannot_be_inspected(self):
		frappe.set_user("Administrator")
		receipt = purchase_receive(NO_TEMPLATE_ITEM, 1, submit=False)
		receipt.items[0].mds_certificate = certificate(receipt.name)
		receipt.save()
		receipt.submit()
		receipt = frappe.get_doc("Purchase Receipt", receipt.name)
		self.verified(receipt)
		with self.assertRaises(frappe.ValidationError) as refused:
			frappe.get_doc(
				{
					"doctype": "Quality Inspection",
					"inspection_type": "Incoming",
					"reference_type": "Purchase Receipt",
					"reference_name": receipt.name,
					"item_code": NO_TEMPLATE_ITEM,
					"batch_no": lot_of(receipt.items[0]),
					"sample_size": 5,
					"inspected_by": frappe.session.user,
				}
			).insert()
		# Nothing to read against: the item has no specification (module 6).
		self.assertIn("has no incoming specification", str(refused.exception))

	def test_only_incoming_inspections_are_held_to_the_receipt_rules(self):
		# An in-process inspection names no receipt and no lot; module 20 sets its own rules.
		guard_incoming_inspection(
			frappe.get_doc({"doctype": "Quality Inspection", "inspection_type": "In Process"})
		)

	def test_no_newer_accepted_inspection_is_found_after_a_later_time(self):
		receipt, lot = self.received()
		self.verified(receipt)
		inspection = self.inspected(receipt, lot)
		self.assertIsNone(accepted_inspection(lot, after=inspection.modified))

	def test_stores_cannot_record_an_inspection(self):
		receipt, lot = self.received()
		self.verified(receipt)
		frappe.set_user(self.stores_user)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Quality Inspection",
					"inspection_type": "Incoming",
					"reference_type": "Purchase Receipt",
					"reference_name": receipt.name,
					"item_code": ITEM,
					"batch_no": lot,
					"sample_size": 5,
					"inspected_by": frappe.session.user,
					"readings": readings_for(self.template, self.instrument),
				}
			).insert()


@verifies("URS-STK-004", "URS-QC-003")
class TestTheSignedDecision(ReleaseTestCase):
	def test_release_needs_an_accepted_inspection(self):
		receipt, lot = self.received()
		self.verified(receipt)
		with self.assertRaises(frappe.ValidationError) as refused:
			self.decide(lot, "Release")
		self.assertIn("no accepted incoming inspection", str(refused.exception))
		self.inspected(receipt, lot, accept=False)
		with self.assertRaises(frappe.ValidationError):
			self.decide(lot, "Release")
		self.assertEqual(status_of([lot])[lot], QUARANTINE)

	def test_release_signs_approves_and_moves_the_lot_in_one_transaction(self):
		receipt, lot = self.ready()

		decision = self.decide(lot, "Release", "Incoming inspection accepted")

		self.assertEqual(
			(decision.decision, decision.docstatus, decision.decided_by), ("Release", 1, self.qa)
		)
		self.assertEqual(decision.lot_status_before, QUARANTINE)
		self.assertEqual(decision.quality_inspection, accepted_inspection(lot))
		self.assertEqual(
			(decision.source_doctype, decision.source_name, decision.supplier),
			("Purchase Receipt", receipt.name, SUPPLIER),
		)
		self.assertEqual(status_of([lot])[lot], APPROVED)
		self.assertEqual(qty_in(lot, store("Stores")), 6)
		self.assertEqual(frappe.db.get_value("Batch", lot, "mds_lot_release"), decision.name)
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Lot Release", "reference_name": decision.name},
			fields=["signer", "meaning", "action"],
		)
		self.assertEqual(
			[(s.signer, s.meaning, s.action) for s in signatures], [(self.qa, "Released", "Release")]
		)
		self.assertTrue(verify_signatures("Lot Release", decision.name))

	def test_a_wrong_password_decides_nothing(self):
		_receipt, lot = self.ready()
		with self.assertRaises(frappe.ValidationError):
			self.decide(lot, "Release", password="wrong password")
		self.assertEqual(status_of([lot])[lot], QUARANTINE)
		self.assertEqual(qty_in(lot, store(stores.QUARANTINE)), 6)
		self.assertFalse(frappe.db.exists("Lot Release", {"batch": lot, "docstatus": 1}))

	def test_reject_moves_the_lot_to_the_rejected_store_and_drafts_the_return_for_purchase(self):
		receipt, lot = self.received()
		self.verified(receipt)
		self.inspected(receipt, lot, accept=False)

		decision = self.decide(lot, "Reject", "Dimension out of limits")

		self.assertEqual(status_of([lot])[lot], REJECTED)
		self.assertEqual(qty_in(lot, store(stores.REJECTED)), 6)
		back = frappe.get_doc("Purchase Receipt", decision.supplier_return)
		self.assertEqual((back.docstatus, back.is_return, back.return_against), (0, 1, receipt.name))
		self.assertEqual(
			[(row.batch_no, row.qty, row.warehouse) for row in back.items],
			[(lot, -6, store(stores.REJECTED))],
		)
		frappe.set_user(self.purchase)
		self.assertTrue(frappe.has_permission("Purchase Receipt", "read", doc=back.name))

	def test_hold_moves_the_lot_to_the_hold_store_and_release_needs_a_new_inspection(self):
		receipt, lot = self.ready()

		held = self.decide(lot, "Hold", "label unreadable")

		self.assertEqual(status_of([lot])[lot], HOLD)
		self.assertEqual(qty_in(lot, store(stores.HOLD)), 6)
		with self.assertRaises(frappe.ValidationError) as refused:
			self.decide(lot, "Release", "looked fine")
		self.assertIn("made after it was put on hold", str(refused.exception))
		self.inspected(receipt, lot)
		released = self.decide(lot, "Release", "retested and accepted")
		self.assertGreaterEqual(
			released.held_since, frappe.db.get_value("Lot Release", held.name, "decided_at")
		)
		self.assertEqual(status_of([lot])[lot], APPROVED)

	def test_an_inspection_only_changed_after_a_hold_is_not_a_new_inspection(self):
		_receipt, lot = self.ready()
		earlier = accepted_inspection(lot)
		self.decide(lot, "Hold", "label unreadable")
		frappe.set_user("Administrator")
		frappe.db.set_value("Quality Inspection", earlier, "remarks", "noted after the hold")
		with self.assertRaises(frappe.ValidationError) as refused:
			self.decide(lot, "Release", "the old inspection was touched")
		self.assertIn("made after it was put on hold", str(refused.exception))

	def test_a_draft_for_a_held_lot_saves_as_the_desk_sends_it(self):
		_receipt, lot = self.ready()
		self.decide(lot, "Hold", "label unreadable")
		record = self.record(lot)
		self.assertTrue(record.held_since)
		# The desk sends a date and time back as text; it is the same value, not a change.
		record.held_since = str(record.held_since)
		record.save()

	def test_the_decision_is_never_submitted_unsigned_cancelled_or_deleted(self):
		_receipt, lot = self.ready()
		record = self.record(lot)
		with self.assertRaises(frappe.PermissionError):
			record.submit()
		decision = self.decide(lot, "Release")
		with self.assertRaises(frappe.PermissionError):
			decision.cancel()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Lot Release", decision.name)
		with self.assertRaises(frappe.ValidationError) as refused:
			sign_action("Lot Release", decision.name, "Hold", PASSWORD, "again")
		self.assertIn("has been decided", str(refused.exception))

	def test_only_qa_creates_or_signs_a_decision(self):
		_receipt, lot = self.ready()
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
		record = self.record(lot)
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.PermissionError):
			sign_action("Lot Release", record.name, "Release", PASSWORD, "mine")

	def test_the_details_of_a_draft_are_set_from_the_lot_not_typed(self):
		_receipt, lot = self.ready()
		record = self.record(lot)
		self.assertEqual((record.item, record.lot_status_before, record.quantity), (ITEM, QUARANTINE, 6))
		record.decision = "Release"
		with self.assertRaises(frappe.PermissionError):
			record.save()
		# QA may still point the draft at another lot; the details follow it.
		record = self.record(lot)
		frappe.set_user("Administrator")
		other = receive(ITEM, 2, store(stores.QUARANTINE))
		frappe.set_user(self.qa)
		record.batch = other
		record.save()
		self.assertEqual((record.batch, record.quantity, record.source_doctype), (other, 2, "Stock Entry"))
		record.run_method("onload")
		self.assertEqual(
			record.get_onload("lot"),
			{"status": QUARANTINE, "from_supplier": False, "certificate_verified": False},
		)
		# A role that could create the record but is not QA is still refused before insert.
		frappe.set_user(self.qc)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({"doctype": "Lot Release", "batch": lot}).run_method("before_insert")

	def test_nobody_cancels_deletes_or_changes_a_decision_not_even_the_administrator(self):
		_receipt, lot = self.ready()
		decision = self.decide(lot, "Release")
		frappe.set_user("Administrator")
		stored = frappe.get_doc("Lot Release", decision.name)
		with self.assertRaises(frappe.PermissionError) as refused:
			stored.cancel()
		self.assertIn("never cancelled", str(refused.exception))
		# Frappe refuses to delete a submitted record before our controller is asked; a draft
		# reaches the controller, which refuses it too.
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Lot Release", decision.name)
		draft = self.record(lot)
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError) as refused:
			frappe.delete_doc("Lot Release", draft.name)
		self.assertIn("never deleted", str(refused.exception))
		stored = frappe.get_doc("Lot Release", decision.name)  # as stored, after the refused cancel
		stored.reason = "rewritten"
		with self.assertRaises(frappe.PermissionError) as refused:
			stored.save()
		self.assertIn("decided only by signing", str(refused.exception))

	def test_the_printout_shows_the_decision_and_its_signature(self):
		_receipt, lot = self.ready()
		decision = self.decide(lot, "Release", "accepted; certificate and inspection in order")
		html = frappe.get_print("Lot Release", decision.name, print_format="Lot Release (Signed)")
		for text in (
			"Electronic signatures",
			"Released",
			frappe.utils.get_fullname(self.qa),
			"inspection in order",
		):
			self.assertIn(text, html)

	def test_a_lot_that_never_came_in_is_not_released(self):
		"""Released "on documents only", an empty lot would let stock arrive in it later with no
		inspection (URS-QC-003: released only after an inspection on what brought it in)."""
		frappe.set_user("Administrator")
		lot = (
			frappe.get_doc(
				{
					"doctype": "Batch",
					"item": ITEM,
					"expiry_date": frappe.utils.add_days(frappe.utils.today(), 30),
				}
			)
			.insert()
			.name
		)
		with self.assertRaisesRegex(frappe.ValidationError, "only stock received on a goods receipt"):
			self.decide(lot, "Release", "documents only")
		self.assertNotEqual(status_of([lot])[lot], APPROVED)

	def test_the_decisions_of_a_lot_are_indexed(self):
		on_doctype_update()
		indexed = {row.Column_name for row in frappe.db.sql("SHOW INDEX FROM `tabLot Release`", as_dict=True)}
		self.assertTrue({"batch", "decided_at"} <= indexed)

	def test_a_rejected_lot_is_decided_no_further(self):
		receipt, lot = self.received()
		self.verified(receipt)
		self.inspected(receipt, lot, accept=False)
		self.decide(lot, "Reject", "no")
		with self.assertRaises(frappe.ValidationError) as refused:
			self.decide(lot, "Hold", "later")
		self.assertIn("cannot be hold", str(refused.exception).lower())


@verifies("URS-QC-004")
class TestTheQueueAndTheFinishedLot(ReleaseTestCase):
	def test_the_queue_says_what_each_lot_waits_for_and_how_long(self):
		_bare, bare_lot = self.received(with_certificate=False)
		_unverified, unverified_lot = self.received()
		uninspected, uninspected_lot = self.received()
		self.verified(uninspected)
		rejected, rejected_lot = self.received()
		self.verified(rejected)
		self.inspected(rejected, rejected_lot, accept=False)
		_ready, ready_lot = self.ready()
		released = self.ready()[1]
		self.decide(released, "Release")
		# Held after its inspection: an old inspection edited since is not a new one.
		held = self.ready()[1]
		self.decide(held, "Hold", "label unreadable")
		frappe.db.set_value(
			"Quality Inspection", accepted_inspection(held), "remarks", "edited after the hold"
		)

		_columns, rows = queue({"item_code": ITEM})
		waiting = {row["batch_no"]: row["waiting_for"] for row in rows}
		self.assertEqual(waiting.get(bare_lot), "Supplier certificate")
		self.assertEqual(waiting.get(unverified_lot), "Certificate verification by QC")
		self.assertEqual(waiting.get(uninspected_lot), "Incoming inspection")
		self.assertEqual(waiting.get(rejected_lot), "Decision on a rejected inspection")
		self.assertEqual(waiting.get(ready_lot), "QA release")
		self.assertEqual(waiting.get(held), "Incoming inspection")
		self.assertNotIn(released, waiting)
		self.assertTrue(all(row["days"] >= 0 for row in rows))

	def test_a_lot_a_work_order_made_is_released_on_the_signature_alone(self):
		# Final QC and batch release (modules 25, 26) replace this; until then QA signs it out.
		frappe.set_user("Administrator")
		material = receive(ITEM, 2, store("Stores"))
		set_status(material, APPROVED, "Approved material for the test's production")
		lot_item(FINISHED_ITEM, "Products")
		# Made by a manufacture entry, as a work order makes its finished lot.
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Manufacture",
				"company": company(),
				"fg_completed_qty": 1,
				"items": [
					{
						"item_code": ITEM,
						"qty": 1,
						"s_warehouse": store("Stores"),
						"batch_no": material,
						"use_serial_batch_fields": 1,
						"basic_rate": 10,
					},
					{
						"item_code": FINISHED_ITEM,
						"qty": 1,
						"t_warehouse": store(stores.QUARANTINE),
						"is_finished_item": 1,
					},
				],
			}
		).insert()
		entry.submit()
		lot = lot_of(frappe.get_doc("Stock Entry", entry.name).items[1])

		decision = self.decide(lot, "Release", "Final inspection accepted")

		self.assertEqual((decision.source_doctype, decision.quality_inspection), ("Stock Entry", None))
		self.assertEqual(status_of([lot])[lot], APPROVED)


@verifies("URS-QC-003")
class TestMaterialReceiptLots(ReleaseTestCase):
	"""A lot brought in on a material receipt (opening stock, stock found in a count) is released only
	after an accepted incoming inspection made on that entry (decided 2026-10-09)."""

	def material_receipt_lot(self) -> tuple[str, str]:
		frappe.set_user("Administrator")
		lot = receive(ITEM, 4, store(stores.QUARANTINE))
		return lot, frappe.db.get_value("Batch", lot, "reference_name")

	def inspection_on(self, lot: str, reference_type: str, reference_name: str) -> Document:
		frappe.set_user(self.qc)
		return frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": reference_type,
				"reference_name": reference_name,
				"item_code": ITEM,
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": self.qc,
				"readings": readings_for(self.template, self.instrument),
			}
		)

	def test_a_material_receipt_lot_is_not_released_without_an_inspection(self):
		lot, _entry = self.material_receipt_lot()
		with self.assertRaisesRegex(frappe.ValidationError, "no accepted incoming inspection"):
			self.decide(lot, "Release", "Opening stock")
		self.assertEqual(status_of([lot])[lot], QUARANTINE)

	def test_it_is_inspected_on_the_entry_that_brought_it_in(self):
		lot, _entry = self.material_receipt_lot()
		receipt, _ = self.received()
		with self.assertRaisesRegex(frappe.ValidationError, "came in on material receipt"):
			self.inspection_on(lot, "Purchase Receipt", receipt.name).insert()

	def test_an_accepted_inspection_on_its_entry_lets_qa_release_it(self):
		lot, entry = self.material_receipt_lot()
		inspection = self.inspection_on(lot, "Stock Entry", entry)
		inspection.insert()
		inspection.submit()
		record = self.decide(lot, "Release", "Opening stock inspected")
		self.assertEqual((record.decision, record.quality_inspection), ("Release", inspection.name))
		self.assertEqual(status_of([lot])[lot], APPROVED)


@verifies("URS-QC-003", "URS-QC-001")
class TestWhereALotCameFrom(ReleaseTestCase):
	"""Where a lot came from is read from the stock ledger, not the batch's own reference, which a
	batch made beforehand would leave blank (found in the final review, 2026-10-09)."""

	def test_a_delivery_does_not_come_in_on_a_lot_that_holds_stock(self):
		frappe.set_user("Administrator")
		existing = receive(ITEM, 2, store(stores.QUARANTINE))  # a lot that came in already
		receipt = purchase_receive(ITEM, 2, submit=False)
		receipt.items[0].update({"batch_no": existing, "use_serial_batch_fields": 1})
		with self.assertRaisesRegex(frappe.ValidationError, "a delivery comes in as a new lot"):
			receipt.save()

	def test_a_lot_scanned_on_the_receipt_is_a_new_lot_of_that_receipt(self):
		"""ERPNext's batch selector and barcode scan make the batch before the receipt, without stock."""
		frappe.set_user("Administrator")
		scanned = frappe.get_doc(
			{"doctype": "Batch", "item": ITEM, "batch_id": f"SCAN-{frappe.generate_hash(length=6)}"}
		).insert()
		receipt = purchase_receive(ITEM, 2, submit=False)
		receipt.items[0].update({"batch_no": scanned.name, "use_serial_batch_fields": 1})
		receipt.save()
		receipt.submit()
		self.assertEqual(receipt_line_of(scanned.name).receipt, receipt.name)
		with self.assertRaisesRegex(frappe.ValidationError, "no accepted incoming inspection"):
			self.decide(scanned.name, "Release", "accepted")

	def test_an_amended_receipt_brings_its_lot_in_again(self):
		"""A cancelled receipt's lot holds no stock; the receipt's amendment is its new origin."""
		frappe.set_user("Administrator")
		receipt = purchase_receive(ITEM, 2)
		lot = lot_of(receipt.items[0])
		stage_reason("Purchase Receipt", receipt.name, "cancel", "Wrong price entered")
		frappe.get_doc("Purchase Receipt", receipt.name).cancel()
		amended = frappe.copy_doc(frappe.get_doc("Purchase Receipt", receipt.name))
		amended.amended_from = receipt.name
		amended.docstatus = 0  # as the desk's Amend makes it: a new draft
		amended.items[0].update(
			{"rate": 11, "batch_no": lot, "use_serial_batch_fields": 1, "serial_and_batch_bundle": None}
		)
		stage_reason("Purchase Receipt", receipt.name, "amend", "Wrong price entered")
		amended.insert()
		amended.submit()
		self.assertEqual(receipt_line_of(lot).receipt, amended.name)

	def count_of(self, lines: list[tuple[str, float]], rate: float = 10) -> Document:
		"""Stores' count of the lots in the approved store, as ERPNext's stock reconciliation."""
		frappe.set_user("Administrator")
		count = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company(),
				"purpose": "Stock Reconciliation",
				"items": [
					{
						"item_code": ITEM,
						"warehouse": store("Stores"),
						"qty": qty,
						"valuation_rate": rate,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
					for lot, qty in lines
				],
			}
		).insert()
		count.submit()
		return count

	def test_a_count_records_a_loss_or_no_change_on_approved_lots(self):
		"""ERPNext posts a count line as the lot's stock taken out and the counted stock put back;
		that is the count, not new stock. (A count with no change at all ERPNext refuses itself.)"""
		_receipt, lost = self.ready()
		self.decide(lost, "Release", "accepted")
		_receipt, unchanged = self.ready()
		self.decide(unchanged, "Release", "accepted")
		held_lost, held_unchanged = qty_in(lost, store("Stores")), qty_in(unchanged, store("Stores"))
		self.count_of([(lost, held_lost - 1), (unchanged, held_unchanged)])
		self.assertEqual(qty_in(lost, store("Stores")), held_lost - 1)
		self.assertEqual(qty_in(unchanged, store("Stores")), held_unchanged)
		self.count_of([(unchanged, held_unchanged)], rate=12)  # a corrected value, same stock
		self.assertEqual(qty_in(unchanged, store("Stores")), held_unchanged)

	def test_no_stock_is_added_to_a_lot_already_in_stock(self):
		"""A material receipt or a count gain into an approved lot would take its status uninspected."""
		_receipt, lot = self.ready()
		self.decide(lot, "Release", "accepted")
		frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.ValidationError, "new stock comes in as a new lot"):
			stock_entry(
				"Material Receipt",
				[
					{
						"item_code": ITEM,
						"qty": 5,
						"t_warehouse": store("Stores"),
						"basic_rate": 10,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			)
		with self.assertRaisesRegex(frappe.ValidationError, "new stock comes in as a new lot"):
			self.count_of([(lot, qty_in(lot, store("Stores")) + 2)])  # two found: a gain

	def test_a_received_lot_is_known_by_its_receipt_even_without_the_batch_reference(self):
		receipt, lot = self.received()
		self.verified(receipt)
		frappe.db.set_value("Batch", lot, {"reference_doctype": None, "reference_name": None})
		self.assertEqual(receipt_line_of(lot).receipt, receipt.name)  # from the stock ledger
		with self.assertRaisesRegex(frappe.ValidationError, "no accepted incoming inspection"):
			self.decide(lot, "Release", "accepted")

	def test_a_lot_that_came_in_any_other_way_is_not_released(self):
		"""Opening stock loaded by a stock reconciliation, as ERPNext offers, is never released:
		opening stock comes in on material receipts and is inspected (DFD 3.1-3.5)."""
		frappe.set_user("Administrator")
		lot = frappe.get_doc(
			{"doctype": "Batch", "item": ITEM, "batch_id": f"REC-{frappe.generate_hash(length=6)}"}
		).insert()
		reconciliation = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company(),
				"purpose": "Opening Stock",
				# An opening entry's difference goes to the temporary opening account, as ERPNext requires.
				"expense_account": frappe.db.get_value(
					"Account", {"company": company(), "account_type": "Temporary", "is_group": 0}
				),
				"items": [
					{
						"item_code": ITEM,
						"warehouse": store(stores.QUARANTINE),
						"qty": 3,
						"valuation_rate": 10,
						"batch_no": lot.name,
						"use_serial_batch_fields": 1,
					}
				],
			}
		).insert()
		reconciliation.submit()
		with self.assertRaisesRegex(frappe.ValidationError, "only stock received on a goods receipt"):
			self.decide(lot.name, "Release", "found in a count")
