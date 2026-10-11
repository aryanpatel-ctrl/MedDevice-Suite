"""The standard flow of the pilot (Phase 2, task S2), step by step.

Each step is done as the demo user of the job that does it in the client's
process, through ERPNext's own documents and the roles, approvals and audit
trail of this app:

	Sales        enquiry → quotation → (price approval) → sales order      D1, D3
	Stores       purchase request                                           D2, D3
	QA           approves the supplier for the materials, with a signature   D2, D3
	Purchase     purchase order → (purchase approval)                       D1, D2
	Stores       goods receipt into quarantine, by batch, with the
	             supplier's certificate on each line                        D2, D3
	QC           verifies the certificates and inspects each lot             D3
	QA           releases each lot with an electronic signature             D2, D3
	Production   work order → material issue → operations → finished batch  D2, D3
	Operator     records the operations on the job cards                    D3
	QA           release of the finished lot from quarantine                D2, D3
	Stores       delivery of the finished batch                             D2, D3

The same steps build the pilot's sample records and are run end to end by the
tests, so what the client sees is what the tests prove.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from erpnext.crm.doctype.opportunity.opportunity import make_quotation
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as make_production_entry
from erpnext.selling.doctype.quotation.quotation import make_sales_order
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.stock.doctype.material_request.material_request import make_purchase_order
from frappe.model.document import Document
from frappe.model.workflow import apply_workflow
from frappe.query_builder.functions import Max
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime, today

from meddevice_suite.lot_control import release as lot_release
from meddevice_suite.lot_control.receipt import verify_certificate
from meddevice_suite.master_data import supplier_approval
from meddevice_suite.pilot import refuse_unless_pilot_site
from meddevice_suite.pilot.demo import BOM_QUANTITY, PRODUCTS, RAW_MATERIALS, demo_user_email
from meddevice_suite.pilot.inspection import readings_for


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


def approve_supplier(supplier: str, items: list[str]) -> str:
	"""QA approves the supplier for the items with a signature, keeping the items it was already
	approved for (module 4); returns the decision in force."""
	approval = supplier_approval.current_approval(supplier)
	approved = approval and approval.decision == supplier_approval.APPROVED
	if approved and set(items) <= approval.item_codes:
		return approval.name
	with as_job("QA Manager"):
		certificate = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"ISO-13485-{frappe.scrub(supplier)}.txt",
				"content": f"ISO 13485 certificate (demo)\nSupplier: {supplier}\n",
				"is_private": 1,
			}
		).insert()
		record = frappe.get_doc(
			{
				"doctype": "Supplier Approval",
				"supplier": supplier,
				"certificate_number": f"QMS-{frappe.scrub(supplier).upper()[:12]}",
				"certificate_expiry": add_days(today(), 365),
				"certificate": certificate.file_url,
				"items": [
					{"item_code": item}
					for item in sorted(set(items) | (approval.item_codes if approved else set()))
				],
			}
		).insert()
		supplier_approval.approve(
			record,
			password=frappe.conf.pilot_user_password,
			reason="Supplier audited; quality system certificate checked (demo)",
		)
		return record.name


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
	"""Goods receipt into quarantine with the supplier's lot number and certificate on each
	line; ERPNext creates a batch per line, with its expiry."""
	with as_job("Stores"):
		receipt = make_purchase_receipt(order.name)
		for row in receipt.items:
			row.warehouse = store("Quarantine")
		receipt.insert()
		for row in receipt.items:
			row.mds_supplier_lot_no = f"SUP-{row.item_code}-{frappe.generate_hash(length=4).upper()}"
			row.mds_certificate = certificate_file(receipt, row.item_code)
		receipt.save()
		receipt.submit()
		return stored(receipt)


def certificate_file(receipt: Document, item: str) -> str:
	"""A fictional certificate of analysis attached to the receipt; returns its address."""
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"COA-{item}-{receipt.name}.txt",
			"content": f"Certificate of analysis (demo)\nItem: {item}\nReceipt: {receipt.name}\nResult: conforms\n",
			"is_private": 1,
			"attached_to_doctype": "Purchase Receipt",
			"attached_to_name": receipt.name,
		}
	).insert()
	return file.file_url


def inspect_received_lots(receipt: Document, instrument: str, accept: bool = True) -> list[Document]:
	"""QC verifies each line's certificate and inspects its lot against the item's template."""
	inspections = []
	with as_job("QC Inspector"):
		for row in frappe.get_doc("Purchase Receipt", receipt.name).items:
			verify_certificate(receipt.name, row.name)
			template = frappe.db.get_value("Item", row.item_code, "quality_inspection_template")
			inspection = frappe.get_doc(
				{
					"doctype": "Quality Inspection",
					"inspection_type": "Incoming",
					"reference_type": "Purchase Receipt",
					"reference_name": receipt.name,
					"item_code": row.item_code,
					"batch_no": lot_of(row),
					"sample_size": 5,
					"inspected_by": frappe.session.user,
					"readings": readings_for(template, instrument, within_limits=accept),
				}
			)
			inspection.insert()
			inspection.submit()
			inspections.append(inspection)
	return inspections


def decide_lot(lot: str, decision: str, reason: str) -> Document:
	"""QA signs its decision on the lot: Release, Reject or Hold (module 16)."""
	with as_job("QA Manager"):
		record = frappe.get_doc({"doctype": "Lot Release", "batch": lot}).insert()
		act = {"Release": lot_release.release, "Reject": lot_release.reject, "Hold": lot_release.hold}[
			decision
		]
		act(record, password=frappe.conf.pilot_user_password, reason=reason)
		return frappe.get_doc("Lot Release", record.name)


def release_received_lots(receipt: Document) -> list[Document]:
	"""QA releases each inspected lot of the receipt with a signature; the release moves it to
	the approved store (modules 16, 17). Returns the decisions."""
	return [
		decide_lot(lot_of(row), "Release", "Incoming inspection accepted (pilot)")
		for row in frappe.get_doc("Purchase Receipt", receipt.name).items
	]


def reject_lot(lot: str, reason: str) -> Document:
	return decide_lot(lot, "Reject", reason)


def hold_lot(lot: str, reason: str) -> Document:
	return decide_lot(lot, "Hold", reason)


def buy(materials: dict[str, float], inspect: bool = True) -> list[Document]:
	"""Request, order, approve, receive and, unless told not to, inspect materials, one purchase
	order per supplier; returns the goods receipts."""
	by_supplier: dict[str, dict[str, float]] = {}
	for item, qty in materials.items():
		by_supplier.setdefault(RAW_MATERIALS[item][4], {})[item] = qty
	receipts = []
	for supplier, items in by_supplier.items():
		approve_supplier(supplier, list(items))
		order = order_materials(request_materials(items), supplier)
		receipt = receive(approve_purchase(order))
		if inspect:
			inspect_received_lots(receipt, instrument())
		receipts.append(receipt)
	return receipts


def instrument() -> str:
	return frappe.db.get_value("Asset", {"company": company(), "item_code": "INST-CALIPER"})


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
				"fg_warehouse": store("Quarantine"),  # the finished lot waits there for QA
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
	start = max(now_datetime(), busy_until(cards))
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


def busy_until(cards: list[str]) -> datetime:
	"""When the workstations of these job cards are free: the end of the last time logged at any
	of them. A workstation runs one job at a time, so a flow run again the same day logs its
	operations after the earlier run's, as an operator would."""
	if not cards:
		return now_datetime()
	job_card, time_log = frappe.qb.DocType("Job Card"), frappe.qb.DocType("Job Card Time Log")
	workstations = frappe.qb.from_(job_card).select(job_card.workstation).where(job_card.name.isin(cards))
	(last,) = (
		frappe.qb.from_(time_log)
		.join(job_card)
		.on(time_log.parent == job_card.name)
		.select(Max(time_log.to_time))
		.where(job_card.workstation.isin(workstations) & (job_card.docstatus < 2))
		.run()[0]
	)
	return get_datetime(last) if last else now_datetime()


def finish_production(work_order: Document) -> Document:
	"""The finished lot is made into quarantine; ERPNext creates its batch with the product's expiry."""
	with as_job("Production Supervisor"):
		entry = frappe.get_doc(make_production_entry(work_order.name, "Manufacture", work_order.qty))
		entry.insert()
		entry.submit()
		return stored(entry)


def finished_lot_of(entry: Document) -> str:
	return next(lot_of(row) for row in entry.items if row.get("t_warehouse") and row.is_finished_item)


def release_finished_lot(entry: Document) -> str:
	"""QA releases the finished lot to the finished goods store with a signature; final QC and
	batch release (modules 25, 26) take this over."""
	lot = finished_lot_of(entry)
	decide_lot(lot, "Release", "Final inspection accepted (pilot)")
	return lot


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
