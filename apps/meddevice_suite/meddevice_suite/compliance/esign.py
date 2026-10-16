"""Electronic signatures (21 CFR Part 11 subpart C).

A feature signs from its own whitelisted action, after checking that the action
itself is allowed:

	signature = sign(doc, meaning="Released", action="Batch Release", password=password)

`sign` re-authenticates the signer with their password (11.200), refuses shared
accounts (11.100), records name, time and meaning (11.50), and binds the
signature to the record's exact content and to the record's previous signature
(11.70). `verify_signatures` and `is_unchanged_since` detect tampering later.

A signature is recorded only here: the Electronic Signature refuses any other insert,
whatever permissions the caller holds or skips, and one recorded under an account other
than the signer's. A declared signed act (compliance/signing.py) is signed only with the
meaning its declaration gives, so the meaning shown in the signing dialog is the one recorded.

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
from meddevice_suite.compliance.signing import declared_meaning

MEANINGS = (
	"Authored",
	"Reviewed",
	"Approved",
	"Rejected",
	"Released",
	"Verified",
	"Trained",
	"Competent",
	"Withdrawn",
	"Held",
)
# The fields a signature's fingerprint covers, besides the fingerprint before it.
FINGERPRINTED = (
	"reference_doctype",
	"document_name_at_signing",
	"action",
	"meaning",
	"reason",
	"signer",
	"signer_name",
	"signed_at",
	"record_hash",
)

# Accounts that do not identify one person (21 CFR 11.100(a)).
SHARED_ACCOUNTS = ("Administrator", "Guest")
# Frappe locks an account after this many wrong passwords, for this long; a site that allows
# unlimited attempts never locks, so a locked account could never be kept from signing.


def apply_lockout() -> None:
	"""Site configuration: an account locks after wrong passwords (URS-CMP-001), as many and for as
	long as Quality Settings say (URS-PLT-013). The lockout is written with the rest of the access
	policy (compliance/access_policy.py), its one writer: on install, on every migrate and when QA
	changes the settings, so a hand edit of the site's System Settings does not last."""
	from meddevice_suite.compliance.access_policy import apply_access_policy  # it reads the settings

	apply_access_policy()


# record type -> fields the system maintains after signing, outside what a signature binds.
# Record types whose signature binds part of the record, not all of it: dotted path of
# content(doc) -> dict. An item's approval binds what QA approved (master_data.item_master).
SIGNED_CONTENT: dict[str, str] = {
	"Regulatory Licence": "meddevice_suite.qms.regulatory_licence.signed_content",
	# Module 31: the item, the headings and each element with its pinned version; not the status,
	# so the signatures stay intact when the record goes out of date or is superseded.
	"Device Master Record": "meddevice_suite.qms.device_master_record.signed_content",
	"Item": "meddevice_suite.master_data.item_master.signed_content",
	"Customer": "meddevice_suite.master_data.customer_licence.customer_signed_content",
	"BOM": "meddevice_suite.master_data.bom.signed_content",
	"Asset": "meddevice_suite.master_data.equipment.signed_content",
	"Operation": "meddevice_suite.master_data.master_approval.signed_content",
	"Workstation": "meddevice_suite.master_data.master_approval.signed_content",
	"Routing": "meddevice_suite.master_data.master_approval.signed_content",
	"Warehouse": "meddevice_suite.master_data.master_approval.signed_content",
	# Module 6 (plan D-33): what the specification states and its version.
	"Quality Inspection Template": "meddevice_suite.master_data.specification.signed_content",
	# Module 18: a correction of a lot's dates binds the lot and the dates QA approved; its quantity
	# and status move on with the stock and QA's decisions.
	"Batch": "meddevice_suite.lot_control.lot_dates.signed_content",
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
	# QA's impact assessment comes after the performer's signature, with its own (module 38). The
	# records of the period submitted since are added by the system as they are submitted, each
	# recorded in the audit trail with the record that caused it; a field added after records were
	# signed, so no signature ever bound it (independent review of 2026-10-15).
	"Calibration Record": (
		"docstatus",
		"impact_status",
		"impact_decision",
		"assessed_by",
		"assessed_on",
		"later_impact",
	),
	"Maintenance Record": ("docstatus",),
	# QA's approval and a return come after the performer's execution signature (module 40).
	"Equipment Qualification": ("docstatus", "approved_by", "approved_at", "executed_by", "executed_at"),
	# The assessor's and QA's signatures come after the trainee's; what was planned and signed
	# for stays fixed (module 37).
	"Training Record": (
		"docstatus",
		"status",
		"trained_by",
		"trained_on",
		"assessed_by",
		"assessed_on",
		"valid_until",
		"outcome_reason",
		"revoked_by",
		"revoked_on",
		"is_historical",
	),
	"Training Matrix": ("docstatus",),
	# Module 39: Management's approval comes after QA's review signature and binds what QA
	# reviewed; a later policy supersedes it.
	"Risk Acceptability Policy": (
		"docstatus",
		"status",
		"approved_by",
		"approved_at",
		"in_force_since",
		"superseded_by",
	),
	# The next approved revision supersedes it, as a document revision (module 30).
	"Risk Assessment": ("status", "docstatus", "superseded_by"),
	"Risk Management Report": ("status", "docstatus"),
	"Risk Review": ("docstatus",),
	# The record each listed record's change was made in, recorded as the change is made, or that it
	# needed no change, recorded as an action is done (module 36).
	"Change Request Affected Record": (
		"change_record_type",
		"change_record",
		"changed_on",
		"no_change_needed",
		"no_change_reason",
	),
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


# (record type, act) -> fields the system maintains after that act's signature, beyond the record
# type's own. For an act added after its record type already carried signatures: widening
# SYSTEM_MAINTAINED would change what those earlier signatures bind, and a signature that verified
# must keep verifying (21 CFR 11.70). The act is part of the signature and its fingerprint, so each
# signature is checked against the content its own act binds.
ACT_MAINTAINED: dict[tuple[str, str], tuple[str, ...]] = {
	# The periodic review (module 30, 2026-10-12): the review alert's bookkeeping moves on after it
	# when the next review falls due.
	("Controlled Document", "Review"): ("review_alerted_for",),
}

# record type (or child table) -> fields added after records of it were signed, bound once they hold
# a value. A record signed before the field existed was signed without it; binding the empty field
# would change that content and break a signature that verified (21 CFR 11.70).
BOUND_WHEN_SET: dict[str, tuple[str, ...]] = {
	# Module 38 (foundation review 2026-10-12): the workstation a job card in an out-of-tolerance
	# calibration's impact list was at; an inspection's row has none.
	"Calibration Impact": ("workstation",),
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
	# 11.50(a)(3): the signing dialog shows the declared meaning, so the act records that one.
	declared = declared_meaning(doc.doctype, action)
	if declared and meaning != declared:
		frappe.throw(
			_("{0} on {1} is signed as {2}, not as {3}.").format(
				_(action), _(doc.doctype), _(declared), _(meaning)
			)
		)
	plain_text(reason)

	doc.check_permission(permission)
	authenticate(user, password, doc, action)

	# Serialises signings of the same record, so each one chains to the one before.
	frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
	previous = last_signature(doc.doctype, doc.name)
	identify_new_rows(doc)

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
			"record_hash": record_fingerprint(doc, action),
			"previous_signature": previous.name if previous else None,
		}
	)
	signature.signature_hash = signature_fingerprint(signature, previous.signature_hash if previous else "")
	# No role may create signatures directly; this service is the only way in, and the record
	# refuses an insert without this mark, which a request cannot set on a document.
	signature.flags.recorded_by_signing = True
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


def identify_new_rows(doc: Document) -> None:
	"""Give the rows an act added just before signing (an out-of-tolerance calibration's impact
	list, say) the name, owner and creation Frappe gives them when the record is saved, as Frappe
	gives them (Document.set_user_and_timestamp): signed without them, the stored rows would carry
	values the signature never bound, and it could never verify. Rows already stored are left as
	they are, so what earlier signatures bound is unchanged."""
	from frappe.model.naming import set_new_name

	for row in doc.get_all_children():
		if not row.name:
			set_new_name(row)
		if not row.owner:
			row.owner = doc.owner
		if not row.creation:
			row.creation = doc.creation


def record_fingerprint(doc: Document, action: str | None = None) -> str:
	"""SHA-256 of the content a signature of `action` binds, child rows included (see the module
	docstring and `ACT_MAINTAINED`)."""
	if doc.doctype in SIGNED_CONTENT:
		return sha256(frappe.get_attr(SIGNED_CONTENT[doc.doctype])(doc))
	content = record_content(doc)
	for field in (
		"name",
		*SYSTEM_MAINTAINED.get(doc.doctype, ()),
		*ACT_MAINTAINED.get((doc.doctype, action), ()),
	):
		content.pop(field, None)
	without_unset_new_fields(content)
	for rows in content.values():
		if isinstance(rows, list):
			for row in rows:
				if isinstance(row, dict):
					# A row's docstatus is its parent's, set as the parent is submitted.
					for field in ("parent", "docstatus", *SYSTEM_MAINTAINED.get(row.get("doctype"), ())):
						row.pop(field, None)
					without_unset_new_fields(row)
	return sha256(content)


def without_unset_new_fields(content: dict) -> None:
	"""Leave out the fields of `BOUND_WHEN_SET` that hold no value."""
	for field in BOUND_WHEN_SET.get(content.get("doctype"), ()):
		if content.get(field) in (None, ""):
			content.pop(field, None)


def signature_fingerprint(signature: Document | frappe._dict, previous_hash: str) -> str:
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
	"""Whether every signature on the record is intact and the chain is unbroken.

	The chain is followed by its links, as the audit trail's is (`audit.verify_events`), not by
	time: a server clock set back between two signings orders them wrongly by time, which must not
	read as tampering. An altered signature, one removed from the chain, one inserted into it and a
	second one claiming the same place are still detected.
	"""
	signatures = frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["name", *FINGERPRINTED, "previous_signature", "signature_hash"],
	)
	by_name = {signature.name: signature for signature in signatures}
	firsts, followed = 0, set()
	for signature in signatures:
		previous = None
		if signature.previous_signature:
			previous = by_name.get(signature.previous_signature)
			# The signature before it is missing, or another signature already follows it.
			if previous is None or signature.previous_signature in followed:
				return False
			followed.add(signature.previous_signature)
		else:
			firsts += 1
		if signature.signature_hash != signature_fingerprint(
			signature, previous.signature_hash if previous else ""
		):
			return False
	# One chain: a single first signature, every other one following another.
	return not signatures or firsts == 1


def is_unchanged_since(signature: Document) -> bool:
	"""Whether the signed record's content is still exactly what was signed."""
	doc = frappe.get_doc(signature.reference_doctype, signature.reference_name)
	return record_fingerprint(doc, signature.action) == signature.record_hash
