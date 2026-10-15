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

A completed calibration, maintenance or qualification record is corrected only by QA's signed
Correct, with a reason (decided 2026-10-12, plan D-29): the act cancels the record under that
signature and opens its amendment as a new draft, which is completed again by its own signed act.
Frappe's own Cancel and the API are refused: cancelling a failed record unsigned would make the
pass before it the latest, and the equipment fit again. From the moment the record is cancelled
until its amendment is completed, the equipment is not fit, whatever the records before it say.

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

from meddevice_suite.compliance.checks import QA_ROLE, require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import equipment

CALIBRATION, MAINTENANCE, QUALIFICATION = (
	"Calibration Record",
	"Maintenance Record",
	"Equipment Qualification",
)
# The signed act that completes each record type. A cancelled record carrying its signature was
# completed and then withdrawn for correction; a draft discarded unfinished carries none.
COMPLETED_BY_ACT = {CALIBRATION: "Complete", MAINTENANCE: "Complete", QUALIFICATION: "Approve"}
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


def being_corrected(doctype: str, assets: list[str]) -> dict[str, list[str]]:
	"""Each asset's records of this type withdrawn for correction whose correction is not yet
	completed, by asset (plan D-29). A withdrawn record is no longer read as done, so without this
	the record before it, perhaps a pass before a failure, would decide fitness again.

	A correction is done once an amendment of the record is completed, or once an amendment was
	itself completed and then withdrawn: that one then carries the pending correction. A draft
	amendment, or one discarded unfinished, does not settle it."""
	cancelled = frappe.get_all(
		doctype,
		filters={"asset": ["in", assets], "docstatus": 2},
		fields=["name", "asset"],
		order_by="performed_on asc, creation asc",
	)
	if not cancelled:
		return {}
	names = [row.name for row in cancelled]
	amendments = frappe.get_all(
		doctype,
		filters={"amended_from": ["in", names], "docstatus": ["in", (1, 2)]},
		fields=["name", "amended_from", "docstatus"],
	)
	completed = set(
		frappe.get_all(
			"Electronic Signature",
			filters={
				"reference_doctype": doctype,
				"reference_name": ["in", names + [row.name for row in amendments]],
				"action": COMPLETED_BY_ACT[doctype],
			},
			pluck="reference_name",
		)
	)
	settled = {row.amended_from for row in amendments if row.docstatus == 1 or row.name in completed}
	pending: dict[str, list[str]] = {}
	for row in cancelled:
		if row.name in completed and row.name not in settled:
			pending.setdefault(row.asset, []).append(row.name)
	return pending


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
	correcting = {doctype: being_corrected(doctype, assets) for doctype in COMPLETED_BY_ACT}
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
		for needed, doctype, what in (
			(equipment.CALIBRATION, CALIBRATION, _("calibration")),
			(equipment.MAINTENANCE, MAINTENANCE, _("preventive maintenance")),
			(equipment.QUALIFICATION, QUALIFICATION, _("qualification")),
		):
			# Whatever the records before it say: the one withdrawn may be the one that failed.
			if cint(need.get(needed)):
				why.extend(
					_("its {0} {1} is being corrected").format(what, name)
					for name in correcting[doctype].get(asset, ())
				)
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


# --- Correction of a completed record (plan D-29) -------------------------------------------------


def only_by_correction(doc: Document, method: str | None = None) -> None:
	"""before_cancel of a calibration, maintenance or qualification record: Frappe's Cancel button
	and the API would withdraw a completed record unsigned, and the equipment would then be judged
	on the record before it. Only QA's signed Correct cancels one."""
	if not doc.flags.care_correction:
		frappe.throw(
			_("{0} is not cancelled on its own; QA corrects it by signing Correct, with a reason.").format(
				doc.name
			),
			frappe.PermissionError,
		)


def correct(doc: Document, password: str, reason: str | None = None) -> str:
	"""Signed act: QA withdraws a completed record for correction, saying why. The record is
	cancelled under this signature and its amendment opened as a draft, which is completed again by
	its own signed act; until then the equipment is not fit (`being_corrected`). Returns the
	amendment's name, for the form to open it."""
	require(doc.doctype in COMPLETED_BY_ACT, _("{0} is not corrected this way.").format(_(doc.doctype)))
	require(
		doc.docstatus == 1, _("{0} is not completed; only a completed record is corrected.").format(doc.name)
	)
	require(QA_ROLE in frappe.get_roles(), _("Only QA corrects a completed record."), frappe.PermissionError)
	require(reason, _("Say what is to be corrected and why."))
	sign(doc, "Rejected", "Correct", password, reason, permission="read")
	doc.flags.care_correction = True
	doc.flags.audit_reason = reason  # the audit trail asks the reason for the cancellation
	doc.flags.ignore_permissions = True  # nobody holds Frappe's cancel: this signed act is its only cancel
	doc.cancel()
	# Copied without what the signed acts set (the fields not to copy): the amendment is completed
	# afresh. Frappe's Amend on the form copies those too, and each record type clears them on a draft.
	amendment = frappe.copy_doc(doc)
	amendment.docstatus = 0  # a draft, also where Frappe's copy keeps the status (in its tests)
	amendment.amended_from = doc.name
	amendment.flags.audit_reason = reason  # and for the amendment
	amendment.insert()
	return amendment.name


def copy_attachments(amendment: Document) -> None:
	"""The amendment of a corrected record carries the files of the record it corrects, as Frappe's
	amend does, copied from that record's own file rows. Frappe's copy checks the signer against
	whichever file first has the same address, and identical files share one: one laboratory
	certificate often covers several instruments, so a file another person uploaded for another
	instrument refused the correction. The signer read the corrected record (`correct` checks it), and
	its files are already stored and validated; only the record they are attached to changes."""
	for name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": amendment.doctype, "attached_to_name": amendment.amended_from},
		pluck="name",
	):
		copy = frappe.copy_doc(frappe.get_doc("File", name))
		copy.attached_to_name = amendment.name
		copy.set_new_name()
		copy.set_user_and_timestamp()
		copy.db_insert()  # the copy of a stored file; Frappe's insert would run the check above again


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
