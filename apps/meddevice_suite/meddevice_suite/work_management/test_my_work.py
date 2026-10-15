"""My Work (module 59, URS-PLT-017): tests derived from the requirement's clauses."""

import frappe
from frappe.tests import UnitTestCase
from frappe.utils import today

from meddevice_suite.compliance.signing import SIGNING_ACTIONS, sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import LotTestCase, customer_return, deliver, store
from meddevice_suite.master_data.test_versioning import draft_change
from meddevice_suite.qa import verifies
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.qms import test_equipment_care, test_training
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.qms.test_device_master_record import DeviceMasterRecordTestCase
from meddevice_suite.qms.test_document_control import DocumentControlTestCase
from meddevice_suite.qms.test_equipment_care import CareTestCase
from meddevice_suite.qms.test_regulatory_licence import LicenceTestCase
from meddevice_suite.qms.test_training import TrainingTestCase
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.work_management import my_work, reminders
from meddevice_suite.work_management.report.my_work.my_work import execute
from meddevice_suite.work_management.test_work_task import TaskTestCase


def listed(user: str) -> dict[tuple[str, str], str]:
	"""What waits on the person: (record type, record) -> what to do."""
	frappe.set_user(user)
	try:
		return {(item.doctype, item.name): item.what for item in my_work.waiting_on()}
	finally:
		frappe.set_user("Administrator")


@verifies("URS-PLT-017")
class TestTasksAndApprovals(TaskTestCase):
	def test_the_task_waits_on_the_person_assigned_then_on_the_approver(self):
		name = self.raise_task()
		self.assertIn(("Work Task", name), listed(self.operator))
		self.assertNotIn(("Work Task", name), listed(self.qa))
		self.submit(name)
		self.assertNotIn(("Work Task", name), listed(self.operator))
		self.assertIn("Approve the task", listed(self.qa)[("Work Task", name)])

	def test_a_draft_master_waits_on_qa_and_nobody_else(self):
		from meddevice_suite.setup.test_stock import stock_item

		item = stock_item(f"MDS-MYWORK-{frappe.generate_hash(length=10)}")  # a new item, in draft
		self.assertIn(("Item", item), listed(self.qa))
		self.assertNotIn(("Item", item), listed(self.stores_user))

	def test_a_purchase_order_awaiting_approval_waits_on_its_approver(self):
		from frappe.model.workflow import apply_workflow

		from meddevice_suite.lot_control.test_lot_status import lot_item
		from meddevice_suite.master_data.test_supplier_approval import (
			ITEM,
			SUPPLIER,
			approved_for,
			ensure_supplier,
			purchase_order,
		)
		from meddevice_suite.setup.test_stock import approve_for_test

		frappe.set_user("Administrator")
		ensure_supplier(SUPPLIER)
		lot_item(ITEM)
		approve_for_test(ITEM)
		approved_for(SUPPLIER, [ITEM])
		frappe.set_user(signer("Purchase"))
		order = purchase_order().insert()
		apply_workflow(order, "Send for Approval")
		self.assertIn(("Purchase Order", order.name), listed(signer("Management")))
		self.assertNotIn(("Purchase Order", order.name), listed(self.stores_user))

	def test_a_change_request_never_asks_its_requester_to_approve_it(self):
		from meddevice_suite.qms.change_control import submit_for_assessment
		from meddevice_suite.qms.test_change_control import assessment, dual_role_user
		from meddevice_suite.setup.test_stock import work_instruction_for_test

		frappe.set_user("Administrator")
		document = work_instruction_for_test()
		requester = dual_role_user()  # QA and Management, so both approvals would otherwise list it
		frappe.set_user(requester)
		change = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Revise {document}",
				"change_type": "Document",
				"description": "Update the procedure",
				"reason": "Found in an internal audit",
				"affected_records": [
					{"reference_doctype": "Controlled Document", "reference_name": document}
				],
				"actions": [
					{
						"description": f"New revision of {document}",
						"action_owner": requester,
						"due_date": today(),
					}
				],
			}
		).insert()
		submit_for_assessment(change.name)
		frappe.set_user(self.qa)
		change = frappe.get_doc("Change Request", change.name)
		change.update(assessment(classification="Major"))
		change.save()
		sign_action("Change Request", change.name, "Assess", PASSWORD)
		key = ("Change Request", change.name)
		self.assertIn("Approve", listed(self.qa)[key])
		self.assertNotIn(key, listed(requester))
		frappe.set_user(self.qa)
		sign_action("Change Request", change.name, "Approve", PASSWORD)
		self.assertIn("Approve as management", listed(signer("Management"))[key])
		self.assertNotIn(key, listed(requester))
		self.assertNotIn(key, listed(self.qa))

	def test_change_requests_wait_on_whoever_acts_next(self):
		frappe.set_user("Administrator")
		from meddevice_suite.setup.test_stock import work_instruction_for_test

		change = draft_change(self.supervisor, "Controlled Document", work_instruction_for_test())
		self.assertIn(("Change Request", change), listed(self.supervisor))
		self.assertNotIn(("Change Request", change), listed(self.stores_user))

	def test_the_report_lists_them_with_their_records(self):
		name = self.raise_task()
		frappe.set_user(self.operator)
		columns, rows = execute({})
		self.assertEqual([c["fieldname"] for c in columns], ["what", "doctype", "name", "due", "since"])
		self.assertIn(("Work Task", name), [(row["doctype"], row["name"]) for row in rows])


@verifies("URS-PLT-017")
class TestRevisions(DocumentControlTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def test_a_revision_waits_on_its_author_then_its_reviewer_then_qa(self):
		revision = self.draft(self.register(title=f"SOP {frappe.generate_hash(length=10)}"))
		self.assertIn(("Document Revision", revision.name), listed(self.author))
		self.act(self.author, revision, "Author")
		self.assertNotIn(("Document Revision", revision.name), listed(self.author))
		self.assertIn(("Document Revision", revision.name), listed(self.reviewer))
		self.act(self.reviewer, revision, "Review")
		self.assertIn(("Document Revision", revision.name), listed(self.qa))
		self.assertNotIn(("Document Revision", revision.name), listed(self.reviewer))

	def test_qa_is_not_asked_to_approve_a_revision_they_reviewed(self):
		revision = self.draft(self.register(title=f"SOP {frappe.generate_hash(length=10)}"), reviewer=self.qa)
		self.act(self.author, revision, "Author")
		self.act(self.qa, revision, "Review")
		self.assertNotIn(("Document Revision", revision.name), listed(self.qa))


# --- Every signed act a person waits to do is a queue (found in the foundation review of 2026-10-12)


@verifies("URS-PLT-017")
class TestEverySignedActIsAccountedFor(UnitTestCase):
	"""The register of signed acts (compliance.signing.SIGNING_ACTIONS) held to My Work: an act
	added to it fails here until it is listed by a source, listed through its reminder, or declared
	not a queue with the reason."""

	def test_each_signed_act_is_listed_or_said_why_not(self):
		accounted = {
			"a source": set(my_work.LISTED_BY),
			"a reminder": set(my_work.REMINDED_AS),
			"not waiting": set(my_work.NOT_WAITING),
		}
		missing = set(SIGNING_ACTIONS) - set().union(*accounted.values())
		self.assertEqual(missing, set(), "signed acts with no place in My Work")
		for how, acts in accounted.items():
			with self.subTest(how=how):
				self.assertEqual(acts - set(SIGNING_ACTIONS), set(), "no longer signed acts")
		self.assertFalse(
			accounted["a source"] & accounted["a reminder"]
			or accounted["a source"] & accounted["not waiting"]
			or accounted["a reminder"] & accounted["not waiting"],
			"an act accounted for twice",
		)

	def test_each_place_is_real(self):
		for act, source in my_work.LISTED_BY.items():
			with self.subTest(act=act):
				self.assertIn(source, my_work.SOURCES)
		self.assertIn(my_work.reminders, my_work.SOURCES)
		for act, category in my_work.REMINDED_AS.items():
			with self.subTest(act=act):
				self.assertIn(category, reminders.CATEGORIES)
		for act, why in my_work.NOT_WAITING.items():
			with self.subTest(act=act):
				self.assertTrue(why.strip())


@verifies("URS-PLT-017")
class TestDeviceMasterRecordAndRiskQueues(DeviceMasterRecordTestCase):
	"""Users: the Production Supervisor (`supervisor`), QA (`qa`, `qa2`), Management (`manager`), the
	QC Inspector (`inspector`), who reads these records and signs none of them."""

	def test_a_device_master_record_waits_on_its_author_then_on_another_qa(self):
		record = self.dmr_draft(self.product())
		key = (dmr.DOCTYPE, record.name)
		self.assertIn("Complete and sign as author", listed(self.supervisor)[key])
		self.assertNotIn(key, listed(self.qa))
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		self.assertNotIn(key, listed(self.supervisor))
		self.assertIn("Approve the device master record", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.inspector))
		self.assertNotIn(key, listed(self.manager))
		self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")
		self.assertNotIn(key, listed(self.qa))

	def test_qa_is_not_asked_to_approve_a_device_master_record_they_authored(self):
		record = self.dmr_draft(self.product(), user=self.qa)
		self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Author")
		key = (dmr.DOCTYPE, record.name)
		self.assertNotIn(key, listed(self.qa))
		self.assertIn(key, listed(self.qa2))

	def test_a_risk_policy_waits_on_qa_to_review_then_on_management_to_approve(self):
		name = self.policy()
		key = (rm.POLICY, name)
		self.assertIn("Review the risk acceptability policy", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.manager))
		self.sign_as(self.qa, rm.POLICY, name, "Review")
		self.assertNotIn(key, listed(self.qa))
		self.assertIn("Approve the risk acceptability policy", listed(self.manager)[key])
		self.assertNotIn(key, listed(self.supervisor))

	def test_a_risk_assessment_waits_on_its_author_its_reviewer_then_another_qa(self):
		assessment = self.draft_assessment()  # QA authors it; the Production Supervisor reviews it
		key = (rm.ASSESSMENT, assessment.name)
		self.assertIn("Complete and sign as author", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.supervisor))
		self.sign_as(self.qa, rm.ASSESSMENT, assessment.name, "Author")
		self.assertIn("Review the risk assessment", listed(self.supervisor)[key])
		self.assertNotIn(key, listed(self.qa))
		self.sign_as(self.supervisor, rm.ASSESSMENT, assessment.name, "Review")
		self.assertNotIn(key, listed(self.supervisor))
		self.assertNotIn(key, listed(self.qa))  # its author
		self.assertIn("Approve the risk assessment", listed(self.qa2)[key])
		self.assertNotIn(key, listed(self.manager))

	def test_a_risk_management_report_waits_on_its_author_then_another_qa(self):
		assessment = self.approved()
		frappe.set_user(self.qa)
		try:
			report = frappe.get_doc(
				{
					"doctype": rm.REPORT,
					"risk_assessment": assessment.name,
					"plan_implemented": 1,
					"plan_review": "Every activity of the plan checked against its record.",
					"overall_conclusion": "All residual risks acceptable; overall residual risk acceptable.",
					"information_sources": [{"controlled_document": self.plan_document}],
				}
			).insert()
		finally:
			frappe.set_user("Administrator")
		key = (rm.REPORT, report.name)
		self.assertIn("Complete and sign as author", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.qa2))
		self.sign_as(self.qa, rm.REPORT, report.name, "Author")
		self.assertNotIn(key, listed(self.qa))
		self.assertIn("Approve the risk management report", listed(self.qa2)[key])
		self.assertNotIn(key, listed(self.supervisor))


@verifies("URS-PLT-017")
class TestQualificationQueue(CareTestCase):
	# The qualification tests' own records (qms/test_equipment_care.py), without running their tests.
	needing = test_equipment_care.TestQualification.needing
	stage = test_equipment_care.TestQualification.stage
	sop = test_equipment_care.TestQualification.sop

	def test_an_executed_qualification_waits_for_qa_never_its_performer(self):
		asset = self.needing()
		name = self.stage(asset, "IQ")
		key = ("Equipment Qualification", name)
		self.assertNotIn(key, listed(self.qa))  # signed by its performer as the protocol is executed
		self.sign(self.maintenance, "Equipment Qualification", name, "Execute")
		self.assertIn("Approve the IQ", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.maintenance))
		self.sign(self.qa, "Equipment Qualification", name, "Approve")
		self.assertNotIn(key, listed(self.qa))

	def test_qa_is_not_asked_to_approve_a_qualification_they_executed(self):
		name = self.stage(self.needing(), "IQ")
		self.sign(self.qa, "Equipment Qualification", name, "Execute")
		self.assertNotIn(("Equipment Qualification", name), listed(self.qa))


@verifies("URS-PLT-017")
class TestRegulatoryLicenceQueue(LicenceTestCase):
	def test_a_draft_licence_waits_for_qa_to_verify_it(self):
		name = self.draft()
		key = ("Regulatory Licence", name)
		self.assertIn("Verify the regulatory licence", listed(self.qa)[key])
		self.assertNotIn(key, listed(signer("Management")))  # reads licences, does not verify them
		self.act(self.qa, name, "Verify")
		self.assertNotIn(key, listed(self.qa))


@verifies("URS-PLT-017")
class TestTrainingMatrixQueue(TrainingTestCase):
	def test_a_draft_matrix_waits_for_qa_to_approve_it(self):
		document, _revision = self.sop()
		frappe.set_user(self.qa)
		try:
			name = (
				frappe.get_doc(
					{
						"doctype": "Training Matrix",
						"designation": test_training.designation(),
						"items": [{"controlled_document": document.name}],
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")
		key = ("Training Matrix", name)
		self.assertIn("Approve the training matrix", listed(self.qa)[key])
		self.assertNotIn(key, listed(self.author))  # the Production Supervisor reads it
		self.sign(self.qa, "Training Matrix", name, "Approve", "Job's documents checked")
		self.assertNotIn(key, listed(self.qa))


@verifies("URS-PLT-017", "URS-STK-010")
class TestCustomerReturnsQueue(LotTestCase):
	def test_goods_a_customer_sent_back_wait_on_qa_until_decided(self):
		lot = self.approved()
		note = deliver(lot, 3, store("Stores"))
		customer_return(note, 2)  # into the returns store, for QA to decide

		def waiting(user: str) -> list[tuple[str, str]]:
			return [
				(item.doctype, item.name)
				for item in my_work.waiting_on(user)
				if (item.doctype, item.name) == ("Batch", lot)
			]

		self.assertEqual(waiting(self.qa), [("Batch", lot)])
		self.assertEqual(waiting(self.stores_user), [])
