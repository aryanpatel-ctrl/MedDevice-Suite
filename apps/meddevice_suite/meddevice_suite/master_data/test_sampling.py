"""Sampling plans (module 6, URS-MST-009): tests derived from the requirement's clauses, the tables
checked against an independent source (test_iso2859_1_reference.json)."""

import json
import os

import frappe
from frappe.tests import UnitTestCase

from meddevice_suite.master_data import sampling
from meddevice_suite.master_data.specification import DOCTYPE, check_sample_size
from meddevice_suite.master_data.test_specification import SpecificationTestCase, new_item, template
from meddevice_suite.qa import verifies
from meddevice_suite.setup.fixtures.inspection import DIMENSION


def reference() -> dict:
	with open(os.path.join(os.path.dirname(__file__), "test_iso2859_1_reference.json")) as f:
		return json.load(f)["plans"]


@verifies("URS-MST-009")
class TestTables(UnitTestCase):
	def test_every_plan_matches_the_independent_calculator(self):
		"""All 16 code letters x 26 AQLs, arrows followed, against SQCOnline's ISO 2859-1 answers."""
		plans = reference()
		self.assertEqual(sum(len(row) for row in plans.values()), 416)
		for letter, row in plans.items():
			for aql, expected in row.items():
				with self.subTest(letter=letter, aql=aql):
					plan = sampling.table_plan(letter, aql)
					self.assertEqual([plan.sample_size, plan.accept, plan.reject], expected)

	def test_code_letters(self):
		"""ISO 2859-1 Table 1 at the edges of its lot-size ranges and its levels."""
		for lot, level, letter in (
			(2, "II", "A"),
			(8, "II", "A"),
			(9, "II", "B"),
			(15, "III", "C"),
			(16, "III", "D"),
			(500, "II", "H"),
			(501, "II", "J"),
			(1200, "II", "J"),
			(1201, "II", "K"),
			(1200, "I", "G"),
			(3200, "III", "L"),
			(35000, "II", "M"),
			(500000, "II", "P"),
			(500001, "III", "R"),
			(10_000_000, "II", "Q"),
			(90, "S-1", "B"),
			(150, "S-4", "D"),
			(3201, "S-3", "F"),
		):
			with self.subTest(lot=lot, level=level):
				self.assertEqual(sampling.code_letter(lot, level), letter)

	def test_an_arrow_takes_the_sample_size_of_the_letter_it_leads_to(self):
		plan = sampling.table_plan("G", "0.65")  # up to F
		self.assertEqual((plan.code_letter, plan.sample_size, plan.accept, plan.reject), ("F", 20, 0, 1))
		plan = sampling.table_plan("A", "10")  # down past B to C
		self.assertEqual((plan.code_letter, plan.sample_size, plan.accept, plan.reject), ("C", 5, 1, 2))
		plan = sampling.table_plan("R", "0.015")  # up past Q to P
		self.assertEqual((plan.code_letter, plan.sample_size), ("P", 800))

	def test_each_plan_rejects_at_one_more_than_it_accepts(self):
		for row in sampling.tables()["normal_single"].values():
			for cell in row:
				if isinstance(cell, list):
					self.assertEqual(cell[1], cell[0] + 1)
				else:
					self.assertIn(cell, (sampling.UP, sampling.DOWN))

	def test_a_lot_the_sample_would_reach_is_inspected_unit_by_unit(self):
		plan = sampling.plan_for(8, "II", "1.0")  # A, down to E: 13 units, more than the lot
		self.assertEqual((plan.sample_size, plan.accept, plan.reject, plan.every_unit), (8, 0, 1, True))
		plan = sampling.plan_for(1200, "II", "1.0")
		self.assertEqual(
			(plan.code_letter, plan.sample_size, plan.accept, plan.reject, plan.every_unit),
			("J", 80, 2, 3, False),
		)

	def test_a_sample_equal_to_the_lot_is_every_unit(self):
		plan = sampling.plan_for(13, "II", "1.0")  # B, down to E: 13 units, the whole lot
		self.assertEqual((plan.sample_size, plan.every_unit), (13, True))

	def test_a_lot_of_one_unit_is_that_unit(self):
		plan = sampling.plan_for(1, "II", "1.0")
		self.assertEqual((plan.sample_size, plan.accept, plan.reject, plan.every_unit), (1, 0, 1, True))

	def test_what_is_not_in_the_standard_is_refused(self):
		for lot, level, aql in (
			(100, "IV", "1.0"),
			(100, "II", "2.0"),
			(0, "II", "1.0"),
			(12.5, "II", "1.0"),
		):
			with self.subTest(lot=lot, level=level, aql=aql):
				with self.assertRaises(frappe.ValidationError):
					sampling.plan_for(lot, level, aql)


@verifies("URS-MST-009")
class TestPlanOnTheInspection(SpecificationTestCase):
	def sample_by(self, method: str, level: str | None = None, aql: str | None = None) -> None:
		"""The item's incoming specification samples by a plan, as QA would have changed it under
		change control (that path is tested below); put back after the test."""
		before = frappe.db.get_value(DOCTYPE, self.template, [sampling.METHOD, sampling.LEVEL, sampling.AQL])
		frappe.db.set_value(
			DOCTYPE, self.template, {sampling.METHOD: method, sampling.LEVEL: level, sampling.AQL: aql}
		)
		self.addCleanup(
			frappe.db.set_value,
			DOCTYPE,
			self.template,
			dict(zip((sampling.METHOD, sampling.LEVEL, sampling.AQL), before, strict=True)),
		)

	def planned(self, qty: float, **values):
		receipt, lot = self.verified_lot_of(qty)
		doc = self.inspection(receipt, lot, sample_size=None, **values)
		doc.insert()
		return doc

	def verified_lot_of(self, qty: float):
		receipt, lot = self.received(qty=qty)
		self.verified(receipt)
		return receipt, lot

	def test_the_plan_follows_from_the_lot(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		doc = self.planned(600)  # 501 to 1200 at level II: J, 80 units, accept 2, reject 3
		self.assertEqual(
			(
				doc.mds_lot_size,
				doc.mds_code_letter,
				doc.sample_size,
				doc.mds_accept_number,
				doc.mds_reject_number,
			),
			(600, "J", 80, 2, 3),
		)
		self.assertFalse(doc.mds_every_unit)

	def test_the_lot_is_counted_by_the_system_not_typed(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		doc = self.inspection(receipt, lot, sample_size=None, mds_lot_size=10, mds_accept_number=99)
		doc.insert()
		self.assertEqual((doc.mds_lot_size, doc.mds_accept_number, doc.sample_size), (600, 2, 80))

	def test_the_lot_is_accepted_up_to_the_accept_number_and_rejected_at_the_reject_number(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		for found, status in ((0, "Accepted"), (2, "Accepted"), (3, "Rejected")):
			with self.subTest(found=found):
				doc = self.planned(600, mds_nonconforming=found)
				self.assertEqual(doc.status, status)

	def test_a_reading_out_of_its_limits_is_a_nonconforming_unit(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		doc = self.inspection(receipt, lot, sample_size=None, mds_nonconforming=0)
		dimension = next(r for r in doc.readings if r.specification == DIMENSION)
		dimension.reading_1, dimension.reading_2 = "9", "9"  # two units above the limit of 2
		with self.assertRaisesRegex(frappe.ValidationError, "show at least 2 nonconforming"):
			doc.insert()
		doc.mds_nonconforming = 2
		doc.insert()
		self.assertEqual(doc.status, "Accepted")  # within the accept number; the units stay nonconforming

	def test_the_count_is_within_the_sample(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		with self.assertRaisesRegex(frappe.ValidationError, "between 0 and the 80 units sampled"):
			self.planned(600, mds_nonconforming=81)

	def test_the_sample_is_at_least_the_plans(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		doc = self.planned(600)
		doc.sample_size = 10  # as the record would be, past the plan
		with self.assertRaisesRegex(frappe.ValidationError, "sampled with 80 units"):
			check_sample_size(doc)

	def test_a_small_lot_is_inspected_unit_by_unit(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		doc = self.planned(6, mds_nonconforming=1)
		self.assertEqual(
			(doc.sample_size, doc.mds_accept_number, doc.mds_every_unit, doc.status), (6, 0, 1, "Rejected")
		)

	def test_every_unit_of_the_lot(self):
		self.sample_by(sampling.EVERY_UNIT)
		doc = self.planned(40, mds_nonconforming=0)
		self.assertEqual(
			(doc.sample_size, doc.mds_accept_number, doc.mds_reject_number, doc.mds_every_unit, doc.status),
			(40, 0, 1, 1, "Accepted"),
		)

	def test_an_in_process_lot_is_the_job_cards_quantity(self):
		from unittest.mock import patch

		doc = frappe._dict(
			reference_type="Job Card", reference_name="JC-0001", batch_no="LOT-1", item_code="X"
		)
		card = frappe._dict(production_item="X", for_quantity=250)
		with patch.object(frappe.db, "get_value", return_value=card):
			self.assertEqual(sampling.lot_size_of(doc), 250)
		with patch.object(
			frappe.db, "get_value", return_value=frappe._dict(production_item="Y", for_quantity=250)
		):
			with self.assertRaisesRegex(frappe.ValidationError, "does not make X"):
				sampling.lot_size_of(doc)

	def test_a_delivered_lot_is_the_quantity_received_not_what_is_left(self):
		from unittest.mock import patch

		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		doc = self.inspection(receipt, lot, sample_size=None)
		with patch("erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=10):  # most moved on
			doc.insert()
		self.assertEqual((doc.mds_lot_size, doc.sample_size), (600, 80))

	def test_a_lot_the_system_cannot_count_is_not_sampled_by_its_size(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, sample_size=None)
		doc.quality_inspection_template = self.template  # as choose_specification sets it first
		doc.batch_no = None  # no lot named
		with self.assertRaisesRegex(frappe.ValidationError, "samples by the size of the lot"):
			sampling.apply_plan(doc)

	def test_a_fixed_sample_size_is_decided_by_the_readings_as_before(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot)
		doc.insert()
		self.assertEqual((doc.mds_lot_size, doc.mds_reject_number, doc.sample_size), (0, 0, 5))
		frappe.get_doc("Quality Inspection", doc.name).submit()  # reloaded, as the desk submits it
		self.assertEqual(frappe.db.get_value("Quality Inspection", doc.name, "docstatus"), 1)

	def test_the_inspection_is_submitted_under_its_plan(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		doc = self.planned(600, mds_nonconforming=1)
		doc = frappe.get_doc("Quality Inspection", doc.name)  # reloaded, as the desk submits it
		with self.assertRaisesRegex(frappe.ValidationError, "say in the remarks how they were"):
			doc.submit()
		doc = frappe.get_doc("Quality Inspection", doc.name)
		doc.remarks = "One unit with a burr, tagged and moved to the rejected store"
		doc.submit()
		self.assertEqual((doc.docstatus, doc.status, doc.sample_size), (1, "Accepted", 80))

	def test_the_plan_sent_with_the_record_is_worked_out_again_at_submit(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		doc = frappe.get_doc("Quality Inspection", self.planned(600, mds_nonconforming=3).name)
		doc.update({"mds_lot_size": 2, "mds_reject_number": 99, "mds_accept_number": 98, "sample_size": 2})
		doc.remarks = "Three units out"
		doc.submit()
		self.assertEqual(
			(doc.mds_lot_size, doc.mds_accept_number, doc.mds_reject_number, doc.sample_size, doc.status),
			(600, 2, 3, 80, "Rejected"),
		)

	def test_an_inspection_inserted_as_submitted_is_planned_too(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		doc = self.inspection(receipt, lot, sample_size=1, mds_nonconforming=0, docstatus=1)
		doc.insert()
		self.assertEqual((doc.docstatus, doc.sample_size, doc.mds_reject_number), (1, 80, 3))

	def test_a_lot_from_a_conditional_supplier_is_inspected_unit_by_unit(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		frappe.db.set_value("Purchase Receipt Item", receipt.items[0].name, "mds_full_inspection", 1)
		doc = self.inspection(receipt, lot, sample_size=None, mds_nonconforming=1)
		doc.insert()
		self.assertEqual(
			(doc.sample_size, doc.mds_accept_number, doc.mds_every_unit, doc.status), (600, 0, 1, "Rejected")
		)

	def test_an_inspectors_rejection_stands(self):
		self.sample_by(sampling.ISO_2859_1, "II", "1.0")
		receipt, lot = self.verified_lot_of(600)
		doc = self.inspection(receipt, lot, sample_size=None, mds_nonconforming=0, manual_inspection=1)
		doc.status = "Rejected"
		doc.insert()
		self.assertEqual(doc.status, "Rejected")


@verifies("URS-MST-009")
class TestPlanOnTheSpecification(SpecificationTestCase):
	def test_iso_2859_1_needs_an_inspection_level_and_an_aql(self):
		frappe.set_user("Administrator")
		item = new_item()
		frappe.set_user(self.qa)
		doc = template(f"MDS SPEC AQL {item}", item, sample=0)
		doc.set(sampling.METHOD, sampling.ISO_2859_1)
		with self.assertRaisesRegex(frappe.ValidationError, "the inspection level, the AQL"):
			doc.insert()
		doc.set(sampling.LEVEL, "II")
		doc.set(sampling.AQL, "0.65")
		doc.insert()  # no fixed sample size needed

	def test_every_unit_needs_no_sample_size(self):
		frappe.set_user("Administrator")
		item = new_item()
		frappe.set_user(self.qa)
		doc = template(f"MDS SPEC EVERY {item}", item, sample=0)
		doc.set(sampling.METHOD, sampling.EVERY_UNIT)
		doc.insert()

	def test_the_plan_of_a_specification_in_use_changes_only_under_change_control(self):
		receipt, lot = self.verified_lot()
		self.inspected(receipt, lot)  # an inspection made against it: in use
		frappe.set_user(self.qa)
		doc = frappe.get_doc(DOCTYPE, self.template)
		doc.set(sampling.METHOD, sampling.ISO_2859_1)
		doc.set(sampling.LEVEL, "II")
		doc.set(sampling.AQL, "1.0")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()
