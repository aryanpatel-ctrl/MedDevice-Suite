"""Reminders and next due (module 60; URS-PLT-014, URS-PLT-015; DFD 9C: C1 define reminder category,
C2 set due date or frequency, C3 generate reminder, C4 notify users, C5 track and escalate, C6 mark
completed; data store D11 Task, Approval & Reminders).

Every due item the system alerts on is tracked as a Reminder from the day it falls within its lead
time until its own record shows the work done: a lot used up or kept apart, a certificate or a
licence renewed, a document reviewed, a change action done, an effectiveness check closed. The
categories, what makes an item due and what shows it done are declared here, in code; the lead
times are QA's, in Quality Settings. A system reminder is never ticked off by hand: only the work
in its record completes it, so the list cannot be cleared without the work being done.

The lead time decides when a reminder is raised, never when it is settled. A reminder that drops
out of what its category shows due within the lead time is settled only if the category, read with
no lead time at all (`whole_horizon`), no longer shows its item either; an item still to be done
(QA shortened the lead time, say) keeps its reminder open. An item due again after its reminder
was settled (a lot back from hold) is raised again: a settled reminder never stands in for an open
one (found in the foundation review of 2026-10-12).

Raised, a reminder tells its responsible person, or, naming none, everyone of its responsible
jobs, unless the module that owns the record already tells them as the item falls due (its
alert, declared on the category as `told_by`): one notice per item, from one source. Past its due
date it is Overdue; overdue for the days QA sets (Quality Settings), it is escalated once to QA
and Management, and its responsible person told again, for every category. What each reminder's
people were last told is kept on it (`notified`), so the daily run never tells them twice, and a
notice that failed is sent on the next run.

QA also plans recurring activities (Reminder Schedule: a management review, an internal audit):
each occurrence is raised within its lead time, its job is told, it escalates like any other,
and a person of that job, or QA, completes it with a remark; the next occurrence follows the
calendar from the due date, not from the day it was done.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, add_months, cint, date_diff, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings
from meddevice_suite.compliance.checks import QA_ROLE

DOCTYPE = "Reminder"
SCHEDULE = "Reminder Schedule"
OPEN, OVERDUE, ESCALATED, COMPLETED = "Open", "Overdue", "Escalated", "Completed"
STILL_OPEN = (OPEN, OVERDUE, ESCALATED)
RAISED = "Raised"  # what a reminder's people were told: that it was raised, then that it escalated
MANAGEMENT = ("Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager")
SEES_ALL = frozenset({QA_ROLE, *MANAGEMENT, "System Manager"})
OVERDUE_ALERT = "Reminder Overdue"
SCHEDULED = "Recurring"
SAVEPOINT = "reminder"
FREQUENCY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Half-yearly": 6, "Yearly": 12}


@dataclass(frozen=True)
class Due:
	"""One item due: the record it is about, when, and who answers for it."""

	reference_doctype: str
	reference_name: str
	due_date: object
	subject: str
	roles: tuple[str, ...] = ()
	user: str | None = None
	part: str = ""  # a row of the record, when the record holds several due items

	def key(self, category: str) -> str:
		return (
			f"{category}|{self.reference_doctype}|{self.reference_name}|{self.part}|{getdate(self.due_date)}"
		)


# The furthest day a category is read to when the lead time is set aside (`whole_horizon`).
WHOLE_HORIZON = "9999-12-31"


def within(setting: str | None = None) -> object:
	"""The last due day a category shows: today plus the lead time QA sets in `setting` (today, with
	none). Every category reads its lead time through here, so `whole_horizon` can set it aside."""
	if frappe.flags.reminders_whole_horizon:
		return getdate(WHOLE_HORIZON)
	return add_days(today(), quality_settings.value(setting)) if setting else today()


@contextmanager
def whole_horizon() -> Iterator[None]:
	"""Read the categories with no lead time: every item still to be done, however far off its due
	date. What a category shows due is always part of what it shows here."""
	before, frappe.flags.reminders_whole_horizon = frappe.flags.reminders_whole_horizon, True
	try:
		yield
	finally:
		frappe.flags.reminders_whole_horizon = before


def date_text(value) -> str:
	return frappe.format_value(value, {"fieldtype": "Date"})


# --- The categories: what is due, as each record says ---------------------------------------


def lots_expiring() -> Iterator[Due]:
	"""Lots with stock that expire within the lead time or have expired, until used up or kept
	apart (on hold or rejected, so out of usable stock)."""
	from meddevice_suite.lot_control.lot_status import HOLD, REJECTED, STATUS_FIELD

	for lot in frappe.get_all(
		"Batch",
		filters=[
			["expiry_date", "is", "set"],
			["expiry_date", "<=", within("lot_expiry_warning_days")],
			["batch_qty", ">", 0],
			["disabled", "=", 0],
			[STATUS_FIELD, "not in", [HOLD, REJECTED]],
		],
		fields=["name", "item", "expiry_date"],
	):
		yield Due(
			"Batch",
			lot.name,
			lot.expiry_date,
			_("Lot {0} of {1} expires on {2}").format(lot.name, lot.item, date_text(lot.expiry_date)),
			("Stock User", QA_ROLE),
		)


def supplier_certificates_expiring() -> Iterator[Due]:
	"""The certificate of each supplier decision in force that expires within the lead time,
	until a new decision replaces it."""
	from meddevice_suite.master_data.supplier_approval import APPROVED, CONDITIONAL, current_approval

	for row in frappe.get_all(
		"Supplier Approval",
		filters={
			"docstatus": 1,
			"decision": ["in", [APPROVED, CONDITIONAL]],
			"certificate_expiry": ["<=", within("supplier_certificate_warning_days")],
		},
		fields=["name", "supplier", "certificate_expiry"],
	):
		in_force = current_approval(row.supplier)
		if in_force and in_force.name == row.name:
			yield Due(
				"Supplier Approval",
				row.name,
				row.certificate_expiry,
				_("Certificate of supplier {0} expires on {1}").format(
					row.supplier, date_text(row.certificate_expiry)
				),
				(QA_ROLE, "Purchase User", "Purchase Manager"),
			)


def customer_licences_expiring() -> Iterator[Due]:
	"""Each verified customer licence that ends within the lead time, until a renewal is verified."""
	from meddevice_suite.master_data.customer_licence import VERIFIED, renewed

	for row in frappe.get_all(
		"Customer Licence",
		filters={
			"docstatus": 1,
			"status": VERIFIED,
			"valid_until": ["<=", within("customer_licence_warning_days")],
		},
		fields=["name", "customer", "valid_until"],
	):
		if not renewed(row.customer, row.valid_until):
			yield Due(
				"Customer Licence",
				row.name,
				row.valid_until,
				_("Licence of customer {0} ends on {1}").format(row.customer, date_text(row.valid_until)),
				(QA_ROLE, "Sales User", "Sales Manager"),
			)


def document_reviews_due() -> Iterator[Due]:
	"""Each effective document whose periodic review falls within the lead time, until it is
	reviewed (its next review date moves on)."""
	for doc in frappe.get_all(
		"Controlled Document",
		filters=[
			["status", "=", "Effective"],
			["next_review_date", "is", "set"],
			["next_review_date", "<=", within("document_review_warning_days")],
		],
		fields=["name", "title", "process_owner", "next_review_date"],
	):
		yield Due(
			"Controlled Document",
			doc.name,
			doc.next_review_date,
			_("Review of {0} {1} is due on {2}").format(doc.name, doc.title, date_text(doc.next_review_date)),
			(QA_ROLE,),
			doc.process_owner,
		)


def change_actions_due() -> Iterator[Due]:
	"""Each action of an approved change that falls due within the lead time, until it is done."""
	for row in frappe.get_all(
		"Change Request Action",
		filters={
			"parenttype": "Change Request",
			"done": 0,
			"due_date": ["<=", within("change_action_warning_days")],
		},
		fields=["name", "parent", "idx", "action_owner", "due_date"],
	):
		if frappe.db.get_value("Change Request", row.parent, "status") == "Approved":
			yield Due(
				"Change Request",
				row.parent,
				row.due_date,
				_("Action {0} of change request {1} is due on {2}").format(
					row.idx, row.parent, date_text(row.due_date)
				),
				(QA_ROLE,),
				row.action_owner,
				part=row.name,
			)


def effectiveness_checks_due() -> Iterator[Due]:
	"""Each implemented change whose effectiveness check has fallen due, until it is closed."""
	for row in frappe.get_all(
		"Change Request",
		filters={"status": "Implemented", "effectiveness_due_on": ["<=", within()]},
		fields=["name", "effectiveness_due_on"],
	):
		yield Due(
			"Change Request",
			row.name,
			row.effectiveness_due_on,
			_("Effectiveness check of change request {0} was due on {1}").format(
				row.name, date_text(row.effectiveness_due_on)
			),
			(QA_ROLE,),
			part="effectiveness",
		)


def work_tasks_due() -> Iterator[Due]:
	"""Each task not yet approved or withdrawn whose due date falls within the lead time: with the
	person assigned, or with the approver once it is submitted."""
	for task in frappe.get_all(
		"Work Task",
		filters={
			"status": ["in", ["Open", "Submitted", "Returned"]],
			"due_date": ["<=", within("change_action_warning_days")],
		},
		fields=["name", "subject", "status", "assigned_to", "approver", "due_date", "submitted_on"],
	):
		yield Due(
			"Work Task",
			task.name,
			task.due_date,
			_("Task {0} {1} is due on {2}").format(task.name, task.subject, date_text(task.due_date)),
			(),
			task.approver if task.status == "Submitted" else task.assigned_to,
			# Each round of the task is its own reminder: assigned, submitted, returned, submitted again.
			part=f"{task.status}|{task.submitted_on or ''}",
		)


def files_ending() -> Iterator[Due]:
	"""Each active repository file whose current version's validity ends within the lead time,
	until a new version renews it or it is archived; its category's uploaders answer for it."""
	from meddevice_suite.files_repository.files import EDITORS, access

	files = frappe.get_all(
		"Important File",
		filters={"status": "Active", "valid_until": ["<=", within("file_validity_warning_days")]},
		fields=["name", "title", "category", "valid_until"],
	)
	known = access() if files else {}
	for row in files:
		yield Due(
			"Important File",
			row.name,
			row.valid_until,
			_("{0} is valid until {1}").format(row.title, date_text(row.valid_until)),
			tuple(sorted(known.get(row.category, {}).get(EDITORS) or {QA_ROLE})),
		)


CARE_ROLES = ("Maintenance User", QA_ROLE)


def equipment_needing(field: str) -> list[frappe._dict]:
	"""Approved equipment, not retired, that needs the care the field names."""
	from meddevice_suite.master_data import equipment

	return frappe.get_all(
		"Asset",
		filters={
			field: 1,
			equipment.APPROVAL_STATUS: equipment.APPROVED,
			equipment.STATUS: ["!=", equipment.RETIRED],
			"docstatus": ["<", 2],
		},
		fields=["name", "asset_name", equipment.APPROVED_AT],
	)


def care_due(field: str, doctype: str, setting: str, what: str) -> Iterator[Due]:
	"""Each piece of equipment whose calibration or maintenance falls due within the lead time:
	the next due date of its latest completed record, or, never done or last not passed, the day QA
	approved it. Done when a new record moves the date on."""
	from meddevice_suite.master_data import equipment
	from meddevice_suite.qms.equipment_care import latest

	assets = equipment_needing(field)
	last = latest(doctype, [a.name for a in assets], ["next_due_date"]) if assets else {}
	soon = getdate(within(setting))
	for asset in assets:
		record = last.get(asset.name)
		due = (record and record.next_due_date) or getdate(asset.get(equipment.APPROVED_AT))
		if getdate(due) <= soon:
			yield Due(
				"Asset",
				asset.name,
				due,
				_("{0} of {1} is due on {2}").format(what, asset.asset_name or asset.name, date_text(due)),
				CARE_ROLES,
				part=doctype,
			)


def calibrations_due() -> Iterator[Due]:
	from meddevice_suite.master_data import equipment

	yield from care_due(
		equipment.CALIBRATION, "Calibration Record", "calibration_warning_days", _("Calibration")
	)


def maintenance_due() -> Iterator[Due]:
	from meddevice_suite.master_data import equipment

	yield from care_due(
		equipment.MAINTENANCE, "Maintenance Record", "maintenance_warning_days", _("Preventive maintenance")
	)


def requalifications_due() -> Iterator[Due]:
	"""Each piece of equipment with a requalification interval whose last approved PQ reaches it
	within the lead time; never qualified, due from the day QA approved it."""
	from meddevice_suite.master_data import equipment
	from meddevice_suite.qms.equipment_care import latest

	assets = [a for a in equipment_needing(equipment.QUALIFICATION)]
	if not assets:
		return
	intervals = dict(
		frappe.get_all(
			"Asset",
			filters={"name": ["in", [a.name for a in assets]]},
			fields=["name", equipment.REQUALIFICATION_DAYS],
			as_list=True,
		)
	)
	last_pq = latest(
		"Equipment Qualification", [a.name for a in assets], [], {"stage": "PQ", "outcome": "Pass"}
	)
	soon = getdate(within("requalification_warning_days"))
	for asset in assets:
		pq = last_pq.get(asset.name)
		if pq and not cint(intervals.get(asset.name)):
			continue  # qualified, and the qualification does not expire
		due = (
			add_days(pq.performed_on, cint(intervals[asset.name]))
			if pq
			else getdate(asset.get(equipment.APPROVED_AT))
		)
		if getdate(due) <= soon:
			yield Due(
				"Asset",
				asset.name,
				due,
				_("Qualification of {0} is due on {1}").format(
					asset.asset_name or asset.name, date_text(due)
				),
				CARE_ROLES,
				part="Equipment Qualification",
			)


def calibration_impacts_open() -> Iterator[Due]:
	"""Each calibration found out of tolerance until QA signs its impact assessment, also once QA
	has withdrawn it for correction: the assessment stays open on it (plan D-29), and a correction
	does not complete this reminder. A draft never holds an open assessment (`calibration`)."""
	for row in frappe.get_all(
		"Calibration Record",
		filters={"docstatus": ["in", (1, 2)], "impact_status": "Open"},
		fields=["name", "asset", "performed_on"],
	):
		yield Due(
			"Calibration Record",
			row.name,
			row.performed_on,
			_("Assess what {0} measured before it was found out of tolerance ({1})").format(
				row.asset, row.name
			),
			(QA_ROLE,),
		)


def training_due() -> Iterator[Due]:
	"""Each training opened and not yet completed: the trainee's until they sign, then the
	assessor's; the Training Coordinator and QA see them all (module 37)."""
	for row in frappe.get_all(
		"Training Record",
		filters={"docstatus": 0, "status": ["in", ("Open", "Trained")], "trigger": ["!=", "Historical"]},
		fields=[
			"name",
			"employee_name",
			"controlled_document",
			"revision",
			"due_date",
			"status",
			"trainee_user",
			"assessor",
		],
	):
		yield Due(
			"Training Record",
			row.name,
			row.due_date,
			_("{0}: training on {1} revision {2} ({3})").format(
				row.employee_name, row.controlled_document, row.revision, _(row.status).lower()
			),
			("HR User", QA_ROLE),
			user=row.trainee_user if row.status == "Open" else row.assessor,
			part=row.status,
		)


def risk_reviews_open() -> Iterator[Due]:
	"""Each open risk review, from the day it is raised until QA closes it, by a new revision of the
	risk assessment or with a signed rationale (module 39)."""
	for row in frappe.get_all(
		"Risk Review",
		filters={"docstatus": 0, "status": "Open"},
		fields=["name", "risk_management_file", "family_name", "trigger", "due_date"],
	):
		yield Due(
			"Risk Review",
			row.name,
			row.due_date,
			_("Risk review {0} of {1}: {2}").format(
				row.name, row.family_name or row.risk_management_file, _(row.trigger)
			),
			(QA_ROLE,),
		)


def regulatory_licences_due() -> Iterator[Due]:
	"""Each verified licence ending within the lead time or ended, until its renewal is verified;
	each obligation due within it or overdue, until QA records it done (module 41)."""
	from meddevice_suite.qms import regulatory_licence

	until = within("regulatory_licence_warning_days")
	for row in regulatory_licence.expiring(until):
		yield Due(
			"Regulatory Licence",
			row.name,
			row.valid_until,
			_("Licence {0} ({1}) ends on {2}").format(
				row.licence_number, row.licence_type, date_text(row.valid_until)
			),
			(QA_ROLE,),
		)
	for row in regulatory_licence.obligations_due(until):
		yield Due(
			"Regulatory Licence",
			row.name,
			row.due_date,
			_("{0} of licence {1} is due on {2}").format(
				row.obligation, row.licence_number, date_text(row.due_date)
			),
			(QA_ROLE,),
			part=row.row,
		)


def dmr_reapprovals_due() -> Iterator[Due]:
	"""Each device master record out of date, from the day it went out of date until QA approves the
	revision that supersedes it, or withdraws it; due within the days QA sets (module 31)."""
	from meddevice_suite.qms import device_master_record

	for row in device_master_record.reapprovals_due(quality_settings.value("dmr_reapproval_days")):
		yield Due(
			device_master_record.DOCTYPE,
			row.name,
			row.due_date,
			_("Device master record {0} of {1} is out of date: approve a new revision").format(
				row.name, row.item_name or row.item
			),
			(QA_ROLE,),
		)


@dataclass(frozen=True)
class Category:
	"""A kind of reminder: what is due now, and who tells its people as an item falls due.

	`told_by` names the function of the module that owns the record which already tells the
	reminder's responsible people, once, as the item falls due (its alert); the reminder then sends
	no notice of its own when raised, so nobody is told the same thing twice. Empty, the reminder
	tells them. No module alert escalates: escalation is the reminder's, for every category."""

	due: Callable[[], Iterator[Due]]
	told_by: str = ""


# category -> what is due now and who tells it (module that owns the record)
CATEGORIES: dict[str, Category] = {
	"Lot Expiry": Category(  # module 18: Lot Expiring Soon, then Lot Expired, to Stores and QA
		lots_expiring, "meddevice_suite.compliance.alerts.send_expiry_alerts"
	),
	"Supplier Certificate": Category(  # module 4: Supplier Certificate Expiring, to QA and Purchase
		supplier_certificates_expiring,
		"meddevice_suite.master_data.supplier_approval.send_certificate_alerts",
	),
	"Customer Licence": Category(  # module 5: Customer Licence Expiring, to QA and Sales
		customer_licences_expiring, "meddevice_suite.master_data.customer_licence.send_expiry_alerts"
	),
	"Document Review": Category(  # module 30: Document Review Due, to the process owner and QA
		document_reviews_due, "meddevice_suite.qms.document_control.send_review_alerts"
	),
	"Change Action": Category(  # module 36: the action's owner, directly
		change_actions_due, "meddevice_suite.qms.change_control.alert_action_owner"
	),
	"Effectiveness Check": Category(  # module 36: Change Effectiveness Check Due, to QA
		effectiveness_checks_due, "meddevice_suite.qms.change_control.alert_effectiveness"
	),
	"Work Task": Category(work_tasks_due),  # module 59
	"File Validity": Category(files_ending),  # module 61
	"Calibration Due": Category(calibrations_due),  # module 38
	"Maintenance Due": Category(maintenance_due),  # module 38
	"Requalification Due": Category(requalifications_due),  # module 40
	"Calibration Impact": Category(  # module 38: Calibration Out of Tolerance, to QA, on completion
		calibration_impacts_open, "meddevice_suite.qms.calibration.complete"
	),
	"Training Due": Category(training_due),  # module 37
	"Risk Review": Category(risk_reviews_open),  # module 39
	"Regulatory Licence": Category(  # module 41: Regulatory Licence Expiring, to QA, licences and duties
		regulatory_licences_due, "meddevice_suite.qms.regulatory_licence.send_expiry_alerts"
	),
	"DMR Re-approval": Category(  # module 31: Device Master Record Out of Date, to QA
		dmr_reapprovals_due, "meddevice_suite.qms.device_master_record.alert"
	),
}


# --- The daily run ------------------------------------------------------------------------------


def run() -> None:
	"""Daily: raise and settle the system reminders, raise the recurring ones, mark what is
	overdue, escalate what has stayed overdue, and tell each reminder's people what they have not
	been told yet. One category's failure is logged and leaves its reminders as they were; the
	others go on."""
	for category, kind in CATEGORIES.items():
		in_step(
			f"reminders of {category}",
			lambda category=category, due_now=kind.due: track(category, due_now),
		)
	for name in frappe.get_all(SCHEDULE, filters={"active": 1}, pluck="name"):
		in_step(f"recurring reminder {name}", lambda name=name: raise_occurrence(name))
	for name in frappe.get_all(
		DOCTYPE, filters={"status": ["in", STILL_OPEN], "due_date": ["<", today()]}, pluck="name"
	):
		in_step(f"reminder {name}", lambda name=name: follow_up(name))
	send_notices()


def in_step(what: str, step: Callable[[], None]) -> None:
	frappe.db.savepoint(SAVEPOINT)
	try:
		step()
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"The {what} could not be brought up to date")
	else:
		frappe.db.release_savepoint(SAVEPOINT)


def track(category: str, due_now: Callable[[], Iterator[Due]]) -> None:
	"""Raise a reminder for each item due within its lead time that has no open one; settle the
	category's open reminders whose record no longer shows their item to be done at all."""
	due = list(due_now())
	keys = {item.key(category) for item in due}
	still_open = frappe.get_all(
		DOCTYPE,
		filters={"category": category, "status": ["in", STILL_OPEN]},
		fields=["name", "reminder_key", "reference_doctype", "reference_name"],
	)
	open_keys = {row.reminder_key for row in still_open}
	for item in due:
		# Only an open reminder stands for the item: one settled before, its item due again, does not.
		if item.key(category) not in open_keys:
			make(category, item)
			open_keys.add(item.key(category))
	dropped = [row for row in still_open if row.reminder_key not in keys]
	if not dropped:
		return
	with whole_horizon():
		pending = {item.key(category): item for item in due_now()}
	# An item is its key without the due date: still to be done on another day, its date moved.
	moved_to = {key.rpartition("|")[0]: item.due_date for key, item in pending.items()}
	for row in dropped:
		if row.reminder_key in pending:
			continue  # still to be done, only outside the lead time now: it stays open
		record = (_(row.reference_doctype), row.reference_name)
		new_date = moved_to.get(row.reminder_key.rpartition("|")[0])
		complete(
			row.name,
			_("The due date in {0} {1} moved to {2}.").format(*record, date_text(new_date))
			if new_date
			else _("{0} {1} no longer shows this due.").format(*record),
		)


def make(category: str, item: Due, schedule: str | None = None) -> frappe.model.document.Document:
	key = item.key(category)
	if settled := frappe.db.get_value(DOCTYPE, {"reminder_key": key, "status": COMPLETED}):
		# The same item and due date, settled before and due again: the settled reminder keeps its
		# record and gives up the key, which names the one reminder open for the item.
		update(settled, reminder_key=None)
	reminder = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"category": category,
			"subject": item.subject,
			"reference_doctype": item.reference_doctype,
			"reference_name": item.reference_name,
			"schedule": schedule,
			"due_date": item.due_date,
			"responsible_roles": "\n".join(item.roles),
			"responsible_user": item.user,
			"raised_on": today(),
			"status": OVERDUE if getdate(item.due_date) < getdate(today()) else OPEN,
			"reminder_key": key,
		}
	)
	reminder.flags.reminder = True
	# Nobody holds create on a reminder: this service is the only way one is raised.
	reminder.flags.ignore_permissions = True
	return reminder.insert()


def update(name: str, **values) -> None:
	reminder = frappe.get_doc(DOCTYPE, name)
	reminder.update(values)
	reminder.flags.reminder = True
	reminder.flags.ignore_permissions = True  # kept by this service only
	reminder.save()


def complete(name: str, how: str, by: str | None = None) -> None:
	update(name, status=COMPLETED, completed_on=now_datetime(), completed_by=by, completion=how)


def settle(
	category: str,
	reference_doctype: str,
	reference_name: str,
	how: str,
	by: str | None = None,
	still_due_on: object = None,
) -> None:
	"""The work in a record was done just now by `by` (a document reviewed and found current): its
	open reminders of the category are completed at once, not at the next daily run, with who did it.
	One for `still_due_on`, the record's due date now, stays open: the daily run would not raise its
	key again."""
	filters = {
		"category": category,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"status": ["in", STILL_OPEN],
	}
	if still_due_on:
		filters["due_date"] = ["!=", getdate(still_due_on)]
	for name in frappe.get_all(DOCTYPE, filters=filters, pluck="name"):
		complete(name, how, by=by)


def follow_up(name: str) -> None:
	"""A reminder past its due date is Overdue; overdue for the escalation days, escalated once
	(`send_notices` tells the escalation)."""
	reminder = frappe.get_doc(DOCTYPE, name)
	days_over = date_diff(today(), reminder.due_date)
	if reminder.status == OPEN:
		update(name, status=OVERDUE)
	if reminder.status != ESCALATED and days_over >= quality_settings.value("reminder_escalation_days"):
		update(name, status=ESCALATED, escalated_on=now_datetime())


# --- Telling people -------------------------------------------------------------------------------


def send_notices() -> None:
	"""Tell the people of each open reminder what they have not been told yet: that it was raised,
	that it was escalated. Each reminder on its own, so one that fails is logged and told on the
	next run, and the others still go."""
	untold = frappe.get_all(
		DOCTYPE,
		filters={"status": ["in", STILL_OPEN], "notified": ["is", "not set"]},
		pluck="name",
		order_by="creation asc",
	)
	escalation_untold = frappe.get_all(
		DOCTYPE, filters={"status": ESCALATED, "notified": RAISED}, pluck="name", order_by="creation asc"
	)
	for name in untold + escalation_untold:
		in_step(f"notice of reminder {name}", lambda name=name: notify(name))


def notify(name: str) -> None:
	"""Raised: its responsible person is told, or, naming none, everyone of its responsible jobs,
	unless its category's module has told them already (`Category.told_by`). Escalated: QA,
	Management and its responsible person, by the "Reminder Overdue" alert. What was told is
	recorded on the reminder in the same step, so it is never told twice."""
	reminder = frappe.get_doc(DOCTYPE, name)
	kind = CATEGORIES.get(reminder.category)
	if not reminder.notified and not (kind and kind.told_by):
		tell_raised(reminder)
	if reminder.status == ESCALATED and reminder.notified != ESCALATED:
		frappe.get_doc("Notification", OVERDUE_ALERT).send(reminder)
	update(name, notified=ESCALATED if reminder.status == ESCALATED else RAISED)


def tell_raised(reminder: frappe.model.document.Document) -> None:
	if reminder.responsible_user:
		users = [reminder.responsible_user]
	else:
		roles = [role for role in (reminder.responsible_roles or "").split("\n") if role]
		users = sorted({user for role in roles for user in users_with_role(role)})
	if not users:
		return
	if reminder.schedule:
		details = frappe.db.get_value(SCHEDULE, reminder.schedule, "description") or ""
	else:
		details = _("Due on {0}. It is completed by the work in {1} {2}.").format(
			date_text(reminder.due_date), _(reminder.reference_doctype), reminder.reference_name
		)
	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": DOCTYPE,
			"document_name": reminder.name,
			"subject": reminder.subject,
			"email_content": frappe.utils.escape_html(details),
		},
	)


# --- Recurring reminders QA plans -----------------------------------------------------------------


def next_after(due, frequency: str):
	return add_days(due, 7) if frequency == "Weekly" else add_months(due, FREQUENCY_MONTHS[frequency])


def raise_occurrence(name: str) -> None:
	"""The schedule's next occurrence, once within its lead time; its job is told by
	`send_notices`."""
	schedule = frappe.get_doc(SCHEDULE, name)
	due = getdate(schedule.next_due_date or schedule.first_due_date)
	if getdate(add_days(due, -cint(schedule.lead_days))) > getdate(today()):
		return
	item = Due(
		SCHEDULE,
		schedule.name,
		due,
		_("{0} is due on {1}").format(schedule.title, date_text(due)),
		(schedule.responsible_role,),
	)
	if frappe.db.exists(DOCTYPE, {"reminder_key": item.key(SCHEDULED)}):
		return  # raised already; a completed occurrence keeps its key, so it is never raised again
	make(SCHEDULED, item, schedule=schedule.name)


def users_with_role(role: str) -> list[str]:
	from frappe.utils.user import get_users_with_role

	return [user for user in get_users_with_role(role) if user not in ("Administrator", "Guest")]


@frappe.whitelist()
def complete_occurrence(name: str, remark: str) -> None:
	"""A person of the responsible job, or QA, records that the recurring activity was done."""
	reminder = frappe.get_doc(DOCTYPE, name)
	reminder.check_permission("read")
	if not reminder.schedule:
		frappe.throw(_("{0} is completed by the work in its record, not by hand.").format(name))
	if reminder.status == COMPLETED:
		frappe.throw(_("{0} is completed already.").format(name))
	roles = set(frappe.get_roles())
	if not ({QA_ROLE, *reminder.responsible_roles.split("\n")} & roles):
		frappe.throw(
			_("Only its responsible job, or QA, completes {0}.").format(name), frappe.PermissionError
		)
	remark = (remark or "").strip()
	if not remark:
		frappe.throw(_("Say what was done."))
	complete(name, remark, by=frappe.session.user)
	schedule = frappe.get_doc(SCHEDULE, reminder.schedule)
	if getdate(schedule.next_due_date) == getdate(reminder.due_date):
		schedule.next_due_date = next_after(reminder.due_date, schedule.frequency)
		schedule.flags.reminder = True
		schedule.flags.ignore_permissions = True  # the schedule moves on as its occurrence is done
		schedule.save()


# --- Who sees which reminders --------------------------------------------------------------------


def query_conditions(user: str | None = None) -> str:
	"""Reminder list: QA, management and the administrator see all; everyone else those of their
	jobs and their own."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if roles & SEES_ALL:
		return ""
	# The responsible roles are one per line; a role matches a whole line, never part of a name.
	lines = "concat(char(10), `tabReminder`.`responsible_roles`, char(10))"
	conditions = [f"`tabReminder`.`responsible_user` = {frappe.db.escape(user)}"]
	for role in sorted(roles):
		pattern = "%" + "\n" + role + "\n" + "%"
		conditions.append(f"{lines} like {frappe.db.escape(pattern)}")
	return "(" + " or ".join(conditions) + ")"


def has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if roles & SEES_ALL:
		return True
	return doc.responsible_user == user or bool(roles & set((doc.responsible_roles or "").split("\n")))
