import frappe

from meddevice_suite.lot_control.test_lot_status import lot_item
from meddevice_suite.lot_control.test_release import ITEM, ReleaseTestCase
from meddevice_suite.master_data.specification import (
	DOCTYPE,
	FINAL,
	INCOMING,
	check_against_specification,
	choose_specification,
	specification_for,
	specification_for_inspection,
)
from meddevice_suite.pilot.inspection import APPEARANCE, DIMENSION, readings_for
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change, signer

OTHER_ITEM = "MDS-SPEC-OTHER"


def parameters(method: str = "Caliper", low: float = 1.0, high: float = 2.0) -> list[dict]:
	return [
		{"specification": APPEARANCE, "mds_method": "Visual", "numeric": 0, "value": "Conforms"},
		{"specification": DIMENSION, "mds_method": method, "numeric": 1, "min_value": low, "max_value": high},
	]


def new_item() -> str:
	"""An item of its own, so that each test registers its specifications afresh."""
	code = f"MDS-SPEC-{frappe.generate_hash(length=6).upper()}"
	lot_item(code)
	return code


def template(name: str, item: str | None, stage: str | None = INCOMING, sample: int = 5, rows=None):
	values = {"mds_item": item, "mds_stage": stage, "mds_sample_size": sample} if item else {}
	return frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"quality_inspection_template_name": name,
			"item_quality_inspection_parameter": rows if rows is not None else parameters(),
			**values,
		}
	)


class SpecificationTestCase(ReleaseTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		lot_item(OTHER_ITEM)
		cls.requester = signer("Production Supervisor")

	def setUp(self):
		super().setUp()
		frappe.set_user(self.qa)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def inspection(self, receipt, lot, **values):
		frappe.set_user(self.qc)
		doc = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": receipt.name,
				"item_code": ITEM,
				"batch_no": lot,
				"sample_size": 5,
				"inspected_by": self.qc,
				"readings": readings_for(self.template, self.instrument),
				**values,
			}
		)
		return doc

	def verified_lot(self):
		receipt, lot = self.received()
		self.verified(receipt)
		return receipt, lot


@verifies("URS-MST-002")
class TestSpecification(SpecificationTestCase):
	def test_a_specification_states_its_item_stage_sample_size_methods_and_limits(self):
		for label, doc in (
			("no method", template("MDS SPEC A", OTHER_ITEM, rows=parameters(method=""))),
			("no limits", template("MDS SPEC B", OTHER_ITEM, rows=parameters(low=0, high=0))),
			(
				"no accepted value",
				template(
					"MDS SPEC C",
					OTHER_ITEM,
					rows=[{"specification": APPEARANCE, "mds_method": "Visual", "numeric": 0, "value": ""}],
				),
			),
			("no sample size", template("MDS SPEC D", OTHER_ITEM, sample=0)),
			("no stage", template("MDS SPEC E", OTHER_ITEM, stage=None)),
			("no parameters", template("MDS SPEC F", OTHER_ITEM, rows=[])),
		):
			with self.subTest(label), self.assertRaisesRegex(frappe.ValidationError, "does not yet state"):
				doc.insert()
		spec = template("MDS SPEC OTHER FINAL", OTHER_ITEM, stage=FINAL).insert()
		self.assertEqual(specification_for(OTHER_ITEM, FINAL), spec.name)

	def test_one_specification_per_item_and_stage(self):
		with self.assertRaisesRegex(frappe.ValidationError, "already holds the incoming specification"):
			template("MDS SPEC SECOND IQC", ITEM).insert()
		self.assertEqual(specification_for(ITEM, INCOMING), self.template)

	def test_the_inspection_form_finds_the_specification_for_its_stage(self):
		frappe.set_user(self.qc)
		self.assertEqual(specification_for_inspection(ITEM, "Incoming"), self.template)
		self.assertIsNone(specification_for_inspection(ITEM, "In Process"))


@verifies("URS-MST-002")
class TestInspectionAgainstSpecification(SpecificationTestCase):
	def test_an_inspection_is_made_against_the_items_specification_for_its_stage(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, readings=[], quality_inspection_template=None)
		choose_specification(doc)
		self.assertEqual(doc.quality_inspection_template, self.template)
		doc.get_item_specification_details()  # as ERPNext lays out the readings in its validate
		self.assertEqual(sorted(r.specification for r in doc.readings), sorted([APPEARANCE, DIMENSION]))

	def test_readings_taken_against_another_template_are_refused(self):
		frappe.set_user("Administrator")
		other = template("MDS SPEC LOOSE", None).insert().name
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, quality_inspection_template=other)
		with self.assertRaisesRegex(frappe.ValidationError, "against its incoming specification"):
			doc.insert()

	def test_an_item_without_a_specification_for_the_stage_is_not_inspected(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, inspection_type="In Process", readings=[])
		with self.assertRaisesRegex(frappe.ValidationError, "no in process specification"):
			doc.insert()

	def test_the_limits_come_from_the_specification(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot)
		next(r for r in doc.readings if r.specification == DIMENSION).max_value = 99
		with self.assertRaisesRegex(frappe.ValidationError, "not changed on an inspection"):
			doc.insert()

	def test_each_parameter_is_read(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot)
		doc.readings = [r for r in doc.readings if r.specification != APPEARANCE]
		with self.assertRaisesRegex(frappe.ValidationError, "reads each of its parameters once"):
			doc.insert()

	def test_a_job_cards_inspection_is_judged_on_the_in_process_specification(self):
		"""ERPNext copies the item master's template limits into a job card's readings; the
		specification's limits replace them and the readings are judged against those."""
		frappe.set_user("Administrator")
		item = new_item()
		frappe.set_user(self.qa)
		spec = template(
			f"MDS SPEC IPQC {item}", item, stage="In Process", rows=parameters(low=1, high=2)
		).insert()
		frappe.set_user(self.qc)
		doc = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "In Process",
				"reference_type": "Job Card",
				"reference_name": "JC-TEST",
				"item_code": item,
				"quality_inspection_template": spec.name,
				"sample_size": 5,
				"readings": [
					{
						"specification": APPEARANCE,
						"numeric": 0,
						"value": "Conforms",
						"reading_value": "Conforms",
					},
					# As ERPNext leaves it: the limits of another template, read inside them.
					{
						"specification": DIMENSION,
						"numeric": 1,
						"min_value": 9,
						"max_value": 10,
						"reading_1": "9.5",
					},
				],
			}
		)
		check_against_specification(doc)
		dimension = next(r for r in doc.readings if r.specification == DIMENSION)
		self.assertEqual((dimension.min_value, dimension.max_value, dimension.status), (1, 2, "Rejected"))

	def test_the_sample_is_at_least_the_specifications(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, sample_size=2)
		doc.insert()
		with self.assertRaisesRegex(frappe.ValidationError, "samples at least 5"):
			doc.submit()


@verifies("URS-MST-002")
class TestChangeControl(SpecificationTestCase):
	def in_use_specification(self) -> str:
		frappe.set_user("Administrator")
		item = new_item()
		name = template(f"MDS SPEC IN USE {frappe.generate_hash(length=6)}", None).insert().name
		frappe.set_user(self.qa)
		spec = frappe.get_doc(DOCTYPE, name)
		spec.update({"mds_item": item, "mds_stage": "In Process", "mds_sample_size": 3})
		spec.save()
		# As if inspected against: a submitted inspection names it.
		receipt, lot = self.verified_lot()
		inspection = self.inspection(receipt, lot)
		inspection.insert()
		inspection.submit()
		frappe.db.set_value(
			"Quality Inspection", inspection.name, "quality_inspection_template", name, update_modified=False
		)
		frappe.set_user(self.qa)
		return name

	def widened(self, name: str, **values):
		spec = frappe.get_doc(DOCTYPE, name)
		next(r for r in spec.item_quality_inspection_parameter if r.specification == DIMENSION).max_value = 3
		spec.update(values)
		return spec

	def test_a_specification_not_yet_used_changes_freely(self):
		frappe.set_user("Administrator")
		item = new_item()
		frappe.set_user(self.qa)
		name = template(f"MDS SPEC UNUSED {item}", item, stage="In Process").insert().name
		self.widened(name).save()
		self.assertEqual(
			frappe.db.get_value(
				"Item Quality Inspection Parameter", {"parent": name, "specification": DIMENSION}, "max_value"
			),
			3,
		)

	def test_a_specification_in_use_changes_only_under_an_approved_change_request_listing_it(self):
		name = self.in_use_specification()
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			self.widened(name).save()
		elsewhere = approved_change(self.requester, self.qa, self.template, DOCTYPE, "Product or BOM")
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			self.widened(name, mds_change_request=elsewhere).save()
		change = approved_change(self.requester, self.qa, name, DOCTYPE, "Product or BOM")
		self.widened(name, mds_change_request=change).save()
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, ["mds_changed_under", "mds_change_request"]), (change, None)
		)

	def test_each_change_names_its_change_request_again(self):
		name = self.in_use_specification()
		change = approved_change(self.requester, self.qa, name, DOCTYPE, "Product or BOM")
		self.widened(name, mds_change_request=change).save()
		# The next change, by anyone, does not ride on the request named for the last one.
		spec = frappe.get_doc(DOCTYPE, name)
		next(r for r in spec.item_quality_inspection_parameter if r.specification == DIMENSION).max_value = 4
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			spec.save()

	def test_the_request_a_change_was_made_under_is_recorded_only_by_the_change(self):
		name = self.in_use_specification()
		elsewhere = approved_change(self.requester, self.qa, self.template, DOCTYPE, "Product or BOM")
		spec = frappe.get_doc(DOCTYPE, name)
		spec.mds_changed_under = elsewhere
		with self.assertRaisesRegex(frappe.ValidationError, "records the change request"):
			spec.save()

	def test_a_template_made_before_the_rule_is_completed_once_without_a_change_request(self):
		frappe.set_user("Administrator")
		item = new_item()
		name = template(f"MDS SPEC LEGACY {frappe.generate_hash(length=6)}", None).insert().name
		receipt, lot = self.verified_lot()
		inspection = self.inspection(receipt, lot)
		inspection.insert()
		inspection.submit()
		frappe.db.set_value(
			"Quality Inspection", inspection.name, "quality_inspection_template", name, update_modified=False
		)
		frappe.set_user(self.qa)
		legacy = frappe.get_doc(DOCTYPE, name)
		legacy.update({"mds_item": item, "mds_stage": FINAL, "mds_sample_size": 4})
		legacy.save()  # its first issue
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			self.widened(name).save()

	def test_a_specification_in_use_is_never_deleted(self):
		name = self.in_use_specification()
		frappe.set_user("Administrator")
		with self.assertRaises((frappe.PermissionError, frappe.LinkExistsError)):
			frappe.delete_doc(DOCTYPE, name)
		self.assertTrue(frappe.db.exists(DOCTYPE, name))
