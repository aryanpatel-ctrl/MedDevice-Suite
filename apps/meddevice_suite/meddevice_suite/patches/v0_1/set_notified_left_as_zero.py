"""Put right what each reminder's people were told where Frappe's Reminder left "0" (URS-PLT-020).

While the reminders shared their name and table with Frappe's Reminder (see
rename_reminder_to_due_reminder), every migrate turned `notified` into "0", Frappe's default for its
own field of that name. "0" is none of the values (blank, Raised, Escalated), so the daily run
never told those reminders' people anything and every later change to such a reminder was refused.

What was told cannot be read back, so it is set as when reminders first kept it
(record_reminders_already_told): an escalated reminder had told its escalation, and a recurring
occurrence its job as it was raised; any other open reminder is left unrecorded, so its people are
told once on the next run unless its module alerts them itself, and a completed one needs nothing
told. Each change goes through the reminders service, with this reason on its audit event. Values
other than "0" are left alone, so the patch can run again without effect.
"""

import frappe

from meddevice_suite.work_management import reminders

LEFT_BY_FRAPPE = "0"
REASON = (
	"People Told Of was \"0\", the default of Frappe's Reminder, which shared this record's table "
	"(patch set_notified_left_as_zero)."
)


def told(status: str, schedule: str | None) -> str | None:
	if status == reminders.ESCALATED:
		return reminders.ESCALATED
	if schedule and status in reminders.STILL_OPEN:
		return reminders.RAISED
	return None


def execute() -> None:
	for row in frappe.get_all(
		reminders.DOCTYPE,
		filters={"notified": LEFT_BY_FRAPPE},
		fields=["name", "status", "schedule"],
		order_by="creation asc",
	):
		reminders.update(row.name, reason=REASON, notified=told(row.status, row.schedule))
