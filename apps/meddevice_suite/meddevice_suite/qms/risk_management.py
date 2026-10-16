"""Risk management (module 39; URS-QMS-017 to URS-QMS-025; ISO 14971:2019; D2 flowchart: Risk
Identification, Risk Analysis, Risk Evaluation, Control Measures, Risk Report, Risk Review).

The acceptability policy (ISO 14971 4.2) holds the severity and probability scales and, for every
pair, whether the risk is Acceptable, to be reviewed as low as reasonably practicable, or
Unacceptable (plan D-13). QA writes it and signs it Reviewed; Management, a different person,
signs it Approved and it is in force, superseding the one before (plan D-21). One is in force.

Each product family has one Risk Management File: its finished goods (each in one file only), its
intended use, its reasonably foreseeable misuse and its effective risk management plan. Its
analysis is a Risk Assessment, issued in revisions as documents are (module 30):

	Draft ──✍Author──▶ In Review ──✍Review──▶ Awaiting Approval ──✍Approve (QA)──▶ Approved
	  ▲                    │                        │                                 │ next revision
	  └──────✍Return───────┴────────────────────────┘                                 ▼
	                                                                              Superseded

- each hazard records its sequence of events, hazardous situation and harm with a severity and a
  probability, before and after control; the system scores both from the policy in force and
  nobody types the result (`score`);
- every unacceptable risk has a control of an ISO 14971 7.1 kind, linked to a real record (an
  effective document, a specification, an item, a bill of materials or an operation), with the
  records that verify its implementation and its effectiveness; information for safety is a
  Packaging and Labelling document; a residual risk that is not acceptable is justified;
- the author, the reviewer and QA's approver are three people; approval re-checks everything,
  refuses a control that is no longer in force, pins each control's version and the policy, and
  supersedes the previous revision; nothing is deleted or cancelled;
- once a file has an approved revision, a new one is started only under an approved change
  request that lists the file, or an open risk review (`start_revision`).

QA authors a Risk Management Report on the approved revision (ISO 14971 8, 9) and another QA
person approves it: the plan implemented, the overall residual risk acceptable or justified, and
the SOPs that collect production and post-production information (ISO 14971 10). Every finished
good's device master record pins the report in force of its file (module 31, plan D-23): when a
report is approved, or a revision takes effect and leaves the file without one, the records of the
file's items that do not pin the report now in force go out of date in the same transaction.

A Risk Review is raised by the system: when a change request listing the file is approved, when a
pinned control changes or is withdrawn (qms/pinned_records.py), when the periodic review falls
due, and, through `raise_review`, when a complaint (module 43) or a CAPA (module 35) names it.
One review is open per file and source. QA closes it with a signature: "no change" with a
rationale, or by approving the new revision started under it. A change request is not closed
while a review it raised is open.

The signed acts are registered in `compliance.signing.SIGNING_ACTIONS`.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields, table_fields
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.utils import add_days, add_months, cint, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import item_master
from meddevice_suite.qms import pinned_records
from meddevice_suite.qms.doctype.change_request.change_request import changed_fields
from meddevice_suite.setup.roles import JOB_PROFILES

POLICY, FILE, ASSESSMENT = "Risk Acceptability Policy", "Risk Management File", "Risk Assessment"
REPORT, REVIEW = "Risk Management Report", "Risk Review"
HAZARD, CONTROL, FILE_ITEM, CELL, LEVEL = (
	"Risk Hazard",
	"Risk Control Measure",
	"Risk Management File Item",
	"Risk Matrix Cell",
	"Risk Scale Level",
)
CHANGE_REQUEST, CONTROLLED_DOCUMENT = "Change Request", "Controlled Document"
# Plan D-21: top management approves the policy (ISO 14971 4.2).
MANAGEMENT_ROLES = frozenset({"Manufacturing Manager", *JOB_PROFILES["Management"]})
FLAG = "risk_management"

# Plan D-13: the scales and the three regions of the matrix, in order of rising risk.
ACCEPTABLE, ALARP, UNACCEPTABLE = "Acceptable", "Review (ALARP)", "Unacceptable"
ACCEPTABILITY = (ACCEPTABLE, ALARP, UNACCEPTABLE)
D13_SEVERITY = ("Negligible", "Minor", "Serious", "Critical", "Catastrophic")
D13_PROBABILITY = ("Improbable", "Remote", "Occasional", "Probable", "Frequent")

DRAFT, REVIEWED, IN_FORCE, SUPERSEDED = "Draft", "Reviewed", "In Force", "Superseded"
IN_REVIEW, AWAITING_APPROVAL, APPROVED = "In Review", "Awaiting Approval", "Approved"
IN_PROGRESS = (DRAFT, IN_REVIEW, AWAITING_APPROVAL)
WITHDRAWN = "Withdrawn"
OPEN, CLOSED = "Open", "Closed"
NO_CHANGE, NEW_REVISION = "No change", "New revision"
CHANGE_REQUEST_TRIGGER, CONTROL_CHANGED, PERIODIC, COMPLAINT, CAPA = (
	"Change Request",
	"Control Changed",
	"Periodic",
	"Complaint",
	"CAPA",
)
TRIGGERS = (CHANGE_REQUEST_TRIGGER, CONTROL_CHANGED, PERIODIC, COMPLAINT, CAPA)

# ISO 14971 7.1, in its order of priority, and the records each kind of control is (plan Q6:
# a design element is an effective specification document, an item or a bill of materials).
INHERENT = "Inherently safe design and manufacture"
PROTECTIVE = "Protective measures"
INFORMATION = "Information for safety"
CONTROL_TYPES: dict[str, tuple[str, ...]] = {
	INHERENT: (CONTROLLED_DOCUMENT, "Item", "BOM"),
	PROTECTIVE: (CONTROLLED_DOCUMENT, "Quality Inspection Template", "Item", "BOM", "Operation"),
	INFORMATION: (CONTROLLED_DOCUMENT,),
}
# The controlled document type a kind of control must be, where it is restricted.
DOCUMENT_TYPES: dict[str, tuple[str, ...]] = {
	INHERENT: ("Specification",),
	INFORMATION: ("Packaging and Labelling",),
}
# Records that verify a control's implementation and effectiveness (ISO 14971 7.2, 7.3).
EVIDENCE_TYPES = (
	CONTROLLED_DOCUMENT,
	"Document Revision",
	"Quality Inspection",
	CHANGE_REQUEST,
	"Equipment Qualification",
	"Important File",
)
# The documents that collect production and post-production information (ISO 14971 10.1).
POST_PRODUCTION_TYPES = ("SOP",)

# Set only by the system on each record; whatever a user sends for them is discarded.
POLICY_SYSTEM = ("status", "reviewed_by", "reviewed_at", "approved_by", "approved_at", "in_force_since")
FILE_SYSTEM = ("status", "current_revision", "current_report", "next_periodic_review")
# The file's scope, which an approved revision of its analysis sets once there is one.
FILE_SCOPE = ("family_name", "intended_use", "foreseeable_misuse", "risk_management_plan", "items")
ASSESSMENT_SYSTEM = (
	"risk_management_file",
	"revision",
	"status",
	"author",
	"approver",
	"approved_on",
	"superseded_by",
	"change_request",
	"risk_review",
)
REPORT_SYSTEM = ("risk_assessment", "status", "author", "approver", "approved_on", "overall_residual_risk")
REVIEW_SAVEPOINT = "risk_review"


def roles_of(user: str | None = None) -> set[str]:
	return set(frappe.get_roles(user or frappe.session.user))


def is_management(user: str | None = None) -> bool:
	return bool(MANAGEMENT_ROLES & roles_of(user))


def lock(doctype: str, name: str) -> None:
	"""Row-lock a record for the rest of the transaction, so acts on it run one after another."""
	frappe.db.get_value(doctype, name, "name", for_update=True)


def save(doc: Document) -> None:
	# The act has made its own checks; the controllers' edit rules are for people editing forms.
	doc.flags[FLAG] = True
	doc.save(ignore_permissions=True)


def submit(doc: Document) -> None:
	doc.flags[FLAG] = True
	# Nobody holds Frappe's submit permission on these records, so its Submit button never offers
	# a way round the signatures; the signed act is the only submit.
	doc.flags.ignore_permissions = True
	doc.submit()


def only_by_signature(doc: Document) -> None:
	"""before_validate: Frappe's Submit button and the API would otherwise approve unsigned."""
	if doc.get("_action") == "submit" and not doc.flags.get(FLAG):
		frappe.throw(
			_("{0} {1} is approved only by signing it.").format(_(doc.doctype), doc.name or ""),
			frappe.PermissionError,
		)


def only_by_act(doc: Document) -> None:
	"""before_update_after_submit: what is approved changes only through its signed acts."""
	require(
		doc.flags.get(FLAG),
		_("{0} {1} is approved; it changes only through its signed acts.").format(_(doc.doctype), doc.name),
		frappe.PermissionError,
	)


def keep_system_fields(doc: Document, fields: tuple[str, ...]) -> None:
	"""Discard what a user sent for the fields the system sets: the stored values, or none on a
	new record."""
	if doc.flags.get(FLAG):
		return
	before = None if doc.is_new() else doc.get_doc_before_save()
	for field in fields:
		doc.set(field, before.get(field) if before else None)


def refuse_changes(doc: Document, message: str) -> None:
	"""A stored draft that only a signed act may move on: any change by a person is refused."""
	if doc.is_new() or doc.flags.get(FLAG):
		return
	if changed_fields(doc):
		frappe.throw(message, frappe.PermissionError)


def plain(text: str | None) -> str:
	return (text or "").strip()


# --- The acceptability policy (URS-QMS-017) ---------------------------------------------------


def current_policy() -> str | None:
	"""The policy in force: one at a time, as approval supersedes the one before."""
	return frappe.db.get_value(POLICY, {"status": IN_FORCE, "docstatus": 1}, "name")


def new_policy(doc: Document) -> None:
	"""before_insert: a new policy starts with the D-13 scales and every pair of the matrix listed,
	its acceptability left for QA to decide."""
	if not doc.severity_levels:
		doc.set("severity_levels", [{"level": i, "level_name": n} for i, n in enumerate(D13_SEVERITY, 1)])
	if not doc.probability_levels:
		doc.set(
			"probability_levels", [{"level": i, "level_name": n} for i, n in enumerate(D13_PROBABILITY, 1)]
		)
	if not doc.matrix:
		doc.set(
			"matrix",
			[
				{"severity": s.level, "probability": p.level}
				for s in doc.severity_levels
				for p in doc.probability_levels
			],
		)


def require_scales(doc: Document) -> None:
	"""validate: a policy holds its severity and probability scales and its matrix. They are not
	required on the form, as the server fills them for a new policy (`new_policy`): marked required
	there, the browser refused to save a new policy before the server could fill them (found by the
	browser walk-through of data store D9, 2026-10-15)."""
	missing = [
		_(label)
		for field, label in (
			("severity_levels", "Severity"),
			("probability_levels", "Probability"),
			("matrix", "Matrix"),
		)
		if not doc.get(field)
	]
	require(not missing, _("{0} has no {1}.").format(doc.name, ", ".join(missing)))


def guard_policy(doc: Document) -> None:
	"""before_validate: QA edits a draft; it moves on only by its signed acts."""
	only_by_signature(doc)
	if doc.docstatus != 0:
		return
	keep_system_fields(doc, (*POLICY_SYSTEM, "superseded_by"))
	if doc.is_new():
		doc.status = DRAFT
	elif doc.status != DRAFT:
		refuse_changes(
			doc, _("Policy {0} is {1}; return it to draft to change it.").format(doc.name, _(doc.status))
		)
	check_scales(doc)


def scale(rows: list) -> dict[int, str]:
	return {cint(row.level): plain(row.level_name) for row in rows}


def check_scales(doc: Document, complete: bool = False) -> None:
	"""Each scale numbered 1, 2, … with a name per level; each pair of the matrix listed once;
	complete: every pair with its acceptability, never more acceptable as the risk rises. A scale or
	matrix missing altogether is said first, as such: numbering an empty scale says nothing useful."""
	require_scales(doc)
	for table, label in (("severity_levels", _("severity")), ("probability_levels", _("probability"))):
		rows = doc.get(table) or []
		levels = [cint(row.level) for row in rows]
		require(
			len(rows) >= 2 and sorted(levels) == list(range(1, len(rows) + 1)),
			_("Number the {0} levels 1, 2, 3 … without gaps.").format(label),
		)
		names = [plain(row.level_name) for row in rows]
		require(all(names), _("Name each {0} level.").format(label))
		require(len(set(names)) == len(names), _("Each {0} level has its own name.").format(label))
	severities, probabilities = scale(doc.severity_levels), scale(doc.probability_levels)
	seen: dict[tuple[int, int], str] = {}
	for row in doc.matrix or []:
		pair = (cint(row.severity), cint(row.probability))
		require(
			pair[0] in severities and pair[1] in probabilities,
			_("Row {0} of the matrix names a level the scales do not have.").format(row.idx),
		)
		require(
			pair not in seen,
			_("Row {0}: severity {1}, probability {2} is listed twice.").format(row.idx, *pair),
		)
		require(
			not row.acceptability or row.acceptability in ACCEPTABILITY,
			_("Row {0}: unknown acceptability.").format(row.idx),
		)
		seen[pair] = row.acceptability
	if not complete:
		return
	missing = [
		_("severity {0}, probability {1}").format(s, p)
		for s in severities
		for p in probabilities
		if not seen.get((s, p))
	]
	require(not missing, _("Decide the acceptability of every pair: {0}.").format(", ".join(missing)))
	rank = {value: i for i, value in enumerate(ACCEPTABILITY)}
	for (s, p), value in seen.items():
		for higher in ((s + 1, p), (s, p + 1)):
			if higher in seen:
				require(
					rank[seen[higher]] >= rank[value],
					_(
						"Severity {0}, probability {1} cannot be more acceptable than severity {2}, probability {3}."
					).format(*higher, s, p),
				)


def review_policy(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA, who writes the policy, signs it Reviewed for Management's approval."""
	require(is_qa(), _("Only QA signs a risk acceptability policy as reviewed."), frappe.PermissionError)
	require(doc.docstatus == 0 and doc.status == DRAFT, _("Only a draft policy is reviewed."))
	require(plain(doc.policy_statement), _("State how the criteria were set."))
	check_scales(doc, complete=True)
	doc.status, doc.reviewed_by, doc.reviewed_at = REVIEWED, frappe.session.user, now_datetime()
	sign(doc, "Reviewed", "Review", password, reason)
	save(doc)


def return_policy(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.docstatus == 0 and doc.status == REVIEWED, _("Only a reviewed policy is returned."))
	require(
		is_qa() or is_management(),
		_("Only QA or Management returns a policy for changes."),
		frappe.PermissionError,
	)
	require(reason, _("Say what must change."))
	doc.status, doc.reviewed_by, doc.reviewed_at = DRAFT, None, None
	sign(doc, "Rejected", "Return", password, reason, permission="read")
	save(doc)


def approve_policy(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act (plan D-21): Management approves the policy QA reviewed, as a second person; it
	is in force at once and the one before it is superseded."""
	require(doc.docstatus == 0 and doc.status == REVIEWED, _("Only a policy QA has reviewed is approved."))
	require(
		is_management(),
		_("Only Management approves the risk acceptability policy (ISO 14971 4.2)."),
		frappe.PermissionError,
	)
	require(
		frappe.session.user != doc.reviewed_by,
		_("{0} reviewed this policy; Management approves it as a second person.").format(doc.reviewed_by),
	)
	policies = frappe.qb.DocType(POLICY)
	# One approval at a time, so one policy is in force.
	frappe.qb.from_(policies).select(policies.name).where(
		policies.status.isin((REVIEWED, IN_FORCE)) | (policies.name == doc.name)
	).for_update().run()
	check_scales(doc, complete=True)
	doc.status, doc.approved_by, doc.approved_at = IN_FORCE, frappe.session.user, now_datetime()
	doc.in_force_since = today()
	sign(doc, "Approved", "Approve", password, reason, permission="read")
	submit(doc)
	in_force = (
		frappe.qb.from_(policies)
		.select(policies.name)
		.where((policies.status == IN_FORCE) & (policies.name != doc.name))
		.for_update()  # a locking read, so a policy approved meanwhile is superseded too
		.run(pluck=True)
	)
	for previous in in_force:
		frappe.get_doc(POLICY, previous).db_set({"status": SUPERSEDED, "superseded_by": doc.name})


def policy_scales(policy: str) -> tuple[set[int], set[int], dict[tuple[int, int], str]]:
	"""The severity and probability levels of a policy and the acceptability of each pair."""
	levels = frappe.get_all(
		LEVEL,
		filters={"parenttype": POLICY, "parent": policy},
		fields=["parentfield", "level"],
	)
	cells = frappe.get_all(
		CELL,
		filters={"parenttype": POLICY, "parent": policy},
		fields=["severity", "probability", "acceptability"],
	)
	return (
		{cint(row.level) for row in levels if row.parentfield == "severity_levels"},
		{cint(row.level) for row in levels if row.parentfield == "probability_levels"},
		{(cint(cell.severity), cint(cell.probability)): cell.acceptability for cell in cells},
	)


# --- The risk management file (URS-QMS-018) ---------------------------------------------------


def guard_file(doc: Document) -> None:
	"""validate: a family's finished goods are each in one file only; once the file has an
	approved analysis, its scope changes only through the next approved revision."""
	keep_system_fields(doc, FILE_SYSTEM)
	if doc.is_new():
		doc.status = DRAFT
	doc.family_name = plain(doc.family_name)
	if not doc.flags.get(FLAG) and doc.current_revision:
		changed = changed_fields(doc) & set(FILE_SCOPE)
		require(
			not changed,
			_("{0} has an approved risk assessment; its scope changes through a new revision.").format(
				doc.name
			),
			frappe.PermissionError,
		)
	check_items(doc.items, doc.name)


def check_items(rows: list, file: str | None) -> None:
	"""Each item listed once, a finished good, and in no other risk management file."""
	items = [row.item for row in rows or []]
	twice = sorted({item for item in items if items.count(item) > 1})
	require(not twice, _("Listed twice: {0}.").format(", ".join(twice)))
	if not items:
		return
	not_finished = sorted(
		row.name
		for row in frappe.get_all(
			"Item", filters={"name": ["in", items]}, fields=["name", item_master.ITEM_TYPE]
		)
		if row.get(item_master.ITEM_TYPE) != item_master.FINISHED_GOOD
	)
	require(
		not not_finished,
		_("A risk management file covers finished goods: {0} is not one.").format(", ".join(not_finished)),
	)
	elsewhere = frappe.get_all(
		FILE_ITEM,
		filters={"parenttype": FILE, "item": ["in", items], "parent": ["!=", file or ""]},
		fields=["item", "parent"],
	)
	require(
		not elsewhere,
		_("Each finished good is in one risk management file: {0}.").format(
			", ".join(_("{0} is in {1}").format(row.item, row.parent) for row in elsewhere)
		),
	)


# --- The risk assessment (URS-QMS-019 to URS-QMS-022) -----------------------------------------


def next_revision(file: str) -> str:
	"""R01, R02, … compared as numbers, so R100 follows R99."""
	numbers = [
		cint(revision.lstrip("R"))
		for revision in frappe.get_all(ASSESSMENT, filters={"risk_management_file": file}, pluck="revision")
	]
	return f"R{max(numbers, default=0) + 1:02d}"


def name_assessment(doc: Document) -> None:
	doc.revision = next_revision(doc.risk_management_file)
	doc.name = f"{doc.risk_management_file}-{doc.revision}"


def new_assessment(doc: Document) -> None:
	"""before_insert: QA starts a revision, one at a time per file; once the file has an approved
	revision, only under an approved change request listing the file or an open review."""
	require(is_qa(), _("Only QA starts a risk assessment."), frappe.PermissionError)
	lock(FILE, doc.risk_management_file)
	file = frappe.get_doc(FILE, doc.risk_management_file)
	in_progress = revision_in_progress(file.name)
	require(not in_progress, _("{0} already has a revision in progress: {1}.").format(file.name, in_progress))
	change_request, risk_review = doc.change_request, doc.risk_review
	for field in ASSESSMENT_SYSTEM:
		if field not in ("risk_management_file", "revision"):
			doc.set(field, None)
	doc.change_request, doc.risk_review = change_request, risk_review
	if file.current_revision:
		require_reason_to_revise(doc, file.name)
	elif doc.risk_review:
		require_open_review(doc.risk_review, file.name)
	doc.author, doc.status = frappe.session.user, DRAFT
	for field in ("intended_use", "foreseeable_misuse", "risk_management_plan"):
		if not doc.get(field):
			doc.set(field, file.get(field))
	if not doc.items:
		doc.set("items", [{"item": row.item} for row in file.items])


def revision_in_progress(file: str) -> str | None:
	return frappe.db.get_value(
		ASSESSMENT, {"risk_management_file": file, "status": ["in", IN_PROGRESS], "docstatus": 0}
	)


def require_open_review(review: str, file: str) -> None:
	row = frappe.db.get_value(REVIEW, review, ["risk_management_file", "status", "docstatus"], as_dict=True)
	require(
		row and row.risk_management_file == file and row.status == OPEN and row.docstatus == 0,
		_("Risk review {0} is not an open review of {1}.").format(review, file),
	)


def require_reason_to_revise(doc: Document, file: str) -> None:
	"""URS-QMS-024: a new revision of an approved file is made under an approved change request
	that lists the file, or to answer an open risk review."""
	from meddevice_suite.qms import change_control  # change control raises reviews in this module

	if doc.change_request and not doc.risk_review:
		doc.risk_review = frappe.db.get_value(
			REVIEW,
			{
				"risk_management_file": file,
				"source_doctype": CHANGE_REQUEST,
				"source_name": doc.change_request,
				"status": OPEN,
			},
		)
	if doc.risk_review:
		require_open_review(doc.risk_review, file)
		if doc.change_request:
			change_control.require_approved_change(doc.change_request, FILE, file, made_in=doc)
		return
	require(
		doc.change_request,
		_(
			"{0} has an approved risk assessment: name the approved change request or the open risk review for this revision."
		).format(file),
	)
	change_control.require_approved_change(doc.change_request, FILE, file, made_in=doc)


def guard_assessment(doc: Document) -> None:
	"""before_validate: the author edits the draft; everything else moves on only by signing."""
	only_by_signature(doc)
	if doc.docstatus != 0 or doc.is_new() or doc.flags.get(FLAG):
		return
	before = doc.get_doc_before_save()
	if before.status == DRAFT and frappe.session.user == before.author:
		keep_system_fields(doc, ASSESSMENT_SYSTEM)
		return
	refuse_changes(
		doc,
		_("Revision {0} is {1}; only its author changes it, as a draft.").format(doc.name, _(before.status)),
	)


def score(doc: Document) -> None:
	"""validate: the risks are worked out from the policy in force, over whatever was typed for
	them (URS-QMS-019); a draft's controls carry no pinned version until approval (URS-QMS-020)."""
	if doc.docstatus != 0 or doc.status != DRAFT:
		# Once its author has signed, a revision keeps the policy and the scores they signed for;
		# a new policy is taken only by returning it to draft and authoring it again.
		return
	doc.policy = current_policy()
	require(doc.policy, _("No risk acceptability policy is in force; Management approves one first."))
	severities, probabilities, cells = policy_scales(doc.policy)
	ids = [plain(row.hazard_id) for row in doc.hazards or []]
	twice = sorted({hazard for hazard in ids if ids.count(hazard) > 1})
	require(not twice, _("Hazard {0} is listed twice.").format(", ".join(twice)))
	worst = None
	for row in doc.hazards or []:
		row.hazard_id = plain(row.hazard_id)
		require(
			cint(row.severity) in severities and cint(row.probability) in probabilities,
			_("Hazard {0}: rate severity 1 to {1} and probability 1 to {2}.").format(
				row.hazard_id, max(severities), max(probabilities)
			),
		)
		row.initial_acceptability = cells.get((cint(row.severity), cint(row.probability)))
		row.residual_acceptability = None
		if cint(row.residual_severity) or cint(row.residual_probability):
			require(
				cint(row.residual_severity) in severities and cint(row.residual_probability) in probabilities,
				_("Hazard {0}: rate the residual severity 1 to {1} and probability 1 to {2}.").format(
					row.hazard_id, max(severities), max(probabilities)
				),
			)
			row.residual_acceptability = cells.get(
				(cint(row.residual_severity), cint(row.residual_probability))
			)
			if worst is None or ACCEPTABILITY.index(row.residual_acceptability) > ACCEPTABILITY.index(worst):
				worst = row.residual_acceptability
	doc.highest_residual_risk = worst
	check_controls(doc, set(ids))


def check_controls(doc: Document, hazards: set[str]) -> None:
	"""URS-QMS-020: each control answers a hazard, is of a 14971 7.1 kind and is a real record of
	the types that kind allows; it carries no version until approval pins it."""
	documents = {
		row.control_name
		for row in doc.controls or []
		if row.control_doctype == CONTROLLED_DOCUMENT and row.control_name
	}
	document_type = (
		dict(
			frappe.get_all(
				CONTROLLED_DOCUMENT,
				filters={"name": ["in", sorted(documents)]},
				fields=["name", "document_type"],
				as_list=True,
			)
		)
		if documents
		else {}
	)
	for row in doc.controls or []:
		row.pinned_version = None
		row.hazard_id = plain(row.hazard_id)
		where = _("Control {0}").format(row.idx)
		require(
			row.hazard_id in hazards, _("{0}: no hazard {1} in this assessment.").format(where, row.hazard_id)
		)
		require(
			row.option in CONTROL_TYPES, _("{0}: choose the kind of control (ISO 14971 7.1).").format(where)
		)
		require(
			row.control_doctype and row.control_name,
			_(
				"{0}: link the record that implements the control; a description alone is not a control."
			).format(where),
		)
		require(
			row.control_doctype in CONTROL_TYPES[row.option],
			_("{0}: {1} is implemented by a {2}.").format(
				where, _(row.option), " / ".join(_(t) for t in CONTROL_TYPES[row.option])
			),
		)
		if row.option in DOCUMENT_TYPES and row.control_doctype == CONTROLLED_DOCUMENT:
			# A document is of the type the kind of control names; an item or a bill of materials
			# is a design element in itself (plan Q6).
			require(
				document_type.get(row.control_name) in DOCUMENT_TYPES[row.option],
				_("{0}: {1} is a {2} document.").format(
					where, _(row.option), " / ".join(_(t) for t in DOCUMENT_TYPES[row.option])
				),
			)
		if row.introduces_hazard_id:
			row.introduces_hazard_id = plain(row.introduces_hazard_id)
			require(
				row.introduces_hazard_id in hazards and row.introduces_hazard_id != row.hazard_id,
				_(
					"{0}: the hazard it introduces (ISO 14971 7.5) is another hazard of this assessment."
				).format(where),
			)
		for kind in ("implementation", "effectiveness"):
			evidence_type = row.get(f"{kind}_evidence_type")
			require(
				not evidence_type or evidence_type in EVIDENCE_TYPES,
				_("{0}: unknown kind of evidence.").format(where),
			)


def valid_evidence(pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
	"""Of the (record type, name) pairs, those that stand as evidence: a submitted record, an
	effective document, an active file. One query per record type."""
	by_type: dict[str, list[str]] = {}
	for doctype, name in pairs:
		by_type.setdefault(doctype, []).append(name)
	valid = set()
	for doctype, names in by_type.items():
		filters: dict = {"name": ["in", sorted(set(names))]}
		if doctype == CONTROLLED_DOCUMENT:
			filters["status"] = "Effective"
		elif doctype == "Important File":
			filters["status"] = "Active"
		elif frappe.get_meta(doctype).is_submittable:
			filters["docstatus"] = 1
		valid |= {(doctype, name) for name in frappe.get_all(doctype, filters=filters, pluck="name")}
	return valid


def check_content(doc: Document) -> None:
	"""What a revision needs before it is signed by its author and again at approval: the scope
	(URS-QMS-018), every hazard's residual risk (URS-QMS-021), a control for every unacceptable
	risk (URS-QMS-020) and the evidence of every control (URS-QMS-021)."""
	require(plain(doc.intended_use), _("State the intended use."))
	require(plain(doc.foreseeable_misuse), _("State the reasonably foreseeable misuse."))
	require(
		doc.risk_management_plan
		and frappe.db.get_value(CONTROLLED_DOCUMENT, doc.risk_management_plan, "status") == "Effective",
		_("Name the risk management plan, an effective controlled document."),
	)
	require(doc.items, _("List the finished goods of the family."))
	check_items(doc.items, doc.risk_management_file)
	require(doc.hazards, _("Analyse at least one hazard."))
	controlled = {row.hazard_id for row in doc.controls or []}
	for row in doc.hazards:
		require(
			row.residual_acceptability,
			_("Hazard {0}: estimate the residual risk.").format(row.hazard_id),
		)
		require(
			cint(row.residual_severity) <= cint(row.severity)
			and cint(row.residual_probability) <= cint(row.probability),
			_("Hazard {0}: the residual risk is not higher than the risk before control.").format(
				row.hazard_id
			),
		)
		require(
			row.initial_acceptability != UNACCEPTABLE or row.hazard_id in controlled,
			_("Hazard {0} is unacceptable: add a risk control measure.").format(row.hazard_id),
		)
		reduced = (cint(row.residual_severity), cint(row.residual_probability)) != (
			cint(row.severity),
			cint(row.probability),
		)
		require(
			not reduced or row.hazard_id in controlled,
			_("Hazard {0}: a risk is reduced only by a risk control measure (ISO 14971 7); add it.").format(
				row.hazard_id
			),
		)
		require(
			row.residual_acceptability == ACCEPTABLE or plain(row.justification),
			_("Hazard {0}: justify the residual risk that is not acceptable (ALARP or benefit-risk).").format(
				row.hazard_id
			),
		)
	evidence = set()
	for row in doc.controls or []:
		for kind in ("implementation", "effectiveness"):
			pair = (row.get(f"{kind}_evidence_type"), row.get(f"{kind}_evidence"))
			require(
				all(pair),
				_("Control {0}: link the record that verifies its {1}.").format(row.idx, _(kind)),
			)
			evidence.add(pair)
	not_valid = evidence - valid_evidence(evidence)
	require(
		not not_valid,
		_("Not in force as evidence (submitted, effective or active): {0}.").format(
			", ".join(f"{_(t)} {n}" for t, n in sorted(not_valid))
		),
	)


def out_of_force(doc: Document) -> list[str]:
	"""The controls whose record is no longer in force."""
	by_type: dict[str, set[str]] = {}
	for row in doc.controls or []:
		by_type.setdefault(row.control_doctype, set()).add(row.control_name)
	live = {t: pinned_records.in_force_names(t, names) for t, names in by_type.items()}
	return [
		f"{_(row.control_doctype)} {row.control_name}"
		for row in doc.controls or []
		if row.control_name not in live[row.control_doctype]
	]


def author(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.docstatus == 0 and doc.status == DRAFT, _("Only a draft is signed by its author."))
	require(
		frappe.session.user == doc.author,
		_("Only the author of {0} signs it as author.").format(doc.name),
		frappe.PermissionError,
	)
	require(doc.reviewer, _("Choose the reviewer before signing."))
	require(doc.reviewer != doc.author, _("The reviewer cannot be the author."))
	may_review = frappe.has_permission(ASSESSMENT, "read", user=doc.reviewer)
	require(
		may_review and frappe.db.get_value("User", doc.reviewer, "enabled"),
		_("{0} cannot review risk assessments.").format(doc.reviewer),
	)
	require(doc.revision == "R01" or plain(doc.summary_of_change), _("Say what changed in this revision."))
	score(doc)
	check_content(doc)
	doc.status = IN_REVIEW
	sign(doc, "Authored", "Author", password, reason)
	save(doc)


def review(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.docstatus == 0 and doc.status == IN_REVIEW, _("Only a revision in review is reviewed."))
	require(
		frappe.session.user == doc.reviewer,
		_("Only the reviewer of {0} signs it as reviewer.").format(doc.name),
		frappe.PermissionError,
	)
	doc.status = AWAITING_APPROVAL
	sign(doc, "Reviewed", "Review", password, reason, permission="read")
	save(doc)


def return_for_changes(doc: Document, password: str, reason: str | None = None) -> None:
	require(
		doc.docstatus == 0 and doc.status in (IN_REVIEW, AWAITING_APPROVAL),
		_("Only a revision in review or awaiting approval is returned."),
	)
	require(
		frappe.session.user == doc.reviewer or is_qa(),
		_("Only the reviewer or QA returns {0} for changes.").format(doc.name),
		frappe.PermissionError,
	)
	require(reason, _("Say what must change."))
	doc.status = DRAFT
	sign(doc, "Rejected", "Return", password, reason, permission="read")
	save(doc)


def withdraw(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA withdraws a revision or report that will not be finished (its author left, or
	the analysis found nothing to change). Nothing is deleted; a withdrawn one no longer holds the
	file, its review or the change request back."""
	require(
		doc.docstatus == 0 and doc.status in IN_PROGRESS,
		_("Only an unfinished {0} is withdrawn.").format(_(doc.doctype)),
	)
	require(is_qa(), _("Only QA withdraws it."), frappe.PermissionError)
	require(plain(reason), _("Say why it is withdrawn."))
	doc.status = WITHDRAWN
	sign(doc, "Rejected", "Withdraw", password, reason, permission="read")
	save(doc)


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA, neither the author nor the reviewer, approves the revision. Everything is
	checked again; each control's version and the policy are pinned; the revision is the file's
	from now on and the one before is superseded (URS-QMS-022)."""
	require(
		doc.docstatus == 0 and doc.status == AWAITING_APPROVAL, _("Only a reviewed revision is approved.")
	)
	require(is_qa(), _("Only QA approves a risk assessment."), frappe.PermissionError)
	require(frappe.session.user != doc.author, _("The author of {0} cannot approve it.").format(doc.name))
	require(
		frappe.session.user != doc.reviewer, _("The reviewer of {0} cannot also approve it.").format(doc.name)
	)
	lock(FILE, doc.risk_management_file)
	if doc.risk_review:
		require_open_review(doc.risk_review, doc.risk_management_file)
	require(
		doc.policy == current_policy(),
		_(
			"The risk acceptability policy has changed since {0} was authored; return it to its author to "
			"be scored and signed again."
		).format(doc.name),
	)
	check_content(doc)
	stale = out_of_force(doc)
	require(
		not stale,
		_("No longer in force, so not a control: {0}. Revise the assessment.").format(", ".join(stale)),
	)
	for doctype in {row.control_doctype for row in doc.controls}:
		tokens = pinned_records.version_tokens(
			doctype, [row.control_name for row in doc.controls if row.control_doctype == doctype]
		)
		for row in doc.controls:
			if row.control_doctype == doctype:
				row.pinned_version = tokens.get(row.control_name)
	doc.approver, doc.approved_on, doc.status = frappe.session.user, now_datetime(), APPROVED
	sign(doc, "Approved", "Approve", password, reason)
	submit(doc)
	take_effect(doc)


def take_effect(doc: Document) -> None:
	"""The approved revision becomes the file's: the one before is superseded, its report no longer
	reports on the file in force, the scope is the revision's, and the next periodic review is due
	a review period from today. The review it answers is closed with it."""
	file = frappe.get_doc(FILE, doc.risk_management_file)
	if file.current_revision and file.current_revision != doc.name:
		frappe.get_doc(ASSESSMENT, file.current_revision).db_set(
			{"status": SUPERSEDED, "superseded_by": doc.name}
		)
	previous_report = file.current_report
	if previous_report:
		frappe.get_doc(REPORT, previous_report).db_set("status", SUPERSEDED)
	listed_before = [row.item for row in file.items]
	file.update(
		{
			"intended_use": doc.intended_use,
			"foreseeable_misuse": doc.foreseeable_misuse,
			"risk_management_plan": doc.risk_management_plan,
			"current_revision": doc.name,
			"current_report": None,
			"status": IN_FORCE,
			"next_periodic_review": add_months(
				today(), quality_settings.value("risk_periodic_review_months")
			),
		}
	)
	file.set("items", [{"item": row.item} for row in doc.items])
	save(file)
	# The report no longer reports on the analysis in force: what pins it (a device master record,
	# module 31) is told in this transaction, and so is the record of every item the file listed
	# before or lists now, which has no report in force until the next is approved (plan D-23).
	pinned_records.records_changed(REPORT, [previous_report])
	device_master_records_changed([*listed_before, *(row.item for row in doc.items)])
	if doc.risk_review:
		review_doc = frappe.get_doc(REVIEW, doc.risk_review)
		review_doc.update(
			{
				"outcome": NEW_REVISION,
				"resulting_revision": doc.name,
				"rationale": plain(doc.summary_of_change) or _("Revision {0} approved.").format(doc.name),
			}
		)
		close(review_doc, doc.approver)


@frappe.whitelist(methods=["POST"])
def start_revision(
	risk_management_file: str, change_request: str | None = None, risk_review: str | None = None
) -> str:
	"""QA starts the next revision of a file's assessment, from the revision in force (URS-QMS-024)."""
	frappe.has_permission(ASSESSMENT, "create", throw=True)
	file = frappe.get_doc(FILE, risk_management_file)
	file.check_permission("read")
	doc = frappe.new_doc(ASSESSMENT)
	doc.update(
		{
			"risk_management_file": file.name,
			"change_request": change_request or None,
			"risk_review": risk_review or None,
		}
	)
	if file.current_revision:
		current = frappe.get_doc(ASSESSMENT, file.current_revision)
		for table in ("hazards", "controls"):
			doc.set(table, [copy_row(row, ("pinned_version",)) for row in current.get(table)])
	doc.insert()
	return doc.name


def copy_row(row: Document, leave_out: tuple[str, ...] = ()) -> dict:
	return {
		df.fieldname: row.get(df.fieldname)
		for df in row.meta.fields
		if df.fieldtype not in no_value_fields
		and df.fieldtype not in table_fields
		and df.fieldname not in leave_out
	}


# --- The risk management report (URS-QMS-023) -------------------------------------------------


def new_report(doc: Document) -> None:
	"""before_insert: QA reports on the file's approved revision in force, one report at a time."""
	require(is_qa(), _("Only QA writes a risk management report."), frappe.PermissionError)
	assessment = doc.risk_assessment
	for field in REPORT_SYSTEM:
		doc.set(field, None)
	doc.risk_assessment = assessment
	require_current(doc)
	in_progress = frappe.db.get_value(
		REPORT, {"risk_assessment": assessment, "status": ["in", (DRAFT, AWAITING_APPROVAL)], "docstatus": 0}
	)
	require(not in_progress, _("{0} already has a report in progress: {1}.").format(assessment, in_progress))
	doc.author, doc.status = frappe.session.user, DRAFT


def require_current(doc: Document) -> frappe._dict:
	row = frappe.db.get_value(
		ASSESSMENT,
		doc.risk_assessment,
		["risk_management_file", "status", "highest_residual_risk"],
		as_dict=True,
	)
	require(
		row
		and row.status == APPROVED
		and frappe.db.get_value(FILE, row.risk_management_file, "current_revision") == doc.risk_assessment,
		_("A report is on the approved revision in force of a risk management file."),
	)
	doc.risk_management_file = row.risk_management_file
	doc.overall_residual_risk = row.highest_residual_risk
	return row


def guard_report(doc: Document) -> None:
	"""before_validate: the author edits the draft; it moves on only by signing."""
	only_by_signature(doc)
	if doc.docstatus != 0 or doc.flags.get(FLAG):
		return
	if not doc.is_new():
		before = doc.get_doc_before_save()
		if before.status == DRAFT and frappe.session.user == before.author:
			keep_system_fields(doc, REPORT_SYSTEM)
		else:
			refuse_changes(
				doc,
				_("Report {0} is {1}; only its author changes it, as a draft.").format(
					doc.name, _(before.status)
				),
			)
	if doc.risk_assessment:
		doc.overall_residual_risk = frappe.db.get_value(
			ASSESSMENT, doc.risk_assessment, "highest_residual_risk"
		)


def check_report(doc: Document) -> None:
	"""The plan implemented, the overall residual risk evaluated, and the SOPs that collect
	production and post-production information named (ISO 14971 8, 9, 10)."""
	require_current(doc)
	require(
		cint(doc.plan_implemented) and plain(doc.plan_review),
		_("Confirm the risk management plan was implemented, and say how it was checked."),
	)
	require(
		plain(doc.overall_conclusion),
		_("Evaluate the overall residual risk: acceptable, or justified by the benefits."),
	)
	documents = [row.controlled_document for row in doc.information_sources or []]
	require(documents, _("Name the SOPs that collect production and post-production information."))
	effective = set(
		frappe.get_all(
			CONTROLLED_DOCUMENT,
			filters={
				"name": ["in", documents],
				"status": "Effective",
				"document_type": ["in", POST_PRODUCTION_TYPES],
			},
			pluck="name",
		)
	)
	missing = sorted(set(documents) - effective)
	require(not missing, _("Not an effective SOP: {0}.").format(", ".join(missing)))


def author_report(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.docstatus == 0 and doc.status == DRAFT, _("Only a draft report is signed by its author."))
	require(
		frappe.session.user == doc.author,
		_("Only the author of {0} signs it as author.").format(doc.name),
		frappe.PermissionError,
	)
	check_report(doc)
	doc.status = AWAITING_APPROVAL
	sign(doc, "Authored", "Author", password, reason)
	save(doc)


def return_report(doc: Document, password: str, reason: str | None = None) -> None:
	require(
		doc.docstatus == 0 and doc.status == AWAITING_APPROVAL,
		_("Only a report awaiting approval is returned."),
	)
	require(is_qa(), _("Only QA returns a risk management report."), frappe.PermissionError)
	require(reason, _("Say what must change."))
	doc.status = DRAFT
	sign(doc, "Rejected", "Return", password, reason)
	save(doc)


def approve_report(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA, not the author, approves the report; it is the file's report from now on."""
	require(doc.docstatus == 0 and doc.status == AWAITING_APPROVAL, _("Only a signed report is approved."))
	require(is_qa(), _("Only QA approves a risk management report."), frappe.PermissionError)
	require(frappe.session.user != doc.author, _("The author of {0} cannot approve it.").format(doc.name))
	lock(FILE, doc.risk_management_file)
	check_report(doc)
	doc.approver, doc.approved_on, doc.status = frappe.session.user, now_datetime(), APPROVED
	sign(doc, "Approved", "Approve", password, reason)
	submit(doc)
	report_takes_effect(doc)


def report_takes_effect(doc: Document) -> None:
	"""The approved report is the file's from now on and the one before it is superseded. What pins
	the superseded report, and the device master record of each item of the file, is told in this
	transaction (module 31, plan D-23)."""
	file = frappe.get_doc(FILE, doc.risk_management_file)
	previous = file.current_report if file.current_report != doc.name else None
	if previous:
		frappe.get_doc(REPORT, previous).db_set("status", SUPERSEDED)
	file.current_report = doc.name
	save(file)
	pinned_records.records_changed(REPORT, [previous])
	device_master_records_changed([row.item for row in file.items])


def device_master_records_changed(items: list[str]) -> None:
	"""Module 31 (plan D-23): each item's device master record must pin the report in force of the
	item's file; one that does not goes out of date in this transaction."""
	# Imported here: the device master record builds on this module.
	from meddevice_suite.qms.device_master_record import risk_files_changed

	risk_files_changed(items)


# --- Risk reviews (URS-QMS-024, URS-QMS-025) --------------------------------------------------


def raise_review(
	file: str,
	trigger: str,
	source_doctype: str,
	source_name: str,
	state: str = "",
	details: str = "",
) -> str:
	"""Open a review of the file for what happened to the source; the interface modules 43
	(complaints) and 35 (CAPA) call. One review is open per file and source, and the same event
	(the source at the same `state`, such as a control's version) raises one review only."""
	require(trigger in TRIGGERS, _("Unknown reason for a risk review: {0}.").format(trigger))
	require(frappe.db.exists(FILE, file), _("Risk management file {0} does not exist.").format(file))
	lock(FILE, file)  # one review at a time per file, so none is raised twice
	source = {"risk_management_file": file, "source_doctype": source_doctype, "source_name": source_name}
	reviews = frappe.qb.DocType(REVIEW)
	# A locking read: it sees a review another transaction has just raised for the same source.
	earlier = (
		frappe.qb.from_(reviews)
		.select(reviews.name, reviews.status, reviews.docstatus, reviews.source_state)
		.where(
			(reviews.risk_management_file == file)
			& (reviews.source_doctype == source_doctype)
			& (reviews.source_name == source_name)
		)
		.orderby(reviews.creation)
		.for_update()
		.run(as_dict=True)
	)
	for row in earlier:
		if (row.status == OPEN and row.docstatus == 0) or (row.source_state or "") == (state or ""):
			return row.name
	doc = frappe.get_doc(
		{
			"doctype": REVIEW,
			**source,
			"trigger": trigger,
			"source_state": state or "",
			"details": details or None,
		}
	)
	doc.flags[FLAG] = True
	doc.flags.ignore_permissions = True  # raised by the system for whoever's change caused it
	doc.insert()
	return doc.name


def new_review(doc: Document) -> None:
	"""before_insert: reviews are raised by the system only."""
	require(doc.flags.get(FLAG), _("Risk reviews are raised by the system."), frappe.PermissionError)
	doc.status, doc.raised_on = OPEN, today()
	doc.due_date = add_days(today(), quality_settings.value("risk_review_due_days"))
	for field in ("outcome", "rationale", "resulting_revision", "closed_by", "closed_on"):
		doc.set(field, None)


def guard_review(doc: Document) -> None:
	only_by_signature(doc)
	if doc.docstatus == 0:
		refuse_changes(doc, _("Risk review {0} is closed by QA's signature.").format(doc.name))


def close(doc: Document, user: str) -> None:
	doc.closed_by, doc.closed_on, doc.status = user, now_datetime(), CLOSED
	submit(doc)


def close_review(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA closes the review without a new revision, with its rationale."""
	require(doc.docstatus == 0 and doc.status == OPEN, _("Risk review {0} is not open.").format(doc.name))
	require(is_qa(), _("Only QA closes a risk review."), frappe.PermissionError)
	require(plain(reason), _("Give the rationale for leaving the risk assessment unchanged."))
	if doc.trigger == CONTROL_CHANGED:
		control = frappe.db.get_value(REVIEW, doc.name, ["source_doctype", "source_name"], as_dict=True)
		require(
			pinned_records.in_force(control.source_doctype, control.source_name),
			_(
				"{0} {1} is no longer in force, so it is not a control: revise the assessment instead of "
				"leaving it unchanged."
			).format(_(control.source_doctype), control.source_name),
		)
	in_progress = frappe.db.get_value(
		ASSESSMENT, {"risk_review": doc.name, "status": ["in", IN_PROGRESS], "docstatus": 0}
	)
	require(
		not in_progress,
		_(
			"Revision {0} is in progress for this review; approve it, or withdraw it before closing the review."
		).format(in_progress),
	)
	doc.outcome, doc.rationale = NO_CHANGE, plain(reason)
	doc.closed_by, doc.closed_on, doc.status = frappe.session.user, now_datetime(), CLOSED
	sign(doc, "Reviewed", "Close: No Change", password, reason, permission="read")
	submit(doc)
	if doc.trigger == PERIODIC:
		file = frappe.get_doc(FILE, doc.risk_management_file)
		file.next_periodic_review = add_months(today(), quality_settings.value("risk_periodic_review_months"))
		save(file)


def control_pins(targets: dict[str, list[str]] | None) -> list[pinned_records.Pin]:
	"""Pinned records (qms/pinned_records.py): the controls of each file's revision in force."""
	file, control = frappe.qb.DocType(FILE), frappe.qb.DocType(CONTROL)
	query = (
		frappe.qb.from_(file)
		.join(control)
		.on((control.parent == file.current_revision) & (control.parenttype == ASSESSMENT))
		.select(
			file.name.as_("file"),
			control.control_doctype,
			control.control_name,
			control.pinned_version,
			control.name,
		)
		.where(file.status == IN_FORCE)
	)
	if targets is not None:
		if not any(targets.values()):
			return []
		query = query.where(
			Criterion.any(
				(control.control_doctype == doctype) & control.control_name.isin(names)
				for doctype, names in targets.items()
				if names
			)
		)
	return [
		pinned_records.Pin(row.file, row.control_doctype, row.control_name, row.pinned_version, row.name)
		for row in query.run(as_dict=True)
		if row.control_doctype in pinned_records.PINNABLE
	]


def controls_changed(changes: list[pinned_records.Change]) -> None:
	"""A control changed or withdrawn raises one review of its file (URS-QMS-025)."""
	events: dict[tuple[str, str, str], pinned_records.Change] = {}
	for change in changes:
		events.setdefault((change.pin.owner, change.pin.doctype, change.pin.name), change)
	raised = {
		(row.risk_management_file, row.source_doctype, row.source_name, row.source_state or "")
		for row in frappe.get_all(
			REVIEW,
			filters={"risk_management_file": ["in", sorted({file for file, _t, _n in events})]},
			fields=["risk_management_file", "source_doctype", "source_name", "source_state"],
		)
	}
	for (file, doctype, name), change in sorted(events.items()):
		state = (change.current or "") + ("" if change.in_force else "|out of force")
		if (file, doctype, name, state) in raised:
			continue
		details = (
			_("{0} {1} was pinned at version {2}; it is now at {3}.").format(
				_(doctype), name, change.pin.version, change.current
			)
			if change.in_force
			else _("{0} {1} is no longer in force.").format(_(doctype), name)
		)
		raise_review(file, CONTROL_CHANGED, doctype, name, state=state, details=details)


def raise_periodic_reviews() -> None:
	"""Daily: a review of each file in force whose periodic review has fallen due, once, including
	a day the run did not happen. Each file on its own, so one failure holds back no other."""
	due = frappe.get_all(
		FILE,
		filters={"status": IN_FORCE, "next_periodic_review": ["<=", today()]},
		fields=["name", "next_periodic_review"],
	)
	if not due:
		return
	raised = {
		(row.risk_management_file, row.source_state or "")
		for row in frappe.get_all(
			REVIEW,
			filters={"risk_management_file": ["in", [f.name for f in due]], "trigger": PERIODIC},
			fields=["risk_management_file", "source_state"],
		)
	}
	for row in due:
		state = str(row.next_periodic_review)
		if (row.name, state) in raised:
			continue
		frappe.db.savepoint(REVIEW_SAVEPOINT)
		try:
			raise_review(
				row.name,
				PERIODIC,
				FILE,
				row.name,
				state=state,
				details=_("The periodic review was due on {0}.").format(
					frappe.format_value(row.next_periodic_review, {"fieldtype": "Date"})
				),
			)
		except Exception:
			frappe.db.rollback(save_point=REVIEW_SAVEPOINT)
			frappe.log_error(title=f"Periodic risk review of {row.name} could not be raised")
		else:
			frappe.db.release_savepoint(REVIEW_SAVEPOINT)


# --- Change control (URS-QMS-024) --------------------------------------------------------------


def files_listed(change_request: Document) -> list[str]:
	return sorted(
		{
			row.reference_name
			for row in change_request.get("affected_records") or []
			if row.reference_doctype == FILE and row.reference_name
		}
	)


def require_file_for_risk_impact(change_request: Document) -> None:
	"""Assessing a change: an impact on risk names the risk management file it affects."""
	require(
		change_request.impact_risk != "Yes" or files_listed(change_request),
		_("The change affects risk: list the Risk Management File it affects."),
	)


def raise_change_reviews(change_request: Document) -> None:
	"""An approved change raises a review of each risk management file it lists."""
	for file in files_listed(change_request):
		raise_review(
			file,
			CHANGE_REQUEST_TRIGGER,
			CHANGE_REQUEST,
			change_request.name,
			details=_("Change request {0}: {1}").format(change_request.name, change_request.title),
		)


def open_reviews_of(source_doctype: str, source_name: str) -> list[str]:
	return frappe.get_all(
		REVIEW,
		filters={
			"source_doctype": source_doctype,
			"source_name": source_name,
			"status": OPEN,
			"docstatus": 0,
		},
		pluck="name",
	)
