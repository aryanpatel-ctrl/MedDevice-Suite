"""Calibration (module 38; URS-EQP-001, URS-EQP-002, URS-EQP-003; ISO 13485 7.6; 21 CFR 11.50).

A Calibration Record holds, per point, the nominal value, its limits and the values found before
and left after adjustment; the system works out what was in tolerance, the result (on the values
left: the instrument as it goes back into use) and, for a pass, the next due date from the
interval QA approved on the equipment. An in-house calibration follows an effective procedure and
names its reference standards, each approved equipment other than the instrument and itself in
calibration on the day; an external one names the laboratory, its certificate number and the
certificate, which becomes evidence. The record is completed only by its performer's signature
(Maintenance or QA), never deleted, and corrected only by QA's signed Correct (plan D-29,
`equipment_care.correct`), which cancels it and opens its amendment.

A value found out of tolerance means the instrument may have measured wrongly since its last good
calibration: the record lists the inspections that named it and the job cards at each workstation
it was at since then, each only for the days it was there, QA is alerted, and the record waits for
QA's signed assessment (no product impact, or product action needed), which a reminder tracks until
it is signed. A record withdrawn for correction keeps its open assessment, still tracked, until QA
signs it on that record: what was found on the instrument stays with the record that found it, and
a correction cannot make it, or the reminder's overdue days, disappear.

The list is taken when the record is completed, but production records work when it is done: a job
card drafted before the finding and submitted after it, or an inspection submitted later with an
earlier report date, falls in the period already assessed. Each one is added to the record's list of
records submitted since, by the same rule, when it is submitted, and the assessment is open again
for QA, who is alerted; the audit trail records the addition with the record that caused it
(independent review of 2026-10-15; ISO 13485 7.6). The submit is not refused: production records
what happened.

Where the instrument was is read from the audit trail (module 50), which records every change of
its workstation with the time and the old and new values, and is tamper-evident; not from the
asset as it is now, which would miss the job cards of a workstation it has since left and take in
those of its new one from before it came (found in the foundation review of 2026-10-12).
"""

from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime

from meddevice_suite.compliance.audit import as_data
from meddevice_suite.compliance.checks import QA_ROLE, require
from meddevice_suite.compliance.esign import sign
from meddevice_suite.master_data import equipment
from meddevice_suite.qms import equipment_care

DOCTYPE = "Calibration Record"
IN_HOUSE, EXTERNAL = "In-house", "External laboratory"
PASS, FAIL = "Pass", "Fail"
NOT_NEEDED, OPEN, ASSESSED = "Not needed", "Open", "Assessed"
NO_IMPACT, ACTION_NEEDED = "No product impact", "Product action needed"
# The records of the period assessed submitted after the record was completed (`add_later_use`).
LATER_IMPACT = "later_impact"
OUT_OF_TOLERANCE_ALERT = "Calibration Out of Tolerance"
PROCEDURE_TYPES = ("SOP", "Work Instruction")
PERFORMERS = frozenset({"Maintenance User", "Quality Manager"})
PRECISION = 9
# Set only by the signed acts. A draft never holds them, though Frappe's Amend copies them from the
# cancelled record: an amendment is completed, and its impact assessed, afresh.
SET_BY_ACTS = (
	"interval_days",
	"next_due_date",
	"completed_by",
	"last_good_calibration",
	"impact",
	LATER_IMPACT,
	"impact_status",
	"impact_decision",
	"assessed_by",
	"assessed_on",
)
TABLES_SET_BY_ACTS = frozenset({"impact", LATER_IMPACT})


def only_by_signature(doc: Document, method: str | None = None) -> None:
	"""before_validate: a record is completed (submitted) only by its signed act."""
	if doc.get("_action") == "submit" and not doc.flags.calibration_signed:
		frappe.throw(_("A calibration is completed by its performer's signature."), frappe.PermissionError)


def in_tolerance(value, low, high) -> int:
	return int(flt(low, PRECISION) <= flt(value, PRECISION) <= flt(high, PRECISION))


def effective_procedure(name: str | None, types: tuple[str, ...]) -> bool:
	if not name:
		return False
	row = frappe.db.get_value("Controlled Document", name, ["status", "document_type"], as_dict=True)
	return bool(row) and row.status == "Effective" and row.document_type in types


def last_done(asset: str, before: str | None = None, result: str | None = None) -> frappe._dict | None:
	filters = {"asset": asset, "docstatus": 1}
	if before:
		filters["name"] = ["!=", before]
	if result:
		filters["result"] = result
	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=["name", "performed_on", "result", "next_due_date"],
		order_by="performed_on desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def evaluate(doc: Document, method: str | None = None) -> None:
	"""validate: what the record may hold, and what follows from its points."""
	if doc.docstatus != 0:
		return
	for field in SET_BY_ACTS:
		doc.set(field, [] if field in TABLES_SET_BY_ACTS else None)
	asset = frappe.db.get_value(
		"Asset", doc.asset, [equipment.APPROVAL_STATUS, equipment.STATUS, equipment.CALIBRATION], as_dict=True
	)
	require(asset, _("Equipment {0} does not exist.").format(doc.asset))
	require(
		asset.get(equipment.APPROVAL_STATUS) == equipment.APPROVED,
		_("{0} is not approved equipment.").format(doc.asset),
	)
	require(asset.get(equipment.STATUS) != equipment.RETIRED, _("{0} is retired.").format(doc.asset))
	require(cint(asset.get(equipment.CALIBRATION)), _("{0} does not need calibration.").format(doc.asset))
	equipment_care.require_done(doc.performed_on, _("A calibration is recorded once it is done."))
	previous = last_done(doc.asset, before=doc.name)
	require(
		not previous or getdate(doc.performed_on) >= getdate(previous.performed_on),
		_("{0} was calibrated on {1} already; a calibration is recorded in order.").format(
			doc.asset, frappe.format_value(previous and previous.performed_on, {"fieldtype": "Date"})
		),
	)
	if doc.calibrated_by == IN_HOUSE:
		require(
			effective_procedure(doc.procedure, PROCEDURE_TYPES),
			_("An in-house calibration follows an effective SOP or work instruction."),
		)
		require(doc.standards, _("Name the reference standards used."))
		check_standards(doc)
	else:
		require(
			doc.laboratory and (doc.certificate_number or "").strip() and doc.certificate,
			_("An external calibration names the laboratory, its certificate number and the certificate."),
		)
	require(doc.points, _("Record the points calibrated."))
	for row in doc.points:
		require(
			flt(row.lower_limit, PRECISION) <= flt(row.upper_limit, PRECISION),
			_("Row {0}: the lower limit is above the upper limit.").format(row.idx),
		)
		row.found_in_tolerance = in_tolerance(row.as_found, row.lower_limit, row.upper_limit)
		row.left_in_tolerance = in_tolerance(row.as_left, row.lower_limit, row.upper_limit)
	doc.out_of_tolerance = int(not all(row.found_in_tolerance for row in doc.points))
	doc.result = PASS if all(row.left_in_tolerance for row in doc.points) else FAIL


def check_standards(doc: Document) -> None:
	"""URS-EQP-002: each reference standard is approved equipment, not the instrument, and was in
	calibration on the day the calibration was done."""
	day = getdate(doc.performed_on)
	for row in doc.standards:
		require(
			row.standard != doc.asset,
			_("Row {0}: an instrument is not its own reference standard.").format(row.idx),
		)
		require(
			frappe.db.get_value("Asset", row.standard, equipment.APPROVAL_STATUS) == equipment.APPROVED,
			_("Reference standard {0} is not approved equipment.").format(row.standard),
		)
		covering = frappe.get_all(
			DOCTYPE,
			filters={
				"asset": row.standard,
				"docstatus": 1,
				"result": PASS,
				"performed_on": ["<=", day],
				"next_due_date": [">=", day],
			},
			fields=["next_due_date"],
			order_by="performed_on desc",
			limit=1,
		)
		require(
			covering,
			_("Reference standard {0} was not in calibration on {1}.").format(
				row.standard, frappe.format_value(day, {"fieldtype": "Date"})
			),
		)
		row.standard_due_on = covering[0].next_due_date


def workstation_history(asset: str, start, end) -> list[tuple[str, date, date]]:
	"""Where the instrument was from `start` to `end`: (workstation, first day, last day) for each
	workstation in turn, days at none left out. Read in one query, whatever the number of moves.

	It was, on the first day, where the first change of its workstation since then moved it from, or
	where it is now if none did. A move takes the day it was made into both workstations' intervals:
	the trail knows the time, a job card only its day, and a day in doubt is listed, not missed. A
	record made within the period was at no workstation before it was made."""
	first, last = getdate(start), getdate(end)
	instrument, event = frappe.qb.DocType("Asset"), frappe.qb.DocType("Audit Event")
	rows = (
		frappe.qb.from_(instrument)
		.left_join(event)
		.on(
			(event.reference_doctype == "Asset")
			& (event.reference_name == instrument.name)
			# A rename rewrites `reference_name` on every event, so the whole history is found.
			& (event.event_at >= get_datetime(first))
		)
		.select(instrument[equipment.WORKSTATION].as_("current_workstation"), event.event_at, event.changes)
		.where(instrument.name == asset)
		.orderby(event.event_at)
		.orderby(event.creation)
		.run(as_dict=True)
	)
	if not rows:
		return []
	moves = []  # (day, from, to), in the order they were made
	for row in rows:
		if not row.event_at:
			continue  # no event since the first day: the joined row carries only where it is now
		changes = as_data(row.changes) or {}
		if "snapshot" in changes:  # created (or amended) within the period
			moves.append((getdate(row.event_at), None, changes["snapshot"].get(equipment.WORKSTATION)))
		moves += [(getdate(row.event_at), old, new) for old, new in equipment.workstation_changes(changes)]
	place = moves[0][1] if moves else rows[0].current_workstation
	since, history = first, []
	for day, _from, to in moves:
		if day > last:
			break
		if (to or None) == (place or None):
			continue
		history.append((place, since, day))
		place, since = to, day
	history.append((place, since, last))
	return [(workstation, start_day, end_day) for workstation, start_day, end_day in history if workstation]


def impact_since(asset: str, start, end, only: tuple[str, str] | None = None) -> list[dict]:
	"""What the instrument may have measured wrongly: submitted inspections that named it, and
	submitted job cards at each workstation it was at, while it was there, from its last good
	calibration to this one. A job card's row names its workstation. `only` (record type, name)
	asks the same of one record: whether it falls in the period, by the same rule."""
	only_type, only_name = only or (None, None)
	rows = []
	if only_type in (None, "Quality Inspection"):
		inspection, reading = (
			frappe.qb.DocType("Quality Inspection"),
			frappe.qb.DocType("Quality Inspection Reading"),
		)
		query = (
			frappe.qb.from_(inspection)
			.join(reading)
			.on(reading.parent == inspection.name)
			.select(inspection.name, inspection.report_date, inspection.item_code, inspection.batch_no)
			.where(
				(inspection.docstatus == 1)
				& (reading.mds_instrument == asset)
				& (inspection.report_date >= getdate(start))
				& (inspection.report_date <= getdate(end))
			)
			.distinct()
			.orderby(inspection.report_date)
		)
		if only_name:
			query = query.where(inspection.name == only_name)
		rows = query.run(as_dict=True)
	impact = [
		{
			"reference_doctype": "Quality Inspection",
			"reference_name": row.name,
			"date": row.report_date,
			"item_code": row.item_code,
			"batch_no": row.batch_no,
		}
		for row in rows
	]
	if only_type not in (None, "Job Card"):
		return impact
	if history := workstation_history(asset, start, end):
		card = frappe.qb.DocType("Job Card")
		# One query for every workstation it was at; a card has one workstation, so it is listed once
		# even where two of its intervals meet on the day of a move.
		query = (
			frappe.qb.from_(card)
			.select(card.name, card.posting_date, card.production_item, card.workstation)
			.where(
				(card.docstatus == 1)
				& Criterion.any(
					(card.workstation == workstation) & card.posting_date.between(first_day, last_day)
					for workstation, first_day, last_day in history
				)
			)
			.orderby(card.posting_date)
			.orderby(card.name)
		)
		if only_name:
			query = query.where(card.name == only_name)
		cards = query.run(as_dict=True)
		impact += [
			{
				"reference_doctype": "Job Card",
				"reference_name": row.name,
				"date": row.posting_date,
				"item_code": row.production_item,
				"workstation": row.workstation,
			}
			for row in cards
		]
	return impact


def complete(doc: Document, password: str, reason: str | None = None) -> None:
	"""Signed act: the performer completes the calibration."""
	require(doc.docstatus == 0, _("{0} is completed already.").format(doc.name))
	require(
		PERFORMERS & set(frappe.get_roles()),
		_("Maintenance or QA completes a calibration."),
		frappe.PermissionError,
	)
	evaluate(doc)
	doc.interval_days = cint(frappe.db.get_value("Asset", doc.asset, equipment.CALIBRATION_DAYS))
	require(doc.interval_days > 0, _("{0} has no calibration interval.").format(doc.asset))
	doc.next_due_date = add_days(doc.performed_on, doc.interval_days) if doc.result == PASS else None
	doc.completed_by = frappe.session.user
	if doc.out_of_tolerance:
		good = last_done(doc.asset, before=doc.name, result=PASS)
		doc.last_good_calibration = (
			good.performed_on if good else getdate(frappe.db.get_value("Asset", doc.asset, "creation"))
		)
		doc.set("impact", impact_since(doc.asset, doc.last_good_calibration, doc.performed_on))
		doc.impact_status = OPEN
	else:
		doc.impact_status = NOT_NEEDED
	sign(doc, "Verified", "Complete", password, reason)
	doc.flags.calibration_signed = True
	# Nobody holds Frappe's submit permission on a calibration: the signed act is its only submit.
	doc.flags.ignore_permissions = True
	doc.submit()
	if doc.out_of_tolerance:
		frappe.get_doc("Notification", OUT_OF_TOLERANCE_ALERT).send(doc)


def add_later_use(doc: Document, method: str | None = None) -> None:
	"""Job Card and Quality Inspection on_submit: a record submitted after an instrument was found
	out of tolerance whose use of it falls in the period that finding assessed (by the rule of
	`impact_since`) is added to that calibration's records submitted since, and the assessment is
	open again for QA, who is alerted. Never refused: production records what happened.

	Only completed records are read: one withdrawn for correction is assessed afresh, on the whole
	period, when its amendment is completed, and an amendment completed already is itself completed
	(docstatus 1). The records found are those whose period covers the day: one for each out-of-
	tolerance finding of the instruments concerned, each read in its own few queries, as each reads
	its own instrument's place in the audit trail."""
	if doc.doctype == "Quality Inspection":
		day = doc.get("report_date")
		instruments = sorted({row.get("mds_instrument") for row in doc.get("readings") or []} - {None, ""})
		if not instruments:
			return
	else:
		day, instruments = doc.get("posting_date"), None
		if not doc.get("workstation"):
			return
	if not day:
		return
	filters = {
		"docstatus": 1,
		"out_of_tolerance": 1,
		"last_good_calibration": ["<=", getdate(day)],
		"performed_on": [">=", getdate(day)],
	}
	if instruments:
		filters["asset"] = ["in", instruments]
	for name in frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="performed_on asc"):
		record = frappe.get_doc(DOCTYPE, name)
		listed = {
			(row.reference_doctype, row.reference_name) for row in (*record.impact, *record.get(LATER_IMPACT))
		}
		if (doc.doctype, doc.name) in listed:
			continue
		rows = impact_since(
			record.asset, record.last_good_calibration, record.performed_on, (doc.doctype, doc.name)
		)
		if rows:
			reopen(record, rows, doc)


def reopen(record: Document, rows: list[dict], cause: Document) -> None:
	"""The rows added to the record's records submitted since, and its assessment open again: QA's
	earlier decision, kept in its signature and the audit trail, did not cover them."""
	record.extend(LATER_IMPACT, rows)
	record.impact_status = OPEN
	record.impact_decision = record.assessed_by = record.assessed_on = None
	record.flags.calibration_later_use = True
	record.flags.audit_reason = _(
		"{0} {1} was submitted after {2} was found out of tolerance and used it within the period assessed; QA assesses it."
	).format(_(cause.doctype), cause.name, record.asset)
	# The person submitting the job card or inspection may not change a calibration: this system
	# process, after its own checks, is the only way rows are added here.
	record.flags.ignore_permissions = True
	record.save()
	frappe.get_doc("Notification", OUT_OF_TOLERANCE_ALERT).send(record)


def assess(doc: Document, decision: str, act: str, password: str, reason: str | None) -> None:
	"""QA's signed assessment, on a completed record or on one withdrawn for correction (plan D-29),
	which keeps its open assessment."""
	require(
		doc.docstatus in (1, 2) and doc.impact_status == OPEN,
		_("{0} has no open impact assessment.").format(doc.name),
	)
	require(QA_ROLE in frappe.get_roles(), _("Only QA assesses the impact."), frappe.PermissionError)
	require(reason, _("Say what was assessed and why."))
	doc.impact_status, doc.impact_decision = ASSESSED, decision
	doc.assessed_by, doc.assessed_on = frappe.session.user, now_datetime()
	sign(doc, "Reviewed", act, password, reason, permission="read")
	if doc.docstatus == 2:
		# Frappe saves no cancelled record; the audit trail records these values as written by code.
		doc.db_set(
			{
				"impact_status": doc.impact_status,
				"impact_decision": doc.impact_decision,
				"assessed_by": doc.assessed_by,
				"assessed_on": doc.assessed_on,
			}
		)
		return
	doc.flags.calibration_signed = True
	doc.flags.audit_reason = reason  # the assessment is the reason for the change
	doc.flags.ignore_permissions = True  # the impact fields are set by this signed act only
	doc.save()


def assess_no_impact(doc: Document, password: str, reason: str | None = None) -> None:
	assess(doc, NO_IMPACT, "Assess: No Product Impact", password, reason)


def assess_product_action(doc: Document, password: str, reason: str | None = None) -> None:
	assess(doc, ACTION_NEEDED, "Assess: Product Action Needed", password, reason)


def guard_after_completion(doc: Document, method: str | None = None) -> None:
	"""before_update_after_submit: only the signed assessment, and a record of the period submitted
	since (`add_later_use`), change a completed record."""
	require(
		doc.flags.calibration_signed or doc.flags.calibration_later_use,
		_("A completed calibration does not change."),
		frappe.PermissionError,
	)


def refuse_erpnext_maintenance(doc: Document, method: str | None = None) -> None:
	"""Asset Maintenance and Asset Maintenance Log before_validate: equipment is calibrated and
	maintained on this app's signed records (URS-EQP-009)."""
	frappe.throw(
		_("Calibration and maintenance are recorded as Calibration and Maintenance Records, signed."),
		title=_("Not used"),
	)
