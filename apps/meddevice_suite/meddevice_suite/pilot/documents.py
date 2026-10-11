"""Demo work instructions and approved production masters (modules 3 and 30), for the pilot site
and the browser journey's test site.

Made as the system makes them: the Production Supervisor authors a work instruction, the QC
Inspector reviews it and QA approves it, each with a signature, so it is in effect; QA then
approves the workstations, the operations (each naming its work instruction) and the routing with
signatures (master_data/routing.py). Everything is made only if missing.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from io import BytesIO

import frappe

from meddevice_suite.master_data import master_approval, routing

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
) -> str:
	"""An effective work instruction of `title`, issued through document control (module 30). A
	revision left part-way by an earlier run is taken on from where it stopped."""
	from meddevice_suite.qms import document_control

	existing = frappe.get_all(
		"Controlled Document",
		filters={
			"title": title,
			"document_type": routing.WORK_INSTRUCTION_TYPE,
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
						"document_type": routing.WORK_INSTRUCTION_TYPE,
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
