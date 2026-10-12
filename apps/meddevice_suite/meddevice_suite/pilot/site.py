"""Bring a new pilot site to the state the walk-through starts from.

	bench --site pilot.localhost execute meddevice_suite.pilot.site.prepare_pilot_site

Completes the setup wizard with a fictional Indian demo company. Refuses to run
on a site that is not marked as a pilot site.
"""

from meddevice_suite.pilot import refuse_unless_pilot_site
from meddevice_suite.qa.site_setup import complete_setup_wizard, ensure_fiscal_years, with_fiscal_year

PILOT_COMPANY = {
	"language": "English",
	"country": "India",
	"timezone": "Asia/Kolkata",
	"currency": "INR",
	"company_name": "Demo Medical Devices",
	"company_abbr": "DMD",
	"chart_of_accounts": "Standard",
	"setup_demo": 0,
	"enable_audit_trail": 1,
	"company_gstin": "",
}


def prepare_pilot_site() -> None:
	refuse_unless_pilot_site()
	complete_setup_wizard(with_fiscal_year(PILOT_COMPANY))
	ensure_fiscal_years()  # the pilot runs for months: next year's postings find their year
