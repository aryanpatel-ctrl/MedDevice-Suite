"""The records D1-T15 and D1-T16 of the browser test sheet of D1 Customer Master start from
(qa/sheets/d1.py prepare_sheet), proven on their own.

The sheet's Cypress spec (cypress/integration/sheet_d1.js) prepares the sheet as the Administrator of
a test site, after ui_journey.prepare_journey. These tests make the same calls the same way and check
that the customer D1-T15 reads the audit trail of is in use and was changed by Sales under a change
request QA approved, with the change in its audit trail and its licence verified by QA with a
signature; and that D1-T16 has a licence QA verified and one awaiting QA. The rest of the sheet's
set-up, and its refusals, are proven in qa/test_sheets.py.
"""

import frappe
from frappe.utils import get_fullname, today

from meddevice_suite.compliance.audit import as_data
from meddevice_suite.master_data import customer_licence
from meddevice_suite.master_data.versioning import CHANGE_REQUEST, CHANGED_UNDER
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d1
from meddevice_suite.qa.test_sheets import QA, SheetTestCase, signed
from meddevice_suite.qa.ui_journey import prepare_journey
from meddevice_suite.qa.ui_users import email_for


@verifies("URS-PLT-007")
class TestSheetD1OwnRecords(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d1.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def assertVerifiedByQa(self, licence: str, customer: str) -> None:
		"""Recorded by Sales for the customer; verified by QA with a signature, for the reason named."""
		self.assertEqual(
			frappe.db.get_value(
				customer_licence.DOCTYPE, licence, ["customer", "owner", "docstatus", "status", "reason"]
			),
			(customer, email_for("Sales"), 1, customer_licence.VERIFIED, self.sheet["licence_checked"]),
		)
		self.assertEqual(signed(customer_licence.DOCTYPE, licence), [("Verify", email_for(QA))])

	def test_a_customer_in_use_changed_by_sales_under_an_approved_change_request(self):
		customer, request = self.sheet["audited_customer"], self.sheet["audited_change_request"]

		self.assertTrue(frappe.db.exists("Sales Order", {"customer": customer, "docstatus": 1}), "in use")
		self.assertEqual(
			frappe.db.get_value("Customer", customer, ["customer_group", CHANGED_UNDER, CHANGE_REQUEST]),
			(d1.OTHER_GROUP, request, None),
		)
		self.assertApprovedChange(request, "Sales", "Customer", customer)
		self.assertEqual(frappe.db.get_value("Change Request", request, "reason"), d1.CHANGE_REASON)
		self.assertEqual(self.sheet["change_reason"], d1.CHANGE_REASON)

	def test_the_change_is_in_the_customers_audit_trail(self):
		customer, request = self.sheet["audited_customer"], self.sheet["audited_change_request"]

		events = [
			(event.user, as_data(event.changes)["changed"])
			for event in frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": "Customer", "reference_name": customer, "action": "Updated"},
				fields=["user", "changes"],
			)
			if request in str(event.changes)
		]

		self.assertEqual(len(events), 1)
		user, changed = events[0]
		self.assertEqual(user, email_for("Sales"))
		self.assertIn(["customer_group", d1.CUSTOMER_GROUP, d1.OTHER_GROUP], changed)
		self.assertIn([CHANGED_UNDER, None, request], changed)

	def test_the_audited_customers_licence_is_verified_by_qa(self):
		self.assertVerifiedByQa(self.sheet["audited_licence"], self.sheet["audited_customer"])

	def test_a_verified_licence_and_one_awaiting_qa_for_d1_t16(self):
		verified, awaiting = self.sheet["kept_licence"], self.sheet["awaiting_licence"]
		customer = frappe.db.get_value(customer_licence.DOCTYPE, verified, "customer")

		self.assertVerifiedByQa(verified, customer)
		self.assertEqual(
			frappe.db.get_value(customer_licence.DOCTYPE, awaiting, ["customer", "owner", "docstatus"]),
			(customer, email_for("Sales"), 0),
		)
		self.assertFalse(frappe.db.get_value(customer_licence.DOCTYPE, awaiting, "status"), "undecided")
		self.assertEqual(signed(customer_licence.DOCTYPE, awaiting), [])
		self.assertTrue(customer_licence.licences_valid_on(customer, today()))

	def test_names_the_jobs_as_the_screens_show_them(self):
		self.assertEqual(
			self.sheet["full_names"],
			{job: get_fullname(email_for(job)) for job in ("Sales", QA)},
		)
