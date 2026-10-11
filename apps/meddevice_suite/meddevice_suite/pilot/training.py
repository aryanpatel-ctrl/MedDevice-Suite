"""The demo operator, trained on the work instructions they work to (module 37), for the pilot
site and the browser journey's test site.

Made as the system makes it: the Training Coordinator records the Operator job's employee, QA
approves the job's training matrix with a signature, the system opens a training record on each
work instruction, the Training Coordinator plans each training, the operator signs that they
were trained and the Production Supervisor signs them competent. Everything is made only if
missing, so a later run adds what a new operation needs.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe.utils import add_days, today

from meddevice_suite.master_data import routing
from meddevice_suite.qms import training

OPERATOR_JOB = "Machine Operator"
REASON = "Trained for the demo"
AMENDED = "Work instructions added for the demo"


def ensure_operator(company: str, user: str) -> str:
	"""The employee of the Operator job's user, in the Machine Operator job."""
	if not frappe.db.exists("Designation", OPERATOR_JOB):
		frappe.get_doc({"doctype": "Designation", "designation_name": OPERATOR_JOB}).insert(
			ignore_permissions=True  # demo loader
		)
	employee = frappe.db.get_value("Employee", {"user_id": user})
	if employee:
		return employee
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": "Demo",
				"last_name": "Operator",
				"gender": "Female",
				"date_of_birth": "1994-05-12",
				"date_of_joining": add_days(today(), -365),
				"company": company,
				"status": "Active",
				"designation": OPERATOR_JOB,
				"user_id": user,
			}
		)
		.insert(ignore_permissions=True)  # demo loader
		.name
	)


def ensure_matrix(
	instructions: set[str], act_as: Callable[[str], AbstractContextManager], password: str
) -> None:
	"""The Machine Operator job's matrix names every one of these work instructions, approved by
	QA; a missing one is added by cancel and amend, approved again."""
	current = frappe.db.get_value("Training Matrix", {"designation": OPERATOR_JOB, "docstatus": 1})
	listed = (
		set(frappe.get_all("Training Matrix Item", filters={"parent": current}, pluck="controlled_document"))
		if current
		else set()
	)
	if instructions <= listed:
		return
	with act_as("QA Manager"):
		if current:
			old = frappe.get_doc("Training Matrix", current)
			old.flags.audit_reason = AMENDED  # the audit trail's question
			old.cancel()
			matrix = frappe.copy_doc(old)
			matrix.docstatus = 0  # the amendment is a new draft of the cancelled matrix
			matrix.amended_from = old.name
			matrix.flags.audit_reason = AMENDED
		else:
			matrix = frappe.get_doc({"doctype": "Training Matrix", "designation": OPERATOR_JOB})
		matrix.set("items", [{"controlled_document": d} for d in sorted(instructions | listed)])
		matrix.insert()
		training.approve_matrix(matrix, password=password, reason=REASON)


def train_operator(
	company: str,
	operations: list[str],
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
) -> str:
	"""The Operator job's employee, competent on the work instructions of `operations`."""
	employee = ensure_operator(company, user_of("Operator"))
	instructions = set(
		frappe.get_all("Operation", filters={"name": ["in", operations]}, pluck=routing.WORK_INSTRUCTION)
	) - {None, ""}
	ensure_matrix(instructions, act_as, password)
	training.reconcile([employee])
	for name in frappe.get_all(
		training.RECORD,
		filters={
			"employee": employee,
			"docstatus": 0,
			"status": training.OPEN,
			"controlled_document": ["in", sorted(instructions)],
		},
		pluck="name",
	):
		with act_as("Training Coordinator"):
			record = frappe.get_doc(training.RECORD, name)
			record.update(
				{
					"training_method": "On-the-job",
					"training_date": today(),
					"trainer": "Production Supervisor (demo)",
					"assessor": user_of("Production Supervisor"),
					"assessment_method": "Practical Demonstration",
				}
			)
			record.save()
		with act_as("Operator"):
			training.sign_trained(frappe.get_doc(training.RECORD, name), password=password, reason=REASON)
		with act_as("Production Supervisor"):
			training.sign_competent(frappe.get_doc(training.RECORD, name), password=password, reason=REASON)
	return employee
