"""QA's signed approval of a master before it is used (master plan section 3: masters are created in
draft, reviewed and approved by the responsible role before they can be used in a transaction).

The masters that carry no approval of their own in ERPNext share these fields and this act: the
operation, the workstation and the routing (module 3, master_data/routing.py), the warehouse
(module 7) and the specification (module 6, master_data/specification.py; plan D-33). Each module
says what its master needs before approval and where an unapproved one is refused. The item
(module 1) and the equipment (module 8) have their own approvals.

- A new or copied master is in draft; only the signed act approves it, on every save.
- What QA approved changes once approved only under an approved change request
  (master_data/versioning.py, which counts an approved master as in use). A specification's
  approval stands for what it says: a change returns it to draft (`return_to_draft`) until QA
  approves it again, and its own module decides when a change needs a request.
- The signature binds what versioning controls of the master (compliance.esign.SIGNED_CONTENT).
"""

from collections.abc import Callable

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign

APPROVAL_STATUS, APPROVED_BY, APPROVED_AT = (
	"mds_master_approval",
	"mds_master_approved_by",
	"mds_master_approved_at",
)
APPROVAL_FIELDS = (APPROVAL_STATUS, APPROVED_BY, APPROVED_AT)
DRAFT, APPROVED = "Draft", "Approved"
FLAG = "master_approval"


def approval_fields(insert_after: str) -> list[dict]:
	"""The approval fields of a master, placed after a field of its form."""
	return [
		{
			"fieldname": "mds_master_approval_section",
			"label": "Approval by QA",
			"fieldtype": "Section Break",
			"insert_after": insert_after,
		},
		{
			"fieldname": APPROVAL_STATUS,
			"label": "Approval by QA",
			"fieldtype": "Select",
			"options": f"{DRAFT}\n{APPROVED}",
			"default": DRAFT,
			"insert_after": "mds_master_approval_section",
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": APPROVED_BY,
			"label": "Approved By",
			"fieldtype": "Link",
			"options": "User",
			"insert_after": APPROVAL_STATUS,
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": APPROVED_AT,
			"label": "Approved At",
			"fieldtype": "Datetime",
			"insert_after": APPROVED_BY,
			"read_only": 1,
			"no_copy": 1,
		},
	]


def configured(doctype: str) -> bool:
	return frappe.get_meta(doctype).has_field(APPROVAL_STATUS)  # the fields arrive with apply_configuration


def approved(doctype: str, name: str | None) -> bool:
	return bool(name) and frappe.db.get_value(doctype, name, APPROVAL_STATUS) == APPROVED


def unapproved(doctype: str, names: list[str | None]) -> list[str]:
	"""Those of the masters that QA has not approved, in one query."""
	wanted = sorted({name for name in names if name})
	if not wanted or not configured(doctype):
		return []
	ok = set(
		frappe.get_all(doctype, filters={"name": ["in", wanted], APPROVAL_STATUS: APPROVED}, pluck="name")
	)
	return [name for name in wanted if name not in ok]


def approve(
	doc: Document, password: str, reason: str | None, missing_for_approval: Callable[[Document], list[str]]
) -> None:
	"""QA's signed approval of a master (compliance.signing.SIGNING_ACTIONS)."""
	frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)  # one approval at a time
	doc = frappe.get_doc(doc.doctype, doc.name)
	doc.check_permission("read")
	require(is_qa(), _("Only QA approves a {0}.").format(_(doc.doctype).lower()), frappe.PermissionError)
	require(doc.get(APPROVAL_STATUS) != APPROVED, _("{0} is approved already.").format(doc.name))
	require(not cint(doc.get("disabled")), _("{0} is disabled; it is not approved.").format(doc.name))
	require(reason, _("Say why the {0} is approved.").format(_(doc.doctype).lower()))
	missing = missing_for_approval(doc)
	require(not missing, _("{0} cannot be approved yet; it lacks {1}.").format(doc.name, "; ".join(missing)))
	doc.set(APPROVAL_STATUS, APPROVED)
	doc.set(APPROVED_BY, frappe.session.user)
	doc.set(APPROVED_AT, now_datetime())
	sign(doc, "Approved", "Approve", password, reason, permission="read")
	doc.flags.master_approval = True
	# QA reads these masters; the signed approval is the one change QA makes to them.
	doc.save(ignore_permissions=True)


def guard_approval(doc: Document, method: str | None = None) -> None:
	"""Validate of each master: the approval is the signed act's alone; a new or copied one is in
	draft."""
	if not configured(doc.doctype) or doc.flags.get(FLAG):
		return
	before = None if doc.is_new() else doc.get_doc_before_save()
	if before is None:
		doc.set(APPROVAL_STATUS, DRAFT)
		doc.set(APPROVED_BY, None)
		doc.set(APPROVED_AT, None)
		return
	for field in APPROVAL_FIELDS:
		if not same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype):
			frappe.throw(
				_("The approval of {0} is given only by QA's signed approval.").format(doc.name),
				frappe.PermissionError,
			)


def return_to_draft(doc: Document) -> None:
	"""A master whose approval stands for its content alone (a specification, module 6): changed
	once approved, it is in draft again until QA approves what it now says. Called by its module's
	validate after `guard_approval`, for a change of what QA approved."""
	if doc.flags.get(FLAG) or doc.get(APPROVAL_STATUS) != APPROVED:
		return
	doc.set(APPROVAL_STATUS, DRAFT)
	doc.set(APPROVED_BY, None)
	doc.set(APPROVED_AT, None)


def onload(doc: Document, missing_for_approval: Callable[[Document], list[str]]) -> None:
	is_approved = doc.get(APPROVAL_STATUS) == APPROVED
	doc.set_onload(
		"master_approval",
		{"approved": is_approved, "missing": [] if is_approved else missing_for_approval(doc)},
	)


def signed_content(doc: Document) -> dict:
	"""What QA's signature on a master binds: what version control controls of it, and the fields
	the module adds to what approval needs."""
	from meddevice_suite.master_data.versioning import MASTERS

	master = MASTERS[doc.doctype]
	content = {field: value_of(doc, field) for field in master.fields}
	for table, columns in master.tables.items():
		content[table] = [
			{column: value_of(row, column) for column in columns} for row in doc.get(table) or []
		]
	return content


def value_of(doc: Document, field: str) -> object:
	df = doc.meta.get_field(field)
	value = doc.get(field)
	if df and df.fieldtype in ("Check", "Int"):
		return cint(value)
	if df and df.fieldtype in ("Float", "Percent", "Currency"):
		from frappe.utils import flt

		return flt(value)
	return value or None
