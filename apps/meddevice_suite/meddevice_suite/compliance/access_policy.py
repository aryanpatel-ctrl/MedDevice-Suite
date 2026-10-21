"""Access policy: how a person signs in (URS-CMP-037; 21 CFR 11.10(d), 11.100, 11.300; master plan
section 10, "password policy, session timeout, account lockout").

Each person signs in with their own user ID (their email address) and password, never by a link
sent to their mailbox, which would let whoever reads the mail in without the password. A password
meets the strength QA sets and is changed after the number of days QA sets; a session ends after
the minutes of inactivity QA sets, so a terminal left signed in on the shop floor does not stay
open to the next person. Those three values, and the lockout after wrong passwords
(URS-PLT-013), are Quality Settings, changed only under change control; the rest is fixed here.

The values are written to the site's System Settings, which Frappe enforces at sign-in: on install,
on every migrate and as soon as QA changes them. A change made in System Settings by hand is refused
at once. Earlier passwords are not kept to refuse their reuse: 21 CFR 11.300 asks for unique
identification, aging and protection against misuse, which the rules above give, and keeping a
password history would mean replacing Frappe's own password change (decided 2026-10-11).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# System Settings that are not adjustable: fixed by the requirement, not by QA.
FIXED: dict[str, int] = {
	"enable_password_policy": 1,
	"login_with_email_link": 0,  # sign-in by a link mailed to the user, without the password
	"logout_on_password_reset": 1,  # a reset password ends every session signed in with the old one
	"allow_login_using_mobile_number": 0,  # the user ID is the email address only
	"allow_login_using_user_name": 0,
}


def session_expiry(minutes: int) -> str:
	"""Frappe's session expiry, HH:MM of inactivity."""
	return f"{minutes // 60:02d}:{minutes % 60:02d}"


def system_values() -> dict[str, int | str]:
	"""Every System Settings value this policy decides, as it should stand now."""
	from meddevice_suite.compliance.quality_settings import value  # the settings build on change control

	return {
		**FIXED,
		# Frappe locks an account once its failures exceed this number (esign.apply_lockout).
		"allow_consecutive_login_attempts": value("login_lockout_attempts") - 1,
		"allow_login_after_fail": value("login_lockout_minutes") * 60,
		"minimum_password_score": str(value("password_strength")),
		"force_user_to_reset_password": value("password_expiry_days"),
		"session_expiry": session_expiry(value("session_idle_minutes")),
	}


def apply_access_policy() -> None:
	"""Site configuration (on install and every migrate) and whenever QA changes the settings."""
	for field, wanted in system_values().items():
		frappe.db.set_single_value("System Settings", field, wanted)
		# As System Settings' own save does (set_defaults): the desk shows a setting from the
		# defaults, so they must say what is enforced, or the form shows Frappe's 170:00 for a
		# 30-minute session (found by browser test D12-T18, 2026-10-21).
		frappe.db.set_default(field, wanted)
	frappe.clear_cache(doctype="System Settings")


def same(field: str, left: object, right: object) -> bool:
	if field == "session_expiry":
		return str(left or "") == str(right or "")
	return cint(left) == cint(right)


def guard_system_settings(doc: Document, method: str | None = None) -> None:
	"""System Settings validate: what this policy decides is changed only in Quality Settings, under
	change control, or here in code; a hand edit is refused at once rather than undone at the next
	migrate. Other system settings save as before."""
	before = doc.get_doc_before_save()
	for field, wanted in system_values().items():
		changed = before is None or not same(field, before.get(field), doc.get(field))
		if changed and not same(field, doc.get(field), wanted):
			frappe.throw(
				_("{0} is set by the access policy: in Quality Settings, under change control.").format(
					_(doc.meta.get_label(field))
				),
				frappe.PermissionError,
			)
