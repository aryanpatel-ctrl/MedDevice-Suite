"""Electronic signatures (21 CFR Part 11 subpart C).

A feature signs from its own whitelisted action, after checking that the action
itself is allowed:

	signature = sign(doc, meaning="Released", action="Batch Release", password=password)

`sign` re-authenticates the signer with their password (11.200), refuses shared
accounts (11.100), records name, time and meaning (11.50), and binds the
signature to the record's exact content and to the record's previous signature
(11.70). `verify_signatures` and `is_unchanged_since` detect tampering later.

A wrong password counts towards the account lockout like a failed login, and is
written to the login history by a background job, so it stays on record although
the refused request is rolled back (11.300(d)). It does not end the session:
Frappe signs a user out on its own authentication error, which a mistyped
signing password must not do.

The content a signature binds is what the person signed. Fields the system keeps
up to date as the record's life moves on (its status, links to later records) are
left out, as is the record's name, which a rename may change; the audit trail
records every one of those changes.
"""

import frappe
from frappe import _
from frappe.auth import get_login_attempt_tracker
from frappe.model.document import Document
from frappe.utils import get_datetime, get_fullname, now_datetime
from frappe.utils.password import check_password

from meddevice_suite.compliance.fingerprint import plain_text, record_content, sha256

MEANINGS = ("Authored", "Reviewed", "Approved", "Rejected", "Released", "Verified")

# Accounts that do not identify one person (21 CFR 11.100(a)).
SHARED_ACCOUNTS = ("Administrator", "Guest")

# record type -> fields the system maintains after signing, outside what a signature binds.
SYSTEM_MAINTAINED: dict[str, tuple[str, ...]] = {
	"Document Revision": ("status", "docstatus", "superseded_by"),
	"Controlled Document": ("status", "current_revision", "effective_since", "next_review_date"),
}


class SigningRefused(frappe.ValidationError):
	"""The signature was not recorded. Not an authentication error, which would sign the user out."""


def sign(
	doc: Document,
	meaning: str,
	action: str,
	password: str,
	reason: str | None = None,
	permission: str = "write",
) -> Document:
	"""Record the session user's signature on `doc`; return the Electronic Signature.

	`permission` is the permission the signed action requires on `doc`.
	"""
	user = frappe.session.user
	if user in SHARED_ACCOUNTS:
		frappe.throw(
			_("Sign in with your own account to sign; shared accounts cannot sign."), frappe.PermissionError
		)
	# Frappe's password check does not look at the account, and a session outlives disabling it.
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("Your account is disabled; it cannot sign."), frappe.PermissionError)
	if meaning not in MEANINGS:
		frappe.throw(_("Unknown signature meaning: {0}").format(meaning))
	if not action:
		frappe.throw(_("A signature needs the action being signed."))
	plain_text(reason)

	doc.check_permission(permission)
	authenticate(user, password, doc, action)

	# Serialises signings of the same record, so each one chains to the one before.
	frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
	previous = last_signature(doc.doctype, doc.name)

	signature = frappe.get_doc(
		{
			"doctype": "Electronic Signature",
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"document_name_at_signing": doc.name,
			"action": action,
			"meaning": meaning,
			"reason": reason,
			"signer": user,
			"signer_name": get_fullname(user),
			"signed_at": now_datetime(),
			"record_hash": record_fingerprint(doc),
			"previous_signature": previous.name if previous else None,
		}
	)
	signature.signature_hash = signature_fingerprint(signature, previous.signature_hash if previous else "")
	# No role may create signatures directly; this service is the only way in.
	signature.insert(ignore_permissions=True)
	return signature


def authenticate(user: str, password: str, doc: Document, action: str) -> None:
	"""Check the signer's password, counting failures towards the login lockout (11.300(d))."""
	tracker = get_login_attempt_tracker(user)  # raises if the account is locked
	try:
		check_password(user, password, delete_tracker_cache=False)
	except frappe.AuthenticationError:
		tracker.add_failure_attempt()
		frappe.enqueue(
			record_failed_signing,
			queue="short",
			# Run in its own transaction, not this request's, which is rolled back.
			now=frappe.in_test,
			user=user,
			doctype=doc.doctype,
			name=doc.name,
			action=action,
		)
		frappe.throw(_("Incorrect password. The signature was not recorded."), SigningRefused)
	tracker.add_success_attempt()


def record_failed_signing(user: str, doctype: str, name: str, action: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Activity Log",
			"user": user,
			"operation": "Login",
			"status": "Failed",
			# The record is named in the subject: Frappe marks a log that links a record as
			# "Linked" instead of "Failed", which would hide the failure.
			"subject": f"Electronic signature refused, wrong password: {action} on {doctype} {name}",
		}
	).insert(ignore_permissions=True, ignore_links=True)  # login history is written by the system


def last_signature(doctype: str, name: str) -> frappe._dict | None:
	signatures = frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["name", "signature_hash"],
		order_by="signed_at desc, creation desc",
		limit=1,
	)
	return signatures[0] if signatures else None


def record_fingerprint(doc: Document) -> str:
	"""SHA-256 of the content a signature binds, child rows included (see the module docstring)."""
	content = record_content(doc)
	for field in ("name", *SYSTEM_MAINTAINED.get(doc.doctype, ())):
		content.pop(field, None)
	for rows in content.values():
		if isinstance(rows, list):
			for row in rows:
				if isinstance(row, dict):
					row.pop("parent", None)
	return sha256(content)


def signature_fingerprint(signature: Document, previous_hash: str) -> str:
	# The name as it was at signing: renaming a record rewrites `reference_name`
	# in every signature, which must not read as tampering.
	return sha256(
		[
			previous_hash,
			signature.reference_doctype,
			signature.document_name_at_signing,
			signature.action,
			signature.meaning,
			signature.reason or "",
			signature.signer,
			signature.signer_name,
			str(get_datetime(signature.signed_at)),
			signature.record_hash,
		]
	)


def verify_signatures(doctype: str, name: str) -> bool:
	"""Whether every signature on the record is intact and the chain is unbroken."""
	signatures = frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["name"],
		order_by="signed_at asc, creation asc",
		pluck="name",
	)
	previous = None
	for signature_name in signatures:
		signature = frappe.get_doc("Electronic Signature", signature_name)
		expected_previous = previous.name if previous else None
		previous_hash = previous.signature_hash if previous else ""
		if signature.previous_signature != expected_previous:
			return False
		if signature.signature_hash != signature_fingerprint(signature, previous_hash):
			return False
		previous = signature
	return True


def is_unchanged_since(signature: Document) -> bool:
	"""Whether the signed record's content is still exactly what was signed."""
	doc = frappe.get_doc(signature.reference_doctype, signature.reference_name)
	return record_fingerprint(doc) == signature.record_hash
