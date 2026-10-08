"""Sample records for the pilot walk-through (Phase 2, task S2).

	bench --site pilot.localhost execute meddevice_suite.pilot.samples.load_sample_records

Runs the standard flow of `pilot.flow` so that every stage has a record the
client can open, each made by the demo user of the job that makes it:

- delivered: Foley catheters ordered, bought, received, made and delivered;
- in production: urine bags with materials issued and job cards open for the
  operators;
- waiting for approval: a quotation's price and a purchase order;
- in quarantine: latex received and not yet released;
- rejected: a lot of PVC film QA rejected, in the rejected store.

Refuses to run on any site that is not a pilot site, and runs only once.
"""

import frappe

from meddevice_suite.pilot import flow, refuse_unless_pilot_site
from meddevice_suite.pilot.demo import CUSTOMERS, RAW_MATERIALS, load_demo_data

DELIVERED = ("FC-2W-SIL-16", 200)
IN_PRODUCTION = ("UB-2000-TAP", 300)
AWAITING_PRICE_APPROVAL = ("FC-3W-SIL-18", 150)
AWAITING_PURCHASE_APPROVAL = {"RM-LATEX": 25}
IN_QUARANTINE = {"RM-LATEX": 40}
REJECTED = {"RM-PVC-FILM": 10}


def load_sample_records() -> None:
	refuse_unless_pilot_site()
	load_demo_data()
	if samples_loaded():
		return

	item, qty = DELIVERED
	order = flow.book_sales_order(
		flow.approve_price(flow.quote(flow.record_enquiry(CUSTOMERS[0], item, qty)))
	)
	release(flow.buy(flow.materials_for(item, qty)))
	work_order = flow.plan_production(order, item, qty)
	flow.issue_materials(work_order)
	flow.record_operations(work_order)
	flow.release_finished_lot(flow.finish_production(work_order))
	flow.deliver(order)

	item, qty = IN_PRODUCTION
	order = flow.book_sales_order(
		flow.approve_price(flow.quote(flow.record_enquiry(CUSTOMERS[1], item, qty)))
	)
	release(flow.buy(flow.materials_for(item, qty)))
	flow.issue_materials(flow.plan_production(order, item, qty))

	item, qty = AWAITING_PRICE_APPROVAL
	flow.quote(flow.record_enquiry(CUSTOMERS[0], item, qty))

	flow.order_materials(
		flow.request_materials(AWAITING_PURCHASE_APPROVAL), supplier_of(AWAITING_PURCHASE_APPROVAL)
	)
	flow.buy(IN_QUARANTINE)
	for receipt in flow.buy(REJECTED):
		for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
			flow.reject_lot(
				flow.lot_of(row), "Certificate of analysis does not match the specification (pilot)"
			)


def samples_loaded() -> bool:
	return bool(frappe.db.exists("Opportunity", {"party_name": CUSTOMERS[0]}))


def release(receipts: list) -> None:
	for receipt in receipts:
		flow.release_received_lots(receipt)


def supplier_of(materials: dict[str, float]) -> str:
	return RAW_MATERIALS[next(iter(materials))][4]
