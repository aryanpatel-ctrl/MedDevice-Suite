"""Calibration, maintenance and qualification (modules 38 and 40; URS-EQP-001 to URS-EQP-012, and the
history card's additions to URS-MST-011): tests derived from the requirements' clauses."""

from unittest.mock import patch

import frappe
from frappe.tests.classes.context_managers import freeze_time
from frappe.utils import add_days, getdate, now_datetime, today

from meddevice_suite.compliance import esign
from meddevice_suite.compliance.esign import is_unchanged_since
from meddevice_suite.compliance.evidence_files import is_evidence
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_audit import events_for
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import company, lot_item, store
from meddevice_suite.master_data import equipment, item_master
from meddevice_suite.master_data.test_equipment import EquipmentTestCase
from meddevice_suite.master_data.test_routing import RoutingTestCase
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa import verifies
from meddevice_suite.qms import calibration, equipment_care
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.qms.test_document_control import private_file
from meddevice_suite.setup.test_stock import (
	approve_for_test,
	care_in_date_for_test,
	production_line_for_test,
	submit_bom_for_test,
	work_instruction_for_test,
)
from meddevice_suite.work_management import reminders

IN = {"parameter": "Length 100 mm", "unit": "mm", "nominal": 100, "lower_limit": 99.98, "upper_limit": 100.02}
# A calibration's impact reminder not yet completed.
OPEN_IMPACT = {"category": "Calibration Impact", "status": ["!=", "Completed"]}


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
					"certificate_number": f"LAB-{frappe.generate_hash(length=10)}",
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
	) -> str | None:
		"""Sign the act as the user; the name of a record the act opened."""
		frappe.set_user(user)
		try:
			return sign_action(doctype, name, act, PASSWORD, reason)
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

	def workstations(self, count: int) -> list[str]:
		return [
			frappe.get_doc(
				{
					"doctype": "Workstation",
					"workstation_name": f"MDS Care WS {frappe.generate_hash(length=6)}",
				}
			)
			.insert()
			.name
			for _each in range(count)
		]

	def change_workstation(self, asset: str, workstation: str, offset: int, at: str = "11:00:00") -> None:
		"""Moved to another workstation on the form, on the day `offset` days from today, under an
		approved change request, as the record of approved equipment is changed. The audit trail
		records the change at that time."""
		change = approved_change(signer("Production Supervisor"), self.qa, asset, "Asset", "Equipment")
		frappe.set_user(self.qa)
		try:
			with freeze_time(f"{on_day(offset)} {at}"):
				doc = frappe.get_doc("Asset", asset)
				doc.set(equipment.WORKSTATION, workstation)
				doc.set(CHANGE_REQUEST, change)
				doc.save()
		finally:
			frappe.set_user("Administrator")

	def machine(self) -> str:
		return self.approved(**{equipment.MAINTENANCE: 1, equipment.MAINTENANCE_DAYS: 90})

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

	def test_its_certificate_stays_evidence_while_the_record_is_corrected(self):
		"""A withdrawn calibration is still the record of what was found; its certificate is kept."""
		from meddevice_suite.compliance import evidence_files

		record = self.calibrated(self.approved())
		self.sign(self.qa, "Calibration Record", record.name, "Correct", "Wrong certificate number")
		self.assertEqual(frappe.db.get_value("Calibration Record", record.name, "docstatus"), 2)
		self.assertTrue(evidence_files.is_evidence(record.certificate))
		file = frappe.get_doc("File", {"file_url": record.certificate, "attached_to_name": record.name})
		frappe.set_user(self.qa)
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("File", file.name)
		finally:
			frappe.set_user("Administrator")

	def test_a_certificate_shared_with_another_record_does_not_stop_the_correction(self):
		"""One laboratory certificate often covers several instruments, and identical files share one
		address: Frappe's amend checked the signer against whichever file had it, here one only its
		owner may read, and refused the correction."""
		record = self.calibrated(self.approved())
		# The same file, uploaded by Maintenance for another instrument and not yet attached: only its
		# owner may read it.
		shared = frappe.copy_doc(
			frappe.get_doc("File", {"file_url": record.certificate, "attached_to_name": record.name})
		)
		shared.update({"attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None})
		shared.set_new_name()
		shared.owner = self.maintenance
		shared.db_insert()  # as Frappe stores an upload; its own insert would rename a file it did not write
		self.assertEqual(shared.file_url, record.certificate)

		# And a supporting file attached to the record, as the form's attachments do.
		data_sheet = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "raw-readings.txt",
				"content": "Raw readings of the calibration",
				"is_private": 1,
				"attached_to_doctype": "Calibration Record",
				"attached_to_name": record.name,
			}
		).insert()

		amendment = self.sign(
			self.qa, "Calibration Record", record.name, "Correct", "Wrong certificate number"
		)
		self.assertEqual(
			sorted(
				frappe.get_all(
					"File",
					filters={"attached_to_doctype": "Calibration Record", "attached_to_name": amendment},
					pluck="file_url",
				)
			),
			sorted([record.certificate, data_sheet.file_url]),
		)


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

	def test_an_inspection_submitted_after_the_finding_is_added_by_its_submit(self):
		"""Independent review of 2026-10-15: the list was taken only at completion, so an inspection
		of the period submitted afterwards was never assessed. Through the inspection's own submit."""
		asset = self.approved()
		record = self.calibrated(asset, points=[point(found=100.05)])  # its period is today
		self.assertEqual((record.impact_status, record.impact), ("Open", []))
		self.sign(self.qa, "Calibration Record", record.name, "Assess: No Product Impact", "Nothing measured")
		receipt, lot = self.received()
		self.verified(receipt)
		saved, self.instrument = self.instrument, asset  # the inspection names this instrument
		try:
			inspection = self.inspected(receipt, lot)
		finally:
			self.instrument = saved
		record.reload()
		self.assertEqual(
			[(row.reference_doctype, row.reference_name, row.batch_no) for row in record.later_impact],
			[("Quality Inspection", inspection.name, lot)],
		)
		self.assertEqual((record.impact_status, record.impact_decision), ("Open", None))
		# Another instrument's finding is not this inspection's.
		other = self.calibrated(self.approved(), points=[point(found=100.05)])
		self.assertEqual(frappe.get_doc("Calibration Record", other.name).later_impact, [])


def on_day(offset: int):
	"""The day `offset` days from today."""
	return getdate(add_days(today(), offset))


@verifies("URS-EQP-003")
class TestImpactFollowsTheInstrument(CareTestCase):
	"""The job cards an out-of-tolerance instrument may have affected are those of each workstation
	it was at, while it was there, read from the audit trail (found in the foundation review of
	2026-10-12). Created today, it moves on later days, frozen there, so the trail records each
	move at its own time as it does on the plant."""

	def instrument_at(self, workstation: str) -> str:
		return self.approved(**{equipment.WORKSTATION: workstation})

	def job_card(self, workstation: str, offset: int) -> str:
		"""A submitted job card made at the workstation on the day, stored as it stands: the rules at
		the job card are tested with it (TestRefusedAtTheWorkstation); here only where and when."""
		card = frappe.new_doc("Job Card")
		card.update(
			{
				"workstation": workstation,
				"posting_date": on_day(offset),
				"company": company(),
				"for_quantity": 1,
				"docstatus": 1,
			}
		)
		card.set_new_name()
		card.db_insert()
		return card.name

	def cards(self, asset: str, start: int, end: int) -> dict[str, str]:
		"""The job cards listed for the period, with the workstation each row names."""
		return {
			row["reference_name"]: row["workstation"]
			for row in calibration.impact_since(asset, on_day(start), on_day(end))
			if row["reference_doctype"] == "Job Card"
		}

	def history(self, asset: str, start: int, end: int) -> list[tuple]:
		return calibration.workstation_history(asset, on_day(start), on_day(end))

	def test_moved_in_the_period_each_workstation_counts_only_while_it_was_there(self):
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		self.change_workstation(asset, b, 10)
		at_a_before, at_a_after = self.job_card(a, 5), self.job_card(a, 15)
		at_b_before, at_b_after = self.job_card(b, 5), self.job_card(b, 15)
		# The trail knows the time of the move, a job card only its day: that day is in doubt at both.
		at_a_that_day, at_b_that_day = self.job_card(a, 10), self.job_card(b, 10)

		self.assertEqual(
			self.history(asset, 1, 30), [(a, on_day(1), on_day(10)), (b, on_day(10), on_day(30))]
		)
		self.assertEqual(
			self.cards(asset, 1, 30),
			{at_a_before: a, at_a_that_day: a, at_b_that_day: b, at_b_after: b},
		)
		listed = self.cards(asset, 1, 30)
		self.assertNotIn(at_a_after, listed)
		self.assertNotIn(at_b_before, listed)

	def test_not_moved_its_workstation_counts_for_the_whole_period(self):
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		inside, after, elsewhere = self.job_card(a, 5), self.job_card(a, 31), self.job_card(b, 5)

		self.assertEqual(self.history(asset, 1, 30), [(a, on_day(1), on_day(30))])
		self.assertEqual(self.cards(asset, 1, 30), {inside: a})
		self.assertNotIn(after, self.cards(asset, 1, 30))
		self.assertNotIn(elsewhere, self.cards(asset, 1, 30))

	def test_moved_twice_three_workstations_each_for_its_own_days(self):
		a, b, c = self.workstations(3)
		asset = self.instrument_at(a)
		self.change_workstation(asset, b, 10)
		self.change_workstation(asset, c, 20)
		at_a, at_b, at_c = self.job_card(a, 5), self.job_card(b, 15), self.job_card(c, 25)
		not_listed = [self.job_card(a, 15), self.job_card(b, 5), self.job_card(b, 25), self.job_card(c, 15)]

		self.assertEqual(
			self.history(asset, 1, 30),
			[(a, on_day(1), on_day(10)), (b, on_day(10), on_day(20)), (c, on_day(20), on_day(30))],
		)
		listed = self.cards(asset, 1, 30)
		self.assertEqual({name: listed.get(name) for name in (at_a, at_b, at_c)}, {at_a: a, at_b: b, at_c: c})
		self.assertFalse(set(not_listed) & set(listed))

	def test_moved_just_before_the_period_only_the_new_workstation_counts(self):
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		self.change_workstation(asset, b, 10)
		at_a, at_b = self.job_card(a, 15), self.job_card(b, 15)

		self.assertEqual(self.history(asset, 11, 30), [(b, on_day(11), on_day(30))])
		self.assertEqual(self.cards(asset, 11, 30), {at_b: b})
		self.assertNotIn(at_a, self.cards(asset, 11, 30))

	def test_moved_after_the_period_only_the_old_workstation_counts(self):
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		self.change_workstation(asset, b, 10)
		at_a, at_b = self.job_card(a, 5), self.job_card(b, 5)

		self.assertEqual(self.history(asset, 1, 9), [(a, on_day(1), on_day(9))])
		self.assertEqual(self.cards(asset, 1, 9), {at_a: a})
		self.assertNotIn(at_b, self.cards(asset, 1, 9))

	def alerts(self, name: str) -> int:
		return frappe.db.count(
			"Notification Log",
			{"document_type": "Calibration Record", "document_name": name, "for_user": self.qa},
		)

	def test_a_job_card_submitted_after_the_finding_is_added_and_the_assessment_opens_again(self):
		"""Independent review of 2026-10-15: a job card drafted before an out-of-tolerance finding and
		submitted after it was never assessed (ISO 13485 7.6). Its submit adds it, by the same rule
		as the list taken at completion, and QA assesses again; the submit is not refused."""
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		listed = self.job_card(a, 5)
		with freeze_time(f"{on_day(20)} 10:00:00"):
			record = self.calibrated(asset, points=[point(found=100.05)])
		self.sign(self.qa, "Calibration Record", record.name, "Assess: No Product Impact", "Readings held")
		reminders.run()
		self.assertFalse(frappe.db.exists("Reminder", {"reference_name": record.name, **OPEN_IMPACT}))
		alerts = self.alerts(record.name)

		late, elsewhere, after = self.job_card(a, 10), self.job_card(b, 10), self.job_card(a, 25)
		for card in (late, elsewhere, after, late, listed):  # late twice: listed once
			calibration.add_later_use(frappe.get_doc("Job Card", card), "on_submit")

		record.reload()
		self.assertEqual([row.reference_name for row in record.impact], [listed])
		self.assertEqual(
			[(row.reference_doctype, row.reference_name, row.workstation) for row in record.later_impact],
			[("Job Card", late, a)],
		)
		self.assertEqual(
			(record.impact_status, record.impact_decision, record.assessed_by), ("Open", None, None)
		)
		(event,) = [e for e in events_for("Calibration Record", record.name) if late in (e.reason or "")]
		self.assertEqual(event.action, "Updated after submission")
		self.assertGreater(self.alerts(record.name), alerts)
		# What the performer and QA signed stands: the rows added since are kept out of what they bind.
		for signature in frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
			pluck="name",
		):
			self.assertTrue(is_unchanged_since(frappe.get_doc("Electronic Signature", signature)))
		self.assertTrue(esign.verify_signatures("Calibration Record", record.name))
		reminders.run()
		self.assertTrue(frappe.db.exists("Reminder", {"reference_name": record.name, **OPEN_IMPACT}))
		self.sign(
			self.qa, "Calibration Record", record.name, "Assess: Product Action Needed", f"{late} to recheck"
		)
		self.assertEqual(
			frappe.db.get_value("Calibration Record", record.name, ["impact_status", "impact_decision"]),
			("Assessed", "Product action needed"),
		)

	def test_the_submits_of_job_cards_and_inspections_add_their_later_use(self):
		events = frappe.get_hooks("doc_events")
		for doctype in ("Job Card", "Quality Inspection"):
			with self.subTest(doctype=doctype):
				self.assertIn("meddevice_suite.qms.calibration.add_later_use", events[doctype]["on_submit"])

	def test_read_in_a_fixed_number_of_queries_however_often_it_moved(self):
		a, b, c = self.workstations(3)
		still, moved = self.instrument_at(a), self.instrument_at(a)
		for workstation, offset in ((b, 5), (c, 10), (a, 15), (b, 20)):
			self.change_workstation(moved, workstation, offset)
		self.job_card(a, 3)
		self.job_card(b, 25)

		for asset in (still, moved):
			with self.assertQueryCount(1):
				self.history(asset, 1, 30)
			# The inspections, where it was, and the job cards of every workstation at once.
			with self.assertQueryCount(3):
				calibration.impact_since(asset, on_day(1), on_day(30))
		self.assertEqual(len(self.history(moved, 1, 30)), 5)

	def test_the_completed_record_names_each_job_cards_workstation_and_binds_it(self):
		"""Through the performer's signed completion: its last good calibration is the day the
		instrument was recorded (none before), so the period starts with its record's creation."""
		a, b = self.workstations(2)
		asset = self.instrument_at(a)
		self.change_workstation(asset, b, 10)
		at_a, at_b, at_a_after = self.job_card(a, 5), self.job_card(b, 15), self.job_card(a, 15)
		with freeze_time(f"{on_day(20)} 10:00:00"):
			record = self.calibrated(asset, points=[point(found=100.05)])

		self.assertEqual(getdate(record.last_good_calibration), getdate(today()))
		rows = {row.reference_name: row for row in record.impact if row.reference_doctype == "Job Card"}
		self.assertEqual({name: row.workstation for name, row in rows.items()}, {at_a: a, at_b: b})
		self.assertNotIn(at_a_after, rows)
		completion = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
		)
		self.assertTrue(is_unchanged_since(completion))
		frappe.db.set_value("Calibration Impact", rows[at_a].name, "workstation", b)
		self.assertFalse(is_unchanged_since(completion))  # the workstation listed is signed

	@verifies("URS-CMP-004")
	def test_an_out_of_tolerance_completion_verifies_with_the_impact_rows_it_added(self):
		"""The impact rows are listed by the signed completion itself, before the record is saved:
		they are signed with the name, owner and creation they are stored with (compliance.esign
		identify_new_rows), so the signature verifies once the record is completed."""
		a = self.workstations(1)[0]
		asset = self.instrument_at(a)
		self.job_card(a, 5)
		with freeze_time(f"{on_day(20)} 10:00:00"):
			record = self.calibrated(asset, points=[point(found=100.05)])
		self.assertTrue(record.impact)
		completion = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
		)
		self.assertTrue(is_unchanged_since(completion))
		self.assertTrue(esign.verify_signatures("Calibration Record", record.name))

	@verifies("URS-CMP-004")
	def test_a_record_signed_before_rows_named_a_workstation_still_verifies(self):
		"""A release never invalidates a signature that verified (21 CFR 11.70): a calibration whose
		impact rows were listed and signed before they carried a workstation still verifies."""
		a = self.workstations(1)[0]
		asset = self.instrument_at(a)
		self.job_card(a, 5)
		with freeze_time(f"{on_day(20)} 10:00:00"):
			record = self.calibrated(asset, points=[point(found=100.05)])
		# As a record made before the column: its rows have no workstation.
		frappe.db.set_value("Calibration Impact", {"parent": record.name}, "workstation", None)
		real = esign.record_content

		def before_the_column(doc):
			content = real(doc)
			for row in content.get("impact") or []:
				row.pop("workstation", None)
			return content

		# Fingerprint version 1, the version such a record was signed with.
		with (
			patch.object(esign, "record_content", before_the_column),
			patch.object(esign, "CURRENT_VERSION", 1),
		):
			self.sign(self.qa, "Calibration Record", record.name, "Assess: No Product Impact")
		# By its act: the completion, signed on a day frozen ahead, is the record's latest by creation.
		assessment = frappe.get_last_doc(
			"Electronic Signature",
			filters={
				"reference_doctype": "Calibration Record",
				"reference_name": record.name,
				"action": "Assess: No Product Impact",
			},
		)
		self.assertTrue(is_unchanged_since(assessment))


@verifies("URS-EQP-004")
class TestMaintenance(CareTestCase):
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
		from meddevice_suite.setup.fixtures.inspection import ensure_inspection_masters
		from meddevice_suite.setup.test_stock import approve_equipment_for_test

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

	def test_equipment_kept_at_no_workstation_never_holds_up_production(self):
		"""A portable instrument not yet calibrated, assigned to no workstation, is not the
		equipment of an operation that names no workstation."""
		from meddevice_suite.master_data.test_equipment import EquipmentTestCase as Fixture

		loose = Fixture.new_equipment(self).name  # needs calibration, never calibrated, no workstation
		frappe.set_user(self.qa)
		sign_action("Asset", loose, "Approve", PASSWORD, "Caliper checked")
		frappe.set_user("Administrator")
		self.assertIn(loose, equipment_care.unfit([loose]))
		self.assertEqual(equipment_care.equipment_at([None, ""]), [])
		equipment_care.refuse_unusable([None], "A work order")  # nothing at no workstation

	def order_on(self, line: str | None) -> frappe.model.document.Document:
		"""A draft work order of a new sub-assembly on its bill of materials, which has the routing, or
		none, with the bill's materials and operations as the form takes them."""
		product, material = lot_item(f"MDS-EC-SA-{self.suffix()}"), lot_item(f"MDS-EC-M-{self.suffix()}")
		approve_for_test(product, material)
		frappe.db.set_value("Item", product, item_master.ITEM_TYPE, item_master.SUB_ASSEMBLY)  # test fixture
		bill = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
				**({"with_operations": 1, "routing": line} if line else {}),
			}
		).insert()
		submit_bom_for_test(bill)
		order = frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": product,
				"bom_no": bill.name,
				"qty": 1,
				"company": company(),
				"source_warehouse": store("Stores"),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store("Quarantine"),
			}
		)
		order.get_items_and_operations_from_bom()
		return order.insert()

	def submit(self, name: str, skip_validation: bool) -> None:
		order = frappe.get_doc("Work Order", name)
		order.flags.ignore_validate = skip_validation
		order.submit()

	def test_a_work_order_without_a_routing_is_not_submitted(self):
		"""Independent review of 2026-10-15: the equipment a work order uses is found through its
		operations' workstations, so one without operations went into production with none checked.
		A draft is planning and is kept; it is not submitted, also when validation is skipped."""
		bare = self.order_on(None)
		self.assertEqual(bare.operations, [])
		for skipped in (False, True):
			with self.subTest(validation_skipped=skipped):
				with self.assertRaisesRegex(frappe.ValidationError, "has no routing"):
					self.submit(bare.name, skipped)
		line, _workstation = production_line_for_test()
		emptied = self.order_on(line)
		emptied.set("operations", [])
		emptied.save()
		with self.assertRaisesRegex(frappe.ValidationError, "has no operations: take them from"):
			self.submit(emptied.name, False)
		routed = self.order_on(line)
		self.submit(routed.name, False)
		self.assertEqual(frappe.db.get_value("Work Order", routed.name, "docstatus"), 1)

	def test_a_work_order_is_refused_at_submit_while_its_machine_is_overdue_also_unvalidated(self):
		"""Frappe skips before_submit when a caller skips validation: the check runs at before_validate
		of the submit as well (found with the review of 2026-10-15)."""
		line, workstation = production_line_for_test()
		asset = self.machine_at(workstation)
		order = self.order_on(line)
		frappe.db.set_value("Maintenance Record", {"asset": asset}, "next_due_date", add_days(today(), -1))
		for skipped in (False, True):
			with self.subTest(validation_skipped=skipped):
				with self.assertRaisesRegex(
					frappe.ValidationError, f"equipment {asset} at its workstation is not fit"
				):
					self.submit(order.name, skipped)
		events = frappe.get_hooks("doc_events")["Work Order"]
		for event in ("before_validate", "before_submit"):
			self.assertIn("meddevice_suite.qms.equipment_care.guard_work_order", events[event], event)

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

	@verifies("URS-EQP-001", "URS-EQP-004")
	def test_no_care_record_is_dated_ahead(self):
		"""Independent review of 2026-10-15: a qualification could carry a future date, which would
		qualify the equipment from a day that has not come; calibration and maintenance refused one."""
		tomorrow = add_days(today(), 1)
		with self.assertRaisesRegex(frappe.ValidationError, "A qualification is recorded once it is done"):
			self.stage(self.needing(), "IQ", performed_on=tomorrow)
		with self.assertRaisesRegex(frappe.ValidationError, "Maintenance is recorded once it is done"):
			self.maintenance_record(self.machine(), performed_on=tomorrow)
		with self.assertRaisesRegex(frappe.ValidationError, "A calibration is recorded once it is done"):
			self.calibration(self.approved(), performed_on=tomorrow)
		self.assertTrue(self.stage(self.needing(), "IQ"))  # done today: recorded

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

	def qualified(self, days_ago: int = 2, **values) -> str:
		asset = self.needing(**values)
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
	def test_a_workstation_changed_after_the_qualification_is_a_move(self):
		"""Decided 2026-10-15: moved to another line on the form, as by an asset movement. Its record,
		made today at its first workstation, is no move (`qualified` finds it fit)."""
		a, b = self.workstations(2)
		asset = self.qualified(**{equipment.WORKSTATION: a})  # IQ, OQ and PQ two days ago
		self.change_workstation(asset, b, -1)
		self.assertIn(
			f"it was moved on {frappe.format_value(on_day(-1), {'fieldtype': 'Date'})} ",
			" ".join(equipment_care.unfit([asset])[asset]),
		)
		self.done(asset, "PQ", occasion="After relocation or repair")
		self.assertIn("moved on", " ".join(equipment_care.unfit([asset])[asset]))
		for stage in ("IQ", "OQ", "PQ"):
			self.done(asset, stage, occasion="After relocation or repair")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	@verifies("URS-EQP-013")
	def test_a_workstation_changed_before_the_qualification_or_on_its_day_is_not(self):
		"""Read by the day, as an asset movement: a qualification on the day of the change counts as
		after it, and the day after the qualification does not."""
		a, b, c = self.workstations(3)
		before = self.needing(**{equipment.WORKSTATION: a})
		self.change_workstation(before, b, -3)
		same_day = self.needing(**{equipment.WORKSTATION: a})
		self.change_workstation(same_day, b, -2, at="23:00:00")
		for asset in (before, same_day):
			self.calibrated(asset, performed_on=add_days(today(), -2))
			for stage in ("IQ", "OQ", "PQ"):
				self.done(asset, stage, performed_on=add_days(today(), -2))
		self.assertEqual(equipment_care.repaired_and_moved([same_day])[1], {same_day: on_day(-2)})
		self.assertNotIn(before, equipment_care.unfit([before]))
		self.assertNotIn(same_day, equipment_care.unfit([same_day]))
		self.change_workstation(same_day, c, -1, at="00:30:00")
		self.assertIn("moved on", " ".join(equipment_care.unfit([same_day])[same_day]))

	@verifies("URS-EQP-013")
	def test_the_latest_relocation_is_the_later_of_a_movement_and_a_workstation_change(self):
		a, b = self.workstations(2)
		changed_last = self.qualified(days_ago=5, **{equipment.WORKSTATION: a})
		move(changed_last, day=add_days(now_datetime(), -4))
		self.change_workstation(changed_last, b, -1)
		moved_last = self.qualified(days_ago=5, **{equipment.WORKSTATION: a})
		self.change_workstation(moved_last, b, -4)
		move(moved_last, day=add_days(now_datetime(), -1))
		not_moved = self.qualified(days_ago=5)

		_repaired, moved = equipment_care.repaired_and_moved([changed_last, moved_last, not_moved])
		self.assertEqual(moved, {changed_last: on_day(-1), moved_last: on_day(-1)})

	def test_relocations_are_read_in_a_fixed_number_of_queries(self):
		a, b = self.workstations(2)
		one = [self.needing(**{equipment.WORKSTATION: a})]
		many = [self.needing(**{equipment.WORKSTATION: a}) for _each in range(3)]
		for asset in many:
			self.change_workstation(asset, b, -2)
			move(asset, day=add_days(now_datetime(), -1))
		equipment_care.repaired_and_moved(one)  # metadata loaded once
		for assets in (one, many):
			# The repairs, the asset movements and the workstation changes.
			with self.assertQueryCount(3):
				equipment_care.repaired_and_moved(assets)

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


@verifies(
	"URS-EQP-001", "URS-EQP-003", "URS-EQP-004", "URS-EQP-005", "URS-EQP-008", "URS-EQP-010", "URS-EQP-012"
)
class TestCorrection(CareTestCase):
	"""A completed record is corrected only by QA's signed Correct (decided 2026-10-12, plan D-29)."""

	REASON = "Certificate number mistyped"

	def correct(
		self, doctype: str, name: str, user: str | None = None, reason: str | None = REASON
	) -> str | None:
		return self.sign(user or self.qa, doctype, name, "Correct", reason)

	def why(self, asset: str) -> list[str]:
		return equipment_care.unfit([asset]).get(asset, [])

	def test_frappes_own_cancel_is_refused_for_each_record_type(self):
		calibration = self.calibrated(self.approved()).name
		maintenance = self.maintenance_record(self.machine())
		self.sign(self.maintenance, "Maintenance Record", maintenance, "Complete")
		qualification = self.done(self.needing(), "IQ")
		for doctype, name in (
			("Calibration Record", calibration),
			("Maintenance Record", maintenance),
			("Equipment Qualification", qualification),
		):
			with self.subTest(doctype):
				self.assertFalse(frappe.has_permission(doctype, "cancel", user=self.qa))
				doc = frappe.get_doc(doctype, name)
				doc.flags.ignore_permissions = True  # past permissions, as code would
				doc.flags.audit_reason = self.REASON
				with self.assertRaisesRegex(frappe.PermissionError, "signing Correct"):
					doc.cancel()
				self.assertEqual(frappe.db.get_value(doctype, name, "docstatus"), 1)

	def test_qas_signed_correction_cancels_the_record_and_opens_its_amendment(self):
		record = self.calibrated(self.approved())
		amendment = frappe.get_doc("Calibration Record", self.correct("Calibration Record", record.name))
		record.reload()
		self.assertEqual(record.docstatus, 2)
		self.assertEqual(
			(amendment.docstatus, amendment.amended_from, amendment.asset, amendment.certificate_number),
			(0, record.name, record.asset, record.certificate_number),
		)
		self.assertEqual((amendment.completed_by, amendment.next_due_date), (None, None))
		signature = frappe.get_last_doc(
			"Electronic Signature",
			filters={"reference_doctype": "Calibration Record", "reference_name": record.name},
		)
		self.assertEqual(
			(signature.signer, signature.meaning, signature.action, signature.reason),
			(self.qa, "Rejected", "Correct", self.REASON),
		)
		cancelled = [
			e.reason for e in events_for("Calibration Record", record.name) if e.action == "Cancelled"
		]
		self.assertEqual(cancelled, [self.REASON])
		self.assertTrue(is_evidence(record.certificate))  # the withdrawn record keeps its certificate

	def test_the_equipment_is_not_fit_until_the_corrected_calibration_is_completed(self):
		asset = self.approved()
		self.calibrated(asset)
		failed = self.calibrated(asset, points=[point(left=100.05)]).name
		amendment = self.correct("Calibration Record", failed)
		# Without the failed record the pass before it is the latest: the correction holds it unfit.
		self.assertIn(f"its calibration {failed} is being corrected", self.why(asset))
		self.assertFalse(equipment.usable(asset).ok)
		frappe.set_user(self.maintenance)
		try:
			draft = frappe.get_doc("Calibration Record", amendment)
			draft.points[0].as_left = 100.0  # adjusted again and read within its limits
			draft.save()
		finally:
			frappe.set_user("Administrator")
		self.assertIn(f"its calibration {failed} is being corrected", self.why(asset))
		self.sign(self.maintenance, "Calibration Record", amendment, "Complete")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_corrected_maintenance_holds_the_machine_until_completed_again(self):
		machine = self.machine()
		self.calibrated(machine)
		done = self.maintenance_record(machine)
		self.sign(self.maintenance, "Maintenance Record", done, "Complete")
		self.assertNotIn(machine, equipment_care.unfit([machine]))
		amendment = self.correct("Maintenance Record", done)
		self.assertIn(f"its preventive maintenance {done} is being corrected", self.why(machine))
		self.sign(self.maintenance, "Maintenance Record", amendment, "Complete")
		self.assertNotIn(machine, equipment_care.unfit([machine]))

	def test_a_corrected_qualification_holds_the_machine_until_approved_again(self):
		asset = self.needing()
		self.calibrated(asset)
		self.done(asset, "IQ")
		self.done(asset, "OQ")
		pq = self.done(asset, "PQ")
		self.assertNotIn(asset, equipment_care.unfit([asset]))
		amendment = self.correct("Equipment Qualification", pq)
		self.assertIn(f"its qualification {pq} is being corrected", self.why(asset))
		self.sign(self.maintenance, "Equipment Qualification", amendment, "Execute")
		self.assertIn(f"its qualification {pq} is being corrected", self.why(asset))  # not yet approved
		self.sign(self.qa, "Equipment Qualification", amendment, "Approve")
		self.assertNotIn(asset, equipment_care.unfit([asset]))

	def test_an_open_impact_assessment_stays_open_and_tracked_after_the_correction(self):
		record = self.calibrated(self.approved(), points=[point(found=100.05)])
		reminders.run()
		self.correct("Calibration Record", record.name)
		reminders.run()
		self.assertEqual(
			frappe.db.get_value("Calibration Record", record.name, ["docstatus", "impact_status"]),
			(2, "Open"),
		)
		# The correction does not complete the reminder: only QA's signed assessment does.
		self.assertTrue(frappe.db.exists("Reminder", {"reference_name": record.name, **OPEN_IMPACT}))
		self.sign(
			self.qa,
			"Calibration Record",
			record.name,
			"Assess: No Product Impact",
			"Readings were within half the tolerance",
		)
		self.assertEqual(
			frappe.db.get_value("Calibration Record", record.name, ["impact_status", "assessed_by"]),
			("Assessed", self.qa),
		)
		reminders.run()
		self.assertFalse(frappe.db.exists("Reminder", {"reference_name": record.name, **OPEN_IMPACT}))

	def test_only_qa_corrects_and_always_says_why(self):
		record = self.calibrated(self.approved())
		with self.assertRaises(frappe.PermissionError):
			self.correct("Calibration Record", record.name, user=self.maintenance)
		for reason in (None, "   "):
			with self.assertRaisesRegex(frappe.ValidationError, "Say what is to be corrected"):
				self.correct("Calibration Record", record.name, reason=reason)
		self.assertEqual(frappe.db.get_value("Calibration Record", record.name, "docstatus"), 1)
		self.assertFalse(
			frappe.db.exists("Electronic Signature", {"reference_name": record.name, "action": "Correct"})
		)

	def test_an_amendment_carries_nothing_the_signed_acts_set(self):
		"""Frappe's Amend copies every field of the cancelled record; whatever is copied or typed
		into the amendment, what the signatures set stays with the record that was signed."""
		asset = self.needing()
		iq = self.done(asset, "IQ")
		iq_amendment = self.correct("Equipment Qualification", iq)
		found = self.calibrated(self.approved(), points=[point(found=100.05)]).name
		calibration_amendment = self.correct("Calibration Record", found)
		signed = {
			"Equipment Qualification": (iq, iq_amendment),
			"Calibration Record": (found, calibration_amendment),
		}
		frappe.set_user(self.qa)
		try:
			for doctype, (original, amendment) in signed.items():
				# As Amend does: every field of the signed record copied onto the draft.
				source = frappe.get_doc(doctype, original).as_dict(no_default_fields=True)
				draft = frappe.get_doc(doctype, amendment)
				for field in (
					"executed_by",
					"executed_at",
					"approved_by",
					"approved_at",
					"completed_by",
					"impact_status",
					"next_due_date",
				):
					if draft.meta.has_field(field) and source.get(field):
						draft.set(field, source.get(field))
				draft.save()
		finally:
			frappe.set_user("Administrator")
		qualification = frappe.get_doc("Equipment Qualification", iq_amendment)
		self.assertEqual((qualification.executed_by, qualification.approved_by), (None, None))
		with self.assertRaisesRegex(frappe.ValidationError, "once executed"):
			self.sign(self.qa, "Equipment Qualification", qualification.name, "Approve")
		self.assertIn(f"its qualification {iq} is being corrected", self.why(asset))
		calibration = frappe.get_doc("Calibration Record", calibration_amendment)
		self.assertEqual((calibration.impact_status, calibration.completed_by), (None, None))
		listed = [due.reference_name for due in reminders.calibration_impacts_open()]
		self.assertIn(found, listed)  # the open assessment stays on the record that found it
		self.assertNotIn(calibration.name, listed)
