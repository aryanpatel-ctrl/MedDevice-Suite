"""The built-in Administrator account on a working site (URS-CMP-039; 21 CFR 11.10(d), 11.100(a),
11.300; master plan section 10: "the built-in super-user disabled for daily use"; go-live check V9).

Administrator is shared by nobody in particular and holds every permission in Frappe, past the
permission matrix and the configuration lock. Once a working site is set up, it does not sign
in, by the login page or the API, and a session it already holds ends at its next request:
system administration is done by named System Administrator accounts, so every action is a
person's.

For maintenance that needs it, IT opens a window from the server, in the site's configuration:
`"administrator_login_until": "YYYY-MM-DD HH:MM"`. It closes by itself at that time; every
sign-in is in the login history (kept for its retention period, URS-CMP-024). Development sites
(developer mode), test sites and the pilot keep Administrator, which their setup and tests use,
and so does a site whose setup wizard is not complete yet.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from meddevice_suite.setup.site_flags import site_flag

ADMINISTRATOR = "Administrator"
WINDOW_KEY = "administrator_login_until"


def maintenance_window_open() -> bool:
	until = frappe.conf.get(WINDOW_KEY)
	if not until:
		return False
	try:
		return now_datetime() < get_datetime(until)
	except Exception:
		return False  # a window that cannot be read is closed


def administrator_allowed() -> bool:
	"""Whether the Administrator account may be used on this site now."""
	if not frappe.is_setup_complete():
		return True  # the setup wizard is run as Administrator
	if site_flag("developer_mode") or site_flag("allow_tests") or site_flag("pilot_site"):
		return True
	return maintenance_window_open()


def refusal() -> str:
	return _(
		"The Administrator account is not used on this site. Sign in with your own System "
		"Administrator account; for maintenance, IT opens a time-limited window on the server."
	)


def refuse_administrator_login(login_manager) -> None:
	"""on_login, before the session is made: the login page and the API's login."""
	if login_manager.user == ADMINISTRATOR and not administrator_allowed():
		frappe.throw(refusal(), frappe.AuthenticationError)


def refuse_administrator_requests() -> None:
	"""auth_hooks, on every request: a session Administrator already holds, or its API keys."""
	if frappe.session.user == ADMINISTRATOR and not administrator_allowed():
		raise frappe.AuthenticationError(refusal())
