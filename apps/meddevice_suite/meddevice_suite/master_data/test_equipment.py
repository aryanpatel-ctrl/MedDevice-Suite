"""Equipment master (module 8, URS-MST-010) and its history card (module 62, URS-MST-011): tests
derived from the requirements' clauses."""

import frappe
from frappe.utils import add_days, now_datetime, today

from meddevice_suite.compliance.esign import SigningRefused, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.lot_control.test_lot_status import company
from meddevice_suite.lot_control.test_release import ReleaseTestCase
from meddevice_suite.master_data import equipment
from meddevice_suite.master_data.report.equipment_history_card import equipment_history_card
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.fixtures.inspection import INSTRUMENT_EQUIPMENT, INSTRUMENT_ITEM, LOCATION
from meddevice_suite.setup.test_stock import care_in_date_for_test

REFUSED = (frappe.ValidationError, frappe.PermissionError)


class EquipmentTestCase(ReleaseTestCase):
	"""ReleaseTestCase sets up the demo instrument, its item and a lot to inspect."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.maintenance = signer("Maintenance")

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def new_equipment(self, **values) -> frappe.model.document.Document:
		"""A new piece of equipment, as QA records it."""
		suffix = frappe.generate_hash(length=6).upper()
		frappe.set_user(self.qa)
		try:
			return frappe.get_doc(
				{
					"doctype": "Asset",
					"asset_name": f"Caliper {suffix}",
					"item_code": INSTRUMENT_ITEM,
					"company": company(),
					"location": LOCATION,
					"purchase_date": today(),
					"available_for_use_date": today(),
					"asset_type": "Existing Asset",
					"gross_purchase_amount": 15000,
					"net_purchase_amount": 15000,
					"asset_quantity": 1,
					"calculate_depreciation": 0,
					**INSTRUMENT_EQUIPMENT,
					equipment.TAG: f"QC-CAL-{suffix}",
					equipment.SERIAL: f"SN-{suffix}",
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def approve(self, asset: str, reason: str = "Make, model, serial and calibration checked") -> None:
		frappe.set_user(self.qa)
		try:
			sign_action("Asset", asset, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")


@verifies("URS-MST-010")
class TestApproval(EquipmentTestCase):
	def test_new_equipment_is_a_draft_until_qa_approves_it_with_a_signature(self):
		asset = self.new_equipment()
		self.assertEqual(asset.get(equipment.APPROVAL_STATUS), equipment.DRAFT)
		self.approve(asset.name, "Checked against the supplier's delivery note")
		doc = frappe.get_doc("Asset", asset.name)
		self.assertEqual(
			(doc.get(equipment.APPROVAL_STATUS), doc.get(equipment.APPROVED_BY)),
			(equipment.APPROVED, self.qa),
		)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Asset", "reference_name": asset.name}
		)
		self.assertEqual((signature.meaning, signature.signer), ("Approved", self.qa))
		self.assertTrue(verify_signatures("Asset", asset.name))
		self.assertTrue(is_unchanged_since(signature))

	def test_only_qa_approves(self):
		asset = self.new_equipment()
		for user in (self.qc, self.maintenance, signer("Stores")):
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(REFUSED) as refused:
					sign_action("Asset", asset.name, "Approve", PASSWORD, "Not mine")
				if user == self.maintenance:  # who may read equipment is refused by the rule itself
					self.assertIn("Only QA approves", str(refused.exception))
		frappe.set_user("Administrator")
		self.assertFalse(equipment.approved(asset.name))

	def test_a_wrong_password_or_no_reason_approves_nothing_and_twice_is_refused(self):
		asset = self.new_equipment()
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action("Asset", asset.name, "Approve", "not the password", "Checked")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action("Asset", asset.name, "Approve", PASSWORD, " ")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		self.assertFalse(equipment.approved(asset.name))
		self.approve(asset.name)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			sign_action("Asset", asset.name, "Approve", PASSWORD, "Again")

	def test_the_approval_is_given_only_by_the_signature(self):
		frappe.set_user(self.qa)
		asset = self.new_equipment()
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.APPROVAL_STATUS, equipment.APPROVED)
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			doc.save()
		inserted = self.new_equipment(**{equipment.APPROVAL_STATUS: equipment.APPROVED})
		self.assertEqual(inserted.get(equipment.APPROVAL_STATUS), equipment.DRAFT)  # cleared as it was made

	def test_what_approval_needs(self):
		cases = {
			"its equipment tag": {equipment.TAG: None},
			"its class": {equipment.CLASS: None},
			"its manufacturer": {equipment.MAKER: None},
			"its model": {equipment.MODEL: None},
			"its manufacturer's serial number": {equipment.SERIAL: None},
			"where it is used": {"location": None},
			"its calibration interval": {equipment.CALIBRATION_DAYS: 0},
			"a measuring instrument is calibrated": {equipment.CALIBRATION: 0, equipment.CALIBRATION_DAYS: 0},
			"its maintenance interval": {equipment.MAINTENANCE: 1, equipment.MAINTENANCE_DAYS: 0},
		}
		for missing, values in cases.items():
			with self.subTest(missing=missing):
				asset = self.new_equipment()
				frappe.db.set_value("Asset", asset.name, values)  # as an import or an early record leaves it
				frappe.set_user(self.qa)
				with self.assertRaisesRegex(
					frappe.ValidationError, f"cannot be approved yet; it lacks .*{missing}"
				):
					sign_action("Asset", asset.name, "Approve", PASSWORD, "Checked")
				frappe.set_user("Administrator")

	def test_a_retired_piece_of_equipment_is_not_approved(self):
		asset = self.new_equipment(**{equipment.STATUS: equipment.RETIRED})
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "retired"):
			sign_action("Asset", asset.name, "Approve", PASSWORD, "Checked")

	def test_the_form_says_what_approval_needs(self):
		asset = self.new_equipment(**{equipment.MODEL: None})
		doc = frappe.get_doc("Asset", asset.name)
		doc.set("__onload", frappe._dict())
		doc.run_method("onload")
		self.assertIn("its model", doc.get_onload().equipment_approval["missing"])


@verifies("URS-MST-010")
class TestTheRecord(EquipmentTestCase):
	def test_the_tag_is_unique_among_equipment_not_cancelled(self):
		asset = self.new_equipment()
		with self.assertRaisesRegex(frappe.ValidationError, "each piece of equipment has its own"):
			self.new_equipment(**{equipment.TAG: asset.get(equipment.TAG)})
		frappe.db.set_value("Asset", asset.name, "docstatus", 2)  # cancelled, as by its own form
		amended = frappe.copy_doc(frappe.get_doc("Asset", asset.name))  # as Amend starts it, the tag kept
		amended.update({"amended_from": asset.name, "docstatus": 0})
		amended.flags.audit_reason = "Entered with the wrong value"  # the audit trail's question
		amended.insert()
		self.assertEqual(amended.get(equipment.TAG), asset.get(equipment.TAG))  # the amendment keeps it

	def test_retired_is_final(self):
		asset = self.new_equipment()
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.STATUS, equipment.RETIRED)
		doc.save()
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.STATUS, equipment.IN_SERVICE)
		with self.assertRaisesRegex(frappe.ValidationError, "not put back into use"):
			doc.save()

	def submitted(self, asset: str) -> frappe.model.document.Document:
		# As whoever books the asset submits it (its accounting); QA's own submit needs the asset
		# movement it makes, which QA may not create (open item, see the feature doc).
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Asset", asset)
		doc.submit()
		frappe.set_user(self.qa)
		return doc

	def test_a_submitted_assets_approval_is_the_signatures_alone(self):
		from meddevice_suite.compliance.audit_reason import stage_reason

		asset = self.new_equipment()
		self.submitted(asset.name)
		stage_reason("Asset", asset.name, "update_after_submit", "Approving by hand")
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			frappe.client.set_value("Asset", asset.name, equipment.APPROVAL_STATUS, equipment.APPROVED)
		frappe.set_user("Administrator")
		self.assertFalse(equipment.approved(asset.name))
		self.approve(asset.name)  # the signed act approves a submitted asset
		self.assertTrue(equipment.approved(asset.name))

	def test_a_submitted_approved_assets_details_change_under_a_change_request(self):
		from meddevice_suite.compliance.audit_reason import stage_reason
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST, CHANGED_UNDER
		from meddevice_suite.qms.test_change_control import approved_change

		asset = self.new_equipment()
		self.approve(asset.name)
		self.submitted(asset.name)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.CALIBRATION_DAYS, 180)
		stage_reason("Asset", asset.name, "update_after_submit", "Calibrated twice a year")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()
		change = approved_change(signer("Production Supervisor"), self.qa, asset.name, "Asset", "Equipment")
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.CALIBRATION_DAYS, 180)
		doc.set(CHANGE_REQUEST, change)
		stage_reason("Asset", asset.name, "update_after_submit", "Calibrated twice a year")
		doc.save()
		self.assertEqual(
			frappe.db.get_value("Asset", asset.name, [equipment.CALIBRATION_DAYS, CHANGED_UNDER]),
			(180, change),
		)

	def test_a_move_is_not_a_change_of_what_qa_approved(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Asset", "reference_name": asset.name}
		)
		frappe.db.set_value("Asset", asset.name, "location", "Store")  # as ERPNext's asset movement writes it
		self.assertTrue(is_unchanged_since(signature))

	def test_a_copy_is_another_piece_of_equipment_in_draft(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		copy = frappe.copy_doc(
			frappe.get_doc("Asset", asset.name), ignore_no_copy=False
		)  # the desk's Duplicate
		self.assertEqual(
			(copy.get(equipment.TAG), copy.get(equipment.SERIAL), copy.get(equipment.APPROVAL_STATUS)),
			(None, None, None),
		)
		copy.set(equipment.TAG, f"QC-CAL-{frappe.generate_hash(length=6)}")
		copy.insert()
		self.assertEqual(copy.get(equipment.APPROVAL_STATUS), equipment.DRAFT)

	def test_approved_equipment_keeps_what_its_approval_needed(self):
		"""Even under an approved change request, approved equipment stays complete."""
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST
		from meddevice_suite.qms.test_change_control import approved_change

		asset = self.new_equipment()
		self.approve(asset.name)
		change = approved_change(signer("Production Supervisor"), self.qa, asset.name, "Asset", "Equipment")
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.CALIBRATION_DAYS, 0)
		doc.set(CHANGE_REQUEST, change)
		with self.assertRaisesRegex(frappe.ValidationError, "keeps what its approval needed"):
			doc.save()

	def test_what_qa_approved_changes_only_under_change_control(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.MODEL, "Another model")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()

	def test_its_status_changes_from_day_to_day(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.STATUS, equipment.OUT_OF_SERVICE)
		doc.save()
		self.assertEqual(frappe.db.get_value("Asset", asset.name, equipment.STATUS), equipment.OUT_OF_SERVICE)

	def test_the_signature_binds_the_equipment_not_its_accounting(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Asset", "reference_name": asset.name}
		)
		frappe.db.set_value(
			"Asset", asset.name, {"net_purchase_amount": 99999, equipment.STATUS: equipment.OUT_OF_SERVICE}
		)
		self.assertTrue(is_unchanged_since(signature))
		frappe.db.set_value("Asset", asset.name, equipment.SERIAL, "ANOTHER")  # past the rules
		self.assertFalse(is_unchanged_since(signature))


@verifies("URS-MST-010")
class TestUseOnInspections(EquipmentTestCase):
	def inspect_with(self, instrument: str):
		from meddevice_suite.setup.fixtures.inspection import readings_for

		receipt, lot = self.received()
		self.verified(receipt)
		frappe.set_user(self.qc)
		return frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": receipt.name,
				"item_code": "MDS-REL-RAW",
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": self.qc,
				"readings": readings_for(self.template, instrument),
			}
		)

	def test_a_reading_names_only_approved_equipment_in_service(self):
		draft = self.new_equipment()
		with self.assertRaisesRegex(frappe.ValidationError, "QA has not approved it"):
			self.inspect_with(draft.name).insert()
		self.approve(draft.name)
		care_in_date_for_test(draft.name)  # module 38: calibrated, so only the status is tested here
		for status in (equipment.OUT_OF_SERVICE, equipment.RETIRED):
			with self.subTest(status=status):
				frappe.db.set_value("Asset", draft.name, equipment.STATUS, status)
				with self.assertRaisesRegex(frappe.ValidationError, "is not used for an inspection"):
					self.inspect_with(draft.name).insert()
		frappe.db.set_value("Asset", draft.name, equipment.STATUS, equipment.IN_SERVICE)
		for status in ("Out of Order", "Scrapped", "In Maintenance"):  # ERPNext's own asset status
			with self.subTest(erpnext_status=status):
				frappe.db.set_value("Asset", draft.name, "status", status)
				with self.assertRaisesRegex(frappe.ValidationError, "is not used for an inspection"):
					self.inspect_with(draft.name).insert()
		frappe.db.set_value("Asset", draft.name, "status", "Draft")
		self.inspect_with(draft.name).insert()  # approved and in service


@verifies("URS-MST-011")
class TestHistoryCard(EquipmentTestCase):
	def card(self, asset: str, **filters):
		_columns, rows, _message, _chart, summary = equipment_history_card.execute(
			{"asset": asset, **filters}
		)
		return rows, {s["label"]: s["value"] for s in summary}

	def test_the_card_lists_what_happened_to_the_equipment_newest_first(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.STATUS, equipment.OUT_OF_SERVICE)
		doc.save()
		frappe.set_user("Administrator")
		# Records ERPNext keeps, as their own forms leave them (the card only reads them).
		move = frappe.get_doc(
			{
				"doctype": "Asset Movement",
				"company": company(),
				"purpose": "Transfer",
				"transaction_date": frappe.utils.add_to_date(
					now_datetime(), minutes=5
				),  # out of order with the rest
				"docstatus": 1,
			}
		)
		move.db_insert()
		frappe.get_doc(
			{
				"doctype": "Asset Movement Item",
				"parent": move.name,
				"parenttype": "Asset Movement",
				"parentfield": "assets",
				"asset": asset.name,
				"source_location": LOCATION,
				"target_location": "Store",
			}
		).db_insert()
		repair = frappe.get_doc(
			{
				"doctype": "Asset Repair",
				"asset": asset.name,
				"company": company(),
				"failure_date": now_datetime(),
				"repair_status": "Completed",
				"description": "Display replaced",
				"downtime": "12.5 Hrs",
			}
		)
		repair.db_insert()
		plan = frappe.get_doc(
			{"doctype": "Asset Maintenance", "asset_name": asset.name, "company": company()}
		)
		plan.db_insert()
		frappe.get_doc(
			{
				"doctype": "Asset Maintenance Log",
				"asset_maintenance": plan.name,
				"completion_date": today(),
				"maintenance_type": "Calibration",
				"maintenance_status": "Completed",
				"task_name": "Yearly calibration",
			}
		).db_insert()
		rows, summary = self.card(asset.name)
		kinds = {row["record_type"] for row in rows}
		self.assertTrue(
			{"Electronic Signature", "Audit Event", "Asset Movement", "Asset Repair", "Asset Maintenance Log"}
			<= kinds,
			kinds,
		)
		self.assertEqual((summary["Repairs"], summary["Downtime (hours)"]), (1, 12.5))
		self.assertTrue(
			any("Equipment Status" in row["details"] for row in rows if row["record_type"] == "Audit Event")
		)
		self.assertIn("Recorded", [row["event"] for row in rows])
		dates = [frappe.utils.getdate(row["date"]) for row in rows]
		self.assertEqual(dates, sorted(dates, reverse=True))

	def test_the_inspections_that_used_it_are_on_its_card(self):
		receipt, lot = self.received()
		self.verified(receipt)
		inspection = self.inspected(receipt, lot)
		frappe.set_user(self.qa)
		rows, summary = self.card(self.instrument)
		self.assertIn(
			inspection.name, [row["record"] for row in rows if row["record_type"] == "Quality Inspection"]
		)
		self.assertGreaterEqual(summary["Inspections using it"], 1)

	def test_a_planned_log_is_the_schedule_not_history(self):
		asset = self.new_equipment()
		plan = frappe.get_doc(
			{"doctype": "Asset Maintenance", "asset_name": asset.name, "company": company()}
		)
		plan.db_insert()
		frappe.get_doc(
			{
				"doctype": "Asset Maintenance Log",
				"asset_maintenance": plan.name,
				"due_date": add_days(today(), 30),
				"maintenance_status": "Planned",
				"task_name": "Next calibration",
			}
		).db_insert()
		rows, _summary = self.card(asset.name, to_date=add_days(today(), 60))
		self.assertNotIn("Asset Maintenance Log", {row["record_type"] for row in rows})

	def test_the_card_covers_the_whole_life_unless_a_period_is_chosen(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		old = add_days(now_datetime(), -800)
		frappe.db.set_value("Asset", asset.name, "creation", old, update_modified=False)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Asset", "reference_name": asset.name}
		)
		frappe.db.set_value("Electronic Signature", signature.name, "signed_at", add_days(old, 1))
		rows, _summary = self.card(asset.name)
		self.assertIn(signature.name, [row["record"] for row in rows])

	def test_events_of_one_day_are_in_their_order(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Asset", asset.name)
		doc.set(equipment.STATUS, equipment.OUT_OF_SERVICE)
		doc.save()
		rows, _summary = self.card(asset.name)
		times = [frappe.utils.get_datetime(row["date"]) for row in rows]
		self.assertEqual(times, sorted(times, reverse=True))
		self.assertEqual(rows[-1]["event"], "Recorded")  # the first thing that happened to it

	def test_the_period_bounds_the_card(self):
		asset = self.new_equipment()
		self.approve(asset.name)
		rows, _summary = self.card(asset.name, from_date=add_days(today(), 1), to_date=add_days(today(), 30))
		self.assertEqual(rows, [])

	def test_reading_the_card_needs_reading_the_equipment(self):
		asset = self.new_equipment()
		frappe.set_user(signer("Sales"))
		try:
			with self.assertRaises(frappe.PermissionError):
				self.card(asset.name)
		finally:
			frappe.set_user("Administrator")

	def test_downtime_is_read_from_erpnexts_text(self):
		self.assertEqual(
			[equipment_history_card.hours(text) for text in ("12.5 Hrs", "3 Hrs", "", None, "n/a")],
			[12.5, 3.0, 0.0, 0.0, 0.0],
		)
