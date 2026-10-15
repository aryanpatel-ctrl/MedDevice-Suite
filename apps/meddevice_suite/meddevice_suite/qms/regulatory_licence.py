"""The plant's own licences, registrations and certificates (module 41; URS-REG-001 to URS-REG-006;
DFD 9E Regulatory Compliance, 9C alerts; CDSCO MDR 2017 Chapter IV; ISO 13485 4.1.1, 4.2.5;
21 CFR 11.50, 11.70).

QA records each one on a Regulatory Licence: its type (from QA's list), number, issuing authority,
site, the products it covers, its validity, and its copy, which is an Important File (module 61),
so the repository keeps every version, its fingerprint and who may read it. It counts only once QA
verifies it with an electronic signature, which pins the copy's version and fingerprint: a newer
copy uploaded afterwards is shown as not verified. QA rejects a draft, or withdraws a verified
licence the authority suspended or cancelled, with a signed reason.

A verified licence is never edited, cancelled or deleted. A renewal is a new licence naming the
one it renews; verifying it supersedes the old one in the same transaction, and a rejected renewal
leaves the old one as it was. Obligations with a due date (retention fee, surveillance audit) are
done by QA with the evidence as an Important File. Submissions to the authority (device and plant
master files, change notifications) are kept on the licence with their date and reference.

QA is alerted once when a licence or an obligation falls within QA's lead time (Quality Settings,
90 days, plan decision D-12) or is overdue, and a reminder stays open until it is renewed or done.
A change request that affects a licence (regulatory impact "Yes") names it before it is assessed.
QA owns licences (plan decision D-14).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.checks import QA_ROLE, is_qa, require
from meddevice_suite.compliance.esign import sign

DOCTYPE, TYPE_DOCTYPE = "Regulatory Licence", "Regulatory Licence Type"
DRAFT, VERIFIED, REJECTED, WITHDRAWN, SUPERSEDED = "Draft", "Verified", "Rejected", "Withdrawn", "Superseded"
EXPIRY_ALERT = "Regulatory Licence Expiring"
SAVEPOINT = "regulatory_licence_alert"


def date_text(value: object) -> str:
	return frappe.format_value(value, {"fieldtype": "Date"})


def has_expiry(licence_type: str | None) -> bool:
	return bool(licence_type and frappe.db.get_value(TYPE_DOCTYPE, licence_type, "has_expiry"))


# --- The record ----------------------------------------------------------------------------------


def validate(doc: Document, method: str | None = None) -> None:
	"""before_validate: submitted only by QA's signature; what a draft may hold."""
	if doc.get("_action") == "submit" and not doc.flags.regulatory_licence:
		frappe.throw(_("A licence counts once QA verifies it with a signature."), frappe.PermissionError)
	if doc.docstatus != 0:
		return
	if not doc.flags.regulatory_licence:
		keep_system_fields(doc)
	if doc.valid_from and doc.valid_until:
		require(
			getdate(doc.valid_until) > getdate(doc.valid_from),
			_("The licence is valid from a day up to a later one; check its dates."),
		)
	if doc.renews:
		renewed = frappe.db.get_value(DOCTYPE, doc.renews, ["status", "licence_type"], as_dict=True)
		require(
			renewed and renewed.status == VERIFIED,
			_("{0} is not a verified licence; only a verified licence is renewed.").format(doc.renews),
		)
		require(renewed.licence_type == doc.licence_type, _("A renewal is of the same type of licence."))
	items = [row.item_code for row in doc.get("products") or []]
	require(len(items) == len(set(items)), _("A product is listed once."))


# Set only by QA's acts; a draft saved through the API or the form never carries them.
SYSTEM_FIELDS = (
	"verified_file_version",
	"verified_file_sha256",
	"reason",
	"decided_by",
	"decided_at",
	"superseded_by",
)
OBLIGATION_SYSTEM_FIELDS = ("done_on", "done_by", "evidence")


def keep_system_fields(doc: Document) -> None:
	"""A draft is QA's entry of the licence as granted; what QA's later acts record (the
	verification, an obligation done, a submission, an alert) cannot be typed into it."""
	doc.status = DRAFT
	doc.expiry_alerted = 0
	for field in SYSTEM_FIELDS:
		doc.set(field, None)
	for row in doc.get("obligations") or []:
		for field in OBLIGATION_SYSTEM_FIELDS:
			row.set(field, None)
		row.alerted, row.added_after_verification = 0, 0
	require(not doc.get("submissions"), _("Submissions are recorded on a verified licence."))


def guard_after_verification(doc: Document, method: str | None = None) -> None:
	"""before_update_after_submit: a verified licence changes only by QA's acts (withdrawal,
	supersession, an obligation added or done, a submission recorded)."""
	require(doc.flags.regulatory_licence, _("A verified licence does not change."), frappe.PermissionError)


def refuse_delete(doc: Document, method: str | None = None) -> None:
	frappe.throw(_("A licence is never deleted."), frappe.PermissionError)


def refuse_cancel(doc: Document, method: str | None = None) -> None:
	frappe.throw(_("A licence is never cancelled; QA withdraws it."), frappe.PermissionError)


def locked(name: str) -> Document:
	table = frappe.qb.DocType(DOCTYPE)
	frappe.qb.from_(table).select(table.name).where(
		table.name == name
	).for_update().run()  # one act at a time
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return doc


def current_file_version(important_file: str) -> frappe._dict | None:
	rows = frappe.get_all(
		"Important File Version",
		filters={"parent": important_file, "parenttype": "Important File"},
		fields=["version", "file_sha256"],
		order_by="version desc",
		limit=1,
	)
	return rows[0] if rows else None


# --- The signed acts -----------------------------------------------------------------------------


def verify(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA verifies the licence against its copy: the copy's version and fingerprint are pinned, and
	the licence it renews is superseded, in one transaction."""
	doc = locked(doc.name)
	require(is_qa(), _("Only QA verifies a licence."), frappe.PermissionError)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(
		doc.licence_type and (doc.licence_number or "").strip() and (doc.issuing_authority or "").strip(),
		_("Record the licence's type, number and issuing authority before it is verified."),
	)
	require(doc.valid_from, _("Record the day the licence is valid from."))
	if has_expiry(doc.licence_type):
		require(doc.valid_until, _("This type of licence expires: record the day it is valid until."))
		require(
			getdate(doc.valid_until) >= getdate(today()),
			_("The licence expired on {0}; it cannot be verified.").format(date_text(doc.valid_until)),
		)
	if doc.renews:
		renewed = locked(doc.renews)
		require(
			renewed.docstatus == 1 and renewed.status == VERIFIED,
			_("{0} is {1}; only a verified licence is renewed.").format(
				doc.renews, _(renewed.status).lower()
			),
		)
		require(renewed.licence_type == doc.licence_type, _("A renewal is of the same type of licence."))
	require(doc.important_file, _("Attach the licence's copy as an important file."))
	file = frappe.get_doc("Important File", doc.important_file)
	file.check_permission("read")  # QA verifies only a copy they may read
	require(file.status == "Active", _("The copy {0} is archived.").format(doc.important_file))
	version = current_file_version(doc.important_file)
	require(version and version.file_sha256, _("The copy {0} has no file yet.").format(doc.important_file))
	doc.verified_file_version, doc.verified_file_sha256 = version.version, version.file_sha256
	doc.status, doc.reason = VERIFIED, reason
	doc.decided_by, doc.decided_at = frappe.session.user, now_datetime()
	sign(doc, "Verified", "Verify", password, reason, permission="read")
	doc.flags.regulatory_licence = True
	doc.flags.ignore_permissions = (
		True  # no role holds Frappe's submit permission; the signed act is the submit
	)
	doc.submit()
	if doc.renews:
		old = locked(doc.renews)
		old.status, old.superseded_by = SUPERSEDED, doc.name
		old.flags.regulatory_licence = True
		old.flags.audit_reason = _("Renewed by {0}").format(doc.name)
		old.flags.ignore_permissions = True  # part of the signed verification of its renewal
		old.save()


def reject(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA rejects a draft licence that does not match its copy or the authority's record."""
	doc = locked(doc.name)
	require(is_qa(), _("Only QA rejects a licence."), frappe.PermissionError)
	require(doc.docstatus == 0, _("{0} has been decided.").format(doc.name))
	require(reason, _("Say why the licence is rejected."))
	doc.status, doc.reason = REJECTED, reason
	doc.decided_by, doc.decided_at = frappe.session.user, now_datetime()
	sign(doc, "Rejected", "Reject", password, reason, permission="read")
	doc.flags.regulatory_licence = True
	doc.flags.ignore_permissions = True  # as for verify: the signed act is the only submit
	doc.submit()


def withdraw(doc: Document, password: str, reason: str | None = None) -> None:
	"""QA withdraws a verified licence the authority suspended or cancelled."""
	doc = locked(doc.name)
	require(is_qa(), _("Only QA withdraws a licence."), frappe.PermissionError)
	require(doc.docstatus == 1 and doc.status == VERIFIED, _("Only a verified licence is withdrawn."))
	require(reason, _("Say why the licence is withdrawn."))
	doc.status, doc.reason = WITHDRAWN, reason
	doc.decided_by, doc.decided_at = frappe.session.user, now_datetime()
	sign(doc, "Rejected", "Withdraw", password, reason, permission="read")
	doc.flags.regulatory_licence = True
	doc.flags.audit_reason = reason  # the audit trail records the withdrawal under QA's reason
	doc.flags.ignore_permissions = True  # the signed act is the only change after verification
	doc.save()


# --- Obligations and submissions (QA, recorded, not approvals) ------------------------------------


def check_evidence(important_file: str | None) -> None:
	require(important_file, _("Attach the evidence as an important file."))
	evidence = frappe.get_doc("Important File", important_file)
	evidence.check_permission("read")
	require(evidence.status == "Active", _("The evidence {0} is archived.").format(important_file))


@frappe.whitelist(methods=["POST"])
def complete_obligation(name: str, row: str, evidence: str) -> None:
	"""QA records that an obligation (a fee paid, an audit passed) was done, with its evidence."""
	doc = locked(name)
	require(is_qa(), _("Only QA records a licence's obligations."), frappe.PermissionError)
	require(
		doc.docstatus == 1 and doc.status == VERIFIED, _("Only a verified licence's obligations are done.")
	)
	line = next((o for o in doc.obligations if o.name == row), None)
	require(line, _("No such obligation on {0}.").format(name))
	require(not line.done_on, _("{0} was done on {1}.").format(line.obligation, date_text(line.done_on)))
	check_evidence(evidence)
	line.done_on, line.done_by, line.evidence = today(), frappe.session.user, evidence
	doc.flags.regulatory_licence = True
	doc.flags.audit_reason = _("Obligation {0} done").format(line.obligation)
	doc.flags.ignore_permissions = True  # QA's own record; the licence itself does not change
	doc.save()


@frappe.whitelist(methods=["POST"])
def add_obligation(name: str, obligation: str, due_date: str) -> None:
	"""QA records an obligation that arises after verification: the next retention fee, the next
	surveillance audit (URS-REG-004)."""
	doc = locked(name)
	require(is_qa(), _("Only QA records a licence's obligations."), frappe.PermissionError)
	require(doc.docstatus == 1 and doc.status == VERIFIED, _("Obligations are added to a verified licence."))
	require((obligation or "").strip(), _("Say what the obligation is."))
	require(due_date, _("Record when it is due."))
	doc.append(
		"obligations",
		{"obligation": obligation.strip(), "due_date": due_date, "added_after_verification": 1},
	)
	doc.flags.regulatory_licence = True
	doc.flags.audit_reason = _("Obligation {0} added").format(obligation.strip())
	doc.flags.ignore_permissions = True  # QA's record of what the licence requires next
	doc.save()


@frappe.whitelist(methods=["POST"])
def add_submission(
	name: str, submission_type: str, submitted_on: str, authority_reference: str, important_file: str
) -> None:
	"""QA records a submission to the authority under the licence (URS-REG-005)."""
	doc = locked(name)
	require(is_qa(), _("Only QA records a submission."), frappe.PermissionError)
	require(doc.status == VERIFIED, _("Submissions are recorded on a verified licence."))
	require((authority_reference or "").strip(), _("Record the authority's reference."))
	require(getdate(submitted_on) <= getdate(today()), _("A submission is recorded once it is made."))
	check_evidence(important_file)
	doc.append(
		"submissions",
		{
			"submission_type": submission_type,
			"submitted_on": submitted_on,
			"authority_reference": authority_reference.strip(),
			"important_file": important_file,
			"recorded_by": frappe.session.user,
		},
	)
	doc.flags.regulatory_licence = True
	doc.flags.audit_reason = _("Submission {0} recorded").format(authority_reference.strip())
	doc.flags.ignore_permissions = True  # QA's record of what was sent; the licence itself does not change
	doc.save()


# --- What falls due ------------------------------------------------------------------------------


def lead_day():
	return getdate(add_days(today(), quality_settings.value("regulatory_licence_warning_days")))


def renewed(name: str) -> bool:
	"""A verified renewal exists: verifying it superseded this one."""
	return frappe.db.get_value(DOCTYPE, name, "status") == SUPERSEDED


def expiring(until: object = None) -> list[frappe._dict]:
	"""Verified licences that end by `until` (within the lead time, unless the reminders say
	otherwise) or have ended, not yet renewed."""
	return frappe.get_all(
		DOCTYPE,
		filters={"docstatus": 1, "status": VERIFIED, "valid_until": ["<=", getdate(until or lead_day())]},
		fields=["name", "licence_type", "licence_number", "valid_until", "expiry_alerted"],
		order_by="valid_until asc",
	)


def obligations_due(until: object = None) -> list[frappe._dict]:
	"""Undone obligations of verified licences due by `until` (within the lead time, unless the
	reminders say otherwise) or overdue."""
	licence, line = frappe.qb.DocType(DOCTYPE), frappe.qb.DocType("Regulatory Licence Obligation")
	return (
		frappe.qb.from_(licence)
		.join(line)
		.on(line.parent == licence.name)
		.select(
			licence.name,
			licence.licence_number,
			line.name.as_("row"),
			line.obligation,
			line.due_date,
			line.alerted,
		)
		.where(
			(licence.docstatus == 1)
			& (licence.status == VERIFIED)
			& line.done_on.isnull()
			& (line.due_date <= getdate(until or lead_day()))
		)
		.orderby(line.due_date)
		.run(as_dict=True)
	)


def send_expiry_alerts() -> None:
	"""Daily: QA is alerted once for each licence and each obligation within the lead time or
	overdue; one record's failure is logged and retried the next day."""
	notification = frappe.get_doc("Notification", EXPIRY_ALERT)
	for row in expiring():
		if not row.expiry_alerted:
			alert(
				notification,
				row.name,
				lambda name=row.name: frappe.db.set_value(
					DOCTYPE, name, "expiry_alerted", 1, update_modified=False
				),
			)
	for row in obligations_due():
		if not row.alerted:
			alert(
				notification,
				row.name,
				lambda r=row.row: frappe.db.set_value(
					"Regulatory Licence Obligation", r, "alerted", 1, update_modified=False
				),
			)


def alert(notification: Document, name: str, mark) -> None:
	frappe.db.savepoint(SAVEPOINT)
	try:
		notification.send(frappe.get_doc(DOCTYPE, name))
		mark()  # bookkeeping of the alert, kept out of the signed record (esign.SYSTEM_MAINTAINED)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"Regulatory licence alert for {name} could not be sent")
	else:
		frappe.db.release_savepoint(SAVEPOINT)


# --- What QA's signature binds -------------------------------------------------------------------


def signed_content(doc: Document) -> dict:
	"""What QA's verification binds (compliance.esign.SIGNED_CONTENT): the licence as verified and
	the copy it was verified against; not its status, the obligations done or the submissions
	recorded afterwards, which are QA's later records."""
	return {
		"licence_type": doc.licence_type,
		"licence_number": doc.licence_number,
		"issuing_authority": doc.issuing_authority,
		"site": doc.site or None,
		"valid_from": str(doc.valid_from) if doc.valid_from else None,
		"valid_until": str(doc.valid_until) if doc.valid_until else None,
		"renews": doc.renews or None,
		"important_file": doc.important_file,
		"verified_file_version": doc.verified_file_version,
		"verified_file_sha256": doc.verified_file_sha256,
		"products": sorted(row.item_code for row in doc.get("products") or []),
		"obligations": sorted(
			(row.obligation, str(row.due_date))
			for row in doc.get("obligations") or []
			if not row.added_after_verification
		),
	}


# --- Change control (URS-REG-006) -----------------------------------------------------------------


def verified_licences(rows) -> set[str]:
	names = [row.reference_name for row in rows if row.reference_doctype == DOCTYPE and row.reference_name]
	return set(
		frappe.get_all(
			DOCTYPE, filters={"name": ["in", names or [""]], "docstatus": 1, "status": VERIFIED}, pluck="name"
		)
	)


def require_licence_for_regulatory_change(doc: Document) -> None:
	"""A change that needs regulatory notification names the licence or registration it affects."""
	require(
		doc.impact_regulatory != "Yes"
		or any(
			row.reference_doctype == DOCTYPE and row.reference_name in verified_licences(doc.affected_records)
			for row in doc.affected_records
		),
		_("A change with regulatory impact lists the verified licence or registration it affects."),
	)
