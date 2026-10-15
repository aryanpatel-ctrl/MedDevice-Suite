"""Routings, operations and workstations (module 3; URS-MST-012; DFD D3 Item / BOM / Routing
Master, D2 masters linked with Document Control; ISO 13485 4.2.4, 7.5.1; 21 CFR 820.70(a)).

Production records them; QA approves each with an electronic signature before it is used
(master_data/master_approval.py):

- an operation once it names its work instruction, an effective controlled document of type
  Work Instruction (module 30), and states whether it carries an in-process check (the check's
  parameters and limits are the item's in-process specification, module 6; holding a job card to
  it is module 20);
- a workstation once every piece of equipment at it (module 8) is approved;
- a routing once each of its operations, and the workstation of each, is approved.

A bill of materials with an unapproved routing, operation or workstation is not submitted, and no
work order or job card is made on an unapproved operation or workstation. What QA approved
changes once approved only under an approved change request (master_data/versioning.py).
Decided 2026-10-11, the user's instruction to follow the requirement (master plan section 3).
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document

from meddevice_suite.master_data import master_approval
from meddevice_suite.master_data.defining_documents import DefiningDocument, document_missing
from meddevice_suite.master_data.master_approval import unapproved

WORK_INSTRUCTION, IN_PROCESS_CHECK = "mds_work_instruction", "mds_in_process_check"
WORK_INSTRUCTION_TYPE = "Work Instruction"
# The document an operation is done by (plan D-34: masters linked with Document Control).
WORK_INSTRUCTION_DOCUMENT = DefiningDocument(WORK_INSTRUCTION, (WORK_INSTRUCTION_TYPE,), "work instruction")
CUSTOM_FIELDS = {
	"Operation": [
		{
			"fieldname": WORK_INSTRUCTION,
			"label": "Work Instruction",
			"fieldtype": "Link",
			"options": "Controlled Document",
			"insert_after": "workstation",
			"description": "The effective work instruction this operation is done by (document control).",
		},
		{
			"fieldname": IN_PROCESS_CHECK,
			"label": "In-Process Check",
			"fieldtype": "Select",
			"options": "\nYes\nNo",
			"insert_after": WORK_INSTRUCTION,
			"description": "Whether the operation is inspected in process; the checks are the item's in-process specification.",
		},
		*master_approval.approval_fields(IN_PROCESS_CHECK),
	],
	"Workstation": master_approval.approval_fields("workstation_type"),
	"Routing": master_approval.approval_fields("disabled"),
}


def apply_routing_fields() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


# --- What approval needs --------------------------------------------------------------------


def operation_missing(doc: Document) -> list[str]:
	missing = document_missing(doc, WORK_INSTRUCTION_DOCUMENT)
	if doc.get(IN_PROCESS_CHECK) not in ("Yes", "No"):
		missing.append(_("whether it carries an in-process check"))
	if doc.get("workstation") and unapproved("Workstation", [doc.workstation]):
		missing.append(_("its workstation {0} approved").format(doc.workstation))
	subs = [
		row.operation
		for row in doc.get("sub_operations") or []
		if row.operation and row.operation != doc.name
	]
	missing += [_("its sub-operation {0} approved").format(name) for name in unapproved("Operation", subs)]
	return missing


def workstation_missing(doc: Document) -> list[str]:
	from meddevice_suite.master_data import equipment

	# The equipment at it now, rightly: QA approves the workstation as it now stands.
	at_it = frappe.get_all(
		"Asset",
		filters={
			equipment.WORKSTATION: doc.name,
			"docstatus": ["!=", 2],
			equipment.STATUS: ["!=", equipment.RETIRED],
		},
		fields=["name", equipment.APPROVAL_STATUS],
	)
	return [
		_("its equipment {0} approved").format(asset.name)
		for asset in at_it
		if asset.get(equipment.APPROVAL_STATUS) != equipment.APPROVED
	]


def routing_missing(doc: Document) -> list[str]:
	rows = doc.get("operations") or []
	if not rows:
		return [_("its operations")]
	return [
		*(
			_("operation {0} approved").format(name)
			for name in unapproved("Operation", [r.operation for r in rows])
		),
		*(
			_("workstation {0} approved").format(name)
			for name in unapproved("Workstation", [r.workstation for r in rows])
		),
	]


MISSING = {"Operation": operation_missing, "Workstation": workstation_missing, "Routing": routing_missing}


# --- The signed approval --------------------------------------------------------------------


def approve(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA's signed approval of an operation, workstation or routing."""
	master_approval.approve(doc, password, reason, MISSING[doc.doctype])


def guard(doc: Document, method: str | None = None) -> None:
	"""Operation, Workstation and Routing validate: approval by signature only; an approved one keeps
	what its approval needed."""
	master_approval.guard_approval(doc)
	if (
		master_approval.configured(doc.doctype)
		and doc.get(master_approval.APPROVAL_STATUS) == master_approval.APPROVED
	):
		if missing := MISSING[doc.doctype](doc):
			frappe.throw(
				_("{0} is approved and keeps what its approval needed; it lacks {1}.").format(
					doc.name, "; ".join(missing)
				)
			)


def onload(doc: Document, method: str | None = None) -> None:
	master_approval.onload(doc, MISSING[doc.doctype])


# --- Where an unapproved one is refused -----------------------------------------------------


def not_usable(
	operations: list[str | None], workstations: list[str | None], routing: str | None = None
) -> list[str]:
	"""What of these may not be used now, in words: not approved by QA; for an operation also its
	sub-operations, and its work instruction no longer in effect; for a workstation and a routing,
	disabled; for a workstation, equipment at it that is not approved."""
	ops = sorted({o for o in operations if o})
	subs = (
		sorted(
			set(
				frappe.get_all(
					"Sub Operation",
					filters={"parenttype": "Operation", "parent": ["in", ops]},
					pluck="operation",
				)
			)
			- {None}
		)
		if ops
		else []
	)
	reasons = [_("operation {0} not approved").format(n) for n in unapproved("Operation", [*ops, *subs])]
	if ops:
		instructions = dict(
			frappe.get_all(
				"Operation", filters={"name": ["in", ops]}, fields=["name", WORK_INSTRUCTION], as_list=True
			)
		)
		effective = (
			set(
				frappe.get_all(
					"Controlled Document",
					filters={"name": ["in", [w for w in instructions.values() if w]], "status": "Effective"},
					pluck="name",
				)
			)
			if any(instructions.values())
			else set()
		)
		reasons += [
			_("operation {0}: its work instruction {1} is no longer in effect").format(op, wi)
			for op, wi in instructions.items()
			if wi and wi not in effective
		]
	stations = sorted({w for w in workstations if w})
	reasons += [_("workstation {0} not approved").format(n) for n in unapproved("Workstation", stations)]
	if stations:
		reasons += [
			_("workstation {0} is disabled").format(n)
			for n in frappe.get_all(
				"Workstation", filters={"name": ["in", stations], "disabled": 1}, pluck="name"
			)
		]
		from meddevice_suite.master_data import equipment

		# The equipment at each workstation now, rightly: whether it may be used now.
		reasons += [
			_("workstation {0}: its equipment {1} approved").format(
				asset.get(equipment.WORKSTATION), asset.name
			)
			for asset in frappe.get_all(
				"Asset",
				filters={
					equipment.WORKSTATION: ["in", stations],
					"docstatus": ["!=", 2],
					equipment.STATUS: ["!=", equipment.RETIRED],
					equipment.APPROVAL_STATUS: ["!=", equipment.APPROVED],
				},
				fields=["name", equipment.WORKSTATION],
			)
		]
	if routing:
		reasons += [_("routing {0} not approved").format(n) for n in unapproved("Routing", [routing])]
		if frappe.db.get_value("Routing", routing, "disabled"):
			reasons.append(_("routing {0} is disabled").format(routing))
	return reasons


def refuse_unapproved(
	record: str, operations: list[str | None], workstations: list[str | None], routing: str | None = None
) -> None:
	if reasons := not_usable(operations, workstations, routing):
		frappe.throw(
			_("{0} is made only on what QA has approved and is in use now: {1}.").format(
				record, "; ".join(reasons)
			),
			title=_("Not approved by QA"),
		)


def guard_bom(doc: Document, method: str | None = None) -> None:
	"""BOM before_submit: its routing, operations and workstations are approved."""
	rows = (doc.get("operations") or []) if doc.get("with_operations") else []
	refuse_unapproved(
		_("A bill of materials"),
		[r.operation for r in rows],
		[r.workstation for r in rows],
		doc.get("routing"),
	)


def guard_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order before_validate and validate: made only on approved operations and workstations.
	Before validate, which Production Plan's work orders (saved without validation) pass through
	too, and again after ERPNext has fetched the operations again in its validate."""
	if doc.docstatus == 2:
		return
	rows = doc.get("operations") or []
	refuse_unapproved(_("A work order"), [r.operation for r in rows], [r.workstation for r in rows])


def guard_job_card(doc: Document, method: str | None = None) -> None:
	"""Job Card before_validate and validate: made only on an approved operation and workstation,
	its sub-operations included; again after ERPNext has filled them (and the workstation of a type)
	in its validate."""
	if doc.docstatus == 2:
		return
	subs = [row.get("sub_operation") for row in doc.get("sub_operations") or []]
	refuse_unapproved(_("A job card"), [doc.get("operation"), *subs], [doc.get("workstation")])
