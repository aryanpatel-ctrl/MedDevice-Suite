"""The audit trail's scope stays complete (URS-CMP-041): tests derived from the requirement's clauses.

Found in the foundation review of 2026-10-12: sales orders were not audited, and the scope was a
hand list that nothing compared with the record types the app works with.
"""

import importlib
from collections import defaultdict

import frappe
from frappe.tests import IntegrationTestCase

from meddevice_suite.compliance.audit import should_audit
from meddevice_suite.compliance.audit_reason import requires_reason
from meddevice_suite.compliance.audit_scope import APP, NOT_AUDITED, REGULATED_DOCTYPES
from meddevice_suite.compliance.retention import retention_years
from meddevice_suite.compliance.test_audit import actions_for, because, events_for
from meddevice_suite.master_data.test_customer_licence import CustomerLicenceTestCase
from meddevice_suite.qa import verifies

TABLES = ("Table", "Table MultiSelect")
# The hooks of hooks.py that name record types.
RECORD_TYPE_HOOKS = ("doc_events", "has_permission", "permission_query_conditions")


def touched_record_types() -> dict[str, set[str]]:
	"""Each standard record type this app hooks into, adds fields to or links to from its own record
	types, and where. A child table counts as the record types it is part of, whose audit events
	record its rows."""
	own = set(
		frappe.get_all("DocType", filters={"module": ["in", frappe.get_module_list(APP)]}, pluck="name")
	)
	touched: dict[str, set[str]] = defaultdict(set)

	hooks = importlib.import_module(f"{APP}.hooks")
	for hook in RECORD_TYPE_HOOKS:
		for key in getattr(hooks, hook, {}):
			for doctype in key if isinstance(key, tuple) else (key,):
				if doctype != "*":
					touched[doctype].add(f"hooks.{hook}")
	for field in frappe.get_all(
		"DocField",
		filters={"parent": ["in", sorted(own)], "fieldtype": ["in", ["Link", *TABLES]]},
		fields=["parent", "fieldname", "options"],
	):
		touched[field.options].add(f"{field.parent}.{field.fieldname}")
	# Every custom field of this app is named mds_ (engineering rules, section 4).
	for field in frappe.get_all(
		"Custom Field", filters={"fieldname": ["like", "mds\\_%"]}, fields=["dt", "fieldname"]
	):
		touched[field.dt].add(f"{field.dt}.{field.fieldname}")

	standard = {doctype: where for doctype, where in touched.items() if doctype not in own}
	children = set(
		frappe.get_all("DocType", filters={"name": ["in", sorted(standard)], "istable": 1}, pluck="name")
	)
	for table in (
		*frappe.get_all(
			"DocField",
			filters={"options": ["in", sorted(children) or [""]], "fieldtype": ["in", TABLES]},
			fields=["parent", "options"],
		),
		*frappe.get_all(
			"Custom Field",
			filters={"options": ["in", sorted(children) or [""]], "fieldtype": ["in", TABLES]},
			fields=["dt as parent", "options"],
		),
	):
		standard.setdefault(table.parent, set()).update(
			f"{table.options}: {where}" for where in standard[table.options]
		)
	return {doctype: where for doctype, where in standard.items() if doctype not in children | own}


@verifies("URS-CMP-041")
class TestScopeIsComplete(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.touched = touched_record_types()

	def test_every_standard_record_type_the_app_works_with_is_audited_or_left_out_for_a_reason(self):
		unreviewed = {
			doctype: sorted(where)
			for doctype, where in self.touched.items()
			if doctype not in REGULATED_DOCTYPES and doctype not in NOT_AUDITED
		}

		self.assertEqual(unreviewed, {})

	def test_the_list_of_record_types_left_out_is_reviewed(self):
		for doctype, reason in NOT_AUDITED.items():
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype))
				self.assertTrue(reason.strip())
				self.assertNotIn(doctype, REGULATED_DOCTYPES)
				# An entry nothing touches any more is removed, so the list stays a reviewed one.
				self.assertIn(doctype, self.touched)
				self.assertFalse(should_audit(frappe.new_doc(doctype)))

	def test_the_check_finds_what_the_app_touches(self):
		# Hooked (customer licences), linked from this app's records (lot release), carrying this
		# app's fields (lot status), and through a child table (the receipt's lines).
		for doctype, where in (
			("Sales Order", "hooks.doc_events"),
			("Batch", "Lot Release.batch"),
			("Stock Entry", "Stock Entry.mds_lot_release_transfer"),
			("Purchase Receipt", "Purchase Receipt Item"),
		):
			with self.subTest(doctype=doctype):
				self.assertIn(doctype, self.touched)
				self.assertTrue(any(where in place for place in self.touched[doctype]), self.touched[doctype])


@verifies("URS-CMP-041")
class TestSalesOrdersAreAudited(CustomerLicenceTestCase):
	def test_a_sales_order_is_a_regulated_record_kept_with_the_sales_records(self):
		self.assertIn("Sales Order", REGULATED_DOCTYPES)
		self.assertTrue(should_audit(frappe.new_doc("Sales Order")))
		self.assertTrue(requires_reason("Sales Order"))
		self.assertEqual(retention_years("Sales Order"), 10)

	def test_taking_submitting_and_cancelling_an_order_is_recorded(self):
		customer, _licence = self.licensed()
		order = self.sales_order(customer)
		self.submit_order(order)
		frappe.set_user(self.sales)
		doc = frappe.get_doc("Sales Order", order.name)

		with self.assertRaisesRegex(frappe.ValidationError, "Give a reason to cancel"):
			doc.cancel()
		because(frappe.get_doc("Sales Order", order.name), "Customer withdrew the order").cancel()
		frappe.set_user("Administrator")

		actions = actions_for(doc)
		self.assertEqual(actions[0], "Created")
		self.assertIn("Submitted", actions)
		cancelled = next(
			event for event in events_for("Sales Order", order.name) if event.action == "Cancelled"
		)
		self.assertEqual((cancelled.user, cancelled.reason), (self.sales, "Customer withdrew the order"))
		self.assertIn(["docstatus", 1, 2], cancelled.changes["changed"])
