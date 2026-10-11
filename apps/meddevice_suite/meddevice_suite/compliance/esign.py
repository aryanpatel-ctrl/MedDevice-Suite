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
up to date as the record's life moves on (its status, links to later records, what
is recorded on its rows as they are done) are left out, as is the record's name,
which a rename may change; the audit trail records every one of those changes.
"""

import frappe
from frappe import _
from frappe.auth import get_login_attempt_tracker
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, get_fullname, now_datetime
from frappe.utils.password import check_password

from meddevice_suite.compliance.fingerprint import chain_head, plain_text, record_content, sha256

MEANINGS = ("Authored", "Reviewed", "Approved", "Rejected", "Released", "Verified")

# Accounts that do not identify one person (21 CFR 11.100(a)).
SHARED_ACCOUNTS = ("Administrator", "Guest")
# Frappe locks an account after this many wrong passwords, for this long; a site that allows
# unlimited attempts never locks, so a locked account could never be kept from signing.


def apply_lockout() -> None:
	"""Site configuration: an account locks after wrong passwords (URS-CMP-001), as many and for as
	long as Quality Settings say (URS-PLT-013): applied when QA changes them and on every migrate,
	so a hand edit of the site's System Settings does not last."""
	from meddevice_suite.compliance.quality_settings import value  # it builds on change control

	# Frappe locks an account once its failures exceed this number (auth.LoginAttemptTracker); the
	# setting is the number of wrong passwords that locks it.
	frappe.db.set_single_value(
		"System Settings", "allow_consecutive_login_attempts", value("login_lockout_attempts") - 1
	)
	frappe.db.set_single_value(
		"System Settings", "allow_login_after_fail", value("login_lockout_minutes") * 60
	)
	frappe.clear_cache(doctype="System Settings")


# record type -> fields the system maintains after signing, outside what a signature binds.
# Record types whose signature binds part of the record, not all of it: dotted path of
# content(doc) -> dict. An item's approval binds what QA approved (master_data.item_master).
SIGNED_CONTENT: dict[str, str] = {
	"Item": "meddevice_suite.master_data.item_master.signed_content",
	"Customer": "meddevice_suite.master_data.customer_licence.customer_signed_content",
	"BOM": "meddevice_suite.master_data.bom.signed_content",
	"Asset": "meddevice_suite.master_data.equipment.signed_content",
	"Operation": "meddevice_suite.master_data.master_approval.signed_content",
	"Workstation": "meddevice_suite.master_data.master_approval.signed_content",
	"Routing": "meddevice_suite.master_data.master_approval.signed_content",
}

SYSTEM_MAINTAINED: dict[str, tuple[str, ...]] = {
	"Document Revision": ("status", "docstatus", "superseded_by"),
	"Controlled Document": ("status", "current_revision", "effective_since", "next_review_date"),
	# The plan approved with a change stays what was signed while its actions are done.
	"Change Request": (
		"status",
		"docstatus",
		"implemented_on",
		"effectiveness_due_on",
		"effectiveness_alerted",
	),
	# The move and the return the decision causes are recorded after the signature.
	"Lot Release": ("docstatus", "stock_entry", "supplier_return"),
	# The certificate alert is bookkeeping of the alert, not part of the decision.
	"Supplier Approval": ("docstatus", "certificate_alerted"),
	# The expiry alert is bookkeeping of the alert, not part of the verified licence.
	"Customer Licence": ("docstatus", "expiry_alerted"),
	"Change Request Action": (
		"done",
		"done_on",
		"done_by",
		"completion_note",
		"result_doctype",
		"result_name",
		"alerted_for",
	),
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
	return chain_head("Electronic Signature", "previous_signature", "signature_hash", (doctype, name))


def record_fingerprint(doc: Document) -> str:
	"""SHA-256 of the content a signature binds, child rows included (see the module docstring)."""
	if doc.doctype in SIGNED_CONTENT:
		return sha256(frappe.get_attr(SIGNED_CONTENT[doc.doctype])(doc))
	content = record_content(doc)
	for field in ("name", *SYSTEM_MAINTAINED.get(doc.doctype, ())):
		content.pop(field, None)
	for rows in content.values():
		if isinstance(rows, list):
			for row in rows:
				if isinstance(row, dict):
					# A row's docstatus is its parent's, set as the parent is submitted.
					for field in ("parent", "docstatus", *SYSTEM_MAINTAINED.get(row.get("doctype"), ())):
						row.pop(field, None)
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
