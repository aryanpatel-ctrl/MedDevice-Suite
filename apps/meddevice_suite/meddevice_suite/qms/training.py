"""Training and competence (module 37; URS-TRN-001 to URS-TRN-006; D2 "Training"; ISO 13485 6.2;
21 CFR 11.10(i)). Built on ERPNext's Employee and Designation; HRMS is not used (decided 2026-10-11).

A Training Matrix says, for a job (a designation), which controlled documents its people are
trained on, whether reading one is enough, and how long the training stays valid. QA approves it
with a signature; a change is a cancel and amend, approved again (plan decision D-17).

The system keeps one training record open for each active employee and each document their job
needs, on the revision in effect and on a revision approved to take effect later, so people are
trained before it takes effect (D-07). A new revision, a new job, a new matrix row, a leaver, a
training that expires within the warning days, a Not Competent assessment and a revoked competence
all come down to one rule, `reconcile`: what is needed and not covered is opened, what is open
and no longer needed is closed. It runs when any of those happens and again every day, so a
failure on the way is repaired the next day and never holds back the event that caused it.

A record is completed only by signatures (D-08 to D-10): the trainee, on their own account, signs
that they were trained (or read and understood a document whose matrix row allows reading); then
an assessor (the Production Supervisor or QA, never the trainee) signs Competent or Not Competent.
What was planned for the training (method, date, trainer, assessor, assessment method) is fixed
when the trainee signs. QA enters paper history from before the system with its certificate and a
signature, and may revoke a competence with a signed reason (D-18). QA starts such a record from
the Training Record list (`start_history`), for an employee and a document their job's approved
matrix names; what the record holds beyond the training itself (why it exists, its validity,
whether reading is enough) is set by the system there too, never typed. Records are never deleted.

At the workstation, every operator logging time on a job card is competent, on the revision in
effect, in the work instruction of the operation they do (URS-TRN-005); block, never warn.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import routing

RECORD, MATRIX = "Training Record", "Training Matrix"
QA_ROLE = "Quality Manager"
COORDINATOR_ROLE = "HR User"
# Who may assess (D-09): the Production Supervisor's role, and QA.
ASSESSOR_ROLES = frozenset({"Manufacturing User", QA_ROLE})
OPEN, TRAINED, COMPETENT, NOT_COMPETENT = "Open", "Trained", "Competent", "Not Competent"
REVOKED, SUPERSEDED, WITHDRAWN = "Revoked", "Superseded", "Withdrawn"
IN_PROGRESS = (OPEN, TRAINED)
NEW_ASSIGNMENT, NEW_REVISION, REFRESHER, RETRAINING, HISTORICAL = (
	"New Assignment",
	"New Revision",
	"Refresher",
	"Retraining",
	"Historical",
)
READ_AND_UNDERSTAND = "Read and Understand"
ACTIVE = "Active"
RECONCILE_SAVEPOINT = "training_reconcile"
# What the trainee plans and signs for: fixed once trained.
PLANNED = ("training_method", "training_date", "trainer", "assessor", "assessment_method", "certificate")
# What the system sets when it opens a record: what it is for, and what the job's matrix says of it.
OPENED_WITH = ("employee", "controlled_document", "trigger", "read_and_understand_allowed", "validity_months")
# What only a signature or the system fills in.
OUTCOME = (
	"trained_by",
	"trained_on",
	"assessed_by",
	"assessed_on",
	"valid_until",
	"is_historical",
	"outcome_reason",
	"revoked_by",
	"revoked_on",
)


def require(condition, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


def roles_of(user: str) -> set[str]:
	return set(frappe.get_roles(user))


# --- What is needed, and what covers it ---------------------------------------------------------


def needed(employees: list[str] | None = None) -> dict[str, set[str]]:
	"""The documents each active employee's job needs, by employee, from the approved matrices."""
	filters = {"status": ACTIVE, "designation": ["is", "set"]}
	if employees is not None:
		if not employees:
			return {}
		filters["name"] = ["in", employees]
	staff = frappe.get_all("Employee", filters=filters, fields=["name", "designation"])
	matrix, row = frappe.qb.DocType(MATRIX), frappe.qb.DocType("Training Matrix Item")
	documents: dict[str, set[str]] = {}
	designations = sorted({e.designation for e in staff})
	if designations:
		for line in (
			frappe.qb.from_(matrix)
			.join(row)
			.on(row.parent == matrix.name)
			.select(matrix.designation, row.controlled_document)
			.where((matrix.docstatus == 1) & matrix.designation.isin(designations))
			.run(as_dict=True)
		):
			documents.setdefault(line.designation, set()).add(line.controlled_document)
	return {e.name: documents.get(e.designation, set()) for e in staff}


def matrix_rows(designations: list[str]) -> dict[tuple[str, str], frappe._dict]:
	"""The approved matrix row of each (designation, document)."""
	if not designations:
		return {}
	matrix, row = frappe.qb.DocType(MATRIX), frappe.qb.DocType("Training Matrix Item")
	return {
		(line.designation, line.controlled_document): line
		for line in frappe.qb.from_(matrix)
		.join(row)
		.on(row.parent == matrix.name)
		.select(
			matrix.designation, row.controlled_document, row.read_and_understand_allowed, row.validity_months
		)
		.where((matrix.docstatus == 1) & matrix.designation.isin(designations))
		.run(as_dict=True)
	}


def revisions_to_train(documents: set[str]) -> dict[str, list[frappe._dict]]:
	"""Per document, the revision in effect and any approved to take effect later."""
	if not documents:
		return {}
	found: dict[str, list[frappe._dict]] = {}
	for rev in frappe.get_all(
		"Document Revision",
		filters={
			"controlled_document": ["in", sorted(documents)],
			"status": ["in", ("Effective", "Approved")],
			"docstatus": 1,
		},
		fields=["name", "controlled_document", "status", "effective_date"],
		order_by="effective_date asc",
	):
		found.setdefault(rev.controlled_document, []).append(rev)
	withdrawn = set(
		frappe.get_all(
			"Controlled Document",
			filters={"name": ["in", sorted(documents)], "status": "Obsolete"},
			pluck="name",
		)
	)
	return {doc: revs for doc, revs in found.items() if doc not in withdrawn}


def reconcile(employees: list[str] | None = None) -> None:
	"""Open what is needed and not covered; close what is open and no longer needed. Idempotent;
	`employees=None` is everyone (the daily run)."""
	if employees is not None:
		employees = sorted(set(employees) - {None, ""})
		if not employees:
			return
		# One reconciliation of an employee at a time, so a record is never opened twice.
		staff = frappe.qb.DocType("Employee")
		frappe.qb.from_(staff).select(staff.name).where(staff.name.isin(employees)).for_update().run()
	need = needed(employees)
	record_filters = {"docstatus": ["<", 2]}
	if employees is not None:
		record_filters["employee"] = ["in", employees]
	records = frappe.get_all(
		RECORD,
		filters=record_filters,
		fields=[
			"name",
			"employee",
			"controlled_document",
			"document_revision",
			"status",
			"docstatus",
			"valid_until",
			"trigger",
			"creation",
		],
		order_by="creation asc",
	)
	revisions = revisions_to_train(
		{d for docs in need.values() for d in docs} | {r.controlled_document for r in records}
	)
	designation = (
		dict(
			frappe.get_all(
				"Employee", filters={"name": ["in", list(need)]}, fields=["name", "designation"], as_list=True
			)
		)
		if need
		else {}
	)
	rows = matrix_rows(sorted(set(designation.values())))
	warning = quality_settings.value("training_expiry_warning_days")
	due_days = quality_settings.value("training_due_days")
	horizon = getdate(add_days(today(), warning))
	by_pair: dict[tuple[str, str], list[frappe._dict]] = {}
	for rec in records:
		by_pair.setdefault((rec.employee, rec.document_revision), []).append(rec)

	for employee, documents in need.items():
		for document in sorted(documents):
			for rev in revisions.get(document, []):
				held = by_pair.get((employee, rev.name), [])
				# A paper history QA has started is not training until signed: left unsigned, or
				# started for the wrong person, it must not hold back the training the job needs.
				if any(
					r.docstatus == 0 and r.status in IN_PROGRESS and r.trigger != HISTORICAL for r in held
				):
					continue
				competent = [r for r in held if r.docstatus == 1 and r.status == COMPETENT]
				if any(not r.valid_until or getdate(r.valid_until) > horizon for r in competent):
					continue
				row = rows.get((designation.get(employee), document)) or frappe._dict()
				if competent:
					trigger, due = REFRESHER, max(getdate(r.valid_until) for r in competent)
				elif held and held[-1].status in (NOT_COMPETENT, REVOKED):
					trigger, due = RETRAINING, add_days(today(), due_days)
				elif rev.status == "Approved":  # to take effect: trained by then (D-07)
					trigger, due = NEW_REVISION, max(getdate(today()), getdate(rev.effective_date))
				elif any(
					r.controlled_document == document and r.employee == employee and r.docstatus == 1
					for r in records
				):
					trigger, due = NEW_REVISION, add_days(today(), due_days)
				else:
					trigger, due = NEW_ASSIGNMENT, add_days(today(), due_days)
				open_record(employee, document, rev.name, trigger, due, row)

	for rec in records:
		if rec.docstatus != 0 or rec.status not in IN_PROGRESS or rec.trigger == HISTORICAL:
			continue
		if rec.employee not in need:  # left, inactive, or without a job
			close(rec.name, WITHDRAWN, _("The employee is no longer active or has no job."))
		elif rec.document_revision not in {r.name for r in revisions.get(rec.controlled_document, [])}:
			close(rec.name, SUPERSEDED, _("The revision is no longer in effect or to take effect."))
		elif rec.controlled_document not in need.get(rec.employee, set()):
			close(rec.name, WITHDRAWN, _("The employee's job no longer needs this document."))


def open_record(employee: str, document: str, revision: str, trigger: str, due, row: frappe._dict) -> None:
	doc = frappe.get_doc(
		{
			"doctype": RECORD,
			"employee": employee,
			"controlled_document": document,
			"document_revision": revision,
			"trigger": trigger,
			"due_date": due,
			"read_and_understand_allowed": row.get("read_and_understand_allowed") or 0,
			"validity_months": row.get("validity_months") or 0,
			"status": OPEN,
		}
	)
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # opened by the system for whoever's change caused it
	doc.insert()


def close(name: str, status: str, reason: str) -> None:
	doc = frappe.get_doc(RECORD, name)
	doc.status, doc.outcome_reason = status, reason
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # closed by the system: the need it answered has gone
	doc.save()


def reconcile_safely(employees: list[str] | None, what: str) -> None:
	"""Reconcile without holding back the change that called it; the daily run repairs a failure."""
	frappe.db.savepoint(RECONCILE_SAVEPOINT)
	try:
		reconcile(employees)
	except Exception:
		frappe.db.rollback(save_point=RECONCILE_SAVEPOINT)
		frappe.log_error(title=_("Training needs not updated after {0}").format(what))


def employees_needing(document: str) -> list[str]:
	designations = frappe.get_all(
		"Training Matrix Item", filters={"controlled_document": document, "docstatus": 1}, pluck="parent"
	)
	if not designations:
		return []
	jobs = frappe.get_all(MATRIX, filters={"name": ["in", designations], "docstatus": 1}, pluck="designation")
	return (
		frappe.get_all("Employee", filters={"designation": ["in", jobs or [""]]}, pluck="name")
		if jobs
		else []
	)


def on_document_change(document: str) -> None:
	"""A revision approved or taking effect, or the document withdrawn (module 30)."""
	affected = employees_needing(document)
	affected += frappe.get_all(
		RECORD, filters={"controlled_document": document, "docstatus": 0}, pluck="employee", distinct=True
	)
	if affected:
		reconcile_safely(affected, _("document {0}").format(document))


def on_employee_update(doc: Document, method: str | None = None) -> None:
	"""Employee on_update: joining, a new job, leaving or returning."""
	before = doc.get_doc_before_save()
	if before is None or (before.status, before.designation) != (doc.status, doc.designation):
		reconcile_safely([doc.name], _("employee {0}").format(doc.name))


def daily() -> None:
	reconcile_safely(None, _("the daily run"))


# --- The matrix ----------------------------------------------------------------------------------


def validate_matrix(doc: Document, method: str | None = None) -> None:
	if doc.get("_action") == "submit" and not doc.flags.training:
		frappe.throw(_("A training matrix is approved by QA's signature."), frappe.PermissionError)
	require(doc.items, _("Name the documents this job is trained on."))
	seen = set()
	for row in doc.items:
		require(
			row.controlled_document not in seen,
			_("Row {0}: {1} is listed twice.").format(row.idx, row.controlled_document),
		)
		seen.add(row.controlled_document)
		require(
			int(row.validity_months or 0) >= 0, _("Row {0}: the validity cannot be negative.").format(row.idx)
		)
	obsolete = frappe.get_all(
		"Controlled Document", filters={"name": ["in", sorted(seen)], "status": "Obsolete"}, pluck="name"
	)
	require(not obsolete, _("{0} has been withdrawn.").format(", ".join(obsolete)))


def approve_matrix(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA approves the job's training matrix (D-17)."""
	require(doc.docstatus == 0, _("{0} is approved already.").format(doc.name))
	require(
		QA_ROLE in roles_of(frappe.session.user),
		_("Only QA approves a training matrix."),
		frappe.PermissionError,
	)
	other = frappe.db.get_value(
		MATRIX, {"designation": doc.designation, "docstatus": 1, "name": ["!=", doc.name]}
	)
	require(
		not other, _("{0} is the approved matrix of {1}; cancel it first.").format(other, doc.designation)
	)
	doc.approved_by, doc.approved_at = frappe.session.user, now_datetime()
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # the signed approval is the only submit
	doc.submit()
	reconcile_safely(
		frappe.get_all("Employee", filters={"designation": doc.designation}, pluck="name"),
		_("matrix {0}").format(doc.name),
	)


def on_matrix_cancel(doc: Document, method: str | None = None) -> None:
	reconcile_safely(
		frappe.get_all("Employee", filters={"designation": doc.designation}, pluck="name"),
		_("matrix {0}").format(doc.name),
	)


# --- The record ----------------------------------------------------------------------------------


def before_insert(doc: Document, method: str | None = None) -> None:
	"""Records are opened by the system; by hand, only QA enters paper history (D-07), on a document
	the employee's job is trained on. Every way of entering history (`start_history`, the form, the
	API) comes through here, so each keeps the same rules."""
	if doc.flags.training:
		return
	require(
		QA_ROLE in roles_of(frappe.session.user),
		_("Training records are opened by the system."),
		frappe.PermissionError,
	)
	require(
		doc.trigger == HISTORICAL, _("By hand, QA enters only training done before the system (Historical).")
	)
	staff = doc.employee and frappe.db.get_value(
		"Employee", doc.employee, ["designation", "user_id"], as_dict=True
	)
	require(staff, _("Employee {0} does not exist.").format(doc.employee))
	require(
		frappe.session.user != staff.user_id,
		_("Nobody records their own training history."),
		frappe.PermissionError,
	)
	# The validity and whether reading is enough come from the job's matrix, as on a record the
	# system opens: typed by hand, they could make a paper training last for ever.
	row = staff.designation and matrix_rows([staff.designation]).get(
		(staff.designation, doc.controlled_document)
	)
	require(
		row,
		_(
			"History is entered for what the job is trained on: {0}'s approved matrix does not name {1}."
		).format(doc.employee, doc.controlled_document),
	)
	doc.read_and_understand_allowed = row.read_and_understand_allowed or 0
	doc.validity_months = row.validity_months or 0
	for field in OUTCOME:
		doc.set(field, None)
	doc.status = OPEN
	doc.due_date = doc.due_date or today()  # nothing falls due: the training was given long ago


@frappe.whitelist(methods=["POST"])
def start_history(employee: str, controlled_document: str) -> str:
	"""QA starts the record of a training given on paper before the system (D-07), for an employee
	and a document their job's approved matrix names, on the revision in effect: the revision
	the workstation checks (URS-TRN-005). QA then records how and when it was given, attaches the
	certificate and signs Record History. Returns the draft record's name."""
	frappe.has_permission(RECORD, "create", throw=True)
	require(
		QA_ROLE in roles_of(frappe.session.user),
		_("Only QA records training history."),
		frappe.PermissionError,
	)
	revision = frappe.db.get_value(
		"Controlled Document", {"name": controlled_document, "status": "Effective"}, "current_revision"
	)
	require(revision, _("{0} has no revision in effect to record training on.").format(controlled_document))
	# One start at a time for an employee, as in `reconcile`, so the same history is not started twice.
	staff = frappe.qb.DocType("Employee")
	frappe.qb.from_(staff).select(staff.name).where(staff.name == employee).for_update().run()
	for held in frappe.get_all(
		RECORD,
		filters={"employee": employee, "document_revision": revision, "docstatus": ["<", 2]},
		fields=["name", "trigger", "status", "docstatus", "valid_until"],
	):
		if held.docstatus == 0 and held.trigger == HISTORICAL and held.status == OPEN:
			frappe.throw(_("{0} already records this training history; complete it.").format(held.name))
		if (
			held.docstatus == 1
			and held.status == COMPETENT
			and (not held.valid_until or getdate(held.valid_until) >= getdate(today()))
		):
			frappe.throw(_("{0} is already competent on this revision ({1}).").format(employee, held.name))
	doc = frappe.get_doc(
		{
			"doctype": RECORD,
			"employee": employee,
			"controlled_document": controlled_document,
			"document_revision": revision,
			"trigger": HISTORICAL,
		}
	)
	doc.insert()  # as QA: `before_insert` holds the rules of every hand-entered record
	return doc.name


def validate_record(doc: Document, method: str | None = None) -> None:
	"""before_validate and validate: what a record may hold, and who changes what."""
	if doc.get("_action") == "submit" and not doc.flags.training:
		frappe.throw(_("A training record is completed by its signatures."), frappe.PermissionError)
	if doc.docstatus != 0:
		return
	rev = frappe.db.get_value(
		"Document Revision",
		doc.document_revision,
		["controlled_document", "revision", "status"],
		as_dict=True,
	)
	require(
		rev and rev.controlled_document == doc.controlled_document,
		_("The revision is not of {0}.").format(doc.controlled_document),
	)
	doc.revision = rev.revision
	employee = frappe.db.get_value(
		"Employee", doc.employee, ["employee_name", "designation", "user_id"], as_dict=True
	)
	require(employee, _("Employee {0} does not exist.").format(doc.employee))
	doc.employee_name, doc.designation = employee.employee_name, employee.designation
	doc.trainee_user = employee.user_id
	before = None if doc.is_new() else doc.get_doc_before_save()
	if before and not doc.flags.training:
		# A record stays what it was opened for: a coordinator who could turn an open record into
		# Historical would take it out of `reconcile` and let QA sign it complete without training.
		opened = [f for f in OPENED_WITH if (before.get(f) or None) != (doc.get(f) or None)]
		require(
			not opened,
			_("Set when the record was opened: {0}.").format(
				", ".join(_(doc.meta.get_label(f)) for f in opened)
			),
			frappe.PermissionError,
		)
		require(
			before.status == doc.status, _("The status changes only by a signature."), frappe.PermissionError
		)
		if before.status != OPEN:
			changed = [f for f in PLANNED if (before.get(f) or None) != (doc.get(f) or None)]
			require(
				not changed,
				_("Fixed once the trainee signed: {0}.").format(
					", ".join(_(doc.meta.get_label(f)) for f in changed)
				),
				frappe.PermissionError,
			)
	if doc.training_method == READ_AND_UNDERSTAND:
		require(
			doc.read_and_understand_allowed or doc.trigger == HISTORICAL,
			_("Reading is not enough for {0} in this job: it needs training and an assessment.").format(
				doc.controlled_document
			),
		)
	if doc.assessor:
		require(doc.assessor != doc.trainee_user, _("The trainee does not assess their own training."))
		require(
			ASSESSOR_ROLES & roles_of(doc.assessor),
			_("{0} cannot assess: the Production Supervisor or QA assesses.").format(doc.assessor),
		)
	if doc.training_date:
		require(
			getdate(doc.training_date) <= getdate(today()), _("The training date cannot be in the future.")
		)


def guard_after_completion(doc: Document, method: str | None = None) -> None:
	require(doc.flags.training, _("A completed training record does not change."), frappe.PermissionError)


def refuse_delete(doc: Document, method: str | None = None) -> None:
	frappe.throw(_("Training records are never deleted."), frappe.PermissionError)


def valid_until(doc: Document, start) -> str | None:
	months = int(doc.validity_months or 0)
	return add_months(getdate(start), months) if months else None


def complete(doc: Document, status: str) -> None:
	doc.status = status
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # the signatures are the only submit
	doc.submit()
	reconcile_safely([doc.employee], _("record {0}").format(doc.name))


def sign_trained(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the trainee, on their own account, signs that they were trained, or read and
	understood a document their job allows reading for (D-08, D-10)."""
	require(doc.docstatus == 0 and doc.status == OPEN, _("{0} is not open.").format(doc.name))
	require(doc.trigger != HISTORICAL, _("QA records history; the trainee does not sign it."))
	require(
		doc.trainee_user, _("{0} has no user account to sign with.").format(doc.employee_name or doc.employee)
	)
	require(
		frappe.session.user == doc.trainee_user,
		_("Only the trainee signs that they were trained."),
		frappe.PermissionError,
	)
	require(
		frappe.db.get_value("Employee", doc.employee, "status") == ACTIVE,
		_("{0} is not active.").format(doc.employee),
	)
	require(doc.training_method and doc.training_date, _("Record how and when the training was given."))
	reading = doc.training_method == READ_AND_UNDERSTAND
	if not reading:
		require(
			doc.assessor and doc.assessment_method,
			_("Name the assessor and how they will assess, before signing."),
		)
	validate_record(doc)
	doc.trained_by, doc.trained_on = frappe.session.user, now_datetime()
	if reading:
		doc.valid_until = valid_until(doc, doc.training_date)
		sign(doc, "Trained", "Trained", password, reason, permission="read")
		complete(doc, COMPETENT)
		return
	doc.status = TRAINED
	sign(doc, "Trained", "Trained", password, reason, permission="read")
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # the trainee signs; the coordinator keeps the record
	doc.save()


def assess(doc: Document, status: str, meaning: str, action: str, password: str, reason: str | None) -> None:
	require(
		doc.docstatus == 0 and doc.status == TRAINED,
		_("{0} is assessed once the trainee has signed.").format(doc.name),
	)
	require(
		frappe.session.user == doc.assessor, _("Only the named assessor assesses."), frappe.PermissionError
	)
	# Judged as things stand now: the employee's account and the assessor's roles may have changed
	# since the training was planned.
	require(
		frappe.session.user
		not in (doc.trainee_user, frappe.db.get_value("Employee", doc.employee, "user_id")),
		_("The trainee does not assess their own training."),
		frappe.PermissionError,
	)
	require(
		ASSESSOR_ROLES & roles_of(frappe.session.user),
		_("The Production Supervisor or QA assesses."),
		frappe.PermissionError,
	)
	require(
		frappe.db.get_value("Document Revision", doc.document_revision, "status")
		in ("Approved", "Effective"),
		_("{0} is no longer in effect; train on the revision in effect.").format(doc.revision),
	)
	doc.assessed_by, doc.assessed_on = frappe.session.user, now_datetime()
	doc.valid_until = valid_until(doc, today()) if status == COMPETENT else None
	if status == NOT_COMPETENT:
		require(reason, _("Say what the trainee could not yet do."))
		doc.outcome_reason = reason
	sign(doc, meaning, action, password, reason, permission="read")
	complete(doc, status)


def sign_competent(doc: Document, password: str, reason: str | None = None) -> None:
	assess(doc, COMPETENT, "Competent", "Competent", password, reason)


def sign_not_competent(doc: Document, password: str, reason: str | None = None) -> None:
	assess(doc, NOT_COMPETENT, "Rejected", "Not Competent", password, reason)


def record_history(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA enters training done on paper before the system, with its certificate."""
	require(
		doc.docstatus == 0 and doc.trigger == HISTORICAL,
		_("{0} is not a historical record.").format(doc.name),
	)
	require(
		QA_ROLE in roles_of(frappe.session.user),
		_("Only QA records training history."),
		frappe.PermissionError,
	)
	require(
		frappe.session.user != doc.trainee_user,
		_("Nobody records their own training history."),
		frappe.PermissionError,
	)
	require(doc.training_date and doc.training_method, _("Record how and when the training was given."))
	require(
		doc.certificate and doc.certificate.startswith("/private/files/"),
		_("Attach the training certificate, privately."),
	)
	validate_record(doc)
	doc.is_historical = 1
	doc.valid_until = valid_until(doc, doc.training_date)
	doc.assessed_by, doc.assessed_on = frappe.session.user, now_datetime()
	sign(doc, "Verified", "Record History", password, reason)
	complete(doc, COMPETENT)
	# The training the system opened on this revision, before the history was entered (at go-live,
	# say), is no longer needed: closed, so the trainee is not asked to sign for it.
	for name in frappe.get_all(
		RECORD,
		filters={
			"employee": doc.employee,
			"document_revision": doc.document_revision,
			"docstatus": 0,
			"status": OPEN,
			"trigger": ["!=", HISTORICAL],
		},
		pluck="name",
	):
		close(name, WITHDRAWN, _("The training is recorded from paper in {0}.").format(doc.name))


def revoke(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA withdraws a competence, after a nonconformity or a failed check (D-18). The
	employee is refused for the work until retrained; the retraining opens at once."""
	require(
		doc.docstatus == 1 and doc.status == COMPETENT,
		_("{0} holds no competence to revoke.").format(doc.name),
	)
	require(
		QA_ROLE in roles_of(frappe.session.user), _("Only QA revokes a competence."), frappe.PermissionError
	)
	require(reason, _("Say why the competence is revoked."))
	doc.status, doc.outcome_reason = REVOKED, reason
	doc.revoked_by, doc.revoked_on = frappe.session.user, now_datetime()
	sign(doc, "Rejected", "Revoke", password, reason, permission="read")
	doc.flags.audit_reason = reason  # the audit trail's question, answered by the signed reason
	doc.flags.training = True
	doc.flags.ignore_permissions = True  # the signed revocation is the only change after completion
	doc.save()
	reconcile_safely([doc.employee], _("record {0}").format(doc.name))


# --- Who sees a record ---------------------------------------------------------------------------

SEE_ALL = frozenset(
	{
		QA_ROLE,
		COORDINATOR_ROLE,
		"Manufacturing User",
		"Manufacturing Manager",
		"Sales Manager",
		"Purchase Manager",
		"Stock Manager",
		"System Manager",
	}
)


def query_conditions(user: str | None = None) -> str:
	"""Outside QA, training, production and management, people see their own records: as trainee
	or as assessor."""
	user = user or frappe.session.user
	if user == "Administrator" or SEE_ALL & roles_of(user):
		return ""
	quoted = frappe.db.escape(user)
	return f"(`tabTraining Record`.`trainee_user` = {quoted} or `tabTraining Record`.`assessor` = {quoted})"


def has_permission(doc: Document, ptype: str | None = None, user: str | None = None) -> bool:
	"""On top of the roles' rights: everyone else reads and prints only their own records. Frappe
	takes anything but True from this hook as a refusal."""
	user = user or frappe.session.user
	if user == "Administrator" or SEE_ALL & roles_of(user):
		return True
	return user in (doc.get("trainee_user"), doc.get("assessor")) and ptype in ("read", "print", None)


# --- At the workstation --------------------------------------------------------------------------


def not_competent(pairs: set[tuple[str, str]], on=None) -> list[str]:
	"""Of (employee, operation) pairs, why each employee may not do that operation now: active,
	and competent, unexpired, on the revision in effect of the operation's work instruction."""
	pairs = {(e, o) for e, o in pairs if o}
	if not pairs:
		return []
	day = getdate(on or today())
	operations = sorted({o for _e, o in pairs})
	instruction = dict(
		frappe.get_all(
			"Operation",
			filters={"name": ["in", operations]},
			fields=["name", routing.WORK_INSTRUCTION],
			as_list=True,
		)
	)
	current = dict(
		frappe.get_all(
			"Controlled Document",
			filters={
				"name": ["in", sorted({w for w in instruction.values() if w}) or [""]],
				"status": "Effective",
			},
			fields=["name", "current_revision"],
			as_list=True,
		)
	)
	employees = sorted({e for e, _o in pairs if e})
	active = set(
		frappe.get_all(
			"Employee", filters={"name": ["in", employees or [""]], "status": ACTIVE}, pluck="name"
		)
	)
	held = {
		(r.employee, r.document_revision)
		for r in frappe.get_all(
			RECORD,
			filters={
				"employee": ["in", employees or [""]],
				"document_revision": ["in", sorted({r for r in current.values() if r}) or [""]],
				"docstatus": 1,
				"status": COMPETENT,
			},
			fields=["employee", "document_revision", "valid_until"],
		)
		if not r.valid_until or getdate(r.valid_until) >= day
	}
	why = []
	for employee, operation in sorted(pairs, key=lambda p: (p[0] or "", p[1])):
		wi = instruction.get(operation)
		rev = current.get(wi)
		if not employee:
			why.append(_("time on {0} is logged without the operator").format(operation))
		elif employee not in active:
			why.append(_("{0} is not an active employee").format(employee))
		elif not rev:
			why.append(
				_("operation {0} has no work instruction in effect to be trained on").format(operation)
			)
		elif (employee, rev) not in held:
			why.append(
				_("{0} is not competent in {1} revision {2}").format(
					employee, wi, frappe.db.get_value("Document Revision", rev, "revision")
				)
			)
	return why


def operators_to_check(doc: Document) -> set[tuple[str, str]]:
	"""The (operator, operation) of each time log that is running, new, or changed since saved:
	finished time is not judged again by today's training."""
	before = {row.name: row for row in (doc.get_doc_before_save() or frappe._dict()).get("time_logs") or []}
	pairs = set()
	for row in doc.get("time_logs") or []:
		old = before.get(row.name)
		operation = row.get("operation") or doc.get("operation")
		if (
			not row.get("to_time")
			or old is None
			or (old.get("employee"), old.get("operation")) != (row.get("employee"), row.get("operation"))
		):
			pairs.add((row.get("employee"), operation))
	return pairs


def refuse_new_operators(doc: Document, values: frappe._dict) -> None:
	"""ERPNext's `make_time_log` writes the time log it adds straight to the database: the
	operators it starts or adds are checked before it is called."""
	employees = values.get("employees") or []
	if isinstance(employees, str):
		import json

		employees = json.loads(employees)
	names = [e.get("employee") if isinstance(e, dict) else e for e in employees] or [values.get("employee")]
	if values.get("to_time") and not values.get("employees") and not values.get("employee"):
		return  # ends the logs already running, which were checked when they started
	operation = values.get("sub_operation") or doc.get("operation")
	if reasons := not_competent({(name, operation) for name in names}):
		frappe.throw(
			_("Job card {0}: work is logged only by trained, competent operators: {1}.").format(
				doc.name, "; ".join(reasons)
			),
			title=_("Operator not competent"),
		)


def guard_job_card(doc: Document, method: str | None = None) -> None:
	"""Job Card before_validate and before_update_after_submit: every operator logging time is
	competent in the work instruction of what they do (URS-TRN-005). The operators are read from
	the time logs, which ERPNext copies to the card's employee list only in its validate."""
	if doc.docstatus == 2 or doc.get("_action") == "cancel":
		return
	if reasons := not_competent(operators_to_check(doc)):
		frappe.throw(
			_("Job card {0}: work is logged only by trained, competent operators: {1}.").format(
				doc.name or "", "; ".join(reasons)
			),
			title=_("Operator not competent"),
		)
