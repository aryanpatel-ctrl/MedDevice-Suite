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

The content a signature binds is what the person signed, declared for every record
type with a signed act in SIGNED_CONTENT: the fields the signer approves, never the
whole record, so a field added to the record type later changes no signature. Fields
the system keeps up to date as the record's life moves on (its status, links to later
records, what is recorded on its rows as they are done) are left out, as is the
record's name, which a rename may change; the audit trail records every one of those
changes. The signature itself binds the record's type and its name at signing, and
verification checks that the record it is attached to has had that name.

Verification (`signature_problems`, `signed_content_report`) reports, for a reviewer,
a signature altered, removed, inserted or moved from another record, and for each
signature whether the record still holds what it binds, was changed and signed again,
was changed through the system (the audit trail accounts for the change), or was
changed in a way the audit trail does not account for.

Fingerprints are versioned (compliance/fingerprint.py): a signature recorded now is
version 2, keyed with a key held outside the database and bound to the content
SIGNED_CONTENT declares; one recorded before is version 1 and is verified as it was
made, by `version_1_content`.
"""

from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.auth import get_login_attempt_tracker
from frappe.model.document import Document
from frappe.utils import (
	cint,
	flt,
	format_datetime,
	get_datetime,
	get_fullname,
	getdate,
	now_datetime,
)
from frappe.utils.password import check_password

from meddevice_suite.compliance.audit import as_data, copy_of, history, record_names, undo
from meddevice_suite.compliance.fingerprint import (
	CURRENT_VERSION,
	FIRST_VERSION,
	KEYED_VERSION,
	Trail,
	chain_head,
	chain_problems,
	in_chain_order,
	keyed_sha256,
	keyed_since,
	plain_text,
	record_content,
	sha256,
	version_of,
)
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
TRAIL = Trail(
	"Electronic Signature",
	link="previous_signature",
	fingerprint="signature_hash",
	recorded_at="signed_at",
	name_at="document_name_at_signing",
)

# What a signature's verification says of the record's content now (`signed_content_report`).
UNCHANGED = "Unchanged"
SIGNED_AGAIN = "Changed and signed again"
CHANGED = "Changed since signing"
UNACCOUNTED = "Changed outside the audit trail"
# A signature whose record changed after it, and no later signature binds the record as it now is.
CONTENT_FINDINGS = (CHANGED, UNACCOUNTED)
# How far back the audit trail is undone to account for a change since a signature.
HISTORY_LIMIT = 500

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


@dataclass(frozen=True)
class SignedFields:
	"""A record type's signed content, declared as the fields its signers approve and, for each
	child table, the columns of its rows. Called like a content function: content(doc) -> dict."""

	fields: tuple[str, ...]
	tables: dict[str, tuple[str, ...]] = field(default_factory=dict)

	def __call__(self, doc: Document) -> dict:
		content = {name: signed_value(doc, name) for name in self.fields}
		for table, columns in self.tables.items():
			content[table] = [
				{column: signed_value(row, column) for column in columns} for row in doc.get(table) or []
			]
		return content


def signed_value(doc: Document, fieldname: str) -> object:
	"""A field's value in one form, whether the record was read from the database or set in code
	(a date as a date or as text, a number as text from the desk)."""
	df = doc.meta.get_field(fieldname)
	fieldtype = df.fieldtype if df else None
	value = doc.get(fieldname)
	if fieldtype in ("Check", "Int"):
		return cint(value)
	if fieldtype in ("Float", "Currency", "Percent"):
		return flt(value, 9)  # the database keeps nine decimals
	if value in (None, ""):
		return None
	if fieldtype == "Date":
		return str(getdate(value))
	if fieldtype == "Datetime":
		return str(get_datetime(value))
	return value


SIGNED_RECORDS = "meddevice_suite.compliance.signed_records"

# Record type -> dotted path of its signed content: content(doc) -> dict, a function or SignedFields.
# Every record type with a signed act in signing.SIGNING_ACTIONS is declared here (test_signature_
# integrity checks it). Widening a declaration changes what earlier signatures bind: a field added to
# one is bound only when set (as item_master does), so a signature that verified keeps verifying.
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
	# Declared with fingerprint version 2 (independent review 2026-10-15); compliance/signed_records.py.
	"Document Revision": f"{SIGNED_RECORDS}.DOCUMENT_REVISION",
	"Controlled Document": f"{SIGNED_RECORDS}.CONTROLLED_DOCUMENT",
	"Change Request": f"{SIGNED_RECORDS}.CHANGE_REQUEST",
	"Lot Release": f"{SIGNED_RECORDS}.LOT_RELEASE",
	"Supplier Approval": f"{SIGNED_RECORDS}.SUPPLIER_APPROVAL",
	"Customer Licence": f"{SIGNED_RECORDS}.CUSTOMER_LICENCE",
	"Work Task": f"{SIGNED_RECORDS}.WORK_TASK",
	"Calibration Record": f"{SIGNED_RECORDS}.CALIBRATION_RECORD",
	"Maintenance Record": f"{SIGNED_RECORDS}.MAINTENANCE_RECORD",
	"Equipment Qualification": f"{SIGNED_RECORDS}.EQUIPMENT_QUALIFICATION",
	"Training Matrix": f"{SIGNED_RECORDS}.TRAINING_MATRIX",
	"Training Record": f"{SIGNED_RECORDS}.TRAINING_RECORD",
	"Risk Acceptability Policy": f"{SIGNED_RECORDS}.RISK_ACCEPTABILITY_POLICY",
	"Risk Assessment": f"{SIGNED_RECORDS}.RISK_ASSESSMENT",
	"Risk Management Report": f"{SIGNED_RECORDS}.RISK_MANAGEMENT_REPORT",
	"Risk Review": f"{SIGNED_RECORDS}.RISK_REVIEW",
}

# Version 1 only, and never changed: version-1 signatures were taken over these record types'
# declared content and over the whole record of every other type, less the fields below.
VERSION_1_DECLARED = frozenset(
	{
		"Regulatory Licence",
		"Device Master Record",
		"Item",
		"Customer",
		"BOM",
		"Asset",
		"Operation",
		"Workstation",
		"Routing",
		"Warehouse",
		"Quality Inspection Template",
		"Batch",
	}
)

# Version 1: record type -> fields the system maintains after signing, left out of the whole record.

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
	# QA's impact assessment comes after the performer's signature, with its own (module 38).
	"Calibration Record": ("docstatus", "impact_status", "impact_decision", "assessed_by", "assessed_on"),
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
	# The record each listed record's change was made in, recorded as the change is made (module 36).
	"Change Request Affected Record": ("change_record_type", "change_record", "changed_on"),
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


# Version 1: (record type, act) -> fields the system maintains after that act's signature, beyond
# the record type's own. For an act added after its record type already carried signatures: widening
# SYSTEM_MAINTAINED would change what those earlier signatures bind, and a signature that verified
# must keep verifying (21 CFR 11.70). The act is part of the signature and its fingerprint, so each
# signature is checked against the content its own act binds.
ACT_MAINTAINED: dict[tuple[str, str], tuple[str, ...]] = {
	# The periodic review (module 30, 2026-10-12): the review alert's bookkeeping moves on after it
	# when the next review falls due.
	("Controlled Document", "Review"): ("review_alerted_for",),
}

# Version 1: record type (or child table) -> fields added after records of it were signed, bound once
# they hold a value. A record signed before the field existed was signed without it; binding the empty field
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
			"record_hash": record_fingerprint(doc, action, CURRENT_VERSION),
			"previous_signature": previous.name if previous else None,
			"fingerprint_version": CURRENT_VERSION,
		}
	)
	keyed_since()  # recorded before the first keyed signature, if the migrate has not recorded it
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


def record_fingerprint(doc: Document, action: str | None = None, version: int = CURRENT_VERSION) -> str:
	"""SHA-256 of the content a signature of `action` binds, in the signature's fingerprint version.

	The content fingerprint itself is not keyed: the signature's own fingerprint, which is, binds it.
	"""
	return sha256(signed_content(doc, action, version))


def signed_content(doc: Document, action: str | None = None, version: int = CURRENT_VERSION) -> dict:
	"""What a signature of `action` binds: the record type's declared content (SIGNED_CONTENT), or for
	a record type with no signed act, which only code and tests sign, the whole record."""
	declared = VERSION_1_DECLARED if version == FIRST_VERSION else SIGNED_CONTENT
	if doc.doctype in declared:
		return frappe.get_attr(SIGNED_CONTENT[doc.doctype])(doc)
	return version_1_content(doc, action)


def version_1_content(doc: Document, action: str | None = None) -> dict:
	"""The whole record, child rows included, less what the system maintains (see the version-1
	tables above): what a version-1 signature of a record type without declared content binds."""
	content = record_content(doc)
	for fieldname in (
		"name",
		*SYSTEM_MAINTAINED.get(doc.doctype, ()),
		*ACT_MAINTAINED.get((doc.doctype, action), ()),
	):
		content.pop(fieldname, None)
	without_unset_new_fields(content)
	for rows in content.values():
		if isinstance(rows, list):
			for row in rows:
				if isinstance(row, dict):
					# A row's docstatus is its parent's, set as the parent is submitted.
					for fieldname in ("parent", "docstatus", *SYSTEM_MAINTAINED.get(row.get("doctype"), ())):
						row.pop(fieldname, None)
					without_unset_new_fields(row)
	return content


def without_unset_new_fields(content: dict) -> None:
	"""Leave out the fields of `BOUND_WHEN_SET` that hold no value."""
	for fieldname in BOUND_WHEN_SET.get(content.get("doctype"), ()):
		if content.get(fieldname) in (None, ""):
			content.pop(fieldname, None)


def signature_fingerprint(signature: Document | frappe._dict, previous_hash: str) -> str | None:
	"""The signature's fingerprint in its own version (fingerprint.py); None for an unknown version.

	The name as it was at signing: renaming a record rewrites `reference_name` in every signature,
	which must not read as tampering; verification checks that name is one the record has had."""
	content = [
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
	version = version_of(signature)
	if version == FIRST_VERSION:
		return sha256(content)
	if version == KEYED_VERSION:
		return keyed_sha256(TRAIL.doctype, [version, *content])
	return None


def stored_signatures(doctype: str, name: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["name", *FINGERPRINTED, "previous_signature", "signature_hash", "fingerprint_version"],
		order_by="signed_at asc, creation asc",
	)


def signature_problems(doctype: str, name: str) -> list[str]:
	"""What is wrong with the record's chain of signatures; an empty list means every signature is
	intact (fingerprint.chain_problems, the rule the audit trail is verified by too)."""
	signatures = stored_signatures(doctype, name)
	if not signatures:
		return []
	return chain_problems(TRAIL, signatures, record_names(doctype, name), signature_fingerprint)


def verify_signatures(doctype: str, name: str) -> bool:
	"""Whether every signature on the record is intact and the chain is unbroken.

	The chain is followed by its links, as the audit trail's is (`audit.verify_events`), not by
	time: a server clock set back between two signings orders them wrongly by time, which must not
	read as tampering. An altered signature, one removed from the chain, one inserted into it, a
	second one claiming the same place and one moved here from another record are detected.
	"""
	return not signature_problems(doctype, name)


def is_unchanged_since(signature: Document | frappe._dict) -> bool:
	"""Whether the signed record's content is still exactly what was signed."""
	doc = frappe.get_doc(signature.reference_doctype, signature.reference_name)
	return record_fingerprint(doc, signature.action, version_of(signature)) == signature.record_hash


def signed_content_report(doctype: str, name: str) -> list[dict]:
	"""For a reviewer, each signature on the record in the order signed, and whether the record still
	holds the content it binds (21 CFR 11.70; independent review 2026-10-15):

	- Unchanged: it does.
	- Changed and signed again: what it binds changed afterwards, and a later signature binds the
	  record as it now is (a revision reviewed after its author signed, a master approved again).
	- Changed since signing: no signature binds the record as it now is; the audit trail accounts
	  for the change, and the changes are listed (made through the system, under its controls).
	- Changed outside the audit trail: undoing what the audit trail recorded since does not bring
	  back what was signed, so the record was changed past the system.

	Only what the signature binds counts (SIGNED_CONTENT, or the version-1 rules for a version-1
	signature): what the system maintains, and what is changed after submission where the record
	type allows it, are not part of it."""
	signatures = in_chain_order(stored_signatures(doctype, name), TRAIL.link)
	if not signatures:
		return []
	doc = frappe.get_doc(doctype, name)
	fingerprints: dict[tuple[int, str], str] = {}

	def holds(signature: frappe._dict) -> bool:
		key = (version_of(signature), signature.action)
		if key not in fingerprints:
			fingerprints[key] = record_fingerprint(doc, signature.action, key[0])
		return fingerprints[key] == signature.record_hash

	held = [holds(signature) for signature in signatures]
	events = None
	report = []
	for index, signature in enumerate(signatures):
		later = next((s for s, h in zip(signatures[index + 1 :], held[index + 1 :], strict=True) if h), None)
		changes = []
		if held[index]:
			content = UNCHANGED
		elif later:
			content = SIGNED_AGAIN
		else:
			if events is None:
				events = history(doctype, name)
			changes = changes_since(doc, signature, events)
			content = UNACCOUNTED if changes is None else CHANGED
		report.append(
			{
				"signature": signature.name,
				"action": signature.action,
				"meaning": signature.meaning,
				"signer_name": signature.signer_name,
				"signed_at": signature.signed_at,
				"content": content,
				"finding": content in CONTENT_FINDINGS,
				"message": content_message(signature, content, later, changes or []),
			}
		)
	return report


def changes_since(doc: Document, signature: frappe._dict, events: list[frappe._dict]) -> list | None:
	"""The audit events, newest first, whose undoing brings back what the signature binds: the changes
	the audit trail records since it. None when undoing the trail does not bring it back."""
	state = copy_of(doc)
	undone = []
	for event in events[:HISTORY_LIMIT]:
		changes = as_data(event.changes) or {}
		if "snapshot" in changes:  # the record's creation: nothing before it to undo
			return None
		undo(state, changes)
		undone.append(event)
		if record_fingerprint(state, signature.action, version_of(signature)) == signature.record_hash:
			return undone
	return None


def content_message(signature: frappe._dict, content: str, later: frappe._dict | None, changes: list) -> str:
	signed = _("{0} ({1}), signed by {2} on {3}").format(
		_(signature.action), _(signature.meaning), signature.signer_name, format_datetime(signature.signed_at)
	)
	if content == UNCHANGED:
		return _("{0}: the record still holds what was signed.").format(signed)
	if content == SIGNED_AGAIN:
		return _(
			"{0}: what it signed was changed afterwards, and {1} by {2} signed the record as it now is."
		).format(signed, _(later.action), later.signer_name)
	if content == CHANGED:
		recorded = "; ".join(recorded_change(event) for event in reversed(changes))
		return _(
			"{0}: the record has changed since it was signed, and no later signature covers the change. "
			"The audit trail records the change: {1}."
		).format(signed, recorded)
	return _(
		"{0}: the record has changed since it was signed, and the audit trail does not account for the "
		"change: it was made outside the system. Treat it as a data-integrity incident."
	).format(signed)


def recorded_change(event: frappe._dict) -> str:
	when = format_datetime(event.event_at)
	if event.reason:
		return _("{0} by {1} on {2}, reason: {3}").format(_(event.action), event.user, when, event.reason)
	return _("{0} by {1} on {2}").format(_(event.action), event.user, when)
