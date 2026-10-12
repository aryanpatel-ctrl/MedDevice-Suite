"""Lots in quarantine or on hold with stock (module 16; URS-QC-004): what each is waiting
for, and for how many days, so QA and Stores see the queue at any time."""

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, today

from meddevice_suite.lot_control.lot_status import HOLD, QUARANTINE, STATUS_FIELD, decided_kinds, stock_by_lot
from meddevice_suite.lot_control.receipt import origins_of
from meddevice_suite.lot_control.stores import kind_of

WAITING_FOR = {
	"no_certificate": "Supplier certificate",
	"verification": "Certificate verification by QC",
	"inspection": "Incoming inspection",
	"rejected": "Decision on a rejected inspection",
	"release": "QA release",
	"not_releasable": "Nothing: it did not come in on a receipt",
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
	lots = {
		row.name: row
		for row in frappe.get_all(
			"Batch",
			filters={
				"name": ["in", [h.batch_no for h in holdings] or [""]],
				STATUS_FIELD: ["in", [QUARANTINE, HOLD]],
			},
			fields=[
				"name",
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
	inspections = latest_inspections(list(lots))
	lines = receipt_lines(lots.values())
	data = []
	for h in holdings:
		lot = lots.get(h.batch_no)
		if lot is None:
			continue
		status = lot.get(STATUS_FIELD)
		if kind_of(h.warehouse) not in decided_kinds(status):
			continue  # rejected at the door, or come back from a customer: not what the release decides
		since = getdate(lot.mds_status_set_at or lot.creation)
		data.append(
			{
				"batch_no": h.batch_no,
				"item_code": h.item_code,
				"lot_status": _(status),
				"qty": h.qty,
				"warehouse": h.warehouse,
				"waiting_for": _(
					WAITING_FOR[waiting_for(lot, lines.get(lot.name), inspections.get(lot.name))]
				),
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


def latest_inspections(lots: list[str]) -> dict[str, frappe._dict]:
	latest: dict[str, frappe._dict] = {}
	for row in frappe.get_all(
		"Quality Inspection",
		filters={"batch_no": ["in", lots or [""]], "inspection_type": "Incoming", "docstatus": 1},
		fields=["batch_no", "status", "creation"],
		order_by="creation asc",
	):
		latest[row.batch_no] = row
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
