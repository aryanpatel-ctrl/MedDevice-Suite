"""Device master record, the medical device file (module 31; URS-QMS-026 to URS-QMS-031; master plan;
ISO 13485 4.2.3, 4.2.4, 4.2.5, 7.5.1; 21 CFR 820.181; CDSCO MDR 2017 Device Master File).

Each approved finished good has one device master record in force: the index of everything the
device is made, checked, labelled and released by, under the ISO 13485 4.2.3 headings (plan D-15):
general description and intended use, labelling and instructions for use, product specifications,
manufacture with packaging, storage, handling and distribution, measuring and monitoring,
installation and servicing (for these devices usually not applicable, with the reason), and risk
management. A heading that does not apply says why.

It is issued in revisions, as documents are (module 30):

	Draft ──✍Author──▶ Awaiting Approval ──✍Approve (QA)──▶ Current ──a pinned record changes──▶ Out of Date
	  ▲                       │                                 │ next revision approved            │
	  └───────✍Return─────────┘                                 ▼                                   ▼
	                                                        Superseded ◀────────────────────────────┘
	                    any revision not yet replaced may be ✍Withdrawn (the product is discontinued)

- the revision names its elements, each pinned at the version in force when it is assembled
  (`assemble`): the item itself, its default bill of materials, the bill's routing, each operation
  and its work instruction, the item's final specification and its in-process specification
  (needed when an operation of the routing carries an in-process check, module 3), the risk
  management report in force of the item's risk management file when the item is in one (module
  39), and the effective controlled documents the author chooses, at least one of them a Packaging
  and Labelling document. Nobody types a version: every save of a draft fills the pins afresh;
- the author (QA or the Production Supervisor) signs it Authored; QA, another person, approves it.
  Approval assembles it again and is refused if anything is missing, no longer in force, or has
  moved on since the author signed, if the item is not approved, or if another revision of the
  item is in progress. The approved revision is Current and the one before it Superseded, in the
  same transaction; nothing is deleted or cancelled;
- a new version of a pinned record, its withdrawal, deactivation or replacement as the default
  makes the Current revision Out of Date in the transaction that changes it (qms/pinned_records.py;
  a daily sweep catches changes written past the hooks): each changed element shows its version
  now and the change request it was made under. QA is alerted once, and a "DMR Re-approval"
  reminder stays open until a new revision is approved. Going out of date is a documented system
  process: the signatures bind what was approved, not the status, so they stay intact;
- only a Current revision prints as the controlled index with its signatures; any other prints
  marked "not for use";
- a work order for a finished good is submitted only when the item's device master record is
  Current, still matches the live records and pins the bill of materials the order uses (plan Q8,
  accepted 2026-10-11).

The signed acts are registered in `compliance.signing.SIGNING_ACTIONS`; what a signature binds is
`signed_content` (`compliance.esign.SIGNED_CONTENT`).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.utils import add_days, cint, getdate, now_datetime

from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import item_master, routing, specification
from meddevice_suite.master_data.bom import default_bom
from meddevice_suite.master_data.versioning import CHANGED_UNDER, VERSION
from meddevice_suite.qms import pinned_records
from meddevice_suite.qms.doctype.change_request.change_request import changed_fields

DOCTYPE = "Device Master Record"
ELEMENT, HEADING_ROW, DOCUMENT_ROW = (
	"Device Master Record Element",
	"Device Master Record Heading",
	"Device Master Record Document",
)
QA_ROLE, PRODUCTION_ROLE = "Quality Manager", "Manufacturing User"
# Plan D-15: QA or the Production Supervisor authors a revision; QA approves it.
AUTHOR_ROLES = frozenset({QA_ROLE, PRODUCTION_ROLE})
FLAG = "device_master_record"
OUT_OF_DATE_ALERT = "Device Master Record Out of Date"
SAVEPOINT = "device_master_record"

DRAFT, AWAITING_APPROVAL, CURRENT = "Draft", "Awaiting Approval", "Current"
OUT_OF_DATE, SUPERSEDED, WITHDRAWN = "Out of Date", "Superseded", "Withdrawn"
IN_PROGRESS = (DRAFT, AWAITING_APPROVAL)
# Approved and not yet replaced: the revision the item is made by, or must be re-approved.
IN_USE = (CURRENT, OUT_OF_DATE)

# The ISO 13485 4.2.3 headings, as plan D-15 groups them, in their order.
GENERAL = "General Description and Intended Use"
LABELLING = "Labelling and Instructions for Use"
PRODUCT_SPECIFICATIONS = "Product Specifications"
MANUFACTURE = "Manufacture, Packaging, Storage, Handling and Distribution"
MEASURING = "Measuring and Monitoring"
INSTALLATION = "Installation and Servicing"
RISK = "Risk Management"
HEADINGS = (GENERAL, LABELLING, PRODUCT_SPECIFICATIONS, MANUFACTURE, MEASURING, INSTALLATION, RISK)

# The kinds of element, each filed under its heading.
DEVICE, BILL, ROUTE, OPERATION, WORK_INSTRUCTION = (
	"Device",
	"Bill of Materials",
	"Routing",
	"Operation",
	"Work Instruction",
)
IN_PROCESS_SPECIFICATION, FINAL_SPECIFICATION = "In-process Specification", "Final Specification"
LABELLING_DOCUMENT, RISK_REPORT, DOCUMENT = "Packaging and Labelling", "Risk Management Report", "Document"
ELEMENT_TYPES = (
	DEVICE,
	BILL,
	ROUTE,
	OPERATION,
	WORK_INSTRUCTION,
	IN_PROCESS_SPECIFICATION,
	FINAL_SPECIFICATION,
	LABELLING_DOCUMENT,
	RISK_REPORT,
	DOCUMENT,
)
CONTROLLED_DOCUMENT = pinned_records.CONTROLLED_DOCUMENT
LABELLING_TYPE = "Packaging and Labelling"  # the controlled document type (module 30)
# Where an author's document is filed when the author does not say.
HEADING_OF_DOCUMENT_TYPE = {LABELLING_TYPE: LABELLING, "Specification": PRODUCT_SPECIFICATIONS}
# What each record type is titled by in the index.
TITLE_FIELD = {
	"Item": "item_name",
	"BOM": "item",
	"Routing": "routing_name",
	"Operation": "name",
	pinned_records.SPECIFICATION: "quality_inspection_template_name",
	CONTROLLED_DOCUMENT: "title",
	pinned_records.RISK_REPORT: "family_name",
}

# Set only by the system on the record; whatever a user sends for them is discarded.
SYSTEM_FIELDS = (
	"revision",
	"status",
	"author",
	"approver",
	"approved_on",
	"supersedes",
	"superseded_by",
	"out_of_date_since",
)


def require(condition: object, message: str, exception: type[Exception] = frappe.ValidationError) -> None:
	if not condition:
		frappe.throw(message, exception)


def roles_of(user: str | None = None) -> set[str]:
	return set(frappe.get_roles(user or frappe.session.user))


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in roles_of(user)


def plain(text: str | None) -> str:
	return (text or "").strip()


def lock_item(item: str) -> None:
	"""Row-lock every revision of the item for the rest of the transaction, so that starting,
	approving and superseding its revisions run one after another (one Current revision)."""
	table = frappe.qb.DocType(DOCTYPE)
	frappe.qb.from_(table).select(table.name).where(table.item == item).for_update().run()


def act(doc: Document) -> None:
	# The signed act or the system process has made its own checks; the record's edit rules are
	# for people editing the form.
	doc.flags[FLAG] = True
	doc.flags.ignore_permissions = True


# --- Revisions -----------------------------------------------------------------------------------


def current_record(item: str) -> str | None:
	"""The item's Current revision: one at a time, as approval supersedes the one before."""
	return frappe.db.get_value(DOCTYPE, {"item": item, "status": CURRENT, "docstatus": 1})


def in_use_record(item: str) -> str | None:
	"""The item's approved revision not yet replaced, Current or Out of Date."""
	return frappe.db.get_value(DOCTYPE, {"item": item, "status": ["in", IN_USE], "docstatus": 1})


def revision_in_progress(item: str, other_than: str | None = None) -> str | None:
	return frappe.db.get_value(
		DOCTYPE,
		{
			"item": item,
			"docstatus": 0,
			"status": ["in", IN_PROGRESS],
			"name": ["!=", other_than or ""],
		},
	)


def next_revision(item: str) -> str:
	"""01, 02, … compared as numbers, so 100 follows 99."""
	numbers = [cint(n) for n in frappe.get_all(DOCTYPE, filters={"item": item}, pluck="revision")]
	return f"{max(numbers, default=0) + 1:02d}"


def name_record(doc: Document) -> None:
	doc.name = f"DMR-{doc.item}-{doc.revision}"


def new_record(doc: Document) -> None:
	"""before_insert: QA or the Production Supervisor starts a revision of an approved finished
	good's record, one revision at a time per item."""
	require(
		roles_of() & AUTHOR_ROLES,
		_("Only QA or the Production Supervisor drafts a device master record."),
		frappe.PermissionError,
	)
	require(doc.item, _("Choose the finished good."))
	require(
		frappe.db.get_value("Item", doc.item, item_master.ITEM_TYPE) == item_master.FINISHED_GOOD,
		_("A device master record is kept for a finished good; {0} is not one.").format(doc.item),
	)
	lock_item(doc.item)
	in_progress = revision_in_progress(doc.item)
	require(not in_progress, _("{0} already has a revision in progress: {1}.").format(doc.item, in_progress))
	for field in SYSTEM_FIELDS:
		doc.set(field, None)
	doc.revision = next_revision(doc.item)
	doc.status, doc.author = DRAFT, frappe.session.user
	doc.supersedes = in_use_record(doc.item)
	normalise_headings(doc)


def guard(doc: Document) -> None:
	"""before_validate: approved only by signing; the author edits the draft and nobody else
	changes it, before or after approval, except through the signed acts and the system's own
	process of marking it out of date."""
	if doc.get("_action") == "submit" and not doc.flags.get(FLAG):
		frappe.throw(
			_("{0} {1} is approved only by QA's signature.").format(_(doc.doctype), doc.name or ""),
			frappe.PermissionError,
		)
	if doc.docstatus != 0 or doc.is_new() or doc.flags.get(FLAG):
		return
	before = doc.get_doc_before_save()
	if before.status == DRAFT and frappe.session.user == before.author:
		for field in SYSTEM_FIELDS:
			doc.set(field, before.get(field))
		return
	if changed_fields(doc):
		frappe.throw(
			_("Revision {0} is {1}; only its author changes it, as a draft.").format(
				doc.name, _(before.status)
			),
			frappe.PermissionError,
		)


def validate(doc: Document) -> None:
	"""validate: a draft's headings in their order, and its pins filled from the live records over
	whatever was typed for them (URS-QMS-027)."""
	if doc.docstatus != 0 or doc.status != DRAFT or doc.flags.get(FLAG):
		return  # an act assembles and signs what it saves; a signed revision keeps its pins
	normalise_headings(doc)
	assemble(doc)


def only_by_act(doc: Document) -> None:
	"""before_update_after_submit: an approved revision changes only through its signed acts and
	the documented system process that marks it out of date."""
	require(
		doc.flags.get(FLAG),
		_("{0} is approved; it changes only through its signed acts.").format(doc.name),
		frappe.PermissionError,
	)


def refuse_cancel(doc: Document) -> None:
	frappe.throw(
		_("A device master record is superseded or withdrawn, never cancelled."), frappe.PermissionError
	)


def refuse_delete(doc: Document) -> None:
	frappe.throw(_("A device master record is never deleted."), frappe.PermissionError)


def normalise_headings(doc: Document) -> None:
	"""Every heading once, in the order of ISO 13485 4.2.3; what was written under each is kept."""
	written = {}
	for row in doc.get("headings") or []:
		if row.heading in HEADINGS and row.heading not in written:
			written[row.heading] = {"not_applicable": cint(row.not_applicable), "content": plain(row.content)}
	replace_rows(
		doc,
		"headings",
		[
			{"heading": heading, **written.get(heading, {"not_applicable": 0, "content": ""})}
			for heading in HEADINGS
		],
		("heading",),
	)
	unfiled = [row for row in doc.get("documents") or [] if row.heading not in HEADINGS]
	if unfiled:
		types = dict(
			frappe.get_all(
				CONTROLLED_DOCUMENT,
				filters={"name": ["in", [row.controlled_document or "" for row in unfiled]]},
				fields=["name", "document_type"],
				as_list=True,
			)
		)
		for row in unfiled:
			row.heading = HEADING_OF_DOCUMENT_TYPE.get(types.get(row.controlled_document), MANUFACTURE)


@frappe.whitelist(methods=["POST"])
def revise(item: str) -> str:
	"""Start the next revision of the item's record from the one approved, keeping what its author
	wrote and chose; the pins are filled afresh from the live records."""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	frappe.get_doc("Item", item).check_permission("read")
	doc = frappe.new_doc(DOCTYPE)
	doc.item = item
	if previous := frappe.db.get_value(
		DOCTYPE,
		{"item": item, "docstatus": 1, "status": ["in", (*IN_USE, SUPERSEDED)]},
		order_by="creation desc",
	):
		earlier = frappe.get_doc(DOCTYPE, previous)
		earlier.check_permission("read")
		doc.set(
			"headings",
			[
				{"heading": row.heading, "not_applicable": row.not_applicable, "content": row.content}
				for row in earlier.headings
			],
		)
		doc.set(
			"documents",
			[
				{"controlled_document": row.controlled_document, "heading": row.heading}
				for row in earlier.documents
			],
		)
		if changed := [
			f"{_(row.reference_doctype)} {row.reference_name}: {row.change_note}"
			for row in earlier.elements
			if row.stale
		]:
			# What made the record out of date, for the author to complete and sign.
			doc.summary_of_change = _("Brought up to date after: {0}").format("; ".join(changed))
	doc.insert()
	return doc.name


# --- Assembling the elements (URS-QMS-026, URS-QMS-027) ------------------------------------------


def assemble(doc: Document) -> list[str]:
	"""Fill the elements from the live records, each pinned at its version in force, and return
	what the record still lacks. The bill of materials, routing, operations, work instructions,
	specifications and risk report are found by the system; the author's documents are the ones
	chosen on the record."""
	rows: list[dict] = []
	missing: list[str] = []

	def add(element_type: str, doctype: str, name: str, heading: str) -> None:
		rows.append(
			{
				"element_type": element_type,
				"reference_doctype": doctype,
				"reference_name": name,
				"heading": heading,
			}
		)

	add(DEVICE, "Item", doc.item, GENERAL)
	in_process_checked: list[str] = []
	bill = default_bom(doc.item)
	if not bill:
		missing.append(_("the default bill of materials, approved by QA"))
	else:
		add(BILL, "BOM", bill, PRODUCT_SPECIFICATIONS)
		line = frappe.db.get_value("BOM", bill, "routing")
		if line:
			add(ROUTE, "Routing", line, MANUFACTURE)
		else:
			missing.append(_("the routing of {0}").format(bill))
		operations = list(
			dict.fromkeys(
				frappe.get_all(
					"BOM Operation",
					filters={"parent": bill, "parenttype": "BOM"},
					pluck="operation",
					order_by="idx asc",
				)
			)
		)
		if not operations:
			missing.append(_("the operations of {0}").format(bill))
		details = {
			row.name: row
			for row in frappe.get_all(
				"Operation",
				filters={"name": ["in", operations or [""]]},
				fields=["name", routing.WORK_INSTRUCTION, routing.IN_PROCESS_CHECK],
			)
		}
		for operation in operations:
			add(OPERATION, "Operation", operation, MANUFACTURE)
			found = details.get(operation) or {}
			if instruction := found.get(routing.WORK_INSTRUCTION):
				add(WORK_INSTRUCTION, CONTROLLED_DOCUMENT, instruction, MANUFACTURE)
			else:
				missing.append(_("the work instruction of operation {0}").format(operation))
			if found.get(routing.IN_PROCESS_CHECK) == "Yes":
				in_process_checked.append(operation)
	in_process = specification.specification_for(doc.item, specification.IN_PROCESS)
	if in_process:
		add(IN_PROCESS_SPECIFICATION, pinned_records.SPECIFICATION, in_process, MEASURING)
	elif in_process_checked:
		missing.append(
			_("the in-process specification of {0} (operations {1} carry an in-process check)").format(
				doc.item, ", ".join(in_process_checked)
			)
		)
	if final := specification.specification_for(doc.item, specification.FINAL):
		add(FINAL_SPECIFICATION, pinned_records.SPECIFICATION, final, MEASURING)
	else:
		missing.append(_("the final specification of {0}").format(doc.item))
	missing += add_documents(doc, add)
	missing += add_risk_report(doc.item, add)
	replace_rows(doc, "elements", pinned(rows), ("element_type", "reference_doctype", "reference_name"))
	return missing


def replace_rows(doc: Document, table: str, rows: list[dict], key: tuple[str, ...]) -> None:
	"""Set a table to `rows`, keeping the stored rows when they are the same records in the same
	order, so that a save that changes nothing leaves the audit trail nothing to record."""
	existing = doc.get(table) or []
	if [tuple(row.get(f) for f in key) for row in existing] == [
		tuple(row.get(f) for f in key) for row in rows
	]:
		for row, values in zip(existing, rows, strict=True):
			row.update(values)
	else:
		doc.set(table, rows)


def add_documents(doc: Document, add) -> list[str]:
	"""The author's documents, a Packaging and Labelling one among them (plan D-15, Q7)."""
	chosen = [row for row in doc.get("documents") or [] if row.controlled_document]
	types = dict(
		frappe.get_all(
			CONTROLLED_DOCUMENT,
			filters={"name": ["in", [row.controlled_document for row in chosen] or [""]]},
			fields=["name", "document_type"],
			as_list=True,
		)
	)
	for row in chosen:
		labelling = types.get(row.controlled_document) == LABELLING_TYPE
		add(
			LABELLING_DOCUMENT if labelling else DOCUMENT,
			CONTROLLED_DOCUMENT,
			row.controlled_document,
			row.heading,
		)
	if LABELLING_TYPE in types.values():
		return []
	return [_("at least one effective Packaging and Labelling document")]


def add_risk_report(item: str, add) -> list[str]:
	"""The report in force of the item's risk management file, when the item is in one (module 39)."""
	file = frappe.db.get_value(
		"Risk Management File Item", {"parenttype": pinned_records.RISK_FILE, "item": item}, "parent"
	)
	if not file:
		return []
	report = frappe.db.get_value(pinned_records.RISK_FILE, file, "current_report")
	if not report:
		return [_("the risk management report in force of {0}").format(file)]
	add(RISK_REPORT, pinned_records.RISK_REPORT, report, RISK)
	return []


def pinned(rows: list[dict]) -> list[dict]:
	"""Each record once, with the version in force, its label, its title and the change request
	that version was made under, read from the live records: one query per record type."""
	first: dict[tuple[str, str], dict] = {}
	for row in rows:
		first.setdefault((row["reference_doctype"], row["reference_name"]), row)
	unique = list(first.values())
	by_type: dict[str, list[str]] = {}
	for row in unique:
		by_type.setdefault(row["reference_doctype"], []).append(row["reference_name"])
	tokens = {doctype: pinned_records.version_tokens(doctype, names) for doctype, names in by_type.items()}
	about = {doctype: describe(doctype, names) for doctype, names in by_type.items()}
	for row in unique:
		known = about[row["reference_doctype"]].get(row["reference_name"]) or {}
		row.update(
			{
				"pinned_version": tokens[row["reference_doctype"]].get(row["reference_name"]),
				"pinned_revision": known.get("label"),
				"title": known.get("title"),
				"changed_under": known.get("changed_under"),
				"stale": 0,
				"current_version": None,
				"current_changed_under": None,
				"change_note": None,
			}
		)
	return unique


def describe(doctype: str, names: list[str]) -> dict[str, dict]:
	"""For each record: its title, the version a person reads (a revision, a version number) and
	the change request that version was made under."""
	title = TITLE_FIELD[doctype]
	if doctype == CONTROLLED_DOCUMENT:
		documents = frappe.get_all(
			doctype, filters={"name": ["in", names]}, fields=["name", "title", "current_revision"]
		)
		revisions = {
			row.name: row
			for row in frappe.get_all(
				"Document Revision",
				filters={
					"name": ["in", [d.current_revision for d in documents if d.current_revision] or [""]]
				},
				fields=["name", "revision", "change_request"],
			)
		}
		described = {}
		for row in documents:
			revision = revisions.get(row.current_revision) or {}
			described[row.name] = {
				"title": row.title,
				"label": _("Revision {0}").format(revision.get("revision")) if revision else None,
				"changed_under": revision.get("change_request"),
			}
		return described
	if doctype == pinned_records.RISK_REPORT:
		return {
			row.name: {"title": row.family_name, "label": _(row.status), "changed_under": None}
			for row in frappe.get_all(
				doctype, filters={"name": ["in", names]}, fields=["name", "family_name", "status"]
			)
		}
	fields = list(dict.fromkeys(["name", title, VERSION, CHANGED_UNDER]))
	return {
		row.name: {
			"title": row.get(title),
			"label": _("Version {0}").format(cint(row.get(VERSION))),
			"changed_under": row.get(CHANGED_UNDER),
		}
		for row in frappe.get_all(doctype, filters={"name": ["in", names]}, fields=fields)
	}


def out_of_force(doc: Document) -> list[str]:
	"""The elements whose record may no longer be relied on."""
	by_type: dict[str, set[str]] = {}
	for row in doc.elements or []:
		by_type.setdefault(row.reference_doctype, set()).add(row.reference_name)
	live = {doctype: pinned_records.in_force_names(doctype, names) for doctype, names in by_type.items()}
	return [
		f"{_(row.reference_doctype)} {row.reference_name}"
		for row in doc.elements or []
		if row.reference_name not in live[row.reference_doctype]
	]


def what_is_lacking(doc: Document) -> list[str]:
	"""What stands in the way of signing: the item approved, every heading written, every required
	element there and in force (URS-QMS-026, URS-QMS-027)."""
	lacking = assemble(doc)
	for row in doc.headings or []:
		if not plain(row.content):
			lacking.append(
				_('the heading "{0}": describe it, or say why it does not apply').format(_(row.heading))
			)
	if out := out_of_force(doc):
		lacking.append(_("records in force instead of {0}").format(", ".join(out)))
	return lacking


def require_signable(doc: Document) -> None:
	require(
		item_master.approved(doc.item),
		_("{0} is not approved by QA; its record is not signed.").format(doc.item),
	)
	require(not item_master_disabled(doc.item), _("{0} is disabled.").format(doc.item))
	# Only a revision that follows an approved one says what changed; withdrawn drafts do not count.
	approved_before = frappe.db.exists(DOCTYPE, {"item": doc.item, "docstatus": 1, "name": ["!=", doc.name]})
	require(not approved_before or plain(doc.summary_of_change), _("Say what changed in this revision."))
	normalise_headings(doc)
	lacking = what_is_lacking(doc)
	require(not lacking, _("{0} cannot be signed yet; it lacks {1}.").format(doc.name, "; ".join(lacking)))


def item_master_disabled(item: str) -> bool:
	return bool(frappe.db.get_value("Item", item, "disabled"))


# --- The signed acts (URS-QMS-028) ---------------------------------------------------------------


def author(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the author signs the complete draft as they wrote it, with its pins."""
	require(doc.docstatus == 0 and doc.status == DRAFT, _("Only a draft is signed by its author."))
	require(
		frappe.session.user == doc.author,
		_("Only the author of {0} signs it as author.").format(doc.name),
		frappe.PermissionError,
	)
	lock_item(doc.item)
	require_signable(doc)
	doc.status = AWAITING_APPROVAL
	sign(doc, "Authored", "Author", password, reason)
	act(doc)
	doc.save()


def pins_of_record(doc: Document) -> list[tuple]:
	return [
		(row.element_type, row.reference_doctype, row.reference_name, row.pinned_version)
		for row in doc.elements or []
	]


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA, not the author, approves the revision. It is assembled again from the live
	records and refused if anything is missing, no longer in force, or not what the author signed;
	it is then the item's Current record and the one before it is superseded (URS-QMS-028)."""
	require(
		doc.docstatus == 0 and doc.status == AWAITING_APPROVAL,
		_("Only a revision awaiting approval is approved."),
	)
	require(is_qa(), _("Only QA approves a device master record."), frappe.PermissionError)
	require(frappe.session.user != doc.author, _("The author of {0} cannot approve it.").format(doc.name))
	lock_item(doc.item)
	other = revision_in_progress(doc.item, other_than=doc.name)
	require(not other, _("{0} also has revision {1} in progress.").format(doc.item, other))
	signed_pins = pins_of_record(doc)
	require_signable(doc)  # assembles it again from the live records
	moved = sorted({f"{_(pin[1])} {pin[2]}" for pin in set(pins_of_record(doc)) ^ set(signed_pins)})
	require(
		not moved,
		_("{0} changed since the author signed; return the revision for changes: {1}.").format(
			doc.name, ", ".join(moved)
		),
	)
	doc.status, doc.approver, doc.approved_on = CURRENT, frappe.session.user, now_datetime()
	sign(doc, "Approved", "Approve", password, reason, permission="read")
	act(doc)  # nobody holds Frappe's submit permission: the signed approval is the only submit
	doc.submit()
	supersede_previous(doc)


def supersede_previous(doc: Document) -> None:
	"""The item's revision approved before this one is superseded by it, in the same transaction:
	one revision is Current."""
	for previous in frappe.get_all(
		DOCTYPE,
		filters={"item": doc.item, "docstatus": 1, "status": ["in", IN_USE], "name": ["!=", doc.name]},
		pluck="name",
	):
		earlier = frappe.get_doc(DOCTYPE, previous)
		earlier.status, earlier.superseded_by = SUPERSEDED, doc.name
		act(earlier)  # part of the approval of the revision that replaces it
		earlier.flags.audit_reason = _("Superseded by {0}").format(doc.name)
		earlier.save()


def return_for_changes(doc: Document, password: str, reason: str | None = None) -> None:
	require(
		doc.docstatus == 0 and doc.status == AWAITING_APPROVAL,
		_("Only a revision awaiting approval is returned."),
	)
	require(is_qa(), _("Only QA returns a device master record for changes."), frappe.PermissionError)
	require(plain(reason), _("Say what must change."))
	doc.status = DRAFT
	sign(doc, "Rejected", "Return", password, reason, permission="read")
	act(doc)
	doc.save()


def withdraw(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: QA withdraws a revision not yet replaced: a draft abandoned, or the record of a
	product no longer made. A withdrawn record is kept, and no work order is made on it."""
	require(
		doc.status in (*IN_PROGRESS, *IN_USE) and doc.docstatus in (0, 1),
		_("{0} is {1}; it is not withdrawn.").format(doc.name, _(doc.status)),
	)
	require(is_qa(), _("Only QA withdraws a device master record."), frappe.PermissionError)
	require(plain(reason), _("Say why the record is withdrawn."))
	lock_item(doc.item)
	doc.status = WITHDRAWN
	sign(doc, "Rejected", "Withdraw", password, reason, permission="read")
	act(doc)
	doc.flags.audit_reason = plain(reason)  # the audit trail records the withdrawal under QA's reason
	doc.save()


# --- What a signature binds -----------------------------------------------------------------------


def signed_content(doc: Document) -> dict:
	"""What the author's and QA's signatures bind (compliance.esign.SIGNED_CONTENT): the item, the
	revision, what was written under each heading, and each element with the version it was pinned
	at; not the status, nor what the system records as the record goes out of date (which elements
	are stale, their versions now, since when), so the signatures stay intact when it does."""
	return {
		"item": doc.item,
		"revision": doc.revision,
		"supersedes": doc.supersedes or None,
		"summary_of_change": plain(doc.summary_of_change) or None,
		"headings": [
			[row.heading, cint(row.not_applicable), plain(row.content)] for row in doc.get("headings") or []
		],
		"elements": [
			[row.element_type, row.heading, row.reference_doctype, row.reference_name, row.pinned_version]
			for row in doc.get("elements") or []
		],
	}


# --- Out of date (URS-QMS-029) ---------------------------------------------------------------------


def element_pins(targets: dict[str, list[str]] | None) -> list[pinned_records.Pin]:
	"""Pinned records (qms/pinned_records.py): the elements of every Current record, or those on
	the target records."""
	record, element = frappe.qb.DocType(DOCTYPE), frappe.qb.DocType(ELEMENT)
	query = (
		frappe.qb.from_(record)
		.join(element)
		.on((element.parent == record.name) & (element.parenttype == DOCTYPE))
		.select(
			record.name.as_("record"),
			element.reference_doctype,
			element.reference_name,
			element.pinned_version,
			element.name,
		)
		.where((record.status == CURRENT) & (record.docstatus == 1))
	)
	if targets is not None:
		if not any(targets.values()):
			return []
		query = query.where(
			Criterion.any(
				(element.reference_doctype == doctype) & element.reference_name.isin(names)
				for doctype, names in targets.items()
				if names
			)
		)
	return [
		pinned_records.Pin(
			row.record, row.reference_doctype, row.reference_name, row.pinned_version, row.name
		)
		for row in query.run(as_dict=True)
		if row.reference_doctype in pinned_records.PINNABLE
	]


def elements_changed(changes: list[pinned_records.Change]) -> None:
	"""Each Current record with a changed element goes out of date once, on its own, so that one
	record's failure (logged; the daily sweep finds it again) holds back no other."""
	by_record: dict[str, list[pinned_records.Change]] = {}
	for change in changes:
		by_record.setdefault(change.pin.owner, []).append(change)
	for record, its_changes in sorted(by_record.items()):
		frappe.db.savepoint(SAVEPOINT)
		try:
			mark_out_of_date(record, its_changes)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"Device master record {record} could not be marked out of date")
		else:
			frappe.db.release_savepoint(SAVEPOINT)


def mark_out_of_date(name: str, changes: list[pinned_records.Change]) -> bool:
	"""A documented system process: the Current record is marked Out of Date, each changed element
	with its version now and the change request it was made under, and QA is alerted once. Saved
	as an update after submission with the system's reason, so the audit trail records it; returns
	whether it was marked (a record out of date already is left as it is)."""
	table = frappe.qb.DocType(DOCTYPE)
	frappe.qb.from_(table).select(table.name).where(table.name == name).for_update().run()
	doc = frappe.get_doc(DOCTYPE, name)
	if doc.docstatus != 1 or doc.status != CURRENT:
		return False
	by_type: dict[str, list[str]] = {}
	for change in changes:
		by_type.setdefault(change.pin.doctype, []).append(change.pin.name)
	now = {doctype: describe(doctype, names) for doctype, names in by_type.items()}
	rows = {row.name: row for row in doc.elements}
	notes = []
	for change in changes:
		row = rows.get(change.pin.row)
		if row is None:
			continue
		known = now[change.pin.doctype].get(change.pin.name) or {}
		note = (
			_("now {0}").format(known.get("label") or change.current)
			if change.in_force
			else _("no longer in force")
		)
		row.stale, row.current_version, row.change_note = 1, change.current, note
		row.current_changed_under = known.get("changed_under")
		notes.append(f"{_(row.reference_doctype)} {row.reference_name}: {note}")
	doc.status, doc.out_of_date_since = OUT_OF_DATE, now_datetime()
	act(doc)  # a system process, for whoever's change caused it
	doc.flags.audit_reason = _("Out of date: a pinned record changed ({0})").format("; ".join(notes))[:900]
	doc.save()
	alert(doc)
	return True


def alert(doc: Document) -> None:
	"""QA is told once, when the record goes out of date; a failure is logged and does not hold
	back the marking, which the reminder keeps in view."""
	frappe.db.savepoint(f"{SAVEPOINT}_alert")
	try:
		frappe.get_doc("Notification", OUT_OF_DATE_ALERT).send(doc)
	except Exception:
		frappe.db.rollback(save_point=f"{SAVEPOINT}_alert")
		frappe.log_error(title=f"Out-of-date alert for {doc.name} could not be sent")
	else:
		frappe.db.release_savepoint(f"{SAVEPOINT}_alert")


def changes_since_approval(name: str) -> list[pinned_records.Change]:
	"""The elements of a record whose record has moved on since they were pinned, looked up now."""
	pins = [
		pinned_records.Pin(name, row.reference_doctype, row.reference_name, row.pinned_version, row.name)
		for row in frappe.get_all(
			ELEMENT,
			filters={"parent": name, "parenttype": DOCTYPE},
			fields=["name", "reference_doctype", "reference_name", "pinned_version"],
		)
		if row.reference_doctype in pinned_records.PINNABLE
	]
	return pinned_records.changes_of(pins)


def reapprovals_due(days: int) -> list[frappe._dict]:
	"""Records out of date, with the day their re-approval is due (Quality Settings)."""
	return [
		frappe._dict(row, due_date=add_days(getdate(row.out_of_date_since), days))
		for row in frappe.get_all(
			DOCTYPE,
			filters={"docstatus": 1, "status": OUT_OF_DATE},
			fields=["name", "item", "item_name", "out_of_date_since"],
		)
	]


# --- Production (URS-QMS-031) ------------------------------------------------------------------------


def guard_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order before_validate when it is being submitted (which also runs when a caller skips
	validation) and before_submit: a finished good is put into production only on its Current
	device master record, which pins the bill of materials the order uses (plan Q8)."""
	if method == "before_validate" and doc.get("_action") != "submit":
		return
	require_current_record(doc)


def require_current_record(doc: Document) -> None:
	item = doc.get("production_item")
	if not item or frappe.db.get_value("Item", item, item_master.ITEM_TYPE) != item_master.FINISHED_GOOD:
		return
	record = current_record(item)
	require(
		record,
		_(
			"{0} has no Current device master record; QA approves it before {0} is put into production."
		).format(item),
	)
	require(
		frappe.db.exists(
			ELEMENT,
			{
				"parent": record,
				"parenttype": DOCTYPE,
				"reference_doctype": "BOM",
				"reference_name": doc.get("bom_no") or "",
			},
		),
		_(
			"Device master record {0} does not name bill of materials {1}; production uses the one it names."
		).format(record, doc.get("bom_no") or _("none was given")),
	)
	moved = changes_since_approval(record)
	require(
		not moved,
		_("Device master record {0} no longer matches {1}; QA re-approves it before production.").format(
			record, ", ".join(f"{_(c.pin.doctype)} {c.pin.name}" for c in moved)
		),
	)
