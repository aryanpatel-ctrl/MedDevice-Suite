"""Supplier rating (module 4; URS-PUR-002; ISO 13485 7.4.1: suppliers are monitored and the results
feed their re-evaluation; workflow diagram D2 "Purchase Management (PO, GRN, Vendor Rating)";
master plan section 3, vendor master: rating).

For every calendar month in which QA decided any of a supplier's received lots, or any of its
order lines fell due, the system rates the supplier, once the month is over:

- Quality: of its lots QA decided that month (module 16), the share released rather than
  rejected. A hold is not a decision on the lot; the lot counts in the month it is released or
  rejected.
- Delivery: of its order lines required by a day in that month, the share received in full by
  that day, on goods receipts (returns to the supplier aside: a rejected lot did arrive).
- Score: quality and delivery weighed as QA sets in Quality Settings; a month with only one of
  them is scored on that one.

A score below QA's threshold marks the supplier due for re-evaluation and alerts QA and Purchase
once; QA's next signed decision on the supplier (Supplier Approval, whatever it decides) closes
it. The rating itself never stops an order: QA's decision does (master_data/supplier_approval.py).

Each rating lists the lots and order lines it counted and the settings it used, so every figure
can be traced. ERPNext's Supplier Scorecard is not used (decided 2026-10-11): its quality figures
read receipt rejections this process never records (goods are received into quarantine and
rejected by QA's signed decision), its on-time count treats a late receipt as on time, its periods
can be cancelled and are not audited, and its standings block orders outside QA's decision.
"""

from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate, today

from meddevice_suite.compliance import quality_settings

DOCTYPE = "Supplier Rating"
SATISFACTORY, DUE, RE_EVALUATED = "Satisfactory", "Re-evaluation Due", "Re-evaluated"
BELOW_THRESHOLD_ALERT = "Supplier Rating Below Threshold"
MONTHS_BACK = 12  # a month missed while the daily job did not run is rated later, up to a year back
SAVEPOINT = "supplier_rating"


def month_of(day: date) -> tuple[date, date]:
	return getdate(get_first_day(day)), getdate(get_last_day(day))


def key(supplier: str, start: date) -> str:
	return f"{supplier}::{start:%Y-%m}"


# --- What the month holds ---------------------------------------------------------------------


def lots_decided(supplier: str | None, start: date, end: date) -> list[frappe._dict]:
	"""QA's final decisions (release or reject) in the month on lots received from the supplier
	(all suppliers when None), newest decision of a lot only."""
	filters = {
		"docstatus": 1,
		"decision": ["in", ["Release", "Reject"]],
		"source_doctype": "Purchase Receipt",
		"decided_at": ["between", [f"{start} 00:00:00", f"{end} 23:59:59.999999"]],
	}
	if supplier:
		filters["supplier"] = supplier
	else:
		filters["supplier"] = ["is", "set"]
	decided = {}
	for row in frappe.get_all(
		"Lot Release",
		filters=filters,
		fields=["name", "supplier", "batch", "item", "decision", "decided_at"],
		order_by="decided_at asc, creation asc",
	):
		decided[row.batch] = row  # a later decision on the same lot stands
	return list(decided.values())


def lines_due(supplier: str | None, start: date, end: date) -> list[frappe._dict]:
	"""Order lines of submitted purchase orders required by a day in the month, each with the
	quantity received on goods receipts by that day (stock units; returns aside)."""
	order, line = frappe.qb.DocType("Purchase Order"), frappe.qb.DocType("Purchase Order Item")
	query = (
		frappe.qb.from_(line)
		.join(order)
		.on(line.parent == order.name)
		.select(
			order.supplier,
			order.name.as_("purchase_order"),
			line.name.as_("purchase_order_item"),
			line.item_code.as_("item"),
			line.schedule_date.as_("required_by"),
			line.stock_qty.as_("ordered_qty"),
		)
		.where((order.docstatus == 1) & (line.schedule_date[start:end]))
		.orderby(line.schedule_date)
		.orderby(order.name)
		.orderby(line.idx)
	)
	if supplier:
		query = query.where(order.supplier == supplier)
	rows = query.run(as_dict=True)
	received = received_by_required_date([row.purchase_order_item for row in rows])
	for row in rows:
		row.received_by_required_date = received.get(row.purchase_order_item, {}).get(row.required_by, 0)
		row.on_time = int(
			flt(row.received_by_required_date, 6) >= flt(row.ordered_qty, 6) and flt(row.ordered_qty) > 0
		)
	return rows


def received_by_required_date(order_lines: list[str]) -> dict[str, dict[date, float]]:
	"""For each order line, the stock quantity received on submitted goods receipts (not returns)
	posted on or before its required date."""
	if not order_lines:
		return {}
	receipt, received = frappe.qb.DocType("Purchase Receipt"), frappe.qb.DocType("Purchase Receipt Item")
	ordered = frappe.qb.DocType("Purchase Order Item")
	rows = (
		frappe.qb.from_(received)
		.join(receipt)
		.on(received.parent == receipt.name)
		.join(ordered)
		.on(received.purchase_order_item == ordered.name)
		.select(received.purchase_order_item, ordered.schedule_date, received.received_stock_qty)
		.where(
			(receipt.docstatus == 1)
			& (receipt.is_return == 0)
			& (received.purchase_order_item.isin(order_lines))
			& (receipt.posting_date <= ordered.schedule_date)
		)
		.run(as_dict=True)
	)
	totals: dict[str, dict[date, float]] = {}
	for row in rows:
		by_day = totals.setdefault(row.purchase_order_item, {})
		day = getdate(row.schedule_date)
		by_day[day] = by_day.get(day, 0) + flt(row.received_stock_qty)
	return totals


def percent(part: int, whole: int) -> float | None:
	return None if not whole else flt(100 * part / whole, 1)


def score_of(quality: float | None, delivery: float | None, quality_weight: int) -> float:
	if quality is None:
		return flt(delivery, 1)
	if delivery is None:
		return flt(quality, 1)
	return flt((quality * quality_weight + delivery * (100 - quality_weight)) / 100, 1)


# --- Making the ratings -----------------------------------------------------------------------


def rate_finished_months() -> None:
	"""Daily: rate every supplier for each finished month of the last year that holds its lots or
	order lines and is not rated yet. One supplier's failure is logged and retried the next day."""
	this_month = month_of(getdate(today()))[0]
	for back in range(MONTHS_BACK, 0, -1):
		start, end = month_of(add_months(this_month, -back))
		suppliers = {row.supplier for row in lots_decided(None, start, end)} | {
			row.supplier for row in lines_due(None, start, end)
		}
		for supplier in sorted(suppliers):
			if frappe.db.exists(DOCTYPE, {"rating_key": key(supplier, start)}):
				continue
			frappe.db.savepoint(SAVEPOINT)
			try:
				rate(supplier, start)
			except Exception:
				frappe.db.rollback(save_point=SAVEPOINT)
				frappe.log_error(title=f"Supplier rating of {supplier} for {start:%Y-%m} could not be made")
			else:
				frappe.db.release_savepoint(SAVEPOINT)


def rate(supplier: str, day: date) -> Document | None:
	"""The supplier's rating for the finished month holding `day`: made once, or None when the
	month holds neither lots nor order lines of the supplier."""
	start, end = month_of(getdate(day))
	if end >= getdate(today()):
		frappe.throw(_("A month is rated once it is over."))
	existing = frappe.db.get_value(DOCTYPE, {"rating_key": key(supplier, start)})
	if existing:
		return frappe.get_doc(DOCTYPE, existing)
	lots, lines = lots_decided(supplier, start, end), lines_due(supplier, start, end)
	if not lots and not lines:
		return None
	released = sum(1 for lot in lots if lot.decision == "Release")
	on_time = sum(line.on_time for line in lines)
	quality, delivery = percent(released, len(lots)), percent(on_time, len(lines))
	weight, threshold = (
		quality_settings.value("supplier_rating_quality_weight"),
		quality_settings.value("supplier_rating_threshold"),
	)
	score = score_of(quality, delivery, weight)
	rating = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"docstatus": 1,  # made submitted, in one write: a rating is final when made
			"supplier": supplier,
			"period": f"{start:%Y-%m}",
			"period_start": start,
			"period_end": end,
			"rating_key": key(supplier, start),
			"lots_released": released,
			"lots_rejected": len(lots) - released,
			"quality_percent": quality,
			"lines_due": len(lines),
			"lines_on_time": on_time,
			"delivery_percent": delivery,
			"quality_weight": weight,
			"threshold": threshold,
			"settings_version": quality_settings.stored(quality_settings.VERSION) or 1,
			"score": score,
			"standing": SATISFACTORY if score >= threshold else DUE,
			"lots": [
				{
					"batch": lot.batch,
					"item": lot.item,
					"lot_release": lot.name,
					"decision": lot.decision,
					"decided_at": lot.decided_at,
				}
				for lot in lots
			],
			"lines": [
				{
					field: line[field]
					for field in (
						"purchase_order",
						"purchase_order_item",
						"item",
						"required_by",
						"ordered_qty",
						"received_by_required_date",
						"on_time",
					)
				}
				for line in lines
			],
		}
	)
	rating.flags.supplier_rating = True
	# Nobody holds create or submit on a rating: this service, after its own checks, is the only
	# way one is made (engineering rules section 4).
	rating.flags.ignore_permissions = True
	rating.insert()
	if rating.standing == DUE:
		frappe.get_doc("Notification", BELOW_THRESHOLD_ALERT).send(rating)
	return rating


def close_re_evaluations(approval: Document) -> None:
	"""QA's signed decision on a supplier re-evaluates it: every rating of the supplier that was
	due for re-evaluation before the decision is closed with it."""
	for name in frappe.get_all(
		DOCTYPE,
		filters={
			"supplier": approval.supplier,
			"docstatus": 1,
			"standing": DUE,
			"creation": ["<=", approval.decided_at],
		},
		pluck="name",
	):
		rating = frappe.get_doc(DOCTYPE, name)
		rating.standing, rating.re_evaluation = RE_EVALUATED, approval.name
		rating.flags.supplier_rating = True
		rating.flags.ignore_permissions = True  # set by the system when QA decides the supplier
		rating.flags.audit_reason = _("Re-evaluated by {0}").format(approval.name)
		rating.save()


def recent(supplier: str) -> list[frappe._dict]:
	"""The supplier's ratings of the last year, newest first, for its form."""
	return frappe.get_all(
		DOCTYPE,
		filters={"supplier": supplier, "docstatus": 1},
		fields=["name", "period", "score", "threshold", "standing"],
		order_by="period_start desc",
		limit=MONTHS_BACK,
	)


def refuse_scorecard(doc: Document, method: str | None = None) -> None:
	"""Supplier Scorecard before_validate (ahead of ERPNext's own checks): suppliers are rated by
	the Supplier Rating (module 4)."""
	frappe.throw(
		_("Suppliers are rated by the Supplier Rating, from QA's lot decisions and the receipts."),
		title=_("Not used"),
	)
