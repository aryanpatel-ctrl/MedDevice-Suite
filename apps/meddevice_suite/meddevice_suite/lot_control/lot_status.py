"""Lot status (module 17; URS-STK-004 to URS-STK-007; D2 "Quarantine / Approved Stock",
D3 "QC Approval / Rejection", D4 3.5 "Accept / Reject / Hold").

Every lot is Quarantine, Approved, Rejected or Hold. A lot is born Quarantine,
whether a supplier delivered it or a work order made it, and only QA moves it
on, with a reason and an electronic signature, through a Lot Release (`release`);
nothing else may write the status, not the form, not the API (`guard_batch`).

Only an Approved lot may be issued, moved to an approved store, picked or
delivered. The rule is enforced where every stock movement must pass, the
Stock Ledger Entry, with the document already stored (`guard_stock_ledger_entry`),
and earlier on the forms that start a movement, so the person is refused before
they submit (`guard_stock_entry`, `guard_delivery_note`, `guard_pick_list`,
`guard_work_order`, `guard_purchase_receipt`). What a lot that is not Approved
may still do: move into the store of its own role, and, once Rejected, go back
to its supplier or be scrapped (`movement_allowed`).

Each status has a store of its own (`stores`): a lot that is not Approved is kept
there, so ERPNext's expiry-first picking, which looks at one store, only ever
sees approved lots.
"""

from collections.abc import Iterable

import frappe
from erpnext.stock.doctype.batch.batch import get_batch_qty
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import flt, now_datetime

from meddevice_suite.compliance.fingerprint import plain_text
from meddevice_suite.lot_control import stores

# Who posts stock by hand; QA's stock entries are only the transfers its releases post.
STOCK_POSTING_ROLES = frozenset(
	{"Stock User", "Stock Manager", "Manufacturing User", "Manufacturing Manager"}
)

QA_ROLE = "Quality Manager"

STATUS_FIELD = "mds_lot_status"
QUARANTINE, APPROVED, REJECTED, HOLD = "Quarantine", "Approved", "Rejected", "Hold"
STATUSES = (QUARANTINE, APPROVED, REJECTED, HOLD)
# status -> the role of the store its stock is kept in; Approved stock is in any store without a role
ROLE_FOR_STATUS = {QUARANTINE: stores.QUARANTINE, HOLD: stores.HOLD, REJECTED: stores.REJECTED}
STATUS_FIELDS = (
	STATUS_FIELD,
	"mds_status_set_by",
	"mds_status_set_at",
	"mds_status_reason",
	"mds_lot_release",
)
TRANSFER_FIELD = "mds_lot_release_transfer"
MAX_REASON = 1000
PRECISION = 6

CUSTOM_FIELDS = {
	"Batch": [
		{
			"fieldname": "mds_lot_status_section",
			"label": "Lot Status",
			"fieldtype": "Section Break",
			"insert_after": "expiry_date",
		},
		{
			"fieldname": STATUS_FIELD,
			"label": "Lot Status",
			"fieldtype": "Select",
			"options": "\n".join(STATUSES),
			"default": QUARANTINE,
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "mds_lot_status_section",
			"description": "Changed only by QA through a lot release or disposition.",
		},
		{
			"fieldname": "mds_status_set_by",
			"label": "Status Set By",
			"fieldtype": "Link",
			"options": "User",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": STATUS_FIELD,
		},
		{
			"fieldname": "mds_lot_status_column",
			"fieldtype": "Column Break",
			"insert_after": "mds_status_set_by",
		},
		{
			"fieldname": "mds_status_set_at",
			"label": "Status Set At",
			"fieldtype": "Datetime",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "mds_lot_status_column",
		},
		{
			"fieldname": "mds_status_reason",
			"label": "Status Reason",
			"fieldtype": "Small Text",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "mds_status_set_at",
		},
		{
			"fieldname": "mds_lot_release",
			"label": "Latest Decision",
			"fieldtype": "Link",
			"options": "Lot Release",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "mds_status_reason",
		},
	],
	"Stock Entry": [
		{
			"fieldname": TRANSFER_FIELD,
			"label": "Posted by a Lot Release",
			"fieldtype": "Check",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "purpose",
			"description": "A move QA's lot release posted; it is never cancelled, the lot is held instead.",
		}
	],
}

HINT = {
	QUARANTINE: "QA releases it after incoming inspection.",
	HOLD: "QA releases or rejects it.",
	REJECTED: "It can only be returned to its supplier or scrapped.",
}


class LotNotApproved(frappe.ValidationError):
	"""A movement of a lot that is not Approved was refused."""


def apply_lot_status() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	# The quarantine list and the stock report read lots by status.
	frappe.db.add_index("Batch", [STATUS_FIELD])


def is_qa(user: str | None = None) -> bool:
	return QA_ROLE in frappe.get_roles(user or frappe.session.user)


# --- The lot's own record -------------------------------------------------------------------


def guard_batch(doc: Document, method: str | None = None) -> None:
	"""Batch validate: a new lot is Quarantine, whatever it was sent with; a stored lot's status
	fields change only through `set_status` (URS-STK-004)."""
	if doc.is_new():
		doc.set(STATUS_FIELD, QUARANTINE)
		for field in STATUS_FIELDS[1:]:
			doc.set(field, None)
		return
	if doc.flags.lot_control:
		return
	before = doc.get_doc_before_save()
	if before and any(str(before.get(field) or "") != str(doc.get(field) or "") for field in STATUS_FIELDS):
		frappe.throw(
			_("The status of lot {0} changes only through a QA release or disposition.").format(doc.name),
			frappe.PermissionError,
		)


def status_of(lots: Iterable[str]) -> dict[str, str]:
	"""lot -> status, in one query; a lot made before the status existed counts as Quarantine."""
	names = [lot for lot in set(lots) if lot]
	if not names:
		return {}
	rows = frappe.get_all(
		"Batch", filters={"name": ["in", names]}, fields=["name", STATUS_FIELD], as_list=True
	)
	return {name: status or QUARANTINE for name, status in rows}


def set_status(lot: str, status: str, reason: str, release: str | None = None) -> Document:
	"""The only writer of a lot's status: called by the release service after its own checks."""
	if status not in STATUSES:
		frappe.throw(_("Unknown lot status: {0}").format(status))
	frappe.db.get_value("Batch", lot, "name", for_update=True)
	doc = frappe.get_doc("Batch", lot)
	doc.update(
		{
			STATUS_FIELD: status,
			"mds_status_set_by": frappe.session.user,
			"mds_status_set_at": now_datetime(),
			"mds_status_reason": clean_reason(reason),
			"mds_lot_release": release,
		}
	)
	doc.flags.lot_control = True
	# The lot's own permissions are QA's; the service writes for the system after its checks.
	doc.save(ignore_permissions=True)
	return doc


def clean_reason(reason: str | None) -> str:
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Give the reason; it is kept on the lot and in the audit trail."))
	if len(reason) > MAX_REASON:
		frappe.throw(_("Keep the reason within {0} characters.").format(MAX_REASON))
	return plain_text(reason)


# --- Enforcement --------------------------------------------------------------------------------


def movement_allowed(
	status: str,
	voucher_type: str,
	purpose: str | None = None,
	is_return: bool = False,
	target_role: str | None = None,
	lot_release_transfer: bool = False,
) -> bool:
	"""Whether stock of a lot in `status` may leave a store on this document (URS-STK-005)."""
	if status == APPROVED or lot_release_transfer:
		return True
	# A counted correction by the stock manager, not an issue; audited like every posting.
	if voucher_type == "Stock Reconciliation":
		return True
	if voucher_type == "Stock Entry" and purpose == "Material Transfer":
		if target_role and target_role == ROLE_FOR_STATUS.get(status):
			return True  # within the store of its own role
		if status == REJECTED and target_role == stores.RETURNS:
			return True
	if status == REJECTED:
		if voucher_type == "Stock Entry" and purpose == "Material Issue":
			return True  # scrapped
		if voucher_type in ("Purchase Receipt", "Purchase Invoice") and is_return:
			return True  # back to the supplier
	return False


def refuse(lot: str, status: str, store: str | None) -> None:
	frappe.throw(
		_(
			"Lot {0} is {1} and cannot leave {2}; only an Approved lot can be issued, moved, picked or delivered. {3}"
		).format(lot, _(status), store or _("its store"), _(HINT.get(status, ""))),
		LotNotApproved,
		title=_("Lot not approved"),
	)


def lots_of_bundle(bundle: str | None) -> list[str]:
	if not bundle:
		return []
	return frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": bundle, "batch_no": ["is", "set"]},
		pluck="batch_no",
		distinct=True,
	)


def lots_of_row(row: Document) -> list[str]:
	return [row.batch_no] if row.get("batch_no") else lots_of_bundle(row.get("serial_and_batch_bundle"))


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: the one place every outward movement passes, with its
	document already stored (URS-STK-005)."""
	if sle.is_cancelled or flt(sle.actual_qty, PRECISION) >= 0:
		return
	lots = [sle.batch_no] if sle.batch_no else lots_of_bundle(sle.serial_and_batch_bundle)
	if not lots:
		return
	statuses = status_of(lots)
	purpose = is_return = target_role = None
	if sle.voucher_type == "Stock Entry":
		purpose, transfer = frappe.db.get_value("Stock Entry", sle.voucher_no, ["purpose", TRANSFER_FIELD])
		target_role = stores.role_of(
			frappe.db.get_value("Stock Entry Detail", sle.voucher_detail_no, "t_warehouse")
		)
		lot_release_transfer = bool(transfer) and frappe.flags.lot_release_transfer == sle.voucher_no
	else:
		is_return = frappe.get_meta(sle.voucher_type).has_field("is_return") and bool(
			frappe.db.get_value(sle.voucher_type, sle.voucher_no, "is_return")
		)
		lot_release_transfer = False
	for lot in lots:
		status = statuses.get(lot, QUARANTINE)
		if not movement_allowed(
			status, sle.voucher_type, purpose, is_return, target_role, lot_release_transfer
		):
			refuse(lot, status, sle.warehouse)


def guard_stock_entry(doc: Document, method: str | None = None) -> None:
	"""Stock Entry validate: refused on the form, before submit, for the lots already named
	(URS-STK-005); the lot release's own transfer is marked only by the release."""
	if doc.get(TRANSFER_FIELD) and not doc.flags.lot_control:
		frappe.throw(_("Only a lot release posts a lot release transfer."), frappe.PermissionError)
	if not doc.flags.lot_control and not (STOCK_POSTING_ROLES & set(frappe.get_roles())):
		frappe.throw(
			_("Stock is posted by Stores and Production; QA moves a lot through its release."),
			frappe.PermissionError,
		)
	lots_by_row = {row.name: lots_of_row(row) for row in doc.items if row.get("s_warehouse")}
	statuses = status_of(lot for lots in lots_by_row.values() for lot in lots)
	for row in doc.items:
		for lot in lots_by_row.get(row.name, ()):
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(
				status,
				"Stock Entry",
				doc.purpose,
				target_role=stores.role_of(row.get("t_warehouse")),
				lot_release_transfer=bool(doc.flags.lot_control),
			):
				refuse(lot, status, row.s_warehouse)


def refuse_cancel_of_release_transfer(doc: Document, method: str | None = None) -> None:
	"""Stock Entry before_cancel: the move a lot release posted stays; a wrong release is
	corrected by putting the lot on hold, which moves it back."""
	if doc.get(TRANSFER_FIELD):
		frappe.throw(
			_("{0} was posted by a lot release and is not cancelled; put the lot on hold instead.").format(
				doc.name
			),
			frappe.PermissionError,
		)


def guard_delivery_note(doc: Document, method: str | None = None) -> None:
	"""Delivery Note validate: only Approved lots are delivered (URS-STK-005)."""
	lots_by_row = {row.name: lots_of_row(row) for row in doc.items}
	statuses = status_of(lot for lots in lots_by_row.values() for lot in lots)
	for row in doc.items:
		for lot in lots_by_row.get(row.name, ()):
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(status, "Delivery Note", is_return=bool(doc.is_return)):
				refuse(lot, status, row.warehouse)


def guard_pick_list(doc: Document, method: str | None = None) -> None:
	"""Pick List validate: only Approved lots are picked (URS-STK-005)."""
	lots_by_row = {row.name: lots_of_row(row) for row in doc.locations}
	statuses = status_of(lot for lots in lots_by_row.values() for lot in lots)
	for row in doc.locations:
		for lot in lots_by_row.get(row.name, ()):
			status = statuses.get(lot, QUARANTINE)
			if status != APPROVED:
				refuse(lot, status, row.warehouse)


def guard_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order validate: materials come from an approved store, work in progress stays in
	one, and the finished lot lands in the Quarantine store until QA releases it."""
	for field in ("source_warehouse", "wip_warehouse"):
		if stores.role_of(doc.get(field)):
			frappe.throw(
				_("{0} is a {1} store; a work order draws materials from an approved store.").format(
					doc.get(field), _(stores.role_of(doc.get(field)))
				)
			)
	if doc.fg_warehouse and stores.role_of(doc.fg_warehouse) != stores.QUARANTINE:
		frappe.throw(
			_("The finished lot goes to the Quarantine store until QA releases it; {0} is not one.").format(
				doc.fg_warehouse
			)
		)


def guard_purchase_receipt(doc: Document, method: str | None = None) -> None:
	"""Purchase Receipt validate: goods are received into the company's Quarantine store, whatever
	store the item or the form defaulted to, and quantity rejected at the door goes to the
	Rejected store (D2, D3). Set rather than refused: every receipt goes there, so nobody has
	to choose it."""
	if doc.is_return:
		return
	quarantine = stores.store_for(stores.QUARANTINE, doc.company)
	if doc.get("set_warehouse") and doc.set_warehouse != quarantine:
		doc.set_warehouse = quarantine
	for row in doc.items:
		if row.get("warehouse") != quarantine:
			row.warehouse = quarantine
		if flt(row.get("rejected_qty")) and stores.role_of(row.get("rejected_warehouse")) != stores.REJECTED:
			frappe.throw(
				_("Line {0}: quantity rejected at receipt goes to the Rejected store.").format(row.idx)
			)


# --- Moving a lot between stores (for the signed decision in `release`) ---------------


def locked(lot: str) -> Document:
	"""The lot as stored, row-locked for the rest of the transaction, so two decisions on it
	at once are taken one after the other."""
	frappe.db.get_value("Batch", lot, "name", for_update=True)
	return frappe.get_doc("Batch", lot)


def move_to_role(batch: Document, role: str | None, reason: str) -> Document | None:
	"""Move all the lot's stock into the store of `role` (None: the item's approved store), one
	transfer for every store that holds some; nothing is posted when it is already there."""
	holdings = holdings_of(batch.name)
	if not holdings:
		return None
	companies = {frappe.get_cached_value("Warehouse", h["warehouse"], "company") for h in holdings}
	if len(companies) != 1:
		frappe.throw(
			_("Lot {0} has stock in more than one company; move it by hand first.").format(batch.name)
		)
	company = companies.pop()
	target = stores.store_for(role, company) if role else approved_store(batch.item, company)
	rows = [h for h in holdings if h["warehouse"] != target]
	if not rows:
		return None
	transfer = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Transfer",
			"company": company,
			TRANSFER_FIELD: 1,
			"remarks": clean_reason(reason),
			"items": [
				{
					"item_code": batch.item,
					"qty": h["qty"],
					"s_warehouse": h["warehouse"],
					"t_warehouse": target,
					"batch_no": batch.name,
					"use_serial_batch_fields": 1,
				}
				for h in rows
			],
		}
	)
	transfer.flags.lot_control = True
	# Posted under the QA user with QA's own permissions (setup/roles.py), so the audit trail
	# attributes the move to the person who released the lot.
	transfer.insert()
	frappe.flags.lot_release_transfer = transfer.name
	try:
		transfer.submit()
	finally:
		frappe.flags.lot_release_transfer = None
	return transfer


def holdings_of(lot: str) -> list[dict]:
	"""Where the lot's stock is: [{warehouse, qty}] for every store holding some."""
	return [h for h in get_batch_qty(batch_no=lot) if flt(h.get("qty"), PRECISION) > 0]


def approved_store(item: str, company: str) -> str:
	store = frappe.db.get_value("Item Default", {"parent": item, "company": company}, "default_warehouse")
	if not store or stores.role_of(store):
		frappe.throw(
			_("Item {0} has no approved default store for {1}; set it on the item or name the store.").format(
				item, company
			)
		)
	return store


# --- Stock by lot and status --------------------------------------------------------------------


def stock_by_lot(item: str | None = None, warehouse: str | None = None) -> list[frappe._dict]:
	"""[{item_code, warehouse, batch_no, qty}] for every lot with stock, in two grouped queries
	(ERPNext keeps old-style lots on the ledger entry and bundled lots on the bundle's rows)."""
	sle = frappe.qb.DocType("Stock Ledger Entry")
	entry = frappe.qb.DocType("Serial and Batch Entry")
	plain = (
		frappe.qb.from_(sle)
		.select(sle.item_code, sle.warehouse, sle.batch_no, Sum(sle.actual_qty).as_("qty"))
		.where((sle.is_cancelled == 0) & (sle.batch_no.isnotnull()) & (sle.batch_no != ""))
		.groupby(sle.item_code, sle.warehouse, sle.batch_no)
	)
	bundled = (
		frappe.qb.from_(sle)
		.inner_join(entry)
		.on(entry.parent == sle.serial_and_batch_bundle)
		.select(sle.item_code, sle.warehouse, entry.batch_no, Sum(entry.qty).as_("qty"))
		.where((sle.is_cancelled == 0) & (sle.has_batch_no == 1) & (entry.batch_no.isnotnull()))
		.where((sle.batch_no.isnull()) | (sle.batch_no == ""))
		.groupby(sle.item_code, sle.warehouse, entry.batch_no)
	)
	totals: dict[tuple[str, str, str], float] = {}
	for query in (plain, bundled):
		if item:
			query = query.where(sle.item_code == item)
		if warehouse:
			query = query.where(sle.warehouse == warehouse)
		for row in query.run(as_dict=True):
			key = (row.item_code, row.warehouse, row.batch_no)
			totals[key] = totals.get(key, 0) + flt(row.qty)
	return [
		frappe._dict(item_code=i, warehouse=w, batch_no=b, qty=flt(q, PRECISION))
		for (i, w, b), q in sorted(totals.items())
		if flt(q, PRECISION) > 0
	]


def initial_status(holdings: Iterable[frappe._dict]) -> str:
	"""The status a lot takes at cutover from where its stock is: a rejected, hold or
	quarantine store makes it that, in that order of caution; approved stores make it Approved;
	a lot with no stock stays Quarantine (URS-STK-007, decided 2026-10-08)."""
	holdings = list(holdings)
	if not holdings:
		return QUARANTINE
	roles = {stores.role_of(h.warehouse) for h in holdings}
	for role, status in ((stores.REJECTED, REJECTED), (stores.HOLD, HOLD), (stores.QUARANTINE, QUARANTINE)):
		if role in roles:
			return status
	return APPROVED
