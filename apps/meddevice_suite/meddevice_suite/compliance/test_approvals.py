import frappe
from frappe.client import submit as submit_through_api
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.audit_reason import stage_reason
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_roles import user_with_profile

ITEM = "MDS-WORKFLOW-ITEM"
CUSTOMER = "MDS Workflow Customer"
SUPPLIER = "MDS Workflow Supplier"
# One person holding both the requester's and the approver's roles.
BOTH_ROLES = "requester.and.approver@job-profiles.example.com"


def ensure_masters() -> None:
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_name": "Workflow test item",
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"gst_hsn_code": "90183910",  # catheters (for urine, stool)
			}
		).insert()
	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER,
				"customer_group": "Commercial",
				"territory": "India",
			}
		).insert()
	if not frappe.db.exists("Supplier", SUPPLIER):
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": SUPPLIER, "supplier_group": "Raw Material"}
		).insert()
	if not frappe.db.exists("User", BOTH_ROLES):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": BOTH_ROLES,
				"first_name": "Requester and approver",
				"send_welcome_email": 0,
				"roles": [
					{"role": role}
					for role in ("Purchase User", "Purchase Manager", "Sales User", "Sales Manager")
				],
			}
		).insert(ignore_permissions=True)


class ApprovalTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		ensure_masters()
		cls.sales = user_with_profile("Sales")
		cls.purchase = user_with_profile("Purchase")
		cls.approver = user_with_profile("Management")

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.flags.audit_cause = None

	def as_user(self, user: str):
		frappe.set_user(user)

	def quotation(self, user: str | None = None):
		self.as_user(user or self.sales)
		return frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": CUSTOMER,
				"transaction_date": today(),
				"valid_till": add_days(today(), 30),
				"items": [{"item_code": ITEM, "qty": 10, "rate": 125}],
			}
		).insert()

	def purchase_order(self, user: str | None = None):
		self.as_user(user or self.purchase)
		return frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": SUPPLIER,
				"transaction_date": today(),
				"schedule_date": add_days(today(), 7),
				"items": [{"item_code": ITEM, "qty": 100, "rate": 4, "schedule_date": add_days(today(), 7)}],
			}
		).insert()

	def act(self, user: str, doc, action: str):
		self.as_user(user)
		return apply_workflow(frappe.get_doc(doc.doctype, doc.name), action)

	def refused(self, action) -> None:
		"""The refusal comes once the submit is written; a request then rolls back, as here."""
		frappe.db.savepoint("refused")
		with self.assertRaises(frappe.ValidationError):
			action()
		frappe.db.rollback(save_point="refused")

	def state(self, doc) -> tuple[str, int]:
		row = frappe.db.get_value(doc.doctype, doc.name, ["workflow_state", "docstatus"], as_dict=True)
		return row.workflow_state, row.docstatus


@verifies("URS-CMP-019")
class TestQuotationPriceApproval(ApprovalTestCase):
	def test_an_approved_quotation_can_be_sent(self):
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")

		self.act(self.approver, quotation, "Approve")

		self.assertEqual(self.state(quotation), ("Approved", 1))

	def test_a_returned_quotation_is_revised_and_sent_again(self):
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")
		self.act(self.approver, quotation, "Return for Revision")

		self.as_user(self.sales)
		revised = frappe.get_doc("Quotation", quotation.name)
		revised.items[0].rate = 118
		revised.save()
		self.act(self.sales, quotation, "Send for Price Approval")
		self.act(self.approver, quotation, "Approve")

		self.assertEqual(self.state(quotation), ("Approved", 1))

	def test_sales_cannot_approve_their_own_price(self):
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")

		with self.assertRaises(frappe.ValidationError):
			self.act(self.sales, quotation, "Approve")
		self.assertEqual(self.state(quotation), ("Awaiting Price Approval", 0))


@verifies("URS-CMP-020")
class TestPurchaseApproval(ApprovalTestCase):
	def test_an_approved_purchase_order_is_issued(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.act(self.approver, order, "Approve")

		self.assertEqual(self.state(order), ("Approved", 1))

	def test_a_returned_purchase_order_goes_back_to_purchasing(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.act(self.approver, order, "Return for Revision")

		self.assertEqual(self.state(order), ("Draft", 0))

	def test_only_the_approver_cancels_and_gives_a_reason(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")

		self.as_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Purchase Order", order.name).cancel()

		self.as_user(self.approver)
		stage_reason("Purchase Order", order.name, "cancel", "Supplier cannot deliver")
		self.act(self.approver, order, "Cancel")

		self.assertEqual(self.state(order), ("Cancelled", 2))
		self.assertEqual(
			frappe.get_all(
				"Audit Event",
				filters={
					"reference_doctype": "Purchase Order",
					"reference_name": order.name,
					"action": "Cancelled",
				},
				pluck="reason",
			),
			["Supplier cannot deliver"],
		)


@verifies("URS-CMP-021")
class TestApprovalCannotBeBypassed(ApprovalTestCase):
	def test_an_approver_cannot_submit_a_draft_that_was_never_sent_for_approval(self):
		order = self.purchase_order()

		self.as_user(self.approver)
		self.refused(lambda: frappe.get_doc("Purchase Order", order.name).submit())

		self.assertEqual(self.state(order), ("Draft", 0))

	def test_the_api_cannot_submit_around_the_approval(self):
		quotation = self.quotation()

		self.as_user(self.approver)
		self.refused(lambda: submit_through_api(frappe.get_doc("Quotation", quotation.name).as_dict()))

		self.assertEqual(self.state(quotation), ("Draft", 0))

	def test_a_record_cannot_be_created_already_approved(self):
		self.as_user(self.approver)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Quotation",
					"quotation_to": "Customer",
					"party_name": CUSTOMER,
					"transaction_date": today(),
					"valid_till": add_days(today(), 30),
					"docstatus": 1,
					"items": [{"item_code": ITEM, "qty": 1, "rate": 125}],
				}
			).insert()

	def test_nobody_approves_what_they_raised_through_the_workflow(self):
		order = self.purchase_order(BOTH_ROLES)
		self.act(BOTH_ROLES, order, "Send for Approval")

		with self.assertRaises(frappe.ValidationError):
			self.act(BOTH_ROLES, order, "Approve")
		self.assertEqual(self.state(order), ("Awaiting Approval", 0))

	def test_nobody_approves_what_they_raised_by_submitting_directly(self):
		order = self.purchase_order(BOTH_ROLES)
		self.act(BOTH_ROLES, order, "Send for Approval")

		self.as_user(BOTH_ROLES)
		self.refused(lambda: frappe.get_doc("Purchase Order", order.name).submit())
		self.assertEqual(self.state(order), ("Awaiting Approval", 0))

	def test_a_requester_cannot_approve_by_submitting(self):
		# ERPNext lets sales users submit quotations; the permission matrix takes that away.
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")

		self.as_user(self.sales)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Quotation", quotation.name).submit()

		self.assertEqual(self.state(quotation), ("Awaiting Price Approval", 0))

	def test_submit_permission_alone_does_not_approve(self):
		# Second line of defence, should the permission matrix ever give submit to a requester.
		from meddevice_suite.compliance.approvals import on_submit

		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		doc = frappe.get_doc("Purchase Order", order.name)
		doc._doc_before_save = frappe.get_doc("Purchase Order", order.name)
		doc.workflow_state = "Approved"

		self.as_user(self.purchase)
		with self.assertRaises(frappe.ValidationError):
			on_submit(doc)

	def test_requesters_cannot_submit_their_own_records(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.as_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Purchase Order", order.name).submit()


@verifies("URS-CMP-031")
class TestAttributionIsPermanent(ApprovalTestCase):
	def test_the_api_cannot_pass_off_a_record_as_someone_elses_to_approve_it(self):
		order = self.purchase_order(BOTH_ROLES)
		self.act(BOTH_ROLES, order, "Send for Approval")

		self.as_user(BOTH_ROLES)
		forged = frappe.get_doc("Purchase Order", order.name).as_dict()
		forged.owner = self.approver
		self.refused(lambda: submit_through_api(forged))

		self.assertEqual(frappe.db.get_value("Purchase Order", order.name, "owner"), BOTH_ROLES)
		self.assertEqual(self.state(order), ("Awaiting Approval", 0))

	def test_who_created_a_record_and_when_never_change_on_save(self):
		order = self.purchase_order()
		created = frappe.db.get_value("Purchase Order", order.name, ["owner", "creation"])

		self.as_user(self.purchase)
		with self.assertRaises(frappe.ValidationError):
			frappe.client.set_value("Purchase Order", order.name, '{"owner": "%s"}' % self.approver)
		doc = frappe.get_doc("Purchase Order", order.name)
		doc.creation = add_days(doc.creation, -1)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

		self.assertEqual(frappe.db.get_value("Purchase Order", order.name, ["owner", "creation"]), created)


@verifies("URS-CMP-032")
class TestApprovalStateIsTruthful(ApprovalTestCase):
	def test_a_draft_cannot_be_labelled_approved(self):
		order = self.purchase_order(BOTH_ROLES)
		self.act(BOTH_ROLES, order, "Send for Approval")

		self.as_user(self.approver)
		with self.assertRaises(frappe.ValidationError):
			frappe.client.set_value("Purchase Order", order.name, "workflow_state", "Approved")

		self.assertEqual(self.state(order), ("Awaiting Approval", 0))

	def test_an_approved_record_cannot_be_labelled_cancelled_without_cancelling(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")

		self.as_user(self.approver)
		doc = frappe.get_doc("Purchase Order", order.name)
		doc.workflow_state = "Cancelled"
		try:
			doc.save()
		except frappe.ValidationError:
			pass

		self.assertEqual(self.state(order), ("Approved", 1))

	def test_the_requester_cannot_change_a_record_awaiting_approval(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.as_user(self.purchase)
		row = frappe.get_doc("Purchase Order", order.name).items[0].name
		with self.assertRaises(frappe.ValidationError):
			frappe.client.set_value("Purchase Order Item", row, "rate", 400)
		with self.assertRaises(frappe.ValidationError):
			frappe.client.set_value("Purchase Order", order.name, "workflow_state", "Draft")

		self.assertEqual(frappe.db.get_value("Purchase Order Item", row, "rate"), 4)

	def test_the_approver_may_correct_a_record_before_approving_it(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.as_user(self.approver)
		doc = frappe.get_doc("Purchase Order", order.name)
		doc.items[0].rate = 3.5
		doc.save()
		self.act(self.approver, order, "Approve")

		self.assertEqual(self.state(order), ("Approved", 1))
		self.assertEqual(frappe.db.get_value("Purchase Order", order.name, "grand_total"), 350)


@verifies("URS-CMP-033")
class TestApprovedPricesStand(ApprovalTestCase):
	def update_items(self, doc, **changes):
		from erpnext.controllers.accounts_controller import update_child_qty_rate

		row = doc.items[0]
		item = {
			"docname": row.name,
			"item_code": row.item_code,
			"qty": row.qty,
			"rate": row.rate,
			"conversion_factor": row.conversion_factor,
			"uom": row.uom,
			"schedule_date": row.get("schedule_date"),
			**changes,
		}
		update_child_qty_rate(doc.doctype, frappe.as_json([item]), doc.name)

	def test_an_approved_purchase_order_keeps_its_prices_and_quantities(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")

		self.as_user(self.approver)
		for change in ({"rate": 40}, {"qty": 1000}):
			# With a reason given, so only the approval stops it.
			stage_reason("Purchase Order", order.name, "update_after_submit", "Supplier changed the price")
			doc = frappe.get_doc("Purchase Order", order.name)
			self.refused(lambda doc=doc, change=change: self.update_items(doc, **change))

		self.assertEqual(frappe.db.get_value("Purchase Order", order.name, "grand_total"), 400)

	def test_an_approved_quotation_keeps_its_prices(self):
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")
		self.act(self.approver, quotation, "Approve")

		self.as_user(self.approver)
		doc = frappe.get_doc("Quotation", quotation.name)
		self.refused(lambda: self.update_items(doc, rate=1))

		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "grand_total"), 1250)

	def test_an_amended_purchase_order_needs_approval_again(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")
		self.as_user(self.approver)
		stage_reason("Purchase Order", order.name, "cancel", "Quantity changed by the supplier")
		self.act(self.approver, order, "Cancel")

		self.as_user(self.purchase)
		stage_reason("Purchase Order", order.name, "amend", "Quantity changed by the supplier")
		# Copied as the desk's Amend copies: fields marked no-copy, the state among them, start blank.
		amended = frappe.copy_doc(frappe.get_doc("Purchase Order", order.name), ignore_no_copy=False)
		amended.docstatus = 0
		amended.amended_from = order.name
		amended.items[0].qty = 50
		amended.insert()

		self.assertEqual(self.state(amended), ("Draft", 0))
		self.as_user(self.approver)
		self.refused(lambda: frappe.get_doc("Purchase Order", amended.name).submit())

	def test_a_copy_of_an_approved_order_starts_as_a_draft(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")

		self.as_user(self.purchase)
		copy = frappe.copy_doc(frappe.get_doc("Purchase Order", order.name), ignore_no_copy=False)
		copy.docstatus = 0  # as the desk's Duplicate does
		copy.workflow_state = "Approved"
		with self.assertRaises(frappe.ValidationError):
			copy.insert()
		copy.workflow_state = None
		copy.insert()

		self.assertEqual(self.state(copy), ("Draft", 0))
