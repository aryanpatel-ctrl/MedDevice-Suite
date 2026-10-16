"""What the browser test sheet of D1 Customer Master starts from (URS-MST-008, URS-MST-001), on a test
site only (cypress/integration/sheet_d1.js).

The sheet's tests act on the screens as Sales, QA and Stores: Sales records a customer and its
licence, QA verifies, rejects and withdraws licences and exempts a customer with signatures, and the
sales orders and deliveries of the customer are refused or made accordingly. Its product, prices and
the stock it delivers from are the standard flow's (`ui_journey.prepare_journey`, run first). This
module adds what the pilot's demo customers stand for on the pilot site: a customer QA has exempted
from the licence (the sheet's City Hospital (Demo)) and one with a licence QA has verified (Regional
Medical Distributor (Demo)), made afresh each run, as the system makes them, by the run's job users
with their signatures; and a second customer group, for the change the sheet makes under change
control. The approved change request the sheet's last test names is made by `approve_customer_change`
when that test reaches it, raised by Sales and assessed and approved by QA with signatures.

Only the Administrator who runs the tests prepares the sheet.
"""

import frappe
from frappe import _
from frappe.utils import today

from meddevice_suite.master_data import customer_licence
from meddevice_suite.qa.ui_journey import PRODUCT, acting_as, store
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.setup.fixtures.records import LICENCE_TYPE, ensure_licence

CUSTOMER_GROUP = "Commercial"  # the group the sheet's customers are made in
OTHER_GROUP = "Hospitals (browser test)"  # the group the sheet moves its customer to


def require_administrator() -> None:
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)


@frappe.whitelist(methods=["POST"])
def prepare_sheet(password: str) -> dict:
	"""The customers the sheet starts from, made with the run's password of the job users
	(ui_users.prepare_job_users), and the names it works with."""
	require_administrator()
	if not password:
		frappe.throw(_("Give the run's password of the job users to prepare the sheet."))
	if not frappe.db.exists("Customer Group", OTHER_GROUP):
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": OTHER_GROUP,
				"parent_customer_group": "All Customer Groups",
				"is_group": 0,
			}
		).insert()
	suffix = frappe.generate_hash(length=10)

	# The sheet's Regional Medical Distributor (Demo): recorded by Sales, its licence verified by QA.
	licensed = new_customer(f"D1 Regional Medical Distributor {suffix}")
	ensure_licence(  # also makes the licence type the sheet records licences under
		licensed,
		lambda: acting_as(email_for("Sales")),
		lambda: acting_as(email_for("QA Manager")),
		password,
		"Licence copy checked against the authority's register (browser test)",
	)
	# The sheet's City Hospital (Demo): QA decides with a signature that it buys without a licence.
	exempt = new_customer(f"D1 City Hospital {suffix}")
	with acting_as(email_for("QA Manager")):
		customer_licence.exempt_from_licence(
			frappe.get_doc("Customer", exempt),
			password=password,
			reason="Hospital buying for its own use (browser test)",
		)
	return {
		"customer_name": f"TEST-D1 Medical Stores {suffix}",
		"customer_group": CUSTOMER_GROUP,
		"other_group": OTHER_GROUP,
		"licence_type": LICENCE_TYPE,
		"licensed_customer": licensed,
		"exempt_customer": exempt,
		"product": PRODUCT,
		"finished_goods": store("Finished Goods"),
	}


def new_customer(customer_name: str) -> str:
	"""A customer recorded by Sales, as Sales records one."""
	with acting_as(email_for("Sales")):
		return (
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer_name,
					"customer_type": "Company",
					"customer_group": CUSTOMER_GROUP,
					"territory": "India",
				}
			)
			.insert()
			.name
		)


@frappe.whitelist(methods=["POST"])
def approve_customer_change(customer: str, password: str) -> str:
	"""A minor change request listing the customer, raised by Sales and assessed and approved by QA
	with signatures (module 36), for the sheet to change the customer under it. Its name."""
	from meddevice_suite.qms import change_control

	require_administrator()
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} does not exist.").format(customer))
	with acting_as(email_for("Sales")):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Move {customer} to another customer group (browser test)",
				# The request offers no customer type of change; a customer is sold the products.
				"change_type": "Product or BOM",
				"description": f"Customer group of {customer} changed (browser test).",
				"reason": "The customer is now a hospital (browser test).",
				"affected_records": [{"reference_doctype": "Customer", "reference_name": customer}],
				"actions": [
					{
						"description": f"Change the customer group of {customer}",
						"action_owner": email_for("Sales"),
						"due_date": today(),
					}
				],
			}
		).insert()
		change_control.submit_for_assessment(request.name)
	with acting_as(email_for("QA Manager")):
		request = frappe.get_doc("Change Request", request.name)
		request.update(
			{
				**dict.fromkeys(change_control.IMPACT_AREAS, "No"),
				"impact_summary": "A customer's grouping; no product, risk or regulatory impact (browser test).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason="Assessed (browser test)")
		change_control.approve(
			frappe.get_doc("Change Request", request.name),
			password=password,
			reason="Approved (browser test)",
		)
	return request.name
