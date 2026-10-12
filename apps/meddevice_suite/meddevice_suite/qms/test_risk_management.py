"""Risk management (module 39, URS-QMS-017 to URS-QMS-025): tests derived from the requirements'
clauses, on real controlled documents issued through document control and real items."""

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, add_months, getdate, today
from frappe.utils.password import update_password

from meddevice_suite.compliance.esign import MEANINGS, SYSTEM_MAINTAINED, verify_signatures
from meddevice_suite.compliance.retention import retention_years
from meddevice_suite.compliance.signing import SIGNING_ACTIONS, sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.master_data.item_master import FINISHED_GOOD, RAW_MATERIAL
from meddevice_suite.master_data.test_item_master import ItemMasterTestCase, new_item
from meddevice_suite.qa import verifies
from meddevice_suite.qms import pinned_records
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.qms.test_change_control import approved_change, dual_role_user, signer
from meddevice_suite.qms.test_document_control import DocumentControlTestCase
from meddevice_suite.work_management import reminders

REFUSED = (frappe.ValidationError, frappe.PermissionError)  # Frappe or this module, whichever checks first


def acceptability(severity: int, probability: int) -> str:
	"""The tests' matrix: severity counts double, so it is not symmetric and a swapped axis shows."""
	weight = 2 * severity + probability
	if weight >= 12:
		return rm.UNACCEPTABLE
	return rm.ALARP if weight >= 8 else rm.ACCEPTABLE


STANDARD = {(s, p): acceptability(s, p) for s in range(1, 6) for p in range(1, 6)}


def second_qa() -> str:
	"""A second QA person: QA authors an assessment and another QA person approves it."""
	email = "second.qa@risk-management.example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Second QA",
				"send_welcome_email": 0,
				"roles": [{"role": "Quality Manager"}],
			}
		).insert(ignore_permissions=True)
	update_password(email, PASSWORD)
	reset_lockout(email)
	return email


def risk_file_for(qa: str, plan: str, items: list[str] | None = None) -> str:
	"""A risk management file, made by QA; the plan need not be effective until a revision is signed."""
	frappe.set_user(qa)
	try:
		return (
			frappe.get_doc(
				{
					"doctype": rm.FILE,
					"family_name": f"Foley catheter {frappe.generate_hash(length=8)}",
					"intended_use": "Drainage of the urinary bladder through the urethra.",
					"foreseeable_misuse": "Inflating the balloon in the urethra; reuse of a single-use device.",
					"risk_management_plan": plan,
					"items": [{"item": item} for item in items or []],
				}
			)
			.insert()
			.name
		)
	finally:
		frappe.set_user("Administrator")


class RiskTestCase(ItemMasterTestCase, DocumentControlTestCase):
	"""Users: QA authors (`qa`), the Production Supervisor reviews (`supervisor`), a second QA person
	approves (`qa2`), Management approves the policy (`manager`); Sales reads nothing (`outsider`)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.supervisor = signer("Production Supervisor")
		cls.manager = signer("Management")
		cls.qa2 = second_qa()
		cls.plan_document = None

	def setUp(self):
		super().setUp()
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.flags.audit_cause = None
		self.ensure_policy()
		if not type(self).plan_document:
			type(self).plan_document = self.effective("SOP")

	# --- Records ------------------------------------------------------------------------------

	def effective(self, document_type: str = "Specification") -> str:
		"""A controlled document of the type, issued through document control and in effect."""
		document = self.register(f"Risk test {frappe.generate_hash(length=6)}", document_type, "QA")
		self.issue(document)
		frappe.set_user("Administrator")
		return document.name

	def registered(self, document_type: str = "Specification") -> str:
		"""A controlled document of the type that has never taken effect."""
		document = self.register(f"Risk test draft {frappe.generate_hash(length=6)}", document_type, "QA")
		frappe.set_user("Administrator")
		return document.name

	def finished_good(self) -> str:
		frappe.set_user("Administrator")
		return new_item(FINISHED_GOOD).name

	def risk_file(self, items: list[str] | None = None) -> str:
		return risk_file_for(
			self.qa, self.plan_document, items if items is not None else [self.finished_good()]
		)

	def policy(self, cells: dict[tuple[int, int], str] | None = None, title: str = "Risk policy") -> str:
		"""A policy drafted by QA with the matrix given (the tests' standard one unless told)."""
		frappe.set_user(self.qa)
		try:
			doc = frappe.get_doc(
				{
					"doctype": rm.POLICY,
					"title": f"{title} {frappe.generate_hash(length=6)}",
					"policy_statement": "ISO 14971 Annex C, the state of the art for urinary catheters, and the complaint history.",
				}
			).insert()
			for cell in doc.matrix:
				cell.acceptability = (cells or STANDARD)[(cell.severity, cell.probability)]
			doc.save()
			return doc.name
		finally:
			frappe.set_user("Administrator")

	def approved_policy(self, cells: dict[tuple[int, int], str] | None = None) -> str:
		name = self.policy(cells)
		self.sign_as(self.qa, rm.POLICY, name, "Review")
		self.sign_as(self.manager, rm.POLICY, name, "Approve")
		return name

	def ensure_policy(self) -> None:
		"""The tests' standard policy in force, whatever an earlier test approved."""
		current = rm.current_policy()
		if not current or rm.policy_scales(current)[2] != STANDARD:
			self.approved_policy()

	def hazard(self, **values) -> dict:
		return {
			"hazard_id": "H-01",
			"hazard_category": "Energy",
			"hazard": "Balloon bursts on inflation",
			"sequence_of_events": "Balloon over-inflated; wall thinner than specified",
			"hazardous_situation": "Balloon fragments left in the bladder",
			"harm": "Infection; cystoscopy to retrieve the fragments",
			"severity": 4,
			"probability": 4,  # 2 * 4 + 4 = 12: unacceptable
			"residual_severity": 2,
			"residual_probability": 1,  # acceptable
			**values,
		}

	def control(self, target: str, **values) -> dict:
		return {
			"hazard_id": "H-01",
			"option": rm.INHERENT,
			"description": "Balloon wall thickness and burst volume specified and tested on every lot",
			"control_doctype": "Controlled Document",
			"control_name": target,
			"implementation_evidence_type": "Controlled Document",
			"implementation_evidence": target,
			"effectiveness_evidence_type": "Controlled Document",
			"effectiveness_evidence": self.plan_document,
			**values,
		}

	def draft_assessment(
		self,
		file: str | None = None,
		hazards: list[dict] | None = None,
		controls: list[dict] | None = None,
		user: str | None = None,
		**values,
	) -> Document:
		file = file or self.risk_file()
		if controls is None:
			controls = [self.control(self.effective())]
		frappe.set_user(user or self.qa)
		try:
			return frappe.get_doc(
				{
					"doctype": rm.ASSESSMENT,
					"risk_management_file": file,
					"reviewer": self.supervisor,
					"hazards": hazards if hazards is not None else [self.hazard()],
					"controls": controls,
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def sign_as(self, user: str, doctype: str, name: str, action: str, reason: str | None = None) -> Document:
		frappe.set_user(user)
		try:
			sign_action(doctype, name, action, PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")
		return frappe.get_doc(doctype, name)

	def through(self, assessment: Document, *steps: tuple[str, str]) -> Document:
		for user, action in steps:
			assessment = self.sign_as(user, rm.ASSESSMENT, assessment.name, action)
		return assessment

	def approved(self, assessment: Document | None = None, **values) -> Document:
		"""Authored by QA, reviewed by the Production Supervisor, approved by a second QA person."""
		assessment = assessment or self.draft_assessment(**values)
		return self.through(
			assessment, (self.qa, "Author"), (self.supervisor, "Review"), (self.qa2, "Approve")
		)

	def revise(self, file: str, **values) -> Document:
		"""A new revision of an approved file, under an approved change request listing it."""
		change = approved_change(
			self.author, self.qa, file, record_type=rm.FILE, change_type="Product or BOM"
		)
		frappe.set_user(self.qa)
		try:
			revision = frappe.get_doc(rm.ASSESSMENT, rm.start_revision(file, change_request=change, **values))
			revision.summary_of_change = "Hazards reviewed under the change"
			revision.reviewer = self.supervisor  # the author chooses the reviewer, as for a document
			revision.save()
		finally:
			frappe.set_user("Administrator")
		return revision

	def reviews(self, file: str, **filters) -> list[frappe._dict]:
		return frappe.get_all(
			rm.REVIEW,
			filters={"risk_management_file": file, **filters},
			fields=["name", "trigger", "status", "source_doctype", "source_name", "outcome", "details"],
			order_by="creation asc",
		)

	def signatures_on(self, doctype: str, name: str) -> list[tuple[str, str]]:
		return [
			(row.signer, row.meaning)
			for row in frappe.get_all(
				"Electronic Signature",
				filters={"reference_doctype": doctype, "reference_name": name},
				fields=["signer", "meaning"],
				order_by="signed_at asc, creation asc",
			)
		]


@verifies("URS-QMS-017")
class TestPolicy(RiskTestCase):
	def test_one_policy_in_force(self):
		first = self.approved_policy()
		second = self.approved_policy()

		self.assertEqual(rm.current_policy(), second)
		self.assertEqual(frappe.db.count(rm.POLICY, {"status": rm.IN_FORCE}), 1)
		self.assertEqual(
			frappe.db.get_value(rm.POLICY, first, ["status", "superseded_by"]), (rm.SUPERSEDED, second)
		)
		self.assertEqual(frappe.db.get_value(rm.POLICY, second, "in_force_since"), getdate(today()))
		self.assertTrue(verify_signatures(rm.POLICY, second))

	def test_qa_reviews_and_management_approves_as_two_people(self):
		name = self.policy()
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.manager, rm.POLICY, name, "Review")  # QA reviews (plan D-21)
		self.sign_as(self.qa, rm.POLICY, name, "Review")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.qa2, rm.POLICY, name, "Approve")  # QA does not approve its own policy
		policy = self.sign_as(self.manager, rm.POLICY, name, "Approve")

		self.assertEqual(
			self.signatures_on(rm.POLICY, name), [(self.qa, "Reviewed"), (self.manager, "Approved")]
		)
		self.assertEqual(
			(policy.docstatus, policy.status, policy.approved_by), (1, rm.IN_FORCE, self.manager)
		)

	def test_one_person_cannot_review_and_approve(self):
		both = dual_role_user()  # QA and Manufacturing Manager
		name = self.policy()
		self.sign_as(both, rm.POLICY, name, "Review")
		with self.assertRaisesRegex(frappe.ValidationError, "second person"):
			self.sign_as(both, rm.POLICY, name, "Approve")

	def test_a_new_policy_starts_with_the_d13_scales_and_every_pair(self):
		policy = frappe.get_doc(rm.POLICY, self.policy())

		self.assertEqual([row.level_name for row in policy.severity_levels], list(rm.D13_SEVERITY))
		self.assertEqual([row.level_name for row in policy.probability_levels], list(rm.D13_PROBABILITY))
		self.assertEqual(len(policy.matrix), 25)

	def test_every_pair_is_decided_and_never_more_acceptable_as_risk_rises(self):
		blank = self.policy({**STANDARD, (3, 3): ""})
		with self.assertRaisesRegex(frappe.ValidationError, "every pair"):
			self.sign_as(self.qa, rm.POLICY, blank, "Review")
		inverted = self.policy({**STANDARD, (5, 5): rm.ACCEPTABLE})
		with self.assertRaisesRegex(frappe.ValidationError, "more acceptable"):
			self.sign_as(self.qa, rm.POLICY, inverted, "Review")

	def test_the_scales_are_numbered_without_gaps(self):
		frappe.set_user(self.qa)
		doc = frappe.get_doc({"doctype": rm.POLICY, "title": "Gaps", "policy_statement": "x"}).insert()
		doc.severity_levels[-1].level = 7
		with self.assertRaisesRegex(frappe.ValidationError, "without gaps"):
			doc.save()

	def test_a_reviewed_policy_changes_only_by_its_acts(self):
		name = self.policy()
		self.sign_as(self.qa, rm.POLICY, name, "Review")
		frappe.set_user(self.qa)
		doc = frappe.get_doc(rm.POLICY, name)
		doc.title = "Changed after review"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc.reload()
		with self.assertRaises(REFUSED):
			doc.submit()  # Frappe's own Submit never approves it

	def test_returned_with_a_reason_to_draft(self):
		name = self.policy()
		self.sign_as(self.qa, rm.POLICY, name, "Review")
		with self.assertRaisesRegex(frappe.ValidationError, "what must change"):
			self.sign_as(self.manager, rm.POLICY, name, "Return")
		policy = self.sign_as(
			self.manager, rm.POLICY, name, "Return", "Probability 3 needs a frequency range"
		)
		self.assertEqual((policy.status, policy.reviewed_by), (rm.DRAFT, None))

	def test_assessment_keeps_policy(self):
		assessment = self.approved()
		kept = assessment.policy
		self.approved_policy({pair: rm.ACCEPTABLE for pair in STANDARD})

		assessment.reload()
		self.assertEqual(assessment.policy, kept)
		self.assertEqual(assessment.hazards[0].initial_acceptability, rm.UNACCEPTABLE)
		draft = self.draft_assessment()
		self.assertEqual(draft.policy, rm.current_policy())
		self.assertEqual(draft.hazards[0].initial_acceptability, rm.ACCEPTABLE)


@verifies("URS-QMS-018")
class TestRiskManagementFile(RiskTestCase):
	def test_item_in_two_files_refused(self):
		item = self.finished_good()
		first = self.risk_file([item])

		with self.assertRaisesRegex(frappe.ValidationError, first):
			self.risk_file([item])
		with self.assertRaisesRegex(frappe.ValidationError, "twice"):
			self.risk_file([self.finished_good()] * 2)

	def test_a_file_covers_finished_goods(self):
		frappe.set_user("Administrator")
		material = new_item(RAW_MATERIAL).name
		with self.assertRaisesRegex(frappe.ValidationError, "finished goods"):
			self.risk_file([material])

	def test_one_file_per_family(self):
		file = self.risk_file()
		family = frappe.db.get_value(rm.FILE, file, "family_name")
		frappe.set_user(self.qa)
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{
					"doctype": rm.FILE,
					"family_name": family,
					"intended_use": "x",
					"foreseeable_misuse": "x",
					"risk_management_plan": self.plan_document,
				}
			).insert()

	def test_the_plan_is_effective_when_signed(self):
		file = risk_file_for(self.qa, self.registered("SOP"), [self.finished_good()])
		assessment = self.draft_assessment(file)
		with self.assertRaisesRegex(frappe.ValidationError, "risk management plan"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

	def test_the_scope_changes_only_through_a_revision(self):
		assessment = self.approved()
		file = frappe.get_doc(rm.FILE, assessment.risk_management_file)
		self.assertEqual((file.status, file.current_revision), (rm.IN_FORCE, assessment.name))

		frappe.set_user(self.qa)
		file.intended_use = "Suprapubic drainage"
		with self.assertRaises(frappe.PermissionError):
			file.save()
		file.reload()
		file.current_revision = None  # what the system sets is not typed
		file.save()
		self.assertEqual(frappe.db.get_value(rm.FILE, file.name, "current_revision"), assessment.name)

	def test_the_revision_takes_the_files_scope_and_gives_it_back_approved(self):
		assessment = self.draft_assessment()
		file = frappe.get_doc(rm.FILE, assessment.risk_management_file)
		self.assertEqual(assessment.intended_use, file.intended_use)
		self.assertEqual([r.item for r in assessment.items], [r.item for r in file.items])

		frappe.set_user(self.qa)
		assessment.intended_use = "Drainage of the bladder, up to 30 days"
		assessment.save()
		self.approved(assessment)
		self.assertEqual(frappe.db.get_value(rm.FILE, file.name, "intended_use"), assessment.intended_use)


@verifies("URS-QMS-019")
class TestScoring(RiskTestCase):
	def test_score_not_typed(self):
		hazards = [
			self.hazard(initial_acceptability=rm.ACCEPTABLE, residual_acceptability=rm.UNACCEPTABLE),
			self.hazard(
				hazard_id="H-02", severity=5, probability=1, residual_severity=0, residual_probability=0
			),
			self.hazard(
				hazard_id="H-03", severity=1, probability=5, residual_severity=0, residual_probability=0
			),
		]
		assessment = self.draft_assessment(hazards=hazards)
		rows = {row.hazard_id: row for row in assessment.hazards}

		self.assertEqual(rows["H-01"].initial_acceptability, rm.UNACCEPTABLE)
		self.assertEqual(rows["H-01"].residual_acceptability, rm.ACCEPTABLE)
		self.assertEqual(rows["H-02"].initial_acceptability, rm.ALARP)  # severity 5, probability 1
		self.assertEqual(rows["H-03"].initial_acceptability, rm.ACCEPTABLE)  # severity 1, probability 5
		self.assertIsNone(rows["H-02"].residual_acceptability)
		self.assertEqual(assessment.highest_residual_risk, rm.ACCEPTABLE)
		self.assertEqual(assessment.policy, rm.current_policy())

	def test_ratings_are_within_the_scales(self):
		for values in ({"severity": 6}, {"probability": 0}, {"residual_severity": 9}):
			with self.subTest(**values), self.assertRaisesRegex(frappe.ValidationError, "1 to 5"):
				self.draft_assessment(hazards=[self.hazard(**values)])

	def test_the_situation_and_harm_are_recorded(self):
		for field in ("sequence_of_events", "hazardous_situation", "harm", "hazard_category"):
			with self.subTest(field=field), self.assertRaises(frappe.MandatoryError):
				self.draft_assessment(hazards=[self.hazard(**{field: None})])

	def test_each_hazard_once(self):
		with self.assertRaisesRegex(frappe.ValidationError, "twice"):
			self.draft_assessment(hazards=[self.hazard(), self.hazard()])


@verifies("URS-QMS-020")
class TestControls(RiskTestCase):
	def test_unacceptable_needs_control(self):
		assessment = self.draft_assessment(controls=[])
		with self.assertRaisesRegex(frappe.ValidationError, "add a risk control measure"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

	def test_free_text_control_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.draft_assessment(controls=[self.control(None, control_doctype=None)])
		assessment = self.draft_assessment()
		frappe.set_user(self.qa)
		assessment.controls[0].control_name = None
		assessment.flags.ignore_mandatory = True  # the rule holds without the form's mandatory check
		with self.assertRaisesRegex(frappe.ValidationError, "a description alone is not a control"):
			assessment.save()

	def test_safety_information_needs_labelling(self):
		sop = self.effective("SOP")
		with self.assertRaisesRegex(frappe.ValidationError, "Packaging and Labelling"):
			self.draft_assessment(controls=[self.control(sop, option=rm.INFORMATION)])
		label = self.effective("Packaging and Labelling")
		assessment = self.draft_assessment(controls=[self.control(label, option=rm.INFORMATION)])
		self.assertEqual(assessment.controls[0].control_name, label)

	def test_inherently_safe_design_is_a_design_element(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Specification"):
			self.draft_assessment(controls=[self.control(self.effective("SOP"), option=rm.INHERENT)])
		with self.assertRaisesRegex(frappe.ValidationError, "implemented by"):
			item = self.control(
				self.finished_good(),
				option=rm.INFORMATION,
				control_doctype="Item",
				implementation_evidence=self.plan_document,
			)
			self.draft_assessment(controls=[item])

	def test_an_item_is_a_design_element_in_itself(self):
		"""An item or a bill of materials is an inherently safe design control (plan Q6); only a
		document must be a Specification."""
		item = self.control(
			self.finished_good(),
			option=rm.INHERENT,
			control_doctype="Item",
			implementation_evidence=self.plan_document,
		)
		self.assertEqual(self.draft_assessment(controls=[item]).controls[0].control_doctype, "Item")

	def test_a_risk_is_reduced_only_by_a_control(self):
		lowered = self.hazard(severity=2, probability=2, residual_severity=1, residual_probability=1)
		assessment = self.draft_assessment(hazards=[lowered], controls=[])
		with self.assertRaisesRegex(frappe.ValidationError, "reduced only by a risk control measure"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

	def test_control_not_in_force_blocks(self):
		draft_spec = self.registered("Specification")
		control = self.control(draft_spec, implementation_evidence=self.plan_document)
		assessment = self.through(
			self.draft_assessment(controls=[control]), (self.qa, "Author"), (self.supervisor, "Review")
		)
		with self.assertRaisesRegex(frappe.ValidationError, "No longer in force"):
			self.sign_as(self.qa2, rm.ASSESSMENT, assessment.name, "Approve")
		self.assertEqual(frappe.db.get_value(rm.ASSESSMENT, assessment.name, "docstatus"), 0)

	def test_approve_supersedes_and_pins(self):
		spec = self.effective()
		first = self.draft_assessment(controls=[self.control(spec, pinned_version="typed")])
		self.assertIsNone(first.controls[0].pinned_version)  # pinned only at approval

		first = self.approved(first)
		self.assertEqual(
			first.controls[0].pinned_version, pinned_records.version_token("Controlled Document", spec)
		)
		self.assertEqual(
			first.controls[0].pinned_version,
			frappe.db.get_value("Controlled Document", spec, "current_revision"),
		)

		second = self.approved(self.revise(first.risk_management_file))
		first.reload()
		self.assertEqual((first.status, first.superseded_by), (rm.SUPERSEDED, second.name))
		self.assertEqual(second.name, f"{first.risk_management_file}-R02")
		self.assertEqual(
			frappe.db.get_value(rm.FILE, first.risk_management_file, "current_revision"), second.name
		)
		self.assertTrue(verify_signatures(rm.ASSESSMENT, first.name))

	def test_a_control_introduces_only_a_hazard_of_the_assessment(self):
		with self.assertRaisesRegex(frappe.ValidationError, "7.5"):
			self.draft_assessment(controls=[self.control(self.effective(), introduces_hazard_id="H-99")])
		assessment = self.draft_assessment(
			hazards=[
				self.hazard(),
				self.hazard(
					hazard_id="H-02", severity=1, probability=1, residual_severity=1, residual_probability=1
				),
			],
			controls=[self.control(self.effective(), introduces_hazard_id="H-02")],
		)
		self.assertEqual(assessment.controls[0].introduces_hazard_id, "H-02")


@verifies("URS-QMS-021")
class TestEvidenceAndResidualRisk(RiskTestCase):
	def test_residual_needs_justification(self):
		hazard = self.hazard(severity=5, probability=5, residual_severity=4, residual_probability=4)
		assessment = self.draft_assessment(hazards=[hazard])
		self.assertEqual(assessment.hazards[0].residual_acceptability, rm.UNACCEPTABLE)
		with self.assertRaisesRegex(frappe.ValidationError, "justify the residual risk"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

		frappe.set_user(self.qa)
		assessment.reload()
		assessment.hazards[0].justification = "Benefit of bladder drainage outweighs the residual risk."
		assessment.save()
		self.assertEqual(self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author").status, rm.IN_REVIEW)

	def test_every_hazard_has_a_residual_risk(self):
		assessment = self.draft_assessment(hazards=[self.hazard(residual_severity=0, residual_probability=0)])
		with self.assertRaisesRegex(frappe.ValidationError, "estimate the residual risk"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

	def test_the_residual_risk_is_not_higher(self):
		assessment = self.draft_assessment(hazards=[self.hazard(residual_severity=5, residual_probability=1)])
		with self.assertRaisesRegex(frappe.ValidationError, "not higher"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

	def test_each_control_has_its_verifying_records(self):
		spec = self.effective()
		missing = self.draft_assessment(
			controls=[self.control(spec, effectiveness_evidence=None, effectiveness_evidence_type=None)]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "effectiveness"):
			self.sign_as(self.qa, rm.ASSESSMENT, missing.name, "Author")
		not_effective = self.draft_assessment(
			controls=[self.control(spec, implementation_evidence=self.registered())]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "evidence"):
			self.sign_as(self.qa, rm.ASSESSMENT, not_effective.name, "Author")


@verifies("URS-QMS-022")
class TestSignatures(RiskTestCase):
	def test_what_the_author_signed_is_what_is_approved(self):
		"""A policy brought into force after the author signed is not taken silently: the scores the
		author and reviewer signed for stay, and approval asks for the revision to be authored again."""
		assessment = self.through(self.draft_assessment(), (self.qa, "Author"), (self.supervisor, "Review"))
		signed_policy = assessment.policy
		stricter = {cell: rm.UNACCEPTABLE if cell != (1, 1) else rm.ACCEPTABLE for cell in STANDARD}
		self.approved_policy(stricter)
		frappe.set_user(self.qa)
		try:
			frappe.get_doc(rm.ASSESSMENT, assessment.name).save()  # a save with nothing changed
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(rm.ASSESSMENT, assessment.name, "policy"), signed_policy)
		with self.assertRaisesRegex(frappe.ValidationError, "policy has changed"):
			self.sign_as(self.qa2, rm.ASSESSMENT, assessment.name, "Approve")
		self.approved_policy()  # the standard policy again, for the other tests

	def test_three_distinct_signers(self):
		assessment = self.draft_assessment(reviewer=None)
		frappe.set_user(self.qa)
		assessment.reviewer = self.qa
		assessment.save()
		with self.assertRaisesRegex(frappe.ValidationError, "reviewer cannot be the author"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")

		frappe.set_user(self.qa)
		assessment.reload()
		assessment.reviewer = self.qa2  # a QA person reviewing
		assessment.save()
		assessment = self.through(assessment, (self.qa, "Author"), (self.qa2, "Review"))
		with self.assertRaisesRegex(frappe.ValidationError, "author"):
			self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Approve")
		with self.assertRaisesRegex(frappe.ValidationError, "reviewer"):
			self.sign_as(self.qa2, rm.ASSESSMENT, assessment.name, "Approve")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.supervisor, rm.ASSESSMENT, assessment.name, "Approve")  # QA approves

		third = dual_role_user()
		assessment = self.sign_as(third, rm.ASSESSMENT, assessment.name, "Approve")
		self.assertEqual(
			self.signatures_on(rm.ASSESSMENT, assessment.name),
			[(self.qa, "Authored"), (self.qa2, "Reviewed"), (third, "Approved")],
		)
		self.assertEqual((assessment.docstatus, assessment.approver), (1, third))

	def test_only_the_author_edits_the_draft_and_nobody_after(self):
		assessment = self.draft_assessment()
		frappe.set_user(self.qa2)
		assessment.summary_of_change = "Not mine"
		with self.assertRaises(frappe.PermissionError):
			assessment.save()

		assessment = self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")
		frappe.set_user(self.qa)
		assessment.hazards[0].probability = 1
		with self.assertRaises(frappe.PermissionError):
			assessment.save()

	def test_what_the_system_sets_is_not_typed(self):
		assessment = self.draft_assessment()
		frappe.set_user(self.qa)
		assessment.status = rm.APPROVED
		assessment.approver = self.qa
		assessment.save()
		self.assertEqual((assessment.status, assessment.approver), (rm.DRAFT, None))

	def test_returned_with_a_reason(self):
		assessment = self.sign_as(self.qa, rm.ASSESSMENT, self.draft_assessment().name, "Author")
		with self.assertRaisesRegex(frappe.ValidationError, "what must change"):
			self.sign_as(self.supervisor, rm.ASSESSMENT, assessment.name, "Return")
		assessment = self.sign_as(
			self.supervisor, rm.ASSESSMENT, assessment.name, "Return", "H-01 misses the leak"
		)
		self.assertEqual(assessment.status, rm.DRAFT)

	def test_approved_only_by_signing_and_never_deleted_or_cancelled(self):
		assessment = self.through(self.draft_assessment(), (self.qa, "Author"), (self.supervisor, "Review"))
		frappe.set_user(self.qa2)
		with self.assertRaises(REFUSED):
			frappe.get_doc(rm.ASSESSMENT, assessment.name).submit()

		frappe.set_user("Administrator")
		assessment = self.sign_as(self.qa2, rm.ASSESSMENT, assessment.name, "Approve")
		with self.assertRaises(REFUSED):
			frappe.delete_doc(rm.ASSESSMENT, assessment.name, ignore_permissions=True)
		with self.assertRaises(REFUSED):
			assessment.flags.audit_reason = "Trying to cancel"
			assessment.cancel()
		assessment.reload()
		assessment.summary_of_change = "Changed after approval"
		with self.assertRaises(REFUSED):
			assessment.flags.audit_reason = "Trying to change"
			assessment.save()

	def test_wrong_password_records_nothing(self):
		assessment = self.draft_assessment()
		frappe.set_user(self.qa)
		with self.assertRaises(frappe.ValidationError):
			sign_action(rm.ASSESSMENT, assessment.name, "Author", "wrong password")
		reset_lockout(self.qa)
		self.assertEqual(frappe.db.get_value(rm.ASSESSMENT, assessment.name, "status"), rm.DRAFT)


@verifies("URS-QMS-023")
class TestReport(RiskTestCase):
	def report(self, assessment: str, user: str | None = None, **values) -> Document:
		frappe.set_user(user or self.qa)
		try:
			return frappe.get_doc(
				{
					"doctype": rm.REPORT,
					"risk_assessment": assessment,
					"plan_implemented": 1,
					"plan_review": "Every activity of the plan checked against its record.",
					"overall_conclusion": "All residual risks acceptable; overall residual risk acceptable.",
					"information_sources": [{"controlled_document": self.sop}],
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def setUp(self):
		super().setUp()
		if not getattr(type(self), "sop", None):
			type(self).sop = self.effective("SOP")

	def test_qa_approves_a_signed_report_on_the_approved_revision(self):
		assessment = self.approved()
		report = self.report(assessment.name)
		self.assertEqual(
			(report.risk_management_file, report.overall_residual_risk),
			(assessment.risk_management_file, rm.ACCEPTABLE),
		)
		report = self.sign_as(self.qa, rm.REPORT, report.name, "Author")
		with self.assertRaisesRegex(frappe.ValidationError, "author"):
			self.sign_as(self.qa, rm.REPORT, report.name, "Approve")
		report = self.sign_as(self.qa2, rm.REPORT, report.name, "Approve")

		self.assertEqual((report.docstatus, report.status), (1, rm.APPROVED))
		self.assertEqual(
			self.signatures_on(rm.REPORT, report.name), [(self.qa, "Authored"), (self.qa2, "Approved")]
		)
		self.assertEqual(
			frappe.db.get_value(rm.FILE, assessment.risk_management_file, "current_report"), report.name
		)

	def test_only_on_the_approved_revision_in_force(self):
		with self.assertRaisesRegex(frappe.ValidationError, "approved revision in force"):
			self.report(self.draft_assessment().name)

	def test_plan_conclusion_and_post_production_sops_are_needed(self):
		report = self.report(self.approved().name)
		complete = {
			"plan_implemented": 1,
			"overall_conclusion": "All residual risks acceptable.",
			"information_sources": [{"controlled_document": self.sop}],
		}
		for values, message in (
			({"plan_implemented": 0}, "plan was implemented"),
			({"overall_conclusion": ""}, "overall residual risk"),
			({"information_sources": []}, "post-production"),
			(
				{"information_sources": [{"controlled_document": self.effective("Specification")}]},
				"effective SOP",
			),
		):
			with self.subTest(values=values):
				frappe.set_user(self.qa)
				doc = frappe.get_doc(rm.REPORT, report.name)
				doc.update({**complete, **values})
				doc.save()  # its author edits the draft
				with self.assertRaisesRegex(frappe.ValidationError, message):
					self.sign_as(self.qa, rm.REPORT, report.name, "Author")

	def test_a_new_revision_leaves_the_report_out_of_date(self):
		assessment = self.approved()
		report = self.report(assessment.name)
		self.sign_as(self.qa, rm.REPORT, report.name, "Author")
		self.sign_as(self.qa2, rm.REPORT, report.name, "Approve")

		self.approved(self.revise(assessment.risk_management_file))
		self.assertEqual(frappe.db.get_value(rm.REPORT, report.name, "status"), rm.SUPERSEDED)
		self.assertIsNone(frappe.db.get_value(rm.FILE, assessment.risk_management_file, "current_report"))


@verifies("URS-QMS-024")
class TestRevisionUnderChange(RiskTestCase):
	def test_revision_needs_cr_or_review(self):
		file = self.approved().risk_management_file
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(
			frappe.ValidationError, "approved change request or the open risk review"
		):
			rm.start_revision(file)
		other = approved_change(
			self.author, self.qa, self.risk_file(), record_type=rm.FILE, change_type="Product or BOM"
		)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			rm.start_revision(file, change_request=other)

		revision = self.revise(file)
		self.assertTrue(revision.change_request)
		# The change raised a review of the file; the revision answers it.
		self.assertEqual(revision.risk_review, self.reviews(file, trigger=rm.CHANGE_REQUEST_TRIGGER)[0].name)
		with self.assertRaisesRegex(frappe.ValidationError, "in progress"):
			self.revise(file)

	@verifies("URS-QMS-033")
	def test_a_change_request_revises_the_file_once_and_a_withdrawn_revision_does_not_count(self):
		file = self.approved().risk_management_file
		change = approved_change(
			self.author, self.qa, file, record_type=rm.FILE, change_type="Product or BOM"
		)

		def start() -> str:
			frappe.set_user(self.qa)
			try:
				return rm.start_revision(file, change_request=change)
			finally:
				frappe.set_user("Administrator")

		given_up = start()
		self.sign_as(self.qa, rm.ASSESSMENT, given_up, "Withdraw", "Started on the wrong analysis")
		revision = frappe.get_doc(rm.ASSESSMENT, start())  # the withdrawn one spent nothing
		revision.update(
			{"summary_of_change": "Hazards reviewed under the change", "reviewer": self.supervisor}
		)
		frappe.set_user(self.qa)
		revision.save()
		frappe.set_user("Administrator")
		self.approved(revision)

		with self.assertRaisesRegex(frappe.ValidationError, f"{change} has already changed"):
			start()
		row = frappe.get_doc("Change Request", change).affected_records[0]
		self.assertEqual((row.change_record_type, row.change_record), (rm.ASSESSMENT, revision.name))

	def test_an_open_review_allows_a_revision(self):
		file = self.approved().risk_management_file
		review = rm.raise_review(file, rm.COMPLAINT, "Item", self.finished_good())
		frappe.set_user(self.qa)
		revision = frappe.get_doc(rm.ASSESSMENT, rm.start_revision(file, risk_review=review))
		self.assertEqual(revision.risk_review, review)
		self.assertEqual(len(revision.hazards), 1)  # it starts from the revision in force


@verifies("URS-QMS-025")
class TestReviews(RiskTestCase):
	def test_control_change_raises_one_review(self):
		spec = self.effective()
		file = self.approved(controls=[self.control(spec)]).risk_management_file

		self.issue(frappe.get_doc("Controlled Document", spec), summary="Burst volume raised to 40 ml")
		frappe.set_user("Administrator")
		raised = self.reviews(file)
		self.assertEqual(
			[(r.trigger, r.source_doctype, r.source_name) for r in raised],
			[(rm.CONTROL_CHANGED, "Controlled Document", spec)],
		)
		self.assertIn(spec, raised[0].details)

		pinned_records.sweep()
		self.issue(frappe.get_doc("Controlled Document", spec), summary="Burst volume back to 30 ml")
		frappe.set_user("Administrator")
		self.assertEqual(len(self.reviews(file)), 1)  # one open per file and source

	def test_a_withdrawn_control_raises_a_review(self):
		spec = self.effective()
		file = self.approved(controls=[self.control(spec)]).risk_management_file
		self.sign_as(self.qa, "Controlled Document", spec, "Withdraw", "Replaced by the new balloon design")
		self.assertEqual(
			[r.details for r in self.reviews(file)], [f"Controlled Document {spec} is no longer in force."]
		)

	def test_the_daily_sweep_finds_a_change_made_past_the_hooks(self):
		spec = self.effective()
		file = self.approved(controls=[self.control(spec)]).risk_management_file
		frappe.db.set_value("Controlled Document", spec, "status", "Obsolete")  # a database correction
		self.assertEqual(self.reviews(file), [])

		pinned_records.sweep()
		pinned_records.sweep()
		self.assertEqual(len(self.reviews(file, trigger=rm.CONTROL_CHANGED)), 1)

	def test_periodic_review(self):
		file = self.approved().risk_management_file
		self.assertEqual(
			frappe.db.get_value(rm.FILE, file, "next_periodic_review"),
			getdate(add_months(today(), 12)),
		)
		frappe.db.set_value(rm.FILE, file, "next_periodic_review", today())  # the day has come
		rm.raise_periodic_reviews()
		rm.raise_periodic_reviews()
		periodic = self.reviews(file, trigger=rm.PERIODIC)
		self.assertEqual(len(periodic), 1)
		self.assertEqual(
			frappe.db.get_value(rm.REVIEW, periodic[0].name, "due_date"), getdate(add_days(today(), 30))
		)

		with use_setting("risk_periodic_review_months", 6):
			self.sign_as(
				self.qa,
				rm.REVIEW,
				periodic[0].name,
				"Close: No Change",
				"No new hazards; complaints reviewed",
			)
		self.assertEqual(
			frappe.db.get_value(rm.FILE, file, "next_periodic_review"), getdate(add_months(today(), 6))
		)

	def test_no_change_is_refused_while_the_control_is_out_of_force(self):
		spec = self.effective()
		file = self.approved(controls=[self.control(spec)]).risk_management_file
		self.sign_as(self.qa, "Controlled Document", spec, "Withdraw", "Replaced by the new balloon design")
		(review,) = self.reviews(file)
		with self.assertRaisesRegex(frappe.ValidationError, "no longer in force"):
			self.sign_as(self.qa2, rm.REVIEW, review.name, "Close: No Change", "Nothing to change")

	def test_a_withdrawn_draft_no_longer_holds_the_file_or_its_review(self):
		file = self.approved().risk_management_file
		review = rm.raise_review(file, rm.CAPA, "Item", self.finished_good())
		frappe.set_user(self.qa)
		try:
			stuck = rm.start_revision(file, risk_review=review)
		finally:
			frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.ValidationError, "withdraw it before closing"):
			self.sign_as(self.qa2, rm.REVIEW, review, "Close: No Change", "No change after all")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.supervisor, rm.ASSESSMENT, stuck, "Withdraw", "Author left")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			self.sign_as(self.qa2, rm.ASSESSMENT, stuck, "Withdraw")
		self.sign_as(self.qa2, rm.ASSESSMENT, stuck, "Withdraw", "The CAPA found nothing to change")
		self.assertEqual(
			frappe.db.get_value(rm.ASSESSMENT, stuck, ["status", "docstatus"]), (rm.WITHDRAWN, 0)
		)
		closed = self.sign_as(
			self.qa2, rm.REVIEW, review, "Close: No Change", "The CAPA found nothing to change"
		)
		self.assertEqual(closed.status, rm.CLOSED)
		self.assertIsNone(rm.revision_in_progress(file))

	def test_no_change_needs_rationale(self):
		file = self.approved().risk_management_file
		review = rm.raise_review(file, rm.COMPLAINT, "Item", self.finished_good())
		with self.assertRaisesRegex(frappe.ValidationError, "rationale"):
			self.sign_as(self.qa, rm.REVIEW, review, "Close: No Change")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.supervisor, rm.REVIEW, review, "Close: No Change", "Not mine to close")

		closed = self.sign_as(
			self.qa, rm.REVIEW, review, "Close: No Change", "Complaint not related to a hazard"
		)
		self.assertEqual((closed.docstatus, closed.status, closed.outcome), (1, rm.CLOSED, rm.NO_CHANGE))
		self.assertEqual(self.signatures_on(rm.REVIEW, review), [(self.qa, "Reviewed")])

	def test_a_new_revision_closes_its_review(self):
		file = self.approved().risk_management_file
		review = rm.raise_review(file, rm.CAPA, "Item", self.finished_good())
		frappe.set_user(self.qa)
		revision = frappe.get_doc(rm.ASSESSMENT, rm.start_revision(file, risk_review=review))
		revision.summary_of_change = "Hazard H-01 re-estimated after the CAPA"
		revision.reviewer = self.supervisor
		revision.save()
		with self.assertRaisesRegex(frappe.ValidationError, "in progress"):
			self.sign_as(self.qa2, rm.REVIEW, review, "Close: No Change", "Covered")

		self.approved(revision)
		closed = frappe.get_doc(rm.REVIEW, review)
		self.assertEqual(
			(closed.status, closed.outcome, closed.resulting_revision, closed.closed_by),
			(rm.CLOSED, rm.NEW_REVISION, revision.name, self.qa2),
		)

	def test_the_interface_raises_one_review_per_source(self):
		file = self.risk_file()
		item = self.finished_good()
		first = rm.raise_review(file, rm.COMPLAINT, "Item", item)
		self.assertEqual(rm.raise_review(file, rm.COMPLAINT, "Item", item), first)
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown"):
			rm.raise_review(file, "Rumour", "Item", item)

	def test_the_daily_jobs_run_before_the_reminders(self):
		daily = frappe.get_hooks("scheduler_events")["daily"]
		reminders_run = daily.index("meddevice_suite.work_management.reminders.run")
		for job in (
			"meddevice_suite.qms.risk_management.raise_periodic_reviews",
			"meddevice_suite.qms.pinned_records.sweep",
		):
			with self.subTest(job=job):
				self.assertLess(daily.index(job), reminders_run)

	def test_reviews_are_raised_by_the_system_only(self):
		file = self.risk_file()
		frappe.set_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": rm.REVIEW, "risk_management_file": file, "trigger": rm.PERIODIC}
			).insert(
				ignore_permissions=True  # past the role's permission: the rule still refuses it
			)

	def test_an_open_review_is_a_reminder_until_closed(self):
		file = self.risk_file()
		review = rm.raise_review(file, rm.COMPLAINT, "Item", self.finished_good())
		due = [item for item in reminders.risk_reviews_open() if item.reference_name == review]
		self.assertEqual(len(due), 1)
		self.assertEqual(due[0].roles, ("Quality Manager",))

		self.sign_as(self.qa, rm.REVIEW, review, "Close: No Change", "Not related")
		self.assertFalse([item for item in reminders.risk_reviews_open() if item.reference_name == review])
		self.assertIn("Risk Review", reminders.CATEGORIES)


@verifies("URS-QMS-017", "URS-QMS-022")
class TestPermissionsAndRegistration(RiskTestCase):
	def test_permissions(self):
		doctypes = (rm.POLICY, rm.FILE, rm.ASSESSMENT, rm.REPORT, rm.REVIEW)
		readers = (self.supervisor, self.manager)
		for doctype in doctypes:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.has_permission(doctype, "read", user=self.qa))
				for user in readers:
					self.assertTrue(frappe.has_permission(doctype, "read", user=user))
					self.assertFalse(frappe.has_permission(doctype, "write", user=user))
					self.assertFalse(frappe.has_permission(doctype, "create", user=user))
				self.assertFalse(frappe.has_permission(doctype, "read", user=self.outsider))
				for user in (self.qa, *readers, signer("System Administrator")):
					for ptype in ("submit", "cancel", "amend", "delete", "import", "share"):
						self.assertFalse(frappe.has_permission(doctype, ptype, user=user), (user, ptype))
		for doctype in (rm.POLICY, rm.FILE, rm.ASSESSMENT, rm.REPORT):
			self.assertTrue(frappe.has_permission(doctype, "create", user=self.qa))
		self.assertFalse(frappe.has_permission(rm.REVIEW, "create", user=self.qa))
		self.assertFalse(frappe.has_permission(rm.ASSESSMENT, "write", user=signer("System Administrator")))

	def test_the_signed_acts_are_declared(self):
		acts = {key: act for key, act in SIGNING_ACTIONS.items() if key[0].startswith("Risk ")}
		self.assertEqual(len(acts), 13)  # with Withdraw on an unfinished revision and report
		for (doctype, action), act in acts.items():
			with self.subTest(doctype=doctype, action=action):
				self.assertIn(act.meaning, MEANINGS)
				self.assertTrue(callable(frappe.get_attr(act.perform)))
		self.assertIn("superseded_by", SYSTEM_MAINTAINED[rm.ASSESSMENT])

	def test_kept_ten_years(self):
		for doctype in (rm.POLICY, rm.FILE, rm.ASSESSMENT, rm.REPORT, rm.REVIEW):
			with self.subTest(doctype=doctype):
				self.assertGreaterEqual(retention_years(doctype), 10)

	def test_the_risk_register_lists_the_hazards_in_force(self):
		from meddevice_suite.qms.report.risk_register.risk_register import execute

		assessment = self.approved()
		frappe.set_user(self.supervisor)
		_columns, rows = execute({"risk_management_file": assessment.risk_management_file})
		self.assertEqual(
			[(r["hazard_id"], r["initial_acceptability"]) for r in rows], [("H-01", rm.UNACCEPTABLE)]
		)
		self.assertIn(assessment.controls[0].control_name, rows[0]["controls"])


class TestPinnedRecords(RiskTestCase):
	@verifies("URS-QMS-020")
	def test_version_token_and_in_force_of_documents_and_items(self):
		spec = self.effective()
		revision = frappe.db.get_value("Controlled Document", spec, "current_revision")
		self.assertEqual(pinned_records.version_token("Controlled Document", spec), revision)
		self.assertTrue(pinned_records.in_force("Controlled Document", spec))
		self.assertFalse(pinned_records.in_force("Controlled Document", self.registered()))

		draft = self.finished_good()
		self.assertFalse(pinned_records.in_force("Item", draft))
		approved = self.approved_item(FINISHED_GOOD)
		self.assertTrue(pinned_records.in_force("Item", approved))
		version = frappe.db.get_value("Item", approved, "mds_version")
		self.assertEqual(pinned_records.version_token("Item", approved), str(version))
		with self.assertRaises(frappe.ValidationError):
			pinned_records.version_token("Supplier", "anything")

	@verifies("URS-QMS-025")
	def test_every_pinnable_record_type_reports_its_changes(self):
		hook = "meddevice_suite.qms.pinned_records.on_pinned_change"
		events = frappe.get_hooks("doc_events")
		for doctype in ("Item", "Routing", "Operation", "Quality Inspection Template", "Controlled Document"):
			with self.subTest(doctype=doctype):
				self.assertIn(hook, events[doctype]["on_update"])
		for event in ("on_submit", "on_update_after_submit", "on_cancel"):
			with self.subTest(event=event):
				self.assertIn(hook, events["BOM"][event])
		self.assertEqual(
			set(pinned_records.PINNABLE),
			{"Controlled Document", "BOM", *pinned_records.VERSIONED, "Risk Management Report"},
		)
