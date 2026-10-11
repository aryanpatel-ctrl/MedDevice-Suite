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
"""

import frappe
from frappe import _
from frappe.model.document import Document

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
	# An external laboratory's calibration certificate, once the calibration is completed (module 38).
	("Calibration Record", "certificate", {"docstatus": 1}),
)
# What identifies a file and where it is kept.
IDENTITY = ("file_url", "is_private", "file_name", "content_hash", "attached_to_doctype", "attached_to_name")


def is_evidence(file_url: str | None) -> bool:
	return bool(file_url) and any(
		frappe.db.exists(doctype, {field: file_url, **condition}) for doctype, field, condition in EVIDENCE
	)


def has_permission(doc: Document, ptype: str | None = None, user: str | None = None) -> bool:
	"""File permission hook: nobody may change or delete an evidence file. Only narrows."""
	return not (ptype in ("write", "delete") and is_evidence(doc.file_url))


def protect(doc: Document, method: str | None = None) -> None:
	"""File before_validate, ahead of Frappe's own validate, which moves a file made public."""
	if doc.is_new():
		return
	before = doc.get_doc_before_save()
	if before and is_evidence(before.file_url) and any(before.get(f) != doc.get(f) for f in IDENTITY):
		frappe.throw(
			_("{0} is part of a record's evidence and cannot be changed.").format(before.file_name),
			frappe.PermissionError,
		)
