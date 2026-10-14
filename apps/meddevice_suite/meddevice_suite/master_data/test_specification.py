import re

import frappe

from meddevice_suite.compliance.esign import SigningRefused, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action, signing_actions
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.lot_control.test_lot_status import lot_item
from meddevice_suite.lot_control.test_release import ITEM, ReleaseTestCase
from meddevice_suite.master_data import master_approval
from meddevice_suite.master_data.master_approval import (
	APPROVAL_STATUS,
	APPROVED,
	APPROVED_AT,
	APPROVED_BY,
	DRAFT,
)
from meddevice_suite.master_data.specification import (
	DOCTYPE,
	FINAL,
	IN_PROCESS,
	INCOMING,
	TEST_METHOD,
	check_against_specification,
	choose_specification,
	specification_for,
	specification_for_inspection,
)
from meddevice_suite.master_data.test_routing import document
from meddevice_suite.pilot.inspection import APPEARANCE, DIMENSION, readings_for
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change, signer

REFUSED = (frappe.ValidationError, frappe.PermissionError)

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

	def inspected_against(self, name: str) -> None:
		"""As if inspected against it: a submitted inspection names it. The inspection is made against
		the approved specification of the lot's item and pointed at `name` afterwards."""
		receipt, lot = self.verified_lot()
		inspection = self.inspection(receipt, lot)
		inspection.insert()
		inspection.submit()
		frappe.db.set_value(
			"Quality Inspection", inspection.name, "quality_inspection_template", name, update_modified=False
		)
		frappe.set_user(self.qa)

	def draft_specification(self, stage: str = FINAL, test_method: str | None = "SOP", **values) -> str:
		"""A new item's specification for the stage, made by QA, naming an effective test method of the
		type `test_method` (none when None)."""
		frappe.set_user("Administrator")
		item = new_item()
		method = document(test_method) if test_method else None
		frappe.set_user(self.qa)
		doc = template(f"MDS SPEC APPROVAL {item}", item, stage=stage)
		doc.update({TEST_METHOD: method, **values})
		return doc.insert().name

	def approve(
		self, name: str, user: str | None = None, reason: str = "Parameters, limits and test method checked"
	) -> None:
		"""QA's signed approval, as the form's Approve button sends it; back to QA afterwards."""
		frappe.set_user(user or self.qa)
		try:
			sign_action(DOCTYPE, name, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user(self.qa)

	def widened(self, name: str, **values):
		spec = frappe.get_doc(DOCTYPE, name)
		next(r for r in spec.item_quality_inspection_parameter if r.specification == DIMENSION).max_value = 3
		spec.update(values)
		return spec


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

	def test_the_sample_size_comes_from_the_specification(self):
		receipt, lot = self.verified_lot()
		doc = self.inspection(receipt, lot, readings=[], quality_inspection_template=None, sample_size=None)
		choose_specification(doc)
		self.assertEqual(doc.sample_size, 5)
		doc = self.inspection(receipt, lot, readings=[], quality_inspection_template=None, sample_size=8)
		choose_specification(doc)
		self.assertEqual(doc.sample_size, 8)  # the inspector may sample more

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
		self.inspected_against(name)
		return name

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
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "mds_version"), 2)  # URS-MST-001

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

	def test_a_parameter_read_by_a_specification_in_use_keeps_its_name(self):
		self.in_use_specification()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.rename_doc("Quality Inspection Parameter", DIMENSION, f"{DIMENSION} renamed")

	def test_removing_a_parameter_of_a_specification_in_use_needs_a_request(self):
		name = self.in_use_specification()
		spec = frappe.get_doc(DOCTYPE, name)
		spec.item_quality_inspection_parameter = spec.item_quality_inspection_parameter[:1]
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			spec.save()

	def test_a_request_not_yet_approved_does_not_allow_a_change(self):
		from meddevice_suite.master_data.test_versioning import draft_change

		name = self.in_use_specification()
		draft = draft_change(self.requester, DOCTYPE, name)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved for implementation"):
			self.widened(name, mds_change_request=draft).save()

	def test_the_api_changes_a_specification_in_use_under_the_same_rule(self):
		name = self.in_use_specification()
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			frappe.client.set_value(DOCTYPE, name, "mds_sample_size", 10)

	def test_a_specification_in_use_is_never_deleted(self):
		name = self.in_use_specification()
		frappe.set_user("Administrator")
		with self.assertRaises((frappe.PermissionError, frappe.LinkExistsError)):
			frappe.delete_doc(DOCTYPE, name)
		self.assertTrue(frappe.db.exists(DOCTYPE, name))


# --- QA's signed approval (URS-MST-014, plan D-33) ------------------------------------------------


@verifies("URS-MST-014")
class TestApproval(SpecificationTestCase):
	def test_a_new_specification_is_a_draft_that_qa_approves_with_a_signature(self):
		name = self.draft_specification()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), DRAFT)
		self.approve(name, reason="Limits and test method checked against the drawing")
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, [APPROVAL_STATUS, APPROVED_BY]), (APPROVED, self.qa)
		)
		self.assertTrue(frappe.db.get_value(DOCTYPE, name, APPROVED_AT))
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
		)
		self.assertEqual(
			(signature.meaning, signature.action, signature.signer, signature.reason),
			("Approved", "Approve", self.qa, "Limits and test method checked against the drawing"),
		)
		self.assertTrue(verify_signatures(DOCTYPE, name))
		self.assertTrue(is_unchanged_since(signature))

	def test_only_qa_approves_with_password_and_reason_and_once(self):
		name = self.draft_specification()
		for user in (self.qc, self.requester):
			with self.subTest(user=user), self.assertRaises(REFUSED):
				self.approve(name, user=user)
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action(DOCTYPE, name, "Approve", "not the password", "Checked")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action(DOCTYPE, name, "Approve", PASSWORD, " ")
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), DRAFT)
		self.approve(name)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			self.approve(name)

	def test_the_approval_is_given_only_by_the_signed_act(self):
		name = self.draft_specification()
		doc = frappe.get_doc(DOCTYPE, name)
		doc.set(APPROVAL_STATUS, APPROVED)
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			doc.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value(DOCTYPE, name, APPROVED_BY, self.qa)
		made = self.draft_specification(**{APPROVAL_STATUS: APPROVED, APPROVED_BY: self.qa})
		self.assertEqual(frappe.db.get_value(DOCTYPE, made, [APPROVAL_STATUS, APPROVED_BY]), (DRAFT, None))
		self.approve(made)
		frappe.set_user("Administrator")
		other = new_item()
		frappe.set_user(self.qa)
		copy = frappe.copy_doc(frappe.get_doc(DOCTYPE, made))  # as the form's Duplicate
		copy.update({"quality_inspection_template_name": f"MDS SPEC COPY {other}", "mds_item": other})
		copy.insert()
		self.assertEqual(copy.get(APPROVAL_STATUS), DRAFT)

	def test_an_inspection_is_made_only_against_an_approved_specification(self):
		name = self.draft_specification(stage=FINAL)
		item = frappe.db.get_value(DOCTYPE, name, "mds_item")
		outgoing = frappe.get_doc(
			{"doctype": "Quality Inspection", "inspection_type": "Outgoing", "item_code": item}
		)
		with self.assertRaisesRegex(
			frappe.ValidationError, f"Specification {re.escape(name)} of {item} is in draft"
		):
			choose_specification(outgoing)
		self.approve(name)
		choose_specification(outgoing)
		self.assertEqual(outgoing.quality_inspection_template, name)
		# The lot's own specification in draft again, as a change leaves it: the inspection is refused.
		frappe.db.set_value(DOCTYPE, self.template, APPROVAL_STATUS, DRAFT)
		self.addCleanup(frappe.db.set_value, DOCTYPE, self.template, APPROVAL_STATUS, APPROVED)
		receipt, lot = self.verified_lot()
		with self.assertRaisesRegex(
			frappe.ValidationError, f"Specification {re.escape(self.template)} .* is in draft"
		):
			self.inspection(receipt, lot).insert()

	def test_a_change_returns_an_approved_specification_to_draft(self):
		name = self.draft_specification()
		self.approve(name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": DOCTYPE, "reference_name": name}
		)
		frappe.get_doc(DOCTYPE, name).save()  # saved unchanged: still approved
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), APPROVED)
		for label, change in (
			("a limit", lambda spec: self.widened(name)),
			("the test method", lambda spec: spec.set(TEST_METHOD, document("Work Instruction")) or spec),
			("the sample size", lambda spec: spec.set("mds_sample_size", 8) or spec),
		):
			with self.subTest(changed=label):
				change(frappe.get_doc(DOCTYPE, name)).save()
				self.assertEqual(
					frappe.db.get_value(DOCTYPE, name, [APPROVAL_STATUS, APPROVED_BY, APPROVED_AT]),
					(DRAFT, None, None),
				)
				self.assertFalse(is_unchanged_since(signature))  # what QA signed is no longer what it says
				self.approve(name)

	def test_a_change_of_a_specification_in_use_needs_its_request_and_returns_it_to_draft(self):
		name = self.draft_specification(stage=IN_PROCESS)
		self.approve(name)
		self.inspected_against(name)
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			self.widened(name).save()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), APPROVED)
		change = approved_change(self.requester, self.qa, name, DOCTYPE, "Product or BOM")
		self.widened(name, mds_change_request=change).save()
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, name, [APPROVAL_STATUS, "mds_changed_under", "mds_version"]),
			(DRAFT, change, 2),
		)
		self.approve(name)  # what it says now, approved anew
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), APPROVED)

	def test_naming_the_test_method_for_the_first_time_needs_no_request(self):
		"""A specification approved by migration (in use before the rule, plan D-33) names no test
		method; QA names it once without a change request, and approves it again with it."""
		name = self.draft_specification(stage=IN_PROCESS, test_method=None)
		self.inspected_against(name)
		frappe.db.set_value(DOCTYPE, name, APPROVAL_STATUS, APPROVED)  # as the migration leaves it
		spec = frappe.get_doc(DOCTYPE, name)
		spec.set(TEST_METHOD, document("SOP"))
		spec.save()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, [APPROVAL_STATUS, "mds_version"]), (DRAFT, 1))
		self.approve(name)
		spec = frappe.get_doc(DOCTYPE, name)
		spec.set(TEST_METHOD, document("SOP"))  # another one: a change of what QA approved
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			spec.save()

	def test_the_form_shows_the_approval_or_what_stands_in_its_way(self):
		name = self.draft_specification(test_method=None)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.run_method("onload")
		shown = doc.get_onload().master_approval
		self.assertFalse(shown["approved"])
		self.assertIn("its test method", shown["missing"])
		frappe.db.set_value(DOCTYPE, name, TEST_METHOD, document("SOP"))
		self.approve(name)
		doc = frappe.get_doc(DOCTYPE, name)
		doc.run_method("onload")
		self.assertEqual(
			(doc.get_onload().master_approval["approved"], doc.get_onload().master_approval["by_migration"]),
			(True, False),
		)
		frappe.db.set_value(DOCTYPE, name, APPROVED_BY, None)  # as the migration leaves it
		doc = frappe.get_doc(DOCTYPE, name)
		doc.run_method("onload")
		self.assertTrue(doc.get_onload().master_approval["by_migration"])

	def test_qa_is_offered_the_approval_and_a_draft_waits_on_qa(self):
		from meddevice_suite.work_management.my_work import waiting_on

		self.assertEqual(signing_actions(DOCTYPE), {"Approve": "Approved"})
		name = self.draft_specification()
		self.assertIn((DOCTYPE, name), {(row.doctype, row.name) for row in waiting_on(self.qa)})
		self.assertNotIn((DOCTYPE, name), {(row.doctype, row.name) for row in waiting_on(self.qc)})
		self.approve(name)
		self.assertNotIn((DOCTYPE, name), {(row.doctype, row.name) for row in waiting_on(self.qa)})


@verifies("URS-MST-014")
class TestApprovalByMigration(SpecificationTestCase):
	def test_a_specification_already_inspected_against_is_approved_by_migration_and_no_other(self):
		from meddevice_suite.patches.v0_1 import approve_specifications_in_use as patch

		in_use = self.draft_specification(stage=IN_PROCESS, test_method=None)
		self.inspected_against(in_use)
		unused = self.draft_specification(stage=FINAL)
		frappe.set_user("Administrator")
		loose = template(f"MDS SPEC LOOSE MIGRATION {frappe.generate_hash(length=6)}", None).insert().name
		self.inspected_against(loose)  # a template that is no specification is never inspected against now
		approved_by_qa = frappe.db.get_value(DOCTYPE, self.template, [APPROVAL_STATUS, APPROVED_BY])

		frappe.set_user("Administrator")
		patch.execute()

		self.assertEqual(
			frappe.db.get_value(DOCTYPE, in_use, [APPROVAL_STATUS, APPROVED_BY]), (APPROVED, None)
		)
		self.assertTrue(frappe.db.get_value(DOCTYPE, in_use, APPROVED_AT))
		self.assertTrue(
			frappe.db.exists(
				"Audit Event",
				{"reference_doctype": DOCTYPE, "reference_name": in_use, "reason": patch.REASON},
			)
		)
		for name in (unused, loose):
			with self.subTest(name=name):
				self.assertEqual(frappe.db.get_value(DOCTYPE, name, APPROVAL_STATUS), DRAFT)
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, self.template, [APPROVAL_STATUS, APPROVED_BY]), approved_by_qa
		)

		# Changed since, it is in draft again; the migration run again leaves it there.
		frappe.db.set_value(DOCTYPE, in_use, APPROVAL_STATUS, DRAFT)
		patch.execute()
		self.assertEqual(frappe.db.get_value(DOCTYPE, in_use, APPROVAL_STATUS), DRAFT)


# --- The test method (URS-MST-015, plan D-34) ------------------------------------------------------


@verifies("URS-MST-015")
class TestTestMethod(SpecificationTestCase):
	def test_a_specification_is_approved_only_with_its_test_method_in_effect(self):
		for lacks, method in (
			("its test method", None),
			("a test method as its document", "Form"),
			("its test method in effect", "SOP:Draft"),
			("its test method in effect", "SOP:Obsolete"),
		):
			with self.subTest(lacks=lacks, method=method):
				name = self.draft_specification(test_method=None)
				if method:
					document_type, _sep, status = method.partition(":")
					frappe.db.set_value(
						DOCTYPE, name, TEST_METHOD, document(document_type, status or "Effective")
					)
				with self.assertRaisesRegex(
					frappe.ValidationError, f"cannot be approved yet; it lacks .*{lacks}"
				):
					self.approve(name)
				self.assertFalse(master_approval.approved(DOCTYPE, name))
		for document_type in ("SOP", "Work Instruction", "External Document"):
			with self.subTest(accepted=document_type):
				name = self.draft_specification(test_method=document_type)
				self.approve(name)
				self.assertTrue(master_approval.approved(DOCTYPE, name))

	def test_no_inspection_is_made_against_a_specification_whose_test_method_is_withdrawn(self):
		before = frappe.db.get_value(DOCTYPE, self.template, TEST_METHOD)
		method = document("SOP")
		frappe.db.set_value(DOCTYPE, self.template, TEST_METHOD, method)
		self.addCleanup(frappe.db.set_value, DOCTYPE, self.template, TEST_METHOD, before)
		frappe.db.set_value(
			"Controlled Document", method, "status", "Obsolete"
		)  # as its withdrawal leaves it
		receipt, lot = self.verified_lot()
		with self.assertRaisesRegex(
			frappe.ValidationError, f"test method {method} .* is no longer in effect"
		):
			self.inspection(receipt, lot).insert()
