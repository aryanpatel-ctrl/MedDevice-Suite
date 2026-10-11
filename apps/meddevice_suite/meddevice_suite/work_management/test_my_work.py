"""My Work (module 59, URS-PLT-017): tests derived from the requirement's clauses."""

import frappe

from meddevice_suite.master_data.test_versioning import draft_change
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.qms.test_document_control import DocumentControlTestCase
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.work_management import my_work
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

		item = stock_item(f"MDS-MYWORK-{frappe.generate_hash(length=5)}")  # a new item, in draft
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
		revision = self.draft(self.register(title=f"SOP {frappe.generate_hash(length=5)}"))
		self.assertIn(("Document Revision", revision.name), listed(self.author))
		self.act(self.author, revision, "Author")
		self.assertNotIn(("Document Revision", revision.name), listed(self.author))
		self.assertIn(("Document Revision", revision.name), listed(self.reviewer))
		self.act(self.reviewer, revision, "Review")
		self.assertIn(("Document Revision", revision.name), listed(self.qa))
		self.assertNotIn(("Document Revision", revision.name), listed(self.reviewer))

	def test_qa_is_not_asked_to_approve_a_revision_they_reviewed(self):
		revision = self.draft(self.register(title=f"SOP {frappe.generate_hash(length=5)}"), reviewer=self.qa)
		self.act(self.author, revision, "Author")
		self.act(self.qa, revision, "Review")
		self.assertNotIn(("Document Revision", revision.name), listed(self.qa))
