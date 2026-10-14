import frappe
from frappe.utils import add_days, getdate, today

from meddevice_suite.compliance.esign import is_unchanged_since, verify_signatures
from meddevice_suite.compliance.signing import sign_action, signing_actions
from meddevice_suite.compliance.test_alerts import alerts_for
from meddevice_suite.compliance.test_esign import PASSWORD
from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.lot_dates import (
	CORRECTED_AT,
	CORRECTED_BY,
	CORRECTION_REASON,
	LotDatesFixed,
	batch_onload,
	has_stock_history,
)
from meddevice_suite.lot_control.test_lot_status import (
	RAW,
	LotTestCase,
	company,
	issue,
	move,
	qty_in,
	receive,
	stock_entry,
	store,
)
from meddevice_suite.qa import verifies
from meddevice_suite.setup.test_stock import approve_for_test

ACT = "Correct Dates"


def dates(lot: str) -> tuple:
	return tuple(
		getdate(value) if value else None
		for value in frappe.db.get_value("Batch", lot, ["manufacturing_date", "expiry_date"])
	)


def signatures(lot: str) -> list[frappe._dict]:
	return frappe.get_all(
		"Electronic Signature",
		filters={"reference_doctype": "Batch", "reference_name": lot},
		fields=["name", "signer", "meaning", "action", "reason"],
		order_by="signed_at asc",
	)


class LotDatesTestCase(LotTestCase):
	def lot_without_stock(self) -> str:
		frappe.set_user("Administrator")
		approve_for_test(RAW)  # module 1: a lot is made only of an approved item
		return frappe.get_doc({"doctype": "Batch", "item": RAW}).insert().name

	def edit(self, lot: str, **values) -> None:
		"""QA edits the lot on its form (QA's job holds Item Manager, which writes lots)."""
		frappe.set_user(self.qa)
		doc = frappe.get_doc("Batch", lot)
		doc.update(values)
		doc.save()

	def correct(self, lot: str, reason: str | None = "Supplier's certificate gives other dates", **values):
		frappe.set_user(self.qa)
		return sign_action("Batch", lot, ACT, PASSWORD, reason, values=values)


@verifies("URS-STK-011")
class TestLotDatesFixedOnceInStock(LotDatesTestCase):
	def test_the_dates_of_a_lot_without_stock_are_edited_on_its_form(self):
		lot = self.lot_without_stock()

		self.edit(lot, manufacturing_date=add_days(today(), -10), expiry_date=add_days(today(), 200))

		self.assertEqual(dates(lot), (getdate(add_days(today(), -10)), getdate(add_days(today(), 200))))
		self.assertFalse(has_stock_history(lot))

	def test_a_lot_on_a_draft_receipt_has_no_stock_yet_and_its_submit_fixes_the_dates(self):
		lot = self.lot_without_stock()
		row = {
			"item_code": RAW,
			"qty": 1,
			"t_warehouse": store(stores.QUARANTINE),
			"basic_rate": 10,
			"batch_no": lot,
			"use_serial_batch_fields": 1,
		}
		entry = stock_entry("Material Receipt", [row], submit=False)

		self.edit(lot, expiry_date=add_days(today(), 300))  # still being received: ERPNext's to set
		frappe.set_user("Administrator")
		frappe.get_doc("Stock Entry", entry.name).submit()

		self.assertTrue(has_stock_history(lot))
		with self.assertRaises(LotDatesFixed):
			self.edit(lot, expiry_date=add_days(today(), 400))
		self.assertEqual(dates(lot)[1], getdate(add_days(today(), 300)))

	def test_the_dates_of_a_lot_with_stock_are_fixed_on_its_form(self):
		lot = self.quarantined(2)
		before = dates(lot)

		for field, value in (
			("expiry_date", add_days(today(), 900)),
			("manufacturing_date", add_days(today(), -30)),
		):
			with self.subTest(field=field), self.assertRaises(LotDatesFixed) as refused:
				self.edit(lot, **{field: value})
			self.assertIn("Correct Dates", str(refused.exception))
		self.assertEqual(dates(lot), before)

	def test_the_dates_of_a_lot_with_stock_are_fixed_through_the_api(self):
		lot = self.quarantined(2)
		before = dates(lot)
		frappe.set_user(self.qa)

		with self.assertRaises(LotDatesFixed):
			frappe.client.set_value(
				"Batch",
				lot,
				{"expiry_date": add_days(today(), 900), "manufacturing_date": add_days(today(), -30)},
			)
		self.assertEqual(dates(lot), before)

	def test_a_lot_whose_stock_was_counted_keeps_its_dates_fixed(self):
		frappe.set_user("Administrator")
		lot = receive(RAW, 2, store("Stores"))  # approved stock, which a count may change (plan D-24)
		count = frappe.get_doc(
			{
				"doctype": "Stock Reconciliation",
				"company": company(),
				"purpose": "Stock Reconciliation",
				"items": [
					{
						"item_code": RAW,
						"warehouse": store("Stores"),
						"qty": 1,
						"valuation_rate": 10,
						"batch_no": lot,
						"use_serial_batch_fields": 1,
					}
				],
			}
		).insert()
		count.submit()
		self.assertEqual(qty_in(lot, store("Stores")), 1)

		with self.assertRaises(LotDatesFixed):
			self.edit(lot, expiry_date=add_days(today(), 900))

	def test_the_correction_is_recorded_only_by_the_signed_act(self):
		lot = self.quarantined(1)
		for field, value in ((CORRECTION_REASON, "typed in"), (CORRECTED_BY, self.qa)):
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				self.edit(lot, **{field: value})
		# A new lot sent with a correction carries none: only the signed act records one.
		frappe.set_user(self.qa)
		made = frappe.get_doc(
			{"doctype": "Batch", "item": RAW, CORRECTION_REASON: "sent with the lot"}
		).insert()
		self.assertIsNone(frappe.db.get_value("Batch", made.name, CORRECTION_REASON))

	def test_the_form_shows_the_dates_fixed_and_offers_qa_the_signed_correction(self):
		in_stock, new = self.quarantined(1), self.lot_without_stock()
		for lot, fixed in ((in_stock, True), (new, False)):
			doc = frappe.get_doc("Batch", lot)
			batch_onload(doc)
			with self.subTest(lot=lot):
				self.assertEqual(doc.get_onload().dates_fixed, fixed)
		self.assertEqual(signing_actions("Batch"), {ACT: "Approved"})
		events = frappe.get_hooks("doc_events")["Batch"]
		self.assertIn("meddevice_suite.lot_control.lot_dates.guard_batch", events["validate"])


@verifies("URS-STK-012")
class TestCorrectDates(LotDatesTestCase):
	def test_qa_corrects_the_dates_with_a_signature_that_binds_them(self):
		lot = self.quarantined(2)
		made, expires = getdate(add_days(today(), -20)), getdate(add_days(today(), 500))
		reason = "Supplier's certificate of analysis gives other dates"

		self.correct(lot, reason, manufacturing_date=str(made), expiry_date=str(expires))

		self.assertEqual(dates(lot), (made, expires))
		stored = frappe.get_doc("Batch", lot)
		self.assertEqual((stored.get(CORRECTED_BY), stored.get(CORRECTION_REASON)), (self.qa, reason))
		self.assertTrue(stored.get(CORRECTED_AT))
		[signature] = signatures(lot)
		self.assertEqual(
			(signature.signer, signature.meaning, signature.action, signature.reason),
			(self.qa, "Approved", ACT, reason),
		)
		self.assertTrue(verify_signatures("Batch", lot))
		self.assertTrue(is_unchanged_since(frappe.get_doc("Electronic Signature", signature.name)))

	def test_a_date_not_given_stays_as_it_is(self):
		lot = self.quarantined(1)
		made, _expires = dates(lot)

		self.correct(lot, expiry_date=add_days(today(), 450))

		self.assertEqual(dates(lot), (made, getdate(add_days(today(), 450))))

	def test_the_audit_trail_records_the_old_and_new_dates_with_the_reason(self):
		lot = self.quarantined(1)
		_made, old_expiry = dates(lot)
		new_expiry = getdate(add_days(today(), 450))
		reason = "Expiry misread at receipt"

		self.correct(lot, reason, expiry_date=str(new_expiry))

		event = frappe.get_all(
			"Audit Event",
			filters={"reference_doctype": "Batch", "reference_name": lot, "action": "Updated"},
			fields=["user", "changes"],
			order_by="creation desc",
			limit=1,
		)[0]
		changed = {field: (old, new) for field, old, new in frappe.parse_json(event.changes)["changed"]}
		self.assertEqual(event.user, self.qa)
		self.assertEqual(changed["expiry_date"], (str(old_expiry), str(new_expiry)))
		self.assertEqual(changed[CORRECTION_REASON], (None, reason))

	def test_only_qa_corrects_the_dates(self):
		lot = self.quarantined(1)
		before = dates(lot)
		for user in (self.stores_user, self.qc):
			frappe.set_user(user)
			with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
				sign_action("Batch", lot, ACT, PASSWORD, "not mine", values={"expiry_date": "2099-01-01"})
		self.assertEqual(signatures(lot), [])
		self.assertEqual(dates(lot), before)

	def test_a_reason_is_required(self):
		lot = self.quarantined(1)
		before = dates(lot)

		with self.assertRaisesRegex(frappe.ValidationError, "Give the reason"):
			self.correct(lot, None, expiry_date=add_days(today(), 450))

		self.assertEqual(signatures(lot), [])
		self.assertEqual(dates(lot), before)

	def test_dates_that_cannot_be_right_are_refused(self):
		lot = self.quarantined(1)
		made, expires = dates(lot)
		for values, message in (
			({"manufacturing_date": add_days(today(), 1)}, "made after today"),
			({"expiry_date": add_days(made, -1)}, "expire before it was made"),
			({"expiry_date": str(expires)}, "nothing to correct"),
		):
			with self.subTest(values=values), self.assertRaisesRegex(frappe.ValidationError, message):
				self.correct(lot, **values)
		self.assertEqual(signatures(lot), [])

	def test_a_value_the_act_does_not_take_is_refused(self):
		lot = self.quarantined(1)

		with self.assertRaisesRegex(frappe.ValidationError, "takes no batch_qty"):
			self.correct(lot, expiry_date=add_days(today(), 450), batch_qty=99)

		self.assertEqual(signatures(lot), [])

	def test_a_lot_corrected_to_an_expiry_already_past_is_held_from_use_at_once(self):
		"""As expiry.py rules for any expired lot: never used, only kept apart for QA to decide; and
		Stores and QA are alerted now, not at the next daily run."""
		frappe.set_user("Administrator")
		lot = receive(RAW, 3, store("Stores"))  # approved stock in use

		self.correct(
			lot,
			"Supplier's notice: the lot expired yesterday",
			manufacturing_date=add_days(today(), -400),
			expiry_date=add_days(today(), -1),
		)

		frappe.set_user(self.stores_user)
		with self.assertRaisesRegex(frappe.ValidationError, f"Lot {lot} expired on"):
			issue(RAW, lot, 1, store("Stores"))
		move(RAW, lot, 3, store("Stores"), store(stores.HOLD))  # kept apart, for QA to decide
		self.assertEqual(qty_in(lot, store(stores.HOLD)), 3)
		self.assertTrue(
			any("has expired" in subject for subject in alerts_for(self.stores_user, "Batch", lot))
		)
