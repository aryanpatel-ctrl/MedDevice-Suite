# Reminders and next due (module 60) — feature document and browser test guide

Requirements: **URS-PLT-014** (system reminders tracked, overdue, escalated, completed by the work), **URS-PLT-015** (recurring reminders QA plans) and **URS-PLT-020** (who is told when a reminder is raised and when it is escalated) (`meddevice_suite/qa/requirements.toml`). Builds on the alerts of modules 4, 5, 18, 30, 36 and 51, and on Quality Settings (URS-PLT-012).
Code: `meddevice_suite/work_management/reminders.py`; records *Reminder* and *Reminder Schedule* (`work_management/doctype/`); alert `work_management/notification/reminder_overdue/`; hooks (daily job, Reminder list permissions) in `meddevice_suite/hooks.py`; Quality Settings `reminder_escalation_days`.
DFD trace: 9C Reminders / Next Due (C1 define reminder category, C2 set due date or frequency, C3 generate reminder, C4 notify users, C5 track and escalate, C6 mark completed); data store D11 Task, Approval & Reminders; master plan sections 8 and 10.

## 1. Feature overview

**Purpose.** Nothing that falls due is forgotten: each due item is tracked until the work is done, and what stays undone is escalated.

**Why it matters.** A one-off alert that nobody acts on leaves no trace: an expired lot left in stock, a supplier certificate lapsed, a review not done. ISO 13485 asks that these activities are planned and their records kept; an auditor asks what was overdue and who knew.

| Job profile | Here |
| --- | --- |
| Every job | Sees the reminders of its jobs and its own (Reminder list); is told when a reminder is raised for it |
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
| Document Review | an effective document's next review | the process owner, QA | reviewed (the next review date moves on): a new revision taking effect, or QA's signed **Review: Still Current**, which completes the reminder at once with QA as Completed By (`reminders.settle`, URS-QMS-034, 2026-10-12) |
| Change Action | an action of an approved change | its owner, QA | the action is done |
| Effectiveness Check | an implemented change's check falls due | QA | the change is closed |

- **The table above is not the whole list:** the categories of modules 31, 37, 38, 39, 40, 41, 59 and 61 (DMR re-approval, training, calibration, maintenance, calibration impact, requalification, risk review, regulatory licence, work task, file validity) join it the same way; `reminders.CATEGORIES` is the register.
- **Completed only by the work in its record**: a system reminder has no Complete button. When its record no longer shows the item to be done at all, the next run completes it and records how. A due date moved in its record completes the old reminder, saying the date moved and to when, and a new one is raised for the new date once it falls within the lead time.
- **The lead time decides when a reminder is raised, never when it is completed.** If QA shortens a lead time, a reminder already raised whose item is still to be done stays open: the run reads the category again with no lead time and completes only what that no longer shows.
- **Due again, raised again:** an item whose reminder was completed and which falls due again (a lot taken off hold, still expiring) gets a new reminder on the next run; the completed one keeps its record.
- **Who is told, once each, from one source** (URS-PLT-020): when a reminder is raised, its responsible person, or, when it names none, everyone holding its responsible jobs, gets one notice: from the module that owns the record where that module already alerts them as the item falls due (Lot Expiry, Supplier Certificate, Customer Licence, Document Review, Change Action, Effectiveness Check, Calibration Impact, Regulatory Licence, DMR Re-approval), from the reminder otherwise (Work Task, File Validity, Calibration Due, Maintenance Due, Requalification Due, Training Due, Risk Review, and recurring occurrences). Each category declares which (`Category.told_by`). When a reminder is escalated, QA, Management and its responsible person get *Overdue: …* from the reminder, for every category: no module alert escalates. What was last told is kept on the reminder (**People Told Of**: Raised, Escalated), so a run that repeats tells nobody again; a notice that could not be sent is sent on the next run.
- **Overdue and escalation:** past its due date a reminder is *Overdue*; overdue for the days QA sets (*Escalate After Overdue*, 7 until changed) it is *Escalated* once.
- **The existing alerts are unchanged** (Lot Expiring Soon and Expired, Supplier Certificate Expiring, Customer Licence Expiring, Document Review Due, the change action and effectiveness alerts, Calibration Out of Tolerance, Regulatory Licence Expiring, Device Master Record Out of Date): they remain the raise notice of their categories.
- **Recurring reminders:** QA records a *Reminder Schedule* (what, responsible job, first due date, frequency weekly to yearly, days before to raise). Each occurrence is raised within that time and everyone of the job is told, with the schedule's description; it goes overdue and escalates like any reminder; a person of the job, or QA, opens it and clicks **Mark Completed**, saying what was done. The next occurrence follows the calendar from the due date. A schedule is never deleted; clearing **Active** stops it. The next due date is set by the system.
- **Who sees what:** QA, Management and the System Administrator see every reminder; everyone else those whose responsible jobs they hold, and those naming them.
- **Robustness:** a category whose records cannot be read is logged and left as it was, so a failure never completes reminders; the other categories go on.

## 3. Requirements and acceptance criteria

> **URS-PLT-014** Every item the system alerts on as falling due (a lot's expiry, a supplier's certificate, a customer's licence, a document's review, a change action, a change's effectiveness check) is tracked as a reminder from the day it falls within its lead time until its own record shows the work done, which alone completes it, recording when; a reminder whose item its record still shows to be done stays open, whatever the lead time is changed to, and an item due again after its reminder was completed is raised again on the next run; past its due date it is overdue, and overdue for the days QA sets in Quality Settings (7 until changed) it is escalated once to QA and Management and its responsible person; each job sees the reminders of its jobs and its own, QA and Management all; a reminder is never made, changed or deleted by hand.

> **URS-PLT-015** QA plans recurring activities, each with what is to be done, the responsible job, its first due date, a frequency (weekly, monthly, quarterly, half-yearly or yearly) and how many days before it is raised; the system raises each occurrence within that time and tells the responsible job; an occurrence is overdue and escalated as any reminder; a person of the responsible job, or QA, completes it saying what was done, and the next occurrence follows the calendar from the due date; a plan is never deleted, only made inactive.

> **URS-PLT-020** When a reminder is raised, its responsible person is told, or, when it names none, everyone holding its responsible jobs, by one notice: the alert of the module that owns the record where it has one, the reminder's otherwise; when it is escalated, QA, Management and its responsible person are told by the reminder; each is told of each once, however often the daily run repeats, and a notice that could not be sent is sent on the next run.

| # | Criterion | Verification (`work_management/test_reminders.py`) |
| --- | --- | --- |
| C1 | Each of the six categories raised when due and completed only by its record | `TestEachCategoryIsTrackedUntilItsRecordShowsItDone.*` |
| C1a | Every category in `CATEGORIES`, present or added later: raised within the lead time, its people told of the raise once, by its module or by the reminder, never both; kept open when the lead time is shortened; completed only when done; raised again when due again; each category's choice of who tells it matches the test's explicit list | `TestEveryCategoryKeepsTheContract.*` (data-driven) |
| C1b | A shorter lead time completes nothing; a lot back from hold is raised again, the first reminder kept; a moved due date is recorded as such | `TestSettledOnlyWhenTheWorkIsDone.*` |
| C1c | Raised: its jobs told when it names no person, its person alone when it names one; where the module alerts (lot expiry), its alert alone; escalated: QA, Management and its person told; each once; a failed notice is sent on the next run; the upgrade records what was told before it | `TestNotices.*` |
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
A reminder past due by more than the escalation days: status *Escalated*; QA and Management have *Overdue: …*; **People Told Of** reads *Escalated*. Run the daily job again: nobody gets a second notice.

### RM-04 Raised and told, kept while still to be done
As Stores, check the bell after RM-01's run: *Lot … expires on …*, opening the reminder. As QA, under change control, shorten *Lot Expiry Alert* so the lot is outside it and run the daily job: the reminder is still Open. Take the lot off hold after RM-01 (QA): the next run raises a new reminder; the first stays Completed.

### RM-03 A recurring activity
As QA: **Quality › Reminder Schedule › New**: *Management review*, job QA Manager, Yearly, first due in 5 days, raise 7 days before. After the next run: the reminder is raised and QA told. Open it, **Mark Completed** with a remark: completed; the schedule's Next Due is a year after the first.

## 5. End-to-end business workflow
An item falls within its lead time → the alert tells its job and a reminder is raised → the work is done in its record (or not) → done: the reminder completes; not done: overdue, then escalated to QA and Management → the escalation is followed up.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `reminders.CATEGORIES` (each a `Category`: a function yielding what is due now, its lead time read through `within`, and `told_by`, the module alert that tells its people on raise, if any), `run` (daily), `track`, `whole_horizon`, `follow_up`, `send_notices`, `notify`, `raise_occurrence`, `complete_occurrence` (whitelisted), `query_conditions`, `has_permission` |
| Records | *Reminder* (`reminder_key` unique per category, record, row and due date, held by the open reminder of the item; `notified`: what its people were last told), *Reminder Schedule* |
| Patch | `patches/v0_1/record_reminders_already_told.py`: escalated reminders and raised recurring occurrences recorded as told before the upgrade |
| Settings | Quality Settings `reminder_escalation_days` (7, 1–90) |
| Retention | Quality system records, 10 years |

## 7. Defects, edge cases and risks
- Calibration, maintenance, training expiry and licence renewals join as categories with their modules (38, 37, 41): each adds one function to `CATEGORIES`.
- A reminder raised for a lot that is then on hold is completed: an expired lot on hold is dealt with by QA's decision (module 17), not by this reminder. Taken off hold and still expiring, the lot is raised again.
- Found in the foundation review of 2026-10-12 and fixed: a system reminder told nobody when it was raised; a reminder was completed when QA shortened the lead time, though the work was not done; a completed reminder stopped its item being raised again.
- A lot taken off hold and raised again is not told again: its expiry alert went once, and the reminder leaves raise notices to the module for that category.
- Fixed before release: the reminder also told, on raise, the people its module had just alerted; categories now declare who tells them. The escalation notice is Frappe's standard alert, which logs a failure to send instead of raising it; the reminder then records it as told.
- Escalation tells people once; the reminder stays escalated until done, visible to QA and Management.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Six categories tracked and completed by their records | Verified (automated) |
| Every category held to the contract (data-driven); notices once each | Not yet run (automated, added 2026-10-12) |
| Overdue and escalation | Verified (automated) |
| Recurring reminders | Verified (automated) |
| Visibility by job | Verified (automated) |
| Manual RM-01…RM-04 | **Not tested** |
