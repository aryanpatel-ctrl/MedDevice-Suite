"""Item master (module 1): tests derived from URS-MST-003 to URS-MST-006, each rule tried on every
way a record can be made (the matrix in docs/features/item-master.md, section 3)."""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from meddevice_suite.compliance.esign import SigningRefused, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import company, store
from meddevice_suite.master_data import item_master
from meddevice_suite.master_data.item_master import (
	APPROVAL_STATUS,
	APPROVED,
	APPROVED_AT,
	APPROVED_BY,
	BOXES_PER_CARTON,
	DEVICE_CLASS,
	DRAFT,
	EQUIPMENT,
	FINISHED_GOOD,
	ITEM_TYPE,
	PACKAGING,
	RAW_MATERIAL,
	SETUP_TYPES,
	STERILE,
	STORAGE_CONDITION,
	SUB_ASSEMBLY,
	UDI_DI,
	UNITS_PER_BOX,
	item_fields_of,
	valid_gtin,
)
from meddevice_suite.master_data.versioning import CHANGE_REQUEST, VERSION
from meddevice_suite.pilot.inspection import ensure_inspection_masters, ensure_inspection_template
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import approved_change
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import (
	approve_equipment_for_test,
	licensed_for_test,
	submit_bom_for_test,
)

REFUSED = (frappe.ValidationError, frappe.PermissionError)
NOT_APPROVED = "Not yet approved by QA"
SUPPLIER = "MDS Item Master Supplier"
CUSTOMER = "MDS Item Master Customer"


def gtin(seed: str) -> str:
	"""A well-formed GTIN-13 in GS1's range for restricted circulation (20…), unique per seed."""
	body = "20" + f"{abs(hash(seed)) % 10**10:010d}"
	total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
	return body + str((10 - total % 10) % 10)


def code(prefix: str = "IM") -> str:
	return f"MDS-{prefix}-{frappe.generate_hash(length=8).upper()}"


def new_item(item_type: str = RAW_MATERIAL, **values) -> frappe.model.document.Document:
	"""A draft item of the type, with what its approval needs unless told otherwise."""
	item_code = values.pop("item_code", None) or code()
	lot_tracked = item_type in item_master.LOT_TRACKED_TYPES
	data = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_code,
		"item_group": "Products" if item_type == FINISHED_GOOD else "Raw Material",
		"stock_uom": "Nos",
		"gst_hsn_code": "39269099",
		ITEM_TYPE: item_type,
		"is_stock_item": int(item_type != EQUIPMENT),
		"has_batch_no": int(lot_tracked),
		"create_new_batch": int(lot_tracked),
		"batch_number_series": f"{item_code}-.####" if lot_tracked else None,
		"has_expiry_date": int(lot_tracked),
		"shelf_life_in_days": 365 if lot_tracked else 0,
		STORAGE_CONDITION: "Below 30 °C, dry (test)" if lot_tracked else None,
		"item_defaults": [{"company": company(), "default_warehouse": store("Stores")}],
	}
	if item_type == FINISHED_GOOD:
		data.update(
			{
				STERILE: "Yes",
				DEVICE_CLASS: "B",
				UNITS_PER_BOX: 10,
				BOXES_PER_CARTON: 20,
				UDI_DI: gtin(item_code),
			}
		)
	data.update(values)
	return frappe.get_doc(data).insert()


def with_specification(doc) -> frappe.model.document.Document:
	"""The specification the item's type is inspected against (module 6), set on the item."""
	group = "Products" if doc.get(ITEM_TYPE) == FINISHED_GOOD else "Raw Material"
	ensure_inspection_template(doc.name, group)
	return frappe.get_doc("Item", doc.name)


class ItemMasterTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.qa = signer("QA Manager")
		cls.purchase = signer("Purchase")  # holds no QA role
		cls.stores_user = signer("Stores")
		cls.requester = signer("Production Supervisor")
		ensure_inspection_masters(company(), lambda item: cls.approve_as_qa(item), approve_equipment_for_test)
		for doctype, name, values in (
			("Supplier", SUPPLIER, {"supplier_name": SUPPLIER, "supplier_group": "Raw Material"}),
			(
				"Customer",
				CUSTOMER,
				{"customer_name": CUSTOMER, "customer_group": "Commercial", "territory": "India"},
			),
		):
			if not frappe.db.exists(doctype, name):
				frappe.get_doc({"doctype": doctype, **values}).insert()
		licensed_for_test(CUSTOMER)  # module 5: sold to only under a verified licence

	@classmethod
	def approve_as_qa(cls, item: str, reason: str = "Item master checked") -> None:
		frappe.set_user(cls.qa)
		try:
			sign_action("Item", item, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def approved_item(self, item_type: str = RAW_MATERIAL, **values) -> str:
		doc = new_item(item_type, **values)
		if item_type in (RAW_MATERIAL, PACKAGING, FINISHED_GOOD):
			with_specification(doc)
		self.approve_as_qa(doc.name)
		return doc.name


# --- URS-MST-003: approved by QA with a signature before use ---------------------------------------


@verifies("URS-MST-003")
class TestSignedApproval(ItemMasterTestCase):
	def test_a_new_item_is_a_draft(self):
		doc = new_item()
		self.assertEqual(doc.get(APPROVAL_STATUS), DRAFT)
		self.assertFalse(doc.get(APPROVED_BY) or doc.get(APPROVED_AT))

	def test_qa_approves_an_item_with_a_signature(self):
		doc = with_specification(new_item())
		self.approve_as_qa(doc.name, "Specification and supplier data checked")
		stored = frappe.get_doc("Item", doc.name)
		self.assertEqual(stored.get(APPROVAL_STATUS), APPROVED)
		self.assertEqual(stored.get(APPROVED_BY), self.qa)
		self.assertTrue(stored.get(APPROVED_AT))
		signature = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Item", "reference_name": doc.name},
			fields=["meaning", "action", "signer", "reason"],
		)
		self.assertEqual(
			[(s.meaning, s.action, s.signer, s.reason) for s in signature],
			[("Approved", "Approve", self.qa, "Specification and supplier data checked")],
		)
		self.assertTrue(verify_signatures("Item", doc.name))
		latest = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": "Item", "reference_name": doc.name},
			fields=["action", "changes"],
			order_by="event_at desc, creation desc",
			limit=1,
		)[0]
		self.assertEqual(latest.action, "Updated")  # the audit trail keeps who approved, and when
		self.assertIn(APPROVAL_STATUS, latest.changes)

	def test_only_qa_approves(self):
		doc = with_specification(new_item())
		for user in (self.purchase, self.stores_user, self.requester):
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(REFUSED):
					sign_action("Item", doc.name, "Approve", PASSWORD, "Not mine to approve")
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):  # a shared account never signs
			sign_action("Item", doc.name, "Approve", PASSWORD, "Shared account")
		# Someone who may edit items (Item Manager) but is not QA: the case the rule is for.
		editor = "item.editor@item-master.example.com"
		if not frappe.db.exists("User", editor):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": editor,
					"first_name": "Item editor",
					"send_welcome_email": 0,
					"roles": [{"role": "Item Manager"}],
				}
			).insert(ignore_permissions=True)
		from frappe.utils.password import update_password

		from meddevice_suite.compliance.test_esign import reset_lockout

		update_password(editor, PASSWORD)
		reset_lockout(editor)
		self.assertTrue(frappe.has_permission("Item", "write", doc=doc.name, user=editor))
		frappe.set_user(editor)
		with self.assertRaisesRegex(frappe.PermissionError, "Only QA approves an item"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "Editing is not approving")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), DRAFT)

	def test_a_wrong_password_approves_nothing(self):
		doc = with_specification(new_item())
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action("Item", doc.name, "Approve", "not the password", "Checked")
		frappe.set_user("Administrator")
		from meddevice_suite.compliance.test_esign import reset_lockout

		reset_lockout(self.qa)
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), DRAFT)
		self.assertFalse(frappe.db.exists("Electronic Signature", {"reference_name": doc.name}))

	def test_an_approval_says_why(self):
		doc = with_specification(new_item())
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "  ")

	def test_the_approval_is_given_only_by_the_signed_act(self):
		doc = with_specification(new_item())
		frappe.set_user(self.qa)  # even QA, on the form or through the API
		for field, value in (
			(APPROVAL_STATUS, APPROVED),
			(APPROVED_BY, self.qa),
			(APPROVED_AT, "2026-10-10 10:00:00"),
		):
			with self.subTest(field=field):
				with self.assertRaises(frappe.PermissionError):
					frappe.client.set_value("Item", doc.name, field, value)
		item = frappe.get_doc("Item", doc.name)
		item.set(APPROVAL_STATUS, APPROVED)
		with self.assertRaises(frappe.PermissionError):
			item.save()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), DRAFT)

	def test_an_item_made_approved_starts_as_a_draft(self):
		doc = new_item(
			**{APPROVAL_STATUS: APPROVED, APPROVED_BY: self.qa, APPROVED_AT: "2026-10-10 10:00:00"}
		)
		self.assertEqual(
			frappe.db.get_value("Item", doc.name, [APPROVAL_STATUS, APPROVED_BY, APPROVED_AT]),
			(DRAFT, None, None),
		)
		api = frappe.client.insert(
			{
				"doctype": "Item",
				"item_code": code(),
				"item_group": "Raw Material",
				"stock_uom": "Nos",
				"gst_hsn_code": "39269099",
				ITEM_TYPE: SUB_ASSEMBLY,
				APPROVAL_STATUS: APPROVED,
			}
		)
		self.assertEqual(frappe.db.get_value("Item", api["name"], APPROVAL_STATUS), DRAFT)

	def test_an_approved_item_is_not_approved_again(self):
		item = self.approved_item()
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			sign_action("Item", item, "Approve", PASSWORD, "Again")

	def test_a_copy_of_an_approved_item_is_a_draft_without_its_identifier(self):
		item = self.approved_item(FINISHED_GOOD)
		copy = frappe.copy_doc(frappe.get_doc("Item", item), ignore_no_copy=False)  # as the form's Duplicate
		copy.item_code = code()
		copy.insert()
		self.assertEqual(copy.get(APPROVAL_STATUS), DRAFT)
		self.assertFalse(copy.get(APPROVED_BY) or copy.get(UDI_DI))

	def test_a_template_is_not_approved_and_each_variant_is_approved_on_its_own(self):
		from erpnext.controllers.item_variant import create_variant

		attribute = "MDS Size"
		if not frappe.db.exists("Item Attribute", attribute):
			frappe.get_doc(
				{
					"doctype": "Item Attribute",
					"attribute_name": attribute,
					"item_attribute_values": [{"attribute_value": "16 Fr", "abbr": "16"}],
				}
			).insert()
		template = new_item(
			FINISHED_GOOD,
			has_variants=1,
			attributes=[{"attribute": attribute}],
			**{UDI_DI: None},
		)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "A template is not approved"):
			sign_action("Item", template.name, "Approve", PASSWORD, "Template")
		frappe.set_user("Administrator")
		variant = create_variant(template.name, {attribute: "16 Fr"})
		variant.insert()
		self.assertEqual(variant.get(APPROVAL_STATUS), DRAFT)
		self.assertEqual(variant.get(ITEM_TYPE), FINISHED_GOOD)  # the type follows the template
		self.assertFalse(variant.get(UDI_DI))  # its own identifier, never the template's
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "UDI device identifier"):
			sign_action("Item", variant.name, "Approve", PASSWORD, "Variant without its GTIN")
		frappe.set_user("Administrator")
		variant = frappe.get_doc("Item", variant.name)
		variant.set(UDI_DI, gtin(variant.name))
		variant.save()
		with_specification(variant)
		self.approve_as_qa(variant.name)
		self.assertEqual(frappe.db.get_value("Item", variant.name, APPROVAL_STATUS), APPROVED)

	def test_from_approval_the_controlled_fields_change_only_under_an_approved_change_request(self):
		item = self.approved_item(FINISHED_GOOD)
		for field, value in (
			("shelf_life_in_days", 730),
			(STORAGE_CONDITION, "Below 25 °C"),
			(DEVICE_CLASS, "C"),
			(STERILE, "No"),
			(UNITS_PER_BOX, 12),
			(UDI_DI, gtin(item + "-new")),
		):
			with self.subTest(field=field):
				with self.assertRaisesRegex(
					frappe.ValidationError, "is in use: name the approved change request"
				):
					frappe.client.set_value("Item", item, field, value)
		change = approved_change(self.requester, self.qa, item, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", item)
		doc.update({STORAGE_CONDITION: "Below 25 °C", CHANGE_REQUEST: change})
		doc.save()
		stored = frappe.db.get_value(
			"Item", item, [STORAGE_CONDITION, VERSION, APPROVAL_STATUS], as_dict=True
		)
		self.assertEqual(
			(stored[STORAGE_CONDITION], stored[VERSION], stored[APPROVAL_STATUS]),
			("Below 25 °C", 2, APPROVED),
		)
		self.assertTrue(verify_signatures("Item", item))  # the approval still stands

	def test_the_signature_binds_what_qa_approved_not_what_use_changes(self):
		from meddevice_suite.compliance.esign import is_unchanged_since

		item = self.approved_item()
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Item", "reference_name": item}
		)
		doc = frappe.get_doc("Item", item)
		doc.description = "Medical grade silicone (note)"
		doc.save()
		frappe.db.set_value("Item", item, "last_purchase_rate", 12.5)  # as ERPNext keeps it after a purchase
		self.assertTrue(is_unchanged_since(signature))
		change = approved_change(self.requester, self.qa, item, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", item)
		doc.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		doc.save()
		self.assertFalse(
			is_unchanged_since(signature)
		)  # a controlled change since signing, under its request

	def test_an_item_held_before_the_module_is_completed_once_without_a_change_request(self):
		item = self.approved_item()
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
			}
		).insert().submit()  # in use by its stock
		# As an item held before the module: a draft, its storage never stated.
		frappe.db.set_value("Item", item, {APPROVAL_STATUS: DRAFT, STORAGE_CONDITION: None})
		doc = frappe.get_doc("Item", item)
		doc.set(STORAGE_CONDITION, "Below 30 °C")
		doc.save()  # filled once, without a change request
		doc = frappe.get_doc("Item", item)
		doc.set(STORAGE_CONDITION, "Below 25 °C")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()  # a change of what was stated is controlled

	def test_a_storage_temperature_may_have_one_end_only(self):
		for values in (
			{item_master.MIN_TEMPERATURE: "2"},
			{item_master.MAX_TEMPERATURE: "-20"},
			{item_master.MIN_TEMPERATURE: "0", item_master.MAX_TEMPERATURE: "25"},
		):
			with self.subTest(values=values):
				doc = new_item(**values)
				self.assertEqual({f: frappe.db.get_value("Item", doc.name, f) for f in values}, values)
		with self.assertRaisesRegex(frappe.ValidationError, "is a temperature"):
			new_item(**{item_master.MIN_TEMPERATURE: "cold"})
		with self.assertRaisesRegex(frappe.ValidationError, "not above the maximum"):
			new_item(**{item_master.MIN_TEMPERATURE: "8", item_master.MAX_TEMPERATURE: "2"})

	def test_an_item_held_before_the_module_gets_its_inspection_before_approval(self):
		"""Found on the pilot (2026-10-11): an item with stock from before the specifications, its
		inspection off and no specification, is given them without a change request while QA has
		not approved it; switching inspection off again is a controlled change."""
		item = self.approved_item()
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
			}
		).insert().submit()  # in use by its stock
		specification = frappe.db.get_value("Item", item, "quality_inspection_template")
		frappe.db.set_value(  # as an item held before the modules
			"Item",
			item,
			{
				APPROVAL_STATUS: DRAFT,
				"quality_inspection_template": None,
				"inspection_required_before_purchase": 0,
			},
		)
		doc = frappe.get_doc("Item", item)
		doc.update({"quality_inspection_template": specification, "inspection_required_before_purchase": 1})
		doc.save()  # completed once, without a change request
		doc = frappe.get_doc("Item", item)
		doc.inspection_required_before_purchase = 0
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()  # switching it off again is controlled

	def test_pack_sizes_of_an_item_held_before_the_module_are_filled_once(self):
		item = self.approved_item(FINISHED_GOOD)
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
			}
		).insert().submit()  # in use by its stock
		frappe.db.set_value("Item", item, {APPROVAL_STATUS: DRAFT, UNITS_PER_BOX: 0})  # held before
		doc = frappe.get_doc("Item", item)
		doc.set(UNITS_PER_BOX, 12)
		doc.save()  # stated once, without a change request
		doc = frappe.get_doc("Item", item)
		doc.set(UNITS_PER_BOX, 24)
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()

	def test_a_variants_attributes_change_only_under_a_change_request_once_approved(self):
		from erpnext.controllers.item_variant import create_variant

		from meddevice_suite.compliance.esign import is_unchanged_since

		attribute = "MDS Size"
		if not frappe.db.exists("Item Attribute", attribute):
			frappe.get_doc(
				{"doctype": "Item Attribute", "attribute_name": attribute, "item_attribute_values": []}
			).insert()
		values = frappe.get_doc("Item Attribute", attribute)
		for value, abbr in (("16 Fr", "16"), ("18 Fr", "18")):
			if value not in [row.attribute_value for row in values.item_attribute_values]:
				values.append("item_attribute_values", {"attribute_value": value, "abbr": abbr})
		values.save()
		template = new_item(
			FINISHED_GOOD, has_variants=1, attributes=[{"attribute": attribute}], **{UDI_DI: None}
		)
		variant = create_variant(template.name, {attribute: "16 Fr"})
		variant.set(UDI_DI, gtin(variant.item_code))
		variant.insert()
		with_specification(variant)
		self.approve_as_qa(variant.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Item", "reference_name": variant.name}
		)
		doc = frappe.get_doc("Item", variant.name)
		doc.attributes[0].attribute_value = "18 Fr"
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()
		change = approved_change(self.requester, self.qa, variant.name, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", variant.name)
		doc.attributes[0].attribute_value = "18 Fr"
		doc.set(CHANGE_REQUEST, change)
		doc.save()
		self.assertFalse(is_unchanged_since(signature))  # what QA approved has changed, under its request

	def test_a_table_changed_past_the_rules_shows_as_changed_since_signing(self):
		"""The signature binds the item's tables, not only its fields: a unit conversion changed
		straight in the database (no version raised) is detected (21 CFR 11.70)."""
		from meddevice_suite.compliance.esign import is_unchanged_since

		if not frappe.db.exists("UOM", "Box"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Box"}).insert()
		item = new_item(
			uoms=[{"uom": "Nos", "conversion_factor": 1}, {"uom": "Box", "conversion_factor": 10}]
		)
		with_specification(item)
		self.approve_as_qa(item.name)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Item", "reference_name": item.name}
		)
		self.assertTrue(is_unchanged_since(signature))
		row = frappe.db.get_value("UOM Conversion Detail", {"parent": item.name, "uom": "Box"})
		frappe.db.set_value("UOM Conversion Detail", row, "conversion_factor", 12)  # past every rule
		self.assertFalse(is_unchanged_since(signature))

	def test_a_draft_item_changes_freely(self):
		doc = new_item(FINISHED_GOOD)
		doc.update({DEVICE_CLASS: "C", STORAGE_CONDITION: "Below 25 °C", "shelf_life_in_days": 730})
		doc.save()
		self.assertEqual(frappe.db.get_value("Item", doc.name, VERSION), 1)

	def test_an_approved_item_keeps_its_name(self):
		from frappe.model.rename_doc import rename_doc

		item = self.approved_item()
		with self.assertRaises(REFUSED):
			rename_doc("Item", item, code(), force=True)

	def test_items_held_before_the_item_master_are_drafts(self):
		doc = new_item()
		frappe.db.set_value("Item", doc.name, APPROVAL_STATUS, None)  # as before the module was installed
		item_master.apply_item_master_fields()
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), DRAFT)


@verifies("URS-MST-003")
class TestNotUsedBeforeApproval(ItemMasterTestCase):
	"""A draft item is refused on every record that puts it to use, however the record is made."""

	def assert_refused(self, doc):
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			doc.insert()

	def test_every_record_type_naming_an_item_refuses_a_draft_one(self):
		"""The reference is each installed app's own definition of its record types (their JSON
		files), read here independently of the guard's code: every record type with a link to Item,
		directly or in a table, is given a draft item in that field and must refuse it; only the
		setup record types may take it."""
		import glob
		import json
		import os

		definitions = {}
		for app in frappe.get_installed_apps():
			for path in glob.glob(
				os.path.join(frappe.get_app_path(app), "**", "doctype", "*", "*.json"), recursive=True
			):
				try:
					definition = json.load(open(path))
				except ValueError:
					continue
				if isinstance(definition, dict) and definition.get("doctype") == "DocType":
					definitions[definition["name"]] = definition

		def item_links(definition):
			return [
				f["fieldname"]
				for f in definition.get("fields", [])
				if f.get("fieldtype") == "Link" and f.get("options") == "Item"
			]

		draft = new_item().name
		tried = set()
		for name, definition in sorted(definitions.items()):
			if (
				definition.get("istable")
				or definition.get("issingle")
				or not frappe.db.exists("DocType", name)
			):
				continue
			cases = [(field, None) for field in item_links(definition)]
			for f in definition.get("fields", []):
				# A virtual table is computed on the fly (from the BOM, say) and never stored.
				if (
					f.get("fieldtype") in ("Table", "Table MultiSelect")
					and f.get("options") in definitions
					and not f.get("is_virtual")
				):
					cases += [
						(row_field, f["fieldname"]) for row_field in item_links(definitions[f["options"]])
					]
			for field, table in cases:
				with self.subTest(doctype=name, field=field, table=table):
					doc = frappe.new_doc(name)
					if table:
						doc.append(table, {field: draft})
					else:
						doc.set(field, draft)
					if name in SETUP_TYPES or name in item_master.CORRECTIONS:
						item_master.refuse_unapproved(doc, "validate")  # setup and corrections take it
					else:
						with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
							item_master.refuse_unapproved(doc, "validate")
					tried.add(name)
		# What the requirement names explicitly is among what was tried.
		for doctype in (
			"Material Request",
			"Request for Quotation",
			"Supplier Quotation",
			"Purchase Order",
			"Quotation",
			"Sales Order",
			"Purchase Receipt",
			"Purchase Invoice",
			"Stock Entry",
			"Stock Reconciliation",
			"Batch",
			"Serial and Batch Bundle",
			"BOM",
			"Work Order",
			"Job Card",
			"Quality Inspection",
			"Delivery Note",
			"Sales Invoice",
			"Pick List",
			"Packing Slip",
			"Product Bundle",
			"Item Alternative",
			"Asset",
			"Stock Ledger Entry",
		):
			self.assertIn(doctype, tried)

	def test_a_row_added_to_a_submitted_record_in_memory_is_checked(self):
		"""Code that appends a row to a submitted record and saves it writes the row without the
		row's own hooks; the record's update after submit is where the item is checked."""
		product = self.approved_item(FINISHED_GOOD)
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": CUSTOMER,
				"company": company(),
				"delivery_date": today(),
				"items": [
					{
						"item_code": product,
						"qty": 1,
						"rate": 10,
						"delivery_date": today(),
						"warehouse": store("Stores"),
					}
				],
			}
		).insert()
		order.submit()
		order = frappe.get_doc("Sales Order", order.name)
		draft = with_specification(new_item(FINISHED_GOOD)).name
		order.append(
			"items",
			{
				"item_code": draft,
				"qty": 1,
				"rate": 10,
				"delivery_date": getdate(today()),
				"warehouse": store("Stores"),
			},
		)
		order.flags.ignore_validate_update_after_submit = True  # as ERPNext's own after-submit updates do
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			order.save()

	def test_a_work_order_for_an_item_returned_to_draft_is_refused_on_save(self):
		"""A real save, not the guard alone: a product with a BOM, then held as a draft (as an item
		made before this module is), cannot be put into production."""
		material = self.approved_item()
		product = self.approved_item(FINISHED_GOOD)
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
			}
		).insert()
		submit_bom_for_test(bom)
		frappe.db.set_value("Item", product, APPROVAL_STATUS, DRAFT)  # held as a draft
		order = frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": product,
				"bom_no": bom.name,
				"qty": 1,
				"company": company(),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store("Quarantine"),
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			order.insert()

	def test_purchasing_refuses_a_draft_item(self):
		from meddevice_suite.master_data.test_supplier_approval import approved_for

		item = new_item().name
		approved_for(SUPPLIER, [item])  # the supplier is approved for it; the item is not
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Material Request",
					"material_request_type": "Purchase",
					"company": company(),
					"schedule_date": add_days(today(), 7),
					"items": [
						{
							"item_code": item,
							"qty": 5,
							"schedule_date": add_days(today(), 7),
							"warehouse": store("Stores"),
						}
					],
				}
			)
		)
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Purchase Order",
					"supplier": SUPPLIER,
					"company": company(),
					"schedule_date": add_days(today(), 7),
					"items": [
						{"item_code": item, "qty": 5, "rate": 1, "schedule_date": add_days(today(), 7)}
					],
				}
			)
		)

	def test_stores_refuse_a_draft_item(self):
		item = new_item().name
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": company(),
					"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
				}
			)
		)
		self.assert_refused(frappe.get_doc({"doctype": "Batch", "batch_id": code("LOT"), "item": item}))

	def test_production_refuses_a_draft_item(self):
		material = self.approved_item()
		product = with_specification(new_item(FINISHED_GOOD)).name
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
			}
		)
		self.assert_refused(bom)
		# And a draft material in the bill of an approved product.
		approved_product = self.approved_item(FINISHED_GOOD)
		draft_material = new_item().name
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "BOM",
					"item": approved_product,
					"company": company(),
					"quantity": 1,
					"rm_cost_as_per": "Valuation Rate",
					"items": [{"item_code": draft_material, "qty": 1, "rate": 1}],
				}
			)
		)

	def test_sales_refuse_a_draft_item(self):
		item = with_specification(new_item(FINISHED_GOOD)).name
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Quotation",
					"party_name": CUSTOMER,
					"company": company(),
					"items": [{"item_code": item, "qty": 1, "rate": 10}],
				}
			)
		)
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Sales Order",
					"customer": CUSTOMER,
					"company": company(),
					"delivery_date": today(),
					"items": [{"item_code": item, "qty": 1, "rate": 10, "delivery_date": today()}],
				}
			)
		)

	def test_bundles_and_alternatives_refuse_a_draft_item(self):
		bundle_parent = new_item(
			SUB_ASSEMBLY, is_stock_item=0, has_batch_no=0, has_expiry_date=0, shelf_life_in_days=0
		)
		approved = self.approved_item()
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Product Bundle",
					"new_item_code": bundle_parent.name,
					"items": [{"item_code": approved, "qty": 1}],
				}
			)
		)
		draft = new_item(allow_alternative_item=1).name
		with_alternatives = self.approved_item(allow_alternative_item=1)
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Item Alternative",
					"item_code": with_alternatives,
					"alternative_item_code": draft,
				}
			)
		)

	def test_an_asset_is_not_made_of_a_draft_item(self):
		doc = new_item(
			EQUIPMENT, is_fixed_asset=1, asset_category=frappe.db.get_value("Asset Category", {}, "name")
		)
		self.assert_refused(
			frappe.get_doc(
				{
					"doctype": "Asset",
					"asset_name": doc.name,
					"item_code": doc.name,
					"company": company(),
					"location": frappe.db.get_value("Location", {}, "name"),
					"purchase_date": today(),
					"available_for_use_date": today(),
					"asset_type": "Existing Asset",
					"gross_purchase_amount": 100,
					"net_purchase_amount": 100,
				}
			)
		)

	def test_the_api_refuses_a_draft_item(self):
		item = new_item().name
		frappe.set_user(self.stores_user)
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			frappe.client.insert(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"company": company(),
					"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
				}
			)

	def test_a_movement_posted_straight_to_the_ledger_is_refused(self):
		"""The stock ledger is the backstop: ERPNext's own postings pass it, whatever made them."""
		item = new_item().name
		ledger = frappe.new_doc("Stock Ledger Entry")
		ledger.update(
			{"item_code": item, "warehouse": store("Stores"), "actual_qty": 1, "posting_date": today()}
		)
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			item_master.refuse_unapproved(ledger)

	def test_update_items_on_a_submitted_order_takes_no_draft_item(self):
		"""ERPNext's Update Items adds rows to a submitted order without its validate."""
		import json

		from erpnext.controllers.accounts_controller import update_child_qty_rate

		product = self.approved_item(FINISHED_GOOD)
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": CUSTOMER,
				"company": company(),
				"delivery_date": today(),
				"items": [
					{
						"item_code": product,
						"qty": 1,
						"rate": 10,
						"delivery_date": today(),
						"warehouse": store("Stores"),
					}
				],
			}
		).insert()
		order.submit()
		draft = with_specification(new_item(FINISHED_GOOD)).name
		rows = [
			{"docname": order.items[0].name, "item_code": product, "qty": 1, "rate": 10},
			{"item_code": draft, "qty": 1, "rate": 10, "delivery_date": today()},
		]
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			update_child_qty_rate("Sales Order", json.dumps(rows), order.name)
		self.assertEqual(
			[row.item_code for row in frappe.get_doc("Sales Order", order.name).items], [product]
		)
		# An approved item is taken on as before.
		rows[1]["item_code"] = self.approved_item(FINISHED_GOOD)
		update_child_qty_rate("Sales Order", json.dumps(rows), order.name)
		self.assertEqual(len(frappe.get_doc("Sales Order", order.name).items), 2)

	def test_more_record_types_refuse_a_draft_item(self):
		from meddevice_suite.master_data.test_supplier_approval import approved_for

		item = new_item().name
		approved_for(SUPPLIER, [item])
		finished = with_specification(new_item(FINISHED_GOOD)).name
		records = {
			"Purchase Receipt": {
				"supplier": SUPPLIER,
				"items": [{"item_code": item, "qty": 1, "rate": 1, "warehouse": store("Quarantine")}],
			},
			"Supplier Quotation": {"supplier": SUPPLIER, "items": [{"item_code": item, "qty": 1, "rate": 1}]},
			"Request for Quotation": {
				"suppliers": [{"supplier": SUPPLIER}],
				"message_for_supplier": "Please quote",
				"items": [
					{
						"item_code": item,
						"qty": 1,
						"schedule_date": add_days(today(), 7),
						"uom": "Nos",
						"conversion_factor": 1,
						"warehouse": store("Stores"),
					}
				],
			},
			"Sales Invoice": {"customer": CUSTOMER, "items": [{"item_code": finished, "qty": 1, "rate": 1}]},
		}
		for doctype, values in records.items():
			with self.subTest(doctype=doctype):
				with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
					frappe.get_doc({"doctype": doctype, "company": company(), **values}).insert()
		# These are refused by ERPNext's or this app's own checks first (no stock to deliver, the
		# finished lot's store); the guard is tried on them as built.
		for doctype, values in {
			"Delivery Note": {"customer": CUSTOMER, "items": [{"item_code": finished, "qty": 1, "rate": 1}]},
			"Work Order": {"production_item": finished, "qty": 1},
			"Quality Inspection": {"item_code": item, "inspection_type": "Incoming", "sample_size": 1},
			"Job Card": {"production_item": finished, "for_quantity": 1},
			"Pick List": {"locations": [{"item_code": finished, "qty": 1}]},
			"Stock Reconciliation": {"items": [{"item_code": item, "warehouse": store("Stores"), "qty": 1}]},
		}.items():
			with self.subTest(doctype=doctype):
				with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
					item_master.refuse_unapproved(
						frappe.get_doc({"doctype": doctype, "company": company(), **values})
					)

	def test_a_posting_made_before_the_item_master_is_still_reversed(self):
		"""An item held before this module is a draft: its postings are not refused when cancelled."""
		item = self.approved_item()
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
			}
		).insert()
		entry.submit()
		frappe.db.set_value("Item", item, APPROVAL_STATUS, DRAFT)  # as an item held before the module
		from meddevice_suite.master_data.versioning import item_in_use

		self.assertTrue(item_in_use(frappe.get_doc("Item", item)))  # in use by its stock, not approval
		from meddevice_suite.compliance.audit_reason import stage_reason

		stage_reason("Stock Entry", entry.name, "cancel", "Received in error")
		frappe.get_doc("Stock Entry", entry.name).cancel()
		self.assertEqual(frappe.db.get_value("Stock Entry", entry.name, "docstatus"), 2)

	def test_the_guard_is_wired_to_every_record_type(self):
		"""The sweep above calls the guard; this proves Frappe calls it on every save path."""
		events = frappe.get_hooks("doc_events")["*"]
		guard = "meddevice_suite.master_data.item_master.refuse_unapproved"
		for event in ("before_validate", "validate", "before_update_after_submit"):
			with self.subTest(event=event):
				handlers = events.get(event)
				self.assertIn(guard, handlers if isinstance(handlers, list) else [handlers])

	def test_a_record_saved_without_its_validation_is_still_refused(self):
		"""ERPNext saves some records with ignore_validate (Production Plan, bulk transactions)."""
		item = with_specification(new_item(FINISHED_GOOD)).name
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": CUSTOMER,
				"company": company(),
				"delivery_date": today(),
				"items": [{"item_code": item, "qty": 1, "rate": 10, "delivery_date": today()}],
			}
		)
		order.flags.ignore_validate = True
		with self.assertRaisesRegex(frappe.ValidationError, NOT_APPROVED):
			order.insert()

	def test_lots_and_serial_numbers_made_in_bulk_need_an_approved_item(self):
		"""ERPNext's file upload and its serial-number maker insert straight into the database."""
		overrides = frappe.get_hooks("override_whitelisted_methods")
		module = "erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle"
		for method in ("upload_csv_file", "create_serial_nos"):
			with self.subTest(method=method):
				self.assertEqual(
					overrides.get(f"{module}.{method}"), [f"meddevice_suite.master_data.item_master.{method}"]
				)
		draft = new_item(item_master.SPARE_PART, has_serial_no=1, serial_no_series="MDSSN-.####").name
		frappe.set_user(self.stores_user)
		with self.assertRaisesRegex(frappe.ValidationError, "no lots or serial numbers are made"):
			item_master.create_serial_nos(draft, "MDS-SN-0001")
		with self.assertRaisesRegex(frappe.ValidationError, "no lots or serial numbers are made"):
			item_master.upload_csv_file(draft, "/private/files/none.csv")
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Serial No", "MDS-SN-0001"))
		self.approve_as_qa(draft)
		frappe.set_user(self.stores_user)
		item_master.create_serial_nos(draft, f"MDS-SN-{frappe.generate_hash(length=6)}")  # approved: made

	def test_an_approved_item_is_used(self):
		item = self.approved_item()
		frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "t_warehouse": store("Stores"), "basic_rate": 1}],
			}
		).insert().submit()
		self.assertEqual(frappe.db.get_value("Bin", {"item_code": item}, "actual_qty"), 1)

	def test_setup_records_are_made_for_a_draft_item(self):
		"""What QA approves an item with is made before the approval: its specification, its price."""
		doc = new_item()
		with_specification(doc)
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": doc.name,
				"price_list": "Standard Buying",
				"price_list_rate": 2,
			}
		).insert()
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), DRAFT)


# --- URS-MST-004: type and lot tracking -------------------------------------------------------------


@verifies("URS-MST-004")
class TestTypeAndLots(ItemMasterTestCase):
	def test_every_item_states_its_type(self):
		with self.assertRaises(frappe.MandatoryError):
			new_item(**{ITEM_TYPE: None})
		with self.assertRaises(frappe.ValidationError):
			new_item(**{ITEM_TYPE: "Gadget"})

	def test_a_lot_tracked_type_is_approved_only_with_lots_shelf_life_and_storage(self):
		for item_type in (RAW_MATERIAL, PACKAGING, SUB_ASSEMBLY, FINISHED_GOOD):
			for values, missing in (
				(
					{
						"has_batch_no": 0,
						"create_new_batch": 0,
						"batch_number_series": None,
						"has_expiry_date": 0,
					},
					"lot tracking",
				),
				({"has_expiry_date": 0}, "shelf life"),
				({STORAGE_CONDITION: None}, "storage condition"),
			):
				with self.subTest(item_type=item_type, missing=missing):
					doc = new_item(item_type, **values)
					if item_type != SUB_ASSEMBLY:
						with_specification(doc)
					frappe.set_user(self.qa)
					with self.assertRaisesRegex(frappe.ValidationError, missing):
						sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")
					frappe.set_user("Administrator")

	def test_a_lot_with_an_expiry_has_a_shelf_life(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Shelf Life"):  # ERPNext's own check
			new_item(RAW_MATERIAL, shelf_life_in_days=0)

	def test_a_consumable_or_spare_part_is_approved_without_lots(self):
		for item_type in (item_master.CONSUMABLE, item_master.SPARE_PART, EQUIPMENT):
			with self.subTest(item_type=item_type):
				doc = new_item(item_type)
				self.approve_as_qa(doc.name)
				self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), APPROVED)

	def test_an_approved_item_keeps_its_type_even_under_a_change_request(self):
		item = self.approved_item(FINISHED_GOOD)
		change = approved_change(self.requester, self.qa, item, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", item)
		doc.update({ITEM_TYPE: item_master.CONSUMABLE, CHANGE_REQUEST: change})
		with self.assertRaisesRegex(frappe.ValidationError, "type of an approved item does not change"):
			doc.save()

	def test_reorder_levels_are_set_once_the_item_is_approved(self):
		reorder = [
			{
				"warehouse": store("Stores"),
				"warehouse_reorder_level": 5,
				"warehouse_reorder_qty": 10,
				"material_request_type": "Purchase",
			}
		]
		with self.assertRaisesRegex(frappe.ValidationError, "Reorder levels are set once QA has approved"):
			new_item(reorder_levels=reorder)
		item = frappe.get_doc("Item", self.approved_item())
		item.set("reorder_levels", reorder)
		item.save()  # not controlled: set freely once approved
		self.assertEqual(len(frappe.get_doc("Item", item.name).reorder_levels), 1)

	def test_automatic_reorder_waits_for_items_with_reorder_levels_to_be_approved(self):
		item = new_item()
		frappe.get_doc(
			{
				"doctype": "Item Reorder",
				"parent": item.name,
				"parenttype": "Item",
				"parentfield": "reorder_levels",
				"warehouse": store("Stores"),
				"warehouse_reorder_level": 5,
				"warehouse_reorder_qty": 10,
				"material_request_type": "Purchase",
			}
		).db_insert()  # as an item held before the module keeps its reorder levels
		settings = frappe.get_doc("Stock Settings")
		settings.auto_indent = 1
		with self.assertRaisesRegex(frappe.ValidationError, "Automatic reorder waits"):
			item_master.guard_auto_reorder(settings)

	def test_a_disabled_item_is_not_approved(self):
		doc = new_item(SUB_ASSEMBLY, disabled=1)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "is disabled"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")

	def test_an_approved_item_cannot_lose_its_lot_tracking(self):
		item = self.approved_item()
		change = approved_change(self.requester, self.qa, item, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", item)
		doc.update({STORAGE_CONDITION: "", CHANGE_REQUEST: change})
		with self.assertRaisesRegex(frappe.ValidationError, "cannot lose its storage condition"):
			doc.save()
		for values, lost in (
			({"has_batch_no": 0, "create_new_batch": 0}, "cannot lose lot tracking"),
			({"has_expiry_date": 0}, "cannot lose a shelf life"),
			({"is_stock_item": 0}, "cannot lose stock item"),
		):
			with self.subTest(lost=lost):
				doc = frappe.get_doc("Item", item)
				doc.update({**values, CHANGE_REQUEST: change})
				with self.assertRaisesRegex(frappe.ValidationError, lost):
					doc.save()

	def test_a_lot_tracked_item_is_a_stock_item(self):
		doc = new_item(SUB_ASSEMBLY, is_stock_item=0, has_batch_no=0, create_new_batch=0, has_expiry_date=0)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "stock item"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")


# --- URS-MST-005: finished goods' regulatory and label data ----------------------------------------


@verifies("URS-MST-005")
class TestFinishedGoodData(ItemMasterTestCase):
	def test_a_finished_good_is_approved_only_with_its_device_and_label_data(self):
		for values, missing in (
			({DEVICE_CLASS: None}, "device class"),
			({UNITS_PER_BOX: 0}, "pack sizes"),
			({BOXES_PER_CARTON: 0}, "pack sizes"),
			({UDI_DI: None}, "UDI device identifier"),
		):
			with self.subTest(missing=missing):
				doc = with_specification(new_item(FINISHED_GOOD, **values))
				frappe.set_user(self.qa)
				with self.assertRaisesRegex(frappe.ValidationError, missing):
					sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")
				frappe.set_user("Administrator")

	def test_sterile_or_not_is_stated_and_kept(self):
		doc = with_specification(new_item(FINISHED_GOOD, **{STERILE: None}))
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "whether it is supplied sterile"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")
		frappe.set_user("Administrator")
		item = self.approved_item(FINISHED_GOOD, **{STERILE: "No"})
		self.assertEqual(frappe.db.get_value("Item", item, STERILE), "No")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			frappe.client.set_value("Item", item, STERILE, "Yes")

	def test_a_gtin_8_and_its_14_digit_form_are_one_identifier(self):
		new_item(FINISHED_GOOD, **{UDI_DI: "96385074"})
		with self.assertRaisesRegex(frappe.ValidationError, "identifies .* already"):
			new_item(FINISHED_GOOD, **{UDI_DI: "00000096385074"})

	def test_only_ascii_digits_make_a_gtin(self):
		self.assertFalse(valid_gtin("٤٠٠٦٣٨١٣٣٣٩٣١"))  # Arabic-Indic digits
		self.assertFalse(valid_gtin("0000000000000"))  # all zeros
		self.assertFalse(valid_gtin("40063813339³1"))  # a superscript digit: refused, no server error

	def test_identifiers_entered_before_the_key_get_it_on_migrate(self):
		good = new_item(FINISHED_GOOD)
		frappe.db.set_value("Item", good.name, item_master.GTIN_KEY, None)
		bad = new_item(FINISHED_GOOD)
		frappe.db.set_value("Item", bad.name, {UDI_DI: "123", item_master.GTIN_KEY: None})
		twin = new_item(FINISHED_GOOD)
		frappe.db.set_value("Item", twin.name, {UDI_DI: good.get(UDI_DI), item_master.GTIN_KEY: None})
		item_master.apply_item_master_fields()  # no error, whatever is stored
		keys = [frappe.db.get_value("Item", name, item_master.GTIN_KEY) for name in (good.name, twin.name)]
		self.assertEqual(sorted(filter(None, keys)), [good.get(UDI_DI).zfill(14)])  # one of the two
		self.assertIsNone(frappe.db.get_value("Item", bad.name, item_master.GTIN_KEY))

	def test_one_gtin_written_at_another_length_is_the_same_identifier(self):
		first = new_item(FINISHED_GOOD, **{UDI_DI: "036000291452"})  # GTIN-12
		with self.assertRaisesRegex(frappe.ValidationError, "identifies .* already"):
			new_item(FINISHED_GOOD, **{UDI_DI: "0036000291452"})  # the same, as GTIN-13
		self.assertEqual(frappe.db.get_value("Item", first.name, item_master.GTIN_KEY), "00036000291452")

	def test_the_database_keeps_one_item_per_gtin(self):
		"""Two saves at the same moment both pass the check; the unique key refuses the second."""
		first = new_item(FINISHED_GOOD)
		second = new_item(FINISHED_GOOD)
		try:
			frappe.db.set_value("Item", second.name, item_master.GTIN_KEY, first.get(item_master.GTIN_KEY))
		except Exception as error:
			self.assertTrue(frappe.db.is_unique_key_violation(error))
		else:
			self.fail("the database took a second item with the same GTIN")

	def test_the_identifier_is_a_well_formed_gtin(self):
		self.assertTrue(valid_gtin("4006381333931"))  # a GTIN-13 with its check digit
		self.assertTrue(valid_gtin("96385074"))  # GTIN-8
		self.assertTrue(valid_gtin("036000291452"))  # GTIN-12
		self.assertTrue(valid_gtin("10614141000415"))  # GTIN-14
		for value in ("4006381333932", "400638133393", "40063813339A1", "123", ""):
			with self.subTest(value=value):
				self.assertFalse(valid_gtin(value))
				if value:
					with self.assertRaisesRegex(frappe.ValidationError, "is not a GTIN"):
						new_item(FINISHED_GOOD, **{UDI_DI: value})

	def test_no_two_items_carry_the_same_identifier(self):
		first = new_item(FINISHED_GOOD)
		with self.assertRaisesRegex(frappe.ValidationError, "identifies .* already"):
			new_item(FINISHED_GOOD, **{UDI_DI: first.get(UDI_DI)})

	def test_pack_sizes_are_counts(self):
		with self.assertRaisesRegex(frappe.ValidationError, "is a count"):
			new_item(FINISHED_GOOD, **{UNITS_PER_BOX: -1})

	def test_other_types_need_no_device_data(self):
		item = self.approved_item(RAW_MATERIAL)
		self.assertEqual(frappe.db.get_value("Item", item, APPROVAL_STATUS), APPROVED)


# --- URS-MST-006: inspection and specification ------------------------------------------------------


@verifies("URS-MST-006")
class TestInspectionBeforeApproval(ItemMasterTestCase):
	def test_a_purchased_material_needs_incoming_inspection_and_its_specification(self):
		for item_type in (RAW_MATERIAL, PACKAGING):
			with self.subTest(item_type=item_type, missing="specification"):
				doc = new_item(item_type)
				frappe.set_user(self.qa)
				with self.assertRaisesRegex(frappe.ValidationError, "incoming specification"):
					sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")
				frappe.set_user("Administrator")
			with self.subTest(item_type=item_type, missing="inspection"):
				doc = with_specification(new_item(item_type))
				doc.inspection_required_before_purchase = 0
				doc.save()
				frappe.set_user(self.qa)
				with self.assertRaisesRegex(frappe.ValidationError, "incoming inspection"):
					sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")
				frappe.set_user("Administrator")

	def test_the_specification_of_an_approved_item_stays_its_own(self):
		item = self.approved_item()
		specification = frappe.db.get_value("Quality Inspection Template", {"mds_item": item})
		change = approved_change(
			self.requester, self.qa, specification, "Quality Inspection Template", "Product or BOM"
		)
		frappe.set_user("Administrator")
		for values in ({"mds_stage": "In Process"}, {"mds_item": self.approved_item(SUB_ASSEMBLY)}):
			with self.subTest(values=values):
				template = frappe.get_doc("Quality Inspection Template", specification)
				template.update({**values, "mds_change_request": change})
				with self.assertRaisesRegex(frappe.ValidationError, "stays theirs"):
					template.save()

	def test_a_finished_good_needs_its_final_specification(self):
		doc = new_item(FINISHED_GOOD)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "final specification"):
			sign_action("Item", doc.name, "Approve", PASSWORD, "Checked")

	def test_a_sub_assembly_needs_no_specification_of_its_own(self):
		doc = new_item(SUB_ASSEMBLY)
		self.approve_as_qa(doc.name)
		self.assertEqual(frappe.db.get_value("Item", doc.name, APPROVAL_STATUS), APPROVED)

	def test_an_approved_item_keeps_its_inspection_and_specification(self):
		item = self.approved_item()
		with self.assertRaisesRegex(frappe.ValidationError, "cannot lose incoming inspection"):
			frappe.client.set_value("Item", item, "inspection_required_before_purchase", 0)
		specification = frappe.db.get_value("Quality Inspection Template", {"mds_item": item})
		with self.assertRaisesRegex(frappe.PermissionError, "is in use and is never deleted"):
			frappe.delete_doc("Quality Inspection Template", specification)
		template = frappe.get_doc("Quality Inspection Template", specification)
		template.mds_sample_size = 1
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			template.save()
		change = approved_change(self.requester, self.qa, item, "Item", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Item", item)
		doc.update({"inspection_required_before_purchase": 0, CHANGE_REQUEST: change})
		with self.assertRaisesRegex(frappe.ValidationError, "cannot lose incoming inspection"):
			doc.save()


# --- The desk --------------------------------------------------------------------------------------


@verifies("URS-MST-003")
class TestForm(ItemMasterTestCase):
	def test_the_form_shows_the_approval_or_what_stands_in_its_way(self):
		doc = new_item(FINISHED_GOOD, **{DEVICE_CLASS: None})
		doc.run_method("onload")
		shown = doc.get_onload().item_approval
		self.assertEqual(shown["status"], DRAFT)
		self.assertTrue(any("device class" in what for what in shown["missing"]))
		self.assertTrue(any("final specification" in what for what in shown["missing"]))
		item = frappe.get_doc("Item", self.approved_item())
		item.run_method("onload")
		self.assertEqual(item.get_onload().item_approval["status"], APPROVED)

	def test_qa_is_offered_the_approval(self):
		from meddevice_suite.compliance.signing import signing_actions

		frappe.set_user(self.qa)
		self.assertEqual(signing_actions("Item"), {"Approve": "Approved"})

	def test_a_purchase_order_through_the_workflow_names_approved_items_only(self):
		"""The journey's path: an approved item passes the purchase approval workflow untouched."""
		from meddevice_suite.master_data.test_supplier_approval import approved_for

		item = self.approved_item()
		approved_for(SUPPLIER, [item])
		frappe.set_user(signer("Purchase"))
		order = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": SUPPLIER,
				"company": company(),
				"schedule_date": add_days(today(), 7),
				"items": [{"item_code": item, "qty": 5, "rate": 1, "schedule_date": add_days(today(), 7)}],
			}
		).insert()
		self.assertEqual(apply_workflow(order, "Send for Approval").workflow_state, "Awaiting Approval")
