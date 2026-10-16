"""Files that are part of a record's evidence (21 CFR 11.10(c); ISO 13485 4.2.5).

An approved document's file and an archive's file are fingerprinted with the
record. Frappe lets anyone who may edit the record a file is attached to, and the
person who uploaded it, delete the file or make it public, which moves it and
rewrites its address. For an evidence file either would break the record, and a
public file can be downloaded without signing in. So nobody may change or delete
one: a corrected document is a new revision, an archive is never replaced.

Frappe gives the built-in Administrator account every permission before any app
is asked, and deletes a file from disk inside its own delete handler. That
account is for installing the system, not for daily use; every person's account
is held by the rules here.

No File record may name the audit trail's anchor file (compliance/audit_anchor.py), where it is
kept or was kept: Frappe serves a private file to whoever may read a File record naming it, and a
person may make one naming a file already on disk. The file is kept outside the folders Frappe
serves; this refusal holds should it ever be found inside one. A new File record is refused before
Frappe's own before_insert, which reads the named file and writes a copy of it under a new name.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from meddevice_suite.compliance.audit import same
from meddevice_suite.compliance.audit_anchor import names_anchor_file

# (record type, field holding the file, and when the file becomes evidence)
EVIDENCE: tuple[tuple[str, str, dict], ...] = (
	# Once its author has signed and the file is fingerprinted.
	("Document Revision", "file", {"file_sha256": ["is", "set"]}),
	("Record Archive", "archive_file", {}),
	# A supplier certificate, once the QC Inspector has verified it (module 16).
	("Purchase Receipt Item", "mds_certificate", {"mds_certificate_verified": 1}),
	# A supplier's quality system certificate, once QA has decided on it (module 4).
	("Supplier Approval", "certificate", {"docstatus": 1}),
	# A customer's licence copy, once QA has decided on it (module 5).
	("Customer Licence", "licence_copy", {"docstatus": 1}),
	# Every version of a file in the important files repository, from the moment it is added (module 61).
	("Important File Version", "file", {}),
	# An external laboratory's calibration certificate, once the calibration is completed (module 38),
	# and still once QA withdraws it for correction (plan D-29): the cancelled record keeps it.
	("Calibration Record", "certificate", {"docstatus": ["in", (1, 2)]}),
	# A training certificate (paper history, or one a trainer gave), once the record is completed (module 37).
	("Training Record", "certificate", {"docstatus": 1}),
)
# What identifies a file and where it is kept.
IDENTITY = ("file_url", "is_private", "file_name", "content_hash", "attached_to_doctype", "attached_to_name")


def is_evidence(file_url: str | None) -> bool:
	# Each column is indexed on the file's address by this app's configuration (setup/indexes.py),
	# so each check is an index lookup however many records hold files.
	return bool(file_url) and any(
		frappe.db.exists(doctype, {field: file_url, **condition}) for doctype, field, condition in EVIDENCE
	)


def has_permission(doc: Document, ptype: str | None = None, user: str | None = None) -> bool:
	"""File permission hook: nobody may change or delete an evidence file. Only narrows."""
	return not (ptype in ("write", "delete") and is_evidence(doc.file_url))


def unchanged(before: Document, doc: Document, field: str) -> bool:
	"""As stored: a form sends a check as text or a number, the database returns a number."""
	return same(before.get(field), doc.get(field), doc.meta.get_field(field).fieldtype)


def refuse_anchor_file(doc: Document) -> None:
	if names_anchor_file(doc.file_url) or names_anchor_file(doc.file_name):
		frappe.throw(
			_("The audit trail's anchor file is kept by the system and cannot be added as a file."),
			frappe.PermissionError,
		)


class ProtectedFile:
	"""Extends Frappe's File (hook extend_doctype_class): a new record naming the anchor file is
	refused before Frappe's before_insert reads it and stores a copy under another name, which a
	check of the stored address could no longer recognise."""

	def before_insert(self) -> None:
		refuse_anchor_file(self)
		super().before_insert()


def protect(doc: Document, method: str | None = None) -> None:
	"""File before_validate, ahead of Frappe's own validate, which moves a file made public."""
	refuse_anchor_file(doc)  # a stored record pointed at the anchor file
	if doc.is_new():
		return
	before = doc.get_doc_before_save()
	if before and is_evidence(before.file_url) and any(not unchanged(before, doc, f) for f in IDENTITY):
		frappe.throw(
			_("{0} is part of a record's evidence and cannot be changed.").format(before.file_name),
			frappe.PermissionError,
		)
