"""Lots in quarantine or on hold with stock, and every lot with stock in the hold store whatever its
status (module 16; URS-QC-004): what each is waiting for, and for how many days, so QA and Stores see
the queue at any time. Approved stock Stores set apart in the hold store waits on QA's decision as a
held lot does (independent review 2026-10-15). A held lot production made waits for its final
inspection when its item is re-inspected after a hold, and a lot QA approved a concession for waits
for Management's approval (plan D-35, URS-QC-005)."""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, today

from meddevice_suite.lot_control import stores
from meddevice_suite.lot_control.lot_status import (
	HOLD,
	QUARANTINE,
	STATUS_FIELD,
	decided_kinds,
	last_arrivals,
	stock_by_lot,
)
from meddevice_suite.lot_control.receipt import INCOMING, OUTGOING, origins_of
from meddevice_suite.lot_control.release import pending_concessions
from meddevice_suite.lot_control.stores import kind_of
from meddevice_suite.master_data.item_master import reinspected_after_hold

AWAITING = (QUARANTINE, HOLD)

WAITING_FOR = {
	"no_certificate": "Supplier certificate",
	"verification": "Certificate verification by QC",
	"inspection": "Incoming inspection",
	"rejected": "Decision on a rejected inspection",
	"release": "QA release",
	"not_releasable": "Nothing: it did not come in on a receipt",
	"set_apart": "QA decision on stock set apart",
	"final_inspection": "Final inspection",
	"concession": "Management approval of a concession",
}


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
	filters = frappe._dict(filters or {})
	columns = [
		{"fieldname": "batch_no", "label": _("Lot"), "fieldtype": "Link", "options": "Batch", "width": 200},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 180},
		{"fieldname": "lot_status", "label": _("Lot Status"), "fieldtype": "Data", "width": 100},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100},
		{
			"fieldname": "warehouse",
			"label": _("Store"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
		{"fieldname": "waiting_for", "label": _("Waiting For"), "fieldtype": "Data", "width": 220},
		{"fieldname": "days", "label": _("Days Waiting"), "fieldtype": "Int", "width": 110},
		{"fieldname": "since", "label": _("Since"), "fieldtype": "Date", "width": 100},
		{"fieldname": "source", "label": _("Came From"), "fieldtype": "Data", "width": 180},
	]
	holdings = [h for h in stock_by_lot(filters.get("item_code")) if h.qty > 0]
	set_apart = {h.batch_no for h in holdings if kind_of(h.warehouse) == stores.HOLD}
	lots = {
		row.name: row
		for row in frappe.get_all(
			"Batch",
			filters={"name": ["in", [h.batch_no for h in holdings] or [""]]},
			# A lot awaiting a decision, or with stock in the hold store whatever its status.
			or_filters={STATUS_FIELD: ["in", list(AWAITING)], "name": ["in", list(set_apart) or [""]]},
			fields=[
				"name",
				"item",
				STATUS_FIELD,
				"creation",
				"mds_status_set_at",
				"reference_doctype",
				"reference_name",
			],
		)
	}
	# Where each lot came from, as the stock ledger says, not the batch's own reference.
	origins = origins_of(list(lots))
	purposes = dict(
		frappe.get_all(
			"Stock Entry",
			filters={
				"name": [
					"in",
					[o.voucher_no for o in origins.values() if o.voucher_type == "Stock Entry"] or [""],
				]
			},
			fields=["name", "purpose"],
			as_list=True,
		)
	)
	for name, lot in lots.items():
		origin = origins.get(name)
		lot.reference_doctype = origin.voucher_type if origin else None
		lot.reference_name = origin.voucher_no if origin else None
		lot.origin = (
			"receipt"
			if lot.reference_doctype == "Purchase Receipt"
			else {"Material Receipt": "material_receipt", "Manufacture": "production"}.get(
				purposes.get(lot.reference_name)
			)
			if lot.reference_doctype == "Stock Entry"
			else None
		)
	# When stock set apart in the hold store came there, for every such lot in one read.
	hold_stores = {h.warehouse for h in holdings if kind_of(h.warehouse) == stores.HOLD}
	arrived = last_arrivals(set_apart, hold_stores)
	inspections = latest_inspections(list(lots))
	final = latest_inspections(list(lots), OUTGOING)
	reinspected = reinspected_after_hold(
		{lot.item for lot in lots.values() if lot.origin == "production" and lot.get(STATUS_FIELD) == HOLD}
	)
	concessions = pending_concessions(list(lots))
	lines = receipt_lines(lots.values())
	data = []
	for h in holdings:
		lot = lots.get(h.batch_no)
		if lot is None:
			continue
		status = lot.get(STATUS_FIELD) or QUARANTINE
		kind = kind_of(h.warehouse)
		awaiting = status in AWAITING and kind in decided_kinds(status)
		if not awaiting and kind != stores.HOLD:
			continue  # rejected at the door, or come back from a customer: not what the release decides
		if awaiting:
			since = getdate(lot.mds_status_set_at or lot.creation)
			if lot.name in concessions:
				waiting = "concession"
			elif lot.origin == "production" and lot.item in reinspected:
				waiting = held_production_waits_for(lot, final.get((lot.name, lot.reference_name)))
			else:
				waiting = waiting_for(lot, lines.get(lot.name), inspections.get(lot.name))
		else:
			# Stock of a lot not awaiting a decision, set apart in the hold store: since it came there.
			since = getdate(arrived.get(h.batch_no) or lot.mds_status_set_at or lot.creation)
			waiting = "set_apart"
		data.append(
			{
				"batch_no": h.batch_no,
				"item_code": h.item_code,
				"lot_status": _(status),
				"qty": h.qty,
				"warehouse": h.warehouse,
				"waiting_for": _(WAITING_FOR[waiting]),
				"days": date_diff(today(), since),
				"since": since,
				"source": f"{lot.reference_doctype} {lot.reference_name}" if lot.reference_name else "",
			}
		)
	data.sort(key=lambda row: (-row["days"], row["batch_no"]))
	return columns, data


def waiting_for(lot: frappe._dict, line: frappe._dict | None, inspection: frappe._dict | None) -> str:
	if lot.reference_doctype == "Purchase Receipt" and line is not None:
		if not line.certificate:
			return "no_certificate"
		if not line.verified:
			return "verification"
	if lot.origin is None:
		return "not_releasable"
	if lot.origin in ("receipt", "material_receipt"):
		if inspection is None or (
			# Made after the hold, as the release requires: an old inspection edited after the
			# hold is not a new inspection.
			lot.get(STATUS_FIELD) == HOLD and inspection.creation <= lot.mds_status_set_at
		):
			return "inspection"
		if inspection.status == "Rejected":
			return "rejected"
	return "release"


def held_production_waits_for(lot: frappe._dict, inspection: frappe._dict | None) -> str:
	"""A held lot production made, whose item is re-inspected after a hold (plan D-35): released after
	an accepted final inspection on its manufacture entry made after the hold, as the release
	requires."""
	if lot.get(STATUS_FIELD) != HOLD:
		return "release"
	if inspection is None or (lot.mds_status_set_at and inspection.creation <= lot.mds_status_set_at):
		return "final_inspection"
	if inspection.status == "Rejected":
		return "rejected"
	return "release"


def latest_inspections(lots: list[str], inspection_type: str = INCOMING) -> dict:
	"""For the incoming type, lot -> its newest submitted incoming inspection; for the final
	(Outgoing) type, (lot, document inspected on) -> the newest on that document. Read through the
	index on the inspection's lot that this app configures (setup/indexes.py)."""
	latest: dict = {}
	for row in frappe.get_all(
		"Quality Inspection",
		filters={"batch_no": ["in", lots or [""]], "inspection_type": inspection_type, "docstatus": 1},
		fields=["batch_no", "reference_name", "status", "creation"],
		order_by="creation asc",
	):
		key = row.batch_no if inspection_type == INCOMING else (row.batch_no, row.reference_name)
		latest[key] = row
	return latest


def receipt_lines(lots) -> dict[str, frappe._dict]:
	"""lot -> its receipt line's certificate state, in one query over the receipts named."""
	receipts = {lot.reference_name for lot in lots if lot.reference_doctype == "Purchase Receipt"}
	if not receipts:
		return {}
	by_lot: dict[str, frappe._dict] = {}
	rows = frappe.get_all(
		"Purchase Receipt Item",
		filters={"parent": ["in", list(receipts)], "docstatus": 1},
		fields=[
			"name",
			"parent",
			"batch_no",
			"serial_and_batch_bundle",
			"mds_certificate",
			"mds_certificate_verified",
		],
	)
	bundles = [row.serial_and_batch_bundle for row in rows if row.serial_and_batch_bundle]
	bundle_lots: dict[str, set[str]] = {}
	for entry in frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": ["in", bundles or [""]], "batch_no": ["is", "set"]},
		fields=["parent", "batch_no"],
	):
		bundle_lots.setdefault(entry.parent, set()).add(entry.batch_no)
	for row in rows:
		for lot in ({row.batch_no} if row.batch_no else set()) | bundle_lots.get(
			row.serial_and_batch_bundle, set()
		):
			by_lot[lot] = frappe._dict(certificate=row.mds_certificate, verified=row.mds_certificate_verified)
	return by_lot
