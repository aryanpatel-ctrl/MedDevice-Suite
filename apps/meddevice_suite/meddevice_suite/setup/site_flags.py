"""Switches that mark what a site is for: a test site, a pilot site.

Frappe merges the bench-wide common_site_config.json into every site's
configuration, so a switch set there would mark every site on the bench, the
working site included. A switch counts only when set to exactly 1 or true for
the site itself, never by the bench-wide file, and never as text such as "0".
"""

import os

import frappe


def site_flag(key: str) -> bool:
	value = frappe.conf.get(key)
	if isinstance(value, str) or value not in (1, True):
		return False
	common = os.path.join(frappe.local.sites_path, "common_site_config.json")
	return not (os.path.exists(common) and key in frappe.get_file_json(common))
