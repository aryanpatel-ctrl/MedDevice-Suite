"""Equipment history card (module 62; URS-MST-011; DFD 9D: D2 history card, D5 performance
tracking; ISO 13485 6.3, 7.6).

Everything that happened to one piece of equipment, newest first: QA's approval and every change
to its record (from the audit trail), its movements, its maintenance and calibration logs, its repairs with their
downtime, and the inspections that used it as their instrument. The summary gives the number of
repairs and the total downtime over the period. It reads the records ERPNext and this app keep;
it records nothing itself.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, today

from meddevice_suite.master_data.equipment import APPROVAL_FIELDS


def columns() -> list[dict]:
	"""The report's columns, labelled in the reader's language."""
	return [
		{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "event", "label": _("Event"), "fieldtype": "Data", "width": 170},
		{
			"fieldname": "record_type",
			"label": _("Record Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 150,
		},
		{
			"fieldname": "record",
			"label": _("Record"),
			"fieldtype": "Dynamic Link",
			"options": "record_type",
			"width": 180,
		},
		{"fieldname": "details", "label": _("Details"), "fieldtype": "Data", "width": 360},
		{"fieldname": "by", "label": _("By"), "fieldtype": "Link", "options": "User", "width": 180},
		{"fieldname": "downtime", "label": _("Downtime (hours)"), "fieldtype": "Float", "width": 120},
	]


def execute(filters: dict | None = None) -> tuple:
	filters = frappe._dict(filters or {})
	if not filters.get("asset"):
		frappe.throw(_("Choose the equipment."))
	frappe.has_permission("Asset", "read", doc=filters.asset, throw=True)
	# The whole life of the equipment unless a period is chosen.
	start = getdate(filters.get("from_date") or frappe.db.get_value("Asset", filters.asset, "creation"))
	end = getdate(filters.get("to_date") or today())
	rows = [row for row in events(filters.asset) if row["date"] and start <= getdate(row["date"]) <= end]
	rows.sort(key=lambda row: (get_datetime(row["date"]), row.get("order", 0)), reverse=True)
	for row in rows:
		row.pop("order", None)
	repairs = [row for row in rows if row["record_type"] == "Asset Repair"]
	summary = [
		{"label": _("Repairs"), "value": len(repairs), "datatype": "Int"},
		{
			"label": _("Downtime (hours)"),
			"value": sum(flt(r["downtime"]) for r in repairs),
			"datatype": "Float",
		},
		{
			"label": _("Inspections using it"),
			"value": sum(1 for row in rows if row["record_type"] == "Quality Inspection"),
			"datatype": "Int",
		},
	]
	return columns(), rows, None, None, summary


def events(asset: str) -> list[dict]:
	"""Every event of the equipment, from each record that keeps one."""
	return [
		*signatures(asset),
		*changes(asset),
		*movements(asset),
		*maintenance(asset),
		*repairs(asset),
		*inspections(asset),
	]


def row(
	date, event: str, record_type: str, record: str, details: str = "", by: str | None = None, **extra
) -> dict:
	return {
		"date": date,
		"event": event,
		"record_type": record_type,
		"record": record,
		"details": details,
		"by": by,
		"downtime": None,
		**extra,
	}


def signatures(asset: str) -> list[dict]:
	return [
		row(
			s.signed_at,
			_("Signed: {0}").format(_(s.meaning)),
			"Electronic Signature",
			s.name,
			s.reason or "",
			s.signer,
			order=2,
		)
		for s in frappe.get_all(
			"Electronic Signature",
			filters={"reference_doctype": "Asset", "reference_name": asset},
			fields=["name", "signed_at", "meaning", "reason", "signer"],
		)
	]


def changes(asset: str) -> list[dict]:
	"""When the record was made and each change to it, from the audit trail (module 50), with the
	reason given; the approval fields are shown by the signature itself."""
	meta = frappe.get_meta("Asset")
	out = []
	for event in frappe.get_all(
		"Audit Event",
		filters={"reference_doctype": "Asset", "reference_name": asset},
		fields=["name", "event_at", "user", "action", "reason", "changes"],
	):
		data = event.changes if isinstance(event.changes, dict) else json.loads(event.changes or "{}")
		changed = [
			_("{0}: {1} to {2}").format(
				_(meta.get_label(field)),
				old if old not in (None, "") else "-",
				new if new not in (None, "") else "-",
			)
			for field, old, new in data.get("changed") or []
			if field not in APPROVAL_FIELDS
		]
		if event.action == "Created":
			out.append(
				row(event.event_at, _("Recorded"), "Audit Event", event.name, event.reason or "", event.user)
			)
		elif changed:
			details = "; ".join(changed) + (f" ({event.reason})" if event.reason else "")
			out.append(
				row(event.event_at, _(event.action), "Audit Event", event.name, details, event.user, order=1)
			)
	return out


def movements(asset: str) -> list[dict]:
	items = frappe.get_all(
		"Asset Movement Item",
		filters={"asset": asset, "parenttype": "Asset Movement"},
		fields=["parent", "source_location", "target_location", "from_employee", "to_employee"],
	)
	moves = (
		{
			m.name: m
			for m in frappe.get_all(
				"Asset Movement",
				filters={"name": ["in", [i.parent for i in items]], "docstatus": 1},
				fields=["name", "transaction_date", "purpose", "owner"],
			)
		}
		if items
		else {}
	)
	return [
		row(
			moves[i.parent].transaction_date,
			_("Moved ({0})").format(_(moves[i.parent].purpose)),
			"Asset Movement",
			i.parent,
			_("From {0} to {1}").format(
				i.source_location or i.from_employee or "-", i.target_location or i.to_employee or "-"
			),
			moves[i.parent].owner,
		)
		for i in items
		if i.parent in moves
	]


def maintenance(asset: str) -> list[dict]:
	plans = frappe.get_all("Asset Maintenance", filters={"asset_name": asset}, pluck="name")
	if not plans:
		return []
	return [
		row(
			log.completion_date or log.due_date,
			_("{0}: {1}").format(_(log.maintenance_type or "Maintenance"), _(log.maintenance_status)),
			"Asset Maintenance Log",
			log.name,
			", ".join(part for part in (log.task_name, log.actions_performed) if part),
			log.owner,
		)
		for log in frappe.get_all(
			"Asset Maintenance Log",
			# Done or overdue; a planned log is the schedule, not history (module 38).
			filters={
				"asset_maintenance": ["in", plans],
				"docstatus": ["!=", 2],
				"maintenance_status": ["in", ["Completed", "Overdue"]],
			},
			fields=[
				"name",
				"completion_date",
				"due_date",
				"maintenance_type",
				"maintenance_status",
				"task_name",
				"actions_performed",
				"owner",
			],
		)
	]


def repairs(asset: str) -> list[dict]:
	return [
		row(
			r.failure_date,
			_("Repair: {0}").format(_(r.repair_status)),
			"Asset Repair",
			r.name,
			r.description or "",
			r.owner,
			downtime=hours(r.downtime),
		)
		for r in frappe.get_all(
			"Asset Repair",
			filters={"asset": asset, "docstatus": ["!=", 2]},
			fields=["name", "failure_date", "repair_status", "description", "downtime", "owner"],
		)
	]


def hours(downtime: str | None) -> float:
	"""ERPNext keeps a repair's downtime as text, such as "12.5 Hrs"."""
	found = re.match(r"\s*(\d+(?:\.\d+)?)", downtime or "")
	return flt(found.group(1)) if found else 0.0


def inspections(asset: str) -> list[dict]:
	parents = sorted(
		set(
			frappe.get_all(
				"Quality Inspection Reading",
				filters={"mds_instrument": asset, "parenttype": "Quality Inspection"},
				pluck="parent",
			)
		)
	)
	if not parents:
		return []
	return [
		row(
			qi.report_date,
			_("Used on inspection ({0})").format(_(qi.status)),
			"Quality Inspection",
			qi.name,
			", ".join(part for part in (qi.item_code, qi.batch_no) if part),
			qi.inspected_by or qi.owner,
		)
		for qi in frappe.get_all(
			"Quality Inspection",
			filters={"name": ["in", parents], "docstatus": 1},
			fields=["name", "report_date", "status", "item_code", "batch_no", "inspected_by", "owner"],
		)
	]
