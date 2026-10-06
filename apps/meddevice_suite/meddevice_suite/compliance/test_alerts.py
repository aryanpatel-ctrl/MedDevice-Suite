from unittest.mock import patch

import frappe
from frappe.email.doctype.notification.notification import evaluate_alert
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.alerts import ALERTS
from meddevice_suite.compliance.test_approvals import ApprovalTestCase
from meddevice_suite.qa import verifies
from meddevice_suite.setup.install import apply_configuration
from meddevice_suite.setup.test_roles import user_with_profile

LOT_ITEM = "MDS-ALERT-LOT-ITEM"


def alerts_for(user: str, doctype: str, name: str) -> list[str]:
	return frappe.get_all(
		"Notification Log",
		filters={"for_user": user, "document_type": doctype, "document_name": name},
		pluck="subject",
		order_by="creation asc",
	)


def failed_alerts() -> int:
	# Frappe logs a failing alert instead of raising it, so a broken template would pass silently.
	return frappe.db.count("Error Log", {"method": "Failed to send Notification"})


def run_daily_alerts() -> None:
	"""Frappe's daily alert run (trigger_daily_alerts), without the commit it makes after each alert."""
	for name in frappe.get_all(
		"Notification", filters={"event": ("in", ("Days Before", "Days After")), "enabled": 1}, pluck="name"
	):
		alert = frappe.get_doc("Notification", name)
		for doc in alert.get_documents_for_today():
			evaluate_alert(doc, alert, alert.event)


def ensure_lot_item() -> None:
	if not frappe.db.exists("Item", LOT_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": LOT_ITEM,
				"item_name": "Foley catheter 16 Fr",
				"item_group": "Products",
				"stock_uom": "Nos",
				"gst_hsn_code": "90183910",  # catheters (for urine, stool)
				"has_batch_no": 1,
				"has_expiry_date": 1,
			}
		).insert()


class AlertTestCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		failures = failed_alerts()
		self.addCleanup(lambda: self.assertEqual(failed_alerts(), failures, "an alert failed to send"))


@verifies("URS-CMP-027")
class TestLotExpiryAlerts(AlertTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		apply_configuration()
		# Batch tracking is part of the standard-flow configuration (pilot, task S2).
		frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
		ensure_lot_item()
		cls.stores = user_with_profile("Stores")
		cls.qa = user_with_profile("QA Manager")
		cls.sales = user_with_profile("Sales")

	def lot(self, expires_in_days: int, stock: float = 120, disabled: int = 0) -> str:
		lot = frappe.get_doc(
			{
				"doctype": "Batch",
				"batch_id": f"LOT-{frappe.generate_hash(length=8).upper()}",
				"item": LOT_ITEM,
				"expiry_date": add_days(today(), expires_in_days),
				"disabled": disabled,
			}
		).insert()
		# Stock is kept on the lot by ERPNext's stock postings; set directly for the test.
		lot.db_set("batch_qty", stock, update_modified=False)
		return lot.name

	def test_stores_and_qa_are_warned_30_days_before_expiry(self):
		lot = self.lot(30)

		run_daily_alerts()

		for user in (self.stores, self.qa):
			with self.subTest(user=user):
				self.assertEqual(len(alerts_for(user, "Batch", lot)), 1)
				self.assertIn(lot, alerts_for(user, "Batch", lot)[0])
		self.assertEqual(alerts_for(self.sales, "Batch", lot), [])

	def test_the_alert_says_what_to_do(self):
		lot = self.lot(30)

		run_daily_alerts()

		description = frappe.get_all(
			"Notification Log", filters={"for_user": self.stores, "document_name": lot}, pluck="description"
		)[0]
		self.assertIn(lot, description)
		self.assertIn("still in stock", description)

	def test_the_daily_alert_run_is_scheduled(self):
		daily = frappe.get_hooks("scheduler_events").get("daily", [])

		self.assertIn("frappe.email.doctype.notification.notification.trigger_daily_alerts", daily)

	def test_stores_and_qa_are_alerted_on_the_day_of_expiry(self):
		lot = self.lot(0)

		run_daily_alerts()

		self.assertIn("expires today", alerts_for(self.stores, "Batch", lot)[0])
		self.assertIn("expires today", alerts_for(self.qa, "Batch", lot)[0])

	def test_other_days_raise_no_alert(self):
		lot = self.lot(29)

		run_daily_alerts()

		self.assertEqual(alerts_for(self.stores, "Batch", lot), [])

	def test_lots_without_stock_or_disabled_raise_no_alert(self):
		used_up, disabled = self.lot(30, stock=0), self.lot(30, disabled=1)

		run_daily_alerts()

		self.assertEqual(alerts_for(self.stores, "Batch", used_up), [])
		self.assertEqual(alerts_for(self.stores, "Batch", disabled), [])

	def test_the_daily_lookup_by_expiry_date_is_indexed(self):
		indexed = {row.Column_name for row in frappe.db.sql("SHOW INDEX FROM `tabBatch`", as_dict=True)}

		self.assertIn("expiry_date", indexed)


@verifies("URS-CMP-028")
class TestApprovalAlerts(ApprovalTestCase):
	def setUp(self):
		super().setUp()
		failures = failed_alerts()
		self.addCleanup(lambda: self.assertEqual(failed_alerts(), failures, "an alert failed to send"))

	def test_the_approver_is_told_a_purchase_order_is_waiting(self):
		order = self.purchase_order()

		self.act(self.purchase, order, "Send for Approval")

		self.assertEqual(len(alerts_for(self.approver, "Purchase Order", order.name)), 1)
		self.assertEqual(alerts_for(self.purchase, "Purchase Order", order.name), [])

	def test_the_price_approver_is_told_a_quotation_is_waiting(self):
		quotation = self.quotation()

		self.act(self.sales, quotation, "Send for Price Approval")

		self.assertIn(
			"waiting for your price approval", alerts_for(self.approver, "Quotation", quotation.name)[0]
		)

	def test_the_requester_is_told_of_the_approval(self):
		order = self.purchase_order()
		self.act(self.purchase, order, "Send for Approval")

		self.act(self.approver, order, "Approve")

		self.assertEqual(
			alerts_for(self.purchase, "Purchase Order", order.name),
			[f"Purchase order {order.name} was approved"],
		)

	def test_the_requester_is_told_of_a_return_for_revision(self):
		quotation = self.quotation()
		self.act(self.sales, quotation, "Send for Price Approval")

		self.act(self.approver, quotation, "Return for Revision")

		self.assertEqual(
			alerts_for(self.sales, "Quotation", quotation.name),
			[f"Quotation {quotation.name} was returned for revision"],
		)

	def test_creating_a_record_alerts_nobody(self):
		order = self.purchase_order()

		self.assertEqual(frappe.get_all("Notification Log", filters={"document_name": order.name}), [])


@verifies("URS-CMP-029")
class TestAlertsAreDeclared(IntegrationTestCase):
	def test_every_alert_is_installed_standard_and_enabled(self):
		apply_configuration()
		for name in ALERTS:
			with self.subTest(alert=name):
				alert = frappe.db.get_value(
					"Notification",
					name,
					["is_standard", "enabled", "module", "channel", "message_type", "notification_message"],
					as_dict=True,
				)
				self.assertEqual(
					(alert.is_standard, alert.enabled, alert.module, alert.channel, alert.message_type),
					(1, 1, "Compliance", "System Notification", "HTML"),
				)
				# What the site holds is the shipped version (Frappe re-imports only on a new timestamp).
				self.assertTrue(alert.notification_message)

	def test_an_alert_cannot_be_edited_on_a_site(self):
		alert = frappe.get_doc("Notification", "Lot Expiring Soon")
		alert.days_in_advance = 7

		# Standard notifications are editable only in developer mode, which a live site never runs.
		with patch.dict(frappe.conf, {"developer_mode": 0}), self.assertRaises(frappe.ValidationError):
			alert.save()
