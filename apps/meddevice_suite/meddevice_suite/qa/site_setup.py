"""Bring a disposable test site to a known state before tests run against it.

	bench --site <test-site> execute meddevice_suite.qa.site_setup.prepare_test_site

Completes the setup wizard with a fixed Indian test company, so the desk opens
on the workspace instead of the wizard. Refuses to run on a site that does not
allow tests, so it can never touch a working or production site.

The company's financial year is the Indian April-March year containing the day
the site is made, and the next year is kept ready before tests run: a fixed year
stops every stock posting on a fresh site once it has passed.
"""

from datetime import date

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.utils import add_years, getdate, today

from meddevice_suite.setup.site_flags import site_flag

TEST_COMPANY = {
	"language": "English",
	"country": "India",
	"timezone": "Asia/Kolkata",
	"currency": "INR",
	"company_name": "MedDevice Test Company",
	"company_abbr": "MTC",
	"chart_of_accounts": "Standard",
	"setup_demo": 0,
	"enable_audit_trail": 1,
	"company_gstin": "",
}


def fiscal_year_of(day) -> tuple[date, date]:
	"""The Indian financial year (1 April to 31 March) that contains `day`."""
	day = getdate(day)
	start = day.year if day.month >= 4 else day.year - 1
	return date(start, 4, 1), date(start + 1, 3, 31)


def with_fiscal_year(company: dict, day=None) -> dict:
	"""The wizard's company with the financial year containing `day` (today by default)."""
	start, end = fiscal_year_of(day or today())
	return {**company, "fy_start_date": str(start), "fy_end_date": str(end)}


def ensure_fiscal_years(day=None) -> None:
	"""The financial year containing `day` and the one after it exist, so a site made near the
	year end, or tests that post a year ahead, find a year for every posting date."""
	for when in (getdate(day or today()), getdate(add_years(day or today(), 1))):
		start, end = fiscal_year_of(when)
		if frappe.db.exists("Fiscal Year", {"year_start_date": start}):
			continue
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": f"{start.year}-{end.year}",
				"year_start_date": start,
				"year_end_date": end,
			}
		).insert(ignore_permissions=True)  # test and pilot site set-up, never a working site


def prepare_test_site() -> None:
	if not site_flag("allow_tests"):
		frappe.throw(f"{frappe.local.site} does not allow tests; refusing to prepare it as a test site")

	complete_setup_wizard(with_fiscal_year(TEST_COMPANY))


def before_tests() -> None:
	"""before_tests, test sites only: the financial years tests post in, then the approved stores."""
	if not site_flag("allow_tests"):
		return
	ensure_fiscal_years()
	approve_test_stores()


def complete_setup_wizard(company: dict) -> None:
	"""Complete the setup wizard with `company`, once; also used for the pilot site."""
	if frappe.is_setup_complete():
		return

	# The wizard reports most failures in its result rather than raising.
	result = setup_complete(company)
	if not frappe.is_setup_complete():
		frappe.throw(f"Setup wizard did not complete on {frappe.local.site}: {result}")


def approve_test_stores() -> None:
	"""before_tests, test sites only: every enabled store that holds stock marked approved by QA
	(module 7), so that other modules' tests move stock as on a site QA has set up. A test fixture:
	it writes the approval fields without the signature the act makes; the signed act, and stores
	left unapproved, are tested in master_data/test_warehouse.py."""
	if not site_flag("allow_tests"):
		return
	from frappe.utils import now_datetime

	from meddevice_suite.master_data import master_approval

	if not master_approval.configured("Warehouse"):
		return
	stores = frappe.get_all(
		"Warehouse",
		filters={
			"is_group": 0,
			"disabled": 0,
			master_approval.APPROVAL_STATUS: ["!=", master_approval.APPROVED],
		},
		pluck="name",
	)
	for store in stores:  # test fixture: a handful of stores, once per run
		frappe.db.set_value(
			"Warehouse",
			store,
			{
				master_approval.APPROVAL_STATUS: master_approval.APPROVED,
				master_approval.APPROVED_BY: "Administrator",
				master_approval.APPROVED_AT: now_datetime(),
			},
			update_modified=False,
		)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit - before the tests start, test sites only
