"""The pilot site of Phase 2: the client walks through the standard flow on demo data."""

import frappe
from frappe import _

from meddevice_suite.setup.site_flags import site_flag


def refuse_unless_pilot_site() -> None:
	"""Demo data and pilot setup run only on a site marked `"pilot_site": 1` in its
	site_config, so they can never reach a working or production site."""
	if not site_flag("pilot_site"):
		frappe.throw(_("{0} is not a pilot site; refusing to load demo data").format(frappe.local.site))
