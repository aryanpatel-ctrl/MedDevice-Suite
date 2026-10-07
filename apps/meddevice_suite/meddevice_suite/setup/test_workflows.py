import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_roles import user_with_profile
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
			{"allow_self_approval": 1, "condition": "True", "send_email_to_creator": 1},
		)
		frappe.db.set_value(
			"Workflow Document State",
			{"parent": "Purchase Order Approval", "state": "Awaiting Approval"},
			{"update_field": "owner", "update_value": "x@example.com", "is_optional_state": 1},
		)
		frappe.db.set_value("Workflow", "Quotation Price Approval", "is_active", 0)
		frappe.clear_document_cache("Workflow", "Purchase Order Approval")
		frappe.clear_document_cache("Workflow", "Quotation Price Approval")

		apply_configuration()

		for name, declared in WORKFLOWS.items():
			self.assertEqual(as_declared(frappe.get_doc("Workflow", name)), declared_workflow(name, declared))

	def test_a_deleted_workflow_and_state_are_recreated_on_the_next_migrate(self):
		# Removed behind the application's back, as only the database allows now.
		for table in ("Workflow", "Workflow Document State", "Workflow Transition"):
			key = "name" if table == "Workflow" else "parent"
			frappe.db.delete(table, {key: "Quotation Price Approval"})
		frappe.db.delete("Workflow State", "Awaiting Price Approval")
		frappe.clear_document_cache("Workflow", "Quotation Price Approval")
		frappe.cache.delete_value("workflow")

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

	def test_a_declared_workflow_cannot_be_changed_switched_off_or_deleted_on_a_site(self):
		frappe.set_user(user_with_profile("System Administrator"))
		self.addCleanup(frappe.set_user, "Administrator")
		workflow = frappe.get_doc("Workflow", "Purchase Order Approval")

		workflow.is_active = 0
		with self.assertRaises(frappe.PermissionError):
			workflow.save()
		workflow.reload()
		workflow.transitions[0].condition = "doc.grand_total > 100000"
		with self.assertRaises(frappe.PermissionError):
			workflow.save()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Workflow", "Purchase Order Approval")

		self.assertEqual(
			as_declared(frappe.get_doc("Workflow", "Purchase Order Approval")),
			declared_workflow("Purchase Order Approval", WORKFLOWS["Purchase Order Approval"]),
		)

	def test_no_other_workflow_can_take_over_an_approved_record_type(self):
		rival = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": "Purchase Order Fast Track",
				"document_type": "Purchase Order",
				"is_active": 1,
				"workflow_state_field": "workflow_state",
				"states": [{"state": "Approved", "doc_status": "0", "allow_edit": "Purchase User"}],
			}
		)
		with self.assertRaises(frappe.PermissionError):
			rival.insert(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Workflow", "Purchase Order Approval", "is_active"), 1)
