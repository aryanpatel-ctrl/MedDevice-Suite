"""Routings, operations and workstations (module 3, URS-MST-012): tests derived from the
requirement's clauses."""

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.esign import SigningRefused, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.lot_control.test_lot_status import company, lot_item, store
from meddevice_suite.master_data import master_approval, routing
from meddevice_suite.master_data.master_approval import APPROVAL_STATUS, APPROVED, DRAFT
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.test_stock import (
	approve_equipment_for_test,
	approve_for_test,
	submit_bom_for_test,
	work_instruction_for_test,
)

REFUSED = (frappe.ValidationError, frappe.PermissionError)


def document(document_type: str = "Work Instruction", status: str = "Effective") -> str:
	"""A controlled document of a type and status, as document control leaves it (module 30)."""
	doc = frappe.get_doc(
		{
			"doctype": "Controlled Document",
			"title": f"Document {frappe.generate_hash(length=6)}",
			"document_type": document_type,
			"department": "PRD",
			"process_owner": "Administrator",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	frappe.db.set_value("Controlled Document", doc.name, "status", status)
	return doc.name


class RoutingTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.qa = signer("QA Manager")
		cls.production = signer("Production Supervisor")

	def tearDown(self):
		frappe.set_user("Administrator")

	def suffix(self) -> str:
		return frappe.generate_hash(length=6).upper()

	def workstation(self) -> str:
		frappe.set_user(self.production)
		try:
			return (
				frappe.get_doc({"doctype": "Workstation", "workstation_name": f"MDS RT WS {self.suffix()}"})
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def operation(self, workstation: str | None = None, **values) -> str:
		frappe.set_user(self.production)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": "Operation",
						"name": f"MDS RT OP {self.suffix()}",
						"workstation": workstation,
						routing.WORK_INSTRUCTION: work_instruction_for_test(),
						routing.IN_PROCESS_CHECK: "Yes",
						**values,
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def routing(self, steps: list[tuple[str, str]]) -> str:
		frappe.set_user(self.production)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": "Routing",
						"routing_name": f"MDS RT Line {self.suffix()}",
						"operations": [
							{"operation": op, "workstation": ws, "time_in_mins": 10, "sequence_id": i}
							for i, (op, ws) in enumerate(steps, 1)
						],
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def approve(self, doctype: str, name: str, reason: str = "Checked against the process flow") -> None:
		frappe.set_user(self.qa)
		try:
			sign_action(doctype, name, "Approve", PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def approved_line(self) -> tuple[str, str, str]:
		ws = self.workstation()
		self.approve("Workstation", ws)
		op = self.operation(ws)
		self.approve("Operation", op)
		line = self.routing([(op, ws)])
		self.approve("Routing", line)
		return ws, op, line

	def assert_not_approvable(self, doctype: str, name: str, lacks: str) -> None:
		frappe.set_user(self.qa)
		try:
			with self.assertRaisesRegex(
				frappe.ValidationError, f"cannot be approved yet; it lacks .*{lacks}"
			):
				sign_action(doctype, name, "Approve", PASSWORD, "Checked")
		finally:
			frappe.set_user("Administrator")


@verifies("URS-MST-012")
class TestOperation(RoutingTestCase):
	def test_qa_approves_an_operation_with_a_signature(self):
		op = self.operation()
		self.assertEqual(frappe.db.get_value("Operation", op, APPROVAL_STATUS), DRAFT)
		self.approve("Operation", op, "Work instruction and in-process check in place")
		self.assertEqual(
			frappe.db.get_value("Operation", op, [APPROVAL_STATUS, master_approval.APPROVED_BY]),
			(APPROVED, self.qa),
		)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Operation", "reference_name": op}
		)
		self.assertEqual((signature.meaning, signature.signer), ("Approved", self.qa))
		self.assertTrue(verify_signatures("Operation", op))
		self.assertTrue(is_unchanged_since(signature))

	def test_an_operation_needs_its_effective_work_instruction_and_its_in_process_check(self):
		for lacks, values in (
			("its work instruction", {routing.WORK_INSTRUCTION: None}),
			("a work instruction as its document", {routing.WORK_INSTRUCTION: document("SOP")}),
			("its work instruction in effect", {routing.WORK_INSTRUCTION: document(status="Draft")}),
			("whether it carries an in-process check", {routing.IN_PROCESS_CHECK: None}),
		):
			with self.subTest(lacks=lacks):
				self.assert_not_approvable("Operation", self.operation(**values), lacks)

	def test_an_operations_own_workstation_is_approved_first(self):
		op = self.operation(self.workstation())
		self.assert_not_approvable("Operation", op, "workstation .* approved")

	def test_only_qa_approves_with_password_and_reason_and_once(self):
		op = self.operation()
		frappe.set_user(self.production)
		with self.assertRaisesRegex(frappe.PermissionError, "Only QA approves"):
			sign_action("Operation", op, "Approve", PASSWORD, "Mine")
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action("Operation", op, "Approve", "not the password", "Checked")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action("Operation", op, "Approve", PASSWORD, " ")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		self.assertFalse(master_approval.approved("Operation", op))
		self.approve("Operation", op)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "approved already"):
			sign_action("Operation", op, "Approve", PASSWORD, "Again")

	def test_the_approval_is_given_only_by_the_signature(self):
		op = self.operation()
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		doc.set(APPROVAL_STATUS, APPROVED)
		with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signed approval"):
			doc.save()
		made = self.operation(**{APPROVAL_STATUS: APPROVED})
		self.assertEqual(frappe.db.get_value("Operation", made, APPROVAL_STATUS), DRAFT)  # cleared as made

	def test_what_qa_approved_changes_only_under_change_control(self):
		op = self.operation()
		self.approve("Operation", op)
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		doc.set(routing.IN_PROCESS_CHECK, "No")
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()

	def test_an_operation_held_before_module_3_gets_its_new_fields_once(self):
		"""In use (a submitted job card names it) but not yet approved: its work instruction and
		in-process check are filled in once without a change request, as a first issue."""
		from unittest.mock import patch

		op = self.operation(**{routing.WORK_INSTRUCTION: None, routing.IN_PROCESS_CHECK: None})
		frappe.set_user(self.production)
		from meddevice_suite.master_data.versioning import MASTERS

		in_use = MASTERS["Operation"].__class__(
			fields=MASTERS["Operation"].fields, tables=MASTERS["Operation"].tables, in_use=lambda doc: True
		)
		with patch.dict(MASTERS, {"Operation": in_use}):  # in use, as a job card before module 3 left it
			doc = frappe.get_doc("Operation", op)
			doc.update(
				{routing.WORK_INSTRUCTION: work_instruction_for_test(), routing.IN_PROCESS_CHECK: "Yes"}
			)
			doc.save()
			doc = frappe.get_doc("Operation", op)
			doc.set(routing.IN_PROCESS_CHECK, "No")
			with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
				doc.save()

	def test_an_approved_operation_changes_under_an_approved_change_request(self):
		from meddevice_suite.master_data.versioning import CHANGE_REQUEST, CHANGED_UNDER
		from meddevice_suite.qms.test_change_control import approved_change

		op = self.operation()
		self.approve("Operation", op)
		change = approved_change(self.production, self.qa, op, "Operation", "Process or Routing")
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		doc.set(routing.IN_PROCESS_CHECK, "No")
		doc.set(CHANGE_REQUEST, change)
		doc.save()
		self.assertEqual(
			frappe.db.get_value("Operation", op, [routing.IN_PROCESS_CHECK, CHANGED_UNDER]), ("No", change)
		)
		# Even under a request, an approved operation keeps what its approval needed.
		change = approved_change(self.production, self.qa, op, "Operation", "Process or Routing")
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		doc.set(routing.WORK_INSTRUCTION, None)
		doc.set(CHANGE_REQUEST, change)
		with self.assertRaisesRegex(frappe.ValidationError, "keeps what its approval needed"):
			doc.save()

	def test_sub_operations_are_approved_and_controlled_with_their_operation(self):
		sub = self.operation()
		op = self.operation(sub_operations=[{"operation": sub, "time_in_mins": 5}])
		self.assert_not_approvable("Operation", op, f"sub-operation {sub} approved")
		self.approve("Operation", sub)
		self.approve("Operation", op)
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		another = self.operation()
		self.approve("Operation", another)
		frappe.set_user(self.production)
		doc = frappe.get_doc("Operation", op)
		doc.append("sub_operations", {"operation": another, "time_in_mins": 5})  # a step added
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			doc.save()

	def test_the_signature_binds_what_qa_approved(self):
		op = self.operation()
		self.approve("Operation", op)
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Operation", "reference_name": op}
		)
		frappe.db.set_value("Operation", op, "description", "A note for the floor")
		self.assertTrue(is_unchanged_since(signature))
		frappe.db.set_value("Operation", op, routing.WORK_INSTRUCTION, document())  # past the rules
		self.assertFalse(is_unchanged_since(signature))

	def test_the_form_says_what_approval_needs(self):
		op = self.operation(**{routing.IN_PROCESS_CHECK: None})
		doc = frappe.get_doc("Operation", op)
		doc.set("__onload", frappe._dict())
		doc.run_method("onload")
		self.assertIn("whether it carries an in-process check", doc.get_onload().master_approval["missing"])


@verifies("URS-MST-012")
class TestWorkstationAndRouting(RoutingTestCase):
	def equipment_at(self, workstation: str) -> str:
		from meddevice_suite.pilot.inspection import ensure_instrument

		instrument = frappe.get_doc(
			"Asset", ensure_instrument(company(), approve_for_test, approve_equipment_for_test)
		)
		copy = frappe.copy_doc(instrument)
		copy.update(
			{
				"asset_name": f"Press {self.suffix()}",
				"mds_equipment_tag": f"PR-{self.suffix()}",
				"mds_workstation": workstation,
				"docstatus": 0,
			}
		)
		return copy.insert().name

	def test_a_workstation_needs_its_equipment_approved(self):
		ws = self.workstation()
		asset = self.equipment_at(ws)
		self.assert_not_approvable("Workstation", ws, f"equipment {asset} approved")
		approve_equipment_for_test(asset)
		self.approve("Workstation", ws)
		self.assertTrue(master_approval.approved("Workstation", ws))

	def test_a_routing_without_operations_is_not_approved(self):
		ws = self.workstation()
		op = self.operation(ws)
		line = self.routing([(op, ws)])
		frappe.db.delete(
			"BOM Operation", {"parenttype": "Routing", "parent": line}
		)  # as an import might leave it
		self.assert_not_approvable("Routing", line, "its operations")

	def test_a_routing_needs_its_operations_and_workstations_approved(self):
		ws = self.workstation()
		op = self.operation(ws)
		line = self.routing([(op, ws)])
		self.assert_not_approvable("Routing", line, f"operation {op} approved")
		self.approve("Workstation", ws)
		self.approve("Operation", op)
		self.approve("Routing", line)
		self.assertTrue(master_approval.approved("Routing", line))


@verifies("URS-MST-012")
class TestUse(RoutingTestCase):
	def bom(self, line: str | None, operations: list[dict] | None = None):
		product, material = (
			lot_item(f"MDS-RT-P-{self.suffix()}", "Products"),
			lot_item(f"MDS-RT-M-{self.suffix()}"),
		)
		approve_for_test(product, material)
		return frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"with_operations": 1,
				"routing": line,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
				**({"operations": operations} if operations else {}),
			}
		).insert()

	def approved_bill(self, product: str) -> str:
		"""An approved bill of the product without operations: a work order is made only from one
		(module 2), so the operations a test gives the order are what is checked."""
		material = lot_item(f"MDS-RT-M-{self.suffix()}")
		approve_for_test(material)
		bill = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": product,
				"company": company(),
				"quantity": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
			}
		).insert()
		return submit_bom_for_test(bill).name

	def test_a_bill_of_materials_is_submitted_only_on_approved_masters(self):
		ws = self.workstation()
		op = self.operation(ws)
		line = self.routing([(op, ws)])
		with self.assertRaisesRegex(frappe.ValidationError, "made only on what QA has approved"):
			submit_bom_for_test(self.bom(line))  # as QA's signed approval submits it (module 2)
		_ws, _op, approved_line = self.approved_line()
		submit_bom_for_test(self.bom(approved_line))

	def test_no_work_order_on_an_unapproved_operation_also_without_validation(self):
		ws = self.workstation()
		op = self.operation(ws)
		product = lot_item(f"MDS-RT-WO-{self.suffix()}", "Products")
		approve_for_test(product)
		bill = self.approved_bill(product)
		for skipped in (False, True):
			with self.subTest(validation_skipped=skipped):
				order = frappe.get_doc(
					{
						"doctype": "Work Order",
						"production_item": product,
						"bom_no": bill,
						"company": company(),
						"qty": 1,
						"operations": [{"operation": op, "workstation": ws, "time_in_mins": 10}],
					}
				)
				order.flags.ignore_validate = order.flags.ignore_mandatory = skipped
				with self.assertRaisesRegex(frappe.ValidationError, "made only on what QA has approved"):
					order.insert()

	def test_a_bill_naming_an_unapproved_routing_is_refused_even_without_operations(self):
		ws = self.workstation()
		line = self.routing([(self.operation(ws), ws)])
		bom = self.bom(line)
		frappe.db.set_value("BOM", bom.name, "with_operations", 0)
		with self.assertRaisesRegex(frappe.ValidationError, f"routing {line} not approved"):
			submit_bom_for_test(frappe.get_doc("BOM", bom.name))

	def job_card(self, operation: str, workstation: str, **values):
		return frappe.get_doc(
			{
				"doctype": "Job Card",
				"operation": operation,
				"workstation": workstation,
				"company": company(),
				"for_quantity": 1,
				**values,
			}
		)

	def test_what_was_approved_must_still_be_usable_at_the_point_of_use(self):
		ws, op, _line = self.approved_line()
		cases = {
			"no longer in effect": lambda: frappe.db.set_value(
				"Controlled Document",
				frappe.db.get_value("Operation", op, routing.WORK_INSTRUCTION),
				"status",
				"Obsolete",
			),
			"its equipment .* approved": lambda: self.equipment_at(ws),
			"is disabled": lambda: frappe.db.set_value("Workstation", ws, "disabled", 1),
		}
		for why, make in cases.items():
			with self.subTest(why=why):
				ws, op, _line = self.approved_line()
				make()
				with self.assertRaisesRegex(frappe.ValidationError, why):
					self.job_card(op, ws).insert()

	def test_a_job_cards_sub_operations_are_approved(self):
		ws, op, _line = self.approved_line()
		card = self.job_card(
			op, ws, sub_operations=[{"sub_operation": self.operation(), "status": "Pending"}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			card.insert()

	def test_operations_erpnext_adds_while_validating_are_checked(self):
		"""ERPNext fetches a work order's operations again in its validate (multi-level change), and
		fills a job card's sub-operations there: what it adds after before_validate is checked too."""
		from unittest.mock import patch

		ws, op, _line = self.approved_line()
		unapproved_op = self.operation()
		product = lot_item(f"MDS-RT-WV-{self.suffix()}", "Products")
		approve_for_test(product)
		bill = self.approved_bill(product)

		def refetch(order):
			order.append("operations", {"operation": unapproved_op, "workstation": ws, "time_in_mins": 5})

		with patch(
			"erpnext.manufacturing.doctype.work_order.work_order.WorkOrder.reset_use_multi_level_bom", refetch
		):
			order = frappe.get_doc(
				{
					"doctype": "Work Order",
					"production_item": product,
					"bom_no": bill,
					"company": company(),
					"qty": 1,
					"wip_warehouse": store("Work In Progress"),
					"fg_warehouse": store("Quarantine"),
					"operations": [{"operation": op, "workstation": ws, "time_in_mins": 10}],
				}
			)
			order.flags.ignore_mandatory = True
			with self.assertRaisesRegex(frappe.ValidationError, f"operation {unapproved_op} not approved"):
				order.insert()

		def fill(card):
			card.append("sub_operations", {"sub_operation": unapproved_op, "status": "Pending"})

		with patch("erpnext.manufacturing.doctype.job_card.job_card.JobCard.set_sub_operations", fill):
			with self.assertRaisesRegex(frappe.ValidationError, f"operation {unapproved_op} not approved"):
				self.job_card(op, ws).insert()

	def test_a_job_card_saved_without_validation_is_checked_too(self):
		ws, _op, _line = self.approved_line()
		card = self.job_card(self.operation(), ws)
		card.flags.ignore_validate = card.flags.ignore_mandatory = True
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			card.insert()

	def test_an_operations_own_steps_are_checked_where_it_is_used(self):
		ws = self.workstation()
		self.approve("Workstation", ws)
		sub = self.operation()
		self.approve("Operation", sub)
		op = self.operation(ws, sub_operations=[{"operation": sub, "time_in_mins": 5}])
		self.approve("Operation", op)
		frappe.db.set_value(
			"Operation", sub, APPROVAL_STATUS, DRAFT
		)  # as one approved before module 3's rule
		with self.assertRaisesRegex(frappe.ValidationError, f"operation {sub} not approved"):
			self.job_card(op, ws).insert()
		product = lot_item(f"MDS-RT-WS-{self.suffix()}", "Products")
		approve_for_test(product)
		order = frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": product,
				"bom_no": self.approved_bill(product),
				"company": company(),
				"qty": 1,
				"operations": [{"operation": op, "workstation": ws, "time_in_mins": 10}],  # no step rows
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, f"operation {sub} not approved"):
			order.insert()

	def test_a_work_order_made_from_a_bill_is_checked(self):
		from erpnext.manufacturing.doctype.work_order.work_order import make_work_order

		_ws, op, line = self.approved_line()
		bom = self.bom(line)
		submit_bom_for_test(bom)
		frappe.db.set_value("Operation", op, APPROVAL_STATUS, DRAFT)  # as a bill from before module 3
		order = frappe.get_doc(make_work_order(bom.name, bom.item, 1))
		order.update({"company": company()})
		with self.assertRaisesRegex(frappe.ValidationError, f"operation {op} not approved"):
			order.insert()

	def equipment_at(self, workstation: str) -> str:
		from meddevice_suite.pilot.inspection import ensure_instrument

		instrument = frappe.get_doc(
			"Asset", ensure_instrument(company(), approve_for_test, approve_equipment_for_test)
		)
		copy = frappe.copy_doc(instrument)
		copy.update(
			{
				"asset_name": f"Press {self.suffix()}",
				"mds_equipment_tag": f"PR-{self.suffix()}",
				"mds_workstation": workstation,
				"docstatus": 0,
			}
		)
		return copy.insert().name

	def test_no_job_card_on_an_unapproved_operation_or_workstation(self):
		ws, op, _line = self.approved_line()
		for operation, workstation in ((self.operation(), ws), (op, self.workstation())):
			with self.subTest(operation=operation, workstation=workstation):
				card = frappe.get_doc(
					{
						"doctype": "Job Card",
						"operation": operation,
						"workstation": workstation,
						"company": company(),
						"for_quantity": 1,
					}
				)
				with self.assertRaisesRegex(frappe.ValidationError, "made only on what QA has approved"):
					card.insert()
