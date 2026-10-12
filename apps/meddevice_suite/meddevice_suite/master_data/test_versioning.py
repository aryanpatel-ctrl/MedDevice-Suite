import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from meddevice_suite.compliance.audit_reason import stage_reason
from meddevice_suite.lot_control.test_lot_status import company, lot_item, receive, store
from meddevice_suite.master_data.versioning import (
	CHANGE_REQUEST,
	CHANGED_UNDER,
	EFFECTIVE_FROM,
	MASTERS,
	VERSION,
)
from meddevice_suite.qa import verifies
from meddevice_suite.qms.change_control import approved_changes_for
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_stock import (
	approve_equipment_for_test,
	approve_for_test,
	approve_masters_for_test,
	licensed_for_test,
	submit_bom_for_test,
)

NINE_MASTERS = {
	"Item",
	"BOM",
	"Routing",
	"Operation",
	"Workstation",
	"Supplier",
	"Customer",
	"Warehouse",
	"Asset",
	"Quality Inspection Template",
}


def last_changes(doctype: str, name: str) -> list:
	"""The field changes of the record's latest audit event, as [field, old, new]."""
	event = frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": doctype, "reference_name": name, "action": "Updated"},
		pluck="name",
		order_by="event_at desc, creation desc",
		limit=1,
	)
	return (
		frappe.parse_json(frappe.get_doc("Audit Event", event[0]).changes).get("changed", []) if event else []
	)


def licensed(customer: frappe.model.document.Document) -> frappe.model.document.Document:
	"""A customer the tests sell to holds a verified licence (module 5)."""
	licensed_for_test(customer.name)
	return customer


def new_item_code() -> str:
	return f"MDS-VER-{frappe.generate_hash(length=6).upper()}"


class VersioningTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		cls.qa = signer("QA Manager")
		cls.requester = signer("Production Supervisor")

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def item_in_use(self) -> str:
		code = lot_item(new_item_code())
		receive(code, 5, store("Stores"))  # stock moved: the item is in use
		return code

	def change_request_for(self, doctype: str, name: str) -> str:
		change = approved_change(self.requester, self.qa, name, doctype, "Product or BOM")
		frappe.set_user("Administrator")
		return change

	def bom(self, product: str, material: str, default: int = 1, **values):
		approve_for_test(product, material)  # module 1: a bill names approved items only
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": default,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 2, "rate": 1}],
				**values,
			}
		).insert()
		return bom


@verifies("URS-MST-001")
class TestVersionedMasters(VersioningTestCase):
	def test_the_nine_masters_carry_a_version_and_its_effective_date(self):
		# Item, bill of materials, routing, operation and work centre, supplier, customer,
		# warehouse, equipment, inspection template.
		self.assertEqual(set(MASTERS), NINE_MASTERS)
		for doctype, master in MASTERS.items():
			meta = frappe.get_meta(doctype)
			for field in (VERSION, EFFECTIVE_FROM):
				self.assertTrue(meta.get_field(field), f"{doctype} has no {field}")
			# What a master controls exists on it in this ERPNext release.
			for field in master.fields:
				self.assertTrue(meta.get_field(field), f"{doctype}.{field}")
			for table, row_fields in master.tables.items():
				row_meta = frappe.get_meta(meta.get_field(table).options)
				for field in row_fields:
					self.assertTrue(row_meta.get_field(field), f"{doctype}.{table}.{field}")

	def test_a_new_master_is_version_1_in_effect_from_today(self):
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"MDS Version {new_item_code()}",
				"supplier_group": "Raw Material",
			}
		).insert()
		self.assertEqual((supplier.get(VERSION), str(supplier.get(EFFECTIVE_FROM))), (1, today()))

	def test_a_master_not_yet_in_use_changes_freely(self):
		item = frappe.get_doc("Item", lot_item(new_item_code()))
		item.shelf_life_in_days = 730
		item.save()
		self.assertEqual(frappe.db.get_value("Item", item.name, VERSION), 1)

	def test_a_master_in_use_changes_only_under_an_approved_change_request_that_lists_it(self):
		code = self.item_in_use()
		item = frappe.get_doc("Item", code)
		item.shelf_life_in_days = 730
		with self.assertRaisesRegex(frappe.ValidationError, "is in use: name the approved change request"):
			item.save()
		elsewhere = self.change_request_for("Item", lot_item(new_item_code()))
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 730, CHANGE_REQUEST: elsewhere})
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			item.save()
		change = self.change_request_for("Item", code)
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		item.save()
		stored = frappe.db.get_value(
			"Item", code, ["shelf_life_in_days", VERSION, EFFECTIVE_FROM, CHANGED_UNDER, CHANGE_REQUEST]
		)
		self.assertEqual(
			(stored[0], stored[1], str(stored[2]), stored[3], stored[4]), (730, 2, today(), change, None)
		)
		# The audit trail keeps the value the change replaced, and the request it was made under.
		self.assertIn(["shelf_life_in_days", 365, 730], last_changes("Item", code))
		self.assertIn([CHANGED_UNDER, None, change], last_changes("Item", code))

	def test_each_change_names_its_change_request_again(self):
		code = self.item_in_use()
		change = self.change_request_for("Item", code)
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		item.save()
		# The next change, by anyone, does not ride on the request named for the last one.
		item = frappe.get_doc("Item", code)
		item.shelf_life_in_days = 900
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			item.save()

	@verifies("URS-QMS-033")
	def test_a_request_changes_each_master_it_lists_once(self):
		"""Found in the foundation review of 2026-10-12: named again, the same approved request
		authorised a second, unrelated change."""
		code = self.item_in_use()
		change = self.change_request_for("Item", code)
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		item.save()

		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 900, CHANGE_REQUEST: change})
		with self.assertRaisesRegex(frappe.ValidationError, f"{change} has already changed Item {code}"):
			item.save()
		self.assertEqual(frappe.db.get_value("Item", code, ["shelf_life_in_days", VERSION]), (730, 2))
		row = frappe.get_doc("Change Request", change).affected_records[0]
		self.assertEqual((row.change_record_type, row.change_record), ("Item", code))

	def test_the_version_record_is_set_only_by_a_controlled_change(self):
		code = self.item_in_use()
		change = self.change_request_for("Item", code)
		for field, value in ((EFFECTIVE_FROM, "2020-01-01"), (CHANGED_UNDER, change)):
			with self.subTest(field):
				item = frappe.get_doc("Item", code)
				item.set(field, value)
				with self.assertRaises(frappe.PermissionError):
					item.save()
		# Nor by saving again the record a controlled change was just saved on.
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		item.save()
		item.set(VERSION, 9)
		with self.assertRaises(frappe.PermissionError):
			item.save()

	def test_what_a_master_does_not_control_changes_freely(self):
		code = self.item_in_use()
		item = frappe.get_doc("Item", code)
		item.description = "Medical grade silicone, 40 Shore A (note)"
		item.save()
		self.assertEqual(frappe.db.get_value("Item", code, VERSION), 1)

	def test_the_version_is_set_only_by_a_controlled_change(self):
		item = frappe.get_doc("Item", lot_item(new_item_code()))
		item.set(VERSION, 7)
		with self.assertRaises(frappe.PermissionError):
			item.save()

	def test_a_submitted_bill_of_materials_changes_which_is_in_force_under_control(self):
		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		approve_for_test(product, material)  # module 1: a bill names approved items only
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 2, "rate": 1}],
			}
		).insert()
		submit_bom_for_test(bom)
		bom = frappe.get_doc("BOM", bom.name)
		bom.is_active = 0
		bom.is_default = 0
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			bom.save()
		change = self.change_request_for("BOM", bom.name)
		bom = frappe.get_doc("BOM", bom.name)
		bom.update({"is_active": 0, "is_default": 0, CHANGE_REQUEST: change})
		# A submitted record is changed with a reason (URS-CMP-011), as the desk asks for it.
		stage_reason("BOM", bom.name, "update_after_submit", "Replaced by the new revision")
		bom.save()
		self.assertEqual(frappe.db.get_value("BOM", bom.name, [VERSION, "is_active"]), (2, 0))

	def test_a_new_default_bill_of_materials_replaces_the_one_in_force_only_under_control(self):
		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		in_force = self.bom(product, material)
		submit_bom_for_test(in_force)
		replacement = self.bom(product, material)
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			submit_bom_for_test(replacement)
		elsewhere = self.change_request_for("BOM", replacement.name)
		replacement = frappe.get_doc("BOM", replacement.name)
		replacement.set(CHANGE_REQUEST, elsewhere)
		replacement.save()
		with self.assertRaisesRegex(frappe.ValidationError, "does not list"):
			submit_bom_for_test(replacement)
		change = self.change_request_for("BOM", in_force.name)
		replacement = frappe.get_doc("BOM", replacement.name)
		replacement.set(CHANGE_REQUEST, change)
		replacement.save()
		submit_bom_for_test(replacement)
		# ERPNext switched the old default off straight in the database; the change is recorded.
		self.assertEqual(
			frappe.db.get_value("BOM", in_force.name, ["is_default", VERSION, CHANGED_UNDER]), (0, 2, change)
		)
		self.assertIn(["is_default", 1, 0], last_changes("BOM", in_force.name))

	def test_a_bill_of_materials_is_cancelled_only_under_control(self):
		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		bom = self.bom(product, material)
		submit_bom_for_test(bom)
		stage_reason("BOM", bom.name, "cancel", "Withdrawn")
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			frappe.get_doc("BOM", bom.name).cancel()
		change = self.change_request_for("BOM", bom.name)
		bom = frappe.get_doc("BOM", bom.name)
		bom.set(CHANGE_REQUEST, change)
		stage_reason("BOM", bom.name, "update_after_submit", "Naming the change request")
		bom.save()
		stage_reason("BOM", bom.name, "cancel", "Withdrawn under the change request")
		frappe.get_doc("BOM", bom.name).cancel()
		self.assertEqual(frappe.db.get_value("BOM", bom.name, ["docstatus", CHANGED_UNDER]), (2, change))

		# The request still lists the cancelled bill of materials, and is still acted on; a record it
		# names that does not exist is still refused.
		from meddevice_suite.qms import change_control

		request = frappe.get_doc("Change Request", change)
		self.assertEqual(
			[(row.reference_name, row.get(change_control.CHANGE_RECORD)) for row in request.affected_records],
			[(bom.name, bom.name)],
		)
		request.flags.audit_reason = "A later act on the request"
		change_control.save(request)
		request.affected_records[0].reference_name = "BOM-DOES-NOT-EXIST"
		request.flags.audit_reason = "A record that does not exist"
		with self.assertRaises(frappe.LinkValidationError):
			change_control.save(request)

	def test_a_master_in_use_keeps_its_name(self):
		code = self.item_in_use()
		with self.assertRaises(frappe.PermissionError):
			frappe.rename_doc("Item", code, f"{code}-RENAMED")
		free = lot_item(new_item_code())
		frappe.rename_doc("Item", free, f"{free}-R")  # not yet in use: renamed
		self.assertTrue(frappe.db.exists("Item", f"{free}-R"))

	def template_with_variant_in_use(self) -> tuple[str, str]:
		from erpnext.controllers.item_variant import create_variant

		if not frappe.db.exists("Item Attribute", "MDS Size"):
			frappe.get_doc(
				{
					"doctype": "Item Attribute",
					"attribute_name": "MDS Size",
					"item_attribute_values": [
						{"attribute_value": "14 Fr", "abbr": "14"},
						{"attribute_value": "16 Fr", "abbr": "16"},
					],
				}
			).insert()
		template_code = new_item_code()
		lot_item(template_code)
		template = frappe.get_doc("Item", template_code)
		template.update({"has_variants": 1, "attributes": [{"attribute": "MDS Size"}]})
		template.save()
		variant = create_variant(template_code, {"MDS Size": "16 Fr"})
		variant.insert()
		receive(variant.name, 5, store("Stores"))  # the variant is in use, so the template is
		return template_code, variant.name

	def test_a_variant_follows_its_templates_controlled_change(self):
		template_code, variant_code = self.template_with_variant_in_use()
		template = frappe.get_doc("Item", template_code)
		template.shelf_life_in_days = 730
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			template.save()
		change = self.change_request_for("Item", template_code)
		template = frappe.get_doc("Item", template_code)
		template.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		template.save()  # ERPNext copies the change to the variant
		self.assertEqual(
			frappe.db.get_value("Item", variant_code, ["shelf_life_in_days", VERSION, CHANGED_UNDER]),
			(730, 2, change),
		)

	def test_a_request_left_on_the_record_does_not_cover_a_later_change(self):
		code = lot_item(new_item_code())
		change = self.change_request_for("Item", code)
		item = frappe.get_doc("Item", code)
		item.set(CHANGE_REQUEST, change)  # named while the item is not yet in use
		item.save()
		self.assertIsNone(frappe.db.get_value("Item", code, CHANGE_REQUEST))  # not kept for later
		receive(code, 5, store("Stores"))
		item = frappe.get_doc("Item", code)
		item.shelf_life_in_days = 730
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			item.save()

	def test_an_equipment_record_in_use_is_cancelled_only_under_control(self):
		from meddevice_suite.pilot.inspection import ensure_instrument

		asset = frappe.get_doc(
			"Asset", ensure_instrument(company(), approve_for_test, approve_equipment_for_test)
		)
		copy = frappe.copy_doc(asset)
		copy.asset_name = f"Caliper {new_item_code()}"
		copy.mds_equipment_tag = f"QC-CAL-{new_item_code()}"  # another piece of equipment, its own tag
		copy.insert()
		copy.submit()
		stage_reason("Asset", copy.name, "cancel", "Entered twice")
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			frappe.get_doc("Asset", copy.name).cancel()
		change = self.change_request_for("Asset", copy.name)
		asset = frappe.get_doc("Asset", copy.name)
		asset.set(CHANGE_REQUEST, change)
		stage_reason("Asset", asset.name, "update_after_submit", "Naming the change request")
		asset.save()
		stage_reason("Asset", asset.name, "cancel", "Entered twice")
		frappe.get_doc("Asset", asset.name).cancel()
		self.assertEqual(frappe.db.get_value("Asset", asset.name, ["docstatus", CHANGED_UNDER]), (2, change))

	def test_a_routings_operations_are_controlled(self):
		suffix = new_item_code()
		workstation = frappe.get_doc(
			{"doctype": "Workstation", "workstation_name": f"MDS WS {suffix}"}
		).insert()
		operation = frappe.get_doc(
			{"doctype": "Operation", "name": f"MDS OP {suffix}", "workstation": workstation.name}
		).insert()
		routing = frappe.get_doc(
			{
				"doctype": "Routing",
				"routing_name": f"MDS Line {suffix}",
				"operations": [
					{"operation": operation.name, "workstation": workstation.name, "time_in_mins": 30}
				],
			}
		).insert()
		self.assertFalse(MASTERS["Routing"].in_use(routing))
		approve_masters_for_test(
			("Workstation", workstation.name), ("Operation", operation.name), ("Routing", routing.name)
		)
		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		submit_bom_for_test(self.bom(product, material, with_operations=1, routing=routing.name))
		routing = frappe.get_doc("Routing", routing.name)
		self.assertTrue(MASTERS["Routing"].in_use(routing))
		self.assertTrue(MASTERS["Operation"].in_use(operation))
		self.assertTrue(MASTERS["Workstation"].in_use(workstation))
		routing.operations[0].time_in_mins = 45
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			routing.save()

	def test_each_masters_in_use_check_runs_on_a_record_not_yet_used(self):
		item = frappe.get_doc("Item", lot_item(new_item_code()))
		fresh = {
			"Item": item,
			"Supplier": frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": f"MDS Fresh {new_item_code()}",
					"supplier_group": "Raw Material",
				}
			).insert(),
			"Customer": licensed(
				frappe.get_doc(
					{
						"doctype": "Customer",
						"customer_name": f"MDS Fresh {new_item_code()}",
						"customer_group": "Commercial",
						"territory": "India",
					}
				).insert()
			),
			"Workstation": frappe.get_doc(
				{"doctype": "Workstation", "workstation_name": f"MDS WS {new_item_code()}"}
			).insert(),
		}
		fresh["Operation"] = frappe.get_doc(
			{
				"doctype": "Operation",
				"name": f"MDS OP {new_item_code()}",
				"workstation": fresh["Workstation"].name,
			}
		).insert()
		for doctype, doc in fresh.items():
			with self.subTest(doctype):
				self.assertFalse(MASTERS[doctype].in_use(doc))
		# ... and in use once a submitted record depends on it.
		from meddevice_suite.master_data.test_supplier_approval import approved_for

		approve_for_test(item.name)  # module 1: received only once approved

		approved_for(fresh["Supplier"].name, [item.name])
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": fresh["Supplier"].name,
				"company": company(),
				"items": [{"item_code": item.name, "qty": 1, "rate": 5}],
			}
		).insert()
		receipt.submit()
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": fresh["Customer"].name,
				"company": company(),
				"delivery_date": today(),
				"items": [{"item_code": item.name, "qty": 1, "rate": 50, "delivery_date": today()}],
			}
		).insert()
		order.submit()
		for doctype in ("Supplier", "Customer"):
			with self.subTest(f"{doctype} in use"):
				self.assertTrue(MASTERS[doctype].in_use(fresh[doctype]))

	def variant_disabled_under_its_own_request(self) -> tuple[str, str]:
		template_code, variant = self.template_with_variant_in_use()
		disable = self.change_request_for("Item", variant)
		doc = frappe.get_doc("Item", variant)
		doc.update({"disabled": 1, CHANGE_REQUEST: disable})
		doc.save()
		return template_code, variant

	def test_a_template_saves_after_a_variant_was_changed_on_its_own(self):
		template_code, variant = self.variant_disabled_under_its_own_request()
		template = frappe.get_doc("Item", template_code)
		template.description = "Foley catheter, silicone (note)"
		template.save()  # ERPNext would put the variant's own decision back; it is kept
		self.assertEqual(frappe.db.get_value("Item", variant, "disabled"), 1)

	def test_a_variants_own_decision_survives_a_controlled_change_of_its_template(self):
		template_code, variant = self.variant_disabled_under_its_own_request()
		change = self.change_request_for("Item", template_code)
		template = frappe.get_doc("Item", template_code)
		template.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		template.save()
		# The change reaches the variant; its own decision, not listed in the request, stays.
		self.assertEqual(frappe.db.get_value("Item", variant, ["shelf_life_in_days", "disabled"]), (730, 1))

	def test_a_templates_table_change_reaches_its_variants_in_use(self):
		template_code, variant = self.template_with_variant_in_use()
		if not frappe.db.exists("UOM", "Box"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Box"}).insert()
		template = frappe.get_doc("Item", template_code)
		template.append("uoms", {"uom": "Box", "conversion_factor": 10})
		# A unit conversion sets the quantity of every order and receipt in that unit: controlled
		# (module 1, decided 2026-10-11).
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			template.save()
		template = frappe.get_doc("Item", template_code)
		template.append("uoms", {"uom": "Box", "conversion_factor": 10})
		template.set(CHANGE_REQUEST, self.change_request_for("Item", template_code))
		template.save()
		self.assertIn(
			("Box", 10.0),
			[(row.uom, row.conversion_factor) for row in frappe.get_doc("Item", variant).uoms],
		)

	def test_a_variant_edited_by_hand_does_not_ride_on_its_templates_request(self):
		template_code, variant = self.template_with_variant_in_use()
		change = self.change_request_for("Item", template_code)
		template = frappe.get_doc("Item", template_code)
		template.update({"shelf_life_in_days": 730, CHANGE_REQUEST: change})
		template.save()
		# QA disables the variant under a request of its own ...
		disable = self.change_request_for("Item", variant)
		doc = frappe.get_doc("Item", variant)
		doc.update({"disabled": 1, CHANGE_REQUEST: disable})
		doc.save()
		# ... and enabling it again, though it matches the template, is a change of its own.
		doc = frappe.get_doc("Item", variant)
		doc.disabled = 0
		with self.assertRaisesRegex(frappe.ValidationError, "name the approved change request"):
			doc.save()

	def test_a_warehouse_with_stock_movements_is_in_use(self):
		self.item_in_use()
		warehouse = frappe.get_doc("Warehouse", store("Stores"))
		self.assertTrue(MASTERS["Warehouse"].in_use(warehouse))

	def test_the_form_offers_the_approved_change_requests_that_list_the_record(self):
		code = self.item_in_use()
		change = self.change_request_for("Item", code)
		offered = [
			name
			for name, _title in approved_changes_for(
				"Change Request", "", "name", 0, 20, {"reference_doctype": "Item", "reference_name": code}
			)
		]
		self.assertEqual(offered, [change])


def draft_change(requester: str, doctype: str, name: str) -> str:
	"""A change request listing the record, raised but not approved."""
	previous = frappe.session.user
	frappe.set_user(requester)
	try:
		return (
			frappe.get_doc(
				{
					"doctype": "Change Request",
					"title": f"Change {name}",
					"change_type": "Product or BOM",
					"description": "Proposed change",
					"reason": "Improvement",
					"affected_records": [{"reference_doctype": doctype, "reference_name": name}],
					"actions": [
						{"description": f"Change {name}", "action_owner": requester, "due_date": today()}
					],
				}
			)
			.insert()
			.name
		)
	finally:
		frappe.set_user(previous)


@verifies("URS-MST-001")
class TestRequirementMatrix(VersioningTestCase):
	"""The acceptance matrix of URS-MST-001 (2026-10-09): every master, every way in."""

	def masters_in_use(self) -> dict[str, tuple[str, dict]]:
		"""One record of each master put in use, with a change to what it controls."""
		from meddevice_suite.master_data.test_supplier_approval import approved_for

		suffix = new_item_code()
		item = self.item_in_use()
		workstation = frappe.get_doc(
			{"doctype": "Workstation", "workstation_name": f"MDS WS {suffix}"}
		).insert()
		operation = frappe.get_doc(
			{"doctype": "Operation", "name": f"MDS OP {suffix}", "workstation": workstation.name}
		).insert()
		routing = frappe.get_doc(
			{
				"doctype": "Routing",
				"routing_name": f"MDS Line {suffix}",
				"operations": [
					{"operation": operation.name, "workstation": workstation.name, "time_in_mins": 30}
				],
			}
		).insert()
		approve_masters_for_test(
			("Workstation", workstation.name), ("Operation", operation.name), ("Routing", routing.name)
		)
		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		submit_bom_for_test(self.bom(product, material, with_operations=1, routing=routing.name))
		supplier = frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": f"MDS In Use {suffix}", "supplier_group": "Raw Material"}
		).insert()
		approved_for(supplier.name, [item])
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": supplier.name,
				"company": company(),
				"items": [{"item_code": item, "qty": 1, "rate": 5}],
			}
		).insert()
		receipt.submit()
		customer = licensed(
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": f"MDS In Use {suffix}",
					"customer_group": "Commercial",
					"territory": "India",
				}
			).insert()
		)
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": customer.name,
				"company": company(),
				"delivery_date": today(),
				"items": [{"item_code": item, "qty": 1, "rate": 50, "delivery_date": today()}],
			}
		).insert()
		order.submit()
		if not frappe.db.exists("Warehouse Type", "MDS Cold Room"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "MDS Cold Room"}).insert()
		return {
			"Item": (item, {"shelf_life_in_days": 900}),
			"Routing": (routing.name, None),  # its operations' time; set below
			"Operation": (operation.name, {"batch_size": 5}),
			"Workstation": (workstation.name, {"production_capacity": 3}),
			"Supplier": (
				supplier.name,
				{
					"supplier_group": frappe.get_all(
						"Supplier Group",
						filters={"name": ["!=", "Raw Material"], "is_group": 0},
						limit=1,
						pluck="name",
					)[0]
				},
			),
			"Customer": (
				customer.name,
				{
					"customer_group": frappe.get_all(
						"Customer Group",
						filters={"name": ["!=", "Commercial"], "is_group": 0},
						limit=1,
						pluck="name",
					)[0]
				},
			),
			"Warehouse": (store("Stores"), {"warehouse_type": "MDS Cold Room"}),
		}

	def changed(self, doctype: str, name: str, values: dict | None):
		doc = frappe.get_doc(doctype, name)
		if values is None:  # a routing: the time of its first operation
			doc.operations[0].time_in_mins = (doc.operations[0].time_in_mins or 0) + 5
		else:
			doc.update(values)
		return doc

	def test_every_master_in_use_changes_only_under_an_approved_request_and_gets_a_new_version(self):
		for doctype, (name, values) in self.masters_in_use().items():
			with self.subTest(doctype):
				version = frappe.db.get_value(doctype, name, VERSION)
				with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
					self.changed(doctype, name, values).save()
				change = self.change_request_for(doctype, name)
				doc = self.changed(doctype, name, values)
				doc.set(CHANGE_REQUEST, change)
				doc.save()
				self.assertEqual(
					frappe.db.get_value(doctype, name, [VERSION, CHANGED_UNDER]), (version + 1, change)
				)

	def test_a_request_not_yet_approved_does_not_allow_a_change(self):
		code = self.item_in_use()
		draft = draft_change(self.requester, "Item", code)
		item = frappe.get_doc("Item", code)
		item.update({"shelf_life_in_days": 900, CHANGE_REQUEST: draft})
		with self.assertRaisesRegex(frappe.ValidationError, "not approved for implementation"):
			item.save()

	def test_the_api_changes_a_master_under_the_same_rule(self):
		code = self.item_in_use()
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			frappe.client.set_value("Item", code, "shelf_life_in_days", 900)
		self.assertEqual(frappe.db.get_value("Item", code, "shelf_life_in_days"), 365)

	def test_the_bom_update_tool_does_not_replace_a_bill_of_materials(self):
		from meddevice_suite.master_data.versioning import refuse_bom_replace

		with self.assertRaises(frappe.PermissionError):
			refuse_bom_replace(frappe._dict(doctype="BOM Update Log", update_type="Replace BOM"))
		refuse_bom_replace(frappe._dict(doctype="BOM Update Log", update_type="Update Cost"))  # costs: fine


@verifies("URS-MST-001")
class TestWiredIn(VersioningTestCase):
	"""The rules are enforced on ERPNext's own paths, not only when called."""

	def test_the_bom_update_tool_is_refused_its_replace(self):
		from erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool import enqueue_replace_bom

		product, material = lot_item(new_item_code(), "Products"), lot_item(new_item_code())
		current = self.bom(product, material)
		submit_bom_for_test(current)
		other = self.bom(product, material, default=0)
		submit_bom_for_test(other)
		with self.assertRaises(frappe.PermissionError):
			enqueue_replace_bom({"current_bom": current.name, "new_bom": other.name})

	def test_a_submitted_assets_name_changes_only_under_a_request(self):
		from meddevice_suite.pilot.inspection import ensure_instrument

		copy = frappe.copy_doc(
			frappe.get_doc(
				"Asset", ensure_instrument(company(), approve_for_test, approve_equipment_for_test)
			)
		)
		copy.asset_name = f"Caliper {new_item_code()}"
		copy.mds_equipment_tag = f"QC-CAL-{new_item_code()}"  # another piece of equipment, its own tag
		copy.insert()
		copy.submit()
		asset = frappe.get_doc("Asset", copy.name)
		asset.asset_name = "Caliper renamed"
		stage_reason("Asset", asset.name, "update_after_submit", "Renamed")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			asset.save()
		change = self.change_request_for("Asset", copy.name)
		asset = frappe.get_doc("Asset", copy.name)
		asset.update({"asset_name": "Caliper renamed", CHANGE_REQUEST: change})
		stage_reason("Asset", asset.name, "update_after_submit", "Renamed under the change request")
		asset.save()
		self.assertEqual(frappe.db.get_value("Asset", asset.name, [VERSION, CHANGED_UNDER]), (2, change))
