"""Demo work instructions and approved production masters (modules 3 and 30), for the pilot site
and the browser journey's test site.

Made as the system makes them: the Production Supervisor authors a work instruction, the QC
Inspector reviews it and QA approves it, each with a signature, so it is in effect; QA then
approves the workstations, the operations (each naming its work instruction) and the routing with
signatures (master_data/routing.py). The documents that define the other masters are issued the
same way (plan D-34): the test method each specification names, before QA approves it with a
signature (module 6, plan D-33), and the specification of each purchased material and finished
good, before QA approves the item (module 1). Everything is made only if missing.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from io import BytesIO

import frappe

from meddevice_suite.master_data import item_master, master_approval, routing, specification

AUTHOR, REVIEWER, APPROVER = "Production Supervisor", "QC Inspector", "QA Manager"
DEPARTMENT = "PRD"


def demo_pdf(text: str) -> bytes:
	"""A one-page PDF standing in for the document's file (Frappe validates uploaded PDFs)."""
	from pypdf import PdfWriter  # Frappe's own PDF library

	writer, out = PdfWriter(), BytesIO()
	writer.add_blank_page(width=595, height=842)
	writer.add_metadata({"/Title": text})
	writer.write(out)
	return out.getvalue()


def ensure_work_instruction(
	title: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
	document_type: str = routing.WORK_INSTRUCTION_TYPE,
) -> str:
	"""An effective work instruction of `title` (or a document of another type), issued through
	document control (module 30). A revision left part-way by an earlier run is taken on from where
	it stopped."""
	from meddevice_suite.qms import document_control

	existing = frappe.get_all(
		"Controlled Document",
		filters={
			"title": title,
			"document_type": document_type,
			"status": ["!=", "Obsolete"],
		},
		fields=["name", "status"],
		order_by="creation asc",
		limit=1,
	)
	if existing and existing[0].status == "Effective":
		return existing[0].name
	if existing:
		document = existing[0].name
	else:
		with act_as(APPROVER):
			document = (
				frappe.get_doc(
					{
						"doctype": "Controlled Document",
						"title": title,
						"document_type": document_type,
						"department": DEPARTMENT,
						"process_owner": user_of(AUTHOR),
					}
				)
				.insert()
				.name
			)
	in_progress = frappe.get_all(
		"Document Revision",
		filters={"controlled_document": document, "status": ["in", document_control.IN_PROGRESS]},
		pluck="name",
		limit=1,
	)
	if in_progress:
		revision = in_progress[0]
	else:
		with act_as(AUTHOR):
			file_url = (
				frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"{frappe.scrub(title)}.pdf",
						"is_private": 1,
						"content": demo_pdf(f"{title} (demo)"),
					}
				)
				.insert(ignore_permissions=True)  # demo loader
				.file_url
			)
			revision = (
				frappe.get_doc(
					{
						"doctype": "Document Revision",
						"controlled_document": document,
						"file": file_url,
						"reviewer": user_of(REVIEWER),
					}
				)
				.insert()
				.name
			)
	# Each step signed by its own person, from where the revision stands.
	steps = (
		("Draft", AUTHOR, document_control.author, "Written for the demo"),
		("In Review", REVIEWER, document_control.review, "Reviewed (demo)"),
		("Awaiting Approval", APPROVER, document_control.approve, "Approved (demo)"),
	)
	for status, job, act, reason in steps:
		if frappe.db.get_value("Document Revision", revision, "status") == status:
			with act_as(job):
				act(frappe.get_doc("Document Revision", revision), password=password, reason=reason)
	if frappe.db.get_value("Document Revision", revision, "status") == "Approved":
		document_control.make_effective(revision)  # approved with today as its date
	return document


def approve_production_masters(
	steps: list[tuple[str, str]],
	routings: list[str],
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
) -> None:
	"""QA's signed approval of the workstations, the operations with their work instructions and the
	routings, as `steps` (operation, workstation) and `routings` name them."""
	for operation, _workstation in steps:
		values = {}
		if not frappe.db.get_value("Operation", operation, routing.WORK_INSTRUCTION):
			values[routing.WORK_INSTRUCTION] = ensure_work_instruction(
				f"Work instruction: {operation}", act_as, user_of, password
			)
		if not frappe.db.get_value("Operation", operation, routing.IN_PROCESS_CHECK):
			values[routing.IN_PROCESS_CHECK] = "Yes"
		if values and not master_approval.approved("Operation", operation):
			doc = frappe.get_doc("Operation", operation)
			doc.update(values)
			doc.save(ignore_permissions=True)  # demo loader, while the operation is in draft
	for doctype, names in (
		("Workstation", sorted({w for _o, w in steps})),
		("Operation", sorted({o for o, _w in steps})),
		("Routing", routings),
	):
		for name in names:
			if master_approval.approved(doctype, name):
				continue
			with act_as(APPROVER):
				routing.approve(
					frappe.get_doc(doctype, name), password=password, reason="Checked for the demo"
				)


# --- The documents that define the other masters (plan D-34) ---------------------------------

TEST_METHOD_TYPE = "SOP"  # a standard test procedure, written by the plant
SPECIFICATION_TYPE = "Specification"


def approve_specification(
	name: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
) -> None:
	"""QA's signed approval of a demo specification (module 6, plan D-33), naming first, if it names
	none, the test method of its stage, issued through document control."""
	if master_approval.approved(specification.DOCTYPE, name):
		return
	if not frappe.db.get_value(specification.DOCTYPE, name, specification.TEST_METHOD):
		stage = frappe.db.get_value(specification.DOCTYPE, name, "mds_stage")
		method = ensure_work_instruction(
			f"{stage} inspection test method (demo)", act_as, user_of, password, TEST_METHOD_TYPE
		)
		doc = frappe.get_doc(specification.DOCTYPE, name)
		doc.set(specification.TEST_METHOD, method)
		doc.save(ignore_permissions=True)  # demo loader: the test method named once, its first issue
	with act_as(APPROVER):
		specification.approve(
			frappe.get_doc(specification.DOCTYPE, name),
			password=password,
			reason="Parameters, limits, sampling and test method checked (demo)",
		)


def ensure_specification_document(
	item: str,
	act_as: Callable[[str], AbstractContextManager],
	user_of: Callable[[str], str],
	password: str,
) -> None:
	"""The specification that defines a demo item QA has not approved yet, issued through document
	control and named on the item (module 1, plan D-34), for the item types that need one."""
	values = frappe.db.get_value(
		"Item", item, [item_master.ITEM_TYPE, item_master.SPECIFICATION_DOCUMENT], as_dict=True
	)
	if (
		values.get(item_master.ITEM_TYPE) not in item_master.DEFINED_BY_DOCUMENT
		or values.get(item_master.SPECIFICATION_DOCUMENT)
		or item_master.approved(item)
	):
		return
	document = ensure_work_instruction(
		f"Specification: {item} (demo)", act_as, user_of, password, SPECIFICATION_TYPE
	)
	doc = frappe.get_doc("Item", item)
	doc.set(item_master.SPECIFICATION_DOCUMENT, document)
	doc.save(ignore_permissions=True)  # demo loader, while the item is in draft
