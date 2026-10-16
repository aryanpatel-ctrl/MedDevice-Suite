"""Bill of materials (module 2, URS-MST-007): tests derived from the requirement's clauses."""

import frappe

from meddevice_suite.compliance.esign import SigningRefused, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import company, store
from meddevice_suite.master_data import bom as bill_of_materials
from meddevice_suite.master_data.item_master import (
	APPROVAL_STATUS,
	DRAFT,
	FINISHED_GOOD,
	RAW_MATERIAL,
	SUB_ASSEMBLY,
)
from meddevice_suite.master_data.test_item_master import ItemMasterTestCase, new_item
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer

REFUSED = (frappe.ValidationError, frappe.PermissionError)


class BomTestCase(ItemMasterTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.production = signer("Production Supervisor")

	def draft(self, product: str | None = None, components: list[tuple[str, float]] | None = None, **values):
		"""A draft bill of materials, as Production drafts it."""
		product = product or self.approved_item(FINISHED_GOOD)
		components = components if components is not None else [(self.approved_item(), 2)]
		frappe.set_user(self.production)
		try:
			return frappe.get_doc(
				{
					"doctype": "BOM",
					"item": product,
					"company": company(),
					"quantity": 1,
					"is_active": 1,
					"is_default": 1,
					"rm_cost_as_per": "Valuation Rate",
					"items": [{"item_code": code, "qty": qty, "rate": 1} for code, qty in components],
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def approve(self, name: str, reason: str = "Components and quantities checked") -> None:
		frappe.set_user(self.qa)
		try:
			sign_action("BOM", name, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def work_order(self, product: str, bom: str) -> frappe.model.document.Document:
		return frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": product,
				"bom_no": bom,
				"qty": 1,
				"company": company(),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store("Quarantine"),
			}
		)


@verifies("URS-MST-007")
class TestSignedApproval(BomTestCase):
	def test_production_drafts_and_qa_approves_with_a_signature(self):
		bom = self.draft()
		self.assertEqual(bom.docstatus, 0)
		self.approve(bom.name, "Silicone and connector quantities checked")
		self.assertEqual(frappe.db.get_value("BOM", bom.name, "docstatus"), 1)
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "BOM", "reference_name": bom.name},
			fields=["meaning", "signer", "reason"],
		)
		self.assertEqual(
			[(s.meaning, s.signer, s.reason) for s in signatures],
			[("Approved", self.qa, "Silicone and connector quantities checked")],
		)
		self.assertTrue(verify_signatures("BOM", bom.name))

	def test_only_qa_approves(self):
		bom = self.draft()
		for user in (self.production, signer("Stores"), signer("Purchase")):
			with self.subTest(user=user):
				frappe.set_user(user)
				# Refused by the rule itself, before anything is signed (Frappe's submit permission would
				# refuse most of them too, later).
				with self.assertRaisesRegex(frappe.PermissionError, "Only QA approves"):
					sign_action("BOM", bom.name, "Approve", PASSWORD, "Not mine to approve")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("BOM", bom.name, "docstatus"), 0)
		self.assertFalse(
			frappe.db.exists("Electronic Signature", {"reference_doctype": "BOM", "reference_name": bom.name})
		)

	def test_the_signed_approval_is_the_only_submit(self):
		bom = self.draft()
		for user in (self.qa, "Administrator"):  # QA, who may submit in Frappe, and the superuser
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
					frappe.get_doc("BOM", bom.name).submit()
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			frappe.client.submit(frappe.get_doc("BOM", bom.name).as_dict())  # through the API
		with self.subTest(path="saved as submitted"):
			doc = frappe.get_doc("BOM", bom.name)
			doc.docstatus = 1
			with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
				doc.save()
		with self.subTest(path="validation skipped"):  # Frappe then skips before_submit
			doc = frappe.get_doc("BOM", bom.name)
			doc.flags.ignore_validate = True
			with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
				doc.submit()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("BOM", bom.name, "docstatus"), 0)

	def test_the_tests_stand_in_is_refused_outside_tests(self):
		bom = frappe.get_doc("BOM", self.draft().name)
		bom.flags.bom_approval = True  # as setup.test_stock.submit_bom_for_test sets it
		frappe.in_test = False
		try:
			bom._action = "submit"
			with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
				bill_of_materials.only_by_approval(bom)
		finally:
			frappe.in_test = True
		self.assertTrue(bill_of_materials.approval_of(bom))

	def test_a_bill_inserted_as_submitted_is_refused(self):
		frappe.set_user(self.qa)
		try:
			with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
				frappe.get_doc(
					{
						"doctype": "BOM",
						"item": self.approved_item(FINISHED_GOOD),
						"company": company(),
						"quantity": 1,
						"docstatus": 1,
						"rm_cost_as_per": "Valuation Rate",
						"items": [{"item_code": self.approved_item(), "qty": 1, "rate": 1}],
					}
				).insert()
		finally:
			frappe.set_user("Administrator")

	def test_an_approval_refused_after_the_password_leaves_no_signature(self):
		"""A check that runs in the submit (here: a second default bill needs a change request)
		refuses the approval; nothing of it is kept, also for a caller that does not roll back."""
		product = self.approved_item(FINISHED_GOOD)
		self.approve(self.draft(product=product).name)  # the default bill in force
		second = self.draft(product=product, is_default=1)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "change request"):
			sign_action("BOM", second.name, "Approve", PASSWORD, "Second bill checked")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("BOM", second.name, "docstatus"), 0)
		self.assertFalse(
			frappe.db.exists(
				"Electronic Signature", {"reference_doctype": "BOM", "reference_name": second.name}
			)
		)

	def test_a_wrong_password_or_no_reason_approves_nothing(self):
		from meddevice_suite.compliance.test_esign import reset_lockout

		bom = self.draft()
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action("BOM", bom.name, "Approve", "not the password", "Checked")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action("BOM", bom.name, "Approve", PASSWORD, " ")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		self.assertEqual(frappe.db.get_value("BOM", bom.name, "docstatus"), 0)

	def test_an_approved_bill_is_not_approved_again(self):
		bom = self.draft()
		self.approve(bom.name)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			sign_action("BOM", bom.name, "Approve", PASSWORD, "Again")

	def test_an_amended_bill_is_a_draft_awaiting_its_own_signature(self):
		from meddevice_suite.compliance.audit_reason import stage_reason
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST
		from meddevice_suite.qms.test_change_control import approved_change

		bom = self.draft(is_default=0)
		self.approve(bom.name)
		change = approved_change(self.requester, self.qa, bom.name, "BOM", "Product or BOM")
		frappe.set_user(self.qa)
		doc = frappe.get_doc("BOM", bom.name)
		doc.set(CHANGE_REQUEST, change)  # a bill in use is cancelled under change control
		stage_reason("BOM", bom.name, "update_after_submit", "Naming the change request")
		doc.save()
		stage_reason("BOM", bom.name, "cancel", "Quantity of silicone corrected")
		frappe.get_doc("BOM", bom.name).cancel()
		amended = frappe.copy_doc(frappe.get_doc("BOM", bom.name))
		amended.update({"amended_from": bom.name, "docstatus": 0})  # as Amend on the form starts it
		stage_reason("BOM", bom.name, "amend", "Quantity of silicone corrected")
		amended.insert()
		self.assertEqual(amended.docstatus, 0)
		with self.assertRaises(frappe.PermissionError):
			amended.submit()


@verifies("URS-MST-007")
class TestWhatApprovalNeeds(BomTestCase):
	def assert_not_approvable(self, bom, missing: str) -> None:
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, missing):
			sign_action("BOM", bom.name, "Approve", PASSWORD, "Checked")
		frappe.set_user("Administrator")

	def test_the_product_is_a_finished_good_or_sub_assembly(self):
		for item_type in (FINISHED_GOOD, SUB_ASSEMBLY):
			with self.subTest(item_type=item_type):
				bom = self.draft(product=self.approved_item(item_type))
				self.approve(bom.name)
				self.assertEqual(frappe.db.get_value("BOM", bom.name, "docstatus"), 1)
		raw = self.draft(product=self.approved_item(RAW_MATERIAL))
		self.assert_not_approvable(raw, "finished good or sub-assembly as its product")

	def test_a_component_with_no_quantity_is_refused(self):
		component = self.approved_item()
		bom = self.draft(components=[(component, 1)])
		frappe.db.set_value("BOM Item", bom.items[0].name, "qty", 0)  # as an import might leave it
		self.assert_not_approvable(frappe.get_doc("BOM", bom.name), f"quantity above zero for {component}")

	def test_a_component_qa_has_not_approved_since_drafting_is_refused(self):
		component = self.approved_item()
		bom = self.draft(components=[(component, 1)])
		frappe.db.set_value("Item", component, APPROVAL_STATUS, DRAFT)  # as an item held before module 1
		self.assert_not_approvable(frappe.get_doc("BOM", bom.name), f"{component} approved by QA")

	def test_a_bill_without_components_is_refused(self):
		bom = self.draft()
		frappe.db.delete("BOM Item", {"parent": bom.name})
		self.assert_not_approvable(frappe.get_doc("BOM", bom.name), "at least one component")

	def test_the_process_loss_is_between_0_and_100_percent(self):
		for loss in (120, -5):  # as an import might leave it
			with self.subTest(loss=loss):
				bom = self.draft()
				frappe.db.set_value("BOM", bom.name, "process_loss_percentage", loss)
				self.assert_not_approvable(frappe.get_doc("BOM", bom.name), "process loss between 0 and 100")

	def test_a_product_qa_has_not_approved_since_drafting_is_refused(self):
		product = self.approved_item(FINISHED_GOOD)
		bom = self.draft(product=product)
		frappe.db.set_value("Item", product, APPROVAL_STATUS, DRAFT)  # as an item held before module 1
		self.assert_not_approvable(frappe.get_doc("BOM", bom.name), "its product approved by QA")

	def test_a_draft_product_or_component_is_refused_on_the_bill_itself(self):
		"""Module 1 already refuses a draft item on any record: the bill is never even saved."""
		with self.assertRaisesRegex(frappe.ValidationError, "Not yet approved by QA"):
			self.draft(product=new_item(FINISHED_GOOD).name)

	def test_the_form_says_what_is_missing(self):
		raw = self.draft(product=self.approved_item(RAW_MATERIAL))
		raw.set("__onload", frappe._dict())  # as a form load sets it up
		raw.run_method("onload")
		self.assertTrue(any("finished good" in what for what in raw.get_onload().bom_approval["missing"]))
		ready = self.draft()
		ready.set("__onload", frappe._dict())
		ready.run_method("onload")
		self.assertEqual(ready.get_onload().bom_approval, {"approved": False, "active": True, "missing": []})
		self.approve(ready.name)
		approved = frappe.get_doc("BOM", ready.name)
		approved.set("__onload", frappe._dict())
		approved.run_method("onload")
		self.assertEqual(
			approved.get_onload().bom_approval, {"approved": True, "active": True, "missing": []}
		)
		frappe.db.set_value("BOM", ready.name, "is_active", 0)
		inactive = frappe.get_doc("BOM", ready.name)
		inactive.set("__onload", frappe._dict())
		inactive.run_method("onload")
		self.assertEqual(inactive.get_onload().bom_approval["active"], False)


@verifies("URS-MST-007")
class TestSignatureBindsTheBill(BomTestCase):
	def test_costs_updated_later_leave_the_signature_unchanged(self):
		bom = self.draft()
		self.approve(bom.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "BOM", "reference_name": bom.name}
		)
		frappe.db.set_value("BOM", bom.name, {"raw_material_cost": 999, "total_cost": 999})  # cost update
		self.assertTrue(is_unchanged_since(signature))
		frappe.db.set_value("BOM Item", bom.items[0].name, "qty", 5)  # a component changed past the rules
		self.assertFalse(is_unchanged_since(signature))

	def test_the_signature_binds_the_bill_as_stored(self):
		"""ERPNext's validation fills a component's default sub-bill as the bill is submitted; the
		signature is taken after it, so it binds that sub-bill and agrees with the record."""
		sub_assembly = self.approved_item(SUB_ASSEMBLY)
		bom = self.draft(components=[(sub_assembly, 1)])
		self.assertFalse(bom.items[0].bom_no)  # the sub-assembly has no bill yet
		self.approve(self.draft(product=sub_assembly).name)  # now it has its default bill
		self.approve(bom.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "BOM", "reference_name": bom.name}
		)
		stored = frappe.get_doc("BOM", bom.name)
		self.assertTrue(stored.items[0].bom_no)
		self.assertTrue(is_unchanged_since(signature))

	def test_the_signature_binds_each_part_of_the_recipe(self):
		"""Changing any bound field of the bill or of a row changes what is signed; a cost does not."""
		changed_value = {"Check": lambda v: 1 - (v or 0), "Int": lambda v: (v or 0) + 1}

		def bill() -> frappe.model.document.Document:
			return frappe.get_doc(
				{
					"doctype": "BOM",
					"item": "PRODUCT",
					"quantity": 1,
					"items": [{"item_code": "PART", "qty": 2}],
					"operations": [{"operation": "Moulding", "time_in_mins": 30}],
					"secondary_items": [{"item_code": "SCRAP", "qty": 1}],
					"total_cost": 10,
				}
			)

		def change(doc, field: str) -> None:
			df = doc.meta.get_field(field)
			if df.fieldtype in ("Float", "Percent", "Currency"):
				doc.set(field, (doc.get(field) or 0) + 1.5)
			else:
				doc.set(field, changed_value.get(df.fieldtype, lambda v: "OTHER")(doc.get(field)))

		signed = bill_of_materials.signed_content(bill())
		for field in bill_of_materials.SIGNED_FIELDS:
			with self.subTest(field=field):
				doc = bill()
				change(doc, field)
				self.assertNotEqual(bill_of_materials.signed_content(doc), signed)
		for table, fields in bill_of_materials.SIGNED_ROW_FIELDS.items():
			for field in fields:
				with self.subTest(field=f"{table}.{field}"):
					doc = bill()
					change(doc.get(table)[0], field)
					self.assertNotEqual(bill_of_materials.signed_content(doc), signed)
		costed = bill()
		costed.total_cost = 999
		self.assertEqual(bill_of_materials.signed_content(costed), signed)

	def test_the_recipe_is_bound(self):
		"""What the requirement and the device master record need bound, written here on its own,
		not read from the code's list: dropping one from the code fails this test."""
		recipe = {
			"BOM": {"item", "quantity", "uom", "process_loss_percentage", "with_operations", "routing"},
			"items": {"item_code", "qty", "uom", "bom_no", "allow_alternative_item"},
			"operations": {"operation", "workstation", "time_in_mins", "sequence_id"},
			"secondary_items": {"item_code", "qty", "uom"},
		}
		self.assertLessEqual(recipe["BOM"], set(bill_of_materials.SIGNED_FIELDS))
		for table in ("items", "operations", "secondary_items"):
			self.assertLessEqual(recipe[table], set(bill_of_materials.SIGNED_ROW_FIELDS[table]), table)
		self.assertTrue(
			{"total_cost", "raw_material_cost", "operating_cost"}.isdisjoint(bill_of_materials.SIGNED_FIELDS)
		)

	def test_numbers_bind_alike_from_the_form_and_the_database(self):
		"""A form sends 2 where the database returns 2.0; both are the same approved quantity."""

		from meddevice_suite.compliance.esign import record_fingerprint

		def bill(qty, loss, flag):  # the hash tells 2 from 2.0, where == does not
			return record_fingerprint(
				frappe.get_doc(
					{
						"doctype": "BOM",
						"item": "PRODUCT",
						"quantity": qty,
						"process_loss_percentage": loss,
						"with_operations": flag,
						"items": [{"item_code": "PART", "qty": qty}],
					}
				)
			)

		self.assertEqual(bill(2, 0, 1), bill(2.0, 0.0, True))

	def test_every_bound_field_is_a_field_of_erpnext(self):
		"""A field ERPNext renames or drops in an upgrade fails here, not silently."""
		for doctype, fields in (
			("BOM", bill_of_materials.SIGNED_FIELDS),
			("BOM Item", bill_of_materials.SIGNED_ROW_FIELDS["items"]),
			("BOM Operation", bill_of_materials.SIGNED_ROW_FIELDS["operations"]),
			("BOM Secondary Item", bill_of_materials.SIGNED_ROW_FIELDS["secondary_items"]),
		):
			meta = frappe.get_meta(doctype)
			self.assertEqual([f for f in fields if not meta.has_field(f)], [], doctype)
		self.assertEqual(
			{t: frappe.get_meta("BOM").get_field(t).options for t in bill_of_materials.SIGNED_ROW_FIELDS},
			{"items": "BOM Item", "operations": "BOM Operation", "secondary_items": "BOM Secondary Item"},
		)


@verifies("URS-MST-007")
class TestWorkOrders(BomTestCase):
	def test_a_work_order_is_made_only_from_an_approved_active_bill_of_its_product(self):
		product = self.approved_item(FINISHED_GOOD)
		draft = self.draft(product=product, is_default=0)
		with self.assertRaisesRegex(frappe.ValidationError, "approved, active bill of materials"):
			self.work_order(product, draft.name).insert()
		self.approve(draft.name)
		self.work_order(product, draft.name).insert()  # approved and active: made
		frappe.db.set_value("BOM", draft.name, "is_active", 0)
		with self.assertRaises(frappe.ValidationError):  # ERPNext's own check or this app's
			self.work_order(product, draft.name).insert()
		other_product = self.approved_item(FINISHED_GOOD)
		other = self.draft(product=other_product, is_default=0)
		self.approve(other.name)
		with self.assertRaises(frappe.ValidationError):
			self.work_order(product, other.name).insert()

	def test_the_check_holds_where_erpnext_skips_its_own(self):
		"""ERPNext does not ask for a submitted bill while tests run (bom.validate_bom_no); the app's
		check does, on the guard itself."""
		product = self.approved_item(FINISHED_GOOD)
		draft = self.draft(product=product, is_default=0)
		with self.assertRaisesRegex(frappe.ValidationError, "approved, active bill of materials"):
			bill_of_materials.guard_work_order(self.work_order(product, draft.name))

	def test_the_check_refuses_each_wrong_bill_by_itself(self):
		"""ERPNext checks the bill's product and that it is active as well; the app's check does not
		rely on that (an upgrade may change it)."""
		product = self.approved_item(FINISHED_GOOD)
		good = self.draft(product=product, is_default=0)
		self.approve(good.name)  # the product's first bill: its default
		inactive = self.draft(product=product, is_default=0)
		self.approve(inactive.name)
		frappe.db.set_value("BOM", inactive.name, "is_active", 0)
		other = self.draft(product=self.approved_item(FINISHED_GOOD), is_default=0)
		self.approve(other.name)
		for case, bom in (
			("inactive", inactive.name),
			("another product's", other.name),
			("unknown", "BOM-NONE"),
		):
			with self.subTest(case=case):
				with self.assertRaisesRegex(frappe.ValidationError, "approved, active bill of materials"):
					bill_of_materials.guard_work_order(self.work_order(product, bom))
		bill_of_materials.guard_work_order(self.work_order(product, good.name))  # the right bill passes

	def test_a_work_order_made_without_validation_is_checked_too(self):
		"""Production Plan makes its work orders with validation skipped; and ERPNext accepts a bill
		that merely contains the product as a component."""
		product = self.approved_item(SUB_ASSEMBLY)
		containing = self.draft(components=[(product, 1)], is_default=0)  # another product's bill
		self.approve(containing.name)
		for skipped in (False, True):
			with self.subTest(validation_skipped=skipped):
				order = self.work_order(product, containing.name)
				order.flags.ignore_validate = skipped
				with self.assertRaisesRegex(frappe.ValidationError, "approved, active bill of materials"):
					order.insert()
		order = self.work_order(product, "")
		order.flags.ignore_validate = order.flags.ignore_mandatory = True
		with self.assertRaisesRegex(frappe.ValidationError, "approved, active bill of materials"):
			order.insert()

	@verifies("URS-MST-001")
	def test_only_the_default_bill_in_force_is_produced_on(self):
		"""A second bill QA approved without displacing the default (to prepare a change, say) is not
		produced on, for a sub-assembly as for a finished good, also when validation is skipped; it
		becomes the bill produced on only as the default, replaced under an approved change request
		(independent review of 2026-10-15)."""
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST
		from meddevice_suite.qms.test_change_control import approved_change

		for item_type in (SUB_ASSEMBLY, FINISHED_GOOD):
			with self.subTest(item_type=item_type):
				product = self.approved_item(item_type)
				default = self.draft(product=product)
				self.approve(default.name)
				second = self.draft(product=product, is_default=0)
				self.approve(second.name, "Prepared for a change of supplier")  # approved, active
				self.assertEqual(frappe.db.get_value("BOM", second.name, ["docstatus", "is_active"]), (1, 1))
				for skipped in (False, True):
					order = self.work_order(product, second.name)
					order.flags.ignore_validate = order.flags.ignore_mandatory = skipped
					with self.assertRaisesRegex(frappe.ValidationError, "not the default bill of materials"):
						order.insert()
				bill_of_materials.guard_work_order(self.work_order(product, default.name))

		# Made the default under an approved change request, it is the one produced on.
		product = self.approved_item(SUB_ASSEMBLY)
		old = self.draft(product=product)
		self.approve(old.name)
		change = approved_change(signer("Production Supervisor"), self.qa, old.name, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		new = self.draft(product=product, **{CHANGE_REQUEST: change})
		self.approve(new.name, "Replaces the bill in force under the change")
		bill_of_materials.guard_work_order(self.work_order(product, new.name))
		with self.assertRaisesRegex(frappe.ValidationError, "not the default bill of materials"):
			bill_of_materials.guard_work_order(self.work_order(product, old.name))
