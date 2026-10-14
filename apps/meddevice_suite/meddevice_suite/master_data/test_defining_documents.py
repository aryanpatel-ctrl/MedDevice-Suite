"""Masters linked with the controlled documents that define them (plan D-34): the guardrail that holds
every master with an approval act to the rule, from the declared list
(master_data/defining_documents.py), so that a master given such a link later is held to it too."""

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.signing import SIGNING_ACTIONS
from meddevice_suite.master_data.defining_documents import (
	CONTROLLED_DOCUMENT,
	DefiningDocument,
	defined_masters,
)
from meddevice_suite.master_data.test_routing import document
from meddevice_suite.master_data.versioning import MASTERS
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration


def master(doctype: str, defining: DefiningDocument, name: str | None) -> frappe.model.document.Document:
	"""A new master of the type that needs its document (an item of a type that does), naming `name`."""
	doc = frappe.new_doc(doctype)
	doc.name = f"MDS-DEFINED-{frappe.generate_hash(length=6)}"  # its specifications are looked up by it
	if defining.required_when:
		field, values = defining.required_when
		doc.set(field, values[0])
	doc.set(defining.field, name)
	return doc


@verifies("URS-MST-016", "URS-MST-015", "URS-MST-012")
class TestEveryMasterIsApprovedOnlyWithItsDocumentInEffect(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()

	def test_every_link_of_a_master_with_an_approval_to_a_controlled_document_is_declared(self):
		declared = defined_masters()
		for doctype in MASTERS:
			if (doctype, "Approve") not in SIGNING_ACTIONS:
				continue
			links = [
				df.fieldname
				for df in frappe.get_meta(doctype).fields
				if df.fieldtype == "Link" and df.options == CONTROLLED_DOCUMENT
			]
			with self.subTest(doctype=doctype):
				if links:
					self.assertIn(doctype, declared, "a master naming a controlled document, not declared")
					self.assertEqual(links, [declared[doctype][0].field])
		for doctype in declared:
			with self.subTest(declared=doctype):
				self.assertIn((doctype, "Approve"), SIGNING_ACTIONS)
				self.assertIn(doctype, MASTERS)

	def test_each_is_refused_approval_while_its_document_is_not_in_effect(self):
		all_types = frappe.get_meta(CONTROLLED_DOCUMENT).get_field("document_type").options.split("\n")
		for doctype, (defining, missing_for_approval) in defined_masters().items():
			for status in ("Draft", "Obsolete"):
				with self.subTest(doctype=doctype, status=status):
					name = document(defining.types[0], status)
					lacks = missing_for_approval(master(doctype, defining, name))
					self.assertTrue(
						any(name in what and "in effect" in what for what in lacks),
						f"{name} {status}: {lacks}",
					)
			with self.subTest(doctype=doctype, document="of another type"):
				other_type = next(t for t in all_types if t and t not in defining.types)
				name = document(other_type)
				lacks = missing_for_approval(master(doctype, defining, name))
				self.assertTrue(any(name in what and "as its document" in what for what in lacks), lacks)
			with self.subTest(doctype=doctype, document="none"):
				lacks = missing_for_approval(master(doctype, defining, None))
				self.assertIn(f"its {defining.what}", lacks)
			for document_type in defining.types:
				with self.subTest(doctype=doctype, document="in effect", document_type=document_type):
					name = document(document_type)
					lacks = missing_for_approval(master(doctype, defining, name))
					self.assertFalse([what for what in lacks if name in what or defining.what in what], lacks)
