"""Document control (module 30; ISO 13485 4.2.4; D2 "Document Control").

A controlled document is issued in revisions. A revision is drafted by its
author, then moves on only through signed acts (21 CFR Part 11):

	Draft ──Author──▶ In Review ──Review──▶ Awaiting Approval ──Approve──▶ Approved
	  ▲                    │                      │                           │ on its effective date
	  └──────Return────────┴──────────────────────┘                           ▼
	                                                       Effective ──next revision──▶ Superseded
	                       a withdrawn document's revisions become Obsolete

- the reviewer is never the author, and QA approves, never the author or the reviewer;
- the document file is fingerprinted when it is authored, and approval is refused
  if it has changed since;
- outside QA, people see only effective revisions, and the drafts they author or
  review, so a superseded or withdrawn revision is not used by mistake;
- nothing is ever deleted; a document is withdrawn, with a signature and a reason;
- each effective document is reviewed every review period (ISO 13485 4.2.4 b): QA either starts a
  new revision or signs that the effective revision is still current (`review_document`), which
  moves the next review a review period on without changing the document or the revision;
- a revision is submitted and changed after submission only by these acts, never
  by Frappe's own Submit or an API call; its file cannot be deleted, made public
  or replaced once it is fingerprinted (`compliance.evidence_files`).

The signed acts are registered in `compliance.signing.SIGNING_ACTIONS`.
"""

import hashlib
from pathlib import Path

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_years, getdate, nowdate, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.qms import training
from meddevice_suite.work_management import reminders

IN_PROGRESS = ("Draft", "In Review", "Awaiting Approval", "Approved")
CHUNK = 1 << 20
EFFECTIVE_SAVEPOINT = "document_revision_effective"
REVIEW_SAVEPOINT = "controlled_document_review_alert"


# --- Signed acts ----------------------------------------------------------------------


def author(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.status == "Draft", _("Only a draft can be signed by its author."))
	require(
		frappe.session.user == doc.author, _("Only the author of {0} can sign it as author.").format(doc.name)
	)
	require(doc.file, _("Attach the document file before signing."))
	require(doc.reviewer, _("Choose the reviewer before signing."))
	require(doc.reviewer != doc.author, _("The reviewer cannot be the author."))
	require(
		doc.revision == "00" or (doc.summary_of_change or "").strip(), _("Say what changed in this revision.")
	)
	doc.file_sha256 = file_fingerprint(doc.file)
	doc.status = "In Review"
	# Signed with the fingerprint and status in place, so the signature covers them.
	sign(doc, "Authored", "Author", password, reason)
	save(doc)


def review(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.status == "In Review", _("Only a revision in review can be signed by its reviewer."))
	require(
		frappe.session.user == doc.reviewer,
		_("Only the reviewer of {0} can sign it as reviewer.").format(doc.name),
	)
	doc.status = "Awaiting Approval"
	sign(doc, "Reviewed", "Review", password, reason)
	save(doc)


def return_for_changes(doc: Document, password: str, reason: str | None = None) -> None:
	require(
		doc.status in ("In Review", "Awaiting Approval"),
		_("Only a revision in review or awaiting approval can be returned."),
	)
	require(
		frappe.session.user == doc.reviewer or is_qa(),
		_("Only the reviewer or QA can return {0} for changes.").format(doc.name),
	)
	require(reason, _("Say what must change."))
	doc.status = "Draft"
	sign(doc, "Rejected", "Return", password, reason)
	save(doc)


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	require(doc.status == "Awaiting Approval", _("Only a reviewed revision can be approved."))
	require(is_qa(), _("Only QA can approve a document revision."), frappe.PermissionError)
	require(frappe.session.user != doc.author, _("The author of {0} cannot approve it.").format(doc.name))
	require(
		frappe.session.user != doc.reviewer, _("The reviewer of {0} cannot also approve it.").format(doc.name)
	)
	require(
		file_fingerprint(doc.file) == doc.file_sha256,
		_("The document file has changed since it was signed by its author; it must be authored again."),
	)
	doc.effective_date = max(getdate(doc.effective_date or today()), getdate(today()))
	doc.approver = frappe.session.user
	doc.status = "Approved"
	sign(doc, "Approved", "Approve", password, reason)
	doc.flags.document_control = True
	# Nobody holds Frappe's submit permission on a revision, so its Submit button never
	# offers a way round the signature; the signed approval is the only submit.
	doc.flags.ignore_permissions = True
	doc.submit()
	if getdate(doc.effective_date) <= getdate(today()):
		make_effective(doc.name)
	else:
		# Trained before it takes effect (module 37, plan D-07).
		training.on_document_change(doc.controlled_document)


def withdraw(document: Document, password: str, reason: str | None = None) -> None:
	require(is_qa(), _("Only QA can withdraw a controlled document."), frappe.PermissionError)
	# Held until the request ends, so no revision can be started while the check below holds.
	lock_document(document.name)
	require(document.status != "Obsolete", _("{0} has already been withdrawn.").format(document.name))
	require(reason, _("Say why the document is withdrawn."))
	require(
		not frappe.db.exists(
			"Document Revision", {"controlled_document": document.name, "status": ["in", IN_PROGRESS]}
		),
		_("Finish or return the revision in progress before withdrawing {0}.").format(document.name),
	)
	document.status = "Obsolete"
	document.withdrawn_reason = reason
	document.next_review_date = None
	sign(document, "Withdrawn", "Withdraw", password, reason)
	if document.current_revision:
		frappe.get_doc("Document Revision", document.current_revision).db_set("status", "Obsolete")
	save(document)
	training.on_document_change(document.name)  # nobody is trained on it any more (module 37)


def review_document(document: Document, password: str, reason: str | None = None) -> None:
	"""Signed act, the periodic review (ISO 13485 4.2.4 b): QA, who approves the document's
	revisions, records that its effective revision was reviewed and is still current. Nothing in the
	document or the revision changes; its next review falls one review period from today, which
	settles the review due (module 60). A revision in progress is the review's other outcome, the
	document being updated; it settles the review when it takes effect."""
	require(
		is_qa(),
		_("Only QA, who approves its revisions, reviews {0}.").format(document.name),
		frappe.PermissionError,
	)
	# Held until the request ends, so no revision is started or takes effect while this holds.
	lock_document(document.name)
	document = frappe.get_doc("Controlled Document", document.name)
	require(
		document.status == "Effective" and document.current_revision,
		_("Only a document in effect is reviewed; {0} is {1}.").format(document.name, _(document.status)),
	)
	in_progress = frappe.db.get_value(
		"Document Revision", {"controlled_document": document.name, "status": ["in", IN_PROGRESS]}, "name"
	)
	require(
		not in_progress,
		_("Revision {0} of {1} is in progress; the review is settled when it takes effect.").format(
			in_progress, document.name
		),
	)
	require(reason, _("Say what the review found."))
	revision = frappe.db.get_value("Document Revision", document.current_revision, "revision")
	document.next_review_date = add_years(today(), document.review_period_years)
	# The signature holds who reviewed it, when and what was found; the audit trail the date it moved.
	sign(document, "Reviewed", "Review", password, reason)
	save(document)
	reminders.settle(
		"Document Review",
		"Controlled Document",
		document.name,
		_("Reviewed, still current: revision {0}").format(revision),
		by=frappe.session.user,
		still_due_on=document.next_review_date,
	)


# --- Taking effect ----------------------------------------------------------------------


def make_effective(revision_name: str) -> None:
	"""The revision takes effect, and the one it replaces is superseded."""
	revision = frappe.get_doc("Document Revision", revision_name)
	lock_document(revision.controlled_document)
	document = frappe.get_doc("Controlled Document", revision.controlled_document)
	require(revision.status == "Approved", _("Only an approved revision can take effect."))
	require(
		document.status != "Obsolete",
		_("{0} has been withdrawn; {1} cannot take effect.").format(document.name, revision.name),
	)
	for previous in frappe.get_all(
		"Document Revision",
		filters={"controlled_document": document.name, "status": "Effective", "name": ["!=", revision.name]},
		pluck="name",
	):
		frappe.get_doc("Document Revision", previous).db_set(
			{"status": "Superseded", "superseded_by": revision.name}
		)
	revision.db_set("status", "Effective")
	document.status = "Effective"
	document.current_revision = revision.name
	document.effective_since = revision.effective_date
	document.next_review_date = add_years(revision.effective_date, document.review_period_years)
	save(document)
	training.on_document_change(document.name)  # module 37: training on the revision now in effect


def make_due_revisions_effective() -> None:
	"""Daily: approved revisions whose effective date has come take effect.

	Each revision takes effect on its own, so one that fails is logged for the
	administrator and does not hold back the others; it is retried the next day."""
	for name in frappe.get_all(
		"Document Revision",
		filters={"status": "Approved", "effective_date": ["<=", nowdate()]},
		pluck="name",
		order_by="effective_date asc",
	):
		frappe.db.savepoint(EFFECTIVE_SAVEPOINT)
		try:
			make_effective(name)
		except Exception:
			frappe.db.rollback(save_point=EFFECTIVE_SAVEPOINT)
			frappe.log_error(title=f"Document revision {name} could not take effect")
		else:
			frappe.db.release_savepoint(EFFECTIVE_SAVEPOINT)


# --- Periodic review -------------------------------------------------------------------

REVIEW_ALERT = "Document Review Due"


def send_review_alerts() -> None:
	"""Daily: alert each effective document's process owner and QA once when its review falls
	due within 30 days, including a review already overdue or a day the alerts did not run."""
	notification = frappe.get_doc("Notification", REVIEW_ALERT)
	for name, review_date in frappe.get_all(
		"Controlled Document",
		filters=[
			["status", "=", "Effective"],
			["next_review_date", "is", "set"],
			[
				"next_review_date",
				"<=",
				add_days(today(), quality_settings.value("document_review_warning_days")),
			],
		],
		fields=["name", "next_review_date"],
		as_list=True,
		order_by="next_review_date asc",
	):
		if frappe.db.get_value("Controlled Document", name, "review_alerted_for") == review_date:
			continue
		frappe.db.savepoint(REVIEW_SAVEPOINT)
		try:
			notification.send(frappe.get_doc("Controlled Document", name))
			# Bookkeeping of the alert itself, not a change to the document: not audited.
			frappe.db.set_value(
				"Controlled Document", name, "review_alerted_for", review_date, update_modified=False
			)
		except Exception:
			frappe.db.rollback(save_point=REVIEW_SAVEPOINT)
			frappe.log_error(title=f"Review alert for document {name} could not be sent")
		else:
			frappe.db.release_savepoint(REVIEW_SAVEPOINT)


# --- Who sees which revisions -------------------------------------------------------------


def revision_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if is_qa(user) or "System Manager" in frappe.get_roles(user):
		return ""
	quoted = frappe.db.escape(user)
	return (
		"(`tabDocument Revision`.status = 'Effective'"
		f" or `tabDocument Revision`.author = {quoted} or `tabDocument Revision`.reviewer = {quoted})"
	)


def revision_has_permission(doc: Document, ptype: str | None = None, user: str | None = None) -> bool:
	"""May only narrow the record type's own permissions; Frappe treats anything but True as a refusal."""
	user = user or frappe.session.user
	if is_qa(user) or "System Manager" in frappe.get_roles(user):
		return True
	return doc.is_new() or doc.status == "Effective" or user in (doc.author, doc.reviewer)


# --- Helpers ----------------------------------------------------------------------------


def file_fingerprint(file_url: str) -> str:
	"""SHA-256 of an attached private file, read in chunks."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	require(name, _("The document file {0} is missing; attach it again.").format(file_url))
	file = frappe.get_doc("File", name)
	require(file.is_private, _("Attach the document as a private file."))
	path = Path(file.get_full_path())
	require(path.is_file(), _("The document file {0} is missing; attach it again.").format(file.file_name))
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		while chunk := handle.read(CHUNK):
			digest.update(chunk)
	return digest.hexdigest()


def lock_document(name: str) -> None:
	"""Row-lock a controlled document for the rest of the transaction, so that withdrawing it,
	starting a revision and a revision taking effect never run at the same time."""
	frappe.db.get_value("Controlled Document", name, "name", for_update=True)


def save(doc: Document) -> None:
	# The signed act has done its own checks; the controllers' edit rules are for users.
	doc.flags.document_control = True
	doc.save(ignore_permissions=True)
