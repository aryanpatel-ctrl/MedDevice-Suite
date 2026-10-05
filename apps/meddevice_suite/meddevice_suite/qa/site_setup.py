"""Bring a disposable test site to a known state before tests run against it.

	bench --site <test-site> execute meddevice_suite.qa.site_setup.prepare_test_site

Completes the setup wizard with a fixed Indian test company, so the desk opens
on the workspace instead of the wizard. Refuses to run on a site that does not
allow tests, so it can never touch a working or production site.
"""

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

TEST_COMPANY = {
	"language": "English",
	"country": "India",
	"timezone": "Asia/Kolkata",
	"currency": "INR",
	"company_name": "MedDevice Test Company",
	"company_abbr": "MTC",
	"chart_of_accounts": "Standard",
	"fy_start_date": "2026-04-01",
	"fy_end_date": "2027-03-31",
	"setup_demo": 0,
	"enable_audit_trail": 1,
	"company_gstin": "",
}


def prepare_test_site() -> None:
	if not frappe.conf.allow_tests:
		frappe.throw(f"{frappe.local.site} does not allow tests; refusing to prepare it as a test site")

	if frappe.is_setup_complete():
		return

	# The wizard reports most failures in its result rather than raising.
	result = setup_complete(TEST_COMPANY)
	if not frappe.is_setup_complete():
		frappe.throw(f"Setup wizard did not complete on {frappe.local.site}: {result}")
