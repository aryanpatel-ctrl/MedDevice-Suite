import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.esign import verify_signatures
from meddevice_suite.compliance.evidence_files import is_evidence
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.receipt import guard_incoming_inspection, receipt_line_of
from meddevice_suite.lot_control.test_lot_status import company, lot_item, store
from meddevice_suite.master_data import supplier_approval
from meddevice_suite.master_data.doctype.supplier_approval.supplier_approval import on_doctype_update
from meddevice_suite.master_data.supplier_approval import (
	APPROVED,
	CERTIFICATE_ALERT,
	CONDITIONAL,
	NOT_APPROVED,
	check_full_inspection,
	current_approval,
	send_certificate_alerts,
)
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.install import apply_configuration

SUPPLIER = "MDS Approval Test Supplier"
OTHER_SUPPLIER = "MDS Approval Other Supplier"
ITEM = "MDS-SA-RAW"
OTHER_ITEM = "MDS-SA-OTHER"


def certificate_file(name: str = "ISO 13485 certificate (test)") -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{frappe.generate_hash(length=8)}.txt",
				"content": name,
				"is_private": 1,
			}
		)
		.insert(ignore_permissions=True)
		.file_url
	)


def draft_approval(supplier: str, items: list[str], **values) -> frappe.model.document.Document:
	return new_approval(supplier, items, **values).insert()


def new_approval(supplier: str, items: list[str], **values) -> frappe.model.document.Document:
	values.setdefault("certificate_number", "QMS-12345")
	values.setdefault("certificate_expiry", add_days(today(), 365))
	values.setdefault("certificate", certificate_file())
	return frappe.get_doc(
		{
			"doctype": "Supplier Approval",
			"supplier": supplier,
			"items": [{"item_code": item} for item in items],
			**values,
		}
	)


def decide(supplier: str, items: list[str], act: str = "Approve", reason: str = "Audited", **values) -> str:
	"""QA's signed decision on the supplier; returns the record. Run as a QA user."""
	doc = draft_approval(supplier, items, **values)
	sign_action("Supplier Approval", doc.name, act, PASSWORD, reason)
	return doc.name


def approved_for(supplier: str, items: list[str]) -> str:
	"""For tests that place purchase orders: QA's approval of the supplier for `items` (and the
	items it was already approved for), signed by a QA user; the acting user is kept."""
	approval = current_approval(supplier)
	if approval and approval.decision == APPROVED and set(items) <= approval.item_codes:
		return approval.name
	already = approval.item_codes if approval and approval.decision == APPROVED else frozenset()
	previous = frappe.session.user
	frappe.set_user(signer("QA Manager"))
	try:
		return decide(supplier, sorted(set(items) | already), reason="Approved for the test")
	finally:
		frappe.set_user(previous)


def ensure_supplier(name: str) -> None:
	if not frappe.db.exists("Supplier", name):
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": name, "supplier_group": "Raw Material"}
		).insert()


def purchase_order(supplier: str = SUPPLIER, items: tuple[str, ...] = (ITEM,), **values):
	return frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": supplier,
			"company": company(),
			"transaction_date": today(),
			"schedule_date": add_days(today(), 7),
			"items": [
				{"item_code": item, "qty": 10, "rate": 5, "schedule_date": add_days(today(), 7)}
				for item in items
			],
			**values,
		}
	)


class SupplierApprovalTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		for name in (SUPPLIER, OTHER_SUPPLIER):
			ensure_supplier(name)
		lot_item(ITEM)
		lot_item(OTHER_ITEM)
		cls.qa = signer("QA Manager")
		cls.purchase = signer("Purchase")
		cls.qc = signer("QC Inspector")
		cls.management = signer("Management")

	def setUp(self):
		frappe.set_user(self.qa)

	def placed(self, order) -> str:
		"""Purchase sends the order for approval and Management approves it (module 48)."""
		frappe.set_user(self.purchase)
		order = apply_workflow(order.insert(), "Send for Approval")
		frappe.set_user(self.management)
		return apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve").name

	def tearDown(self):
		frappe.set_user("Administrator")


@verifies("URS-PUR-001")
class TestDecision(SupplierApprovalTestCase):
	def test_qa_approves_a_supplier_for_its_items_with_a_signature(self):
		name = decide(SUPPLIER, [ITEM], reason="Audited on site; certificate checked")
		doc = frappe.get_doc("Supplier Approval", name)
		self.assertEqual((doc.docstatus, doc.decision, doc.decided_by), (1, APPROVED, self.qa))
		self.assertEqual(doc.reason, "Audited on site; certificate checked")
		self.assertTrue(verify_signatures("Supplier Approval", name))
		approval = current_approval(SUPPLIER)
		self.assertEqual((approval.name, approval.decision, approval.item_codes), (name, APPROVED, {ITEM}))

	def test_the_newest_decision_replaces_the_earlier_one(self):
		decide(SUPPLIER, [ITEM])
		latest = decide(SUPPLIER, [], act="Not Approve", reason="Failed the audit")
		self.assertEqual(current_approval(SUPPLIER).name, latest)
		self.assertEqual(current_approval(SUPPLIER).decision, NOT_APPROVED)

	def test_only_qa_prepares_or_decides(self):
		frappe.set_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			draft_approval(SUPPLIER, [ITEM])
		frappe.set_user(self.qa)
		doc = draft_approval(SUPPLIER, [ITEM])
		frappe.set_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			sign_action("Supplier Approval", doc.name, "Approve", PASSWORD, "Purchase decides")

	def test_the_decision_is_set_only_by_signing(self):
		doc = draft_approval(SUPPLIER, [ITEM])
		doc.decision = APPROVED
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc.reload()
		with self.assertRaises(frappe.PermissionError):
			doc.submit()

	def test_permission_overrides_do_not_let_others_decide(self):
		"""Behind the permissions, the record itself refuses anyone but QA and anything unsigned."""
		frappe.set_user(self.purchase)
		with self.assertRaises(frappe.PermissionError):
			new_approval(SUPPLIER, [ITEM]).insert(ignore_permissions=True)
		frappe.set_user(self.qa)
		draft = draft_approval(SUPPLIER, [ITEM])
		frappe.set_user(self.purchase)
		draft.certificate_number = "QMS-CHANGED"
		with self.assertRaises(frappe.PermissionError):
			draft.save(ignore_permissions=True)
		frappe.set_user(self.qa)
		draft.reload()
		draft.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			draft.submit()
		signed = frappe.get_doc("Supplier Approval", decide(SUPPLIER, [ITEM]))
		with self.assertRaises(frappe.PermissionError):
			signed.save(ignore_permissions=True)

	def test_a_decision_needs_a_reason(self):
		doc = draft_approval(SUPPLIER, [ITEM])
		with self.assertRaises(frappe.ValidationError):
			sign_action("Supplier Approval", doc.name, "Approve", PASSWORD, "")

	def test_an_approval_needs_items_and_a_current_certificate(self):
		for values, items in (
			({}, []),
			({"certificate_number": None}, [ITEM]),
			({"certificate_expiry": add_days(today(), -1)}, [ITEM]),
			({}, [ITEM, ITEM]),
		):
			with self.subTest(values=values, items=items):
				doc = draft_approval(SUPPLIER, items, **values)
				with self.assertRaises(frappe.ValidationError):
					sign_action("Supplier Approval", doc.name, "Approve", PASSWORD, "Audited")
				self.assertEqual(frappe.db.get_value("Supplier Approval", doc.name, "docstatus"), 0)

	def test_a_conditional_approval_holds_until_a_day_to_come(self):
		for valid_until in (None, today()):
			with self.subTest(valid_until=valid_until):
				doc = draft_approval(SUPPLIER, [ITEM], valid_until=valid_until)
				with self.assertRaises(frappe.ValidationError):
					sign_action(
						"Supplier Approval", doc.name, "Approve Conditionally", PASSWORD, "New supplier"
					)

	def test_only_a_conditional_approval_has_an_end_date(self):
		doc = draft_approval(SUPPLIER, [ITEM], valid_until=add_days(today(), 30))
		with self.assertRaises(frappe.ValidationError):
			sign_action("Supplier Approval", doc.name, "Approve", PASSWORD, "Audited")

	def test_a_supplier_not_approved_lists_no_items(self):
		doc = draft_approval(SUPPLIER, [ITEM])
		with self.assertRaises(frappe.ValidationError):
			sign_action("Supplier Approval", doc.name, "Not Approve", PASSWORD, "Failed the audit")

	def test_a_decision_is_never_cancelled_or_deleted(self):
		draft = draft_approval(SUPPLIER, [ITEM]).name
		name = decide(SUPPLIER, [ITEM])
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Supplier Approval", name).cancel()
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("Supplier Approval", draft, ignore_permissions=True)
		# A signed decision is refused by Frappe already: a submitted record is never deleted.
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.delete_doc("Supplier Approval", name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Supplier Approval", name))

	def test_the_decided_certificate_is_protected_evidence(self):
		name = decide(SUPPLIER, [ITEM])
		self.assertTrue(is_evidence(frappe.db.get_value("Supplier Approval", name, "certificate")))

	def test_the_supplier_form_shows_the_decision_in_force(self):
		frappe.set_user("Administrator")
		ensure_supplier("MDS Approval Fresh Supplier")
		supplier = frappe.get_doc("Supplier", "MDS Approval Fresh Supplier")
		supplier.run_method("onload")
		self.assertIsNone(supplier.get_onload().supplier_approval)
		frappe.set_user(self.qa)
		name = decide(SUPPLIER, [ITEM, OTHER_ITEM])
		supplier = frappe.get_doc("Supplier", SUPPLIER)
		supplier.run_method("onload")
		shown = supplier.get_onload().supplier_approval
		self.assertEqual(
			(shown.name, shown.item_codes, shown.ended), (name, sorted([ITEM, OTHER_ITEM]), False)
		)

	def test_the_decision_in_force_is_found_by_an_index(self):
		on_doctype_update()
		indexed = frappe.db.sql("SHOW INDEX FROM `tabSupplier Approval`", as_dict=True)
		self.assertIn("supplier", {row.Column_name for row in indexed if row.Seq_in_index == 1})


@verifies("URS-PUR-001")
class TestPurchaseOrder(SupplierApprovalTestCase):
	def order_as_purchase(self, **values):
		frappe.set_user(self.purchase)
		return purchase_order(**values).insert()

	def test_an_order_from_an_approved_supplier_names_the_approval(self):
		name = decide(SUPPLIER, [ITEM])
		order = self.order_as_purchase()
		self.assertEqual(order.mds_supplier_approval, name)
		self.assertFalse(order.mds_conditional_reason)

	def test_an_order_from_a_supplier_without_a_decision_is_refused(self):
		frappe.set_user("Administrator")
		ensure_supplier("MDS Approval Never Decided")
		with self.assertRaisesRegex(frappe.ValidationError, "not approved by QA"):
			self.order_as_purchase(supplier="MDS Approval Never Decided")

	def test_an_order_from_a_supplier_not_approved_is_refused(self):
		decide(OTHER_SUPPLIER, [], act="Not Approve", reason="Failed the audit")
		with self.assertRaisesRegex(frappe.ValidationError, "not approved by QA"):
			self.order_as_purchase(supplier=OTHER_SUPPLIER)

	def test_an_item_the_supplier_is_not_approved_for_is_refused(self):
		decide(SUPPLIER, [ITEM])
		with self.assertRaisesRegex(frappe.ValidationError, OTHER_ITEM):
			self.order_as_purchase(items=(ITEM, OTHER_ITEM))

	def test_a_conditional_supplier_needs_a_reason_on_each_order(self):
		decide(SUPPLIER, [ITEM], act="Approve Conditionally", valid_until=add_days(today(), 60))
		with self.assertRaisesRegex(frappe.ValidationError, "approved only conditionally"):
			self.order_as_purchase()
		order = self.order_as_purchase(mds_conditional_reason="Only source of this grade this month")
		self.assertEqual(order.mds_conditional_reason, "Only source of this grade this month")

	def test_an_ended_conditional_approval_refuses_the_order(self):
		name = decide(SUPPLIER, [ITEM], act="Approve Conditionally", valid_until=add_days(today(), 10))
		# As if the day had passed: the condition ends, and nothing else changed.
		frappe.db.set_value("Supplier Approval", name, "valid_until", add_days(today(), -1))
		with self.assertRaisesRegex(frappe.ValidationError, "ended on"):
			self.order_as_purchase(mds_conditional_reason="Urgent")

	def test_an_expired_certificate_refuses_the_order(self):
		name = decide(SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 10))
		# As if the days had passed: the certificate expires, nothing else changed.
		frappe.db.set_value("Supplier Approval", name, "certificate_expiry", add_days(today(), -1))
		with self.assertRaisesRegex(frappe.ValidationError, "certificate of supplier .* expired"):
			self.order_as_purchase()

	def test_goods_received_without_an_order_come_only_from_an_approved_supplier(self):
		decide(OTHER_SUPPLIER, [], act="Not Approve", reason="Failed the audit")
		frappe.set_user("Administrator")
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": OTHER_SUPPLIER,
				"company": company(),
				"items": [{"item_code": ITEM, "qty": 1, "rate": 5, "warehouse": store(stores.QUARANTINE)}],
			}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved by QA"):
			receipt.insert()
		frappe.set_user(self.qa)
		decide(SUPPLIER, [ITEM])
		frappe.set_user("Administrator")
		receipt.supplier = SUPPLIER
		receipt.insert()  # approved for the item: received

	def test_the_approval_is_checked_again_when_the_order_is_approved(self):
		decide(SUPPLIER, [ITEM])
		order = apply_workflow(self.order_as_purchase(), "Send for Approval")
		frappe.set_user(self.qa)
		decide(SUPPLIER, [], act="Not Approve", reason="Certificate withdrawn")
		frappe.set_user(self.management)
		with self.assertRaisesRegex(frappe.ValidationError, "not approved by QA"):
			apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")


@verifies("URS-PUR-001")
class TestFullInspection(SupplierApprovalTestCase):
	def receive(self, supplier: str, order: str | None = None) -> frappe.model.document.Document:
		frappe.set_user("Administrator")
		row = {"item_code": ITEM, "qty": 10, "rate": 5, "warehouse": store(stores.QUARANTINE)}
		if order:
			order_row = frappe.get_doc("Purchase Order", order).items[0]
			row.update(purchase_order=order, purchase_order_item=order_row.name)
		receipt = frappe.get_doc(
			{"doctype": "Purchase Receipt", "supplier": supplier, "company": company(), "items": [row]}
		)
		receipt.insert()
		receipt.submit()
		return frappe.get_doc("Purchase Receipt", receipt.name)

	def conditional_order(self) -> str:
		decide(SUPPLIER, [ITEM], act="Approve Conditionally", valid_until=add_days(today(), 60))
		return self.placed(purchase_order(mds_conditional_reason="Second source qualification"))

	def inspection(self, lot: str, receipt: str, sample_size: float):
		return frappe._dict(
			doctype="Quality Inspection",
			inspection_type="Incoming",
			batch_no=lot,
			item_code=ITEM,
			reference_type="Purchase Receipt",
			reference_name=receipt,
			sample_size=sample_size,
			readings=[],
		)

	def test_lots_ordered_under_a_conditional_approval_are_inspected_in_full(self):
		receipt = self.receive(SUPPLIER, self.conditional_order())
		self.assertEqual(receipt.items[0].mds_full_inspection, 1)
		lot = lot_of(receipt.items[0])
		line = receipt_line_of(lot)
		self.assertEqual((line.full_inspection, line.quantity), (1, 10))
		frappe.db.set_value("Purchase Receipt Item", receipt.items[0].name, "mds_certificate_verified", 1)
		with self.assertRaisesRegex(frappe.ValidationError, "inspected in full"):
			guard_incoming_inspection(self.inspection(lot, receipt.name, 5))
		check_full_inspection(self.inspection(lot, receipt.name, 10), line)  # the whole lot: accepted

	def test_a_received_lot_is_inspected_only_on_its_receipt(self):
		"""Inspected on anything else, the receipt's rules (certificate, full inspection) would
		not apply; such an inspection is refused and never releases the lot."""
		receipt = self.receive(SUPPLIER, self.conditional_order())
		lot = lot_of(receipt.items[0])
		elsewhere = self.inspection(lot, receipt.name, 1)
		elsewhere.reference_type, elsewhere.reference_name = "Stock Entry", "MAT-STE-ANY"
		with self.assertRaisesRegex(
			frappe.ValidationError, "its incoming inspection is made on that receipt"
		):
			guard_incoming_inspection(elsewhere)

	def test_lots_from_a_supplier_approved_without_condition_are_sampled(self):
		decide(SUPPLIER, [ITEM])
		receipt = self.receive(SUPPLIER)
		self.assertEqual(receipt.items[0].mds_full_inspection, 0)
		self.assertEqual(receipt_line_of(lot_of(receipt.items[0])).full_inspection, 0)


@verifies("URS-PUR-001")
class TestCertificateAlert(SupplierApprovalTestCase):
	def alerts_for(self, name: str) -> int:
		return frappe.db.count(
			"Notification Log", {"document_type": "Supplier Approval", "document_name": name}
		)

	def test_qa_and_purchase_are_alerted_once_30_days_before_the_certificate_expires(self):
		name = decide(SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 30))
		later = decide(OTHER_SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 31))
		frappe.set_user("Administrator")
		send_certificate_alerts()
		send_certificate_alerts()
		self.assertTrue(frappe.db.get_value("Supplier Approval", name, "certificate_alerted"))
		recipients = set(
			frappe.get_all(
				"Notification Log",
				filters={"document_type": "Supplier Approval", "document_name": name},
				pluck="for_user",
			)
		)
		self.assertTrue({self.qa, self.purchase} <= recipients)
		self.assertEqual(self.alerts_for(name), len(recipients))  # once each
		self.assertEqual(self.alerts_for(later), 0)
		self.assertTrue(verify_signatures("Supplier Approval", name))

	def test_a_decision_replaced_by_a_newer_one_is_not_alerted(self):
		old = decide(SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 10))
		decide(SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 400))
		frappe.set_user("Administrator")
		send_certificate_alerts()
		self.assertEqual(self.alerts_for(old), 0)

	def test_a_failed_alert_is_logged_and_retried_the_next_day(self):
		name = decide(SUPPLIER, [ITEM], certificate_expiry=add_days(today(), 5))
		frappe.set_user("Administrator")
		notification = frappe.get_doc("Notification", CERTIFICATE_ALERT)

		def fail(doc):
			raise RuntimeError("mail server down")

		notification.send = fail
		supplier_approval.alert(notification, name)
		self.assertFalse(frappe.db.get_value("Supplier Approval", name, "certificate_alerted"))
