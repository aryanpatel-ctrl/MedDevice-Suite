"""The standard flow of the pilot (Phase 2, task S2), step by step.

Each step is done as the demo user of the job that does it in the client's
process, through ERPNext's own documents and the roles, approvals and audit
trail of this app:

	Sales        enquiry → quotation → (price approval) → sales order      D1, D3
	Stores       purchase request                                           D2, D3
	Purchase     purchase order → (purchase approval)                       D1, D2
	Stores       goods receipt into quarantine, by batch                    D2, D3
	Stores       move to the approved store (stands in for the QA release
	             of Phase 5)
	Production   work order → material issue → operations → finished batch  D2, D3
	Operator     records the operations on the job cards                    D3
	Stores       delivery of the finished batch                             D2, D3

The same steps build the pilot's sample records and are run end to end by the
tests, so what the client sees is what the tests prove.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from erpnext.crm.doctype.opportunity.opportunity import make_quotation
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as make_production_entry
from erpnext.selling.doctype.quotation.quotation import make_sales_order
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.stock.doctype.material_request.material_request import make_purchase_order
from frappe.model.document import Document
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, add_to_date, now_datetime, today

from meddevice_suite.pilot import refuse_unless_pilot_site
from meddevice_suite.pilot.demo import BOM_QUANTITY, PRODUCTS, RAW_MATERIALS, demo_user_email


@contextmanager
def as_job(profile: str) -> Iterator[None]:
	"""Act as the demo user of a job profile, then return to whoever was acting before.

	Switching user is what lets each step run under that job's own permissions; it is
	allowed only on a pilot site and in tests, never on a working or production site."""
	if not frappe.in_test:
		refuse_unless_pilot_site()
	previous, job_user = frappe.session.user, demo_user_email(profile)
	frappe.set_user(job_user)  # nosemgrep: frappe-setuser - pilot and tests only, checked above
	try:
		yield
	finally:
		frappe.set_user(previous)  # nosemgrep: frappe-setuser - back to the user who was acting


def lot_of(row: Document) -> str:
	"""The lot of a stock line: ERPNext v16 keeps it in the line's Serial and Batch Bundle."""
	return row.batch_no or frappe.db.get_value(
		"Serial and Batch Entry", {"parent": row.serial_and_batch_bundle}, "batch_no"
	)


def stored(doc: Document) -> Document:
	"""The record as stored: ERPNext writes the lots it creates on submit to the database only."""
	return frappe.get_doc(doc.doctype, doc.name)


def company() -> str:
	return frappe.defaults.get_global_default("company")


def store(name: str) -> str:
	return f"{name} - {frappe.db.get_value('Company', company(), 'abbr')}"


# --- Sales -------------------------------------------------------------------


def record_enquiry(customer: str, item: str, qty: float) -> Document:
	with as_job("Sales"):
		return frappe.get_doc(
			{
				"doctype": "Opportunity",
				"opportunity_from": "Customer",
				"party_name": customer,
				"company": company(),
				"items": [{"item_code": item, "qty": qty, "rate": selling_rate(item)}],
			}
		).insert()


def selling_rate(item: str) -> float:
	return frappe.db.get_value(
		"Item Price", {"item_code": item, "price_list": "Standard Selling"}, "price_list_rate"
	)


def quote(enquiry: Document) -> Document:
	with as_job("Sales"):
		quotation = make_quotation(enquiry.name)
		quotation.valid_till = add_days(today(), 30)
		quotation.insert()
		return apply_workflow(quotation, "Send for Price Approval")


def approve_price(quotation: Document) -> Document:
	with as_job("Management"):
		return apply_workflow(frappe.get_doc("Quotation", quotation.name), "Approve")


def book_sales_order(quotation: Document) -> Document:
	with as_job("Sales"):
		order = make_sales_order(quotation.name)
		order.delivery_date = add_days(today(), 21)
		order.insert()
		order.submit()
		return order


# --- Purchasing and receiving ---------------------------------------------------


def request_materials(materials: dict[str, float]) -> Document:
	with as_job("Stores"):
		request = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Purchase",
				"company": company(),
				"schedule_date": add_days(today(), 7),
				"items": [
					{
						"item_code": item,
						"qty": qty,
						"schedule_date": add_days(today(), 7),
						"warehouse": store("Quarantine"),
					}
					for item, qty in materials.items()
				],
			}
		).insert()
		request.submit()
		return request


def order_materials(request: Document, supplier: str) -> Document:
	with as_job("Purchase"):
		order = make_purchase_order(request.name)
		order.supplier = supplier
		order.insert()
		return apply_workflow(order, "Send for Approval")


def approve_purchase(order: Document) -> Document:
	with as_job("Management"):
		return apply_workflow(frappe.get_doc("Purchase Order", order.name), "Approve")


def receive(order: Document) -> Document:
	"""Goods receipt into quarantine; ERPNext creates a batch per line, with its expiry."""
	with as_job("Stores"):
		receipt = make_purchase_receipt(order.name)
		for row in receipt.items:
			row.warehouse = store("Quarantine")
		receipt.insert()
		receipt.submit()
		return stored(receipt)


def move_to_approved_store(receipt: Document) -> Document:
	"""Stands in for the QA release of Phase 5, which will replace this step."""
	with as_job("Stores"):
		move = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Transfer",
				"company": company(),
				"items": [
					{
						"item_code": row.item_code,
						"qty": row.qty,
						"batch_no": lot_of(row),
						"use_serial_batch_fields": 1,
						"s_warehouse": store("Quarantine"),
						"t_warehouse": store("Stores"),
					}
					for row in frappe.get_doc("Purchase Receipt", receipt.name).items
				],
			}
		).insert()
		move.submit()
		return stored(move)


def buy(materials: dict[str, float]) -> list[Document]:
	"""Request, order, approve and receive materials, one purchase order per supplier;
	returns the goods receipts."""
	by_supplier: dict[str, dict[str, float]] = {}
	for item, qty in materials.items():
		by_supplier.setdefault(RAW_MATERIALS[item][4], {})[item] = qty
	receipts = []
	for supplier, items in by_supplier.items():
		order = order_materials(request_materials(items), supplier)
		receipts.append(receive(approve_purchase(order)))
	return receipts


# --- Production ----------------------------------------------------------------


def plan_production(sales_order: Document, item: str, qty: float) -> Document:
	with as_job("Production Supervisor"):
		work_order = frappe.get_doc(
			{
				"doctype": "Work Order",
				"production_item": item,
				"bom_no": frappe.db.get_value("BOM", {"item": item, "is_default": 1, "docstatus": 1}),
				"qty": qty,
				"company": company(),
				"sales_order": sales_order.name,
				"source_warehouse": store("Stores"),
				"wip_warehouse": store("Work In Progress"),
				"fg_warehouse": store("Finished Goods"),
				"planned_start_date": now_datetime(),
			}
		)
		work_order.get_items_and_operations_from_bom()  # as the form does when the BOM is chosen
		work_order.insert()
		work_order.submit()  # ERPNext creates a job card per operation of the routing
		return work_order


def issue_materials(work_order: Document) -> Document:
	"""Materials leave the approved store for the line; the lot that expires first goes first."""
	with as_job("Production Supervisor"):
		issue = frappe.get_doc(
			make_production_entry(work_order.name, "Material Transfer for Manufacture", work_order.qty)
		)
		issue.insert()
		issue.submit()
		return stored(issue)


def record_operations(work_order: Document) -> list[str]:
	"""Operators log each operation of the routing; the supervisor completes the job card."""
	cards = frappe.get_all(
		"Job Card",
		filters={"work_order": work_order.name, "docstatus": 0},
		pluck="name",
		order_by="sequence_id asc",
	)
	start = now_datetime()
	for card_name in cards:
		with as_job("Operator"):
			card = frappe.get_doc("Job Card", card_name)
			minutes = max(1, int(card.time_required or 1))
			card.append(
				"time_logs",
				{
					"from_time": start,
					"to_time": add_to_date(start, minutes=minutes),
					"completed_qty": card.for_quantity,
				},
			)
			card.save()
		start = add_to_date(start, minutes=minutes)
		with as_job("Production Supervisor"):
			card = frappe.get_doc("Job Card", card_name)
			card.status = "Completed"
			card.submit()
	return cards


def finish_production(work_order: Document) -> Document:
	"""The finished lot is made; ERPNext creates its batch with the product's expiry."""
	with as_job("Production Supervisor"):
		entry = frappe.get_doc(make_production_entry(work_order.name, "Manufacture", work_order.qty))
		entry.insert()
		entry.submit()
		return stored(entry)


# --- Dispatch ------------------------------------------------------------------


def deliver(sales_order: Document) -> Document:
	with as_job("Stores"):
		delivery = make_delivery_note(sales_order.name)
		for row in delivery.items:
			row.warehouse = store("Finished Goods")
		delivery.insert()
		delivery.submit()
		return stored(delivery)


def materials_for(item: str, qty: float) -> dict[str, float]:
	"""Raw materials for `qty` pieces of `item`, from its BOM batch of 100."""
	return {material: per_batch * qty / BOM_QUANTITY for material, per_batch in PRODUCTS[item][4].items()}
