"""Record what the people of each open reminder were already told before reminders kept it
(URS-PLT-020), so the first daily run after the upgrade does not tell them again.

Before, an escalated reminder had told QA, Management and its responsible person, and a recurring
occurrence had told its job as it was raised; a system reminder had told nobody when it was raised,
so it is left unrecorded: on the next run its people are told once, by the reminder, unless its
category's module alerts them itself. Reminders already recorded
are left alone, so the patch can run again without effect. Each change goes through the reminders
service, so the audit trail records it.
"""

import frappe

from meddevice_suite.work_management import reminders


def execute() -> None:
	for name, status, schedule in frappe.get_all(
		reminders.DOCTYPE,
		filters={"status": ["in", reminders.STILL_OPEN], "notified": ["is", "not set"]},
		fields=["name", "status", "schedule"],
		as_list=True,
	):
		if status == reminders.ESCALATED:
			reminders.update(name, notified=reminders.ESCALATED)
		elif schedule:
			reminders.update(name, notified=reminders.RAISED)
