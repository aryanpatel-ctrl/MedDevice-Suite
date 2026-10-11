"""Reminders and next due (module 60; URS-PLT-014, URS-PLT-015; DFD 9C: C1 define reminder category,
C2 set due date or frequency, C3 generate reminder, C4 notify users, C5 track and escalate, C6 mark
completed; data store D11 Task, Approval & Reminders).

Every due item the system alerts on is tracked as a Reminder from the day it falls within its lead
time until its own record shows the work done: a lot used up or kept apart, a certificate or a
licence renewed, a document reviewed, a change action done, an effectiveness check closed. The
categories, what makes an item due and what shows it done are declared here, in code; the lead
times are QA's, in Quality Settings. A system reminder is never ticked off by hand: only the work
in its record completes it, so the list cannot be cleared without the work being done.

Past its due date a reminder is Overdue; overdue for the days QA sets (Quality Settings), it is
escalated once to QA and Management, and its responsible person told again. The existing alerts
still tell the responsible jobs as each item falls due (compliance/alerts.py and the modules that
own the records); this adds the tracking, the overdue state, the escalation and the record of
completion.

QA also plans recurring activities (Reminder Schedule: a management review, an internal audit):
each occurrence is raised within its lead time, its job is told, it escalates like any other,
and a person of that job, or QA, completes it with a remark; the next occurrence follows the
calendar from the due date, not from the day it was done.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, add_months, cint, date_diff, getdate, now_datetime, today

from meddevice_suite.compliance import quality_settings

DOCTYPE = "Reminder"
SCHEDULE = "Reminder Schedule"
OPEN, OVERDUE, ESCALATED, COMPLETED = "Open", "Overdue", "Escalated", "Completed"
STILL_OPEN = (OPEN, OVERDUE, ESCALATED)
QA_ROLE = "Quality Manager"
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


def within(setting: str) -> object:
	return add_days(today(), quality_settings.value(setting))


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
		filters={"status": "Implemented", "effectiveness_due_on": ["<=", today()]},
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


# category -> what is due now (module that owns the record)
CATEGORIES: dict[str, Callable[[], Iterator[Due]]] = {
	"Lot Expiry": lots_expiring,  # module 18
	"Supplier Certificate": supplier_certificates_expiring,  # module 4
	"Customer Licence": customer_licences_expiring,  # module 5
	"Document Review": document_reviews_due,  # module 30
	"Change Action": change_actions_due,  # module 36
	"Effectiveness Check": effectiveness_checks_due,  # module 36
}


# --- The daily run ------------------------------------------------------------------------------


def run() -> None:
	"""Daily: raise and complete the system reminders, raise the recurring ones, mark what is
	overdue and escalate what has stayed overdue. One category's failure is logged and leaves
	its reminders as they were; the others go on."""
	for category, due_now in CATEGORIES.items():
		in_step(
			f"reminders of {category}",
			lambda category=category, due_now=due_now: track(category, list(due_now())),
		)
	for name in frappe.get_all(SCHEDULE, filters={"active": 1}, pluck="name"):
		in_step(f"recurring reminder {name}", lambda name=name: raise_occurrence(name))
	for name in frappe.get_all(
		DOCTYPE, filters={"status": ["in", STILL_OPEN], "due_date": ["<", today()]}, pluck="name"
	):
		in_step(f"reminder {name}", lambda name=name: follow_up(name))


def in_step(what: str, step: Callable[[], None]) -> None:
	frappe.db.savepoint(SAVEPOINT)
	try:
		step()
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"The {what} could not be brought up to date")
	else:
		frappe.db.release_savepoint(SAVEPOINT)


def track(category: str, due: list[Due]) -> None:
	"""Raise a reminder for each item newly due; complete the category's open reminders whose item
	is no longer due, the work being done in its record."""
	keys = {item.key(category) for item in due}
	for item in due:
		if not frappe.db.exists(DOCTYPE, {"reminder_key": item.key(category)}):
			make(category, item)
	for name, reference_doctype, reference_name in frappe.get_all(
		DOCTYPE,
		filters={
			"category": category,
			"status": ["in", STILL_OPEN],
			"reminder_key": ["not in", keys or [""]],
		},
		fields=["name", "reference_doctype", "reference_name"],
		as_list=True,
	):
		# The work was done, or the record's due date moved (a new reminder then follows it).
		complete(name, _("{0} {1} no longer shows this due.").format(_(reference_doctype), reference_name))


def make(category: str, item: Due, schedule: str | None = None) -> frappe.model.document.Document:
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
			"reminder_key": item.key(category),
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


def follow_up(name: str) -> None:
	"""A reminder past its due date is Overdue; overdue for the escalation days, escalated once."""
	reminder = frappe.get_doc(DOCTYPE, name)
	days_over = date_diff(today(), reminder.due_date)
	if reminder.status == OPEN:
		update(name, status=OVERDUE)
	if reminder.status != ESCALATED and days_over >= quality_settings.value("reminder_escalation_days"):
		update(name, status=ESCALATED, escalated_on=now_datetime())
		frappe.get_doc("Notification", OVERDUE_ALERT).send(frappe.get_doc(DOCTYPE, name))


# --- Recurring reminders QA plans -----------------------------------------------------------------


def next_after(due, frequency: str):
	return add_days(due, 7) if frequency == "Weekly" else add_months(due, FREQUENCY_MONTHS[frequency])


def raise_occurrence(name: str) -> None:
	"""The schedule's next occurrence, once within its lead time; told to its job."""
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
		return
	reminder = make(SCHEDULED, item, schedule=schedule.name)
	for user in users_with_role(schedule.responsible_role):
		enqueue_create_notification(
			user,
			{
				"type": "Alert",
				"document_type": DOCTYPE,
				"document_name": reminder.name,
				"subject": reminder.subject,
				"email_content": frappe.utils.escape_html(schedule.description or ""),
			},
		)


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
