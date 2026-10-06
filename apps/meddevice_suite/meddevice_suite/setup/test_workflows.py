import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.workflows import WORKFLOWS, apply_workflows, as_declared, declared_workflow


@verifies("URS-CMP-022")
class TestDeclaredWorkflows(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def test_each_workflow_is_applied_exactly_as_declared(self):
		for name, declared in WORKFLOWS.items():
			with self.subTest(workflow=name):
				self.assertEqual(
					as_declared(frappe.get_doc("Workflow", name)), declared_workflow(name, declared)
				)

	def test_only_approving_steps_forbid_self_approval(self):
		for name in WORKFLOWS:
			for row in frappe.get_doc("Workflow", name).transitions:
				with self.subTest(workflow=name, action=row.action):
					self.assertEqual(row.allow_self_approval, int(row.action != "Approve"))

	def test_a_manual_change_is_undone_on_the_next_migrate(self):
		frappe.db.set_value(
			"Workflow Transition",
			{"parent": "Purchase Order Approval", "action": "Approve"},
			"allow_self_approval",
			1,
		)
		frappe.db.set_value("Workflow", "Quotation Price Approval", "is_active", 0)
		frappe.clear_document_cache("Workflow", "Purchase Order Approval")
		frappe.clear_document_cache("Workflow", "Quotation Price Approval")

		apply_configuration()

		for name, declared in WORKFLOWS.items():
			self.assertEqual(as_declared(frappe.get_doc("Workflow", name)), declared_workflow(name, declared))

	def test_a_deleted_workflow_and_state_are_recreated_on_the_next_migrate(self):
		frappe.delete_doc("Workflow", "Quotation Price Approval", ignore_permissions=True, force=True)
		frappe.db.delete("Workflow State", "Awaiting Price Approval")

		apply_configuration()

		self.assertTrue(frappe.db.exists("Workflow State", "Awaiting Price Approval"))
		self.assertEqual(
			as_declared(frappe.get_doc("Workflow", "Quotation Price Approval")),
			declared_workflow("Quotation Price Approval", WORKFLOWS["Quotation Price Approval"]),
		)

	def test_an_unchanged_workflow_is_not_saved_again(self):
		before = {name: frappe.db.get_value("Workflow", name, "modified") for name in WORKFLOWS}

		apply_workflows()

		self.assertEqual(
			{name: frappe.db.get_value("Workflow", name, "modified") for name in WORKFLOWS}, before
		)
