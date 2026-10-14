"""Records pinned at a version (shared by module 39, risk management, and module 31, the device
master record; ISO 14971 7.2, ISO 13485 4.2.3, 7.3.9; 21 CFR 820.181).

An approved record that depends on other records names each of them with the version it was
approved against: a risk control names the specification, item, bill of materials or operation
that implements it, and a device master record its bill, routing and documents. Once approved, a
newer version of such a record, or its withdrawal, means the approved record may no longer be
true, and its owner must look at it again.

- `version_token` says which version of a record is the one in force: a controlled document's
  current revision, a master's version number (master_data/versioning.py), for a bill of
  materials whether it is still submitted, active and the default, and for a risk management
  report whether it is still approved.
- `in_force` says whether the record may be relied on now: the document is effective, the
  master (a specification too) approved and enabled, the bill submitted (QA's signed approval is its only submit),
  active and the default, the risk management report the one in force of its file.
- `on_pinned_change` runs when such a record is saved and asks each owner in `PIN_OWNERS` for
  the pins on it; every pin whose version is no longer the one in force, or whose record is no
  longer in force, is handed to the owner's reaction in the same transaction. `sweep` does the
  same for every pin daily, for changes written past the document hooks (a default bill switched
  off by ERPNext, a database correction). A record whose change runs no document hook of its own
  (a risk management report superseded by its file's next report or revision) is handed in by the
  code that changes it, through `records_changed`.

Each owner registers two functions: which pins it holds, and what it does about those that
changed. A failing owner is logged and does not hold back the change of the record, nor the
other owners; the daily sweep finds the pin again.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from meddevice_suite.master_data import item_master, master_approval
from meddevice_suite.master_data.versioning import VERSION

CONTROLLED_DOCUMENT, BOM, ITEM = "Controlled Document", "BOM", "Item"
SPECIFICATION = "Quality Inspection Template"
# Module 39's report and its file; named here, as risk_management builds on this module.
RISK_REPORT, RISK_FILE = "Risk Management Report", "Risk Management File"
# Masters whose version number versioning raises at each controlled change.
VERSIONED = (ITEM, "Routing", "Operation", SPECIFICATION)
# Masters approved with master_approval's signed act (module 3).
MASTER_APPROVED = ("Routing", "Operation")
PINNABLE = (CONTROLLED_DOCUMENT, BOM, *VERSIONED, RISK_REPORT)
SAVEPOINT = "pinned_records"


@dataclass(frozen=True)
class Pin:
	"""One record pinned by an owner, with the version it was pinned at."""

	owner: str  # the owner's record, such as a risk management file
	doctype: str
	name: str
	version: str | None
	row: str | None = None  # the owner's row that holds the pin


@dataclass(frozen=True)
class Change:
	"""A pin whose record has moved on: its version now, and whether it is still in force."""

	pin: Pin
	current: str | None
	in_force: bool


@dataclass(frozen=True)
class PinOwner:
	"""Dotted paths of the owner's two functions:
	- pins(targets: dict[str, list[str]] | None) -> list[Pin]: the pins on the target records
	  (doctype -> names), or every pin when None, of owners whose approval is in force;
	- react(changes: list[Change]) -> None."""

	pins: str
	react: str


# The record types that pin records, and how each reacts.
PIN_OWNERS: dict[str, PinOwner] = {
	# Module 39: a review of the risk file when a control changes or is withdrawn (URS-QMS-025).
	"Risk Management File": PinOwner(
		pins="meddevice_suite.qms.risk_management.control_pins",
		react="meddevice_suite.qms.risk_management.controls_changed",
	),
	# Module 31: the device master record in force goes out of date (URS-QMS-029).
	"Device Master Record": PinOwner(
		pins="meddevice_suite.qms.device_master_record.element_pins",
		react="meddevice_suite.qms.device_master_record.elements_changed",
	),
}


def require_pinnable(doctype: str) -> None:
	if doctype not in PINNABLE:
		frappe.throw(_("{0} records are not pinned at a version.").format(_(doctype)))


def clean(names: Iterable[str | None]) -> list[str]:
	return sorted({name for name in names if name})


# The columns each record type's version is read from; any other type, its version number.
TOKEN_COLUMNS: dict[str, tuple[str, ...]] = {
	CONTROLLED_DOCUMENT: ("current_revision",),
	BOM: ("is_active", "is_default", "docstatus"),
	RISK_REPORT: ("status", "docstatus"),
}


def version_tokens(doctype: str, names: Iterable[str | None], lock: bool = False) -> dict[str, str]:
	"""The version in force of each existing record, in one query; `lock` reads them with row
	locks held to the end of the transaction, so that none of them changes while what is read is
	being approved (a device master record's approval, module 31)."""
	require_pinnable(doctype)
	names = clean(names)
	if not names:
		return {}
	table = frappe.qb.DocType(doctype)
	columns = TOKEN_COLUMNS.get(doctype, (VERSION,))
	query = (
		frappe.qb.from_(table)
		.select(table.name, *(table[column] for column in columns))
		.where(table.name.isin(names))
	)
	if lock:
		query = query.for_update()
	rows = query.run(as_dict=True)
	if doctype == CONTROLLED_DOCUMENT:
		return {row.name: row.current_revision or "" for row in rows}
	if doctype == BOM:
		return {
			row.name: f"{row.name}|{cint(row.is_active)}|{cint(row.is_default)}|{cint(row.docstatus)}"
			for row in rows
		}
	if doctype == RISK_REPORT:
		# An approved report never changes; what moves on is whether it is still approved.
		return {row.name: f"{row.name}|{row.status}|{cint(row.docstatus)}" for row in rows}
	return {row.name: str(cint(row.get(VERSION))) for row in rows}


def version_token(doctype: str, name: str) -> str | None:
	return version_tokens(doctype, [name]).get(name)


def in_force_names(doctype: str, names: Iterable[str | None]) -> set[str]:
	"""Of the records named, those that may be relied on now, in one query."""
	require_pinnable(doctype)
	names = clean(names)
	if not names:
		return set()
	filters: dict = {"name": ["in", names]}
	if doctype == CONTROLLED_DOCUMENT:
		filters["status"] = "Effective"
	elif doctype == BOM:
		filters.update({"docstatus": 1, "is_active": 1, "is_default": 1})
	elif doctype == ITEM:
		filters.update({item_master.APPROVAL_STATUS: item_master.APPROVED, "disabled": 0})
	elif doctype in MASTER_APPROVED:
		filters[master_approval.APPROVAL_STATUS] = master_approval.APPROVED
		if frappe.get_meta(doctype).has_field("disabled"):
			filters["disabled"] = 0
	elif doctype == SPECIFICATION:
		# A registered specification states its item and stage (module 6), and QA has approved what
		# it says now (plan D-33).
		filters.update(
			{
				"mds_item": ["is", "set"],
				"mds_stage": ["is", "set"],
				master_approval.APPROVAL_STATUS: master_approval.APPROVED,
			}
		)
	elif doctype == RISK_REPORT:
		# Approved, and the report of a file whose approved revision is in force (module 39).
		filters.update({"docstatus": 1, "status": "Approved"})
		current = frappe.get_all(
			RISK_FILE, filters={"current_report": ["in", names], "status": "In Force"}, pluck="current_report"
		)
		filters["name"] = ["in", sorted(set(current))]
		if not current:
			return set()
	return set(frappe.get_all(doctype, filters=filters, pluck="name"))


def in_force(doctype: str, name: str) -> bool:
	return name in in_force_names(doctype, [name])


def changes_of(pins: list[Pin]) -> list[Change]:
	"""The pins whose record has a new version or is no longer in force: one query per type."""
	by_type: dict[str, list[str]] = {}
	for pin in pins:
		by_type.setdefault(pin.doctype, []).append(pin.name)
	tokens, force = {}, {}
	for doctype, names in by_type.items():
		tokens[doctype] = version_tokens(doctype, names)
		force[doctype] = in_force_names(doctype, names)
	changes = []
	for pin in pins:
		current = tokens[pin.doctype].get(pin.name)
		live = pin.name in force[pin.doctype]
		if current != pin.version or not live:
			changes.append(Change(pin, current, live))
	return changes


def check(targets: dict[str, list[str]] | None) -> None:
	"""Hand each owner the changes among its pins on `targets` (None: every pin)."""
	for owner_type, owner in PIN_OWNERS.items():
		frappe.db.savepoint(SAVEPOINT)
		try:
			if changes := changes_of(frappe.get_attr(owner.pins)(targets)):
				frappe.get_attr(owner.react)(changes)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"Pinned records of {owner_type} not checked")
		else:
			frappe.db.release_savepoint(SAVEPOINT)


def on_pinned_change(doc: Document, method: str | None = None) -> None:
	"""on_update of the versioned masters and controlled documents; on_submit,
	on_update_after_submit and on_cancel of a bill of materials. A new default bill switches the
	product's other bills off in the database, so every bill of the product is looked at."""
	if doc.doctype not in PINNABLE:
		return
	names = [doc.name]
	if doc.doctype == BOM and doc.get("item"):
		names += frappe.get_all(BOM, filters={"item": doc.item, "name": ["!=", doc.name]}, pluck="name")
	check({doc.doctype: names})


def records_changed(doctype: str, names: Iterable[str | None]) -> None:
	"""Records changed past their own document hooks, by code that knows it changed them: risk
	management supersedes a report with `db_set` when the file's next report or revision is
	approved (module 39)."""
	require_pinnable(doctype)
	if names := clean(names):
		check({doctype: names})


def sweep() -> None:
	"""Daily: every pin, for changes made past the document hooks."""
	check(None)
