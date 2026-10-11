"""Whether equipment is fit to use: calibrated, maintained and qualified (modules 38 and 40;
URS-EQP-005, URS-EQP-006, URS-EQP-012; production stage 3: "machine must be within calibration and
maintenance due"; ISO 13485 7.6, 6.3, 7.5.6).

Equipment that needs calibration is fit only while its latest completed calibration passed and
today is not past its next due date; never calibrated, it is not fit. Maintenance likewise. One
that needs qualification is fit only while its latest approved IQ, OQ and PQ each passed, in that
order, and are within the requalification interval if one is set. Equipment under an open repair
is not fit either (read from the repair itself: ERPNext's daily asset status update looks pending
repairs up by the asset's display name, not its id, and can clear "Out of Order" the day after).

A completed repair can change what an instrument measures and how a machine performs, and a move
the conditions a machine was qualified in (decided 2026-10-11, URS-EQP-013): after a repair, an
instrument is fit again only once calibrated on or after the day the repair was completed, and a
qualified machine once a PQ was done on or after it; after a move (an ERPNext asset movement that
transfers it), a qualified machine is fit again only once qualified again, IQ, OQ and PQ, on or
after the day it moved. A move alone does not need a calibration: an instrument is carried by hand
and its calibration interval covers it.

`master_data.equipment.usable` applies this, so an inspection names only fit instruments; and a
job card, at every save, submit and time log, and a work order at submit, are refused while any
equipment at their workstations is not usable. Block, never warn.
"""

from collections.abc import Iterable

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Order
from frappe.utils import add_days, cint, getdate, today

from meddevice_suite.master_data import equipment

CALIBRATION, MAINTENANCE, QUALIFICATION = (
	"Calibration Record",
	"Maintenance Record",
	"Equipment Qualification",
)
# ERPNext asset movements that take equipment somewhere else; a receipt is its first placing and an
# issue hands it to a person.
RELOCATIONS = ("Transfer", "Transfer and Issue")


def latest(
	doctype: str, assets: list[str], fields: list[str], filters: dict | None = None
) -> dict[str, frappe._dict]:
	"""Each asset's latest completed record, by the date it was done."""
	rows = frappe.get_all(
		doctype,
		filters={"asset": ["in", assets], "docstatus": 1, **(filters or {})},
		fields=["asset", "performed_on", *fields],
		order_by="performed_on desc, creation desc",
	)
	found: dict[str, frappe._dict] = {}
	for row in rows:
		found.setdefault(row.asset, row)
	return found


def repaired_and_moved(assets: list[str]) -> tuple[dict, dict]:
	"""The day of each asset's latest completed repair, and of its latest relocation."""
	repaired: dict = {}
	for row in frappe.get_all(
		"Asset Repair",
		filters={"asset": ["in", assets], "docstatus": 1, "repair_status": "Completed"},
		fields=["asset", "completion_date"],
		order_by="completion_date desc",
	):
		repaired.setdefault(row.asset, getdate(row.completion_date))
	movement, item = frappe.qb.DocType("Asset Movement"), frappe.qb.DocType("Asset Movement Item")
	moved: dict = {}
	for row in (
		frappe.qb.from_(movement)
		.join(item)
		.on(item.parent == movement.name)
		.select(item.asset, movement.transaction_date)
		.where((movement.docstatus == 1) & movement.purpose.isin(RELOCATIONS) & item.asset.isin(assets))
		.orderby(movement.transaction_date, order=Order.desc)
		.run(as_dict=True)
	):
		moved.setdefault(row.asset, getdate(row.transaction_date))
	return repaired, moved


def unfit(assets: Iterable[str], on=None) -> dict[str, list[str]]:
	"""Why each piece of equipment is not fit on the day, by asset; fit equipment is left out."""
	assets = sorted(set(assets) - {None, ""})
	if not assets:
		return {}
	day = getdate(on or today())
	needs = {
		row.name: row
		for row in frappe.get_all(
			"Asset",
			filters={"name": ["in", assets]},
			fields=["name", equipment.CALIBRATION, equipment.MAINTENANCE, equipment.QUALIFICATION],
		)
	}
	calibrated = latest(CALIBRATION, assets, ["result", "next_due_date"])
	maintained = latest(MAINTENANCE, assets, ["result", "next_due_date"])
	from meddevice_suite.qms.qualification import not_qualified  # it reads these records too

	repaired, moved = repaired_and_moved(assets)
	unqualified = not_qualified(
		[a for a in assets if cint(needs.get(a, {}).get(equipment.QUALIFICATION))], day, repaired, moved
	)
	under_repair = set(
		frappe.get_all(
			"Asset Repair",
			filters={"asset": ["in", assets], "docstatus": 0, "repair_status": "Pending"},
			pluck="asset",
		)
	)
	reasons: dict[str, list[str]] = {}
	for asset in assets:
		why = []
		need = needs.get(asset) or {}
		for needed, record, what, passed in (
			(equipment.CALIBRATION, calibrated, _("calibration"), "Pass"),
			(equipment.MAINTENANCE, maintained, _("preventive maintenance"), "Satisfactory"),
		):
			if not cint(need.get(needed)):
				continue
			last = record.get(asset)
			if not last:
				why.append(_("it has had no {0}").format(what))
			elif last.result != passed:
				why.append(
					_("its last {0} on {1} did not pass").format(
						what, frappe.format_value(last.performed_on, {"fieldtype": "Date"})
					)
				)
			elif not last.next_due_date or getdate(last.next_due_date) < day:
				why.append(
					_("its {0} was due on {1}").format(
						what, frappe.format_value(last.next_due_date, {"fieldtype": "Date"})
					)
				)
			elif (
				needed == equipment.CALIBRATION
				and asset in repaired
				and getdate(last.performed_on) < repaired[asset]
			):
				why.append(
					_("it was repaired on {0} and has not been calibrated since").format(
						frappe.format_value(repaired[asset], {"fieldtype": "Date"})
					)
				)
		if asset in unqualified:
			why.append(unqualified[asset])
		if asset in under_repair:
			why.append(_("it is under repair"))
		if why:
			reasons[asset] = why
	return reasons


# --- At the point of use ------------------------------------------------------------------------


def equipment_at(workstations: Iterable[str]) -> list[str]:
	"""The equipment in use at these workstations. None when no workstation is named: Frappe reads
	`in [""]` as "no workstation set", which would take in every portable instrument on the site."""
	stations = sorted(set(workstations) - {None, ""})
	if not stations:
		return []
	return frappe.get_all(
		"Asset",
		filters={
			equipment.WORKSTATION: ["in", stations],
			equipment.STATUS: ["!=", equipment.RETIRED],
			"docstatus": ["<", 2],
		},
		pluck="name",
	)


def refuse_unusable(workstations: Iterable[str], what: str) -> None:
	for asset in equipment_at(workstations):
		state = equipment.usable(asset)
		if not state.ok:
			frappe.throw(
				_("{0} cannot be used: equipment {1} at its workstation is not fit, {2}.").format(
					what, asset, state.why
				),
				title=_("Equipment not fit"),
			)


def guard_job_card(doc: Document, method: str | None = None) -> None:
	"""Job Card before_validate, validate, before_submit and before_update_after_submit (time logs
	change after submit): no work is recorded at a workstation whose equipment is not fit."""
	if doc.docstatus == 2 or doc.get("_action") == "cancel":
		return
	refuse_unusable([doc.workstation], _("Job card {0}").format(doc.name or ""))


def guard_work_order(doc: Document, method: str | None = None) -> None:
	"""Work Order before_submit: its operations' workstations' equipment is fit."""
	refuse_unusable(
		[row.workstation for row in doc.get("operations") or []], _("Work order {0}").format(doc.name)
	)


@frappe.whitelist()
def make_time_log(kwargs: str | dict) -> None:
	"""ERPNext's job card time log (`job_card.make_time_log`), with the job card's rules first: it
	writes the time log row straight to the database, so no document hook would see it."""
	import json

	from erpnext.manufacturing.doctype.job_card.job_card import make_time_log as erpnext_make_time_log

	from meddevice_suite.master_data import routing
	from meddevice_suite.qms import training

	values = frappe._dict(json.loads(kwargs) if isinstance(kwargs, str) else kwargs)
	doc = frappe.get_doc("Job Card", values.job_card_id)
	doc.check_permission("write")
	routing.guard_job_card(doc)
	guard_job_card(doc)
	training.refuse_new_operators(doc, values)
	erpnext_make_time_log(kwargs)


def due_after(last_done, interval_days: int):
	return add_days(last_done, cint(interval_days)) if last_done and cint(interval_days) else None
