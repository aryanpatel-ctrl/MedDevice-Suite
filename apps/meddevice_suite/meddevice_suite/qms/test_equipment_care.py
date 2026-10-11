"""Calibration, maintenance and qualification (modules 38 and 40; URS-EQP-001 to URS-EQP-012, and the
history card's additions to URS-MST-011): tests derived from the requirements' clauses."""

import frappe
from frappe.tests.classes.context_managers import freeze_time
from frappe.utils import add_days, getdate, now_datetime, today

from meddevice_suite.compliance.esign import is_unchanged_since
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import company
from meddevice_suite.master_data import equipment
from meddevice_suite.master_data.test_equipment import EquipmentTestCase
from meddevice_suite.master_data.test_routing import RoutingTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.qms import equipment_care
from meddevice_suite.qms.test_document_control import private_file
from meddevice_suite.setup.test_stock import care_in_date_for_test, work_instruction_for_test
from meddevice_suite.work_management import reminders

IN = {"parameter": "Length 100 mm", "unit": "mm", "nominal": 100, "lower_limit": 99.98, "upper_limit": 100.02}


def point(found: float = 100.01, left: float = 100.0) -> dict:
	return {**IN, "as_found": found, "as_left": left}


class CareTestCase(EquipmentTestCase):
	def approved(self, **values) -> str:
		"""New equipment approved by QA's signature, with no care record yet."""
		asset = self.new_equipment(**values).name
		self.approve(asset)
		return asset

	def lab(self) -> str:
		name = "MDS Test Calibration Laboratory"
		if not frappe.db.exists("Supplier", name):
			frappe.get_doc(
				{"doctype": "Supplier", "supplier_name": name, "supplier_group": "Raw Material"}
			).insert(ignore_permissions=True)
		return name

	def calibration(self, asset: str, points=None, by: str = "External laboratory", **values) -> str:
		"""A calibration record drafted by Maintenance."""
		frappe.set_user(self.maintenance)
		try:
			base = (
				{
					"laboratory": self.lab(),
					"certificate_number": f"LAB-{frappe.generate_hash(length=5)}",
					"certificate": private_file("certificate"),
				}
				if by == "External laboratory"
				else {}
			)
			return (
				frappe.get_doc(
					{
						"doctype": "Calibration Record",
						"asset": asset,
						"performed_on": today(),
						"calibrated_by": by,
						"points": points or [point()],
						**base,
						**values,
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def sign(
		self, user: str, doctype: str, name: str, act: str, reason: str | None = "Done to procedure"
	) -> None:
		frappe.set_user(user)
		try:
			sign_action(doctype, name, act, PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def card(self, asset: str) -> tuple[list[dict], dict]:
		from meddevice_suite.master_data.report.equipment_history_card import equipment_history_card

		_columns, rows, _message, _chart, summary = equipment_history_card.execute({"asset": asset})
		return rows, {s["label"]: s["value"] for s in summary}

	def calibrated(self, asset: str, **values) -> frappe.model.document.Document:
		name = self.calibration(asset, **values)
		self.sign(self.maintenance, "Calibration Record", name, "Complete")
		return frappe.get_doc("Calibration Record", name)


@verifies("URS-EQP-001")
class TestCalibration(CareTestCase):
	def test_a_pass_sets_the_next_due_date_from_the_approved_interval(self):
		asset = self.approved()
		record = self.calibrated(asset)
		self.assertEqual(
			(record.result, record.docstatus, record.completed_by), ("Pass", 1, self.maintenance)
		)
		self.assertEqual(getdate(record.next_due_date), getdate(add_days(today(), 365)))
		signature = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
		)
		self.assertEqual((signature.signer, signature.meaning), (self.maintenance, "Verified"))

	def test_a_point_left_out_of_tolerance_fails_and_sets_no_due_date(self):
		record = self.calibrated(self.approved(), points=[point(left=100.05)])
		self.assertEqual((record.result, record.next_due_date), ("Fail", None))

	def test_completed_only_by_signature_never_changed_or_deleted(self):
		name = self.calibration(self.approved())
		doc = frappe.get_doc("Calibration Record", name)
		doc.flags.ignore_permissions = True
		with self.assertRaisesRegex(frappe.PermissionError, "signature"):
			doc.submit()
		self.sign(self.maintenance, "Calibration Record", name, "Complete")
		doc = frappe.get_doc("Calibration Record", name)
		doc.certificate_number = "Rewritten"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.delete_doc("Calibration Record", name, ignore_permissions=True)
		draft = self.calibration(frappe.get_value("Calibration Record", name, "asset"), performed_on=today())
		with self.assertRaisesRegex(frappe.PermissionError, "never deleted"):  # past permissions
			frappe.delete_doc("Calibration Record", draft, ignore_permissions=True)

	def test_only_maintenance_or_qa_completes_one(self):
		name = self.calibration(self.approved())
		with self.assertRaises(frappe.PermissionError):
			self.sign(self.stores_user, "Calibration Record", name, "Complete")

	def test_external_needs_its_laboratory_certificate_number_and_certificate(self):
		with self.assertRaisesRegex(
			frappe.ValidationError, "laboratory, its certificate number and the certificate"
		):
			self.calibration(self.approved(), certificate=None)

	def test_dates_are_real_and_in_order(self):
		asset = self.approved()
		with self.assertRaisesRegex(frappe.ValidationError, "once it is done"):
			self.calibration(asset, performed_on=add_days(today(), 1))
		self.calibrated(asset)
		with self.assertRaisesRegex(frappe.ValidationError, "in order"):
			self.calibration(asset, performed_on=add_days(today(), -1))

	def test_only_approved_equipment_that_needs_calibration(self):
		with self.assertRaisesRegex(frappe.ValidationError, "does not need calibration"):
			self.calibration(
				self.approved(
					**{equipment.CALIBRATION: 0, equipment.CALIBRATION_DAYS: 0, equipment.CLASS: "Utility"}
				)
			)
		draft = self.new_equipment().name
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			self.calibration(draft)

	def test_its_certificate_is_evidence(self):
		record = self.calibrated(self.approved())
		file = frappe.get_doc("File", {"file_url": record.certificate})
		frappe.set_user(self.maintenance)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("File", file.name)


@verifies("URS-EQP-002")
class TestReferenceStandards(CareTestCase):
	def in_house(self, asset: str, standards: list[str], **values) -> str:
		return self.calibration(
			asset,
			by="In-house",
			procedure=work_instruction_for_test(),
			standards=[{"standard": s} for s in standards],
			**values,
		)

	def test_a_standard_in_calibration_is_recorded_with_its_due_date(self):
		standard = self.approved()
		due = self.calibrated(standard).next_due_date
		name = self.in_house(self.approved(), [standard])
		self.assertEqual(
			getdate(frappe.get_doc("Calibration Record", name).standards[0].standard_due_on), getdate(due)
		)

	def test_a_standard_is_judged_on_the_day_of_the_calibration_not_today(self):
		standard = self.approved()
		self.calibrated(standard, performed_on=add_days(today(), -370))  # due five days ago
		name = self.in_house(self.approved(), [standard], performed_on=add_days(today(), -10))
		self.assertTrue(name)
		with self.assertRaisesRegex(frappe.ValidationError, "was not in calibration"):
			self.in_house(self.approved(), [standard])

	def test_a_standard_out_of_calibration_on_the_day_is_refused(self):
		standard = self.approved()  # never calibrated
		with self.assertRaisesRegex(frappe.ValidationError, "was not in calibration"):
			self.in_house(self.approved(), [standard])

	def test_not_its_own_standard_and_only_approved_equipment(self):
		asset = self.approved()
		with self.assertRaisesRegex(frappe.ValidationError, "its own reference standard"):
			self.in_house(asset, [asset])
		with self.assertRaisesRegex(frappe.ValidationError, "not approved equipment"):
			self.in_house(asset, [self.new_equipment().name])

	def test_in_house_needs_an_effective_procedure_and_standards(self):
		asset = self.approved()
		with self.assertRaisesRegex(frappe.ValidationError, "effective SOP or work instruction"):
			self.calibration(asset, by="In-house", standards=[{"standard": asset}])
		with self.assertRaisesRegex(frappe.ValidationError, "reference standards"):
			self.calibration(asset, by="In-house", procedure=work_instruction_for_test())


@verifies("URS-EQP-003")
class TestOutOfTolerance(CareTestCase):
	def test_what_it_measured_since_its_last_good_calibration_is_listed_for_qa(self):
		asset = self.approved()
		good = self.calibrated(asset)
		frappe.db.set_value("Calibration Record", good.name, "performed_on", add_days(today(), -30))
		receipt, lot = self.received()
		self.verified(receipt)
		before = self.inspected(receipt, lot)
		frappe.db.set_value(
			"Quality Inspection", before.name, "report_date", add_days(today(), -40)
		)  # before it
		frappe.db.set_value("Quality Inspection Reading", {"parent": before.name}, "mds_instrument", asset)
		receipt2, lot2 = self.received()
		self.verified(receipt2)
		since = self.inspected(receipt2, lot2)
		frappe.db.set_value("Quality Inspection", since.name, "report_date", add_days(today(), -20))
		frappe.db.set_value("Quality Inspection Reading", {"parent": since.name}, "mds_instrument", asset)
		draft = frappe.copy_doc(frappe.get_doc("Quality Inspection", since.name))  # never submitted
		draft.docstatus = 0
		draft.set_new_name()
		draft.set_parent_in_children()
		draft.db_insert()
		for row in draft.readings:
			row.name = frappe.generate_hash(length=10)
			row.docstatus = 0
			row.db_insert()
		# A failed calibration after the good one: what was measured since the good one is in doubt.
		self.calibrated(asset, points=[point(found=100.05, left=100.05)], performed_on=add_days(today(), -10))
		record = self.calibrated(asset, points=[point(found=100.05, left=100.0)])
		self.assertEqual((record.out_of_tolerance, record.impact_status, record.result), (1, "Open", "Pass"))
		listed = [row.reference_name for row in record.impact]
		self.assertIn(since.name, listed)
		self.assertNotIn(before.name, listed)
		self.assertNotIn(draft.name, listed)
		self.assertEqual(getdate(record.last_good_calibration), getdate(add_days(today(), -30)))
		self.assertTrue(
			frappe.db.exists(
				"Notification Log",
				{"document_type": "Calibration Record", "document_name": record.name, "for_user": self.qa},
			)
		)

	def test_only_qa_assesses_with_a_reason_and_the_completion_signature_stands(self):
		record = self.calibrated(self.approved(), points=[point(found=100.05)])
		completion = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
		)
		with self.assertRaises(frappe.PermissionError):
			self.sign(self.maintenance, "Calibration Record", record.name, "Assess: No Product Impact")
		with self.assertRaisesRegex(frappe.ValidationError, "Say what was assessed"):
			self.sign(self.qa, "Calibration Record", record.name, "Assess: No Product Impact", reason=None)
		self.sign(
			self.qa,
			"Calibration Record",
			record.name,
			"Assess: No Product Impact",
			"Readings were within half the tolerance",
		)
		record.reload()
		self.assertEqual(
			(record.impact_status, record.impact_decision, record.assessed_by),
			("Assessed", "No product impact", self.qa),
		)
		self.assertTrue(is_unchanged_since(completion))

	def test_in_tolerance_needs_no_assessment(self):
		self.assertEqual(self.calibrated(self.approved()).impact_status, "Not needed")


@verifies("URS-EQP-004")
class TestMaintenance(CareTestCase):
	def maintenance_record(self, asset: str, outcomes=("OK",), **values) -> str:
		frappe.set_user(self.maintenance)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": "Maintenance Record",
						"asset": asset,
						"performed_on": today(),
						"checks": [{"check": f"Check {i}", "outcome": o} for i, o in enumerate(outcomes)],
						"actions_performed": "Cleaned and lubricated",
						"downtime_hours": 2,
						**values,
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def machine(self) -> str:
		return self.approved(**{equipment.MAINTENANCE: 1, equipment.MAINTENANCE_DAYS: 90})

	def test_satisfactory_only_when_every_check_is_ok_and_only_then_due_again(self):
		asset = self.machine()
		ok = self.maintenance_record(asset)
		self.sign(self.maintenance, "Maintenance Record", ok, "Complete")
		ok = frappe.get_doc("Maintenance Record", ok)
		self.assertEqual(
			(ok.result, getdate(ok.next_due_date)), ("Satisfactory", getdate(add_days(today(), 90)))
		)
		bad = self.maintenance_record(asset, outcomes=("OK", "Not OK"))
		self.sign(self.maintenance, "Maintenance Record", bad, "Complete")
		bad = frappe.get_doc("Maintenance Record", bad)
		self.assertEqual((bad.result, bad.next_due_date), ("Not satisfactory", None))

	def test_completed_only_by_signature_and_downtime_is_hours(self):
		asset = self.machine()
		with self.assertRaises(frappe.ValidationError):
			self.maintenance_record(asset, downtime_hours=-1)
		doc = frappe.get_doc("Maintenance Record", self.maintenance_record(asset))
		doc.flags.ignore_permissions = True
		with self.assertRaisesRegex(frappe.PermissionError, "signature"):
			doc.submit()


@verifies("URS-EQP-005")
class TestFitness(CareTestCase):
	def test_never_calibrated_is_not_fit(self):
		asset = self.approved()
		self.assertIn("no calibration", " ".join(equipment_care.unfit([asset])[asset]))

	def test_fit_through_its_due_date_and_not_the_day_after(self):
		asset = self.approved()
		due = self.calibrated(asset).next_due_date
		self.assertNotIn(asset, equipment_care.unfit([asset], on=due))
		self.assertIn(asset, equipment_care.unfit([asset], on=add_days(due, 1)))

	def test_the_latest_calibration_decides_and_a_failure_overrides_an_older_pass(self):
		asset = self.approved()
		self.calibrated(asset)
		self.calibrated(asset, points=[point(left=100.05)])
		self.assertIn("did not pass", " ".join(equipment_care.unfit([asset])[asset]))

	def test_the_latest_is_the_one_done_last_not_the_one_entered_last(self):
		asset = self.approved()
		self.calibrated(asset, points=[point(left=100.05)])  # failed today
		corrected = self.calibrated(asset)  # entered after it (as an amendment of an older one would be)
		frappe.db.set_value("Calibration Record", corrected.name, "performed_on", add_days(today(), -5))
		self.assertIn("did not pass", " ".join(equipment_care.unfit([asset])[asset]))

	def test_under_an_open_repair_it_is_not_fit(self):
		asset = self.approved()
		self.calibrated(asset)
		frappe.get_doc(
			{
				"doctype": "Asset Repair",
				"asset": asset,
				"company": company(),
				"failure_date": now_datetime(),
				"repair_status": "Pending",
				"description": "Display flickers",
			}
		).db_insert()
		self.assertIn("under repair", " ".join(equipment_care.unfit([asset])[asset]))


def complete_repair(asset: str, day=None) -> None:
	"""A completed ERPNext repair, written as ERPNext submits one (its own validation books stock and
	costs this suite does not set up)."""
	frappe.get_doc(
		{
			"doctype": "Asset Repair",
			"asset": asset,
			"company": company(),
			"failure_date": add_days(now_datetime(), -1),
			"completion_date": day or now_datetime(),
			"repair_status": "Completed",
			"description": "Replaced the display",
			"docstatus": 1,
		}
	).db_insert()


def move(asset: str, purpose: str = "Transfer", day=None) -> None:
	"""A submitted ERPNext asset movement of the asset, written as ERPNext submits one."""
	doc = frappe.get_doc(
		{
			"doctype": "Asset Movement",
			"company": company(),
			"purpose": purpose,
			"transaction_date": day or now_datetime(),
			"docstatus": 1,
			"assets": [{"asset": asset, "source_location": "Room A", "target_location": "Room B"}],
		}
	)
	doc.set_new_name()
	doc.set_parent_in_children()
	doc.db_insert()
	for row in doc.assets:
		row.name = frappe.generate_hash(length=10)
		row.docstatus = 1
		row.db_insert()


@verifies("URS-EQP-013")
class TestAfterRepairOrMove(CareTestCase):
	def test_a_repaired_instrument_is_fit_again_only_once_calibrated_since(self):
		asset = self.approved()
		self.calibrated(asset, performed_on=add_days(today(), -2))
		complete_repair(asset, add_days(now_datetime(), -1))
		self.assertIn("repaired on", " ".join(equipment_care.unfit([asset])[asset]))
		self.calibrated(asset)
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_a_calibration_on_the_day_of_the_repair_counts(self):
		asset = self.approved()
		self.calibrated(asset, performed_on=add_days(today(), -1))
		complete_repair(asset, add_days(now_datetime(), -1))
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_a_repair_cancelled_or_pending_does_not_ask_for_recalibration(self):
		asset = self.approved()
		self.calibrated(asset, performed_on=add_days(today(), -2))
		frappe.get_doc(
			{
				"doctype": "Asset Repair",
				"asset": asset,
				"company": company(),
				"failure_date": add_days(now_datetime(), -1),
				"repair_status": "Cancelled",
				"description": "Not repaired",
				"docstatus": 1,
			}
		).db_insert()
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_a_move_alone_does_not_ask_an_instrument_for_a_calibration(self):
		asset = self.approved()
		self.calibrated(asset, performed_on=add_days(today(), -2))
		move(asset)
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_the_schedule_says_why_equipment_is_not_fit(self):
		from meddevice_suite.qms.report.equipment_care_schedule.equipment_care_schedule import execute

		asset = self.approved()
		self.calibrated(asset, performed_on=add_days(today(), -2))
		complete_repair(asset, add_days(now_datetime(), -1))
		row = next(r for r in execute()[1] if r["asset"] == asset)
		self.assertIn("not been calibrated since", row["not_fit"])


@verifies("URS-EQP-006")
class TestRefusedAtTheInspection(CareTestCase):
	def test_an_instrument_not_fit_is_not_named_by_an_inspection(self):
		asset = self.approved()  # approved, never calibrated
		receipt, lot = self.received()
		self.verified(receipt)
		saved, self.instrument = self.instrument, asset  # the inspection names this instrument
		try:
			with self.assertRaisesRegex(frappe.ValidationError, f"Instrument {asset} is not used"):
				self.inspected(receipt, lot)
		finally:
			self.instrument = saved


@verifies("URS-EQP-006")
class TestRefusedAtTheWorkstation(RoutingTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from meddevice_suite.pilot.inspection import ensure_inspection_masters
		from meddevice_suite.setup.test_stock import approve_equipment_for_test, approve_for_test

		ensure_inspection_masters(
			company(), approve_for_test, approve_equipment_for_test
		)  # the machines' item

	def machine_at(self, workstation: str) -> str:
		from meddevice_suite.master_data.test_equipment import EquipmentTestCase as Fixture

		asset = Fixture.new_equipment(
			self,
			**{equipment.WORKSTATION: workstation, equipment.MAINTENANCE: 1, equipment.MAINTENANCE_DAYS: 30},
		)
		frappe.set_user(self.qa)
		sign_action("Asset", asset.name, "Approve", PASSWORD, "Machine checked")
		frappe.set_user("Administrator")
		care_in_date_for_test(asset.name)
		return asset.name

	def job_card(self, ws: str, op: str):
		card = frappe.get_doc(
			{
				"doctype": "Job Card",
				"operation": op,
				"workstation": ws,
				"company": company(),
				"for_quantity": 1,
			}
		)
		card.flags.ignore_mandatory = True  # no work order: the equipment rule is what is tested
		return card

	def test_no_job_card_is_saved_or_submitted_while_its_machine_is_overdue(self):
		ws, op, _line = self.approved_line()
		asset = self.machine_at(ws)
		card = self.job_card(ws, op)
		card.insert()  # fit: saved
		frappe.db.set_value("Maintenance Record", {"asset": asset}, "next_due_date", add_days(today(), -1))
		with self.assertRaisesRegex(
			frappe.ValidationError, f"equipment {asset} at its workstation is not fit"
		):
			frappe.get_doc("Job Card", card.name).save()
		unvalidated = frappe.get_doc("Job Card", card.name)
		unvalidated.flags.ignore_validate = True  # as code that saves without validation does
		with self.assertRaisesRegex(frappe.ValidationError, "not fit"):
			unvalidated.save()

	def test_checked_again_after_erpnext_fills_the_workstation(self):
		"""ERPNext's validate chooses a workstation of the card's type when time is logged
		(`JobCard.validate_time_logs`); the check runs after it as well as before."""
		events = frappe.get_hooks("doc_events")["Job Card"]
		for event in ("before_validate", "validate", "before_submit", "before_update_after_submit"):
			self.assertIn("meddevice_suite.qms.equipment_care.guard_job_card", events[event], event)

	def test_no_time_is_logged_past_the_job_cards_rules(self):
		ws, op, _line = self.approved_line()
		asset = self.machine_at(ws)
		card = self.job_card(ws, op).insert()
		frappe.db.set_value("Maintenance Record", {"asset": asset}, "next_due_date", add_days(today(), -1))
		with self.assertRaisesRegex(frappe.ValidationError, "not fit"):
			equipment_care.make_time_log(
				{"job_card_id": card.name, "from_time": now_datetime(), "completed_qty": 0}
			)


@verifies("URS-EQP-010", "URS-EQP-011", "URS-EQP-012")
class TestQualification(CareTestCase):
	def needing(self, **values) -> str:
		return self.approved(**{equipment.QUALIFICATION: 1, **values})

	def protocol(self) -> str:
		return work_instruction_for_test()

	def stage(self, asset: str, stage: str, outcomes=("Pass",), protocol: str | None = None, **values) -> str:
		frappe.set_user(self.maintenance)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": "Equipment Qualification",
						"asset": asset,
						"stage": stage,
						"occasion": "Initial",
						"protocol": protocol or self.sop(),
						"performed_on": today(),
						"tests": [
							{
								"test": f"T{i}",
								"acceptance_criteria": "Within limits",
								"observed": "Within limits",
								"outcome": o,
							}
							for i, o in enumerate(outcomes)
						],
						**values,
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def sop(self) -> str:
		title = "Qualification protocol for tests"
		existing = frappe.db.get_value("Controlled Document", {"title": title, "status": "Effective"})
		if existing:
			return existing
		doc = frappe.get_doc(
			{
				"doctype": "Controlled Document",
				"title": title,
				"document_type": "SOP",
				"department": "QA",
				"process_owner": "Administrator",
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		frappe.db.set_value("Controlled Document", doc.name, "status", "Effective")
		return doc.name

	def done(self, asset: str, stage: str, **values) -> str:
		name = self.stage(asset, stage, **values)
		self.sign(self.maintenance, "Equipment Qualification", name, "Execute")
		self.sign(self.qa, "Equipment Qualification", name, "Approve")
		return name

	def test_iq_oq_pq_in_order_make_it_qualified(self):
		asset = self.needing()
		self.calibrated(asset)
		self.assertIn("not qualified", " ".join(equipment_care.unfit([asset])[asset]))
		with self.assertRaisesRegex(frappe.ValidationError, "follows an approved, passed IQ"):
			self.stage(asset, "OQ")
		self.done(asset, "IQ")
		with self.assertRaisesRegex(frappe.ValidationError, "A PQ follows"):
			self.stage(asset, "PQ")
		self.done(asset, "OQ")
		self.done(asset, "PQ")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_a_pq_needs_an_oq_done_on_or_after_the_latest_iq(self):
		asset = self.needing()
		self.done(asset, "IQ", performed_on=add_days(today(), -5))
		self.done(asset, "OQ", performed_on=add_days(today(), -5))
		self.done(asset, "IQ")  # installed again: the OQ before it no longer counts
		with self.assertRaisesRegex(frappe.ValidationError, "A PQ follows"):
			self.stage(asset, "PQ")

	def test_a_failed_test_fails_it_and_the_performer_never_approves(self):
		asset = self.needing()
		failed = self.stage(asset, "IQ", outcomes=("Pass", "Fail"))
		self.assertEqual(frappe.db.get_value("Equipment Qualification", failed, "outcome"), "Fail")
		name = self.stage(asset, "IQ")
		with self.assertRaisesRegex(frappe.ValidationError, "once executed"):
			self.sign(self.qa, "Equipment Qualification", name, "Approve")
		self.sign(self.qa, "Equipment Qualification", name, "Execute")
		with self.assertRaisesRegex(frappe.PermissionError, "performer does not approve"):
			self.sign(self.qa, "Equipment Qualification", name, "Approve")

	def test_a_change_after_execution_needs_execution_again(self):
		asset = self.needing()
		name = self.stage(asset, "IQ")
		self.sign(self.maintenance, "Equipment Qualification", name, "Execute")
		frappe.set_user(self.maintenance)
		doc = frappe.get_doc("Equipment Qualification", name)
		doc.tests[0].observed = "Changed afterwards"
		doc.save()
		frappe.set_user("Administrator")
		self.assertIsNone(frappe.db.get_value("Equipment Qualification", name, "executed_by"))

	def test_the_protocol_is_an_effective_sop_or_form(self):
		with self.assertRaisesRegex(frappe.ValidationError, "effective SOP or form"):
			self.stage(self.needing(), "IQ", protocol=work_instruction_for_test())

	def test_requalification_due_after_its_interval(self):
		asset = self.needing(**{equipment.REQUALIFICATION_DAYS: 180})
		self.calibrated(asset)
		for stage in ("IQ", "OQ", "PQ"):
			self.done(asset, stage)
		self.assertNotIn(asset, equipment_care.unfit([asset], on=add_days(today(), 180)))
		self.assertIn(
			"requalification", " ".join(equipment_care.unfit([asset], on=add_days(today(), 181))[asset])
		)

	def qualified(self, days_ago: int = 2) -> str:
		asset = self.needing()
		self.calibrated(asset, performed_on=add_days(today(), -days_ago))
		for stage in ("IQ", "OQ", "PQ"):
			self.done(asset, stage, performed_on=add_days(today(), -days_ago))
		self.assertNotIn(asset, equipment_care.unfit([asset]))
		return asset

	@verifies("URS-EQP-013")
	def test_after_a_repair_a_machine_needs_a_pq_again(self):
		asset = self.qualified()
		complete_repair(asset, add_days(now_datetime(), -1))
		self.calibrated(asset)
		self.assertIn("has had no PQ since", " ".join(equipment_care.unfit([asset])[asset]))
		self.done(asset, "PQ", occasion="After relocation or repair")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	@verifies("URS-EQP-013")
	def test_after_a_move_a_machine_is_qualified_again_from_the_iq(self):
		asset = self.qualified()
		move(asset, day=add_days(now_datetime(), -1))
		self.assertIn("moved on", " ".join(equipment_care.unfit([asset])[asset]))
		self.done(asset, "PQ", occasion="After relocation or repair")
		self.assertIn("moved on", " ".join(equipment_care.unfit([asset])[asset]))
		for stage in ("IQ", "OQ", "PQ"):
			self.done(asset, stage, occasion="After relocation or repair")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	@verifies("URS-EQP-013")
	def test_a_receipt_or_an_issue_is_not_a_move(self):
		asset = self.qualified()
		move(asset, purpose="Receipt")
		move(asset, purpose="Issue")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_an_approval_signed_before_the_new_field_still_verifies(self):
		from meddevice_suite.compliance.esign import verify_signatures

		asset = self.approved()
		self.assertTrue(verify_signatures("Asset", asset))
		# What an approval signed before the field binds: the field is in it only once it is set.
		self.assertNotIn(
			equipment.REQUALIFICATION_DAYS, equipment.signed_content(frappe.get_doc("Asset", asset))
		)
		set_interval = self.needing(**{equipment.REQUALIFICATION_DAYS: 180})
		self.assertEqual(
			equipment.signed_content(frappe.get_doc("Asset", set_interval))[equipment.REQUALIFICATION_DAYS],
			180,
		)


@verifies("URS-EQP-008")
class TestReminders(CareTestCase):
	def test_calibration_due_is_tracked_until_a_calibration_moves_it_on(self):
		asset = self.approved()
		frappe.set_user("Administrator")
		reminders.run()
		raised = frappe.get_all(
			"Reminder", filters={"reference_name": asset, "category": "Calibration Due"}, fields=["status"]
		)
		self.assertTrue(raised and raised[0].status in ("Open", "Overdue", "Escalated"))
		self.calibrated(asset)
		reminders.run()
		self.assertEqual(
			frappe.get_all(
				"Reminder",
				filters={
					"reference_name": asset,
					"category": "Calibration Due",
					"status": ["!=", "Completed"],
				},
			),
			[],
		)

	def test_an_open_impact_assessment_is_tracked_until_qa_signs_it(self):
		record = self.calibrated(self.approved(), points=[point(found=100.05)])
		reminders.run()
		self.assertTrue(
			frappe.db.exists(
				"Reminder",
				{
					"reference_name": record.name,
					"category": "Calibration Impact",
					"status": ["!=", "Completed"],
				},
			)
		)
		self.sign(self.qa, "Calibration Record", record.name, "Assess: No Product Impact", "Assessed")
		reminders.run()
		self.assertFalse(
			frappe.db.exists(
				"Reminder",
				{
					"reference_name": record.name,
					"category": "Calibration Impact",
					"status": ["!=", "Completed"],
				},
			)
		)


@verifies("URS-EQP-007", "URS-EQP-009", "URS-MST-011")
class TestScheduleAndRecords(CareTestCase):
	def test_the_schedule_shows_each_state(self):
		from meddevice_suite.qms.report.equipment_care_schedule.equipment_care_schedule import execute

		never = self.approved()
		in_date = self.approved()
		self.calibrated(in_date)
		frappe.set_user(self.qa)
		_columns, rows = execute({})
		by_asset = {row["asset"]: row for row in rows}
		self.assertEqual(by_asset[never]["calibration"], "Never done")
		self.assertEqual(by_asset[in_date]["calibration"], "In date")
		with freeze_time(add_days(today(), 400)):
			_columns, rows = execute({})
		self.assertEqual({row["asset"]: row for row in rows}[in_date]["calibration"], "Overdue")

	def test_erpnexts_maintenance_plans_are_not_used(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Calibration and Maintenance Records"):
			frappe.get_doc(
				{"doctype": "Asset Maintenance", "asset_name": self.approved(), "company": company()}
			).insert(ignore_permissions=True)

	def test_the_history_card_shows_care_records_and_out_of_tolerance(self):
		asset = self.approved()
		self.calibrated(asset, points=[point(found=100.05)])
		frappe.set_user(self.qa)
		rows, summary = self.card(asset)
		self.assertIn("Calibration Record", {row["record_type"] for row in rows})
		self.assertEqual(summary["Found out of tolerance"], 1)
