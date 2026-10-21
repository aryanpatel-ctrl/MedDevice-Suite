"""The records D3-T26 and D3-T27 of the browser test sheet of D3 Item / BOM / Routing Master start
from (qa/sheets/d3.py prepare_sheet), proven on their own.

The sheet's Cypress spec (cypress/integration/sheet_d3.js) prepares the sheet as the Administrator of
a test site. These tests make the same call the same way and check that the item D3-T26 reads the
audit trail of was approved by QA with a signature, with its bill of materials, and that QA changed
its shelf life under a change request QA approved, with the change and the approval in its audit
trail; and that D3-T27 has two approved finished goods, each with its approved specification and
its approved bill of materials, alike in what ERPNext compares before a merge, so that what refuses
the merge on the screen is this app's rule. The rest of the sheet's set-up, and its refusals, are
proven in qa/test_sheets.py.
"""

import frappe
from frappe.utils import get_fullname

from meddevice_suite.compliance.audit import as_data
from meddevice_suite.master_data import item_master, master_approval, specification
from meddevice_suite.master_data.versioning import CHANGE_REQUEST, CHANGED_UNDER, VERSION
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d3
from meddevice_suite.qa.test_sheets import QA, SheetTestCase, master_status, signed
from meddevice_suite.qa.ui_users import email_for

# What ERPNext compares before it merges one item into another (stock/doctype/item/item.py
# validate_properties_before_merge).
MERGE_PROPERTIES = ["stock_uom", "is_stock_item", "has_serial_no", "has_batch_no"]


@verifies("URS-PLT-007")
class TestSheetD3OwnRecords(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.sheet = cls.prepared(d3.prepare_sheet)  # the sheet's `before` prepares no journey

	def assertApprovedProduct(self, item: str) -> None:
		"""A finished good approved by QA with a signature, with its final specification approved."""
		self.assertTrue(item_master.approved(item))
		self.assertEqual(frappe.db.get_value("Item", item, item_master.ITEM_TYPE), item_master.FINISHED_GOOD)
		self.assertEqual(signed("Item", item), [("Approve", email_for(QA))])
		template = frappe.db.get_value("Item", item, "quality_inspection_template")
		self.assertEqual(template, f"{item} IQC (Demo)")
		self.assertEqual(master_status(specification.DOCTYPE, template), master_approval.APPROVED)
		self.assertTrue(specification.in_use(template))

	def assertApprovedBom(self, bom: str, item: str) -> None:
		"""The item's default bill of materials, approved (submitted) by QA with a signature."""
		self.assertEqual(
			frappe.db.get_value("BOM", bom, ["item", "docstatus", "is_active", "is_default"]),
			(item, 1, 1, 1),
		)
		self.assertEqual(signed("BOM", bom), [("Approve", email_for(QA))])

	def updates_by(self, item: str, naming: str) -> list[tuple[str, list]]:
		"""The item's Updated events whose changes name `naming`: (user, changed)."""
		return [
			(event.user, as_data(event.changes)["changed"])
			for event in frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": "Item", "reference_name": item, "action": "Updated"},
				fields=["user", "changes"],
			)
			if naming in str(event.changes)
		]

	def test_an_approved_product_changed_by_qa_under_an_approved_change_request(self):
		audited = self.sheet["audited"]
		item, request = audited["item"], audited["change_request"]

		self.assertApprovedProduct(item)
		self.assertEqual(
			frappe.db.get_value("Item", item, ["shelf_life_in_days", VERSION, CHANGED_UNDER, CHANGE_REQUEST]),
			(d3.CHANGED_SHELF_LIFE, 2, request, None),
		)
		self.assertEqual(audited["shelf_life"], {"before": d3.SHELF_LIFE, "after": d3.CHANGED_SHELF_LIFE})
		self.assertApprovedChange(request, d3.PRODUCTION, "Item", item)
		self.assertEqual(audited["change_reason"], frappe.db.get_value("Change Request", request, "reason"))
		self.assertTrue(audited["change_reason"])

	def test_the_change_is_in_the_items_audit_trail(self):
		item, request = self.sheet["audited"]["item"], self.sheet["audited"]["change_request"]

		events = self.updates_by(item, request)

		self.assertEqual(len(events), 1)
		user, changed = events[0]
		self.assertEqual(user, email_for(QA))
		self.assertIn(["shelf_life_in_days", d3.SHELF_LIFE, d3.CHANGED_SHELF_LIFE], changed)
		self.assertIn([CHANGED_UNDER, None, request], changed)

	def test_the_approval_is_in_the_items_audit_trail_with_its_signature(self):
		audited = self.sheet["audited"]
		item = audited["item"]

		events = self.updates_by(item, item_master.APPROVAL_STATUS)

		self.assertEqual(len(events), 1)
		user, changed = events[0]
		self.assertEqual(user, email_for(QA))
		self.assertIn([item_master.APPROVAL_STATUS, item_master.DRAFT, item_master.APPROVED], changed)
		self.assertIn([item_master.APPROVED_BY, None, email_for(QA)], changed)
		self.assertTrue(audited["item_approval_reason"])
		self.assertEqual(audited["item_approval_reason"], d3.approval_reason("Item", item))

	def test_its_bill_of_materials_approved_by_qa_and_in_its_audit_trail(self):
		audited = self.sheet["audited"]
		bom = audited["bom"]

		self.assertApprovedBom(bom, audited["item"])
		self.assertEqual(audited["bom_approval_reason"], d3.approval_reason("BOM", bom))
		self.assertEqual(
			frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": "BOM", "reference_name": bom, "action": "Submitted"},
				pluck="user",
			),
			[email_for(QA)],
		)

	def test_two_approved_products_with_their_bills_and_specifications_for_d3_t27(self):
		kept = self.sheet["kept"]
		item, duplicate = kept["item"], kept["duplicate"]

		self.assertNotEqual(item, duplicate)
		for product, bom, template in (
			(item, kept["bom"], kept["specification"]),
			(duplicate, kept["duplicate_bom"], kept["duplicate_specification"]),
		):
			with self.subTest(product=product):
				self.assertApprovedProduct(product)
				self.assertApprovedBom(bom, product)
				self.assertEqual(
					frappe.db.get_value("Item", product, "quality_inspection_template"), template
				)
		# Alike in what ERPNext compares, so that ERPNext's own check does not refuse the merge first.
		self.assertEqual(
			frappe.db.get_value("Item", item, MERGE_PROPERTIES),
			frappe.db.get_value("Item", duplicate, MERGE_PROPERTIES),
		)

	def test_the_records_of_d3_t26_and_d3_t27_are_their_own(self):
		sheet = self.sheet
		shared = {sheet["product"], sheet["default_bom"], sheet["second_bom"], *sheet["materials"].values()}
		own = {
			*(sheet["audited"][key] for key in ("item", "bom", "change_request")),
			*sheet["kept"].values(),
		}

		self.assertEqual(len(own), 9)
		self.assertFalse(own & shared)
		for name in (sheet["audited"]["item"], sheet["kept"]["item"], sheet["kept"]["duplicate"]):
			with self.subTest(item=name):
				self.assertIn(sheet["suffix"], name)

	def test_names_the_jobs_as_the_screens_show_them(self):
		self.assertEqual(
			self.sheet["full_names"],
			{job: get_fullname(email_for(job)) for job in (QA, d3.PRODUCTION)},
		)
