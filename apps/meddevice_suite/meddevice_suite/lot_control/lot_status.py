"""Lot status (module 17; URS-STK-004 to URS-STK-007, URS-STK-010; D2 "Quarantine / Approved
Stock", D3 "QC Approval / Rejection", D4 3.5 "Accept / Reject / Hold"; ISO 13485 7.5.8, 8.3).

Every lot is Quarantine, Approved, Rejected or Hold. A lot is born Quarantine,
whether a supplier delivered it or a work order made it, and only QA moves it
on, with a reason and an electronic signature, through a Lot Release (`release`);
nothing else may write the status, not the form, not the API (`guard_batch`).

Where stock of a lot may go depends on the lot's status and on the kind of store the
stock sits in (`stores.kind_of`), because one lot can sit in several: a goods receipt
puts the quantity rejected at the door in the Rejected store under the same lot as the
accepted quantity in quarantine, and goods a customer sends back come into the Returns
store whatever the lot's status. One rule, the stock path matrix (`movement_allowed`),
decides every movement of every voucher type:

- approved stock in an approved store is used as usual, and may be set apart in the
  hold or rejected store;
- stock of a lot that is not Approved leaves an approved store only by QA's signed move;
- stock in the quarantine, hold or returns store leaves only by QA's signed decision;
- stock in the rejected store, whatever the lot's status, goes back to its supplier or is
  scrapped; never into usable stock, not by a release;
- new stock of a lot that is not Approved never comes into an approved store, and goods
  a customer sends back come only into the returns store (plan D-25), which receives
  nothing else: goods rejected at the door moved there by hand would reach usable stock by
  QA's Return to Stock (independent review 2026-10-15);
- a stock count changes only approved stock in an approved store (plan D-24);
- cancelling a document is a movement too: one whose reversal would take stock out of the
  hold, rejected or returns store, or stock of a lot QA has held or rejected, is refused;
  nonconforming product leaves only by QA's disposition (`guard_cancellation`; ISO 13485
  8.3; independent review 2026-10-15).

The rule is enforced where every stock movement must pass, the Stock Ledger Entry,
with the document already stored and the lot row-locked, so a delivery and a hold
cannot cross (`guard_stock_ledger_entry`), and earlier on the forms that start a
movement, so the person is refused before they submit (`guard_stock_entry`,
`guard_sales_document`, `guard_pick_list`, `guard_packing_slip`, `guard_work_order`,
`guard_purchase_receipt`).

Each status has a store of its own (`stores`): a lot that is not Approved is kept
there, so ERPNext's expiry-first picking, which looks at one store, only ever
sees approved lots.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.query_builder.functions import Max, Sum
from frappe.utils import cint, flt, get_datetime, now_datetime

from meddevice_suite.compliance.checks import QA_ROLE, is_qa
from meddevice_suite.compliance.fingerprint import plain_text
from meddevice_suite.lot_control import stores

# Who posts stock by hand; QA's stock entries are only the transfers its releases post.
STOCK_POSTING_ROLES = frozenset(
	{"Stock User", "Stock Manager", "Manufacturing User", "Manufacturing Manager"}
)

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
MATERIAL_TRANSFER, MATERIAL_ISSUE = "Material Transfer", "Material Issue"
# Documents a customer's goods come back on, when they are a return (plan D-25).
CUSTOMER_DOCUMENTS = frozenset({"Delivery Note", "Sales Invoice", "POS Invoice"})
# Documents goods go back to their supplier on, when they are a return.
SUPPLIER_DOCUMENTS = frozenset({"Purchase Receipt", "Purchase Invoice"})

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
# What stock kept in each kind of role store may still do, whatever its lot's status.
STORE_HINT = {
	stores.QUARANTINE: "It leaves only by QA's signed release, hold or rejection.",
	stores.HOLD: "It leaves only by QA's signed release, after a new inspection, or rejection.",
	stores.REJECTED: "It can only be returned to its supplier or scrapped.",
	stores.RETURNS: "It leaves only by QA's signed disposition: back to stock after an inspection, or to the Rejected store.",
}


class LotNotApproved(frappe.ValidationError):
	"""A movement of a lot that is not Approved was refused."""


def apply_lot_status() -> None:
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
	# The quarantine list and the stock report read lots by status.
	frappe.db.add_index("Batch", [STATUS_FIELD])


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


def status_of(lots: Iterable[str], lock: bool = False) -> dict[str, str]:
	"""lot -> status, in one query; a lot made before the status existed counts as Quarantine.
	`lock` row-locks the lots until the transaction ends, in name order so two documents never wait
	on each other: a movement then reads the status a decision on the lot may be changing at that
	moment only once the decision is done, and a hold and a delivery cannot cross."""
	names = sorted({lot for lot in lots if lot})
	if not names:
		return {}
	batch = frappe.qb.DocType("Batch")
	query = (
		frappe.qb.from_(batch)
		.select(batch.name, batch[STATUS_FIELD])
		.where(batch.name.isin(names))
		.orderby(batch.name)
	)
	if lock:
		query = query.for_update()
	return {name: status or QUARANTINE for name, status in query.run()}


def qty_precision() -> int:
	"""The places stock quantities are compared and moved at: the precision of the stock ledger's
	own quantity, as ERPNext rounds what it posts, not a fixed number of places."""
	return frappe.get_precision("Stock Ledger Entry", "actual_qty")


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


@dataclass(frozen=True)
class Movement:
	"""A movement of stock as the stock path rule reads it. A store is named by its kind
	(`stores.kind_of`): `source` None is stock coming in from outside the stores (a receipt,
	production, a customer's return), `target` None stock leaving them (issued, consumed, delivered,
	scrapped, sent back to its supplier)."""

	voucher_type: str
	source: str | None
	target: str | None
	purpose: str | None = None  # a stock entry's purpose
	is_return: bool = False
	signed: bool = False  # QA's own transfer, posted by a signed decision (`release`)


def movement_allowed(status: str, movement: Movement) -> bool:
	"""Whether stock of a lot in `status` may make `movement` (URS-STK-005, URS-STK-010): the stock
	path matrix of docs/features/lot-status-control.md section 2.3, the one rule every voucher type
	is held to, on the form and on the stock ledger."""
	if movement.source is None:
		return may_come_in(status, movement)
	if movement.target == stores.RETURNS:
		# The returns store receives only a customer's goods coming back, not stock moved there, not
		# even by QA: goods rejected at the door moved there would reach usable stock by QA's Return to
		# Stock (independent review 2026-10-15).
		return False
	if movement.source == stores.REJECTED:
		# Rejected goods, at the door or by QA, whatever the lot's status now: never into usable stock,
		# not even by a release; they go back to their supplier or are scrapped.
		if movement.voucher_type == "Stock Entry":
			return movement.purpose == MATERIAL_ISSUE and movement.target is None
		return movement.voucher_type in SUPPLIER_DOCUMENTS and movement.is_return
	if movement.signed:
		return True
	if movement.voucher_type == "Stock Reconciliation":
		# A count corrects approved stock only; what QA has not released, or keeps apart, changes only
		# by its decision or its own path (plan D-24).
		return status == APPROVED and movement.source == stores.APPROVED
	if movement.source != stores.APPROVED or status != APPROVED:
		# Quarantine, hold and returns stock, and a lot not approved wherever it is, leave only by QA's
		# signed decision.
		return False
	# Approved stock is used, moved between approved stores, or set apart for QA to decide.
	return movement.target in (None, stores.APPROVED, stores.HOLD, stores.REJECTED)


def may_come_in(status: str, movement: Movement) -> bool:
	"""New stock and returns: goods a customer sends back come into the Returns store, whatever the
	lot's status, for QA to decide (plan D-25), and nothing else does (independent review
	2026-10-15); a lot that is not Approved never comes into an approved store, so a material
	receipt, production or a count cannot put unreleased stock where it is used."""
	if is_customer_return(movement) or movement.target == stores.RETURNS:
		return is_customer_return(movement) and movement.target == stores.RETURNS
	return status == APPROVED or movement.target != stores.APPROVED


def is_customer_return(movement: Movement) -> bool:
	return movement.voucher_type in CUSTOMER_DOCUMENTS and movement.is_return


def refuse(lot: str, status: str, store: str | None, movement: Movement | None = None) -> None:
	title, message = refusal(lot, status, store, movement)
	frappe.throw(message, LotNotApproved, title=title)


def refusal(lot: str, status: str, store: str | None, movement: Movement | None) -> tuple[str, str]:
	"""The title and message of a refused movement: why, and what the stock may still do."""
	if movement and movement.target == stores.RETURNS and not is_customer_return(movement):
		return _("Stock returned"), _(
			"Lot {0} cannot be put in the Returns store; it receives only goods a customer sends back, on a return delivery note or invoice."
		).format(lot)
	if movement and movement.source is None:
		if is_customer_return(movement):
			return _("Stock returned"), _(
				"Lot {0} comes back from a customer into {1}; goods a customer sends back go to the Returns store, where QA decides them."
			).format(lot, store or _("a store"))
		return _("Lot not approved"), _(
			"Lot {0} is {1} and cannot come into {2}, an approved store; new stock comes into the Quarantine store until QA releases it."
		).format(lot, _(status), store or _("an approved store"))
	if status != APPROVED or not movement:
		return _("Lot not approved"), _(
			"Lot {0} is {1} and cannot leave {2}; only an Approved lot can be issued, moved, picked or delivered. {3}"
		).format(lot, _(status), store or _("its store"), _(HINT.get(status, "")))
	if movement.source != stores.APPROVED:
		return _("Stock kept apart"), _(
			"Lot {0} is Approved, but its stock in {1} is kept apart there. {2}"
		).format(lot, store or _("its store"), _(STORE_HINT.get(movement.source, "")))
	return _("Stock kept apart"), _(
		"Lot {0} is Approved and cannot be moved from {1} into the {2} store; only QA's signed decision or a customer's return puts stock there."
	).format(lot, store or _("its store"), _(movement.target or ""))


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
	return lots_of_rows([row])[0]


def lots_of_rows(rows: list[Document | frappe._dict]) -> list[list[str]]:
	"""The lots of each line, in the lines' order: its own lot, or its bundle's. The bundles of all
	the lines are read in one query, not one per line: a stock count or a delivery has hundreds."""
	bundles = sorted(
		{row.get("serial_and_batch_bundle") for row in rows if not row.get("batch_no")} - {None, ""}
	)
	in_bundle: dict[str, list[str]] = {}
	if bundles:
		for entry in frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": ["in", bundles], "batch_no": ["is", "set"]},
			fields=["parent", "batch_no"],
			order_by="idx asc",
		):
			lots = in_bundle.setdefault(entry.parent, [])
			if entry.batch_no not in lots:
				lots.append(entry.batch_no)
	return [
		[row.batch_no] if row.get("batch_no") else list(in_bundle.get(row.get("serial_and_batch_bundle"), []))
		for row in rows
	]


def guard_stock_ledger_entry(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry validate: the one place every movement passes, with its document already
	stored, held to the stock path rule (URS-STK-005, URS-STK-010) under the lots' row locks; new
	stock, see guard_new_stock. A lot the voucher did not name is not known yet: see
	guard_lot_made_on_submit."""
	precision = qty_precision()
	if sle.is_cancelled or not flt(sle.actual_qty, precision):
		return
	lots = [sle.batch_no] if sle.batch_no else lots_of_bundle(sle.serial_and_batch_bundle)
	if not lots:
		sle.flags.lot_not_named = True
		return
	hold_to_stock_path(sle, lots)


def guard_lot_made_on_submit(sle: Document, method: str | None = None) -> None:
	"""Stock Ledger Entry on_submit: a lot the voucher did not name (a new lot made on a receipt or
	production) gets its bundle in ERPNext's own on_submit, after validate; it is held to the same
	rule here, so a new lot does not come into an approved store unchecked. Only for such an entry:
	a named lot was checked at validate, before ERPNext's own stock checks."""
	if not sle.flags.lot_not_named:
		return
	lots = [sle.batch_no] if sle.batch_no else lots_of_bundle(sle.serial_and_batch_bundle)
	if lots:
		hold_to_stock_path(sle, lots)


def hold_to_stock_path(sle: Document, lots: list[str]) -> None:
	movement = movement_of(sle, inward=flt(sle.actual_qty, qty_precision()) > 0)
	if movement is None:
		return  # stock arriving on a move between stores, checked where it left
	# The lots are locked before anything is read of them: two receipts of one lot submitted at once
	# would otherwise both find it new, each before the other's ledger entries exist. The second waits
	# here for the first to finish and is then refused: by the origin check, or by the database, as
	# the lot's row changed since it began (MariaDB's snapshot isolation), to be submitted again and
	# refused (independent review 2026-10-15).
	statuses = status_of(lots, lock=True)
	if movement.source is None:
		guard_new_stock(sle, lots, movement)
	for lot in lots:
		status = statuses.get(lot, QUARANTINE)
		if not movement_allowed(status, movement):
			refuse(lot, status, sle.warehouse, movement)


def movement_of(sle: Document, inward: bool) -> Movement | None:
	"""The movement a ledger entry is part of; None for the arriving end of a move between stores,
	whose leaving end carries both stores."""
	here = stores.kind_of(sle.warehouse)
	header = voucher_header(sle.voucher_type, sle.voucher_no)
	if sle.voucher_type == "Stock Entry":
		purpose, transfer = header.get("purpose"), header.get(TRANSFER_FIELD)
		row = stock_entry_line(sle.voucher_no, sle.voucher_detail_no)
		if inward and row.s_warehouse:
			return None
		return Movement(
			"Stock Entry",
			source=None if inward else here,
			target=here if inward else stores.kind_of(row.t_warehouse),
			purpose=purpose,
			# Only the release's own transfer, while the release posts it.
			signed=bool(transfer) and frappe.flags.lot_release_transfer == sle.voucher_no,
		)
	is_return = bool(header.get("is_return"))  # read only where the record type has it
	return Movement(
		sle.voucher_type,
		source=None if inward else here,
		target=here if inward else None,
		is_return=is_return,
	)


# The fields of a voucher the ledger checks read (here, in expiry and in finished_goods), each where
# its record type has it; mds_fefo_reason is expiry.REASON_FIELD, named here as expiry builds on this.
HEADER_FIELDS = ("purpose", "stock_entry_type", "is_opening", "is_return", TRANSFER_FIELD, "mds_fefo_reason")
VOUCHERS_READ = "mds_vouchers_read"


def voucher_header(voucher_type: str, voucher_no: str) -> frappe._dict:
	"""The voucher's fields the ledger checks read (`HEADER_FIELDS`), read once per document: ERPNext
	posts one ledger entry per line, and each entry's checks read the same header, which a document
	of a hundred lines read a few hundred times."""
	read = vouchers_read()
	key = ("header", voucher_type, voucher_no)
	if key not in read:
		meta = frappe.get_meta(voucher_type)
		fields = [field for field in HEADER_FIELDS if meta.has_field(field)]
		read[key] = (
			frappe.db.get_value(voucher_type, voucher_no, fields, as_dict=True) if fields else None
		) or frappe._dict()
	return read[key]


def stock_entry_line(voucher_no: str, line: str | None) -> frappe._dict:
	"""A stock entry line's stores, all the entry's lines read in one query, once per document."""
	read = vouchers_read()
	key = ("lines", "Stock Entry", voucher_no)
	if key not in read:
		read[key] = {
			row.name: row
			for row in frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": voucher_no, "parenttype": "Stock Entry"},
				fields=["name", "s_warehouse", "t_warehouse"],
			)
		}
	return read[key].get(line) or frappe._dict()


def vouchers_read() -> dict:
	"""What the ledger checks have read of the vouchers being posted, kept for the transaction only:
	forgotten when it commits or rolls back, and a voucher's own when it is submitted again
	(`forget_voucher`), so a value is never read from before the document's latest submission."""
	read = getattr(frappe.local, VOUCHERS_READ, None)
	if read is None:
		read = {}
		setattr(frappe.local, VOUCHERS_READ, read)
		frappe.db.after_commit.add(forget_vouchers)
		frappe.db.after_rollback.add(forget_vouchers)
	return read


def forget_vouchers() -> None:
	setattr(frappe.local, VOUCHERS_READ, None)


def forget_voucher(doc: Document, method: str | None = None) -> None:
	"""before_submit and before_cancel of every record type: a document submitted again in the same
	transaction (a submission refused, the document corrected and submitted) is read afresh."""
	read = getattr(frappe.local, VOUCHERS_READ, None)
	if read:
		for kind in ("header", "lines"):
			read.pop((kind, doc.doctype, doc.name), None)


def note_submission(doc: Document, method: str | None = None) -> None:
	"""before_submit of every record type: read the document afresh (`forget_voucher`), and note that
	it is being submitted now, in this transaction, so the ledger checks know its movement happens
	today, whatever date it is posted on (`submitted_now`)."""
	forget_voucher(doc)
	vouchers_read()[("submitting", doc.doctype, doc.name)] = True


def submitted_now(voucher_type: str, voucher_no: str) -> bool:
	"""Whether the voucher is being submitted in this transaction: goods move when it is submitted,
	not on the date it is posted on. A ledger ERPNext posts again later (a reposting that recreates
	the entries) is not a submission."""
	return bool(vouchers_read().get(("submitting", voucher_type, voucher_no)))


def guard_new_stock(sle: Document, lots: list[str], movement: Movement) -> None:
	"""Stock comes into a lot only on the document that made it (a goods receipt, a material
	receipt or production: DFD 3.3 Internal Batch Creation, 4.6 Batch Creation), and on one line of
	it. More stock, a count gain or opening stock comes in as a new lot, inspected and released on
	its own; added to a lot already in stock, it would take that lot's status without inspection
	(decided 2026-10-09). The same new lot on two lines would be two receipts of one lot, each with
	its own inspection and return, and the lot would be known by one of them only. Goods coming back
	on a return are the same stock."""
	from meddevice_suite.lot_control.receipt import origins_of  # receipt builds on this module

	if movement.is_return:
		return
	origins = origins_of(lots)
	# What a count puts back, read once for all the lots of the entry, not once per lot.
	recounted = counted_back(
		sle, [lot for lot, origin in origins.items() if origin.voucher_no != sle.voucher_no]
	)
	for lot, origin in origins.items():
		if origin.voucher_no != sle.voucher_no:
			if lot not in recounted:
				frappe.throw(
					_("Lot {0} came in on {1} {2}; new stock comes in as a new lot.").format(
						lot, _(origin.voucher_type), origin.voucher_no
					),
					title=_("New lot needed"),
				)
		elif (
			origin.voucher_detail_no
			and sle.voucher_detail_no
			and origin.voucher_detail_no != sle.voucher_detail_no
		):
			frappe.throw(
				_(
					"Lot {0} comes in on another line of {1} {2} already; a new lot comes in on one line."
				).format(lot, _(sle.voucher_type), sle.voucher_no),
				title=_("New lot needed"),
			)


def counted_back(sle: Document, lots: list[str]) -> set[str]:
	"""Of `lots`, those a stock count only puts back. A count posts each line of a batch as the
	lot's stock taken out and the counted stock put back (ERPNext's
	stock_reconciliation.get_sle_for_serialized_items, the taking out first). Putting back no more
	than the same line took out is the count, not new stock: a loss, an unchanged line or a corrected
	value. Only a gain beyond it would be new stock in an existing lot. Read from the ledger entries
	of the line, which exist by now; the bundles may not yet be submitted. Read for all the lots in
	a fixed number of queries, however many the line's bundle holds."""
	if sle.voucher_type != "Stock Reconciliation" or not lots:
		return set()
	taken_out = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": sle.voucher_type,
			"voucher_no": sle.voucher_no,
			"voucher_detail_no": sle.voucher_detail_no,
			"actual_qty": ["<", 0],
			"is_cancelled": 0,
		},
		fields=["batch_no", "serial_and_batch_bundle", "actual_qty"],
	)
	taken: dict[str, float] = {}
	put_back: dict[str, float] = {}
	# Counted the way this entry names the lot: by its batch field or by its bundle, not both.
	if sle.batch_no:
		for row in taken_out:
			if row.batch_no == sle.batch_no:
				taken[sle.batch_no] = taken.get(sle.batch_no, 0) + abs(flt(row.actual_qty))
		put_back[sle.batch_no] = abs(flt(sle.actual_qty))
	bundled = [lot for lot in lots if lot != sle.batch_no]
	if bundled:
		bundles = [row.serial_and_batch_bundle for row in taken_out if row.serial_and_batch_bundle]
		for row in frappe.get_all(
			"Serial and Batch Entry",
			filters={
				"parent": ["in", [*bundles, sle.serial_and_batch_bundle or ""]],
				"batch_no": ["in", bundled],
			},
			fields=["parent", "batch_no", "qty"],
		):
			# The entry's own bundle is what it puts back; the line's earlier bundles what it took out.
			into = put_back if row.parent == sle.serial_and_batch_bundle else taken
			into[row.batch_no] = into.get(row.batch_no, 0) + abs(flt(row.qty))
	precision = qty_precision()
	return {lot for lot in lots if flt(put_back.get(lot, 0), precision) <= flt(taken.get(lot, 0), precision)}


def guard_stock_entry(doc: Document, method: str | None = None) -> None:
	"""Stock Entry validate: refused on the form, before submit, for the lots already named, by
	the stock path rule (URS-STK-005, URS-STK-010); the lot release's own transfer is marked only by
	the release."""
	if doc.get(TRANSFER_FIELD) and not doc.flags.lot_control:
		frappe.throw(_("Only a lot release posts a lot release transfer."), frappe.PermissionError)
	if not doc.flags.lot_control and not (STOCK_POSTING_ROLES & set(frappe.get_roles())):
		frappe.throw(
			_("Stock is posted by Stores and Production; QA moves a lot through its release."),
			frappe.PermissionError,
		)
	rows = list(zip(doc.items, lots_of_rows(doc.items), strict=True))
	statuses = status_of(lot for _row, lots in rows for lot in lots)
	for row, lots in rows:
		movement = Movement(
			"Stock Entry",
			source=stores.kind_of(row.get("s_warehouse")),
			target=stores.kind_of(row.get("t_warehouse")),
			purpose=doc.purpose,
			signed=bool(doc.flags.lot_control),
		)
		for lot in lots:
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(status, movement):
				refuse(lot, status, row.get("s_warehouse") or row.get("t_warehouse"), movement)


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


# Stores whose stock is kept apart for QA to decide, and lot statuses QA has decided against use: what
# a cancellation must not take out (`guard_cancellation`).
KEPT_APART_KINDS = frozenset({stores.HOLD, stores.REJECTED, stores.RETURNS})
DECIDED_APART = frozenset({HOLD, REJECTED})


def guard_cancellation(doc: Document, method: str | None = None) -> None:
	"""before_cancel of every record type: cancelling a stock document posts the reverse of its
	movement, held to the stock path rule as a new movement is (URS-STK-014). Stock it brought into the
	hold, rejected or returns store, or stock of a lot QA has held or rejected, would leave that way
	without QA's disposition: a transfer of damaged stock into the rejected store cancelled would put
	it back in approved stock, a customer's return cancelled would take the goods out of QA's queue
	(ISO 13485 8.3; independent review 2026-10-15). Every other cancellation stays allowed, a goods
	receipt into quarantine among them; the lot release's own transfer is refused on its own
	(`refuse_cancel_of_release_transfer`)."""
	from erpnext.controllers.stock_controller import StockController

	if not isinstance(doc, StockController) or doc.get(TRANSFER_FIELD):
		return
	# What the document brought into a store, which its cancellation takes out again.
	entries = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": doc.doctype,
			"voucher_no": doc.name,
			"is_cancelled": 0,
			"actual_qty": [">", 0],
		},
		fields=["warehouse", "batch_no", "serial_and_batch_bundle"],
	)
	if not entries:
		return
	rows = list(zip(entries, lots_of_rows(entries), strict=True))
	statuses = status_of(lot for _entry, lots in rows for lot in lots)
	for entry, lots in rows:
		kind = stores.kind_of(entry.warehouse)
		for lot in lots:
			status = statuses.get(lot, QUARANTINE)
			if kind in KEPT_APART_KINDS or status in DECIDED_APART:
				frappe.throw(
					_(
						"Cancelling {0} {1} would take lot {2} ({3}) out of {4} without QA's decision. Stock kept apart for QA leaves only by QA's signed disposition on a Lot Release; ask QA to decide it."
					).format(_(doc.doctype), doc.name, lot, _(status), entry.warehouse),
					LotNotApproved,
					title=_("Stock kept apart"),
				)


def guard_sales_document(doc: Document, method: str | None = None) -> None:
	"""Delivery Note, Sales Invoice and POS Invoice validate: a delivery takes only approved stock
	from an approved store, and goods a customer sends back come into the Returns store, whatever
	their lot's status, a recall's return of a held lot among them, for QA to decide (URS-STK-005,
	URS-STK-010; plan D-25). An invoice that moves no stock is not checked."""
	if doc.doctype != "Delivery Note" and not cint(doc.get("update_stock")):
		return
	is_return = bool(doc.get("is_return"))
	rows = list(zip(doc.items, lots_of_rows(doc.items), strict=True))
	statuses = status_of(lot for _row, lots in rows for lot in lots)
	for row, lots in rows:
		kind = stores.kind_of(row.get("warehouse"))
		if (
			is_return
			and kind
			and kind != stores.RETURNS
			and frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		):
			frappe.throw(
				_(
					"Line {0}: goods a customer sends back come into the Returns store, where QA decides them; {1} is not it."
				).format(row.idx, row.warehouse),
				LotNotApproved,
				title=_("Stock returned"),
			)
		movement = Movement(
			doc.doctype,
			source=None if is_return else kind,
			target=kind if is_return else None,
			is_return=is_return,
		)
		for lot in lots:
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(status, movement):
				refuse(lot, status, row.get("warehouse"), movement)


def guard_packing_slip(doc: Document, method: str | None = None) -> None:
	"""Packing Slip validate: only approved stock in an approved store is packed (URS-STK-005,
	URS-STK-010). The slip packs a draft delivery note's lines (or the packed items of its product
	bundles), checked when the note was saved; a lot held since then is caught here. A line not
	found is held to its lot's status alone."""
	lines = {}
	for doctype, field in (("Delivery Note Item", "dn_detail"), ("Packed Item", "pi_detail")):
		named = [row.get(field) for row in doc.items if row.get(field)]
		for line in frappe.get_all(
			doctype,
			filters={"name": ["in", named or [""]]},
			fields=["name", "batch_no", "serial_and_batch_bundle", "warehouse"],
		):
			lines[line.name] = line
	found = [lines.get(row.get("dn_detail")) or lines.get(row.get("pi_detail")) for row in doc.items]
	of_lines = lots_of_rows([line for line in found if line])  # one query for every line's bundle
	lots_of_line = {line.name: lots for line, lots in zip([f for f in found if f], of_lines, strict=True)}
	lots_by_row = []
	for row, line in zip(doc.items, found, strict=True):
		lots = [row.batch_no] if row.get("batch_no") else (lots_of_line[line.name] if line else [])
		lots_by_row.append((lots, line.warehouse if line else None))
	statuses = status_of(lot for lots, _store in lots_by_row for lot in lots)
	for lots, warehouse in lots_by_row:
		movement = Movement("Packing Slip", source=stores.kind_of(warehouse) or stores.APPROVED, target=None)
		for lot in lots:
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(status, movement):
				refuse(lot, status, warehouse, movement)


def guard_pick_list(doc: Document, method: str | None = None) -> None:
	"""Pick List validate: only approved stock in an approved store is picked (URS-STK-005,
	URS-STK-010)."""
	rows = list(zip(doc.locations, lots_of_rows(doc.locations), strict=True))
	statuses = status_of(lot for _row, lots in rows for lot in lots)
	for row, lots in rows:
		movement = Movement(
			"Pick List", source=stores.kind_of(row.get("warehouse")) or stores.APPROVED, target=None
		)
		for lot in lots:
			status = statuses.get(lot, QUARANTINE)
			if not movement_allowed(status, movement):
				refuse(lot, status, row.warehouse, movement)


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


def guard_unvalidated_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order before_validate: Production Plan and Subcontracting Inward Order save their work
	orders with validation skipped (`flags.ignore_validate`), so `guard_work_order` would first run
	when the order is submitted. Checked here as they are made; a validated order is checked at
	`validate`, after ERPNext has filled its default stores."""
	if doc.flags.ignore_validate:
		guard_work_order(doc)


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


def move_to_role(
	batch: Document, role: str | None, reason: str, holdings: list[frappe._dict]
) -> Document | None:
	"""Move `holdings`, the stock of the lot a signed decision acts on, into the store of `role`
	(None: the item's approved store), one transfer row for every store holding some. Stock already in
	a store of that kind stays where it is; nothing is posted when all of it is. Never the lot's
	other stock: the quantity rejected at the door, say, is not the decision's (C1, 2026-10-12)."""
	if not holdings:
		return None
	companies = {frappe.get_cached_value("Warehouse", h.warehouse, "company") for h in holdings}
	if len(companies) != 1:
		frappe.throw(
			_("Lot {0} has stock in more than one company; move it by hand first.").format(batch.name)
		)
	company = companies.pop()
	rows = [h for h in holdings if stores.kind_of(h.warehouse) != (role or stores.APPROVED)]
	if not rows:
		return None
	target = stores.store_for(role, company) if role else approved_store(batch.item, company)
	precision = frappe.get_precision("Stock Entry Detail", "qty")
	transfer = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": transfer_type(),
			"company": company,
			TRANSFER_FIELD: 1,
			"remarks": clean_reason(reason),
			"items": [
				{
					"item_code": batch.item,
					"qty": flt(h.qty, precision),
					"s_warehouse": h.warehouse,
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


def transfer_type() -> str:
	"""The stock entry type of a plain move between stores, found by its purpose rather than its
	name, which a site may change; a type that sends stock into transit is not one."""
	names = frappe.get_all(
		"Stock Entry Type",
		filters={"purpose": MATERIAL_TRANSFER, "add_to_transit": 0},
		pluck="name",
		order_by="is_standard desc, creation asc",
		limit=1,
	)
	if not names:
		frappe.throw(_("No stock entry type moves stock between stores; run the site configuration."))
	return names[0]


def holdings_of(lot: str, kinds: Collection[str] | None = None) -> list[frappe._dict]:
	"""Where the lot's stock is: [{item_code, warehouse, batch_no, qty}] for every store holding
	some, only stores of `kinds` (`stores.kind_of`) if given. Read from the stock ledger itself, so
	an expired or disabled lot counts too: ERPNext's batch quantity leaves an expired lot out (unless
	asked for stock levels) and a disabled one always, and a decision on such a lot must still move
	its stock (found 2026-10-12). Reserved stock is in the store and counts."""
	holdings = stock_by_lot(lot=lot)
	if kinds is None:
		return holdings
	return [h for h in holdings if stores.kind_of(h.warehouse) in kinds]


def decided_kinds(status: str) -> frozenset[str]:
	"""The kinds of store a decision on a lot in `status` acts on (C1, 2026-10-12): the store of
	its status (approved stores for an Approved lot), and for a lot not Approved any approved store
	still holding some (stock an earlier version let in there, or that the cutover found there).
	Never the rejected or the returns store for a lot in quarantine or on hold: the quantity rejected
	at the door and goods a customer sent back are decided on their own paths."""
	if status == APPROVED:
		return frozenset({stores.APPROVED})
	return frozenset({ROLE_FOR_STATUS.get(status, stores.QUARANTINE), stores.APPROVED})


def last_arrival(lot: str, warehouses: Collection[str]) -> datetime | None:
	"""When stock of the lot last came into one of `warehouses`, from the stock ledger (old-style
	lots on the entry, bundled lots on the bundle's rows): an inspection of goods that came back is
	one made after they did."""
	return last_arrivals([lot], warehouses).get(lot)


def last_arrivals(lots: Collection[str], warehouses: Collection[str]) -> dict[str, datetime]:
	"""lot -> when its stock last came into one of `warehouses` (`last_arrival`), for all the lots in
	two grouped queries, however many they are."""
	if not warehouses or not lots:
		return {}
	sle = frappe.qb.DocType("Stock Ledger Entry")
	entry = frappe.qb.DocType("Serial and Batch Entry")
	arrived = (sle.is_cancelled == 0) & (sle.actual_qty > 0) & sle.warehouse.isin(list(warehouses))
	plain = (
		frappe.qb.from_(sle)
		.select(sle.batch_no, Max(sle.creation))
		.where(arrived & sle.batch_no.isin(list(lots)))
		.groupby(sle.batch_no)
	)
	bundled = (
		frappe.qb.from_(sle)
		.inner_join(entry)
		.on(entry.parent == sle.serial_and_batch_bundle)
		.select(entry.batch_no, Max(sle.creation))
		.where(arrived & entry.batch_no.isin(list(lots)))
		.groupby(entry.batch_no)
	)
	latest: dict[str, datetime] = {}
	for query in (plain, bundled):
		for lot, time in query.run():
			if time and (lot not in latest or get_datetime(time) > latest[lot]):
				latest[lot] = get_datetime(time)
	return latest


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


def stock_by_lot(
	item: str | None = None, warehouse: str | list[str] | None = None, lot: str | None = None
) -> list[frappe._dict]:
	"""[{item_code, warehouse, batch_no, qty}] for every lot with stock, in two grouped queries
	(ERPNext keeps old-style lots on the ledger entry and bundled lots on the bundle's rows); in one
	store, or in any of a list of stores."""
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
	# Each query with the field its lot is on.
	for query, lot_field in ((plain, sle.batch_no), (bundled, entry.batch_no)):
		if item:
			query = query.where(sle.item_code == item)
		if warehouse:
			query = query.where(
				sle.warehouse.isin(warehouse) if isinstance(warehouse, list) else sle.warehouse == warehouse
			)
		if lot:
			query = query.where(lot_field == lot)
		for row in query.run(as_dict=True):
			key = (row.item_code, row.warehouse, row.batch_no)
			totals[key] = totals.get(key, 0) + flt(row.qty)
	precision = qty_precision()
	return [
		frappe._dict(item_code=i, warehouse=w, batch_no=b, qty=flt(q, precision))
		for (i, w, b), q in sorted(totals.items())
		if flt(q, precision) > 0
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
