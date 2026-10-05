"""Electronic signatures (21 CFR Part 11 subpart C).

A feature signs from its own whitelisted action, after checking that the action
itself is allowed:

	signature = sign(doc, meaning="Released", action="Batch Release", password=password)

`sign` re-authenticates the signer with their password (11.200), refuses shared
accounts (11.100), records name, time and meaning (11.50), and binds the
signature to the record's exact content and to the record's previous signature
(11.70). `verify_signatures` and `is_unchanged_since` detect tampering later.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.auth import get_login_attempt_tracker
from frappe.model.document import Document
from frappe.utils import get_datetime, get_fullname, now_datetime
from frappe.utils.password import check_password

MEANINGS = ("Authored", "Reviewed", "Approved", "Rejected", "Released", "Verified")

# Accounts that do not identify one person (21 CFR 11.100(a)).
SHARED_ACCOUNTS = ("Administrator", "Guest")

# Fields that change without the record's content changing.
VOLATILE_FIELDS = frozenset(
	{"modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by", "_seen"}
)


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
	if meaning not in MEANINGS:
		frappe.throw(_("Unknown signature meaning: {0}").format(meaning))
	if not action:
		frappe.throw(_("A signature needs the action being signed."))

	doc.check_permission(permission)
	authenticate(user, password)

	# Serialises signings of the same record, so each one chains to the one before.
	frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
	previous = last_signature(doc.doctype, doc.name)

	signature = frappe.get_doc(
		{
			"doctype": "Electronic Signature",
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
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


def authenticate(user: str, password: str) -> None:
	"""Check the signer's password, counting failures towards the login lockout (11.300(d))."""
	tracker = get_login_attempt_tracker(user)  # raises if the account is locked
	try:
		check_password(user, password, delete_tracker_cache=False)
	except frappe.AuthenticationError:
		tracker.add_failure_attempt()
		frappe.throw(_("Incorrect password. The signature was not recorded."), frappe.AuthenticationError)
	tracker.add_success_attempt()


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
	"""SHA-256 of the record's content, child rows included, ignoring bookkeeping fields."""
	content = without_volatile_fields(doc.as_dict(no_private_properties=True, convert_dates_to_str=True))
	return sha256(content)


def signature_fingerprint(signature: Document, previous_hash: str) -> str:
	return sha256(
		[
			previous_hash,
			signature.reference_doctype,
			signature.reference_name,
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


def without_volatile_fields(value):
	if isinstance(value, dict):
		return {
			key: without_volatile_fields(item) for key, item in value.items() if key not in VOLATILE_FIELDS
		}
	if isinstance(value, list):
		return [without_volatile_fields(item) for item in value]
	return value


def sha256(value) -> str:
	canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
	return hashlib.sha256(canonical.encode()).hexdigest()
