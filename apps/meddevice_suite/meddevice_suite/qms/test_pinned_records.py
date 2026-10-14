"""Records pinned at a version (qms/pinned_records.py, shared by modules 39 and 31): which version of
each pinnable record type is the one in force, and whether it may be relied on now."""

import frappe

from meddevice_suite.master_data import master_approval
from meddevice_suite.master_data.test_specification import template
from meddevice_suite.master_data.versioning import VERSION
from meddevice_suite.qa import verifies
from meddevice_suite.qms import pinned_records
from meddevice_suite.qms import risk_management as rm
from meddevice_suite.qms.test_device_master_record import DeviceMasterRecordTestCase


@verifies("URS-QMS-027", "URS-QMS-029")
class TestPinnedRecords(DeviceMasterRecordTestCase):
	def assertPinned(self, doctype: str, name: str, token: str, in_force: bool) -> None:
		with self.subTest(doctype=doctype, name=name):
			self.assertEqual(pinned_records.version_token(doctype, name), token)
			self.assertEqual(pinned_records.in_force(doctype, name), in_force)
			self.assertEqual(pinned_records.version_tokens(doctype, [name, None, name]), {name: token})
			# Read with row locks (a device master record's approval), the same version.
			self.assertEqual(pinned_records.version_tokens(doctype, [name], lock=True), {name: token})
			self.assertEqual(pinned_records.in_force_names(doctype, [name]), {name} if in_force else set())

	def version(self, doctype: str, name: str) -> str:
		return str(frappe.db.get_value(doctype, name, VERSION))

	def report(self, assessment: str) -> str:
		frappe.set_user(self.qa)
		try:
			return (
				frappe.get_doc(
					{
						"doctype": rm.REPORT,
						"risk_assessment": assessment,
						"plan_implemented": 1,
						"plan_review": "Every activity of the plan checked against its record.",
						"overall_conclusion": "All residual risks acceptable.",
						"information_sources": [{"controlled_document": self.plan_document}],
					}
				)
				.insert()
				.name
			)
		finally:
			frappe.set_user("Administrator")

	def test_version_token_each_type(self):
		p = self.product()
		document_revision = frappe.db.get_value("Controlled Document", p.labelling, "current_revision")
		self.assertPinned("Controlled Document", p.labelling, document_revision, True)
		self.assertPinned("Item", p.item, self.version("Item", p.item), True)
		self.assertPinned("BOM", p.bom, f"{p.bom}|1|1|1", True)
		self.assertPinned("Routing", p.routing, self.version("Routing", p.routing), True)
		self.assertPinned("Operation", p.operation, self.version("Operation", p.operation), True)
		for specification in (p.final, p.in_process):
			self.assertPinned(
				"Quality Inspection Template",
				specification,
				self.version("Quality Inspection Template", specification),
				True,
			)

		# A new revision is a new token: the revision now in effect, not the time it was saved.
		self.revise_document(p.labelling)
		revised = frappe.db.get_value("Controlled Document", p.labelling, "current_revision")
		self.assertNotEqual(revised, document_revision)
		self.assertEqual(pinned_records.version_token("Controlled Document", p.labelling), revised)
		self.change_specification(p.final)
		self.assertEqual(
			pinned_records.version_token("Quality Inspection Template", p.final),
			self.version("Quality Inspection Template", p.final),
		)
		with self.assertRaises(frappe.ValidationError):
			pinned_records.version_token("Supplier", "anything")
		self.assertIsNone(pinned_records.version_token("Item", "no such item"))

	def test_in_force_each_type(self):
		p = self.product()
		self.assertFalse(pinned_records.in_force("Controlled Document", self.registered()))
		self.assertFalse(pinned_records.in_force("Item", self.finished_good()))  # in draft
		frappe.db.set_value("Item", p.material, "disabled", 1)
		self.assertFalse(pinned_records.in_force("Item", p.material))

		draft_bill = frappe.get_doc(
			{
				"doctype": "BOM",
				"item": p.item,
				"company": frappe.defaults.get_global_default("company"),
				"quantity": 1,
				"is_default": 0,
				"rm_cost_as_per": "Valuation Rate",
				"items": [{"item_code": p.material, "qty": 2, "rate": 1}],
			}
		)
		frappe.db.set_value("Item", p.material, "disabled", 0)
		draft_bill.insert()
		self.assertPinned("BOM", draft_bill.name, f"{draft_bill.name}|1|0|0", False)

		for doctype, name in (("Routing", p.routing), ("Operation", p.operation)):
			frappe.db.set_value(doctype, name, master_approval.APPROVAL_STATUS, master_approval.DRAFT)
			self.assertFalse(pinned_records.in_force(doctype, name))
		frappe.db.set_value(
			"Routing", p.routing, {master_approval.APPROVAL_STATUS: master_approval.APPROVED, "disabled": 1}
		)
		self.assertFalse(pinned_records.in_force("Routing", p.routing))

		frappe.set_user(self.qa)
		# A template not registered for an item and a stage is not a specification (module 6).
		loose = template(f"Loose template {frappe.generate_hash(length=6)}", None).insert()
		frappe.set_user("Administrator")
		self.assertFalse(pinned_records.in_force("Quality Inspection Template", loose.name))
		# A specification in draft, new or changed since QA approved it, is not relied on (plan D-33).
		frappe.db.set_value(
			"Quality Inspection Template", p.final, master_approval.APPROVAL_STATUS, master_approval.DRAFT
		)
		self.assertFalse(pinned_records.in_force("Quality Inspection Template", p.final))

		self.sign_as(self.qa, "Controlled Document", p.labelling, "Withdraw", "Replaced")
		self.assertFalse(pinned_records.in_force("Controlled Document", p.labelling))

	def test_a_risk_management_report_is_in_force_while_it_is_its_files_report(self):
		assessment = self.approved()
		first = self.report(assessment.name)
		self.assertPinned(rm.REPORT, first, f"{first}|Draft|0", False)
		self.sign_as(self.qa, rm.REPORT, first, "Author")
		self.sign_as(self.qa2, rm.REPORT, first, "Approve")
		self.assertPinned(rm.REPORT, first, f"{first}|Approved|1", True)

		second = self.report(assessment.name)
		self.sign_as(self.qa, rm.REPORT, second, "Author")
		self.sign_as(self.qa2, rm.REPORT, second, "Approve")
		self.assertPinned(rm.REPORT, first, f"{first}|Superseded|1", False)
		self.assertPinned(rm.REPORT, second, f"{second}|Approved|1", True)

		# A new approved revision of the analysis leaves the file without a report in force.
		self.approved(self.revise(assessment.risk_management_file))
		self.assertFalse(pinned_records.in_force(rm.REPORT, second))

	def test_owners_and_records_changed_past_their_hooks(self):
		self.assertEqual(set(pinned_records.PIN_OWNERS), {"Risk Management File", "Device Master Record"})
		for owner in pinned_records.PIN_OWNERS.values():
			self.assertTrue(callable(frappe.get_attr(owner.pins)))
			self.assertTrue(callable(frappe.get_attr(owner.react)))
		with self.assertRaises(frappe.ValidationError):
			pinned_records.records_changed("Supplier", ["anything"])
		pinned_records.records_changed(rm.REPORT, [None, ""])  # nothing to look at
