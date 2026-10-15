"""The data the browser test sheets start from (qa/sheets/d1.py to d4.py), proven on its own.

Each sheet's Cypress spec (cypress/integration/sheet_d1.js to sheet_d4.js) calls these set-ups as
the Administrator of a test site, after ui_journey.prepare_journey where the sheet needs the
journey's masters, and its tests start from what they make. These tests make the same calls the
same way and check that every record is there, in the status and the store the sheet starts from,
with the jobs' approvals and signatures as each set-up states; and that every set-up is refused
off a test site, to anyone but the Administrator, and without the run's password where it signs.
"""

from collections.abc import Callable
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, today

from meddevice_suite.compliance.signing import signatures_of
from meddevice_suite.lot_control import release, stores
from meddevice_suite.lot_control.finished_goods import opening_account
from meddevice_suite.lot_control.lot_status import (
	APPROVED,
	QUARANTINE,
	REJECTED,
	holdings_of,
	status_of,
	stock_by_lot,
)
from meddevice_suite.master_data import (
	customer_licence,
	equipment,
	item_master,
	master_approval,
	specification,
	supplier_approval,
	supplier_rating,
)
from meddevice_suite.qa import verifies
from meddevice_suite.qa.sheets import d1, d2, d3, d4
from meddevice_suite.qa.ui_journey import (
	COMPANY,
	CUSTOMER,
	PRODUCT,
	RAW_MATERIALS,
	SUPPLIER,
	prepare_journey,
	store,
)
from meddevice_suite.qa.ui_users import email_for, prepare_job_users
from meddevice_suite.qms.equipment_care import unfit
from meddevice_suite.setup.fixtures.inspection import INSTRUMENT_NAME, SAMPLE_SIZE
from meddevice_suite.setup.fixtures.records import LICENCE_TYPE
from meddevice_suite.setup.roles import apply_roles

QA, QC, STORES = "QA Manager", "QC Inspector", "Stores"
SHEETS = (d1, d2, d3, d4)


def held(lot: str) -> dict[str, float]:
	"""Where the lot's stock is: store -> quantity."""
	return {row.warehouse: row.qty for row in holdings_of(lot)}


def signed(doctype: str, name: str) -> list[tuple[str, str]]:
	"""The signatures on a record: (action, signer), in the order they were made."""
	return [(row.action, row.signer) for row in signatures_of(doctype, name)]


def master_status(doctype: str, name: str) -> str | None:
	"""QA's approval of an operation, workstation, routing or specification."""
	return frappe.db.get_value(doctype, name, master_approval.APPROVAL_STATUS)


def set_ups(password: str) -> list[tuple[Callable, dict]]:
	"""Every set-up the sheets offer the browser, with arguments of the kind it sends."""
	return [
		(d1.prepare_sheet, {"password": password}),
		(d1.approve_customer_change, {"customer": CUSTOMER, "password": password}),
		(d2.prepare, {"password": password}),
		(d3.prepare_sheet, {"password": password}),
		(d3.approve_change, {"record_type": "Item", "record": PRODUCT, "password": password}),
		(d3.return_specification_to_draft, {"name": "Any specification", "password": password}),
		(d4.prepare_sheet, {"password": password}),
		(d4.prepare_lot, {"password": password, "stage": "received"}),
		(d4.prepare_purchase_order, {"password": password}),
		(d4.prepare_delivery, {}),
		(d4.customer_returns, {"delivery": "Any delivery", "qty": 1}),
		(d4.inspect_returned, {"lot": "Any lot", "entry": "Any entry"}),
		(d4.prepare_quarantined_product, {}),
		(d4.prepare_new_product_lot, {}),
		(d4.prepare_fefo, {"password": password}),
		(d4.unique_tag, {}),
	]


class SheetTestCase(IntegrationTestCase):
	"""The set-up of test_ui_journey: the job roles, a test site, the run's password of the job
	users, and the Administrator who runs the tests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_roles()
		frappe.set_user("Administrator")

	@classmethod
	def prepared(cls, prepare: Callable[[str], dict]) -> dict:
		"""What a sheet's Cypress `before` prepares, with the run's password of the job users."""
		with patch.dict(frappe.local.conf, {"allow_tests": 1}):
			return prepare(prepare_job_users()["password"])

	def setUp(self):
		self.enterContext(patch.dict(frappe.local.conf, {"allow_tests": 1}))
		self.password = prepare_job_users()["password"]  # the run's, as the browser is given it
		# Each test leaves the sheet prepared for the class as it found it: a test that changes it
		# (a specification back to draft) does not change what the next one reads.
		frappe.db.savepoint("sheet_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		try:
			frappe.db.rollback(save_point="sheet_test")
		except frappe.db.OperationalError:
			pass  # a refusal rolled the whole transaction back, the savepoint with it: nothing is left

	def assertApprovedChange(self, name: str, raised_by: str, record_type: str, record: str) -> None:
		"""A minor change request listing the record, raised by the job and assessed and approved by
		QA with signatures (module 36)."""
		request = frappe.get_doc("Change Request", name)
		self.assertEqual(
			(request.status, request.docstatus, request.classification), ("Approved", 1, "Minor")
		)
		self.assertEqual(request.requested_by, email_for(raised_by))
		self.assertEqual(
			[(row.reference_doctype, row.reference_name) for row in request.affected_records],
			[(record_type, record)],
		)
		self.assertEqual(
			signed("Change Request", name), [("Assess", email_for(QA)), ("Approve", email_for(QA))]
		)


@verifies("URS-PLT-007")
class TestSheetSetUpRefusals(SheetTestCase):
	def test_every_set_up_the_sheets_offer_is_checked_here(self):
		offered = {
			value
			for module in SHEETS
			for value in vars(module).values()
			if callable(value)
			and getattr(value, "__module__", None) == module.__name__
			and value in frappe.whitelisted
		}
		self.assertEqual(offered, {set_up for set_up, _arguments in set_ups(self.password)})

	def test_refuses_a_site_that_does_not_allow_tests(self):
		with patch.dict(frappe.local.conf, {"allow_tests": 0}):
			for set_up, arguments in set_ups(self.password):
				with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
					set_up(**arguments)

	def test_only_the_administrator_prepares_the_sheets(self):
		frappe.set_user(prepare_job_users()["users"]["Stores"])

		for set_up, arguments in set_ups(self.password):
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.PermissionError):
				set_up(**arguments)

	def test_refuses_to_sign_without_the_runs_password(self):
		without = [
			(d1.prepare_sheet, {}),
			(d2.prepare, {}),
			(d3.prepare_sheet, {}),
			(d4.prepare_sheet, {}),
			(d4.prepare_lot, {"stage": "received"}),
			(d4.prepare_purchase_order, {}),
			(d4.prepare_fefo, {}),
		]
		made = ("Customer Group", "Customer", "Supplier", "Item", "Purchase Order", "Purchase Receipt")
		before = {doctype: frappe.db.count(doctype) for doctype in made}
		for set_up, arguments in without:
			with self.subTest(set_up=set_up.__name__), self.assertRaises(frappe.ValidationError):
				set_up(password="", **arguments)
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in made}, before)  # refused first

	def test_refuses_what_a_sheet_does_not_prepare(self):
		receipts = frappe.db.count("Purchase Receipt")
		with self.assertRaises(frappe.ValidationError):
			d4.prepare_lot(self.password, "shipped")  # no such stage
		with self.assertRaises(frappe.ValidationError):
			d4.prepare_lot(self.password, "received", item=PRODUCT)  # not one of the sheet's materials
		self.assertEqual(frappe.db.count("Purchase Receipt"), receipts)
		with self.assertRaises(frappe.ValidationError):
			d3.approve_change("Customer", CUSTOMER, self.password)  # no customer under D3's change control
		with self.assertRaises(frappe.ValidationError):
			d1.approve_customer_change("TEST-D1 No such customer", self.password)


@verifies("URS-PLT-007")
class TestSheetD1CustomerMaster(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d1.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(
			(sheet["customer_group"], sheet["other_group"], sheet["licence_type"], sheet["product"]),
			(d1.CUSTOMER_GROUP, d1.OTHER_GROUP, LICENCE_TYPE, PRODUCT),
		)
		self.assertEqual(sheet["finished_goods"], store("Finished Goods"))
		self.assertEqual(frappe.db.get_value("Customer Group", d1.OTHER_GROUP, "is_group"), 0)
		# The customer the sheet records on the screen is new on every run.
		self.assertFalse(frappe.db.exists("Customer", {"customer_name": sheet["customer_name"]}))

	def test_a_customer_recorded_by_sales_with_a_licence_qa_verified(self):
		customer = self.sheet["licensed_customer"]

		self.assertEqual(
			frappe.db.get_value("Customer", customer, ["customer_group", "owner"]),
			(d1.CUSTOMER_GROUP, email_for("Sales")),
		)
		[licence] = customer_licence.licences_valid_on(customer, today())
		self.assertEqual(licence.licence_type, LICENCE_TYPE)
		self.assertEqual(
			frappe.db.get_value(customer_licence.DOCTYPE, licence.name, "owner"), email_for("Sales")
		)
		self.assertEqual(signed(customer_licence.DOCTYPE, licence.name), [("Verify", email_for(QA))])
		self.assertFalse(customer_licence.buys_without_licence(customer))

	def test_a_customer_qa_exempted_from_the_licence_with_a_signature(self):
		customer = self.sheet["exempt_customer"]

		self.assertEqual(
			frappe.db.get_value("Customer", customer, ["customer_group", "owner"]),
			(d1.CUSTOMER_GROUP, email_for("Sales")),
		)
		self.assertTrue(customer_licence.buys_without_licence(customer))
		self.assertEqual(signed("Customer", customer), [("Exempt From Licence", email_for(QA))])
		self.assertEqual(customer_licence.licences_valid_on(customer, today()), [])

	def test_approves_the_change_of_a_customer_for_the_sheets_last_test(self):
		customer = self.sheet["licensed_customer"]

		name = d1.approve_customer_change(customer, self.password)

		self.assertApprovedChange(name, "Sales", "Customer", customer)


@verifies("URS-PLT-007")
class TestSheetD2VendorMaster(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d2.prepare(password)

		cls.sheet = cls.prepared(before)

	def assertApprovedSupplier(self, supplier: str, item: str) -> None:
		"""Created by Purchase; approved by QA for the item on a valid certificate, with a signature."""
		self.assertEqual(
			frappe.db.get_value("Supplier", supplier, ["supplier_group", "owner"]),
			(d2.GROUP, email_for("Purchase")),
		)
		approval = supplier_approval.current_approval(supplier)
		self.assertEqual((approval.decision, approval.item_codes), (supplier_approval.APPROVED, {item}))
		self.assertGreater(getdate(approval.certificate_expiry), getdate(today()))
		self.assertEqual(signed(supplier_approval.DOCTYPE, approval.name), [("Approve", email_for(QA))])

	def assertApprovedOrder(self, supplier: str, item: str, required_by) -> None:
		"""One order, raised by Purchase and approved by Management (module 48)."""
		[order] = frappe.get_all(
			"Purchase Order",
			filters={"supplier": supplier},
			fields=["name", "docstatus", "workflow_state", "owner", "schedule_date"],
		)
		self.assertEqual(
			(order.docstatus, order.workflow_state, order.owner, getdate(order.schedule_date)),
			(1, "Approved", email_for("Purchase"), getdate(required_by)),
		)
		self.assertEqual(
			frappe.get_all("Purchase Order Item", filters={"parent": order.name}, pluck="item_code"), [item]
		)

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(
			sheet["items"], {"pouch": d2.POUCH, "not_listed": d2.NOT_LISTED, "silicone": d2.SILICONE}
		)
		self.assertEqual((sheet["group"], sheet["other_group"]), (d2.GROUP, d2.OTHER_GROUP))
		self.assertTrue(frappe.db.exists("Supplier Group", d2.OTHER_GROUP))
		self.assertIn(sheet["tag"], sheet["new_supplier"])
		# The supplier Purchase creates on the screen has no decision yet: it is new on every run.
		self.assertFalse(frappe.db.exists("Supplier", {"supplier_name": sheet["new_supplier"]}))
		d2.ensure_other_group()  # made once
		self.assertEqual(frappe.db.count("Supplier Group", {"supplier_group_name": d2.OTHER_GROUP}), 1)

	def test_a_supplier_the_system_rated_below_the_threshold_for_last_month(self):
		rated = self.sheet["rated"]
		supplier = rated["supplier"]
		last_month = getdate(get_first_day(add_months(today(), -1)))

		self.assertApprovedSupplier(supplier, d2.POUCH)
		self.assertApprovedOrder(supplier, d2.POUCH, add_days(last_month, 14))
		rating = frappe.get_doc(supplier_rating.DOCTYPE, rated["rating"])
		self.assertEqual(
			(rating.supplier, rating.docstatus, getdate(rating.period_start)), (supplier, 1, last_month)
		)
		self.assertEqual((rating.lines_due, rating.lines_on_time), (1, 0))  # nothing arrived
		self.assertEqual(rating.standing, supplier_rating.DUE)  # for QA's re-evaluation
		self.assertLess(rating.score, rating.threshold)
		self.assertEqual(
			(rated["score"], rated["period"], rated["threshold"], rated["standing"]),
			(rating.score, rating.period, rating.threshold, rating.standing),
		)

	def test_a_supplier_in_use_with_an_approved_change_request_listing_it(self):
		in_use = self.sheet["in_use"]
		supplier = in_use["supplier"]

		self.assertApprovedSupplier(supplier, d2.SILICONE)
		self.assertApprovedOrder(supplier, d2.SILICONE, add_days(today(), 7))
		self.assertApprovedChange(in_use["change_request"], "Purchase", "Supplier", supplier)
		self.assertEqual(
			frappe.db.get_value("Change Request", in_use["change_request"], "change_type"), "Supplier"
		)


@verifies("URS-PLT-007")
class TestSheetD3ItemBomRoutingMaster(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.sheet = cls.prepared(d3.prepare_sheet)  # the sheet's `before` prepares no journey

	def assertApprovedItem(self, code: str) -> None:
		self.assertTrue(item_master.approved(code))
		self.assertIn(("Approve", email_for(QA)), signed("Item", code))

	def assertEffective(self, document: str, document_type: str) -> None:
		self.assertEqual(
			frappe.db.get_value("Controlled Document", document, ["document_type", "status"]),
			(document_type, "Effective"),
		)

	def test_names_what_the_sheet_makes_on_the_screens(self):
		sheet = self.sheet

		self.assertEqual((sheet["company"], sheet["storage_condition"]), (COMPANY, d3.STORAGE_CONDITION))
		self.assertEqual(
			sheet["stores"],
			{
				"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
				"work_in_progress": store("Work In Progress"),
			},
		)
		made_on_screen = {
			"Item": ("item", "finished_good", "second_finished_good"),
			specification.DOCTYPE: ("specification", "second_specification"),
			"Workstation": ("bench",),
			"Operation": ("operation", "second_operation"),
			"Routing": ("routing", "second_routing"),
		}
		for doctype, keys in made_on_screen.items():
			for key in keys:
				with self.subTest(key=key):
					self.assertIn(sheet["suffix"], sheet[key])
					self.assertFalse(frappe.db.exists(doctype, sheet[key]))

	def test_a_new_gtin_and_the_same_digits_with_a_wrong_check_digit(self):
		valid, invalid = self.sheet["gtin"], self.sheet["invalid_gtin"]

		self.assertTrue(valid.startswith("20"))  # GS1's prefix for restricted circulation
		self.assertEqual((len(valid), valid[:-1]), (13, invalid[:-1]))
		self.assertTrue(item_master.valid_gtin(valid))
		self.assertFalse(item_master.valid_gtin(invalid))
		self.assertFalse(frappe.db.exists("Item", {item_master.GTIN_KEY: item_master.gtin_key(valid)}))

	def test_the_documents_the_masters_name_issued_through_document_control(self):
		sheet = self.sheet

		self.assertEffective(sheet["test_method"], "SOP")
		self.assertEffective(sheet["work_instruction"], "Work Instruction")
		self.assertEffective(sheet["specification_document"], "Specification")
		self.assertEffective(sheet["form_document"], "Form")
		self.assertEqual(
			frappe.db.get_value(
				"Controlled Document", sheet["draft_specification_document"], ["document_type", "status"]
			),
			("Specification", "Draft"),
		)
		self.assertEqual(d3.draft_specification_document(), sheet["draft_specification_document"])  # once

	def test_the_instrument_a_reading_names_approved_and_calibrated(self):
		instrument = self.sheet["instrument"]

		self.assertEqual(
			frappe.db.get_value("Asset", instrument, ["asset_name", equipment.APPROVAL_STATUS]),
			(INSTRUMENT_NAME, equipment.APPROVED),
		)
		self.assertEqual(unfit([instrument]), {})  # calibrated: fit to measure today

	def test_the_product_and_its_materials_approved_by_qa_with_their_specifications(self):
		sheet = self.sheet
		materials = sheet["materials"]

		self.assertEqual(set(materials), {*d3.COMPONENTS, "latex"})
		for key, code in materials.items():
			with self.subTest(material=key):
				item = frappe.db.get_value(
					"Item",
					code,
					[
						"stock_uom",
						"has_batch_no",
						"has_expiry_date",
						"quality_inspection_template",
						item_master.ITEM_TYPE,
						item_master.SPECIFICATION_DOCUMENT,
						item_master.STORAGE_CONDITION,
					],
					as_dict=True,
				)
				self.assertEqual(item.stock_uom, d3.UNITS.get(key, d3.COUNTED))
				self.assertEqual((item.has_batch_no, item.has_expiry_date), (1, 1))
				self.assertEqual(item[item_master.ITEM_TYPE], item_master.RAW_MATERIAL)
				self.assertEqual(item[item_master.SPECIFICATION_DOCUMENT], sheet["specification_document"])
				self.assertEqual(item[item_master.STORAGE_CONDITION], d3.STORAGE_CONDITION)
				self.assertEqual(item.quality_inspection_template, f"{code} IQC (Demo)")
				self.assertEqual(
					master_status(specification.DOCTYPE, item.quality_inspection_template),
					master_approval.APPROVED,
				)
				self.assertApprovedItem(code)
		self.assertEqual(sheet["silicone_specification"], f"{materials['silicone']} IQC (Demo)")
		self.assertEqual(sheet["components"], {materials[key]: qty for key, qty in d3.COMPONENTS.items()})

		product = sheet["product"]
		self.assertEqual(
			frappe.db.get_value(
				"Item",
				product,
				[
					item_master.ITEM_TYPE,
					item_master.STERILE,
					item_master.DEVICE_CLASS,
					item_master.SPECIFICATION_DOCUMENT,
				],
			),
			(item_master.FINISHED_GOOD, "Yes", "B", sheet["specification_document"]),
		)
		final = specification.specification_for(product, specification.FINAL)
		self.assertEqual(master_status(specification.DOCTYPE, final), master_approval.APPROVED)
		self.assertApprovedItem(product)

	def test_two_approved_bills_of_the_product_the_default_and_another(self):
		sheet = self.sheet

		for name, is_default in ((sheet["default_bom"], 1), (sheet["second_bom"], 0)):
			with self.subTest(bom=name):
				bom = frappe.get_doc("BOM", name)
				self.assertEqual(
					(bom.item, bom.docstatus, bom.is_active, bom.is_default, bom.quantity),
					(sheet["product"], 1, 1, is_default, 100),
				)
				self.assertEqual({row.item_code: flt(row.qty) for row in bom.items}, sheet["components"])

	def test_a_workstation_recorded_by_production_and_approved_by_qa(self):
		bench = self.sheet["approved_bench"]

		self.assertEqual(frappe.db.get_value("Workstation", bench, "owner"), email_for(d3.PRODUCTION))
		self.assertEqual(master_status("Workstation", bench), master_approval.APPROVED)
		self.assertEqual(signed("Workstation", bench), [("Approve", email_for(QA))])

	def test_a_receipt_into_quarantine_its_certificates_verified_by_qc(self):
		sheet = self.sheet
		silicone, t25_item = sheet["materials"]["silicone"], sheet["t25"]["item"]

		receipt = frappe.get_doc("Purchase Receipt", sheet["receipt"])
		self.assertEqual((receipt.docstatus, receipt.owner), (1, email_for(STORES)))
		self.assertEqual(
			supplier_approval.current_approval(receipt.supplier).item_codes, {silicone, t25_item}
		)
		self.assertEqual(
			[
				(
					row.item_code,
					row.mds_supplier_lot_no,
					row.mds_certificate_verified,
					row.mds_certificate_verified_by,
				)
				for row in receipt.items
			],
			[(code, f"SUP-{code}", 1, email_for(QC)) for code in (silicone, t25_item)],
		)
		quarantine = stores.store_for(stores.QUARANTINE, COMPANY)
		for lot in (sheet["silicone_lot"], sheet["t25"]["lot"]):
			with self.subTest(lot=lot):
				self.assertEqual(held(lot), {quarantine: 10})
				self.assertEqual(status_of([lot])[lot], QUARANTINE)

	def test_a_material_inspected_against_a_test_method_of_its_own(self):
		t25 = self.sheet["t25"]

		self.assertEffective(t25["test_method"], "SOP")
		self.assertEqual(
			frappe.db.get_value("Item", t25["item"], "quality_inspection_template"), t25["specification"]
		)
		self.assertEqual(
			frappe.db.get_value(
				specification.DOCTYPE,
				t25["specification"],
				[specification.TEST_METHOD, master_approval.APPROVAL_STATUS],
			),
			(t25["test_method"], master_approval.APPROVED),
		)
		self.assertApprovedItem(t25["item"])

	def test_the_drafts_the_sheet_completes_or_approves(self):
		sheet = self.sheet

		for code in (sheet["draft_item"], sheet["item_b"], sheet["item_rm2"], sheet["sub_assembly"]):
			with self.subTest(item=code):
				self.assertEqual(
					frappe.db.get_value("Item", code, item_master.APPROVAL_STATUS), item_master.DRAFT
				)
				self.assertEqual(signed("Item", code), [])
		self.assertEqual(
			frappe.db.get_value("Item", sheet["sub_assembly"], [item_master.ITEM_TYPE, "has_batch_no"]),
			(item_master.SUB_ASSEMBLY, 1),
		)
		self.assertFalse(frappe.db.get_value("Item", sheet["item_b"], item_master.SPECIFICATION_DOCUMENT))
		self.assertEqual(
			frappe.db.get_value("Item", sheet["item_rm2"], item_master.SPECIFICATION_DOCUMENT),
			sheet["specification_document"],
		)
		# Specification B lacks its test method; RM2's is complete but QA has not approved it.
		for name, item, test_method in (
			(sheet["specification_b"], sheet["item_b"], None),
			(sheet["specification_rm2"], sheet["item_rm2"], sheet["test_method"]),
		):
			with self.subTest(specification=name):
				spec = frappe.get_doc(specification.DOCTYPE, name)
				self.assertEqual((spec.mds_item, spec.mds_stage), (item, specification.INCOMING))
				self.assertEqual(spec.mds_sample_size, SAMPLE_SIZE)
				self.assertEqual(spec.get(specification.TEST_METHOD) or None, test_method)
				self.assertEqual(spec.get(master_approval.APPROVAL_STATUS), master_approval.DRAFT)

	def test_approves_a_change_of_a_record_the_sheet_changes(self):
		item = self.sheet["materials"]["carton"]

		name = d3.approve_change("Item", item, self.password)

		self.assertApprovedChange(name, d3.PRODUCTION, "Item", item)

	def test_returns_a_specification_in_use_to_draft_under_an_approved_change(self):
		name = f"{self.sheet['materials']['latex']} IQC (Demo)"  # one no other test here changes
		self.assertEqual(master_status(specification.DOCTYPE, name), master_approval.APPROVED)
		before = next(
			row
			for row in frappe.get_doc(specification.DOCTYPE, name).get(specification.PARAMETERS)
			if row.numeric
		)

		result = d3.return_specification_to_draft(name, self.password)

		self.assertApprovedChange(result["change_request"], d3.PRODUCTION, specification.DOCTYPE, name)
		spec = frappe.get_doc(specification.DOCTYPE, name)
		self.assertTrue(specification.in_use(name))  # its item is approved
		self.assertEqual(spec.get("mds_changed_under"), result["change_request"])
		self.assertEqual(spec.get(master_approval.APPROVAL_STATUS), master_approval.DRAFT)
		widened = next(row for row in spec.get(specification.PARAMETERS) if row.name == before.name)
		self.assertAlmostEqual(flt(widened.max_value), flt(before.max_value) + 0.1)
		self.assertAlmostEqual(flt(result["max_value"]), flt(widened.max_value))


@verifies("URS-PLT-007")
class TestSheetD4InventoryAndBatchLedger(SheetTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		def before(password: str) -> dict:
			prepare_journey(password)
			return d4.prepare_sheet(password)

		cls.sheet = cls.prepared(before)

	def assertReceived(self, lot: dict, item: str, qty: float) -> None:
		"""Received by Stores into quarantine with the supplier's lot number and certificate."""
		self.assertEqual((lot["item"], lot["qty"]), (item, qty))
		self.assertEqual(
			frappe.db.get_value("Purchase Receipt", lot["receipt"], ["docstatus", "supplier", "owner"]),
			(1, SUPPLIER, email_for(STORES)),
		)
		line = frappe.db.get_value(
			"Purchase Receipt Item",
			lot["row"],
			["item_code", "mds_supplier_lot_no", "mds_certificate"],
			as_dict=True,
		)
		self.assertEqual((line.item_code, line.mds_supplier_lot_no), (item, lot["supplier_lot"]))
		self.assertTrue(line.mds_certificate)
		self.assertEqual(frappe.db.get_value("Batch", lot["lot"], "item"), item)

	def assertInQuarantine(self, lot: dict) -> None:
		self.assertEqual(held(lot["lot"]), {self.sheet["stores"]["quarantine"]: lot["qty"]})
		self.assertEqual(status_of([lot["lot"]])[lot["lot"]], QUARANTINE)
		self.assertFalse(frappe.db.exists(release.DOCTYPE, {"batch": lot["lot"]}))

	def verified_by(self, lot: dict) -> str | None:
		return frappe.db.get_value(
			"Purchase Receipt Item",
			lot["row"],
			"mds_certificate_verified_by",
		)

	def assertInspected(self, lot: dict, status: str) -> None:
		"""QC's submitted incoming inspection on the goods receipt."""
		inspection = frappe.get_doc("Quality Inspection", lot["inspection"])
		self.assertEqual(
			(
				inspection.docstatus,
				inspection.status,
				inspection.inspection_type,
				inspection.reference_type,
				inspection.reference_name,
				inspection.batch_no,
				inspection.inspected_by,
			),
			(1, status, "Incoming", "Purchase Receipt", lot["receipt"], lot["lot"], email_for(QC)),
		)

	def assertReleased(self, lot: dict) -> None:
		"""Released by QA with a signature on its accepted inspection, moved to the approved store."""
		decision = frappe.get_doc(release.DOCTYPE, lot["release"])
		self.assertEqual(
			(decision.docstatus, decision.decision, decision.quality_inspection, decision.stock_entry),
			(1, release.RELEASE, lot["inspection"], lot["transfer"]),
		)
		self.assertEqual(signed(release.DOCTYPE, decision.name), [(release.RELEASE, email_for(QA))])
		self.assertEqual(frappe.db.get_value("Stock Entry", lot["transfer"], "docstatus"), 1)
		self.assertEqual(status_of([lot["lot"]])[lot["lot"]], APPROVED)
		self.assertEqual(held(lot["lot"]), {self.sheet["stores"]["approved"]: lot["qty"]})

	def test_names_what_the_sheet_works_with(self):
		sheet = self.sheet

		self.assertEqual(
			(
				sheet["company"],
				sheet["material"],
				sheet["fefo_material"],
				sheet["expiry_material"],
				sheet["product"],
				sheet["supplier"],
				sheet["customer"],
			),
			(COMPANY, d4.MATERIAL, d4.FEFO_MATERIAL, d4.EXPIRY_MATERIAL, PRODUCT, SUPPLIER, CUSTOMER),
		)
		self.assertEqual(sheet["shelf_life_days"], RAW_MATERIALS[d4.MATERIAL][3])
		self.assertEqual(sheet["sample_size"], SAMPLE_SIZE)
		self.assertEqual(sheet["opening_account"], opening_account(COMPANY))
		self.assertEqual(
			sheet["specification"], specification.specification_for(d4.MATERIAL, specification.INCOMING)
		)
		self.assertTrue(sheet["specification"])
		self.assertEqual(frappe.db.get_value("Asset", sheet["instrument"], "asset_name"), INSTRUMENT_NAME)
		self.assertEqual(
			sheet["stores"],
			{
				"quarantine": stores.store_for(stores.QUARANTINE, COMPANY),
				"hold": stores.store_for(stores.HOLD, COMPANY),
				"rejected": stores.store_for(stores.REJECTED, COMPANY),
				"returns": stores.store_for(stores.RETURNS, COMPANY),
				"approved": store("Stores"),
				"work_in_progress": store("Work In Progress"),
				"finished_goods": store("Finished Goods"),
				"root": stores.root_store(COMPANY),
			},
		)
		for name in sheet["stores"].values():
			with self.subTest(store=name):
				self.assertTrue(frappe.db.exists("Warehouse", name))

	def test_the_sheets_own_material_approved_with_its_specification(self):
		material = d4.EXPIRY_MATERIAL

		self.assertTrue(item_master.approved(material))
		self.assertIn(("Approve", email_for(QA)), signed("Item", material))
		incoming = specification.specification_for(material, specification.INCOMING)
		self.assertEqual(master_status(specification.DOCTYPE, incoming), master_approval.APPROVED)
		self.assertTrue(frappe.db.get_value("Item", material, item_master.SPECIFICATION_DOCUMENT))
		self.assertEqual(
			frappe.db.get_value(
				"Item Price",
				{"item_code": material, "price_list": "Standard Buying"},
				"price_list_rate",
			),
			d4.EXPIRY_MATERIAL_DETAILS[4],
		)

	def test_the_supplier_approved_for_all_it_delivers_and_approved_once(self):
		approval = supplier_approval.current_approval(SUPPLIER)

		self.assertEqual(approval.decision, supplier_approval.APPROVED)
		self.assertLessEqual({*RAW_MATERIALS, d4.EXPIRY_MATERIAL}, approval.item_codes)
		self.assertGreater(getdate(approval.certificate_expiry), getdate(today()))
		self.assertEqual(signed(supplier_approval.DOCTYPE, approval.name), [("Approve", email_for(QA))])

		decisions = frappe.db.count(supplier_approval.DOCTYPE, {"supplier": SUPPLIER})
		again = d4.prepare_sheet(self.password)  # the decision in force covers all of it already

		self.assertEqual(again, self.sheet)
		self.assertEqual(frappe.db.count(supplier_approval.DOCTYPE, {"supplier": SUPPLIER}), decisions)
		self.assertEqual(supplier_approval.current_approval(SUPPLIER).name, approval.name)

	def test_a_lot_received_its_certificate_not_yet_verified(self):
		lot = d4.prepare_lot(self.password, "received")

		self.assertReceived(lot, d4.MATERIAL, 10)
		self.assertTrue(lot["supplier_lot"].startswith("SUP-"))
		self.assertFalse(self.verified_by(lot))
		self.assertNotIn("inspection", lot)
		self.assertFalse(frappe.db.exists("Quality Inspection", {"batch_no": lot["lot"]}))
		self.assertInQuarantine(lot)

	def test_a_lot_whose_certificate_qc_verified(self):
		lot = d4.prepare_lot(self.password, "verified", qty=4)

		self.assertReceived(lot, d4.MATERIAL, 4)
		self.assertEqual(self.verified_by(lot), email_for(QC))
		self.assertNotIn("inspection", lot)
		self.assertInQuarantine(lot)

	def test_a_lot_whose_incoming_inspection_qc_rejected(self):
		lot = d4.prepare_lot(self.password, "rejected_inspection")

		self.assertReceived(lot, d4.MATERIAL, 10)
		self.assertEqual(self.verified_by(lot), email_for(QC))
		self.assertInspected(lot, "Rejected")
		self.assertNotIn("release", lot)
		self.assertInQuarantine(lot)  # QA has not decided on it

	def test_a_lot_inspected_and_accepted_ready_for_qas_decision(self):
		lot = d4.prepare_lot(self.password, "ready")

		self.assertReceived(lot, d4.MATERIAL, 10)
		self.assertEqual(self.verified_by(lot), email_for(QC))
		self.assertInspected(lot, "Accepted")
		self.assertNotIn("release", lot)
		self.assertInQuarantine(lot)

	def test_a_lot_released_by_qa_to_the_approved_store(self):
		lot = d4.prepare_lot(self.password, "approved")

		self.assertReceived(lot, d4.MATERIAL, 10)
		self.assertEqual(self.verified_by(lot), email_for(QC))
		self.assertInspected(lot, "Accepted")
		self.assertReleased(lot)

	def test_a_released_lot_of_the_sheets_own_material(self):
		lot = d4.prepare_lot(self.password, "approved", item=d4.EXPIRY_MATERIAL)

		self.assertReceived(lot, d4.EXPIRY_MATERIAL, 10)
		self.assertInspected(lot, "Accepted")
		self.assertReleased(lot)

	def test_a_purchase_order_raised_by_purchase_and_approved_by_management(self):
		prepared = d4.prepare_purchase_order(self.password, qty=6)

		self.assertEqual((prepared["item"], prepared["qty"]), (d4.MATERIAL, 6))
		order = frappe.get_doc("Purchase Order", prepared["order"])
		quarantine = self.sheet["stores"]["quarantine"]
		self.assertEqual(
			(order.docstatus, order.workflow_state, order.owner, order.supplier, order.set_warehouse),
			(1, "Approved", email_for("Purchase"), SUPPLIER, quarantine),
		)
		self.assertEqual(
			[(row.item_code, row.qty, row.warehouse) for row in order.items], [(d4.MATERIAL, 6, quarantine)]
		)

	def assertDelivered(self, lot: dict, delivered: float) -> None:
		"""An approved lot of the product, come in as opening stock, partly delivered by Stores."""
		self.assertEqual(lot["item"], PRODUCT)
		self.assertEqual(status_of([lot["lot"]])[lot["lot"]], APPROVED)
		self.assertEqual(
			frappe.db.get_value("Stock Entry", lot["entry"], ["docstatus", "stock_entry_type", "is_opening"]),
			(1, "Material Receipt", "Yes"),
		)
		note = frappe.get_doc("Delivery Note", lot["delivery"])
		self.assertEqual((note.docstatus, note.customer, note.owner), (1, CUSTOMER, email_for(STORES)))
		self.assertEqual([(row.item_code, row.qty) for row in note.items], [(PRODUCT, delivered)])

	def test_an_approved_lot_of_the_product_delivered_to_the_customer(self):
		lot = d4.prepare_delivery(qty=10, delivered=3)

		self.assertDelivered(lot, 3)
		self.assertNotIn("sales_return", lot)
		self.assertEqual(held(lot["lot"]), {self.sheet["stores"]["finished_goods"]: 7})

	def test_a_delivered_lot_partly_sent_back_into_the_returns_store(self):
		lot = d4.prepare_delivery(qty=10, delivered=5, returned=2)

		self.assertDelivered(lot, 5)
		back = frappe.get_doc("Delivery Note", lot["sales_return"])
		self.assertEqual(
			(back.docstatus, back.is_return, back.return_against, back.owner),
			(1, 1, lot["delivery"], email_for(STORES)),
		)
		sheet_stores = self.sheet["stores"]
		self.assertEqual(held(lot["lot"]), {sheet_stores["finished_goods"]: 5, sheet_stores["returns"]: 2})

	def test_the_returned_lot_inspected_by_qc_and_more_of_it_taken_back(self):
		lot = d4.prepare_delivery(qty=10, delivered=5, returned=2)

		name = d4.inspect_returned(lot["lot"], lot["entry"])

		inspection = frappe.get_doc("Quality Inspection", name)
		self.assertEqual(
			(
				inspection.docstatus,
				inspection.status,
				inspection.inspection_type,
				inspection.reference_type,
				inspection.reference_name,
				inspection.item_code,
				inspection.batch_no,
				inspection.inspected_by,
			),
			(1, "Accepted", "Outgoing", "Stock Entry", lot["entry"], PRODUCT, lot["lot"], email_for(QC)),
		)

		back = d4.customer_returns(lot["delivery"], 1)

		self.assertEqual(
			frappe.db.get_value("Delivery Note", back, ["docstatus", "is_return", "return_against"]),
			(1, 1, lot["delivery"]),
		)
		sheet_stores = self.sheet["stores"]
		self.assertEqual(held(lot["lot"]), {sheet_stores["finished_goods"]: 5, sheet_stores["returns"]: 3})

	def test_a_lot_of_the_product_in_quarantine_not_approved(self):
		lot = d4.prepare_quarantined_product(qty=8)

		self.assertEqual((lot["item"], lot["qty"]), (PRODUCT, 8))
		self.assertEqual(frappe.db.get_value("Batch", lot["lot"], "item"), PRODUCT)
		self.assertEqual(held(lot["lot"]), {self.sheet["stores"]["quarantine"]: 8})
		self.assertEqual(status_of([lot["lot"]])[lot["lot"]], QUARANTINE)

	def test_a_new_lot_of_the_product_with_no_stock(self):
		lot = d4.prepare_new_product_lot()

		self.assertEqual(lot["item"], PRODUCT)
		self.assertEqual(frappe.db.get_value("Batch", lot["lot"], "item"), PRODUCT)
		self.assertEqual(held(lot["lot"]), {})

	def test_two_approved_lots_whose_dates_qa_corrected_and_nothing_else_of_the_material(self):
		earlier = d4.prepare_fefo(self.password)

		lots = d4.prepare_fefo(self.password, qty=4)  # an earlier run's lots are scrapped

		self.assertEqual(lots["item"], d4.FEFO_MATERIAL)
		for which, days in d4.FEFO_EXPIRY_DAYS.items():
			lot = lots[which]
			with self.subTest(lot=which):
				self.assertEqual(lots[f"{which}_expiry"], str(getdate(add_days(today(), days))))
				self.assertEqual(status_of([lot])[lot], APPROVED)
				self.assertEqual(held(lot), {self.sheet["stores"]["approved"]: 4})
				self.assertIn(("Correct Dates", email_for(QA)), signed("Batch", lot))
				self.assertEqual(
					[
						row.decision
						for row in frappe.get_all(
							release.DOCTYPE, filters={"batch": lot}, fields=["decision"]
						)
					],
					[release.RELEASE],
				)
				gone = earlier[which]
				self.assertEqual(status_of([gone])[gone], REJECTED)
				self.assertEqual(held(gone), {})
		self.assertGreater(getdate(lots["older_expiry"]), getdate(lots["newer_expiry"]))
		self.assertEqual(
			{row.batch_no for row in stock_by_lot(d4.FEFO_MATERIAL)}, {lots["older"], lots["newer"]}
		)

	def test_a_tag_of_its_own_for_each_test(self):
		first, second = d4.unique_tag(), d4.unique_tag()

		self.assertEqual((len(first), len(second)), (10, 10))
		self.assertNotEqual(first, second)
