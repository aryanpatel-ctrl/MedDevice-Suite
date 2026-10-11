"""Device master record (module 31, URS-QMS-026 to URS-QMS-031): tests derived from the requirements'
clauses, on real items, bills of materials, routings, specifications and controlled documents, each
approved through its own signed act and changed through change control."""

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, getdate
from frappe.www.printview import get_rendered_template

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.alerts import ALERTS
from meddevice_suite.compliance.audit_reason import stage_reason
from meddevice_suite.compliance.esign import MEANINGS, SIGNED_CONTENT, is_unchanged_since, verify_signatures
from meddevice_suite.compliance.fingerprint import sha256
from meddevice_suite.compliance.quality_settings import SETTINGS
from meddevice_suite.compliance.retention import retention_years
from meddevice_suite.compliance.signing import SIGNING_ACTIONS, sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control.test_lot_status import company, store
from meddevice_suite.master_data import item_master, routing
from meddevice_suite.master_data.item_master import FINISHED_GOOD, SUB_ASSEMBLY
from meddevice_suite.master_data.test_item_master import new_item
from meddevice_suite.master_data.test_specification import template
from meddevice_suite.master_data.versioning import CHANGE_REQUEST
from meddevice_suite.qa import verifies
from meddevice_suite.qms import device_master_record as dmr
from meddevice_suite.qms import pinned_records
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.qms.test_change_control import approved_change, signer
from meddevice_suite.qms.test_risk_management import RiskTestCase
from meddevice_suite.setup.sidebars import SIDEBAR_ADDITIONS
from meddevice_suite.setup.test_stock import device_master_record_for_test, risk_report_for_test
from meddevice_suite.work_management import reminders

REFUSED = (frappe.ValidationError, frappe.PermissionError)  # Frappe or this module, whichever checks first
TEXT = {heading: f"{heading}: as in the documents below (test)." for heading in dmr.HEADINGS}


def headings(**overrides) -> list[dict]:
	"""Every heading written; installation and servicing not applicable, with the reason."""
	rows = []
	for heading in dmr.HEADINGS:
		row = {"heading": heading, "not_applicable": 0, "content": TEXT[heading]}
		if heading == dmr.INSTALLATION:
			row.update(not_applicable=1, content="Single-use sterile device: not installed or serviced.")
		row.update(overrides.get(heading, {}))
		rows.append(row)
	return rows


class DeviceMasterRecordTestCase(RiskTestCase):
	"""Users: the Production Supervisor (`supervisor`) drafts and signs as author; QA (`qa`, `qa2`)
	authors and approves; Management (`manager`) and the QC Inspector (`inspector`) read; Sales
	(`outsider`) has no access."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.inspector = signer("QC Inspector")

	# --- A finished good with everything its record names -------------------------------------

	def as_production(self, doc: dict) -> Document:
		frappe.set_user(self.supervisor)
		try:
			return frappe.get_doc(doc).insert()
		finally:
			frappe.set_user("Administrator")

	def approve_master(
		self, doctype: str, name: str, reason: str = "Checked against the process flow"
	) -> None:
		self.sign_as(self.qa, doctype, name, "Approve", reason)

	def product(
		self,
		in_process: bool = True,
		labelling: str | None = None,
		risk: bool = True,
		sub_operation: bool = False,
		sub_assembly: bool = False,
	) -> frappe._dict:
		"""An approved finished good with its final specification, an approved routing of one
		operation to an effective work instruction, its in-process specification when the operation
		carries an in-process check, its approved default bill of materials on the routing, an
		effective Packaging and Labelling document and, unless told otherwise, the risk management
		report in force of a file listing it (plan D-23; recorded as the signed acts leave it, as
		risk management is tested in qms/test_risk_management.py). `sub_operation`: the operation has
		a sub-operation with a work instruction of its own; `sub_assembly`: the bill uses a
		sub-assembly made by its own approved bill."""
		suffix = frappe.generate_hash(length=6).upper()
		item = self.approved_item(FINISHED_GOOD)
		material = self.approved_item()
		instruction = self.effective("Work Instruction")
		workstation = self.as_production(
			{"doctype": "Workstation", "workstation_name": f"MDS DMR WS {suffix}"}
		).name
		self.approve_master("Workstation", workstation)
		step = step_instruction = None
		if sub_operation:
			step_instruction = self.effective("Work Instruction")
			step = self.as_production(
				{
					"doctype": "Operation",
					"name": f"MDS DMR STEP {suffix}",
					"workstation": workstation,
					routing.WORK_INSTRUCTION: step_instruction,
					routing.IN_PROCESS_CHECK: "No",
				}
			).name
			self.approve_master("Operation", step)
		operation = self.as_production(
			{
				"doctype": "Operation",
				"name": f"MDS DMR OP {suffix}",
				"workstation": workstation,
				routing.WORK_INSTRUCTION: instruction,
				routing.IN_PROCESS_CHECK: "Yes" if in_process else "No",
				"sub_operations": [{"operation": step, "time_in_mins": 5}] if step else [],
			}
		).name
		self.approve_master("Operation", operation)
		line = self.as_production(
			{
				"doctype": "Routing",
				"routing_name": f"MDS DMR Line {suffix}",
				"operations": [
					{"operation": operation, "workstation": workstation, "time_in_mins": 10, "sequence_id": 1}
				],
			}
		).name
		self.approve_master("Routing", line)
		in_process_specification = None
		if in_process:
			frappe.set_user(self.qa)
			in_process_specification = (
				template(f"MDS DMR IPQC {suffix}", item, stage="In Process").insert().name
			)
			frappe.set_user("Administrator")
		component = component_bom = None
		if sub_assembly:
			component = self.approved_item(SUB_ASSEMBLY)
			component_bom = self.component_bill(component, material)
		bom = self.bill(
			item,
			material,
			line,
			**(
				{
					"items": [
						{"item_code": material, "qty": 1, "rate": 1},
						{"item_code": component, "qty": 1, "rate": 1, "bom_no": component_bom},
					]
				}
				if component
				else {}
			),
		)
		return frappe._dict(
			item=item,
			material=material,
			instruction=instruction,
			workstation=workstation,
			operation=operation,
			routing=line,
			bom=bom,
			final=frappe.db.get_value(
				"Quality Inspection Template", {"mds_item": item, "mds_stage": "Final"}
			),
			in_process=in_process_specification,
			labelling=labelling or self.effective("Packaging and Labelling"),
			report=risk_report_for_test(item) if risk else None,
			step=step,
			step_instruction=step_instruction,
			component=component,
			component_bom=component_bom,
		)

	def component_bill(self, component: str, material: str, **values) -> str:
		"""A sub-assembly's bill of materials, drafted by Production, approved by QA."""
		bom = self.as_production(
			{
				"doctype": "BOM",
				"item": component,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": 1,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
				**values,
			}
		).name
		self.sign_as(self.qa, "BOM", bom, "Approve", "Components and quantities checked")
		return bom

	def bill(self, item: str, material: str, line: str, **values) -> str:
		"""A bill of materials of the item on the routing, drafted by Production, approved by QA."""
		bom = self.as_production(
			{
				"doctype": "BOM",
				"item": item,
				"company": company(),
				"quantity": 1,
				"is_active": 1,
				"is_default": 1,
				"with_operations": 1,
				"routing": line,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": material, "qty": 1, "rate": 1}],
				**values,
			}
		).name
		self.sign_as(self.qa, "BOM", bom, "Approve", "Components and quantities checked")
		return bom

	def dmr_draft(
		self, p: frappe._dict, user: str | None = None, documents: list[str] | None = None, **values
	):
		frappe.set_user(user or self.supervisor)
		try:
			return frappe.get_doc(
				{
					"doctype": dmr.DOCTYPE,
					"item": p.item,
					"headings": headings(),
					"documents": [
						{"controlled_document": name}
						for name in (documents if documents is not None else [p.labelling])
					],
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def current_dmr(self, p: frappe._dict, **values) -> Document:
		"""Drafted and signed by the Production Supervisor, approved by QA."""
		name = self.dmr_draft(p, **values).name
		self.sign_as(self.supervisor, dmr.DOCTYPE, name, "Author")
		return self.sign_as(self.qa, dmr.DOCTYPE, name, "Approve")

	def next_revision(self, item: str, user: str | None = None) -> Document:
		frappe.set_user(user or self.supervisor)
		try:
			doc = frappe.get_doc(dmr.DOCTYPE, dmr.revise(item))
			doc.summary_of_change = "Brought to the records in force"
			doc.save()
			return doc
		finally:
			frappe.set_user("Administrator")

	def element(self, record: Document, doctype: str, name: str):
		return next(
			row for row in record.elements if (row.reference_doctype, row.reference_name) == (doctype, name)
		)

	def status(self, name: str) -> str:
		return frappe.db.get_value(dmr.DOCTYPE, name, "status")

	def revise_document(self, name: str, summary: str = "Step 4 clarified") -> str:
		"""A new revision of an effective document, under an approved change request (module 30);
		returns the change request."""
		revision = self.issue(frappe.get_doc("Controlled Document", name), summary=summary)
		frappe.set_user("Administrator")
		return frappe.db.get_value("Document Revision", revision.name, "change_request")

	def change_specification(self, specification: str) -> str:
		"""QA changes a specification in use under an approved change request (module 6)."""
		change = approved_change(
			self.author, self.qa, specification, "Quality Inspection Template", "Product or BOM"
		)
		frappe.set_user(self.qa)
		try:
			spec = frappe.get_doc("Quality Inspection Template", specification)
			spec.mds_change_request = change
			spec.item_quality_inspection_parameter[-1].max_value = 9.9
			spec.save()
		finally:
			frappe.set_user("Administrator")
		return change

	def work_order(self, item: str, bom: str) -> Document:
		return frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": item,
				"bom_no": bom,
				"qty": 1,
				"company": company(),
				"source_warehouse": store("Stores"),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store("Quarantine"),
			}
		)

	def alerts_sent(self, name: str) -> int:
		return frappe.db.count(
			"Notification Log", {"document_type": dmr.DOCTYPE, "document_name": name, "for_user": self.qa}
		)


# --- URS-QMS-026, URS-QMS-027: what a revision holds, pinned from the live records -------------------


@verifies("URS-QMS-026", "URS-QMS-027")
class TestAssembly(DeviceMasterRecordTestCase):
	def test_assemble_pins_live(self):
		p = self.product()
		record = self.dmr_draft(p, documents=[p.labelling, self.plan_document])

		expected = {
			(dmr.DEVICE, "Item", p.item),
			(dmr.BILL, "BOM", p.bom),
			(dmr.ROUTE, "Routing", p.routing),
			(dmr.OPERATION, "Operation", p.operation),
			(dmr.WORK_INSTRUCTION, "Controlled Document", p.instruction),
			(dmr.IN_PROCESS_SPECIFICATION, "Quality Inspection Template", p.in_process),
			(dmr.FINAL_SPECIFICATION, "Quality Inspection Template", p.final),
			(dmr.LABELLING_DOCUMENT, "Controlled Document", p.labelling),
			(dmr.DOCUMENT, "Controlled Document", self.plan_document),  # an SOP the author chose
			(dmr.RISK_REPORT, rm.REPORT, p.report),  # plan D-23: every finished good has one
		}
		self.assertEqual(
			{(row.element_type, row.reference_doctype, row.reference_name) for row in record.elements},
			expected,
		)
		for row in record.elements:
			with self.subTest(element=row.element_type):
				self.assertEqual(
					row.pinned_version,
					pinned_records.version_token(row.reference_doctype, row.reference_name),
				)
				self.assertTrue(row.pinned_revision)
				self.assertIn(row.heading, dmr.HEADINGS)
		self.assertEqual(
			self.element(record, "Controlled Document", p.labelling).pinned_version,
			frappe.db.get_value("Controlled Document", p.labelling, "current_revision"),
		)
		self.assertEqual(self.element(record, "Controlled Document", p.labelling).heading, dmr.LABELLING)
		self.assertEqual([row.heading for row in record.headings], list(dmr.HEADINGS))
		self.assertEqual((record.status, record.revision, record.author), (dmr.DRAFT, "01", self.supervisor))
		self.assertEqual(record.name, f"DMR-{p.item}-01")

	def test_sub_operations_and_sub_assembly_bills_are_pinned(self):
		p = self.product(sub_operation=True, sub_assembly=True)
		record = self.dmr_draft(p)
		elements = {(row.element_type, row.reference_doctype, row.reference_name) for row in record.elements}
		for expected in (
			(dmr.OPERATION, "Operation", p.step),
			(dmr.WORK_INSTRUCTION, "Controlled Document", p.step_instruction),
			(dmr.SUB_ASSEMBLY_BILL, "BOM", p.component_bom),
		):
			with self.subTest(element=expected):
				self.assertIn(expected, elements)
		self.assertEqual(
			self.element(record, "BOM", p.component_bom).pinned_version,
			pinned_records.version_token("BOM", p.component_bom),
		)
		# A sub-operation without its work instruction is missing, as an operation is.
		frappe.db.set_value("Operation", p.step, routing.WORK_INSTRUCTION, None)
		with self.assertRaisesRegex(frappe.ValidationError, f"the work instruction of operation {p.step}"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

	def test_typed_version_overwritten(self):
		p = self.product()
		record = self.dmr_draft(p)
		frappe.set_user(self.supervisor)
		record.elements[0].pinned_version = "999"
		record.append(
			"elements",
			{
				"element_type": dmr.DOCUMENT,
				"reference_doctype": "Controlled Document",
				"reference_name": self.plan_document,  # a real document the author did not choose
				"pinned_version": "typed",
			},
		)
		record.status = dmr.CURRENT  # what the system sets is not typed either
		record.save()
		record.reload()

		self.assertNotIn("999", [row.pinned_version for row in record.elements])
		self.assertNotIn(self.plan_document, [row.reference_name for row in record.elements])
		self.assertEqual(record.status, dmr.DRAFT)
		self.assertEqual(
			self.element(record, "Item", p.item).pinned_version, pinned_records.version_token("Item", p.item)
		)

	def test_missing_element_blocks(self):
		p = self.product()
		for documents, values, lacking in (
			([], {}, "Packaging and Labelling"),
			([p.labelling, self.registered("Packaging and Labelling")], {}, "records in force instead of"),
			(None, {"headings": headings(**{dmr.MEASURING: {"content": ""}})}, "Measuring and Monitoring"),
		):
			with self.subTest(lacking=lacking):
				record = self.dmr_draft(p, documents=documents, **values)
				with self.assertRaisesRegex(frappe.ValidationError, lacking):
					self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
				self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Withdraw", "Drafted for the test")

		no_in_process = self.product(in_process=False)
		frappe.db.set_value("Operation", no_in_process.operation, routing.IN_PROCESS_CHECK, "Yes")
		record = self.dmr_draft(no_in_process)
		with self.assertRaisesRegex(frappe.ValidationError, "in-process specification"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

		no_bill = frappe._dict(item=self.approved_item(FINISHED_GOOD), labelling=p.labelling)
		record = self.dmr_draft(no_bill)
		with self.assertRaisesRegex(frappe.ValidationError, "default bill of materials"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

	def test_no_in_process_check_needs_no_in_process_specification(self):
		p = self.product(in_process=False)
		record = self.current_dmr(p)
		self.assertEqual(record.status, dmr.CURRENT)
		self.assertNotIn(dmr.IN_PROCESS_SPECIFICATION, [row.element_type for row in record.elements])

	def test_unapproved_item_refused(self):
		p = self.product()
		record = self.dmr_draft(p)
		frappe.db.set_value("Item", p.item, item_master.APPROVAL_STATUS, item_master.DRAFT)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

		frappe.db.set_value("Item", p.item, item_master.APPROVAL_STATUS, item_master.APPROVED)
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		frappe.db.set_value("Item", p.item, item_master.APPROVAL_STATUS, item_master.DRAFT)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved"):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")

	def test_only_a_finished_good_has_a_record(self):
		with self.assertRaisesRegex(frappe.ValidationError, "finished good"):
			self.dmr_draft(frappe._dict(item=self.approved_item(SUB_ASSEMBLY), labelling=self.plan_document))

	def test_the_risk_management_report_in_force_is_pinned(self):
		# Plan D-23 (decided 2026-10-12): every finished good is in a file whose report is in force.
		p = self.product(risk=False)
		record = self.dmr_draft(p)
		with self.assertRaisesRegex(frappe.ValidationError, f"a risk management file listing {p.item}"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

		file = self.risk_file([p.item])
		assessment = self.approved(file=file)
		with self.assertRaisesRegex(frappe.ValidationError, f"risk management report in force of {file}"):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")

		report = self.approved_report(assessment.name)
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		record = self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")
		row = self.element(record, rm.REPORT, report)
		self.assertEqual((row.element_type, row.heading), (dmr.RISK_REPORT, dmr.RISK))

		# The file's next report supersedes this one: the record goes out of date in that transaction,
		# and says which report is in force now.
		following = self.approved_report(assessment.name)
		record.reload()
		self.assertEqual(record.status, dmr.OUT_OF_DATE)
		self.assertEqual(self.element(record, rm.REPORT, report).change_note, "no longer in force")
		self.assertIn(f"{following} in force, not in this record", record.other_changes)
		self.assertIn(following, frappe.get_doc(dmr.DOCTYPE, dmr.revise(p.item)).summary_of_change)

	@verifies("URS-QMS-029")
	def test_a_risk_revision_taking_effect_puts_it_out_of_date(self):
		p = self.product(risk=False)
		file = self.risk_file([p.item])
		self.approved_report(self.approved(file=file).name)
		record = self.current_dmr(p)

		self.approved(self.revise(file))  # risk_management.take_effect: the report no longer reports on it
		record.reload()
		self.assertEqual(record.status, dmr.OUT_OF_DATE)
		row = next(row for row in record.elements if row.element_type == dmr.RISK_REPORT)
		self.assertEqual((row.stale, row.change_note), (1, "no longer in force"))
		self.assertIn(f"{file} has no risk management report in force", record.other_changes)
		self.assertEqual(self.alerts_sent(record.name), 1)

	@verifies("URS-QMS-029")
	def test_a_record_that_pins_no_report_goes_out_of_date_when_one_is_approved(self):
		# A record recorded without the report (as one approved before plan D-23 would be): the next
		# report approved for the item's file puts it out of date.
		p = self.product()
		record = self.current_dmr(p)
		row = next(row for row in record.elements if row.element_type == dmr.RISK_REPORT)
		frappe.db.delete(dmr.ELEMENT, {"name": row.name})  # past every rule, to stand for that record
		file = frappe.db.get_value(rm.FILE_ITEM, {"parenttype": rm.FILE, "item": p.item}, "parent")

		self.approved_report(frappe.db.get_value(rm.FILE, file, "current_revision"))
		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		self.assertIn(
			"in force, not in this record", frappe.db.get_value(dmr.DOCTYPE, record.name, "other_changes")
		)

	def approved_report(self, assessment: str) -> str:
		if not getattr(type(self), "sop", None):
			type(self).sop = self.effective("SOP")
		frappe.set_user(self.qa)
		try:
			report = frappe.get_doc(
				{
					"doctype": rm.REPORT,
					"risk_assessment": assessment,
					"plan_implemented": 1,
					"plan_review": "Every activity of the plan checked against its record.",
					"overall_conclusion": "All residual risks acceptable.",
					"information_sources": [{"controlled_document": self.sop}],
				}
			).insert()
		finally:
			frappe.set_user("Administrator")
		self.sign_as(self.qa, rm.REPORT, report.name, "Author")
		self.sign_as(self.qa2, rm.REPORT, report.name, "Approve")
		return report.name

	def test_device_lifetime_is_controlled_on_the_item_and_shown_on_the_record(self):
		p = self.product()
		record = self.dmr_draft(p)
		self.assertEqual(record.device_lifetime_years, item_master.DEFAULT_LIFETIME_YEARS)
		# Plan D-16: 5 years unless stated; an approval signed at the default binds no new field.
		self.assertNotIn(
			item_master.DEVICE_LIFETIME, item_master.signed_content(frappe.get_doc("Item", p.item))
		)
		item = frappe.get_doc("Item", self.approved_item(FINISHED_GOOD, **{item_master.DEVICE_LIFETIME: 7}))
		self.assertEqual(item_master.signed_content(item)[item_master.DEVICE_LIFETIME], 7)
		self.assertTrue(verify_signatures("Item", item.name))
		for years in (0, item_master.longest_lifetime() + 1):
			with self.subTest(years=years), self.assertRaisesRegex(frappe.ValidationError, "device lifetime"):
				new_item(FINISHED_GOOD, **{item_master.DEVICE_LIFETIME: years})
		# No shorter than the shelf life, in years rounded up: 400 days is 2 years.
		with self.assertRaisesRegex(frappe.ValidationError, "shorter than its shelf life of 2 years"):
			new_item(FINISHED_GOOD, shelf_life_in_days=400, **{item_master.DEVICE_LIFETIME: 1})
		new_item(FINISHED_GOOD, shelf_life_in_days=730, **{item_master.DEVICE_LIFETIME: 2})
		frappe.set_user(self.qa)
		item.set(item_master.DEVICE_LIFETIME, 6)
		with self.assertRaisesRegex(frappe.ValidationError, "is in use"):
			item.save()  # a controlled field: changed only under an approved change request

	def test_an_item_approved_before_the_lifetime_existed_is_unchanged_since_its_signature(self):
		item = frappe.get_doc("Item", self.approved_item(FINISHED_GOOD))
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": "Item", "reference_name": item.name}
		)
		# What QA's signature bound before plan D-16 added the field: the item's content without it.
		before_d16 = {
			field: value
			for field, value in item_master.signed_content(item).items()
			if field != item_master.DEVICE_LIFETIME
		}
		frappe.db.set_value("Electronic Signature", signature.name, "record_hash", sha256(before_d16))
		# The column added with D-16 holds the default for every item made before it.
		frappe.db.set_value(
			"Item", item.name, item_master.DEVICE_LIFETIME, item_master.DEFAULT_LIFETIME_YEARS
		)
		self.assertTrue(is_unchanged_since(frappe.get_doc("Electronic Signature", signature.name)))
		# Any other lifetime is bound, 0 too: written past the item's rules, it shows as a change.
		frappe.db.set_value("Item", item.name, item_master.DEVICE_LIFETIME, 0)
		self.assertFalse(is_unchanged_since(frappe.get_doc("Electronic Signature", signature.name)))


# --- URS-QMS-028: signed by its author, approved by QA, supersedes the one before ---------------------


@verifies("URS-QMS-028")
class TestSignatures(DeviceMasterRecordTestCase):
	def test_author_cannot_approve(self):
		p = self.product()
		record = self.dmr_draft(p, user=self.qa)
		self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Author")
		with self.assertRaisesRegex(frappe.ValidationError, "author"):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Approve")  # QA approves
		record = self.sign_as(self.qa2, dmr.DOCTYPE, record.name, "Approve")

		self.assertEqual((record.docstatus, record.status, record.approver), (1, dmr.CURRENT, self.qa2))
		self.assertEqual(
			self.signatures_on(dmr.DOCTYPE, record.name), [(self.qa, "Authored"), (self.qa2, "Approved")]
		)
		self.assertTrue(verify_signatures(dmr.DOCTYPE, record.name))

	def test_only_the_author_signs_as_author(self):
		record = self.dmr_draft(self.product())
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Author")

	def test_approve_supersedes(self):
		p = self.product()
		first = self.current_dmr(p)
		second = self.next_revision(p.item)
		self.assertEqual((second.revision, second.supersedes), ("02", first.name))
		self.sign_as(self.supervisor, dmr.DOCTYPE, second.name, "Author")
		self.assertEqual(self.status(first.name), dmr.CURRENT)  # in force until the next is approved
		self.sign_as(self.qa, dmr.DOCTYPE, second.name, "Approve")

		self.assertEqual(
			frappe.db.get_value(dmr.DOCTYPE, first.name, ["status", "superseded_by"]),
			(dmr.SUPERSEDED, second.name),
		)
		self.assertEqual(frappe.db.count(dmr.DOCTYPE, {"item": p.item, "status": dmr.CURRENT}), 1)
		self.assertEqual(dmr.current_record(p.item), second.name)
		for name in (first.name, second.name):
			self.assertTrue(verify_signatures(dmr.DOCTYPE, name))
		self.assertIn(
			"Superseded by",
			frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": dmr.DOCTYPE, "reference_name": first.name},
				pluck="reason",
				order_by="event_at desc",
				limit=1,
			)[0],
		)

	def test_one_revision_in_progress_per_item(self):
		p = self.product()
		self.dmr_draft(p)
		with self.assertRaisesRegex(frappe.ValidationError, "revision in progress"):
			self.dmr_draft(p, user=self.qa)

	def test_approval_assembles_again(self):
		p = self.product()
		record = self.dmr_draft(p)
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		self.change_specification(p.final)  # a new version after the author signed

		with self.assertRaisesRegex(frappe.ValidationError, "changed since the author signed"):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")
		self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Return", "The final specification changed")
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		record = self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")
		self.assertEqual(
			self.element(record, "Quality Inspection Template", p.final).pinned_version,
			pinned_records.version_token("Quality Inspection Template", p.final),
		)

	def test_an_element_out_of_force_blocks_approval(self):
		p = self.product()
		record = self.dmr_draft(p)
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		self.sign_as(self.qa, "Controlled Document", p.labelling, "Withdraw", "Replaced by the new label")
		with self.assertRaisesRegex(frappe.ValidationError, "records in force instead of"):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")

	def test_returned_and_withdrawn_with_a_reason(self):
		p = self.product()
		record = self.dmr_draft(p)
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		with self.assertRaisesRegex(frappe.ValidationError, "what must change"):
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Return")
		self.assertEqual(
			self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Return", "Add the IFU").status, dmr.DRAFT
		)

		current = self.current_dmr(self.product())
		with self.assertRaisesRegex(frappe.ValidationError, "why the record is withdrawn"):
			self.sign_as(self.qa, dmr.DOCTYPE, current.name, "Withdraw")
		with self.assertRaises(frappe.PermissionError):
			self.sign_as(self.supervisor, dmr.DOCTYPE, current.name, "Withdraw", "Discontinued")
		withdrawn = self.sign_as(self.qa, dmr.DOCTYPE, current.name, "Withdraw", "Product discontinued")
		self.assertEqual((withdrawn.status, withdrawn.docstatus), (dmr.WITHDRAWN, 1))
		self.assertIsNone(dmr.current_record(current.item))
		self.assertTrue(verify_signatures(dmr.DOCTYPE, current.name))

	def test_changed_only_by_its_acts_and_never_deleted_or_cancelled(self):
		p = self.product()
		record = self.dmr_draft(p)
		frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.PermissionError, "never deleted"):
			frappe.delete_doc(dmr.DOCTYPE, record.name)  # not even a draft, by the Administrator
		self.assertTrue(frappe.db.exists(dmr.DOCTYPE, record.name))
		frappe.set_user(self.qa)  # not its author
		doc = frappe.get_doc(dmr.DOCTYPE, record.name)
		doc.summary_of_change = "Changed by someone else"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		frappe.set_user("Administrator")
		self.sign_as(self.supervisor, dmr.DOCTYPE, record.name, "Author")
		for user in (self.qa, self.supervisor):
			with self.subTest(user=user), self.assertRaises(REFUSED):
				frappe.set_user(user)
				frappe.get_doc(dmr.DOCTYPE, record.name).submit()  # Frappe's own Submit never approves it
		frappe.set_user("Administrator")
		current = self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Approve")

		current.headings[0].content = "Rewritten after approval"
		with self.assertRaises(REFUSED):
			current.save()
		current.reload()
		with self.assertRaises(REFUSED):
			current.cancel()
		with self.assertRaises(REFUSED):
			frappe.delete_doc(dmr.DOCTYPE, current.name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists(dmr.DOCTYPE, current.name))

	def test_an_out_of_date_record_is_not_put_back_in_force_by_a_save(self):
		p = self.product()
		record = self.current_dmr(p)
		self.revise_document(p.labelling)
		stale = next(row.name for row in frappe.get_doc(dmr.DOCTYPE, record.name).elements if row.stale)
		# Each field is allowed on submit, so the refusal is this module's, not Frappe's.
		meta = frappe.get_meta(dmr.DOCTYPE)
		self.assertTrue(all(meta.get_field(f).allow_on_submit for f in ("status", "out_of_date_since")))
		self.assertTrue(frappe.get_meta(dmr.ELEMENT).get_field("stale").allow_on_submit)
		for user in (self.qa, "Administrator"):
			for change in (
				lambda doc: doc.set("status", dmr.CURRENT),
				lambda doc: doc.set("out_of_date_since", None),
				lambda doc: next(row for row in doc.elements if row.name == stale).set("stale", 0),
			):
				with self.subTest(user=user, change=change):
					frappe.set_user(user)
					doc = frappe.get_doc(dmr.DOCTYPE, record.name)
					change(doc)
					with self.assertRaisesRegex(
						frappe.PermissionError, "changes only through its signed acts"
					):
						doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)

	def test_signature_excludes_status(self):
		p = self.product()
		record = self.current_dmr(p)
		content = dmr.signed_content(record)
		self.assertNotIn("status", content)
		self.assertEqual(
			[row[3:] for row in content["elements"]],
			[
				[row.reference_name, row.pinned_version, row.pinned_revision, row.title, row.changed_under]
				for row in record.elements
			],
		)

		self.revise_document(p.labelling)  # the record goes out of date
		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		self.assertEqual(dmr.signed_content(frappe.get_doc(dmr.DOCTYPE, record.name)), content)
		for signature in frappe.get_all(
			"Electronic Signature", filters={"reference_doctype": dmr.DOCTYPE, "reference_name": record.name}
		):
			with self.subTest(signature=signature.name):
				self.assertTrue(is_unchanged_since(frappe.get_doc("Electronic Signature", signature.name)))
		self.assertTrue(verify_signatures(dmr.DOCTYPE, record.name))

	def test_the_signatures_bind_the_pinned_versions(self):
		record = self.current_dmr(self.product())
		signature = frappe.get_last_doc(
			"Electronic Signature", filters={"reference_doctype": dmr.DOCTYPE, "reference_name": record.name}
		)
		row = record.elements[0]
		# What the printed index shows of each element is bound with its version.
		for field in ("pinned_version", "pinned_revision", "title", "changed_under"):
			with self.subTest(field=field):
				kept = frappe.db.get_value(dmr.ELEMENT, row.name, field)
				frappe.db.set_value(dmr.ELEMENT, row.name, field, "tampered")  # past every rule
				self.assertFalse(is_unchanged_since(signature))
				frappe.db.set_value(dmr.ELEMENT, row.name, field, kept)
				self.assertTrue(is_unchanged_since(signature))


# --- URS-QMS-029: out of date in the same transaction, alerted once, re-approved ----------------------


@verifies("URS-QMS-029")
class TestOutOfDate(DeviceMasterRecordTestCase):
	def test_revision_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = self.revise_document(p.labelling, "New symbol on the carton label")

		record.reload()
		self.assertEqual(record.status, dmr.OUT_OF_DATE)
		self.assertTrue(record.out_of_date_since)
		row = self.element(record, "Controlled Document", p.labelling)
		self.assertEqual(
			(row.stale, row.current_version, row.current_changed_under),
			(1, frappe.db.get_value("Controlled Document", p.labelling, "current_revision"), change),
		)
		self.assertIn("now Revision", row.change_note)
		self.assertEqual(sum(r.stale for r in record.elements), 1)
		self.assertIn(
			"Out of date",
			frappe.get_all(
				"Audit Event",
				filters={"reference_doctype": dmr.DOCTYPE, "reference_name": record.name},
				pluck="reason",
				order_by="event_at desc",
				limit=1,
			)[0],
		)

	def test_spec_version_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = self.change_specification(p.in_process)

		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		row = self.element(
			frappe.get_doc(dmr.DOCTYPE, record.name), "Quality Inspection Template", p.in_process
		)
		self.assertEqual((row.stale, row.current_changed_under), (1, change))

	def test_new_default_bom_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = approved_change(self.author, self.qa, p.bom, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		self.bill(p.item, p.material, p.routing, **{CHANGE_REQUEST: change})

		record.reload()
		self.assertEqual(record.status, dmr.OUT_OF_DATE)
		self.assertEqual(self.element(record, "BOM", p.bom).change_note, "no longer in force")

	def test_a_bill_switched_off_after_approval_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = approved_change(self.author, self.qa, p.bom, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		bom = frappe.get_doc("BOM", p.bom)
		bom.update({"is_active": 0, "is_default": 0, CHANGE_REQUEST: change})
		stage_reason("BOM", bom.name, "update_after_submit", "Product line closed")
		bom.save()  # BOM on_update_after_submit

		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)

	def test_work_instruction_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = self.revise_document(p.instruction, "Torque raised")

		record.reload()
		self.assertEqual(record.status, dmr.OUT_OF_DATE)
		row = self.element(record, "Controlled Document", p.instruction)
		self.assertEqual(
			(row.element_type, row.stale, row.current_changed_under), (dmr.WORK_INSTRUCTION, 1, change)
		)

	def test_an_operation_changed_under_control_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = approved_change(self.supervisor, self.qa, p.operation, "Operation", "Process or Routing")
		frappe.set_user(self.supervisor)
		operation = frappe.get_doc("Operation", p.operation)
		operation.update({"batch_size": 5, CHANGE_REQUEST: change})
		operation.save()
		frappe.set_user("Administrator")

		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)

	def test_sweep_catches_db_change(self):
		labelling = self.effective("Packaging and Labelling")
		records = [self.current_dmr(self.product(labelling=labelling)).name for _ in range(2)]
		frappe.db.set_value("Controlled Document", labelling, "status", "Obsolete")  # a database correction
		self.assertEqual({self.status(name) for name in records}, {dmr.CURRENT})

		pinned_records.sweep()
		# One sweep marks every record the change reaches, not one per run.
		self.assertEqual([self.status(name) for name in records], [dmr.OUT_OF_DATE] * 2)
		pinned_records.sweep()  # and a second run alerts nobody again
		for name in records:
			self.assertEqual(self.alerts_sent(name), 1)

	def test_alert_once(self):
		p = self.product()
		record = self.current_dmr(p)
		self.revise_document(p.labelling)
		self.revise_document(p.instruction)  # a second change to a record already out of date
		pinned_records.sweep()
		# Called again for it (two changes racing), the process records nothing twice.
		self.assertFalse(dmr.mark_out_of_date(record.name, dmr.changes_since_approval(record.name)))

		self.assertEqual(self.alerts_sent(record.name), 1)
		self.assertIn(dmr.OUT_OF_DATE_ALERT, ALERTS)
		record.reload()
		# Marked once, and still recording what changed after: both changes are shown.
		self.assertEqual(
			{row.reference_name for row in record.elements if row.stale}, {p.labelling, p.instruction}
		)
		reasons = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": dmr.DOCTYPE, "reference_name": record.name},
			pluck="reason",
		)
		self.assertTrue(any("a further change recorded" in (reason or "") for reason in reasons))
		summary = frappe.get_doc(dmr.DOCTYPE, self.next_revision_started(p.item)).summary_of_change
		for name in (p.labelling, p.instruction):
			self.assertIn(name, summary)  # the next revision is brought up to date after both

	def next_revision_started(self, item: str) -> str:
		frappe.set_user(self.supervisor)
		try:
			return dmr.revise(item)
		finally:
			frappe.set_user("Administrator")

	def test_an_item_version_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = approved_change(self.author, self.qa, p.item, "Item", "Product or BOM")
		item = frappe.get_doc("Item", p.item)  # QA changes it, under the change request
		item.update({"shelf_life_in_days": 400, CHANGE_REQUEST: change})
		item.save()
		frappe.set_user("Administrator")

		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		row = self.element(frappe.get_doc(dmr.DOCTYPE, record.name), "Item", p.item)
		self.assertEqual((row.stale, row.current_changed_under), (1, change))

	def test_a_routing_version_marks_out_of_date(self):
		p = self.product()
		record = self.current_dmr(p)
		change = approved_change(self.supervisor, self.qa, p.routing, "Routing", "Process or Routing")
		frappe.set_user(self.supervisor)
		line = frappe.get_doc("Routing", p.routing)
		line.operations[0].time_in_mins = (line.operations[0].time_in_mins or 0) + 5
		line.set(CHANGE_REQUEST, change)
		line.save()
		frappe.set_user("Administrator")

		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		row = self.element(frappe.get_doc(dmr.DOCTYPE, record.name), "Routing", p.routing)
		self.assertEqual((row.stale, row.current_changed_under), (1, change))

	def test_a_sub_operation_or_a_sub_assembly_bill_changed_marks_out_of_date(self):
		p = self.product(sub_operation=True, sub_assembly=True)
		record = self.current_dmr(p)
		change = self.revise_document(p.step_instruction, "Sub-step clarified")
		row = self.element(
			frappe.get_doc(dmr.DOCTYPE, record.name), "Controlled Document", p.step_instruction
		)
		self.assertEqual(
			(self.status(record.name), row.stale, row.current_changed_under), (dmr.OUT_OF_DATE, 1, change)
		)

		record = self.current_dmr(p, summary_of_change="Sub-step instruction brought in")
		change = approved_change(self.author, self.qa, p.component_bom, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		self.component_bill(p.component, p.material, **{CHANGE_REQUEST: change})  # its new default
		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		self.assertEqual(
			self.element(frappe.get_doc(dmr.DOCTYPE, record.name), "BOM", p.component_bom).change_note,
			"no longer in force",
		)

	def test_a_reminder_stays_open_until_reapproved(self):
		p = self.product()
		record = self.current_dmr(p)
		self.revise_document(p.labelling)
		since = frappe.db.get_value(dmr.DOCTYPE, record.name, "out_of_date_since")

		due = [item for item in reminders.dmr_reapprovals_due() if item.reference_name == record.name]
		self.assertEqual(len(due), 1)
		self.assertEqual(
			(getdate(due[0].due_date), due[0].roles),
			(
				getdate(add_days(getdate(since), quality_settings.value("dmr_reapproval_days"))),
				("Quality Manager",),
			),
		)
		self.assertIn("DMR Re-approval", reminders.CATEGORIES)

		second = self.next_revision(p.item)
		self.sign_as(self.supervisor, dmr.DOCTYPE, second.name, "Author")
		self.sign_as(self.qa, dmr.DOCTYPE, second.name, "Approve")
		self.assertEqual(self.status(record.name), dmr.SUPERSEDED)
		self.assertFalse(
			[item for item in reminders.dmr_reapprovals_due() if item.reference_name == record.name]
		)


# --- URS-QMS-030: only a Current record prints as the controlled index -------------------------------


@verifies("URS-QMS-030")
class TestPrint(DeviceMasterRecordTestCase):
	def printout(self, name: str) -> str:
		frappe.set_user(self.qa)
		try:
			return get_rendered_template(
				frappe.get_doc(dmr.DOCTYPE, name),
				print_format=frappe.get_doc("Print Format", "Device Master Record"),
				no_letterhead=1,
			)
		finally:
			frappe.set_user("Administrator")

	def test_print_uncontrolled(self):
		p = self.product()
		draft = self.dmr_draft(p)
		html = self.printout(draft.name)
		self.assertIn("NOT FOR USE", html)
		self.assertNotIn("CONTROLLED INDEX", html)

		self.sign_as(self.supervisor, dmr.DOCTYPE, draft.name, "Author")
		self.sign_as(self.qa, dmr.DOCTYPE, draft.name, "Approve")
		html = self.printout(draft.name)
		self.assertIn("CONTROLLED INDEX", html)
		self.assertNotIn("NOT FOR USE", html)
		for user, meaning in ((self.supervisor, "Authored"), (self.qa, "Approved")):
			self.assertIn(user, html)
			self.assertIn(meaning, html)
		self.assertIn(p.bom, html)

		self.revise_document(p.labelling)
		html = self.printout(draft.name)
		self.assertIn("NOT FOR USE", html)
		self.assertIn("Out of Date", html)

	def test_it_is_the_records_default_printout(self):
		self.assertEqual(frappe.get_meta(dmr.DOCTYPE).default_print_format, "Device Master Record")


# --- URS-QMS-031: production only on the Current record ---------------------------------------------


@verifies("URS-QMS-031")
class TestWorkOrders(DeviceMasterRecordTestCase):
	def test_work_order_needs_current_dmr(self):
		p = self.product()
		order = self.work_order(p.item, p.bom).insert()  # a draft order is planning, not production
		for skipped in (False, True):
			with self.subTest(validation_skipped=skipped):
				order = frappe.get_doc("Work Order", order.name)
				order.flags.ignore_validate = skipped
				with self.assertRaisesRegex(frappe.ValidationError, "no Current device master record"):
					order.submit()

		record = self.current_dmr(p)
		dmr.require_current_record(frappe.get_doc("Work Order", order.name))  # passes on the pinned bill

		other = self.bill(p.item, p.material, p.routing, is_default=0)  # active, approved, not the default
		with self.assertRaisesRegex(frappe.ValidationError, "does not name bill of materials"):
			self.work_order(p.item, other).insert().submit()

		frappe.db.set_value("Controlled Document", p.labelling, "status", "Obsolete")  # past the hooks
		with self.assertRaisesRegex(frappe.ValidationError, "no longer matches"):
			frappe.get_doc("Work Order", order.name).submit()
		frappe.db.set_value("Controlled Document", p.labelling, "status", "Effective")

		self.sign_as(self.qa, dmr.DOCTYPE, record.name, "Withdraw", "Product discontinued")
		with self.assertRaisesRegex(frappe.ValidationError, "no Current device master record"):
			frappe.get_doc("Work Order", order.name).submit()

	def test_an_out_of_date_record_stops_production(self):
		p = self.product()
		self.current_dmr(p)
		order = self.work_order(p.item, p.bom).insert()
		self.revise_document(p.instruction)
		with self.assertRaisesRegex(frappe.ValidationError, "no Current device master record"):
			order.submit()

	def test_only_finished_goods_are_held_to_it(self):
		sub_assembly = self.approved_item(SUB_ASSEMBLY)
		dmr.require_current_record(
			frappe.get_doc({"doctype": "Work Order", "production_item": sub_assembly, "bom_no": "any"})
		)

	def test_the_guard_runs_on_every_submit(self):
		events = frappe.get_hooks("doc_events")["Work Order"]
		guard = "meddevice_suite.qms.device_master_record.guard_work_order"
		for event in ("before_validate", "before_submit"):
			with self.subTest(event=event):
				self.assertIn(guard, events[event])

	@verifies("URS-QMS-029")
	def test_the_order_keeps_the_record_it_was_released_on_and_qa_sees_it_open(self):
		p = self.product()
		record = self.current_dmr(p)
		order = self.work_order(p.item, p.bom)
		order.set(dmr.RELEASED_ON, record.name)  # sent before submission: set by the system only
		order.insert()
		self.assertIsNone(order.get(dmr.RELEASED_ON))
		order.submit()
		self.assertEqual(frappe.db.get_value("Work Order", order.name, dmr.RELEASED_ON), record.name)
		field = frappe.get_meta("Work Order").get_field(dmr.RELEASED_ON)
		self.assertEqual((field.read_only, field.no_copy, field.options), (1, 1, dmr.DOCTYPE))

		# Out of date: the alert and the record's form list the order still open, for QA to decide on.
		self.revise_document(p.labelling)
		self.assertEqual(dmr.open_work_orders(p.item), [order.name])
		log = frappe.get_last_doc(
			"Notification Log",
			filters={"document_type": dmr.DOCTYPE, "document_name": record.name, "for_user": self.qa},
		)
		for message in (log.email_content, log.description):
			self.assertIn(order.name, message or "")
		frappe.set_user(self.qa)
		doc = frappe.get_doc(dmr.DOCTYPE, record.name)
		doc.run_method("onload")
		self.assertEqual(doc.get_onload().get("open_work_orders"), [order.name])
		frappe.set_user("Administrator")
		frappe.db.set_value("Work Order", order.name, "status", "Completed")
		self.assertEqual(dmr.open_work_orders(p.item), [])

	def test_a_bill_changed_after_reapproval_stops_its_orders(self):
		p = self.product()
		self.current_dmr(p)
		change = approved_change(self.author, self.qa, p.bom, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		bill = self.bill(p.item, p.material, p.routing, **{CHANGE_REQUEST: change})  # the new default
		self.assertIsNone(dmr.current_record(p.item))
		second = self.next_revision(p.item)
		self.sign_as(self.supervisor, dmr.DOCTYPE, second.name, "Author")
		self.sign_as(self.qa, dmr.DOCTYPE, second.name, "Approve")  # re-approved on the new bill
		dmr.require_current_record(self.work_order(p.item, bill))
		with self.assertRaisesRegex(frappe.ValidationError, "does not name bill of materials"):
			self.work_order(p.item, p.bom).insert().submit()  # the bill replaced

		change = approved_change(self.author, self.qa, bill, "BOM", "Product or BOM")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("BOM", bill)
		doc.update({"is_active": 0, "is_default": 0, CHANGE_REQUEST: change})
		stage_reason("BOM", bill, "update_after_submit", "Product line closed")
		doc.save()  # the bill re-approved on is switched off
		with self.assertRaisesRegex(frappe.ValidationError, "no Current device master record"):
			dmr.require_current_record(self.work_order(p.item, bill))

	def making_entry(self, item: str, purpose: str = "Manufacture", **values) -> Document:
		"""A stock entry that makes `item` from the product's material, as built before it is posted."""
		return frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": purpose,
				"company": company(),
				"items": [
					{
						"item_code": values.pop("material", None) or item,
						"qty": 1,
						"s_warehouse": store("Stores"),
					},
					{"item_code": item, "qty": 1, "t_warehouse": store("Quarantine"), "is_finished_item": 1},
				],
				**values,
			}
		)

	def test_a_finished_good_is_made_only_on_its_released_work_order(self):
		p = self.product()
		material = p.material
		for purpose in ("Manufacture", "Repack"):
			with (
				self.subTest(purpose=purpose),
				self.assertRaisesRegex(
					frappe.ValidationError,
					"made only on a work order released on its Current device master record",
				),
			):
				dmr.guard_stock_entry(self.making_entry(p.item, purpose, material=material), "before_submit")
		# Not a making entry, or not a finished good made: not held to it.
		dmr.guard_stock_entry(
			self.making_entry(p.item, "Material Receipt", material=material), "before_submit"
		)
		dmr.guard_stock_entry(self.making_entry(material, material=material), "before_submit")
		# Saved as a draft, it is checked when it is submitted.
		dmr.guard_stock_entry(self.making_entry(p.item, material=material), "before_validate")

		record = self.current_dmr(p)
		order = self.work_order(p.item, p.bom).insert()
		dmr.guard_stock_entry(
			self.making_entry(p.item, material=material, work_order=order.name), "before_submit"
		)
		other = self.product()
		with self.assertRaisesRegex(frappe.ValidationError, "made only on a work order of its own"):
			dmr.guard_stock_entry(
				self.making_entry(other.item, material=material, work_order=order.name), "before_submit"
			)
		self.revise_document(p.labelling)  # the record goes out of date: the order makes no more
		self.assertEqual(self.status(record.name), dmr.OUT_OF_DATE)
		with self.assertRaisesRegex(frappe.ValidationError, "no Current device master record"):
			dmr.guard_stock_entry(
				self.making_entry(p.item, material=material, work_order=order.name), "before_submit"
			)

	def test_the_making_guard_runs_on_every_submit(self):
		events = frappe.get_hooks("doc_events")["Stock Entry"]
		guard = "meddevice_suite.qms.device_master_record.guard_stock_entry"
		for event in ("before_validate", "before_submit"):
			with self.subTest(event=event):
				self.assertIn(guard, events[event])

	def test_the_fixture_for_other_modules_records_it_as_approval_leaves_it(self):
		p = self.product()
		name = device_master_record_for_test(p.item)
		self.assertEqual(dmr.current_record(p.item), name)
		self.assertEqual(device_master_record_for_test(p.item), name)  # made once
		self.assertEqual(dmr.require_current_record(self.work_order(p.item, p.bom)), name)
		# Plan D-23: it pins the report in force of the item's file, as the risk fixture leaves it.
		self.assertEqual(risk_report_for_test(p.item), p.report)
		self.assertIn(
			(dmr.RISK_REPORT, p.report),
			[(row.element_type, row.reference_name) for row in frappe.get_doc(dmr.DOCTYPE, name).elements],
		)
		self.assertTrue(pinned_records.in_force(rm.REPORT, p.report))


# --- Permissions and registration ------------------------------------------------------------------


@verifies("URS-QMS-026", "URS-QMS-028", "URS-QMS-029")
class TestPermissionsAndRegistration(DeviceMasterRecordTestCase):
	def test_permissions(self):
		for user in (self.qa, self.supervisor):
			with self.subTest(user=user):
				for ptype in ("read", "write", "create", "print"):
					self.assertTrue(frappe.has_permission(dmr.DOCTYPE, ptype, user=user))
		for user in (self.manager, self.inspector):
			with self.subTest(user=user):
				self.assertTrue(frappe.has_permission(dmr.DOCTYPE, "read", user=user))
				self.assertFalse(frappe.has_permission(dmr.DOCTYPE, "write", user=user))
				self.assertFalse(frappe.has_permission(dmr.DOCTYPE, "create", user=user))
		self.assertFalse(frappe.has_permission(dmr.DOCTYPE, "read", user=self.outsider))
		for user in (self.qa, self.supervisor, self.manager, signer("System Administrator")):
			for ptype in ("submit", "cancel", "amend", "delete", "import", "share"):
				with self.subTest(user=user, ptype=ptype):
					self.assertFalse(frappe.has_permission(dmr.DOCTYPE, ptype, user=user))
		self.assertFalse(frappe.has_permission(dmr.DOCTYPE, "write", user=signer("System Administrator")))

	def test_a_reader_cannot_draft_one(self):
		p = self.product()
		with self.assertRaises(frappe.PermissionError):
			self.dmr_draft(p, user=self.manager)

	def test_the_signed_acts_are_declared(self):
		acts = {action: act for (doctype, action), act in SIGNING_ACTIONS.items() if doctype == dmr.DOCTYPE}
		self.assertEqual(
			{action: act.meaning for action, act in acts.items()},
			{"Author": "Authored", "Approve": "Approved", "Return": "Rejected", "Withdraw": "Rejected"},
		)
		for act in acts.values():
			self.assertIn(act.meaning, MEANINGS)
			self.assertTrue(callable(frappe.get_attr(act.perform)))
		self.assertEqual(
			SIGNED_CONTENT[dmr.DOCTYPE], "meddevice_suite.qms.device_master_record.signed_content"
		)

	def test_registered_with_the_shared_services(self):
		self.assertGreaterEqual(retention_years(dmr.DOCTYPE), 10)
		self.assertIn(dmr.DOCTYPE, pinned_records.PIN_OWNERS)
		self.assertEqual(SETTINGS["dmr_reapproval_days"].default, 14)
		for area in ("Quality", "Manufacturing"):
			self.assertIn(dmr.DOCTYPE, [label for label, _type, _to in SIDEBAR_ADDITIONS[area]])
