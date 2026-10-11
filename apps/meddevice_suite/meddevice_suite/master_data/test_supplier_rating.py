"""Supplier rating (module 4, URS-PUR-002): tests derived from the requirement's clauses."""

from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.classes.context_managers import freeze_time
from frappe.utils import add_days, add_months, get_first_day, get_last_day, getdate, today

from meddevice_suite.compliance.test_quality_settings import use_setting
from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.test_lot_status import company, store
from meddevice_suite.lot_control.test_release import ITEM, ReleaseTestCase, certificate
from meddevice_suite.master_data import supplier_rating
from meddevice_suite.master_data.supplier_rating import DUE, RE_EVALUATED, SATISFACTORY
from meddevice_suite.master_data.test_supplier_approval import approved_for, decide, ensure_supplier
from meddevice_suite.pilot.flow import lot_of
from meddevice_suite.qa import verifies
from meddevice_suite.qms.test_document_control import signer
from meddevice_suite.setup.test_stock import approve_for_test

NEXT_MONTH = getdate(add_months(get_first_day(today()), 1))  # this month is then over


class RatingTestCase(ReleaseTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.supplier = f"MDS Rated Supplier {frappe.generate_hash(length=6)}"
		ensure_supplier(self.supplier)
		approve_for_test(ITEM)  # module 1: only approved items are ordered and received
		approved_for(self.supplier, [ITEM])

	def receive(self, qty: float = 6, order_line: str | None = None, posting_date=None):
		"""A receipt from this test's supplier with the lot's certificate, verified by QC."""
		frappe.set_user("Administrator")
		receipt = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": self.supplier,
				"company": company(),
				"set_posting_time": int(bool(posting_date)),
				"posting_date": posting_date or today(),
				"items": [
					{
						"item_code": ITEM,
						"qty": qty,
						"rate": 10,
						"warehouse": store(stores.QUARANTINE),
						**(
							{
								"purchase_order": frappe.db.get_value(
									"Purchase Order Item", order_line, "parent"
								),
								"purchase_order_item": order_line,
							}
							if order_line
							else {}
						),
					}
				],
			}
		).insert()
		receipt.items[0].mds_supplier_lot_no = "SUP-RATE"
		receipt.items[0].mds_certificate = certificate(receipt.name)
		receipt.save()
		receipt.submit()
		receipt = frappe.get_doc("Purchase Receipt", receipt.name)
		self.verified(receipt)
		return receipt, lot_of(receipt.items[0])

	def decided(self, act: str) -> str:
		receipt, lot = self.receive()
		self.inspected(receipt, lot, accept=act == "Release")
		self.decide(lot, act)
		return lot

	def order_line(self, qty: float, required_by) -> str:
		"""An order line required by the day, ordered by Purchase and approved by Management."""
		frappe.set_user(self.purchase)
		order = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": self.supplier,
				"company": company(),
				"transaction_date": min(
					getdate(add_days(required_by, -10)), getdate(today())
				),  # ordered, never ahead
				"schedule_date": required_by,
				"items": [{"item_code": ITEM, "qty": qty, "rate": 10, "schedule_date": required_by}],
			}
		)
		order.insert()
		apply_workflow(order, "Send for Approval")
		frappe.set_user(signer("Management"))
		apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")
		frappe.set_user("Administrator")
		return order.items[0].name

	def rate(self):
		"""This month's rating, made once the month is over."""
		day = today()
		with freeze_time(NEXT_MONTH):
			return supplier_rating.rate(self.supplier, day)


@verifies("URS-PUR-002")
class TestQuality(RatingTestCase):
	def test_quality_is_the_share_of_its_lots_qa_released(self):
		released = [self.decided("Release") for _ in range(3)]
		rejected = self.decided("Reject")
		rating = self.rate()
		self.assertEqual((rating.lots_released, rating.lots_rejected, rating.quality_percent), (3, 1, 75))
		self.assertEqual(
			sorted((row.batch, row.decision) for row in rating.lots),
			sorted([*((lot, "Release") for lot in released), (rejected, "Reject")]),
		)
		self.assertTrue(all(row.lot_release and row.decided_at for row in rating.lots))
		self.assertEqual(rating.score, 75)  # no order line was due: scored on quality alone

	def test_a_hold_is_not_a_decision_on_the_lot(self):
		receipt, lot = self.receive()
		self.inspected(receipt, lot)
		self.decide(lot, "Hold")
		self.assertIsNone(self.rate())  # nothing decided and nothing due: the month is not rated
		self.inspected(receipt, lot)  # a held lot is released after a new inspection
		self.decide(lot, "Release")
		rating = self.rate()
		self.assertEqual([(row.batch, row.decision) for row in rating.lots], [(lot, "Release")])

	def test_only_this_suppliers_lots_count(self):
		from meddevice_suite.lot_control.test_lot_status import purchase_receive

		mine = self.decided("Release")
		frappe.set_user("Administrator")
		other = purchase_receive(ITEM, 6, submit=False)  # another supplier's lot, decided too
		other.items[0].mds_certificate = certificate(other.name)
		other.save()
		other.submit()
		other = frappe.get_doc("Purchase Receipt", other.name)
		self.verified(other)
		self.inspected(other, lot_of(other.items[0]))
		self.decide(lot_of(other.items[0]), "Release")
		self.assertEqual([row.batch for row in self.rate().lots], [mine])


@verifies("URS-PUR-002")
class TestDelivery(RatingTestCase):
	"""Order lines of last month, which is over: receipts are posted on the days they arrived."""

	def setUp(self):
		super().setUp()
		self.due = getdate(add_days(get_first_day(add_months(today(), -1)), 14))

	def test_delivery_is_the_share_of_lines_received_in_full_by_their_required_date(self):
		on_time = self.order_line(6, self.due)
		self.receive(6, on_time, posting_date=add_days(self.due, -2))
		late = self.order_line(6, self.due)
		self.receive(6, late, posting_date=add_days(self.due, 1))
		partial = self.order_line(6, self.due)
		self.receive(4, partial, posting_date=self.due)
		self.order_line(6, self.due)  # nothing received
		rating = supplier_rating.rate(self.supplier, self.due)
		self.assertEqual((rating.lines_due, rating.lines_on_time, rating.delivery_percent), (4, 1, 25))
		by_line = {row.purchase_order_item: row for row in rating.lines}
		self.assertEqual(by_line[on_time].on_time, 1)
		self.assertEqual((by_line[late].on_time, by_line[late].received_by_required_date), (0, 0))
		self.assertEqual((by_line[partial].on_time, by_line[partial].received_by_required_date), (0, 4))
		self.assertEqual(rating.score, 25)  # no lot decided last month: scored on delivery alone

	def test_goods_returned_to_the_supplier_do_not_count_as_undelivered(self):
		"""Received early, rejected by QA and returned, all before the line was due: the line was
		still delivered in full on time; the rejection counts against quality."""
		due = getdate(get_last_day(today()))  # this month's line, rated once the month is over
		line = self.order_line(6, due)
		receipt, lot = self.receive(6, line)
		self.inspected(receipt, lot, accept=False)
		decision = self.decide(lot, "Reject")  # drafts the return to the supplier
		self.assertTrue(decision.supplier_return)
		frappe.set_user("Administrator")
		back = frappe.get_doc("Purchase Receipt", decision.supplier_return)
		self.assertEqual(back.items[0].purchase_order_item, line)  # the return names the order line
		back.submit()
		rating = self.rate()
		self.assertEqual((rating.lines_due, rating.lines_on_time, rating.lots_rejected), (1, 1, 1))


@verifies("URS-PUR-002")
class TestScoreAndStanding(RatingTestCase):
	def test_the_score_weighs_quality_and_delivery_as_qa_set(self):
		due = getdate(get_last_day(today()))  # this month's, rated once the month is over
		self.decided("Release")
		self.decided("Reject")  # quality 50
		self.order_line(6, due)  # delivery 0
		with use_setting("supplier_rating_quality_weight", 60), freeze_time(add_days(NEXT_MONTH, 5)):
			rating = supplier_rating.rate(self.supplier, due)
		self.assertEqual((rating.quality_weight, rating.score), (60, 30))

	def test_a_score_at_the_threshold_is_satisfactory(self):
		for act in ("Release", "Release", "Release", "Release", "Reject"):
			self.decided(act)  # quality 80
		rating = self.rate()
		self.assertEqual((rating.score, rating.threshold, rating.standing), (80, 80, SATISFACTORY))
		self.assertFalse(frappe.db.exists("Notification Log", {"document_name": rating.name}))

	def test_below_the_threshold_qa_and_purchase_are_alerted_to_re_evaluate(self):
		self.decided("Release")
		self.decided("Reject")
		rating = self.rate()
		self.assertEqual((rating.score, rating.standing), (50, DUE))
		alerted = set(
			frappe.get_all(
				"Notification Log",
				filters={"document_type": "Supplier Rating", "document_name": rating.name},
				pluck="for_user",
			)
		)
		self.assertIn(self.qa, alerted)
		self.assertIn(self.purchase, alerted)
		self.assertNotIn(self.stores_user, alerted)

	def test_qas_next_signed_decision_on_the_supplier_closes_the_re_evaluation(self):
		self.decided("Reject")
		rating = self.rate()  # made as the month ends
		self.assertEqual(rating.standing, DUE)
		frappe.set_user("Administrator")  # who sets suppliers up in the tests
		other = f"MDS Other Rated Supplier {frappe.generate_hash(length=6)}"
		ensure_supplier(other)
		with freeze_time(add_days(NEXT_MONTH, 3)):  # QA re-evaluates afterwards
			frappe.set_user(self.qa)
			decide(other, [ITEM])  # another supplier's decision closes nothing here
			rating.reload()
			self.assertEqual((rating.standing, rating.re_evaluation), (DUE, None))
			approval = decide(
				self.supplier, [ITEM], act="Approve Conditionally", valid_until=add_days(NEXT_MONTH, 60)
			)
		rating.reload()
		self.assertEqual((rating.standing, rating.re_evaluation), (RE_EVALUATED, approval))

	def test_the_supplier_form_shows_its_ratings(self):
		self.decided("Reject")
		rating = self.rate()
		supplier = frappe.get_doc("Supplier", self.supplier)
		supplier.run_method("onload")
		shown = supplier.get_onload().supplier_ratings
		self.assertEqual([(row.name, row.standing) for row in shown], [(rating.name, DUE)])


@verifies("URS-PUR-002")
class TestTheRecord(RatingTestCase):
	def test_a_month_is_rated_once_and_only_once_it_is_over(self):
		self.decided("Release")
		with self.assertRaisesRegex(frappe.ValidationError, "once it is over"):
			supplier_rating.rate(self.supplier, today())
		first = self.rate()
		self.assertEqual(self.rate().name, first.name)
		with freeze_time(NEXT_MONTH):
			supplier_rating.rate_finished_months()  # the daily job: nothing new to rate
		self.assertEqual(frappe.db.count("Supplier Rating", {"supplier": self.supplier}), 1)

	def test_the_daily_job_rates_finished_months_it_has_not_rated(self):
		self.decided("Reject")
		with freeze_time(add_months(NEXT_MONTH, 2)):  # the job did not run for two months
			supplier_rating.rate_finished_months()
		rating = frappe.get_last_doc("Supplier Rating", filters={"supplier": self.supplier})
		self.assertEqual((rating.period, rating.standing), (f"{getdate(today()):%Y-%m}", DUE))

	def test_nobody_makes_changes_cancels_or_deletes_a_rating(self):
		self.decided("Reject")
		rating = self.rate()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": "Supplier Rating", "supplier": self.supplier, "period": "2020-01"}
			).insert(ignore_permissions=True)
		doc = frappe.get_doc("Supplier Rating", rating.name)
		doc.score = 99
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("Supplier Rating", rating.name).cancel()
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.delete_doc("Supplier Rating", rating.name, ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Supplier Rating", rating.name, "score"), rating.score)

	def test_qa_and_purchase_read_ratings_and_nobody_creates_one(self):
		for user in (self.qa, self.purchase):
			self.assertTrue(frappe.has_permission("Supplier Rating", "read", user=user))
		# The Administrator holds every permission in Frappe; the record itself refuses it
		# (test_nobody_makes_changes_cancels_or_deletes_a_rating).
		for user in (self.qa, self.purchase):
			for ptype in ("create", "write", "submit", "cancel", "delete"):
				with self.subTest(user=user, ptype=ptype):
					self.assertFalse(frappe.has_permission("Supplier Rating", ptype, user=user))
		self.assertFalse(frappe.has_permission("Supplier Rating", "read", user=self.stores_user))


@verifies("URS-PUR-002")
class TestNoScorecard(RatingTestCase):
	def test_erpnexts_scorecard_is_not_used(self):
		frappe.set_user("Administrator")
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier Rating"):
			frappe.get_doc({"doctype": "Supplier Scorecard", "supplier": self.supplier}).insert()
