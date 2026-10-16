"""Customer licences (module 5, URS-MST-008): tests derived from the requirement's clauses."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.esign import SigningRefused, verify_signatures
from meddevice_suite.compliance.signing import sign_action
from meddevice_suite.compliance.test_esign import PASSWORD, reset_lockout
from meddevice_suite.lot_control.test_lot_status import company
from meddevice_suite.master_data import customer_licence
from meddevice_suite.master_data.customer_licence import (
	DOCTYPE,
	EXEMPT_FIELD,
	REJECTED,
	VERIFIED,
	WITHDRAWN,
	send_expiry_alerts,
)
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_change_control import signer
from meddevice_suite.setup.test_stock import approve_for_test, stock_item

REFUSED = (frappe.ValidationError, frappe.PermissionError)
LICENCE_TYPE = "Registration for the sale of medical devices (tests)"
PRODUCT = "MDS-CL-PRODUCT"


class CustomerLicenceTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.sales = signer("Sales")
		cls.qa = signer("QA Manager")
		cls.stores = signer("Stores")
		if not frappe.db.exists(customer_licence.TYPE_DOCTYPE, LICENCE_TYPE):
			frappe.get_doc({"doctype": customer_licence.TYPE_DOCTYPE, "licence_type": LICENCE_TYPE}).insert()
		stock_item(PRODUCT, item_group="Products", is_stock_item=0)
		approve_for_test(PRODUCT)

	def tearDown(self):
		frappe.set_user("Administrator")

	def customer(self) -> str:
		"""A new customer, as Sales sets it up."""
		name = f"MDS Licence Customer {frappe.generate_hash(length=8)}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_group": "Commercial",
				"territory": "India",
			}
		).insert()
		return name

	def record(self, customer: str, **values) -> frappe.model.document.Document:
		"""A licence as Sales records it."""
		frappe.set_user(self.sales)
		try:
			return frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"customer": customer,
					"licence_type": LICENCE_TYPE,
					"licence_number": f"MD42-{frappe.generate_hash(length=6)}",
					"issuing_authority": "State Licensing Authority, Gujarat",
					"valid_from": add_days(today(), -365),
					"valid_until": add_days(today(), 365),
					"licence_copy": "/private/files/licence.pdf",
					**values,
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def act(self, name: str, act: str, reason: str = "Licence copy checked against the authority's register"):
		frappe.set_user(self.qa)
		try:
			doctype = "Customer" if act in customer_licence.EXEMPT_ACTS else DOCTYPE
			sign_action(doctype, name, act, PASSWORD, reason)
		finally:
			frappe.set_user("Administrator")

	def licensed(self, **values) -> tuple[str, str]:
		customer = self.customer()
		licence = self.record(customer, **values)
		self.act(licence.name, "Verify")
		return customer, licence.name

	def sales_order(self, customer: str, on: str | None = None) -> frappe.model.document.Document:
		on = on or today()
		frappe.set_user(self.sales)
		try:
			return frappe.get_doc(
				{
					"doctype": "Sales Order",
					"customer": customer,
					"company": company(),
					"transaction_date": on,
					"delivery_date": add_days(on, 7),
					"items": [
						{"item_code": PRODUCT, "qty": 1, "rate": 100, "delivery_date": add_days(on, 7)}
					],
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def submit_order(self, order) -> None:
		frappe.set_user(self.sales)
		try:
			frappe.get_doc("Sales Order", order.name).submit()
		finally:
			frappe.set_user("Administrator")

	def status(self, name: str) -> tuple[int, str | None]:
		return tuple(frappe.db.get_value(DOCTYPE, name, ["docstatus", "status"]))


@verifies("URS-MST-008")
class TestRecording(CustomerLicenceTestCase):
	def test_sales_records_a_licence_awaiting_qa(self):
		licence = self.record(self.customer())
		self.assertEqual(self.status(licence.name), (0, None))

	def test_only_sales_records_a_licence(self):
		customer = self.customer()
		for user in (self.qa, self.stores):
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(frappe.PermissionError):
					frappe.get_doc(
						{
							"doctype": DOCTYPE,
							"customer": customer,
							"licence_type": LICENCE_TYPE,
							"licence_number": "X",
							"issuing_authority": "Y",
							"valid_from": today(),
							"valid_until": add_days(today(), 1),
							"licence_copy": "/private/files/licence.pdf",
						}
					).insert()

	def test_the_status_is_set_only_by_qas_signature(self):
		licence = self.record(self.customer(), status=VERIFIED, reason="Self-verified")
		self.assertEqual(self.status(licence.name), (0, None))  # cleared as it was recorded
		frappe.set_user(self.sales)
		doc = frappe.get_doc(DOCTYPE, licence.name)
		doc.status = VERIFIED
		with self.assertRaisesRegex(frappe.PermissionError, "set by the signature"):
			doc.save()

	def test_a_licence_runs_from_a_day_up_to_a_later_one(self):
		with self.assertRaisesRegex(frappe.ValidationError, "later one"):
			self.record(self.customer(), valid_from=today(), valid_until=add_days(today(), -1))

	def test_type_number_authority_dates_and_copy_are_required(self):
		for field in (
			"licence_type",
			"licence_number",
			"issuing_authority",
			"valid_from",
			"valid_until",
			"licence_copy",
		):
			with self.subTest(field=field):
				with self.assertRaises(frappe.MandatoryError):
					self.record(self.customer(), **{field: None})

	def test_licence_types_are_kept_by_qa(self):
		for user in (self.sales, self.stores):
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(frappe.PermissionError):
					frappe.get_doc(
						{"doctype": customer_licence.TYPE_DOCTYPE, "licence_type": f"T {user}"}
					).insert()
		frappe.set_user(self.qa)
		frappe.get_doc(
			{
				"doctype": customer_licence.TYPE_DOCTYPE,
				"licence_type": f"Kept by QA {frappe.generate_hash(length=6)}",
			}
		).insert()


@verifies("URS-MST-008")
class TestVerification(CustomerLicenceTestCase):
	def test_qa_verifies_a_licence_with_a_signature(self):
		licence = self.record(self.customer())
		self.act(licence.name, "Verify", "Checked against the state register")
		doc = frappe.get_doc(DOCTYPE, licence.name)
		self.assertEqual((doc.docstatus, doc.status, doc.decided_by), (1, VERIFIED, self.qa))
		signatures = frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": DOCTYPE, "reference_name": licence.name},
			fields=["meaning", "action", "signer", "reason"],
		)
		self.assertEqual(
			[(s.meaning, s.action, s.signer, s.reason) for s in signatures],
			[("Verified", "Verify", self.qa, "Checked against the state register")],
		)
		self.assertTrue(verify_signatures(DOCTYPE, licence.name))

	def test_only_qa_verifies_or_rejects(self):
		licence = self.record(self.customer())
		for user in (self.sales, self.stores):
			for act in ("Verify", "Reject"):
				with self.subTest(user=user, act=act):
					frappe.set_user(user)
					with self.assertRaisesRegex(frappe.PermissionError, "Only QA verifies"):
						sign_action(DOCTYPE, licence.name, act, PASSWORD, "Not mine")
		frappe.set_user("Administrator")
		self.assertEqual(self.status(licence.name), (0, None))

	def test_a_wrong_password_or_no_reason_decides_nothing(self):
		licence = self.record(self.customer())
		frappe.set_user(self.qa)
		with self.assertRaises(SigningRefused):
			sign_action(DOCTYPE, licence.name, "Verify", "not the password", "Checked")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action(DOCTYPE, licence.name, "Verify", PASSWORD, " ")
		frappe.set_user("Administrator")
		reset_lockout(self.qa)
		self.assertEqual(self.status(licence.name), (0, None))

	def test_an_expired_licence_is_not_verified(self):
		licence = self.record(
			self.customer(), valid_from=add_days(today(), -400), valid_until=add_days(today(), -1)
		)
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "expired"):
			sign_action(DOCTYPE, licence.name, "Verify", PASSWORD, "Checked")

	def test_a_licence_missing_its_copy_is_not_verified(self):
		licence = self.record(self.customer())
		frappe.db.set_value(DOCTYPE, licence.name, "licence_copy", None)  # as an import might leave it
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "and a copy before it is verified"):
			sign_action(DOCTYPE, licence.name, "Verify", PASSWORD, "Checked")

	def test_only_a_submitted_verification_lets_a_customer_buy(self):
		customer = self.customer()
		licence = self.record(customer)
		frappe.db.set_value(DOCTYPE, licence.name, "status", VERIFIED)  # a draft marked past the rules
		self.assertEqual(customer_licence.licences_valid_on(customer, today()), [])

	def test_a_licence_is_decided_once(self):
		licence = self.record(self.customer())
		self.act(licence.name, "Verify")
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "has been decided"):
			sign_action(DOCTYPE, licence.name, "Reject", PASSWORD, "Again")

	def test_the_signed_act_is_the_only_submit(self):
		licence = self.record(self.customer())
		for user in (self.qa, "Administrator"):
			with self.subTest(user=user):
				frappe.set_user(user)
				with self.assertRaises(frappe.PermissionError):
					frappe.get_doc(DOCTYPE, licence.name).submit()
		with self.subTest(path="validation skipped"):
			frappe.set_user("Administrator")
			doc = frappe.get_doc(DOCTYPE, licence.name)
			doc.flags.ignore_validate = True
			with self.assertRaisesRegex(frappe.PermissionError, "only by QA's signature"):
				doc.submit()
		frappe.set_user("Administrator")
		self.assertEqual(self.status(licence.name), (0, None))

	def test_qa_rejects_a_licence_with_a_signature(self):
		customer = self.customer()
		licence = self.record(customer)
		self.act(licence.name, "Reject", "Copy does not match the register")
		self.assertEqual(self.status(licence.name), (1, REJECTED))
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(self.sales_order(customer))

	def test_a_licence_is_never_cancelled_or_deleted(self):
		_customer, name = self.licensed()
		frappe.set_user(self.qa)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, name).cancel()
		frappe.set_user("Administrator")
		with self.assertRaises(REFUSED):  # Frappe refuses a submitted record first, then this app
			frappe.delete_doc(DOCTYPE, name)
		# The Administrator holds every permission: the app's own rules refuse it too.
		doc = frappe.get_doc(DOCTYPE, name)
		doc.flags.audit_reason = "Cancelled by hand"  # past the audit trail's own question
		with self.assertRaisesRegex(frappe.PermissionError, "never cancelled"):
			doc.cancel()
		draft = self.record(self.customer())
		with self.assertRaisesRegex(frappe.PermissionError, "never deleted"):
			frappe.delete_doc(DOCTYPE, draft.name)

	def test_a_verified_licence_changes_only_by_signature(self):
		_customer, name = self.licensed()
		frappe.set_user("Administrator")
		doc = frappe.get_doc(DOCTYPE, name)
		doc.status = WITHDRAWN
		doc.flags.audit_reason = "Withdrawn by hand"  # past the audit trail's own question
		with self.assertRaisesRegex(frappe.PermissionError, "changed only by QA's signature"):
			doc.save()


@verifies("URS-MST-008")
class TestWithdrawal(CustomerLicenceTestCase):
	def test_qa_withdraws_a_verified_licence_and_sales_stop(self):
		customer, name = self.licensed()
		self.submit_order(self.sales_order(customer))  # licensed: sold to
		self.act(name, "Withdraw", "Suspended by the State Licensing Authority")
		self.assertEqual(self.status(name), (1, WITHDRAWN))
		self.assertEqual(
			frappe.get_last_doc("Electronic Signature", filters={"reference_name": name}).action, "Withdraw"
		)
		self.assertTrue(verify_signatures(DOCTYPE, name))
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(self.sales_order(customer))

	def test_only_a_verified_licence_is_withdrawn_and_only_by_qa(self):
		_customer, name = self.licensed()
		frappe.set_user(self.sales)
		with self.assertRaisesRegex(frappe.PermissionError, "Only QA withdraws"):
			sign_action(DOCTYPE, name, "Withdraw", PASSWORD, "Not mine")
		draft = self.record(self.customer())
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "Only a verified licence"):
			sign_action(DOCTYPE, draft.name, "Withdraw", PASSWORD, "Not verified")
		with self.assertRaisesRegex(frappe.ValidationError, "Say why"):
			sign_action(DOCTYPE, name, "Withdraw", PASSWORD, "")


@verifies("URS-MST-008")
class TestSalesNeedALicence(CustomerLicenceTestCase):
	def test_a_sales_order_is_submitted_only_for_a_licensed_customer(self):
		customer = self.customer()
		order = self.sales_order(customer)  # a draft may be saved
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(order)
		licence = self.record(customer)
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(order)  # recorded, not yet verified
		self.act(licence.name, "Verify")
		self.submit_order(order)
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "docstatus"), 1)

	def test_the_licence_is_valid_on_the_orders_date(self):
		customer, _name = self.licensed(valid_from=add_days(today(), -10), valid_until=add_days(today(), 10))
		for day, allowed in (
			(add_days(today(), -11), False),
			(add_days(today(), -10), True),
			(add_days(today(), 10), True),
			(add_days(today(), 11), False),
		):
			with self.subTest(day=day):
				order = self.sales_order(customer, on=day)
				if allowed:
					self.submit_order(order)
				else:
					with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
						self.submit_order(order)

	def test_a_sales_order_saved_without_validation_is_checked_too(self):
		order = self.sales_order(self.customer())
		frappe.set_user(self.sales)
		doc = frappe.get_doc("Sales Order", order.name)
		doc.flags.ignore_validate = True
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			doc.submit()

	def test_a_delivery_is_made_only_to_a_licensed_customer(self):
		customer = self.customer()
		note = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": customer,
				"company": company(),
				"posting_date": today(),
				"items": [{"item_code": PRODUCT, "qty": 1, "rate": 100}],
			}
		)
		note._action = "submit"
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			customer_licence.guard_delivery_note(note)
		note.is_return = 1  # goods coming back are not a sale
		customer_licence.guard_delivery_note(note)
		note.is_return = 0
		self.act(self.record(customer).name, "Verify")
		customer_licence.guard_delivery_note(note)  # licensed: no refusal

	def delivery_note(self, customer: str, **values) -> frappe.model.document.Document:
		return frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": customer,
				"company": company(),
				"posting_date": today(),
				"items": [{"item_code": PRODUCT, "qty": 1, "rate": 100}],
				**values,
			}
		)

	def test_a_delivery_note_is_submitted_only_for_a_licensed_customer(self):
		"""Through Frappe's own submit, inserted as submitted, and with validation skipped."""
		customer = self.customer()
		note = self.delivery_note(customer).insert()  # a draft may be saved
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			frappe.get_doc("Delivery Note", note.name).submit()
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.delivery_note(customer, docstatus=1).insert()
		skipped = frappe.get_doc("Delivery Note", note.name)
		skipped.flags.ignore_validate = True
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			skipped.submit()
		self.assertEqual(frappe.db.get_value("Delivery Note", note.name, "docstatus"), 0)

	def test_a_backdated_record_does_not_reach_past_an_expired_licence(self):
		customer, name = self.licensed(valid_from=add_days(today(), -60))
		frappe.db.set_value(DOCTYPE, name, "valid_until", add_days(today(), -1))  # time has passed
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(self.sales_order(customer, on=add_days(today(), -5)))
		note = self.delivery_note(customer, posting_date=add_days(today(), -5), set_posting_time=1)
		note._action = "submit"
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			customer_licence.guard_delivery_note(note)

	def test_an_invoice_that_delivers_stock_needs_the_licence(self):
		customer = self.customer()
		invoice = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": customer,
				"company": company(),
				"posting_date": today(),
				"update_stock": 1,
				"items": [{"item_code": PRODUCT, "qty": 1, "rate": 100}],
			}
		)
		invoice.flags.ignore_mandatory = True
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			invoice.insert().submit()
		for doctype in ("Sales Invoice", "POS Invoice"):
			with self.subTest(doctype=doctype):
				doc = frappe.get_doc({"doctype": doctype, "customer": customer, "posting_date": today()})
				doc._action = "submit"
				doc.update_stock = 0  # billing alone delivers nothing
				customer_licence.guard_sales_invoice(doc)
				doc.update_stock = 1
				with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
					customer_licence.guard_sales_invoice(doc)
				doc.is_return = 1  # a credit note takes goods back
				customer_licence.guard_sales_invoice(doc)

	def test_lines_added_to_a_submitted_order_need_the_licence_today(self):
		from erpnext.controllers.accounts_controller import update_child_qty_rate

		customer, name = self.licensed()
		order = self.sales_order(customer)
		self.submit_order(order)
		self.act(name, "Withdraw", "Suspended by the authority")
		order = frappe.get_doc("Sales Order", order.name)
		rows = [
			{
				"docname": row.name,
				"item_code": row.item_code,
				"qty": row.qty + 1,
				"rate": row.rate,
				"delivery_date": row.delivery_date,
				"conversion_factor": 1,
				"idx": row.idx,
			}
			for row in order.items
		]
		frappe.set_user(self.sales)
		frappe.db.savepoint("update_items")  # as the request that is refused is rolled back
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			update_child_qty_rate("Sales Order", frappe.as_json(rows), order.name)
		frappe.db.rollback(save_point="update_items")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Sales Order Item", order.items[0].name, "qty"), 1)

	def test_a_draft_delivery_is_not_checked(self):
		note = frappe.get_doc(
			{"doctype": "Delivery Note", "customer": self.customer(), "posting_date": today()}
		)
		note._action = "save"
		customer_licence.guard_delivery_note(note)


@verifies("URS-MST-008")
class TestBuyingWithoutALicence(CustomerLicenceTestCase):
	def test_qa_decides_with_a_signature_that_a_customer_buys_without_a_licence(self):
		customer = self.customer()
		self.act(customer, "Exempt From Licence", "Government hospital buying for its own use")
		self.assertEqual(frappe.db.get_value("Customer", customer, EXEMPT_FIELD), 1)
		signature = frappe.get_last_doc("Electronic Signature", filters={"reference_name": customer})
		self.assertEqual(
			(signature.action, signature.meaning, signature.signer),
			("Exempt From Licence", "Approved", self.qa),
		)
		self.submit_order(self.sales_order(customer))
		self.act(customer, "Require Licence", "Now buys for resale")
		# Ending the exemption revokes QA's approval of it (11.50(a)(3)).
		signature = frappe.get_last_doc("Electronic Signature", filters={"reference_name": customer})
		self.assertEqual((signature.action, signature.meaning), ("Require Licence", "Rejected"))
		with self.assertRaisesRegex(frappe.ValidationError, "holds no licence verified by QA"):
			self.submit_order(self.sales_order(customer))

	def test_only_qa_decides_it_and_only_by_signature(self):
		customer = self.customer()
		frappe.set_user(self.sales)
		with self.assertRaisesRegex(frappe.PermissionError, "Only QA decides"):
			sign_action("Customer", customer, "Exempt From Licence", PASSWORD, "Mine to decide")
		doc = frappe.get_doc("Customer", customer)
		doc.set(EXEMPT_FIELD, 1)
		with self.assertRaisesRegex(frappe.PermissionError, "decided by QA's signature"):
			doc.save()
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value("Customer", customer, EXEMPT_FIELD))

	def test_a_decision_already_in_force_is_not_signed_again(self):
		customer = self.customer()
		frappe.set_user(self.qa)
		with self.assertRaisesRegex(frappe.ValidationError, "already needs a licence"):
			sign_action("Customer", customer, "Require Licence", PASSWORD, "Again")

	def test_the_signature_binds_the_decision_not_the_addresses(self):
		from meddevice_suite.compliance.esign import is_unchanged_since

		customer = self.customer()
		self.act(customer, "Exempt From Licence", "Hospital for its own use")
		signature = frappe.get_last_doc("Electronic Signature", filters={"reference_name": customer})
		frappe.db.set_value("Customer", customer, "customer_details", "New contact person")  # Sales' edit
		self.assertTrue(is_unchanged_since(signature))
		frappe.db.set_value("Customer", customer, EXEMPT_FIELD, 0)  # past the rules
		self.assertFalse(is_unchanged_since(signature))


@verifies("URS-MST-008")
class TestRenamingAndMerging(CustomerLicenceTestCase):
	def test_a_customer_with_a_decided_licence_or_exemption_keeps_its_name(self):
		licensed, _name = self.licensed()
		exempt = self.customer()
		self.act(exempt, "Exempt From Licence", "Hospital for its own use")
		other = self.customer()
		for customer in (licensed, exempt):
			with self.subTest(customer=customer):
				with self.assertRaisesRegex(frappe.PermissionError, "so it is not renamed"):
					frappe.rename_doc("Customer", customer, f"{customer} renamed")
				with self.assertRaisesRegex(frappe.PermissionError, "is not merged into"):
					frappe.rename_doc("Customer", customer, other, merge=True)
		self.assertTrue(customer_licence.licences_valid_on(licensed, today()))
		self.assertFalse(customer_licence.licences_valid_on(other, today()))

	def test_a_customer_with_no_decision_may_be_renamed(self):
		customer = self.customer()
		self.record(customer)  # a draft licence is not a decision
		renamed = frappe.rename_doc("Customer", customer, f"{customer} renamed")
		self.assertEqual(frappe.db.get_value(DOCTYPE, {"customer": renamed}, "customer"), renamed)


@verifies("URS-MST-008")
class TestExpiryAlert(CustomerLicenceTestCase):
	def alerts_for(self, name: str) -> int:
		return frappe.db.count("Notification Log", {"document_type": DOCTYPE, "document_name": name})

	def test_sales_and_qa_are_alerted_once_before_a_licence_expires(self):
		_customer, name = self.licensed(valid_until=add_days(today(), 30))
		_other, later = self.licensed(valid_until=add_days(today(), 31))
		send_expiry_alerts()
		send_expiry_alerts()
		self.assertTrue(frappe.db.get_value(DOCTYPE, name, "expiry_alerted"))
		recipients = set(
			frappe.get_all(
				"Notification Log",
				filters={"document_type": DOCTYPE, "document_name": name},
				pluck="for_user",
			)
		)
		self.assertTrue({self.qa, self.sales} <= recipients)
		self.assertEqual(self.alerts_for(name), len(recipients))  # once each
		self.assertEqual(self.alerts_for(later), 0)
		from meddevice_suite.compliance.esign import is_unchanged_since

		signature = frappe.get_last_doc("Electronic Signature", filters={"reference_name": name})
		self.assertTrue(is_unchanged_since(signature))  # the alert's bookkeeping is not a change

	@verifies("URS-PLT-012")
	def test_the_alert_follows_quality_settings(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		_customer, inside = self.licensed(valid_until=add_days(today(), 50))
		_other, outside = self.licensed(valid_until=add_days(today(), 70))
		with use_setting("customer_licence_warning_days", 60):
			send_expiry_alerts()
		self.assertGreater(self.alerts_for(inside), 0)
		self.assertEqual(self.alerts_for(outside), 0)

	def test_a_renewed_licence_already_verified_is_not_alerted(self):
		customer, old = self.licensed(valid_until=add_days(today(), 10))
		renewed = self.record(customer, valid_from=add_days(today(), 11), valid_until=add_days(today(), 1000))
		self.act(renewed.name, "Verify")
		send_expiry_alerts()
		self.assertEqual(self.alerts_for(old), 0)

	def test_a_renewal_that_leaves_a_gap_does_not_silence_the_alert(self):
		customer, old = self.licensed(valid_until=add_days(today(), 10))
		later = self.record(customer, valid_from=add_days(today(), 20), valid_until=add_days(today(), 1000))
		self.act(later.name, "Verify")
		send_expiry_alerts()
		self.assertGreater(self.alerts_for(old), 0)

	def test_rejected_or_withdrawn_licences_are_not_alerted(self):
		_customer, withdrawn = self.licensed(valid_until=add_days(today(), 5))
		self.act(withdrawn, "Withdraw", "Suspended")
		rejected = self.record(self.customer(), valid_until=add_days(today(), 5))
		self.act(rejected.name, "Reject", "Copy unreadable")
		send_expiry_alerts()
		self.assertEqual((self.alerts_for(withdrawn), self.alerts_for(rejected.name)), (0, 0))

	def test_a_failed_alert_is_logged_and_retried_the_next_day(self):
		_customer, name = self.licensed(valid_until=add_days(today(), 5))
		notification = frappe.get_doc("Notification", customer_licence.EXPIRY_ALERT)

		def fail(doc):
			raise RuntimeError("mail server down")

		notification.send = fail
		customer_licence.alert(notification, name)
		self.assertFalse(frappe.db.get_value(DOCTYPE, name, "expiry_alerted"))


@verifies("URS-MST-008")
class TestCustomerForm(CustomerLicenceTestCase):
	def onload(self, customer: str) -> dict:
		doc = frappe.get_doc("Customer", customer)
		doc.set("__onload", frappe._dict())
		doc.run_method("onload")
		return doc.get_onload().customer_licence

	def test_the_form_says_whether_the_customer_may_be_sold_to_today(self):
		customer = self.customer()
		self.assertEqual(self.onload(customer), {"exempt": False, "valid": []})
		_c, name = self.licensed()
		self.assertEqual([row.name for row in self.onload(_c)["valid"]], [name])
		self.act(customer, "Exempt From Licence", "Hospital")
		self.assertTrue(self.onload(customer)["exempt"])
