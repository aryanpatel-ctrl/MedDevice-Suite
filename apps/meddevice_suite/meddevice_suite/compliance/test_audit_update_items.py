"""ERPNext's Update Items on a submitted order is recorded line by line (URS-CMP-008; 21 CFR 11.10(e)).

ERPNext writes the order's lines on their own before it saves the order; the order's event still
records each line added, removed or changed with its old and new values, and the reason. Found by
the browser test D12-T23, where the event showed only the order's total quantity change.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate, today

from meddevice_suite.compliance.audit import verify_events
from meddevice_suite.compliance.audit_reason import stage_reason
from meddevice_suite.compliance.test_approvals import ApprovalTestCase
from meddevice_suite.compliance.test_audit import events_for
from meddevice_suite.lot_control.test_lot_status import company, store
from meddevice_suite.master_data.test_customer_licence import PRODUCT, CustomerLicenceTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.setup.test_stock import approve_for_test, stock_item

UPDATE_ITEMS = "erpnext.controllers.accounts_controller.update_child_qty_rate"
SECOND = "MDS-UPDATE-ITEMS-SECOND"
THIRD = "MDS-UPDATE-ITEMS-THIRD"
REASON = "Customer changed the order by phone"


def update_items(doctype: str, name: str, lines: list[dict]) -> None:
	"""Update Items as the desk sends it: through the method a request for ERPNext's is routed to."""
	method = frappe.get_attr(frappe.override_whitelisted_method(UPDATE_ITEMS))
	frappe.call(method, parent_doctype=doctype, trans_items=frappe.as_json(lines), parent_doctype_name=name)


def as_line(row: Document, **changes) -> dict:
	"""A line of the Update Items dialog for an existing row, with `changes` made to it."""
	date_field = "delivery_date" if row.doctype == "Sales Order Item" else "schedule_date"
	return {
		"docname": row.name,
		"item_code": row.item_code,
		"qty": row.qty,
		"rate": row.rate,
		"uom": row.uom,
		"conversion_factor": row.conversion_factor,
		date_field: row.get(date_field),
		**changes,
	}


def line_changes(event: frappe._dict, row_name: str) -> dict[str, list]:
	"""The fields of one line that the event records as changed: fieldname -> [old, new]."""
	return {
		field: [old, new]
		for table, name, fields in event.changes.get("row_changed", [])
		if (table, name) == ("items", row_name)
		for field, old, new in fields
	}


def lines(event: frappe._dict, kind: str, item_code: str) -> list[dict]:
	"""The lines of an item that the event records as added or removed."""
	return [
		row
		for table, row in event.changes.get(kind, [])
		if table == "items" and row["item_code"] == item_code
	]


def as_dates(values: list) -> list[str]:
	return [str(getdate(value)) for value in values]


@verifies("URS-CMP-008", "URS-CMP-011")
class TestSalesOrderUpdateItemsIsAudited(CustomerLicenceTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for code in (SECOND, THIRD):
			stock_item(code, item_group="Products", is_stock_item=0)
		approve_for_test(SECOND, THIRD)

	def setUp(self):
		super().setUp()
		frappe.flags.audit_cause = None

	def submitted_order(self) -> Document:
		customer, _licence = self.licensed()
		frappe.set_user(self.sales)
		try:
			order = frappe.get_doc(
				{
					"doctype": "Sales Order",
					"customer": customer,
					"company": company(),
					"transaction_date": today(),
					"delivery_date": add_days(today(), 7),
					"items": [
						{"item_code": PRODUCT, "qty": 1, "rate": 100, "delivery_date": add_days(today(), 7)},
						{"item_code": SECOND, "qty": 1, "rate": 50, "delivery_date": add_days(today(), 7)},
					],
				}
			).insert()
			order.submit()
			return order
		finally:
			frappe.set_user("Administrator")

	def test_each_line_added_removed_or_changed_is_recorded_with_its_old_and_new_values(self):
		order = self.submitted_order()
		first = order.items[0]
		since = len(events_for("Sales Order", order.name))

		frappe.set_user(self.sales)
		try:
			stage_reason("Sales Order", order.name, "update_after_submit", REASON)
			# The first line's quantity, rate and delivery date change; the second line is removed
			# and a third added.
			update_items(
				"Sales Order",
				order.name,
				[
					as_line(first, qty=2, rate=90, delivery_date=add_days(today(), 10)),
					{
						"item_code": THIRD,
						"qty": 3,
						"rate": 30,
						"delivery_date": add_days(today(), 7),
						"uom": "Nos",
						"conversion_factor": 1,
						"warehouse": store("Stores"),
					},
				],
			)
		finally:
			frappe.set_user("Administrator")

		events = events_for("Sales Order", order.name)[since:]
		updates = [event for event in events if event.action == "Updated after submission"]
		self.assertEqual(len(updates), 1)
		update = updates[0]
		self.assertEqual((update.user, update.reason), (self.sales, REASON))

		changed = line_changes(update, first.name)
		self.assertEqual(changed["qty"], [1, 2])
		self.assertEqual(changed["rate"], [100, 90])
		self.assertEqual(
			as_dates(changed["delivery_date"]), as_dates([add_days(today(), 7), add_days(today(), 10)])
		)
		(removed,) = lines(update, "removed", SECOND)
		self.assertEqual((removed["qty"], removed["rate"]), (1, 50))
		(added,) = lines(update, "added", THIRD)
		self.assertEqual((added["qty"], added["rate"]), (3, 30))

		# Nothing recorded twice: the writes ERPNext makes after saving the order in the same call
		# (its status, its delivery) record only their own changes.
		self.assertEqual([event for event in events if "qty" in line_changes(event, first.name)], [update])
		self.assertEqual([event for event in events if lines(event, "removed", SECOND)], [update])
		self.assertEqual([event for event in events if lines(event, "added", THIRD)], [update])
		self.assertEqual(verify_events("Sales Order", order.name), [])

	def test_a_refused_update_leaves_nothing_behind_for_a_later_save(self):
		order = self.submitted_order()
		first, second = order.items

		frappe.set_user(self.sales)
		try:
			frappe.db.savepoint("refused")
			with self.assertRaisesRegex(frappe.ValidationError, "Give a reason to change"):
				update_items("Sales Order", order.name, [as_line(first, qty=5), as_line(second)])
			frappe.db.rollback(save_point="refused")
		finally:
			frappe.set_user("Administrator")

		self.assertFalse(frappe.flags.audit_lines_before)
		self.assertEqual(frappe.db.get_value("Sales Order Item", first.name, "qty"), 1)


@verifies("URS-CMP-008")
class TestPurchaseOrderUpdateItemsIsAudited(ApprovalTestCase):
	def test_a_refused_change_to_an_approved_order_records_nothing(self):
		# Any change to an approved order's lines, a new schedule date too, needs approval again
		# (URS-CMP-033, decided 2026-10-21): refused, so no line is recorded as changed.
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")
		self.act(self.approver, order, "Approve")
		frappe.set_user("Administrator")
		row = frappe.get_doc("Purchase Order", order.name).items[0]
		since = len(events_for("Purchase Order", order.name))

		self.as_user(self.approver)
		stage_reason("Purchase Order", order.name, "update_after_submit", "Supplier ships a week later")
		frappe.db.savepoint("refused")
		with self.assertRaisesRegex(frappe.ValidationError, "has been approved"):
			update_items("Purchase Order", order.name, [as_line(row, schedule_date=add_days(today(), 14))])
		frappe.db.rollback(save_point="refused")  # as the refused request is rolled back
		frappe.set_user("Administrator")

		self.assertFalse(frappe.flags.audit_lines_before)
		self.assertEqual(
			as_dates([frappe.db.get_value("Purchase Order Item", row.name, "schedule_date")]),
			as_dates([add_days(today(), 7)]),
		)
		self.assertEqual(events_for("Purchase Order", order.name)[since:], [])
		self.assertEqual(verify_events("Purchase Order", order.name), [])
