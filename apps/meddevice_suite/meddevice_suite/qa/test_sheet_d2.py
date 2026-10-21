"""The records D2-T15 and D2-T16 of the browser test sheet of D2 Vendor Master start from
(qa/sheets/d2.py prepare), proven on their own.

The sheet's Cypress spec (cypress/integration/sheet_d2.js) prepares the sheet as the Administrator of
a test site, after ui_journey.prepare_journey. These tests make the same calls the same way and check
that the supplier D2-T15 reads the audit trail of is in use and was changed by Purchase under a
change request QA approved, with the change in its audit trail and its approval signed by QA; and
that D2-T16 has a supplier with an approval QA signed and a decision QA has prepared but not signed,
and a second supplier to merge it into. The rest of the sheet's set-up, and its refusals, are proven
in qa/test_sheets.py.
"""

import frappe
from frappe.utils import get_fullname

from meddevice_suite.compliance.audit import as_data
from meddevice_suite.master_data import supplier_approval
from meddevice_suite.master_data.versioning import CHANGE_REQUEST, CHANGED_UNDER
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d2
from meddevice_suite.qa.test_sheets import QA, SheetTestCase, signed
from meddevice_suite.qa.ui_journey import prepare_journey
from meddevice_suite.qa.ui_users import email_for


@verifies("URS-PLT-007")
class TestSheetD2OwnRecords(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d2.prepare(password)

		cls.sheet = cls.prepared(before)

	def assertApprovedByQa(self, approval: str, supplier: str) -> None:
		"""QA's decision on the supplier, approved for the pouch with a signature, for the reason named."""
		self.assertEqual(
			frappe.db.get_value(
				supplier_approval.DOCTYPE,
				approval,
				["supplier", "owner", "docstatus", "decision", "reason", "decided_by"],
			),
			(
				supplier,
				email_for(QA),
				1,
				supplier_approval.APPROVED,
				d2.APPROVAL_REASON,
				email_for(QA),
			),
		)
		self.assertEqual(signed(supplier_approval.DOCTYPE, approval), [("Approve", email_for(QA))])
		self.assertEqual(supplier_approval.current_approval(supplier).name, approval)

	def test_a_supplier_in_use_changed_by_purchase_under_an_approved_change_request(self):
		audited = self.sheet["audited"]
		supplier, request = audited["supplier"], audited["change_request"]

		self.assertTrue(frappe.db.exists("Purchase Order", {"supplier": supplier, "docstatus": 1}), "in use")
		self.assertEqual(
			frappe.db.get_value("Supplier", supplier, ["supplier_group", CHANGED_UNDER, CHANGE_REQUEST]),
			(d2.OTHER_GROUP, request, None),
		)
		self.assertApprovedChange(request, "Purchase", "Supplier", supplier)
		self.assertEqual(frappe.db.get_value("Change Request", request, "reason"), d2.CHANGE_REASON)
		self.assertEqual(audited["change_reason"], d2.CHANGE_REASON)

	def test_the_change_is_in_the_suppliers_audit_trail(self):
		supplier, request = self.sheet["audited"]["supplier"], self.sheet["audited"]["change_request"]

		events = [
			(event.user, as_data(event.changes)["changed"])
			for event in frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": "Supplier", "reference_name": supplier, "action": "Updated"},
				fields=["user", "changes"],
			)
			if request in str(event.changes)
		]

		self.assertEqual(len(events), 1)
		user, changed = events[0]
		self.assertEqual(user, email_for("Purchase"))
		self.assertIn(["supplier_group", d2.GROUP, d2.OTHER_GROUP], changed)
		self.assertIn([CHANGED_UNDER, None, request], changed)

	def test_the_audited_suppliers_approval_is_signed_by_qa_and_in_its_audit_trail(self):
		audited = self.sheet["audited"]

		self.assertApprovedByQa(audited["approval"], audited["supplier"])
		self.assertEqual(audited["approval_reason"], d2.APPROVAL_REASON)
		self.assertEqual(
			frappe.get_all(
				"Audit Event",
				filters={
					"reference_doctype": supplier_approval.DOCTYPE,
					"reference_name": audited["approval"],
				},
				pluck="action",
				order_by="event_at asc, creation asc",
			),
			["Created", "Submitted"],
		)

	def test_a_signed_approval_a_prepared_decision_and_a_duplicate_for_d2_t16(self):
		kept = self.sheet["kept"]
		supplier, draft = kept["supplier"], kept["draft"]

		self.assertApprovedByQa(kept["approval"], supplier)
		self.assertEqual(
			frappe.db.get_value(supplier_approval.DOCTYPE, draft, ["supplier", "owner", "docstatus"]),
			(supplier, email_for(QA), 0),
		)
		self.assertFalse(frappe.db.get_value(supplier_approval.DOCTYPE, draft, "decision"), "undecided")
		self.assertEqual(signed(supplier_approval.DOCTYPE, draft), [])
		self.assertEqual(kept["approval_reason"], d2.APPROVAL_REASON)
		self.assertNotEqual(kept["duplicate"], supplier)
		self.assertEqual(
			frappe.db.get_value("Supplier", kept["duplicate"], ["supplier_group", "owner"]),
			(d2.GROUP, email_for("Purchase")),
		)
		self.assertFalse(
			frappe.db.exists(supplier_approval.DOCTYPE, {"supplier": kept["duplicate"]}), "no decision"
		)

	def test_names_the_jobs_as_the_screens_show_them(self):
		self.assertEqual(
			self.sheet["full_names"],
			{job: get_fullname(email_for(job)) for job in ("Purchase", QA)},
		)
