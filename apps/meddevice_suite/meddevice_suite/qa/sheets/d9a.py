"""What the browser test sheet of D9 Documents starts from, for its controlled documents, important
files, change control and device master record (modules 30, 31, 36, 61; URS-QMS-001 to URS-QMS-016,
URS-QMS-024, URS-QMS-026 to URS-QMS-034, URS-PLT-018, URS-PLT-019, URS-REG-006), on a test site only
(cypress/integration/sheet_d9a.js).

The sheet's tests act on the screens as QA, the Production Supervisor, the QC Inspector, Purchase,
Stores, Sales and Management: documents registered, revised and signed, withdrawn and reviewed;
files kept in categories and versions; changes raised, assessed, approved, implemented and checked;
a device master record authored, approved, returned, withdrawn and put out of date. Each test starts
from records of its own, made here afresh through the code's own services, each step as the job
that does it (`acting_as`), so a test never depends on another having run:

- a controlled document of its own, registered by QA, its revision drafted by its process owner
  (the Production Supervisor), signed by them, reviewed by the QC Inspector and approved by QA, as
  far as the test needs; with a revision in progress under an approved change request, or with its
  periodic review due and the daily reminder run's reminder raised;
- change requests raised by the Production Supervisor and sent for assessment, assessed by QA
  (Minor) and approved, and implemented, as far as the test needs; one listing the risk management
  file of the sheet's product, which its approval puts under review; one whose listing a revision
  has already spent;
- a group and a sub-group of the important files repository, set up by QA, with a file Purchase
  uploaded;
- the sheet's own finished good, with its specifications, approved bill of materials on the
  journey's routing, risk management report in force and Packaging and Labelling document, and its
  Current device master record, drafted by the Production Supervisor and approved by QA
  (setup/fixtures/device_master.py); the product is the sheet's, so putting its record out of date
  leaves the standard flow's product as it is.

The journey's masters (ui_journey.ensure_masters: materials, routing, operations and their work
instructions) are made first. Only the Administrator who runs the tests prepares the sheet.
"""

from contextlib import AbstractContextManager

import frappe
from frappe import _
from frappe.utils import add_days, cint, today

from meddevice_suite.files_repository import files
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	RAW_MATERIALS,
	ROUTING,
	acting_as,
	approve_as_qa,
	approve_specification_as_qa,
	ensure_item_document,
	ensure_masters,
	store,
)
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.qms import change_control, document_control
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.setup.fixtures.device_master import ensure_device_master_record, fill
from meddevice_suite.setup.fixtures.documents import demo_pdf, ensure_work_instruction
from meddevice_suite.setup.fixtures.inspection import (
	ensure_in_process_specification,
	ensure_inspection_template,
)
from meddevice_suite.setup.fixtures.records import ensure_bom, ensure_item
from meddevice_suite.setup.fixtures.risk import ensure_risk_report

QA, QC, PRODUCTION, PURCHASE = "QA Manager", "QC Inspector", "Production Supervisor", "Purchase"
DEPARTMENT = "PRD"
REASON = "Browser test sheet D9"
# The sheet's finished good and what its device master record pins of its own.
PRODUCT = "UIJ-D9-FC-2W-SIL-12"
PRODUCT_DETAILS = ("Foley catheter, 2-way, silicone, 12 Fr (browser test D9)", "90183910", 1825)
PRODUCT_FAMILY = "UIJ Foley catheter, D9 sheet (browser test)"
LABELLING_TITLE = "UIJ D9 labelling and instructions for use (browser test)"
RISK_TAG = "browser test"
# How far a document's first revision is taken (`prepare_document`), and the status it then has.
DOCUMENT_STAGES = {
	"registered": None,
	"drafted": "Draft",
	"in_review": "In Review",
	"awaiting_approval": "Awaiting Approval",
	"effective": "Effective",
}
# How far a change request is taken (`prepare_changes`).
CHANGE_STAGES = ("under_assessment", "awaiting_approval", "approved", "implemented")
DMR_STAGES = ("current", "authored")
# The jobs of the repository's group (D9-FILE-01): Stores reads its files, Purchase uploads them.
READING_JOB, UPLOADING_JOB = "Stock User", "Purchase User"


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


def require_password(password: str | None) -> str:
	if not password:
		frappe.throw(_("Give the run's password of the job users."))
	return password


def act_as(profile: str) -> AbstractContextManager:
	return acting_as(email_for(profile))


def tag() -> str:
	return frappe.generate_hash(length=10)


# --- The sheet's masters ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The journey's masters and the sheet's own finished good with its Current device master
	record; and what the tests work with."""
	require_administrator()
	require_password(password)
	ensure_product(password)
	return {
		"company": COMPANY,
		"department": DEPARTMENT,
		"product": PRODUCT,
		"device_master_record": ensure_current_record(password),
		"labelling": labelling_document(password),
		"risk_file": dmr.risk_file_of(PRODUCT)[0],
		"stores": {
			"approved": store("Stores"),
			"work_in_progress": store("Work In Progress"),
			"quarantine": store("Quarantine"),
		},
	}


def ensure_product(password: str) -> None:
	"""The sheet's finished good, approved with its specifications, its approved default bill of
	materials on the journey's routing, and the risk management report in force of its own file
	(plan D-23), as ui_journey.prepare_device_master_record makes the browser's own product."""
	approve, approve_bom = approve_as_qa(password), approve_as_qa(password, "BOM")
	ensure_masters(approve, approve_bom, approve_as_qa(password, "Asset"), password)
	name, hsn, shelf_life = PRODUCT_DETAILS
	ensure_item(PRODUCT, name, "Products", "Nos", hsn, shelf_life, COMPANY, store("Finished Goods"))
	ensure_inspection_template(PRODUCT, "Products", approve_specification_as_qa(password))
	ensure_item_document(PRODUCT, password)
	approve(PRODUCT)
	ensure_bom(
		PRODUCT, ROUTING, {code: details[5] for code, details in RAW_MATERIALS.items()}, COMPANY, approve_bom
	)
	ensure_in_process_specification(PRODUCT, approve_specification_as_qa(password))
	ensure_risk_report(PRODUCT_FAMILY, [PRODUCT], act_as, email_for, password, RISK_TAG)


def labelling_document(password: str) -> str:
	"""The product's own Packaging and Labelling document, which its record pins."""
	return ensure_work_instruction(LABELLING_TITLE, act_as, email_for, password, "Packaging and Labelling")


def ensure_current_record(password: str) -> str:
	"""The product's Current device master record, with no revision in progress: one an earlier
	test left is withdrawn by QA, and a record out of date is revised, signed and approved again."""
	if in_progress := dmr.revision_in_progress(PRODUCT):
		with act_as(QA):
			dmr.withdraw(
				frappe.get_doc(dmr.DOCTYPE, in_progress),
				password=password,
				reason="Left in progress by an earlier browser test (D9)",
			)
	return ensure_device_master_record(PRODUCT, act_as, email_for, password, LABELLING_TITLE)


# --- Controlled documents (module 30) ------------------------------------------------------------


def new_document(title: str, process_owner: str = PRODUCTION) -> str:
	"""A work instruction QA registers, owned by the job."""
	with act_as(QA):
		return (
			frappe.get_doc(
				{
					"doctype": "Controlled Document",
					"title": title,
					"document_type": "Work Instruction",
					"department": DEPARTMENT,
					"process_owner": email_for(process_owner),
				}
			)
			.insert()
			.name
		)


def private_pdf(title: str) -> str:
	"""A private PDF of its own content, standing in for the document's file; its address."""
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{frappe.scrub(title)}.pdf",
				"is_private": 1,
				"content": demo_pdf(title),
			}
		)
		.insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller
		.file_url
	)


def draft_revision(document: str, change_request: str | None = None, summary: str | None = None) -> str:
	"""The next revision of the document, drafted by its process owner with its file and the QC
	Inspector as reviewer."""
	with act_as(PRODUCTION):
		return (
			frappe.get_doc(
				{
					"doctype": "Document Revision",
					"controlled_document": document,
					"file": private_pdf(f"{document} {tag()}"),
					"reviewer": email_for(QC),
					"change_request": change_request,
					"summary_of_change": summary,
				}
			)
			.insert()
			.name
		)


def advance(revision: str, status: str, password: str) -> None:
	"""The revision signed on, each step by its own person, until it has `status`."""
	steps = (
		("Draft", PRODUCTION, document_control.author),
		("In Review", QC, document_control.review),
		("Awaiting Approval", QA, document_control.approve),
	)
	for before, job, act in steps:
		if frappe.db.get_value("Document Revision", revision, "status") == status:
			return
		if frappe.db.get_value("Document Revision", revision, "status") == before:
			with act_as(job):
				act(frappe.get_doc("Document Revision", revision), password=password, reason=REASON)


def issue_document(password: str, stage: str = "effective") -> dict:
	"""A document of its own and its first revision, taken to the stage."""
	if stage not in DOCUMENT_STAGES:
		frappe.throw(_("The sheet prepares no document {0}.").format(stage))
	title = f"D9 urine bag leak test {tag()} (browser test)"
	document = new_document(title)
	issued = {"document": document, "title": title, "revision": None}
	if stage != "registered":
		issued["revision"] = draft_revision(document)
		advance(issued["revision"], DOCUMENT_STAGES[stage], password)
	return issued


def issue_revision(document: str, change_request: str, password: str) -> str:
	"""The document's next revision under the approved change request, issued by the jobs."""
	revision = draft_revision(
		document, change_request=change_request, summary="Revised under the change request (browser test D9)"
	)
	advance(revision, "Effective", password)
	return revision


@frappe.whitelist(methods=["POST"])
def prepare_document(password: str, stage: str = "effective", in_progress: int = 0) -> dict:
	"""A document of its own, its first revision drafted by its process owner and signed on to the
	stage (D9-DOC-02, 03, 06); `in_progress`: an effective one whose next revision is drafted under
	an approved change request. Returns {"document", "title", "revision", "in_progress"}."""
	require_administrator()
	require_password(password)
	if cint(in_progress) and stage != "effective":
		frappe.throw(_("Only a document in effect has a next revision in progress."))
	issued = issue_document(password, stage)
	issued["in_progress"] = None
	if cint(in_progress):
		change = approved_change(password, [("Controlled Document", issued["document"])])
		issued["in_progress"] = draft_revision(issued["document"], change, "Next revision (browser test D9)")
	return issued


@frappe.whitelist(methods=["POST"])
def prepare_revision_rules(password: str) -> dict:
	"""D9-DOC-05: an effective document, an approved change request listing it and one listing
	another document, and that other document's first revision drafted by its process owner, which
	Sales does not see."""
	require_administrator()
	require_password(password)
	effective = issue_document(password, "effective")
	drafted = issue_document(password, "drafted")
	return {
		"document": effective["document"],
		"revision": effective["revision"],
		"listing_change": approved_change(password, [("Controlled Document", effective["document"])]),
		"other_change": approved_change(password, [("Controlled Document", drafted["document"])]),
		"draft": drafted["revision"],
	}


@frappe.whitelist(methods=["POST"])
def prepare_review_due(password: str) -> dict:
	"""D9-DOC-07: an effective document whose periodic review falls due within the alert's lead
	time, with the reminder the daily run raises for it, and an approved change request listing it
	for its next revision. The review date is brought forward on the test site: a review falls due
	two years after a revision takes effect."""
	from meddevice_suite.work_management import reminders

	require_administrator()
	require_password(password)
	issued = issue_document(password, "effective")
	review_on = add_days(today(), 10)
	# The lifecycle field is set by document control only; the test site stands in for two years.
	frappe.db.set_value("Controlled Document", issued["document"], "next_review_date", review_on)
	reminders.track("Document Review", reminders.CATEGORIES["Document Review"].due)  # the daily run
	return {
		**issued,
		"next_review": str(review_on),
		"reminder": frappe.db.get_value(
			reminders.DOCTYPE,
			{
				"category": "Document Review",
				"reference_doctype": "Controlled Document",
				"reference_name": issued["document"],
				"status": ["in", reminders.STILL_OPEN],
			},
		),
		"change_request": approved_change(password, [("Controlled Document", issued["document"])]),
	}


@frappe.whitelist(methods=["POST"])
def take_effect_now(revision: str) -> str:
	"""D9-CC-07: the day an approved revision's effective date comes, on the test site: its date is
	brought forward to today and the daily run makes it effective. Returns its status."""
	require_administrator()
	if frappe.db.get_value("Document Revision", revision, "status") != "Approved":
		frappe.throw(_("Only an approved revision waits for its effective date."))
	# The test site stands in for the days until the date; the daily run then acts as it would.
	frappe.db.set_value("Document Revision", revision, "effective_date", today())
	document_control.make_effective(revision)
	return frappe.db.get_value("Document Revision", revision, "status")


# --- Change requests (module 36) -------------------------------------------------------------------


def raise_change(records: list[tuple[str, str]], change_type: str = "Document") -> str:
	"""A change request the Production Supervisor raises, listing the records, with one action of
	their own due in a week, and sends for assessment."""
	with act_as(PRODUCTION):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Raise leak test pressure {tag()} (browser test)",
				"change_type": change_type,
				"description": "Leak test pressure raised from 0.3 to 0.4 bar (browser test D9).",
				"reason": "Two leaks found in the field at the lower pressure (browser test D9).",
				"affected_records": [
					{"reference_doctype": doctype, "reference_name": name} for doctype, name in records
				],
				"actions": [
					{
						"description": "Issue the next revision",
						"action_owner": email_for(PRODUCTION),
						"due_date": add_days(today(), 7),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	return request.name


def assess(name: str, password: str, effectiveness_days: int = 0, risk: bool = False) -> None:
	"""QA assesses the change as Minor, with its signature."""
	with act_as(QA):
		request = frappe.get_doc("Change Request", name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_risk": "Yes" if risk else "No",
				"impact_summary": "Within the validated range (browser test D9).",
				"classification": "Minor",
				"effectiveness_check_days": effectiveness_days,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason=REASON)


def approve(name: str, password: str) -> None:
	with act_as(QA):
		change_control.approve(frappe.get_doc("Change Request", name), password=password, reason=REASON)


def implement(name: str) -> None:
	"""The Production Supervisor records the action done, marking as needing no change each listed
	record the sheet's change leaves as it was, as the Record Action Done dialog does (URS-QMS-032)."""
	with act_as(PRODUCTION):
		doc = frappe.get_doc("Change Request", name)
		left_as_it_was = set(change_control.listed_not_changed(doc))
		no_change = [
			row.name
			for row in doc.affected_records
			if change_control.record_label(row.reference_doctype, row.reference_name) in left_as_it_was
		]
		change_control.complete_action(
			name, doc.actions[0].name, "Done (browser test D9)", no_change=no_change or None
		)


def approved_change(password: str, records: list[tuple[str, str]], change_type: str = "Document") -> str:
	name = raise_change(records, change_type)
	assess(name, password)
	approve(name, password)
	return name


@frappe.whitelist(methods=["POST"])
def prepare_changes(password: str, stages: str | list, effectiveness_days: int = 0) -> dict:
	"""Change requests, each raised by the Production Supervisor listing the same effective document
	and taken to its stage: sent for assessment, assessed by QA as Minor, approved, implemented.
	Returns {"document", "revision", "changes": [name per stage]}."""
	require_administrator()
	require_password(password)
	stages = frappe.parse_json(stages) if isinstance(stages, str) else stages
	if not stages or any(stage not in CHANGE_STAGES for stage in stages):
		frappe.throw(_("The sheet prepares change requests only at {0}.").format(", ".join(CHANGE_STAGES)))
	issued = issue_document(password, "effective")
	changes = []
	for stage in stages:
		name = raise_change([("Controlled Document", issued["document"])])
		if stage != "under_assessment":
			assess(name, password, cint(effectiveness_days))
		if stage in ("approved", "implemented"):
			approve(name, password)
		if stage == "implemented":
			implement(name)  # it changed nothing yet, so nothing waits to be in force
		changes.append(name)
	return {"document": issued["document"], "revision": issued["revision"], "changes": changes}


@frappe.whitelist(methods=["POST"])
def prepare_risk_change(password: str) -> dict:
	"""D9-CC-06: a change with an impact on risk listing the risk management file of the sheet's
	product, its effectiveness checked from the day it is implemented, approved by QA (which raises
	a review of the file) and implemented; and a change implemented whose check falls 30 days on."""
	from meddevice_suite.qms import risk_management

	require_administrator()
	require_password(password)
	file = dmr.risk_file_of(PRODUCT)[0]
	if not file:
		frappe.throw(_("Prepare the sheet first: {0} is in no risk management file.").format(PRODUCT))
	name = raise_change([(risk_management.FILE, file)], change_control.CHANGE_TYPE_OF[risk_management.FILE])
	assess(name, password, effectiveness_days=0, risk=True)
	approve(name, password)
	implement(name)
	later = prepare_changes(password, ["implemented"], effectiveness_days=30)["changes"][0]
	return {
		"change_request": name,
		"file": file,
		"review": risk_management.open_reviews_of("Change Request", name)[0],
		"later_change": later,
		"later_due": str(frappe.db.get_value("Change Request", later, "effectiveness_due_on")),
	}


@frappe.whitelist(methods=["POST"])
def prepare_spent_change(password: str) -> dict:
	"""D9-CC-08: an approved change request listing an effective document, whose next revision has
	been issued under it and is in effect: the request has changed the document once."""
	require_administrator()
	require_password(password)
	issued = issue_document(password, "effective")
	change = approved_change(password, [("Controlled Document", issued["document"])])
	revision = issue_revision(issued["document"], change, password)
	return {"document": issued["document"], "change_request": change, "revision": revision}


# --- The important files repository (module 61) ----------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_file_categories(with_file: int = 0) -> dict:
	"""A group QA sets up, whose files Stores reads and Purchase uploads, and a sub-group within it
	naming no jobs of its own; `with_file`: a file of the sub-group, uploaded by Purchase."""
	require_administrator()
	suffix = tag()
	with act_as(QA):
		group = (
			frappe.get_doc(
				{
					"doctype": files.CATEGORY,
					"category_name": f"Licences (test) {suffix}",
					"is_group": 1,
					files.READERS: [{"role": READING_JOB}],
					files.EDITORS: [{"role": UPLOADING_JOB}],
				}
			)
			.insert()
			.name
		)
		sub_group = (
			frappe.get_doc(
				{
					"doctype": files.CATEGORY,
					"category_name": f"CDSCO (test) {suffix}",
					"parent_file_category": group,
				}
			)
			.insert()
			.name
		)
	prepared = {"group": group, "sub_group": sub_group, "file": None, "title": None}
	if cint(with_file):
		title = f"MD-9 licence (test) {suffix}"
		with act_as(PURCHASE):
			prepared["file"] = (
				frappe.get_doc(
					{
						"doctype": files.DOCTYPE,
						"title": title,
						"category": sub_group,
						"new_file": private_pdf(title),
						"new_valid_until": add_days(today(), 30),
					}
				)
				.insert()
				.name
			)
		prepared["title"] = title
	return prepared


# --- The device master record (module 31) ------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def prepare_dmr(password: str, stage: str = "current") -> dict:
	"""The sheet's product with its Current device master record and no revision in progress; at
	"authored", its next revision drafted, completed and signed by the Production Supervisor.
	Returns {"item", "record", "revision"}."""
	require_administrator()
	require_password(password)
	if stage not in DMR_STAGES:
		frappe.throw(_("The sheet prepares no device master record {0}.").format(stage))
	prepared = {"item": PRODUCT, "record": ensure_current_record(password), "revision": None}
	if stage == "authored":
		labelling = labelling_document(password)
		with act_as(PRODUCTION):
			revision = dmr.revise(PRODUCT)
			fill(frappe.get_doc(dmr.DOCTYPE, revision), labelling)
			dmr.author(frappe.get_doc(dmr.DOCTYPE, revision), password=password, reason=REASON)
		prepared["revision"] = revision
	return prepared


@frappe.whitelist(methods=["POST"])
def issue_pinned_document_revision(password: str) -> dict:
	"""D9-DMR-04: the next revision of the Packaging and Labelling document the product's Current
	record pins, issued under an approved change request listing it, effective today: the Production
	Supervisor drafts and signs it, the QC Inspector reviews it and QA approves it."""
	require_administrator()
	require_password(password)
	record = dmr.current_record(PRODUCT)
	if not record:
		frappe.throw(_("{0} has no Current device master record; prepare it first.").format(PRODUCT))
	document = labelling_document(password)
	change = approved_change(password, [("Controlled Document", document)])
	revision = issue_revision(document, change, password)
	return {
		"record": record,
		"document": document,
		"change_request": change,
		"revision": revision,
		"revision_number": frappe.db.get_value("Document Revision", revision, "revision"),
	}
