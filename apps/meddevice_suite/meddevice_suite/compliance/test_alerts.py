from unittest.mock import patch

import frappe
from frappe.email.doctype.notification.notification import Notification
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from meddevice_suite.compliance.alerts import ALERTS, send_expiry_alerts
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
				"mds_item_type": "Finished Good",
			}
		).insert()
	from meddevice_suite.setup.test_stock import approve_for_test

	approve_for_test(LOT_ITEM)  # module 1: its lots are made only for an approved item


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

	def expiry_alerts(self, user: str, lot: str) -> list[str]:
		return alerts_for(user, "Batch", lot)

	def run_alerts(self, on: str | None = None) -> None:
		with patch("meddevice_suite.compliance.alerts.today", return_value=on or today()):
			send_expiry_alerts()

	def test_stores_and_qa_are_warned_30_days_before_expiry(self):
		lot = self.lot(30)

		self.run_alerts()

		for user in (self.stores, self.qa):
			with self.subTest(user=user):
				self.assertEqual(len(self.expiry_alerts(user, lot)), 1)
				self.assertIn(lot, self.expiry_alerts(user, lot)[0])
		self.assertEqual(self.expiry_alerts(self.sales, lot), [])

	@verifies("URS-PLT-012")
	def test_the_warning_follows_quality_settings(self):
		from meddevice_suite.compliance.test_quality_settings import use_setting

		with use_setting("lot_expiry_warning_days", 10):
			outside, inside = self.lot(12), self.lot(9)
			self.run_alerts()
		self.assertEqual(self.expiry_alerts(self.stores, outside), [])
		self.assertEqual(len(self.expiry_alerts(self.stores, inside)), 1)

	def test_the_alert_says_what_to_do(self):
		lot = self.lot(30)

		self.run_alerts()

		description = frappe.get_all(
			"Notification Log", filters={"for_user": self.stores, "document_name": lot}, pluck="description"
		)[0]
		self.assertIn(lot, description)
		self.assertIn("still in stock", description)

	def test_a_lot_is_warned_once_however_often_the_alerts_run(self):
		lot = self.lot(20)

		for day in range(3):
			self.run_alerts(add_days(today(), day))

		self.assertEqual(len(self.expiry_alerts(self.stores, lot)), 1)

	def test_a_lot_received_inside_the_warning_period_is_warned_at_once(self):
		lot = self.lot(5)

		self.run_alerts()

		self.assertEqual(len(self.expiry_alerts(self.stores, lot)), 1)

	def test_a_lot_further_off_is_not_warned_yet(self):
		lot = self.lot(31)

		self.run_alerts()

		self.assertEqual(self.expiry_alerts(self.stores, lot), [])

	def test_a_lot_expiring_today_is_warned_and_alerted_as_expired_the_next_day(self):
		lot = self.lot(0)

		self.run_alerts()
		self.run_alerts(add_days(today(), 1))

		subjects = self.expiry_alerts(self.qa, lot)
		self.assertEqual(len(subjects), 2)
		self.assertIn("expires on", subjects[0])
		self.assertIn("has expired", subjects[1])

	def test_an_expiry_the_alerts_missed_is_still_alerted(self):
		lot = self.lot(-3)

		self.run_alerts()

		subjects = self.expiry_alerts(self.stores, lot)
		self.assertEqual(len(subjects), 1)
		self.assertIn("has expired", subjects[0])

	def test_a_changed_expiry_date_is_alerted_afresh(self):
		lot = self.lot(20)
		self.run_alerts()

		doc = frappe.get_doc("Batch", lot)
		doc.expiry_date = add_days(today(), 25)
		doc.save()
		self.run_alerts()

		self.assertEqual(len(self.expiry_alerts(self.stores, lot)), 2)

	def test_lots_without_stock_disabled_or_without_expiry_raise_no_alert(self):
		used_up, disabled = self.lot(10, stock=0), self.lot(10, disabled=1)
		undated = self.lot(10)
		frappe.db.set_value("Batch", undated, "expiry_date", None)

		self.run_alerts()

		for lot in (used_up, disabled, undated):
			with self.subTest(lot=lot):
				self.assertEqual(self.expiry_alerts(self.stores, lot), [])

	def test_one_lot_failing_does_not_hold_back_the_others(self):
		failing, other = self.lot(10), self.lot(10)
		real = Notification.send

		def send(notification, doc):
			if doc.name == failing:
				raise frappe.ValidationError("template broken")
			return real(notification, doc)

		with patch.object(Notification, "send", send):
			self.run_alerts()

		self.assertEqual(self.expiry_alerts(self.stores, failing), [])
		self.assertEqual(len(self.expiry_alerts(self.stores, other)), 1)
		self.assertTrue(
			frappe.db.exists("Error Log", {"method": f"Expiry alert for lot {failing} could not be sent"})
		)
		self.run_alerts()
		self.assertEqual(len(self.expiry_alerts(self.stores, failing)), 1)

	def test_the_expiry_alerts_run_daily(self):
		self.assertIn(
			"meddevice_suite.compliance.alerts.send_expiry_alerts",
			frappe.get_hooks("scheduler_events").get("daily", []),
		)

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
					(1, 1, ALERTS[name], "System Notification", "HTML"),
				)
				# What the site holds is the shipped version (Frappe re-imports only on a new timestamp).
				self.assertTrue(alert.notification_message)

	def test_an_alert_cannot_be_edited_switched_off_or_deleted_on_a_site(self):
		frappe.set_user(user_with_profile("System Administrator"))
		self.addCleanup(frappe.set_user, "Administrator")
		alert = frappe.get_doc("Notification", "Lot Expiring Soon")

		# Frappe itself allows editing a standard alert once it is switched off, and always in
		# developer mode; neither may change a declared alert.
		with patch.dict(frappe.conf, {"developer_mode": 1}):
			alert.enabled = 0
			with self.assertRaises(frappe.PermissionError):
				alert.save()
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("Notification", "Lot Expiring Soon")

		self.assertEqual(frappe.db.get_value("Notification", "Lot Expiring Soon", "enabled"), 1)

	def test_an_alert_changed_behind_the_applications_back_is_restored_on_migrate(self):
		frappe.db.set_value(
			"Notification", "Purchase Order Awaiting Approval", {"enabled": 0, "subject": "x"}
		)

		apply_configuration()

		self.assertEqual(
			frappe.db.get_value("Notification", "Purchase Order Awaiting Approval", ["enabled", "subject"]),
			(
				1,
				frappe.get_file_json(
					frappe.get_app_path(
						"meddevice_suite",
						"compliance",
						"notification",
						"purchase_order_awaiting_approval",
						"purchase_order_awaiting_approval.json",
					)
				)["subject"],
			),
		)
