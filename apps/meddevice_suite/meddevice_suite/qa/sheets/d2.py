"""Data for the browser test sheet of data store D2, Vendor Master (cypress/integration/sheet_d2.js),
on a test site only.

Most of the sheet is walked on the screens from a supplier Purchase creates in the browser. Two of
its tests start from what only time makes, and are prepared here through the code's own services,
as the jobs would have done it:

- a supplier rated below the threshold for last month (URS-PUR-002): QA approved it, Purchase
  ordered from it with the line due in the middle of last month, Management approved the order,
  nothing arrived, and the system rated the finished month (`supplier_rating.rate`, as the daily job
  does): delivery 0, so the rating is due for QA's re-evaluation;
- a supplier in use (URS-MST-001): QA approved it, Purchase ordered from it and Management approved
  the order; Purchase raised a change request listing it, which QA assessed and approved with
  signatures.

Run after ui_journey.prepare_journey, which makes and approves the journey's raw materials (module
1) the sheet orders. Every supplier is new, its name made unique for the run. Only the Administrator
who runs the tests prepares them, with the run's password of the job users
(ui_users.prepare_job_users).
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, add_months, get_first_day, getdate, today

from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.master_data import supplier_rating
from meddevice_suite.qa.ui_journey import COMPANY, QUARANTINE, acting_as, store
from meddevice_suite.qa.ui_users import email_for, ensure_test_site

# The journey's raw materials, approved by QA (ui_journey.RAW_MATERIALS), in the sheet's roles.
POUCH = "UIJ-RM-POUCH"  # the sheet's RM-POUCH
NOT_LISTED = "UIJ-RM-CONNECTOR"  # the sheet's RM-CARTON: an item the supplier is not approved for
SILICONE = "UIJ-RM-SILICONE"  # the sheet's RM-SILICONE
GROUP = "Raw Material"
OTHER_GROUP = "TEST-D2 Packaging Suppliers"


@frappe.whitelist(methods=["POST"])
def prepare(password: str) -> dict:
	"""What the sheet starts from. Returns {"tag", "new_supplier", "items", "group", "other_group",
	"rated": {"supplier", "rating", "score", "period", "threshold"}, "in_use": {"supplier",
	"change_request"}}."""
	ensure_test_site()
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("The browser test sheets are prepared only by the Administrator."), frappe.PermissionError
		)
	if not password:
		frappe.throw(_("Give the run's password of the job users to sign the sheet's set-up."))
	tag = frappe.generate_hash(length=10)
	ensure_other_group()
	return {
		"tag": tag,
		# Typed by Purchase in D2-T01; unique, so every run starts from a supplier with no decision.
		"new_supplier": f"TEST-D2 Pouch Supplier {tag}",
		"items": {"pouch": POUCH, "not_listed": NOT_LISTED, "silicone": SILICONE},
		"group": GROUP,
		"other_group": OTHER_GROUP,
		"rated": rated_below_threshold(f"TEST-D2 Rated Supplier {tag}", password),
		"in_use": in_use_under_change_request(f"TEST-D2 Silicone Compounds {tag}", password),
	}


def ensure_other_group() -> None:
	"""The supplier group a supplier in use is moved to under its change request (D2-T14)."""
	if not frappe.db.exists("Supplier Group", OTHER_GROUP):
		frappe.get_doc(
			{
				"doctype": "Supplier Group",
				"supplier_group_name": OTHER_GROUP,
				"parent_supplier_group": "All Supplier Groups",
			}
		).insert(ignore_permissions=True)  # test-site set-up by the Administrator, checked by the caller


def rated_below_threshold(name: str, password: str) -> dict:
	"""A supplier with last month rated by the system below the threshold (D2-T13): one order line
	due on the 15th of last month, never received."""
	supplier = approved_supplier(name, POUCH, password)
	due = getdate(add_days(get_first_day(add_months(today(), -1)), 14))
	placed_order(supplier, POUCH, required_by=due, ordered_on=add_days(due, -10))
	rating = supplier_rating.rate(supplier, due)  # the daily job's service, for the finished month
	return {
		"supplier": supplier,
		"rating": rating.name,
		"score": rating.score,
		"period": rating.period,
		"threshold": rating.threshold,
		"standing": rating.standing,
	}


def in_use_under_change_request(name: str, password: str) -> dict:
	"""A supplier in use, with an approved change request that lists it (D2-T14)."""
	supplier = approved_supplier(name, SILICONE, password)
	placed_order(supplier, SILICONE, required_by=add_days(today(), 7), ordered_on=today())
	return {"supplier": supplier, "change_request": approved_change_request(supplier, password)}


def approved_supplier(name: str, item: str, password: str) -> str:
	"""Purchase creates the supplier; QA approves it for `item` with its certificate and a signature."""
	with acting_as(email_for("Purchase")):
		supplier = (
			frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_group": GROUP})
			.insert()
			.name
		)
	with acting_as(email_for("QA Manager")):
		certificate = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"iso-13485-{frappe.scrub(name)}.txt",
				"content": f"ISO 13485 certificate of {name} (browser test sheet D2)",
				"is_private": 1,
			}
		).insert()
		approval = frappe.get_doc(
			{
				"doctype": "Supplier Approval",
				"supplier": supplier,
				"certificate_number": f"ISO13485-{name}",
				"certificate_expiry": add_days(today(), 365),
				"certificate": certificate.file_url,
				"items": [{"item_code": item}],
			}
		).insert()
		sign_action(
			"Supplier Approval", approval.name, "Approve", password, "Audited (browser test sheet D2)"
		)
	return supplier


def placed_order(supplier: str, item: str, required_by, ordered_on) -> str:
	"""Purchase orders `item` from the supplier and sends the order for approval; Management
	approves it (module 48)."""
	with acting_as(email_for("Purchase")):
		order = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": supplier,
				"company": COMPANY,
				"transaction_date": ordered_on,
				"schedule_date": required_by,
				"set_warehouse": store(QUARANTINE),
				"items": [
					{
						"item_code": item,
						"qty": 10,
						"rate": 5,
						"schedule_date": required_by,
						"warehouse": store(QUARANTINE),
					}
				],
			}
		).insert()
		apply_workflow(order, "Send for Approval")
	with acting_as(email_for("Management")):
		apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")
	return order.name


def approved_change_request(supplier: str, password: str) -> str:
	"""A minor change request listing the supplier, raised by Purchase and assessed and approved by
	QA with signatures (module 36)."""
	from meddevice_suite.qms import change_control

	with acting_as(email_for("Purchase")):
		request = frappe.get_doc(
			{
				"doctype": "Change Request",
				"title": f"Move {supplier} to {OTHER_GROUP} (browser test sheet D2)",
				"change_type": "Supplier",
				"description": f"Supplier group of {supplier} changed to {OTHER_GROUP} (browser test).",
				"reason": "The supplier now supplies packaging only (browser test).",
				"affected_records": [{"reference_doctype": "Supplier", "reference_name": supplier}],
				"actions": [
					{
						"description": f"Change the supplier group of {supplier}",
						"action_owner": email_for("Purchase"),
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
				"impact_summary": "A grouping of the supplier only; its approval is unchanged (browser test).",
				"classification": "Minor",
				"effectiveness_check_days": 90,
			}
		)
		request.save()
		change_control.assess(request, password=password, reason="Assessed (browser test sheet D2)")
		change_control.approve(
			frappe.get_doc("Change Request", request.name),
			password=password,
			reason="Approved (browser test sheet D2)",
		)
	return request.name
