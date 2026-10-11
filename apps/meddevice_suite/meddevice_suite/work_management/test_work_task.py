"""Tasks and approvals (module 59, URS-PLT-016): tests derived from the requirement's clauses."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.esign import SigningRefused
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.work_management import reminders, work_task
from meddevice_suite.work_management.work_task import APPROVED, OPEN, RETURNED, SUBMITTED, WITHDRAWN


class TaskTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.supervisor = signer("Production Supervisor")  # raises the task
		cls.operator = signer("Operator")  # does it
		cls.qa = signer("QA Manager")  # approves it
		cls.stores_user = signer("Stores")  # has no part in it

	def tearDown(self):
		frappe.set_user("Administrator")

	def raise_task(self, **values) -> str:
		frappe.set_user(self.supervisor)
		return (
			frappe.get_doc(
				{
					"doctype": "Work Task",
					"subject": f"Clean the extrusion line {frappe.generate_hash(length=5)}",
					"description": "Clean and log the extrusion line before the validation run",
					"assigned_to": self.operator,
					"approver": self.qa,
					"due_date": add_days(today(), 3),
					**values,
				}
			)
			.insert()
			.name
		)

	def submit(self, name: str, result: str = "Cleaned, log sheet attached") -> None:
		frappe.set_user(self.operator)
		work_task.submit_for_approval(name, result)

	def sign(self, user: str, name: str, act: str, reason: str | None = "Checked the log") -> None:
		frappe.set_user(user)
		sign_action("Work Task", name, act, PASSWORD, reason)

	def task(self, name: str):
		return frappe.get_doc("Work Task", name)


@verifies("URS-PLT-016")
class TestTheFlow(TaskTestCase):
	def test_raised_assigned_done_submitted_and_approved_with_a_signature(self):
		name = self.raise_task()
		self.assertEqual(self.task(name).status, OPEN)
		self.submit(name)
		self.assertEqual(self.task(name).status, SUBMITTED)
		self.sign(self.qa, name, "Approve")
		task = self.task(name)
		self.assertEqual((task.status, task.docstatus, task.approved_by), (APPROVED, 1, self.qa))
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Work Task", "reference_name": name}
		)
		self.assertEqual((signature.signer, signature.meaning), (self.qa, "Approved"))

	def test_returned_with_a_signed_reason_and_done_again(self):
		name = self.raise_task()
		self.submit(name)
		with self.assertRaisesRegex(frappe.ValidationError, "Say what must be done again"):
			self.sign(self.qa, name, "Return", reason=None)
		self.sign(self.qa, name, "Return", reason="The log sheet is not signed")
		task = self.task(name)
		self.assertEqual((task.status, task.return_reason), (RETURNED, "The log sheet is not signed"))
		self.submit(name, "Cleaned again; signed log sheet attached")
		self.sign(self.qa, name, "Approve")
		self.assertEqual(self.task(name).status, APPROVED)

	def test_only_the_person_assigned_submits_and_says_what_was_done(self):
		name = self.raise_task()
		frappe.set_user(self.supervisor)
		with self.assertRaises(frappe.PermissionError):
			work_task.submit_for_approval(name, "Done")
		frappe.set_user(self.operator)
		with self.assertRaisesRegex(frappe.ValidationError, "Say what was done"):
			work_task.submit_for_approval(name, "  ")

	def test_only_its_approver_approves_with_the_password(self):
		name = self.raise_task()
		self.submit(name)
		with self.assertRaises(frappe.PermissionError):
			self.sign(self.supervisor, name, "Approve")
		with self.assertRaises(SigningRefused):
			frappe.set_user(self.qa)
			sign_action("Work Task", name, "Approve", "not the password", "Checked")
		frappe.set_user("Administrator")
		from meddevice_suite.compliance.test_esign import reset_lockout

		reset_lockout(self.qa)
		self.assertEqual(self.task(name).status, SUBMITTED)

	def test_the_approver_is_another_named_person(self):
		with self.assertRaisesRegex(frappe.ValidationError, "not the person assigned"):
			self.raise_task(approver=self.operator)
		with self.assertRaisesRegex(frappe.ValidationError, "own active account"):
			self.raise_task(approver="Administrator")

	def test_withdrawn_before_approval_by_whoever_raised_it_or_qa_with_a_reason(self):
		name = self.raise_task()
		for user in (self.stores_user, self.operator):  # cannot see it; sees it, but did not raise it
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(frappe.PermissionError):
					work_task.withdraw(name, "Not needed")
		frappe.set_user(self.supervisor)
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			work_task.withdraw(name, " ")
		work_task.withdraw(name, "The validation run moved to next month")
		self.assertEqual(self.task(name).status, WITHDRAWN)
		approved = self.raise_task()
		self.submit(approved)
		self.sign(self.qa, approved, "Approve")
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "is approved"):
			work_task.withdraw(approved, "Too late")


@verifies("URS-PLT-016")
class TestWhoChangesWhat(TaskTestCase):
	def test_what_it_is_changes_only_by_whoever_raised_it_or_qa_while_in_hand(self):
		name = self.raise_task()
		frappe.set_user(self.operator)
		task = self.task(name)
		task.due_date = add_days(today(), 30)
		with self.assertRaises(frappe.PermissionError):
			task.save()
		frappe.set_user(self.supervisor)
		task = self.task(name)
		task.due_date = add_days(today(), 30)
		task.save()
		self.submit(name)
		frappe.set_user(self.supervisor)
		task = self.task(name)
		task.description = "Changed while waiting for approval"
		with self.assertRaisesRegex(frappe.PermissionError, "submitted"):
			task.save()

	def test_the_result_only_by_the_person_assigned_and_the_status_only_by_its_steps(self):
		name = self.raise_task()
		frappe.set_user(self.supervisor)
		task = self.task(name)
		task.result = "Done, says the supervisor"
		with self.assertRaises(frappe.PermissionError):
			task.save()
		frappe.set_user(self.operator)
		task = self.task(name)
		task.status = APPROVED
		with self.assertRaises(frappe.PermissionError):
			task.save()

	def test_the_person_assigned_saves_the_result_as_the_desk_sends_the_form(self):
		name = self.raise_task()
		frappe.set_user(self.operator)
		task = self.task(name)
		task.due_date = str(task.due_date)  # the desk sends the unchanged date as text
		task.result = "Half done"
		task.save()

	def test_never_deleted_cancelled_or_submitted_except_by_approval(self):
		name = self.raise_task()
		frappe.set_user(self.supervisor)
		task = self.task(name)
		task.flags.ignore_permissions = True  # past Frappe's permission: the record itself refuses
		with self.assertRaisesRegex(frappe.PermissionError, "signed approval"):
			task.submit()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Work Task", name, ignore_permissions=True)
		self.submit(name)
		self.sign(self.qa, name, "Approve")
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			self.task(name).cancel()

	def test_only_those_in_it_see_it_and_qa_all(self):
		name = self.raise_task()
		for user in (self.supervisor, self.operator, self.qa):
			with self.subTest(user=user):
				frappe.set_user(user)
				self.assertIn(name, frappe.get_list("Work Task", pluck="name", limit=0))
		frappe.set_user(self.stores_user)
		self.assertNotIn(name, frappe.get_list("Work Task", pluck="name", limit=0))
		self.assertFalse(frappe.has_permission("Work Task", "read", doc=name))


@verifies("URS-PLT-016")
class TestItsDueDateIsTracked(TaskTestCase):
	def reminder(self, name: str) -> frappe._dict:
		return frappe.get_all(
			"Reminder",
			filters={"reference_doctype": "Work Task", "reference_name": name, "status": ["!=", "Completed"]},
			fields=["name", "responsible_user", "status"],
		)

	def test_with_the_person_assigned_then_the_approver_until_approved(self):
		name = self.raise_task()
		frappe.set_user("Administrator")
		reminders.run()
		self.assertEqual([r.responsible_user for r in self.reminder(name)], [self.operator])
		self.submit(name)
		frappe.set_user("Administrator")
		reminders.run()
		self.assertEqual([r.responsible_user for r in self.reminder(name)], [self.qa])
		self.sign(self.qa, name, "Return", reason="Redo")
		frappe.set_user("Administrator")
		reminders.run()
		self.assertEqual([r.responsible_user for r in self.reminder(name)], [self.operator])
		self.submit(name)
		self.sign(self.qa, name, "Approve")
		frappe.set_user("Administrator")
		reminders.run()
		self.assertEqual(self.reminder(name), [])
