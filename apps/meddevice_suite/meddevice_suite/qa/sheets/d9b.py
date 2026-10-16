"""What the browser test sheet of D9 Quality Records starts from for risk management, regulatory
licences and training (modules 39, 41 and 37; URS-QMS-017 to URS-QMS-025, URS-REG-001 to URS-REG-006,
URS-TRN-001 to URS-TRN-006), on a test site only (cypress/integration/sheet_d9b.js).

The sheet's tests act on the screens as QA, a second QA person, Management, the Production
Supervisor, the Training Coordinator and the operator. Each test starts from records of its own,
made here afresh through the code's own services, each step as the job that does it (`acting_as`),
so a test never depends on another having run:

- a risk management file of its own finished good, its first risk assessment authored by QA,
  reviewed by the Production Supervisor and approved by the second QA person, and, as far as the
  test needs, its report approved, an approved change request that lists the file (which raises a
  risk review) and a new revision QA started under it; a draft acceptability policy QA started;
- an important file holding a licence copy, and a licence QA verified against its copy, with the
  important files the test names as evidence and as a submission's copy; a change request a
  Production Supervisor raised and sent for assessment;
- a new work instruction issued through document control, put on the Machine Operator job's
  training matrix by QA's cancel, amend and signed approval, which opens the operator's training;
  the operator's competence on a journey operation's work instruction with a job card of that
  operation, and the training restored after the test revokes it.

The journey's masters (ui_journey.prepare_journey) are made first: its product, operations, the
operator trained on them, the policy in force and the second QA person. Only the Administrator who
runs the tests prepares the sheet.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.master_data import routing
from meddevice_suite.master_data.item_master import FINISHED_GOOD, ITEM_TYPE
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	LABELLING_TITLE,
	OPERATIONS,
	PRODUCT,
	PRODUCT_DETAILS,
	QUANTITY,
	QUARANTINE,
	RISK_TAG,
	acting_as,
	ensure_second_qa,
	store,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.qms import change_control, regulatory_licence, training
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.setup.fixtures.documents import ensure_work_instruction
from meddevice_suite.setup.fixtures.risk import (
	SECOND_QA,
	ensure_assessment,
	ensure_file,
	ensure_policy,
	ensure_report,
)
from meddevice_suite.setup.fixtures.training import OPERATOR_JOB, ensure_operator, train_operator
from meddevice_suite.work_management import reminders

QA, PRODUCTION, COORDINATOR, OPERATOR = (
	"QA Manager",
	"Production Supervisor",
	"Training Coordinator",
	"Operator",
)
REASON = "Browser test sheet D9"
TAG = "browser test D9"
# The documents a risk file names: its plan, the SOP that collects post-production information, the
# specification that controls its hazard, and the labelling an information-for-safety control is.
PLAN_TITLE = "Risk management plan (browser test)"
SOURCES_TITLE = "Post-market surveillance and complaints (browser test)"
SPECIFICATION_TITLE = "Balloon burst specification (browser test)"
# QA's list of licence types and the category QA keeps licence copies in.
LICENCE_TYPE = "Manufacturing licence (browser test D9)"
LICENCE_CATEGORY = "Licences (browser test D9)"
RISK_STAGES = ("in_force", "change_approved", "revision_in_progress")
LICENCE_STAGES = ("copy", "verified")
# How far a training is prepared, and how many months the matrix row keeps it valid.
TRAINING_VALIDITY = {
	"new_instruction": 0,
	"open": 12,
	"planned_without_assessor": 0,
	"history": 24,
	"history_refusals": 0,
}
# The reminders the sheet runs the daily job for, and the alert that tells QA of a licence's due.
REMINDER_CATEGORIES = ("Regulatory Licence", "Training Due")


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


def require_stage(stage: str, stages: tuple[str, ...]) -> None:
	if stage not in stages:
		frappe.throw(_("Unknown stage {0}.").format(stage))


def unique() -> str:
	return frappe.generate_hash(length=10)


# --- What every test works with --------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The second QA person, the policy in force, the operator, QA's licence type and copy category
	and the risk file's documents, after the journey's masters (ui_journey.prepare_journey); and what
	the tests work with."""
	require_administrator()
	require_password(password)
	second_qa = ensure_second_qa(password)
	ensure_policy(act_as, password, RISK_TAG)
	ensure_licence_masters()
	return {
		"second_qa": second_qa,
		"company": COMPANY,
		"product": PRODUCT,
		"operator": ensure_operator(COMPANY, email_for(OPERATOR)),
		"operator_job": OPERATOR_JOB,
		"licence_type": LICENCE_TYPE,
		"file_category": LICENCE_CATEGORY,
		"training_due_days": quality_settings.value("training_due_days"),
		"licence_warning_days": quality_settings.value("regulatory_licence_warning_days"),
		"documents": risk_documents(password),
	}


def risk_documents(password: str) -> dict[str, str]:
	"""Effective documents issued through document control: the plan and the post-production SOP a
	file and its report name, the specification its control is, and the product labelling."""
	return {
		"plan": ensure_work_instruction(PLAN_TITLE, act_as, email_for, password, "SOP"),
		"sop": ensure_work_instruction(SOURCES_TITLE, act_as, email_for, password, "SOP"),
		"specification": ensure_work_instruction(
			SPECIFICATION_TITLE, act_as, email_for, password, "Specification"
		),
		"labelling": ensure_work_instruction(
			LABELLING_TITLE, act_as, email_for, password, "Packaging and Labelling"
		),
	}


def ensure_licence_masters() -> None:
	"""QA's licence type that expires, and QA's category of licence copies."""
	with act_as(QA):
		if not frappe.db.exists("File Category", LICENCE_CATEGORY):
			frappe.get_doc({"doctype": "File Category", "category_name": LICENCE_CATEGORY}).insert()
		if not frappe.db.exists(regulatory_licence.TYPE_DOCTYPE, LICENCE_TYPE):
			frappe.get_doc(
				{
					"doctype": regulatory_licence.TYPE_DOCTYPE,
					"licence_type_name": LICENCE_TYPE,
					"has_expiry": 1,
				}
			).insert()


# --- Risk management (module 39) --------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_draft_policy(password: str) -> dict:
	"""A new acceptability policy QA started: the D-13 scales and every pair of the matrix listed,
	no acceptability decided yet."""
	require_administrator()
	require_password(password)
	with act_as(QA):
		policy = frappe.get_doc(
			{"doctype": rm.POLICY, "title": f"Risk acceptability policy ({TAG} {unique()})"}
		).insert()
	return {"name": policy.name}


@frappe.whitelist(methods=["POST"])
def prepare_risk_file(password: str, stage: str) -> dict:
	"""A risk management file of a finished good of its own, its first revision approved by three
	people (QA authors, the Production Supervisor reviews, the second QA person approves) and, by
	`stage`:

	- in_force: nothing more; the file has no report;
	- change_approved: its report approved, and an approved change request listing the file, which
	  raised an open risk review;
	- revision_in_progress: as change_approved, and the next revision QA started under it, a draft.
	"""
	require_administrator()
	require_password(password)
	require_stage(stage, RISK_STAGES)
	ensure_second_qa(password)  # the approver of what QA writes
	documents = risk_documents(password)
	tag = unique()
	item = new_finished_good(f"D9-RISK-{tag}", f"Foley catheter, risk sheet D9 {tag}")
	family = f"D9 Foley catheter ({TAG} {tag})"
	file = ensure_file(family, [item], documents["plan"], act_as, TAG)
	revision = ensure_assessment(file, documents["specification"], act_as, email_for, password, TAG)
	made = {"file": file, "family": family, "item": item, "revision": revision, **documents}
	if stage == "in_force":
		return made
	made["report"] = ensure_report(file, revision, documents["sop"], act_as, email_for, password, TAG)
	request = raise_change(
		f"Balloon wall thickness of {family} (browser test D9)", rm.FILE, file, "Product or BOM"
	)
	approve_change(request, password, impact_risk="Yes")
	made["change_request"] = request
	made["review"] = frappe.db.get_value(
		rm.REVIEW,
		{
			"risk_management_file": file,
			"source_doctype": rm.CHANGE_REQUEST,
			"source_name": request,
			"status": rm.OPEN,
		},
	)
	if stage == "revision_in_progress":
		with act_as(QA):
			made["draft"] = rm.start_revision(file, change_request=request)
	return made


def new_finished_good(code: str, name: str) -> str:
	"""A finished good of the test's own, for a file of its own: each is in one file only."""
	return (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": name,
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"gst_hsn_code": PRODUCT_DETAILS[1],  # India Compliance requires it on every item
				ITEM_TYPE: FINISHED_GOOD,
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)


def raise_change(title: str, record_type: str, record: str, change_type: str) -> str:
	"""A change request listing the record, raised by the Production Supervisor and sent for
	assessment (module 36)."""
	with act_as(PRODUCTION):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": title,
				"change_type": change_type,
				"description": f"{title}.",
				"reason": "Process capability study (browser test D9).",
				"affected_records": [{"reference_doctype": record_type, "reference_name": record}],
				"actions": [
					{
						"description": f"Carry out: {title}",
						"action_owner": email_for(PRODUCTION),
						"due_date": add_days(today(), 30),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	return request.name


def approve_change(name: str, password: str, impact_risk: str = "No") -> None:
	"""QA's signed assessment and approval of a minor change."""
	with act_as(QA):
		request = frappe.get_doc("Change Request", name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_risk": impact_risk,
				"impact_summary": "Within the validated range (browser test D9).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=f"{REASON}: assessed")
		change_control.approve(
			frappe.get_doc("Change Request", name), password=password, reason=f"{REASON}: approved"
		)


# --- Regulatory licences (module 41) ----------------------------------------------------------------


def new_copy(title: str) -> str:
	"""An important file QA uploaded into the licence category: a copy or a piece of evidence."""
	tag = unique()
	with act_as(QA):
		copy = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{frappe.scrub(title)}-{tag}.txt",
				"content": f"{title} {tag} (browser test D9)",
				"is_private": 1,
			}
		).insert()
		return (
			frappe.get_doc(
				{
					"doctype": "Important File",
					"title": f"{title} {tag}",
					"category": LICENCE_CATEGORY,
					"new_file": copy.file_url,
				}
			)
			.insert()
			.name
		)


@frappe.whitelist(methods=["POST"])
def prepare_licence(password: str, stage: str) -> dict:
	"""copy: a licence copy in the important files repository; verified: a licence of the plant QA
	verified against its copy with a signature, valid two years, and two more important files, the
	evidence of an obligation and the copy of a submission."""
	require_administrator()
	require_password(password)
	require_stage(stage, LICENCE_STAGES)
	ensure_licence_masters()
	made = {"copy": new_copy("Manufacturing licence copy")}
	if stage == "copy":
		return made
	number = f"MFG/MD-5/D9-{unique()}"
	with act_as(QA):
		licence = frappe.get_doc(
			{
				"doctype": regulatory_licence.DOCTYPE,
				"licence_type": LICENCE_TYPE,
				"licence_number": number,
				"issuing_authority": "State Licensing Authority",
				"valid_from": add_days(today(), -30),
				"valid_until": add_days(today(), 730),
				"important_file": made["copy"],
			}
		).insert()
		regulatory_licence.verify(licence, password=password, reason=f"{REASON}: checked against the copy")
	made.update(
		{
			"licence": licence.name,
			"number": number,
			"valid_until": str(frappe.db.get_value(regulatory_licence.DOCTYPE, licence.name, "valid_until")),
			"evidence": new_copy("Surveillance audit report"),
			"submission_copy": new_copy("Change notification"),
		}
	)
	return made


@frappe.whitelist(methods=["POST"])
def prepare_regulatory_change(password: str) -> dict:
	"""A verified licence, and a change request of a product of its own that the Production Supervisor
	raised and sent for assessment, not listing the licence."""
	require_administrator()
	require_password(password)
	licence = prepare_licence(password, "verified")
	tag = unique()
	item = new_finished_good(f"D9-REG-{tag}", f"Foley catheter, licence sheet D9 {tag}")
	request = raise_change(
		f"New sterilisation site for {item} (browser test D9)", "Item", item, "Product or BOM"
	)
	return {"change_request": request, "item": item, "licence": licence["licence"]}


@frappe.whitelist(methods=["POST"])
def run_daily_reminders(category: str, reference_doctype: str, reference_name: str) -> list[dict]:
	"""The daily run of one category of reminders (work_management.reminders.run), with the alert of
	its owning module first, as the scheduler runs them; the reminders of the record afterwards."""
	require_administrator()
	if category not in REMINDER_CATEGORIES:
		frappe.throw(_("The sheet runs no reminders of {0}.").format(category))
	kind = reminders.CATEGORIES[category]
	if kind.told_by:
		frappe.get_attr(kind.told_by)()
	reminders.track(category, kind.due)
	return frappe.get_all(
		reminders.DOCTYPE,
		filters={
			"category": category,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		fields=["name", "subject", "status", "due_date", "completion"],
		order_by="creation asc",
	)


# --- Training (module 37) ---------------------------------------------------------------------------


def approved_matrix() -> str | None:
	return frappe.db.get_value(training.MATRIX, {"designation": OPERATOR_JOB, "docstatus": 1})


def put_on_matrix(rows: dict[str, dict], password: str) -> str:
	"""QA adds the documents to the Machine Operator job's training matrix, each with what its row
	says (reading enough, months valid): the approved matrix is cancelled and amended and the
	amendment approved with a signature (plan D-17), which opens the job's training."""
	current = approved_matrix()
	with act_as(QA):
		if current:
			old = frappe.get_doc(training.MATRIX, current)
			old.flags.audit_reason = f"{REASON}: documents added"  # the audit trail's question
			old.cancel()
			matrix = frappe.copy_doc(old)
			matrix.docstatus = 0  # the amendment is a new draft of the cancelled matrix
			matrix.amended_from = old.name
			matrix.flags.audit_reason = f"{REASON}: documents added"
		else:
			matrix = frappe.get_doc({"doctype": training.MATRIX, "designation": OPERATOR_JOB})
		for document, values in rows.items():
			matrix.append("items", {"controlled_document": document, **values})
		matrix.insert()
		training.approve_matrix(matrix, password=password, reason=f"{REASON}: documents added")
	return matrix.name


def new_instruction(label: str, password: str) -> str:
	"""A new work instruction issued through document control, effective."""
	return ensure_work_instruction(f"Work instruction: D9 {label} {unique()}", act_as, email_for, password)


def revision_of(document: str) -> frappe._dict:
	"""The document's revision in effect: its name and its number."""
	name = frappe.db.get_value("Controlled Document", document, "current_revision")
	return frappe._dict(name=name, number=frappe.db.get_value("Document Revision", name, "revision"))


def ensure_qa_employee() -> str:
	"""The QA Manager's own employee record, without the user permission ERPNext would add, which
	would let the QA Manager see no other employee."""
	user = email_for(QA)
	if employee := frappe.db.get_value("Employee", {"user_id": user}):
		return employee
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": "QA",
				"last_name": "Manager (browser test)",
				"gender": "Female",
				"date_of_birth": "1988-03-09",
				"date_of_joining": add_days(today(), -730),
				"company": COMPANY,
				"status": "Active",
				"user_id": user,
				"create_user_permission": 0,
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.name
	)


@frappe.whitelist(methods=["POST"])
def prepare_training(password: str, stage: str) -> dict:
	"""A new work instruction for the operator's job and, by `stage`:

	- new_instruction: nothing more; the instruction is on no matrix yet;
	- open: on the job's matrix (valid 12 months), the operator's training opened by the system, its
	  Training Due reminder raised by the daily run;
	- planned_without_assessor: on the matrix (reading not enough), the training opened and planned by
	  the Training Coordinator, on the job, today, with no assessor;
	- history: on the matrix (valid 24 months), the training opened by the system, for QA to record
	  from paper;
	- history_refusals: on the matrix, plus an effective document on no matrix and the QA Manager's own
	  employee record.
	"""
	require_administrator()
	require_password(password)
	require_stage(stage, tuple(TRAINING_VALIDITY))
	operator = ensure_operator(COMPANY, email_for(OPERATOR))
	instruction = new_instruction(stage, password)
	revision = revision_of(instruction)
	made = {
		"operator": operator,
		"instruction": instruction,
		"revision": revision.name,
		"revision_no": revision.number,
		"due_days": quality_settings.value("training_due_days"),
	}
	if stage == "new_instruction":
		made["matrix"] = approved_matrix()
		return made
	made["matrix"] = put_on_matrix(
		{instruction: {"read_and_understand_allowed": 0, "validity_months": TRAINING_VALIDITY[stage]}},
		password,
	)
	training.reconcile([operator])
	made["record"] = frappe.db.get_value(
		training.RECORD,
		{"employee": operator, "document_revision": revision.name, "status": training.OPEN, "docstatus": 0},
	)
	if stage == "open":
		made["reminders"] = run_daily_reminders("Training Due", training.RECORD, made["record"])
	elif stage == "planned_without_assessor":
		with act_as(COORDINATOR):
			record = frappe.get_doc(training.RECORD, made["record"])
			record.update(
				{
					"training_method": "On-the-job",
					"training_date": today(),
					"trainer": "Production Supervisor (browser test D9)",
				}
			)
			record.save()
	elif stage == "history_refusals":
		made["outside"] = new_instruction("outside the matrix", password)
		made["qa_employee"] = ensure_qa_employee()
	return made


@frappe.whitelist(methods=["POST"])
def prepare_revocation(password: str) -> dict:
	"""The operator's competence on the work instruction of the journey's first operation, and a job
	card of that operation: a work order of the journey's product the Production Supervisor raised
	and submitted, which made a job card per operation."""
	require_administrator()
	require_password(password)
	operator = ensure_operator(COMPANY, email_for(OPERATOR))
	operation = OPERATIONS[0][0]
	instruction = frappe.db.get_value("Operation", operation, routing.WORK_INSTRUCTION)
	revision = revision_of(instruction)
	if not competence_of(operator, revision.name):
		restore_training(password)  # an earlier run's revocation, not yet retrained
	record = competence_of(operator, revision.name)
	if not record:
		frappe.throw(
			_("{0} is not competent in {1}; prepare the journey first.").format(operator, instruction)
		)
	with act_as(PRODUCTION):
		order = frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": PRODUCT,
				"bom_no": frappe.db.get_value("BOM", {"item": PRODUCT, "is_default": 1, "docstatus": 1}),
				"qty": QUANTITY,
				"company": COMPANY,
				"planned_start_date": now_datetime(),
				"source_warehouse": store("Stores"),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store(QUARANTINE),  # the finished lot waits there for QA
			}
		)
		# The routing's operations and the bill's materials, as the form brings them when the
		# product is chosen: without operations no job card is made.
		order.get_items_and_operations_from_bom()
		order.insert()
		order.submit()
	job_card = frappe.db.get_value("Job Card", {"work_order": order.name, "operation": operation})
	if not job_card:
		frappe.throw(_("Work order {0} made no job card for {1}.").format(order.name, operation))
	return {
		"operator": operator,
		"operation": operation,
		"instruction": instruction,
		"revision": revision.name,
		"revision_no": revision.number,
		"record": record,
		"work_order": order.name,
		"job_card": job_card,
	}


def competence_of(employee: str, revision: str) -> str | None:
	return frappe.db.get_value(
		training.RECORD,
		{
			"employee": employee,
			"document_revision": revision,
			"docstatus": 1,
			"status": training.COMPETENT,
		},
	)


def restore_training(password: str) -> str:
	"""The operator trained again on the journey's operations, as the journey trains them: what the
	sheet revoked is retrained and signed competent."""
	return train_operator(
		COMPANY,
		[operation for operation, _workstation, _minutes in OPERATIONS],
		act_as,
		email_for,
		password,
	)


@frappe.whitelist(methods=["POST"])
def restore_operator_training(password: str) -> str:
	"""After the sheet revokes the operator's competence: the operator competent again on every
	operation of the journey, for the tests that log work on them."""
	require_administrator()
	require_password(password)
	return restore_training(password)


# Each set-up the sheet calls, for the server tests to check they are all covered (test_sheet_d9b).
SET_UPS: tuple[Callable, ...] = (
	prepare_sheet,
	prepare_draft_policy,
	prepare_risk_file,
	prepare_licence,
	prepare_regulatory_change,
	run_daily_reminders,
	prepare_training,
	prepare_revocation,
	restore_operator_training,
)
