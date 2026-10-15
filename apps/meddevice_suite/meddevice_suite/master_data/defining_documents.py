"""Masters linked with the controlled documents that define them (DFD D2: "All masters are version
controlled and linked with Document Control"; foundation hardening plan D-34, decided 2026-10-14;
ISO 13485 4.2.3, 4.2.4).

A master that is defined by a document names it: an operation its work instruction (module 3), a
specification its test method (module 6), a purchased material its material specification and a
finished good its product specification or drawing (module 1). QA approves the master only while
that document is in effect, a controlled document of a type that can define it. Each master's
approval asks `document_missing` what its document still lacks; `defined_masters` is the list the
guardrail test holds every master with an approval act to, so that a master given a link to a
controlled document later is held to the same rule. The other way round, a document that defines an
approved master in use is not withdrawn (`masters_defined_by`, asked by document control's
withdrawal): QA first points the master to another document, or retires it, under change control.
"""

from collections.abc import Callable
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.document import Document

CONTROLLED_DOCUMENT, EFFECTIVE = "Controlled Document", "Effective"


@dataclass(frozen=True)
class DefiningDocument:
	"""A master's link to the controlled document that defines it."""

	field: str
	types: tuple[str, ...]  # the document types that can define the master
	what: str  # what the document is to the master, as the form says it
	# The document is needed only by masters whose field holds one of the values (an item's type);
	# by every master when None. A document that is named is checked either way.
	required_when: tuple[str, tuple[str, ...]] | None = None

	def required(self, doc: Document) -> bool:
		if self.required_when is None:
			return True
		field, values = self.required_when
		return doc.get(field) in values


def document_missing(doc: Document, defining: DefiningDocument) -> list[str]:
	"""What the master's defining document still lacks for QA to approve the master, in words the
	form shows: none named, one of another type, or one not in effect."""
	name = doc.get(defining.field)
	if not name:
		return [_("its {0}").format(_(defining.what))] if defining.required(doc) else []
	document = frappe.db.get_value(CONTROLLED_DOCUMENT, name, ["document_type", "status"], as_dict=True)
	if not document:
		return [_("its {0} ({1} is not a controlled document)").format(_(defining.what), name)]
	if document.document_type not in defining.types:
		return [
			_("a {0} as its document ({1} is a {2}, not of type {3})").format(
				_(defining.what),
				name,
				_(document.document_type),
				" / ".join(_(document_type) for document_type in defining.types),
			)
		]
	if document.status != EFFECTIVE:
		return [
			_("its {0} in effect ({1} is {2})").format(_(defining.what), name, _(document.status).lower())
		]
	return []


def not_in_effect(names: list[str | None]) -> set[str]:
	"""Those of the named documents that are not in effect now, in one query."""
	wanted = sorted({name for name in names if name})
	if not wanted:
		return set()
	effective = frappe.get_all(
		CONTROLLED_DOCUMENT, filters={"name": ["in", wanted], "status": EFFECTIVE}, pluck="name"
	)
	return set(wanted) - set(effective)


def defined_masters() -> dict[str, tuple[DefiningDocument, Callable[[Document], list[str]]]]:
	"""Each master defined by a document: its link, and what its approval checks
	(missing_for_approval(doc) -> list[str]). The masters' modules build on this one."""
	from meddevice_suite.master_data import item_master, routing, specification

	return {
		"Operation": (routing.WORK_INSTRUCTION_DOCUMENT, routing.operation_missing),
		specification.DOCTYPE: (specification.TEST_METHOD_DOCUMENT, specification.missing_for_approval),
		"Item": (item_master.SPECIFICATION_DOCUMENT_LINK, item_master.missing_for_approval),
	}


def approval_of(doctype: str) -> tuple[str, str]:
	"""The field holding QA's approval of a master defined by a document, and its approved value:
	the item's own (module 1), the one the other masters share (master_approval)."""
	from meddevice_suite.master_data import item_master, master_approval

	if doctype == "Item":
		return item_master.APPROVAL_STATUS, item_master.APPROVED
	return master_approval.APPROVAL_STATUS, master_approval.APPROVED


def masters_defined_by(document: str) -> list[str]:
	"""The approved masters, not disabled, that name `document` as the one defining them, each as
	"Item X". While one does, the document is not withdrawn (ISO 13485 4.2.4: no unintended use of
	an obsolete document; independent review 2026-10-15): QA approved the master against it, and
	the master stays in use. One query per kind of master."""
	cited = []
	for doctype, (defining, _missing_for_approval) in defined_masters().items():
		meta = frappe.get_meta(doctype)
		approval, approved = approval_of(doctype)
		if not (meta.has_field(defining.field) and meta.has_field(approval)):
			continue  # before apply_configuration first adds the fields, nothing names a document
		filters = {defining.field: document, approval: approved}
		if meta.has_field("disabled"):
			filters["disabled"] = 0
		cited += [
			_("{0} {1}").format(_(doctype), name)
			for name in frappe.get_all(doctype, filters=filters, pluck="name", order_by="name asc")
		]
	return cited
