"""Quality Settings: the adjustable values of the validated system (URS-PLT-012, URS-PLT-013, URS-CMP-037).

The alert lead times, the account lockout and the password and session rules are values the client's QA sets on screen, each
within fixed bounds. Everything else that decides how the system behaves — the rules, the
signature meanings, the lot statuses, the roles and their permissions, the workflows, who
receives an alert (people are chosen by the roles they hold) — stays in code, where it changes
only through a release (decided 2026-10-11, master plan section 21's review of hard-coded values).

A change to a value is a change to the validated system (ISO 13485 4.1.6, 21 CFR 11.10(a)): it is
saved only under an approved change request that lists Quality Settings, the request is recorded,
the version raised, and the audit trail keeps the old and new values. Until QA changes a value, the
value decided with the client applies.
"""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, today

DOCTYPE = "Quality Settings"
CHANGE_REQUEST, CHANGED_UNDER, VERSION, EFFECTIVE_FROM = (
	"change_request",
	"changed_under",
	"version",
	"effective_from",
)


@dataclass(frozen=True)
class Setting:
	default: int
	low: int
	high: int


# fieldname -> the value until QA changes it, and its bounds (decided 2026-10-07/08, kept 2026-10-11)
SETTINGS: dict[str, Setting] = {
	"lot_expiry_warning_days": Setting(30, 1, 365),
	"supplier_certificate_warning_days": Setting(30, 1, 365),
	"customer_licence_warning_days": Setting(30, 1, 365),  # module 5, decided 2026-10-11
	"document_review_warning_days": Setting(30, 1, 365),
	"change_action_warning_days": Setting(7, 1, 90),
	"default_effectiveness_check_days": Setting(90, 0, 730),
	"login_lockout_attempts": Setting(5, 3, 10),
	"login_lockout_minutes": Setting(15, 5, 1440),
	# Access policy (URS-CMP-037), decided 2026-10-11: 3 is zxcvbn's "safely unguessable"
	"password_strength": Setting(3, 3, 4),
	"password_expiry_days": Setting(90, 30, 365),
	"session_idle_minutes": Setting(30, 5, 480),
	# Supplier rating (URS-PUR-002), decided 2026-10-11
	"supplier_rating_quality_weight": Setting(70, 0, 100),
	"supplier_rating_threshold": Setting(80, 50, 100),
	# Reminders (URS-PLT-014), decided 2026-10-11: overdue this many days, escalated to QA and Management
	"reminder_escalation_days": Setting(7, 1, 90),
	# Important files repository (URS-PLT-019), decided 2026-10-12: a licence's or certificate's renewal
	"file_validity_warning_days": Setting(60, 1, 365),
}


def raw(field: str) -> str | None:
	"""What is saved for a field of the settings, as stored, or None when nothing is: Frappe reads
	an unset number of a single as 0 and an unset date as 0001-01-01, which are not "never set"."""
	singles = frappe.qb.DocType("Singles")  # a single's values; the table has no creation to sort by
	rows = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == DOCTYPE) & (singles.field == field))
		.run()
	)
	value = rows[0][0] if rows else None
	return None if value in (None, "") else value


def stored(field: str) -> int | None:
	"""The number saved for a setting, or None when QA has never set it."""
	saved = raw(field)
	return None if saved is None else cint(saved)


def value(field: str) -> int:
	"""The setting in force: what QA saved, or the decided default."""
	saved = stored(field)
	return SETTINGS[field].default if saved is None else saved


def validate(doc: Document) -> None:
	"""Each value within its bounds; a change only under an approved change request that lists
	Quality Settings, which is then recorded with a new version; the record of changes is set by
	this rule only."""
	for field, setting in SETTINGS.items():
		if doc.get(field) in (None, ""):
			doc.set(field, value(field))
		number = cint(doc.get(field))
		if not setting.low <= number <= setting.high:
			frappe.throw(
				_("{0} is between {1} and {2}.").format(
					_(doc.meta.get_label(field)), setting.low, setting.high
				)
			)
		doc.set(field, number)
	changed = [field for field in SETTINGS if cint(doc.get(field)) != value(field)]
	stored_record = {field: raw(field) for field in (CHANGED_UNDER, VERSION, EFFECTIVE_FROM)}
	for field, current in stored_record.items():
		if as_text(doc.get(field), field) != as_text(current, field):
			frappe.throw(_("Quality Settings records its own changes."), frappe.PermissionError)
	request, doc.change_request = doc.get(CHANGE_REQUEST), None  # named afresh for each change
	if not changed:
		return
	# One save at a time: the version below is read and raised under this lock.
	frappe.db.sql("select value from `tabSingles` where doctype = %s for update", DOCTYPE)
	if not request:
		frappe.throw(
			_("Quality Settings change only under an approved change request that lists them."),
			title=_("Change control"),
		)
	from meddevice_suite.qms.change_control import require_approved_change  # it reads these settings

	require_approved_change(request, DOCTYPE, DOCTYPE)
	if used_before(request):
		frappe.throw(
			_(
				"Change request {0} has already changed Quality Settings; each change has its own request."
			).format(request),
			title=_("Change control"),
		)
	doc.set(CHANGED_UNDER, request)
	doc.set(VERSION, (stored(VERSION) or 1) + 1)
	doc.set(EFFECTIVE_FROM, today())


def used_before(request: str) -> bool:
	"""A request a change of the settings was already made under: the one recorded now, or one an
	earlier change recorded (each change's audit event keeps the request it was made under)."""
	return raw(CHANGED_UNDER) == request or bool(
		frappe.db.exists("Audit Event", {"reference_doctype": DOCTYPE, "changes": ["like", f"%{request}%"]})
	)


def as_text(value: object, field: str) -> str:
	"""A stored value as text, so that a form's empty string, None and a date object compare alike."""
	if field == VERSION:
		return str(cint(value) if value not in (None, "") else 1)
	if field == EFFECTIVE_FROM:
		return str(getdate(value)) if value else ""
	return str(value or "")


def on_update(doc: Document) -> None:
	"""The site's sign-in rules (lockout, passwords, sessions) follow the settings at once."""
	from meddevice_suite.compliance.access_policy import apply_access_policy  # it builds on this module

	apply_access_policy()
