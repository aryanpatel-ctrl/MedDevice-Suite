"""Change control (module 36; ISO 13485 4.1.4, 7.3.9; D2 "Change Control").

A change request is raised by someone in a business job, then moves on only
through the acts below; those marked ✍ are electronic signatures (21 CFR Part 11):

	Draft ──submit──▶ Under Assessment ──✍Assess──▶ Awaiting Approval ──✍Approve (QA)──▶ Approved
	  ▲                     │                          │ Major                              │
	  │                     │                          ▼                                   │ each action done
	  └──────✍Return────────┴──────── Awaiting Management Approval ──✍Approve (Mgmt)──┘     ▼
	                     ✍Reject ▶ Rejected       withdraw ▶ Withdrawn         Implemented ──✍Verify──▶ Closed

- QA assesses the impact and classifies the change Minor or Major; a change that needs
  regulatory notification is always Major;
- a Minor change is approved by QA, a Major one by QA and then Management, by two different
  people, and never by the person who raised it; QA, or Management for a Major change awaiting
  it, can return the change to the person who raised it or reject it, with a reason;
- the actions are part of what is approved: once approved they are only completed, each by its
  owner or QA with a note, and the change is implemented when every one is done and what it
  changed is in force (`not_in_force`): a revision effective, a risk assessment approved, an item
  or other master QA approves approved, and each device master record the change put out of date
  approved again (found in the foundation review of 2026-10-12);
- an approved request authorises the change of each record it lists, once (ISO 13485 7.3.9,
  4.2.4): the record the change is made in is recorded against the listing once the change is
  saved (`require_approved_change`, `record_changes_made`), and the request authorises no further
  change of that record; a change abandoned before it took effect (a withdrawn risk assessment)
  leaves the listing unspent (found in the foundation review of 2026-10-12);
- QA closes the change with a signed effectiveness check, on or after its check date;
- nothing is deleted or cancelled; until it is approved, the person who raised it may withdraw it;
- a new revision of an effective controlled document needs an approved change request that
  lists the document (`require_change_for_revision`);
- a change with an impact on risk lists the risk management file it affects; its approval raises
  a risk review of each file it lists, and it is not closed while one is open (module 39,
  URS-QMS-024).

The signed acts are registered in `compliance.signing.SIGNING_ACTIONS`. Who may change which
fields in each status is the controller's (`Change Request.validate`).
"""

from types import ModuleType

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.esign import sign
from meddevice_suite.compliance.fingerprint import plain_text
from meddevice_suite.setup.roles import BUSINESS_ROLES, MANAGEMENT, SYSTEM_ADMINISTRATION

DOCTYPE = "Change Request"
QA_ROLE = "Quality Manager"

# The records a change can affect, and those an action can produce.
AFFECTED_TYPES = (
	"Controlled Document",
	"Item",
	"BOM",
	"Routing",
	"Operation",
	"Workstation",
	"Asset",
	"Supplier",
	"Customer",  # the ninth master (URS-MST-001)
	"Quality Inspection Template",
	"Warehouse",
	"Quality Settings",  # the adjustable values of the system (URS-PLT-012)
	"Risk Management File",  # module 39 (URS-QMS-024)
	"Regulatory Licence",  # a licence or registration the change must be notified under (URS-REG-006)
)
RESULT_TYPES = (*AFFECTED_TYPES, "Document Revision")

# On each listed record: the record its change was made in and when (a document's revision, a
# risk file's assessment, a master itself). Set only by `record_changes_made`.
CHANGE_RECORD_TYPE, CHANGE_RECORD, CHANGED_ON = "change_record_type", "change_record", "changed_on"
CHANGE_RECORD_FIELDS = (CHANGE_RECORD_TYPE, CHANGE_RECORD, CHANGED_ON)
# Pending on the record being saved: (change request, listed type, listed name) of each change the
# save makes under a request, recorded once the save has gone through.
PENDING_FLAG = "changes_under_request"
# A record a change was started in that was given up before it took effect; the listing it spent
# is free again (module 39: a withdrawn assessment no longer holds the change request back).
ABANDONED: dict[str, tuple[str, ...]] = {"Risk Assessment": ("Withdrawn",)}

IMPACT_AREAS = (
	"impact_product_quality",
	"impact_risk",
	"impact_regulatory",
	"impact_validation",
	"impact_training",
	"impact_documents",
	"impact_stock",
)
BEFORE_APPROVAL = ("Draft", "Under Assessment", "Awaiting Approval", "Awaiting Management Approval")
AWAITING_DECISION = ("Under Assessment", "Awaiting Approval", "Awaiting Management Approval")
CLOSED = ("Closed", "Rejected", "Withdrawn")

MAX_CHECK_DAYS = 730
MAX_NOTE = 1000

AWAITING_ASSESSMENT = "Change Request Awaiting Assessment"
AWAITING_APPROVAL = "Change Request Awaiting Approval"
AWAITING_MANAGEMENT = "Change Request Awaiting Management Approval"
DECIDED = "Change Request Decided"
EFFECTIVENESS_DUE = "Change Effectiveness Check Due"
ALERT_SAVEPOINT = "change_request_alert"


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)


def is_management(user: str | None = None) -> bool:
	return bool(set(MANAGEMENT) & set(frappe.get_roles(user or frappe.session.user)))


def sees_every_request(user: str | None = None) -> bool:
	roles = set(frappe.get_roles(user or frappe.session.user))
	return bool(roles & {QA_ROLE, *MANAGEMENT, SYSTEM_ADMINISTRATION})


def in_business_job(user: str | None = None) -> bool:
	return bool(set(frappe.get_roles(user or frappe.session.user)) & BUSINESS_ROLES)


# --- Unsigned acts of the person who raised the change -----------------------------------------


@frappe.whitelist(methods=["POST"])
def submit_for_assessment(name: str) -> None:
	doc = locked(name)
	require(doc.status == "Draft", _("Only a draft change request can be sent for assessment."))
	require(
		frappe.session.user == doc.requested_by,
		_("Only the person who raised {0} can send it for assessment.").format(doc.name),
		frappe.PermissionError,
	)
	require(
		doc.affected_records or doc.change_type == "System",
		_("List the records the change affects."),
	)
	check_listing(doc)
	doc.status = "Under Assessment"
	save(doc)
	alert(AWAITING_ASSESSMENT, doc)


@frappe.whitelist(methods=["POST"])
def withdraw(name: str, reason: str) -> None:
	doc = locked(name)
	require(
		frappe.session.user == doc.requested_by,
		_("Only the person who raised {0} can withdraw it.").format(doc.name),
		frappe.PermissionError,
	)
	require(
		doc.status in BEFORE_APPROVAL,
		_("{0} is {1}; it can no longer be withdrawn.").format(doc.name, _(doc.status)),
	)
	doc.status = "Withdrawn"
	doc.decision_reason = note(reason, _("Say why the change is withdrawn."))
	save(doc)


# --- Signed acts --------------------------------------------------------------------------------


def assess(doc: Document, password: str, reason: str | None = None) -> None:
	doc = locked(doc.name)
	require(doc.status == "Under Assessment", _("Only a change request under assessment can be assessed."))
	require(is_qa(), _("Only QA can assess a change request."), frappe.PermissionError)
	missing = [doc.meta.get_label(field) for field in IMPACT_AREAS if doc.get(field) not in ("Yes", "No")]
	require(not missing, _("Record the impact on: {0}.").format(", ".join(missing)))
	require((doc.impact_summary or "").strip(), _("Summarise the assessment."))
	require(doc.classification in ("Minor", "Major"), _("Classify the change as Minor or Major."))
	check_listing(doc)  # QA may have completed the records while assessing
	require(
		doc.impact_regulatory != "Yes" or doc.classification == "Major",
		_("A change that needs regulatory notification is a Major change."),
	)
	risk_management().require_file_for_risk_impact(doc)  # URS-QMS-024
	from meddevice_suite.qms.regulatory_licence import require_licence_for_regulatory_change

	require_licence_for_regulatory_change(doc)  # URS-REG-006
	require(
		0 <= cint(doc.effectiveness_check_days) <= MAX_CHECK_DAYS,
		_("Check the effectiveness within {0} days of implementation.").format(MAX_CHECK_DAYS),
	)
	check_plan(doc)
	doc.assessed_by = frappe.session.user
	doc.assessed_on = today()
	doc.status = "Awaiting Approval"
	sign(doc, "Reviewed", "Assess", password, reason)
	save(doc)
	alert(AWAITING_APPROVAL, doc)


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	doc = locked(doc.name)
	user = frappe.session.user
	require(user != doc.requested_by, _("The person who raised {0} cannot approve it.").format(doc.name))
	check_plan(doc)
	if doc.status == "Awaiting Approval":
		require(is_qa(), _("Only QA can approve this change request."), frappe.PermissionError)
		doc.qa_approver = user
		if doc.classification == "Major":
			doc.status = "Awaiting Management Approval"
			sign(doc, "Approved", "Approve", password, reason)
			save(doc)
			alert(AWAITING_MANAGEMENT, doc)
			return
	elif doc.status == "Awaiting Management Approval":
		require(
			is_management(), _("Only Management can approve a Major change request."), frappe.PermissionError
		)
		require(
			user != doc.qa_approver,
			_("{0} needs a second approver from Management; you have approved it for QA.").format(doc.name),
		)
		doc.management_approver = user
	else:
		frappe.throw(_("Only an assessed change request awaiting approval can be approved."))
	doc.approved_on = today()
	doc.status = "Approved"
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.change_control = True
	# No role holds Frappe's submit permission on a change request, so its Submit button never
	# offers a way round the signatures; the last approval is the only submit.
	doc.flags.ignore_permissions = True
	doc.submit()
	risk_management().raise_change_reviews(doc)  # URS-QMS-024: a review of each risk file it lists
	alert(DECIDED, doc)


def return_for_changes(doc: Document, password: str, reason: str | None = None) -> None:
	doc = locked(doc.name)
	require_decider(doc)
	require(reason, _("Say what must change."))
	doc.status = "Draft"
	doc.decision_reason = reason
	# The returned request is assessed and approved afresh.
	for field in ("assessed_by", "assessed_on", "qa_approver"):
		doc.set(field, None)
	sign(doc, "Rejected", "Return", password, reason)
	save(doc)
	alert(DECIDED, doc)


def reject(doc: Document, password: str, reason: str | None = None) -> None:
	doc = locked(doc.name)
	require_decider(doc)
	require(reason, _("Say why the change is rejected."))
	doc.status = "Rejected"
	doc.decision_reason = reason
	sign(doc, "Rejected", "Reject", password, reason)
	save(doc)
	alert(DECIDED, doc)


def verify_effective(doc: Document, password: str, reason: str | None = None) -> None:
	verify(doc, "Effective", "Verify Effective", password, reason)


def verify_not_effective(doc: Document, password: str, reason: str | None = None) -> None:
	verify(doc, "Not effective", "Verify Not Effective", password, reason)


def verify(doc: Document, outcome: str, act: str, password: str, reason: str | None) -> None:
	doc = locked(doc.name)
	require(doc.status == "Implemented", _("Only an implemented change can be checked for effectiveness."))
	require(is_qa(), _("Only QA can check the effectiveness of a change."), frappe.PermissionError)
	require(
		getdate(today()) >= getdate(doc.effectiveness_due_on),
		_("The effectiveness of {0} is checked on or after {1}.").format(
			doc.name, frappe.format_value(doc.effectiveness_due_on, {"fieldtype": "Date"})
		),
	)
	reviews = risk_management().open_reviews_of(DOCTYPE, doc.name)
	require(
		not reviews,
		_("Risk review {0} raised by this change is open; QA closes it first.").format(", ".join(reviews)),
	)  # URS-QMS-024
	doc.effectiveness_outcome = outcome
	doc.effectiveness_note = note(reason, _("Say how the effectiveness was checked."))
	doc.closed_on = today()
	doc.status = "Closed"
	sign(doc, "Verified", act, password, doc.effectiveness_note)
	doc.flags.audit_reason = _("Effectiveness checked: {0}").format(_(outcome))
	save(doc)


# --- Implementation ------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def complete_action(
	name: str, action: str, note_text: str, result_doctype: str | None = None, result_name: str | None = None
) -> None:
	"""The owner of an approved action, or QA, records that it is done."""
	doc = locked(name)
	frappe.has_permission(DOCTYPE, "read", doc=doc, throw=True)
	require(
		doc.status == "Approved",
		_("Actions are done once the change is approved, and only until it is implemented."),
	)
	row = next((row for row in doc.actions if row.name == action), None)
	require(row, _("{0} has no such action.").format(doc.name))
	require(not row.done, _("Action {0} of {1} is already done.").format(row.idx, doc.name))
	require(
		frappe.session.user == row.action_owner or is_qa(),
		_("Only the owner of action {0} or QA can record it as done.").format(row.idx),
		frappe.PermissionError,
	)
	if result_doctype or result_name:
		require(result_doctype in RESULT_TYPES and result_name, _("Name the record the action produced."))
		require(
			frappe.db.exists(result_doctype, result_name),
			_("{0} {1} does not exist.").format(_(result_doctype), result_name),
		)
		frappe.has_permission(result_doctype, "read", doc=result_name, throw=True)
	row.update(
		{
			"done": 1,
			"done_on": now_datetime(),
			"done_by": frappe.session.user,
			"completion_note": note(note_text, _("Say what was done.")),
			"result_doctype": result_doctype or None,
			"result_name": result_name or None,
		}
	)
	if all(each.done for each in doc.actions):
		# Implemented means the change is in use: nothing it changed may still wait for its approval
		# or its effective date (found in the foundation review of 2026-10-12).
		pending = not_in_force(doc)
		require(
			not pending,
			_(
				"{0} is implemented once what it changed is in force. Not yet in force: {1}. Record action {2} as done once it is."
			).format(doc.name, "; ".join(pending), row.idx),
		)
		doc.status = "Implemented"
		doc.implemented_on = today()
		doc.effectiveness_due_on = add_days(doc.implemented_on, cint(doc.effectiveness_check_days))
	doc.flags.audit_reason = _("Action {0} done: {1}").format(row.idx, row.completion_note)
	save(doc)


# --- In force ------------------------------------------------------------------------------------


def in_force_rules() -> dict[str, tuple[str, tuple]]:
	"""When a record that carries a change, or that an action produced, is in force: the field that
	says so and the values that do. Any other record type takes effect as it is saved: a supplier, a
	customer, a specification, Quality Settings or a licence has no approval of its own, and its new
	version is in effect from the save (master_data/versioning.py)."""
	# The masters' modules build on this one (versioning names a change request).
	from meddevice_suite.master_data import equipment, item_master, master_approval

	approved_master = (master_approval.APPROVAL_STATUS, (master_approval.APPROVED,))
	return {
		# A superseded revision took effect before a later one replaced it.
		"Document Revision": ("status", ("Effective", "Superseded")),
		"Controlled Document": ("status", ("Effective",)),
		"Risk Assessment": ("status", ("Approved", "Superseded")),
		"Risk Management File": ("status", ("In Force",)),
		"Item": (item_master.APPROVAL_STATUS, (item_master.APPROVED,)),
		"Routing": approved_master,
		"Operation": approved_master,
		"Workstation": approved_master,
		"Warehouse": approved_master,
		"Asset": (equipment.APPROVAL_STATUS, (equipment.APPROVED,)),
		# QA's signed approval is its submit; one cancelled under the change was taken out of force.
		"BOM": ("docstatus", (1, 2)),
	}


def not_in_force(doc: Document) -> list[str]:
	"""What the change made that is not in force yet, each in words: the records its changes were
	made in (a revision approved for a later date, an assessment not yet approved), the records its
	actions produced (an item QA has not approved), and each device master record the change put out
	of date that QA has not approved again (module 31). A change given up before it took effect is
	not counted."""
	made = [
		(row.get(CHANGE_RECORD_TYPE), row.get(CHANGE_RECORD))
		for row in doc.get("affected_records") or []
		if row.get(CHANGE_RECORD)
	]
	made += [
		(row.result_doctype, row.result_name)
		for row in doc.get("actions") or []
		if row.result_doctype and row.result_name
	]
	given_up = abandoned(made)
	rules = in_force_rules()
	by_type: dict[str, list[str]] = {}
	for doctype, name in dict.fromkeys(made):
		if doctype in rules and (doctype, name) not in given_up:
			by_type.setdefault(doctype, []).append(name)
	pending = []
	for doctype, names in by_type.items():
		field, in_force_values = rules[doctype]
		for row in frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field]):
			if row.get(field) not in in_force_values:
				state = _("Draft") if field == "docstatus" else _(row.get(field) or "Draft")
				pending.append(_("{0} {1} ({2})").format(_(doctype), row.name, state))
	pending += [
		_("Device Master Record {0} (out of date with this change; QA approves its next revision)").format(
			name
		)
		for name in records_out_of_date_by(doc.name)
	]
	return pending


def records_out_of_date_by(change_request: str) -> list[str]:
	"""The device master records out of date because an element of theirs changed under the request
	(module 31 records the request on each changed element)."""
	from meddevice_suite.qms import device_master_record  # module 31 builds on this one

	parents = frappe.get_all(
		"Device Master Record Element",
		filters={
			"parenttype": device_master_record.DOCTYPE,
			"stale": 1,
			"current_changed_under": change_request,
		},
		pluck="parent",
	)
	if not parents:
		return []
	return frappe.get_all(
		device_master_record.DOCTYPE,
		filters={
			"name": ["in", sorted(set(parents))],
			"docstatus": 1,
			"status": device_master_record.OUT_OF_DATE,
		},
		pluck="name",
		order_by="name asc",
	)


def abandoned(records: list[tuple[str, str]]) -> set[tuple[str, str]]:
	"""Of the records, those given up before they took effect: one query per type that can be."""
	given_up = set()
	for doctype, statuses in ABANDONED.items():
		if names := sorted({name for record_type, name in records if record_type == doctype}):
			given_up |= {
				(doctype, name)
				for name in frappe.get_all(
					doctype, filters={"name": ["in", names], "status": ["in", statuses]}, pluck="name"
				)
			}
	return given_up


# --- Changes made under an approved request --------------------------------------------------


def require_change_for_revision(revision: Document, document: Document) -> None:
	"""A new revision of a document in effect is started only under an approved change request
	that lists the document (URS-QMS-014). The first revision of a new document needs none."""
	if not document.current_revision:
		return
	require(
		revision.change_request,
		_("{0} is in effect: name the approved change request for this revision.").format(document.name),
	)
	require_approved_change(revision.change_request, "Controlled Document", document.name, made_in=revision)


def require_approved_change(
	change_request: str,
	doctype: str,
	name: str,
	made_in: Document | None = None,
	part_of: tuple[str, str] | None = None,
) -> None:
	"""The change request is approved for implementation, lists the record it changes, and has not
	authorised a change of that record already: an approved request authorises the changes it lists,
	once each (ISO 13485 7.3.9, 4.2.4; found in the foundation review of 2026-10-12).

	`made_in` is the record being saved that makes the change (a document's revision, a risk file's
	assessment, the master itself): once its save has gone through, the change is recorded against
	the listing (`record_changes_made`), so a save refused after this check spends nothing.
	`part_of` names the record of a change already recorded that this one is part of (a variant
	taking on its template's change, under the template's request), which the listing allows."""
	# Locked to the end of the transaction: two changes naming the request at once are checked one
	# after the other, the second seeing the first recorded.
	status = frappe.db.get_value(DOCTYPE, change_request, "status", for_update=True)
	require(
		status == "Approved",
		_("Change request {0} is not approved for implementation.").format(change_request),
	)
	listing = listing_of(change_request, doctype, name)
	require(listing, _("Change request {0} does not list {1}.").format(change_request, name))
	made = (listing.get(CHANGE_RECORD_TYPE), listing.get(CHANGE_RECORD))
	if listing.get(CHANGE_RECORD) and made != part_of and made not in abandoned([made]):
		described = record_label(doctype, name)
		if made != (doctype, name):
			described = _("{0} (in {1} {2})").format(described, _(made[0]), made[1])
		frappe.throw(
			_(
				"Change request {0} has already changed {1}; each change has its own approved change request."
			).format(change_request, described),
			title=_("Change control"),
		)
	if made_in is not None:
		pending = made_in.flags.get(PENDING_FLAG) or []
		made_in.flags[PENDING_FLAG] = [*pending, (change_request, doctype, name)]


def listing_of(change_request: str, doctype: str, name: str) -> frappe._dict | None:
	"""The request's row listing the record; a record listed twice (allowed before 2026-10-12) is
	one listing, its first row."""
	rows = frappe.get_all(
		"Change Request Affected Record",
		filters={
			"parent": change_request,
			"parenttype": DOCTYPE,
			"reference_doctype": doctype,
			"reference_name": name,
		},
		fields=["name", *CHANGE_RECORD_FIELDS],
		order_by="idx asc",
		limit=1,
	)
	return rows[0] if rows else None


def record_label(doctype: str, name: str) -> str:
	# A settings record is named by its type; "Quality Settings Quality Settings" says it twice.
	return _(doctype) if doctype == name else f"{_(doctype)} {name}"


def record_changes_made(doc: Document, method: str | None = None) -> None:
	"""on_change of every record (hooks "*"): each change `require_approved_change` allowed in this
	save is recorded on its request, against the record that made it, now that the save has gone
	through."""
	pending = doc.flags.pop(PENDING_FLAG, None)
	for change_request, doctype, name in dict.fromkeys(pending or []):
		record_change(change_request, doctype, name, doc.doctype, doc.name)


def record_change(
	change_request: str,
	doctype: str,
	name: str,
	made_in_type: str,
	made_in: str,
	reason: str | None = None,
) -> None:
	"""A documented system process: the listing of `doctype` `name` is spent by the change made in
	`made_in_type` `made_in`. Saved as an update after submission with the system's reason, so the
	audit trail records it; the signatures do not bind it (compliance.esign.SYSTEM_MAINTAINED)."""
	request = frappe.get_doc(DOCTYPE, change_request)
	row = next(
		(
			row
			for row in request.affected_records
			if (row.reference_doctype, row.reference_name) == (doctype, name)
		),
		None,
	)
	if row is None or (row.get(CHANGE_RECORD_TYPE), row.get(CHANGE_RECORD)) == (made_in_type, made_in):
		return
	row.update({CHANGE_RECORD_TYPE: made_in_type, CHANGE_RECORD: made_in, CHANGED_ON: now_datetime()})
	request.flags.audit_reason = reason or _("{0} changed in {1} {2}").format(
		record_label(doctype, name), _(made_in_type), made_in
	)
	save(request)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def approved_changes_for(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict
) -> list[tuple[str, str]]:
	"""The lookup of a form that changes a record under change control: approved change requests
	that list the record and have not changed it yet, among those the person may see. A document
	revision names its document (`controlled_document`); a master names itself (`reference_doctype`,
	`reference_name`)."""
	filters = filters or {}
	record_type, document = (
		("Controlled Document", filters.get("controlled_document"))
		if filters.get("controlled_document")
		else (filters.get("reference_doctype"), filters.get("reference_name"))
	)
	if not document:
		return []
	first_rows: dict[str, frappe._dict] = {}
	for row in frappe.get_all(
		"Change Request Affected Record",
		filters={
			"parenttype": DOCTYPE,
			"reference_doctype": record_type,
			"reference_name": document,
		},
		fields=["parent", *CHANGE_RECORD_FIELDS],
		order_by="idx asc",
	):
		first_rows.setdefault(row.parent, row)  # a record listed twice is one listing, its first row
	given_up = abandoned([(row.change_record_type, row.change_record) for row in first_rows.values()])
	listing = [
		parent
		for parent, row in first_rows.items()
		if not row.change_record or (row.change_record_type, row.change_record) in given_up
	]
	if not listing:
		return []
	return frappe.get_list(
		DOCTYPE,
		filters={"name": ["in", listing], "status": "Approved"},
		or_filters={"name": ["like", f"%{txt}%"], "title": ["like", f"%{txt}%"]},
		fields=["name", "title"],
		order_by="approved_on desc",
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)


# --- Alerts ----------------------------------------------------------------------------------


def alert(name: str, doc: Document) -> None:
	frappe.get_doc("Notification", name).send(doc)


def send_change_alerts() -> None:
	"""Daily: alert each action owner once when their action falls due within 7 days or is
	overdue, and QA once when a change's effectiveness check falls due, including a day the
	alerts did not run. Each alert is sent on its own, so one that fails is logged and the
	others still go; it is retried the next day."""
	soon = add_days(today(), quality_settings.value("change_action_warning_days"))
	for row in frappe.get_all(
		"Change Request Action",
		filters={"parenttype": DOCTYPE, "done": 0, "due_date": ["<=", soon]},
		fields=["name", "parent", "idx", "description", "action_owner", "due_date", "alerted_for"],
		order_by="due_date asc",
	):
		if row.alerted_for == row.due_date:
			continue
		if frappe.db.get_value(DOCTYPE, row.parent, "status") != "Approved":
			continue
		each_alert(f"action {row.name} of {row.parent}", lambda row=row: alert_action_owner(row))

	notification = frappe.get_doc("Notification", EFFECTIVENESS_DUE)
	for name in frappe.get_all(
		DOCTYPE,
		filters={
			"status": "Implemented",
			"effectiveness_alerted": 0,
			"effectiveness_due_on": ["<=", today()],
		},
		pluck="name",
	):
		each_alert(
			f"effectiveness check of {name}", lambda name=name: alert_effectiveness(notification, name)
		)


def alert_action_owner(row: frappe._dict) -> None:
	# Frappe offers alerts on a table row only on one exact date, which a day the alerts did not
	# run would miss; the owner of the row is alerted directly instead.
	due = frappe.format_value(row.due_date, {"fieldtype": "Date"})
	enqueue_create_notification(
		row.action_owner,
		{
			"type": "Alert",
			"document_type": DOCTYPE,
			"document_name": row.parent,
			"subject": _("Action {0} of change request {1} is due on {2}").format(row.idx, row.parent, due),
			"email_content": frappe.utils.escape_html(row.description),
		},
	)
	# Bookkeeping of the alert itself, not a change to the action: not audited.
	frappe.db.set_value("Change Request Action", row.name, "alerted_for", row.due_date, update_modified=False)


def alert_effectiveness(notification: Document, name: str) -> None:
	notification.send(frappe.get_doc(DOCTYPE, name))
	frappe.db.set_value(DOCTYPE, name, "effectiveness_alerted", 1, update_modified=False)


def each_alert(what: str, send) -> None:
	frappe.db.savepoint(ALERT_SAVEPOINT)
	try:
		send()
	except Exception:
		frappe.db.rollback(save_point=ALERT_SAVEPOINT)
		frappe.log_error(title=f"Change control alert for {what} could not be sent")
	else:
		frappe.db.release_savepoint(ALERT_SAVEPOINT)


# --- Who sees which requests ---------------------------------------------------------------------


def query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if sees_every_request(user):
		return ""
	quoted = frappe.db.escape(user)
	return (
		f"(`tabChange Request`.requested_by = {quoted} or exists (select 1 from `tabChange Request Action` a"
		" where a.parent = `tabChange Request`.name and a.parenttype = 'Change Request'"
		f" and a.action_owner = {quoted}))"
	)


def has_permission(doc: Document, ptype: str | None = None, user: str | None = None) -> bool:
	"""May only narrow the record type's own permissions; Frappe treats anything but True as a refusal."""
	user = user or frappe.session.user
	if sees_every_request(user) or doc.is_new():
		return True
	return user == doc.requested_by or any(row.action_owner == user for row in doc.get("actions", []))


# --- Helpers ---------------------------------------------------------------------------------


def risk_management() -> ModuleType:
	"""Module 39, which builds on this one (a revision of a risk file names an approved change)."""
	from meddevice_suite.qms import risk_management

	return risk_management


def require_decider(doc: Document) -> None:
	require(
		doc.status in AWAITING_DECISION,
		_("Only a change request under assessment or awaiting approval can be returned or rejected."),
	)
	require(
		is_qa() or (doc.status == "Awaiting Management Approval" and is_management()),
		_("Only QA, or Management for a Major change awaiting its approval, can decide {0}.").format(
			doc.name
		),
		frappe.PermissionError,
	)


def check_listing(doc: Document) -> None:
	"""Each record is listed once: the request authorises one change of each record it lists, and a
	second row would authorise a second change."""
	seen = set()
	for row in doc.get("affected_records") or []:
		key = (row.reference_doctype, row.reference_name)
		require(
			key not in seen,
			_("{0} is listed twice; list each record once.").format(record_label(*key)),
		)
		seen.add(key)


def check_plan(doc: Document) -> None:
	"""The actions approved with a change: at least one, each owned by an active person, due no
	earlier than today."""
	require(doc.actions, _("Plan at least one action to implement the change."))
	for row in doc.actions:
		require(
			frappe.db.get_value("User", row.action_owner, "enabled"),
			_("The owner of action {0} is not an active user.").format(row.idx),
		)
		require(
			getdate(row.due_date) >= getdate(today()),
			_("Action {0} is due in the past; set a new due date.").format(row.idx),
		)


def note(text: str | None, missing: str) -> str:
	text = (text or "").strip()
	require(text, missing)
	require(len(text) <= MAX_NOTE, _("Keep it within {0} characters.").format(MAX_NOTE))
	return plain_text(text)


def locked(name: str) -> Document:
	"""The request as stored, row-locked for the rest of the transaction, so that two people
	acting on it at once act one after the other on its latest state."""
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc


def save(doc: Document) -> None:
	# The act has made its own checks; the controller's edit rules are for people editing the form.
	doc.flags.change_control = True
	require_linked_records_exist(doc)
	# A record the change cancelled (a bill of materials, an equipment record) stays listed and named
	# as where the change was made; Frappe refuses any link to a cancelled document, which would stop
	# every later act on the request. Its links were checked just above, for existence only.
	doc.flags.ignore_links = True
	doc.save(ignore_permissions=True)


def require_linked_records_exist(doc: Document) -> None:
	"""Frappe's own link check on the request and its rows, every record it names to exist,
	cancelled or not."""
	missing = list(doc.get_invalid_links()[0])
	for row in doc.get_all_children():
		missing.extend(row.get_invalid_links(is_submittable=doc.meta.is_submittable)[0])
	if missing:
		frappe.throw(
			_("Could not find {0}").format(", ".join(message for _field, _name, message in missing)),
			frappe.LinkValidationError,
		)


def require(condition, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)
