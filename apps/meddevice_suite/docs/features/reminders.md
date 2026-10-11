# Reminders and next due (module 60) — feature document and browser test guide

Requirements: **URS-PLT-014** (system reminders tracked, overdue, escalated, completed by the work) and **URS-PLT-015** (recurring reminders QA plans) (`meddevice_suite/qa/requirements.toml`). Builds on the alerts of modules 4, 5, 18, 30, 36 and 51, and on Quality Settings (URS-PLT-012).
Code: `meddevice_suite/work_management/reminders.py`; records *Reminder* and *Reminder Schedule* (`work_management/doctype/`); alert `work_management/notification/reminder_overdue/`; hooks (daily job, Reminder list permissions) in `meddevice_suite/hooks.py`; Quality Settings `reminder_escalation_days`.
DFD trace: 9C Reminders / Next Due (C1 define reminder category, C2 set due date or frequency, C3 generate reminder, C4 notify users, C5 track and escalate, C6 mark completed); data store D11 Task, Approval & Reminders; master plan sections 8 and 10.

## 1. Feature overview

**Purpose.** Nothing that falls due is forgotten: each due item is tracked until the work is done, and what stays undone is escalated.

**Why it matters.** A one-off alert that nobody acts on leaves no trace: an expired lot left in stock, a supplier certificate lapsed, a review not done. ISO 13485 asks that these activities are planned and their records kept; an auditor asks what was overdue and who knew.

| Job profile | Here |
| --- | --- |
| Every job | Sees the reminders of its jobs and its own (Reminder list), is told when its items fall due |
| QA, Management | See all reminders; are told when one is escalated |
| QA | Plans recurring activities (Reminder Schedule); changes the escalation days under change control |
| The responsible job of a recurring activity, or QA | Completes each occurrence with a remark |

## 2. Detailed functional explanation

- **System reminders**, one per due item, raised by a daily job as the item falls within its lead time (the lead times in Quality Settings):

| Category | Due | Responsible | Done when |
| --- | --- | --- | --- |
| Lot Expiry | a lot with stock nears or passes its expiry | Stores, QA | used up, or kept apart (on hold, rejected) |
| Supplier Certificate | the certificate of the decision in force | QA, Purchase | QA decides the supplier again |
| Customer Licence | a verified licence ends | QA, Sales | a renewal is verified without a gap |
| Document Review | an effective document's next review | the process owner, QA | reviewed (the next review date moves on) |
| Change Action | an action of an approved change | its owner, QA | the action is done |
| Effectiveness Check | an implemented change's check falls due | QA | the change is closed |

- **Completed only by the work in its record**: a system reminder has no Complete button. When its record no longer shows it due, the next run completes it and records when. A due date moved in its record completes the old reminder and raises a new one for the new date.
- **Overdue and escalation:** past its due date a reminder is *Overdue*; overdue for the days QA sets (*Escalate After Overdue*, 7 until changed) it is *Escalated* once, and QA, Management and its responsible person get *Overdue: …*.
- **The existing alerts are unchanged** (Lot Expiring Soon and Expired, Supplier Certificate Expiring, Customer Licence Expiring, Document Review Due, the change action and effectiveness alerts): they still tell the responsible jobs as the item falls due.
- **Recurring reminders:** QA records a *Reminder Schedule* (what, responsible job, first due date, frequency weekly to yearly, days before to raise). Each occurrence is raised within that time and everyone of the job is told; it goes overdue and escalates like any reminder; a person of the job, or QA, opens it and clicks **Mark Completed**, saying what was done. The next occurrence follows the calendar from the due date. A schedule is never deleted; clearing **Active** stops it. The next due date is set by the system.
- **Who sees what:** QA, Management and the System Administrator see every reminder; everyone else those whose responsible jobs they hold, and those naming them.
- **Robustness:** a category whose records cannot be read is logged and left as it was, so a failure never completes reminders; the other categories go on.

## 3. Requirements and acceptance criteria

> **URS-PLT-014** Every item the system alerts on as falling due (a lot's expiry, a supplier's certificate, a customer's licence, a document's review, a change action, a change's effectiveness check) is tracked as a reminder from the day it falls within its lead time until its own record shows the work done, which alone completes it, recording when; past its due date it is overdue, and overdue for the days QA sets in Quality Settings (7 until changed) it is escalated once to QA and Management and its responsible person; each job sees the reminders of its jobs and its own, QA and Management all; a reminder is never made, changed or deleted by hand.

> **URS-PLT-015** QA plans recurring activities, each with what is to be done, the responsible job, its first due date, a frequency (weekly, monthly, quarterly, half-yearly or yearly) and how many days before it is raised; the system raises each occurrence within that time and tells the responsible job; an occurrence is overdue and escalated as any reminder; a person of the responsible job, or QA, completes it saying what was done, and the next occurrence follows the calendar from the due date; a plan is never deleted, only made inactive.

| # | Criterion | Verification (`work_management/test_reminders.py`) |
| --- | --- | --- |
| C1 | Each of the six categories raised when due and completed only by its record | `TestEachCategoryIsTrackedUntilItsRecordShowsItDone.*` |
| C2 | Raised once; not before its lead time | `test_raised_once_while_it_stays_due`, `test_a_lot_outside_its_lead_time_is_not_raised` |
| C3 | Overdue, escalated once at QA's days, to QA and not to others | `test_past_its_due_date_it_is_overdue_and_after_the_days_qa_set_escalated_once` |
| C4 | A failing category completes nothing; the others go on | `test_one_categorys_failure_leaves_its_reminders_and_the_others_go_on` |
| C5 | Never made, changed or deleted by hand; system reminders not completed by hand | `TestTheRecord.*` |
| C6 | Each job sees its own; QA all | `test_each_job_sees_its_own_reminders_qa_sees_all` |
| C7 | Recurring: raised in its lead time, job told; completed by its job or QA with a remark; next by the calendar; plan names a business job, not deleted, next due set by the system | `TestRecurringReminders.*` |
| C8 | Escalation days within bounds, under change control | `compliance/test_quality_settings.py` |

## 4. Step-by-step browser testing guide

### RM-01 A system reminder
On the pilot, a lot with stock whose expiry QA sets 10 days out. Next day (or run the daily job): **Quality › Reminder**: *Lot … expires on …*, Open. As Stores, the same reminder is in the list. Put the lot on hold (QA): the next run completes it.

### RM-02 Escalation
A reminder past due by more than the escalation days: status *Escalated*; QA and Management have *Overdue: …*.

### RM-03 A recurring activity
As QA: **Quality › Reminder Schedule › New**: *Management review*, job QA Manager, Yearly, first due in 5 days, raise 7 days before. After the next run: the reminder is raised and QA told. Open it, **Mark Completed** with a remark: completed; the schedule's Next Due is a year after the first.

## 5. End-to-end business workflow
An item falls within its lead time → the alert tells its job and a reminder is raised → the work is done in its record (or not) → done: the reminder completes; not done: overdue, then escalated to QA and Management → the escalation is followed up.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `reminders.CATEGORIES` (each a function yielding what is due now), `run` (daily), `track`, `follow_up`, `raise_occurrence`, `complete_occurrence` (whitelisted), `query_conditions`, `has_permission` |
| Records | *Reminder* (`reminder_key` unique per category, record, row and due date), *Reminder Schedule* |
| Settings | Quality Settings `reminder_escalation_days` (7, 1–90) |
| Retention | Quality system records, 10 years |

## 7. Defects, edge cases and risks
- Calibration, maintenance, training expiry and licence renewals join as categories with their modules (38, 37, 41): each adds one function to `CATEGORIES`.
- A reminder raised for a lot that is then on hold is completed: an expired lot on hold is dealt with by QA's decision (module 17), not by this reminder.
- Escalation tells people once; the reminder stays escalated until done, visible to QA and Management.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Six categories tracked and completed by their records | Verified (automated) |
| Overdue and escalation | Verified (automated) |
| Recurring reminders | Verified (automated) |
| Visibility by job | Verified (automated) |
| Manual RM-01…RM-03 | **Not tested** |
