"""Warehouses (module 7, URS-MST-013): tests derived from the requirement's clauses."""

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.esign import SigningRefused, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.lot_control.test_lot_status import company, lot_item, receive, stock_entry, store
from meddevice_suite.master_data import master_approval
from meddevice_suite.master_data.master_approval import APPROVAL_STATUS, APPROVED, DRAFT
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer

ITEM = "MDS-WH-RAW"


class WarehouseTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.qa = signer("QA Manager")
		cls.stores_user = signer("Stores")
		lot_item(ITEM)

	def tearDown(self):
		frappe.set_user("Administrator")

	def new_store(self, **values) -> str:
		"""A new store of the company, as it is set up."""
		return (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"MDS WH {frappe.generate_hash(length=6)}",
					"company": company(),
					"parent_warehouse": store("All Warehouses"),
					**values,
				}
			)
			.insert()
			.name
		)

	def approve(self, name: str, reason: str = "Store checked: area, labels, conditions") -> None:
		frappe.set_user(self.qa)
		try:
			sign_action("Warehouse", name, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")


@verifies("URS-MST-013")
class TestApproval(WarehouseTestCase):
	def test_qa_approves_a_store_with_a_signature(self):
		name = self.new_store()
		self.assertEqual(frappe.db.get_value("Warehouse", name, APPROVAL_STATUS), DRAFT)
		self.approve(name, "Raw material store checked")
		self.assertEqual(
			frappe.db.get_value("Warehouse", name, [APPROVAL_STATUS, master_approval.APPROVED_BY]),
			(APPROVED, self.qa),
		)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Warehouse", "reference_name": name}
		)
		self.assertEqual(signature.meaning, "Approved")
		self.assertTrue(verify_signatures("Warehouse", name))
		self.assertTrue(is_unchanged_since(signature))

	def test_only_qa_approves_with_password_and_reason_and_once(self):
		name = self.new_store()
		frappe.set_user(self.stores_user)
		with self.assertRaisesRegex(frappe.PermissionError, "Only QA approves"):
			sign_action("Warehouse", name, "Approve", PASSWORD, "Mine")
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action("Warehouse", name, "Approve", "not the password", "Checked")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action("Warehouse", name, "Approve", PASSWORD, " ")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		self.assertFalse(master_approval.approved("Warehouse", name))
		self.approve(name)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			sign_action("Warehouse", name, "Approve", PASSWORD, "Again")

	def test_a_group_or_a_disabled_store_is_not_approved(self):
		group = self.new_store(is_group=1)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "holds stock"):
			sign_action("Warehouse", group, "Approve", PASSWORD, "Checked")
		frappe.set_user("Administrator")
		disabled = self.new_store(disabled=1)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			sign_action("Warehouse", disabled, "Approve", PASSWORD, "Checked")

	def test_a_store_needs_its_company(self):
		name = self.new_store()
		frappe.db.set_value("Warehouse", name, "company", None)  # as an import might leave it
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "its company"):
			sign_action("Warehouse", name, "Approve", PASSWORD, "Checked")

	def test_a_store_made_with_a_role_awaits_qa_too(self):
		from meddevice_suite.lot_control.stores import ROLE_FIELD

		name = self.new_store(**{ROLE_FIELD: None})
		self.assertEqual(frappe.db.get_value("Warehouse", name, APPROVAL_STATUS), DRAFT)

	def test_an_approved_stores_role_and_company_change_only_under_change_control(self):
		from meddevice_suite.lot_control.stores import HOLD, ROLE_FIELD, store_for

		hold = store_for(HOLD, company())  # approved, holding no stock
		doc = frappe.get_doc("Warehouse", hold)
		doc.set(ROLE_FIELD, None)  # its purpose changed
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()
		other = frappe.get_all("Company", filters={"name": ["!=", company()]}, pluck="name", limit=1)
		if other:
			name = self.new_store()
			self.approve(name)
			doc = frappe.get_doc("Warehouse", name)
			doc.company = other[0]
			with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
				doc.save()

	def test_the_approval_is_given_only_by_the_signature(self):
		name = self.new_store()
		doc = frappe.get_doc("Warehouse", name)
		doc.set(APPROVAL_STATUS, APPROVED)
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			doc.save()
		made = self.new_store(**{APPROVAL_STATUS: APPROVED})
		self.assertEqual(frappe.db.get_value("Warehouse", made, APPROVAL_STATUS), DRAFT)  # cleared as made

	def test_what_qa_approved_changes_only_under_change_control(self):
		name = self.new_store()
		self.approve(name)
		doc = frappe.get_doc("Warehouse", name)
		doc.disabled = 1
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()

	def test_an_approved_store_stays_one_that_holds_stock(self):
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST
		from meddevice_suite.qms.test_change_control import approved_change

		name = self.new_store()
		self.approve(name)
		change = approved_change(signer("Production Supervisor"), self.qa, name, "Warehouse", "System")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Warehouse", name)
		doc.is_group = 1
		doc.set(CHANGE_REQUEST, change)
		with self.assertRaisesRegex(frappe.ValidationError, "keeps what its approval needed"):
			doc.save()

	def test_the_form_says_what_approval_needs(self):
		group = self.new_store(is_group=1)
		doc = frappe.get_doc("Warehouse", group)
		doc.set("__onload", frappe._dict())
		doc.run_method("onload")
		self.assertTrue(any("holds stock" in what for what in doc.get_onload().master_approval["missing"]))


@verifies("URS-MST-013")
class TestStockMovesOnlyThroughApprovedStores(WarehouseTestCase):
	def test_nothing_comes_into_a_store_qa_has_not_approved(self):
		name = self.new_store()
		with self.assertRaisesRegex(frappe.ValidationError, f"Store {name} is not approved by QA"):
			receive(ITEM, 5, name)
		self.approve(name)
		receive(ITEM, 5, name)  # approved: stock comes in

	def test_nothing_leaves_a_store_qa_has_not_approved(self):
		from meddevice_suite.lot_control.lot_status import APPROVED as LOT_APPROVED
		from meddevice_suite.lot_control.lot_status import set_status

		name = self.new_store()
		self.approve(name)
		lot = receive(ITEM, 5, name)
		set_status(lot, LOT_APPROVED, "Released for the test")  # as QA's lot release leaves it (module 16)
		frappe.db.set_value("Warehouse", name, APPROVAL_STATUS, DRAFT)  # as a store not yet approved
		with self.assertRaisesRegex(frappe.ValidationError, f"Store {name} is not approved by QA"):
			stock_entry(
				"Material Issue",
				[
					{
						"item_code": ITEM,
						"qty": 1,
						"s_warehouse": name,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			)

	def test_a_stock_count_into_an_unapproved_store_is_refused(self):
		"""ERPNext writes a count's quantity for an item without lots after the entry is validated;
		the store is checked on the count's entry whatever quantity it shows (opening stock too)."""
		from meddevice_suite.setup.test_stock import approve_for_test, stock_item

		item = stock_item(f"MDS-WH-CONS-{frappe.generate_hash(length=5)}")  # a consumable: no lots
		approve_for_test(item)
		name = self.new_store()
		count = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company(),
				"purpose": "Stock Reconciliation",
				"items": [{"item_code": item, "warehouse": name, "qty": 10, "valuation_rate": 5}],
			}
		)
		count.insert()
		with self.assertRaisesRegex(frappe.ValidationError, f"Store {name} is not approved by QA"):
			count.submit()

	def test_a_cancelled_entry_is_not_a_movement(self):
		from meddevice_suite.master_data.warehouse import guard_stock_ledger_entry

		name = self.new_store()
		entry = frappe._dict(is_cancelled=1, actual_qty=-5, warehouse=name, voucher_type="Stock Entry")
		guard_stock_ledger_entry(entry)  # reversing a movement made before approval was needed
		entry.is_cancelled = 0
		with self.assertRaisesRegex(frappe.ValidationError, "is not approved by QA"):
			guard_stock_ledger_entry(entry)

	def test_the_signature_binds_what_qa_approved(self):
		name = self.new_store()
		self.approve(name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Warehouse", "reference_name": name}
		)
		frappe.db.set_value("Warehouse", name, "phone_no", "0000")  # not part of the approval
		self.assertTrue(is_unchanged_since(signature))
		frappe.db.set_value("Warehouse", name, "disabled", 1)  # past the rules
		self.assertFalse(is_unchanged_since(signature))
