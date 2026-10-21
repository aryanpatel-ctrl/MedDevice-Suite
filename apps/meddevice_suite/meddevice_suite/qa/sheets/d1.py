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
control. The approved change request D1-T14 names is made by `approve_customer_change` when that
test reaches it, raised by Sales and assessed and approved by QA with signatures.

D1-T15 and D1-T16 start from records of their own, so that they run whatever the rows before them
did: a customer in use (a sales order submitted under its verified licence) whose group Sales has
changed under an approved change request, for QA to read in the audit trail; and a customer with a
verified licence and one awaiting QA, which nobody, the Administrator included, deletes or cancels.

Only the Administrator who runs the tests prepares the sheet.
"""

import frappe
from frappe import _
from frappe.utils import add_days, get_fullname, today

from meddevice_suite.master_data import customer_licence
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa.ui_journey import COMPANY, PRODUCT, PRODUCT_DETAILS, acting_as, store
from meddevice_suite.qa.ui_users import email_for, ensure_test_site
from meddevice_suite.setup.fixtures.records import LICENCE_TYPE, ensure_licence, licence_copy

CUSTOMER_GROUP = "Commercial"  # the group the sheet's customers are made in
OTHER_GROUP = "Hospitals (browser test)"  # the group the sheet moves its customer to
LICENCE_CHECKED = "Licence copy checked against the authority's register (browser test)"
CHANGE_REASON = "The customer is now a hospital (browser test)."  # why the sheet's customer changes


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
	verified_licence(licensed, password)  # also makes the licence type the sheet records licences under
	# The sheet's City Hospital (Demo): QA decides with a signature that it buys without a licence.
	exempt = new_customer(f"D1 City Hospital {suffix}")
	with acting_as(email_for("QA Manager")):
		customer_licence.exempt_from_licence(
			frappe.get_doc("Customer", exempt),
			password=password,
			reason="Hospital buying for its own use (browser test)",
		)

	# D1-T15: a customer in use whose group Sales changed under an approved change request.
	audited = new_customer(f"D1 Audited Distributor {suffix}")
	audited_licence = verified_licence(audited, password)
	submitted_order(audited)
	audited_change = approve_customer_change(audited, password)
	with acting_as(email_for("Sales")):
		customer = frappe.get_doc("Customer", audited)
		customer.customer_group = OTHER_GROUP
		customer.set(CHANGE_REQUEST, audited_change)
		customer.save()

	# D1-T16: a verified licence and one awaiting QA, which are never deleted or cancelled.
	kept = new_customer(f"D1 Licensed Pharmacy {suffix}")
	kept_licence = verified_licence(kept, password)
	with acting_as(email_for("Sales")):
		awaiting_licence = (
			frappe.get_doc(
				{
					"doctype": customer_licence.DOCTYPE,
					"customer": kept,
					"licence_type": LICENCE_TYPE,
					"licence_number": f"TEST-D1-{suffix}",
					"issuing_authority": "State Licensing Authority (browser test)",
					"valid_from": add_days(today(), -1),
					"valid_until": add_days(today(), 365),
					"licence_copy": licence_copy(f"{kept} awaiting"),  # a copy of its own
				}
			)
			.insert()
			.name
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
		"audited_customer": audited,
		"audited_licence": audited_licence,
		"audited_change_request": audited_change,
		"change_reason": CHANGE_REASON,
		"licence_checked": LICENCE_CHECKED,
		"kept_licence": kept_licence,
		"awaiting_licence": awaiting_licence,
		"full_names": {job: get_fullname(email_for(job)) for job in ("Sales", "QA Manager")},
	}


def verified_licence(customer: str, password: str) -> str:
	"""The customer's licence, recorded by Sales and verified by QA with a signature. Its name."""
	ensure_licence(
		customer,
		lambda: acting_as(email_for("Sales")),
		lambda: acting_as(email_for("QA Manager")),
		password,
		LICENCE_CHECKED,
	)
	return frappe.db.get_value(
		customer_licence.DOCTYPE,
		{"customer": customer, "docstatus": 1, "status": customer_licence.VERIFIED},
		"name",
	)


def submitted_order(customer: str) -> str:
	"""A sales order of the standard flow's product, submitted by Sales: the customer is in use."""
	delivery = add_days(today(), 7)
	with acting_as(email_for("Sales")):
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": customer,
				"company": COMPANY,
				"transaction_date": today(),
				"delivery_date": delivery,
				"items": [
					{"item_code": PRODUCT, "qty": 1, "rate": PRODUCT_DETAILS[2], "delivery_date": delivery}
				],
			}
		).insert()
		order.submit()
		return order.name


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
				"reason": CHANGE_REASON,
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
