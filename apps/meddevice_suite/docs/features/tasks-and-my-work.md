# Tasks, approvals and My Work (module 59) — feature document and browser test guide

Requirements: **URS-PLT-016** (tasks with their approval) and **URS-PLT-017** (My Work) (`meddevice_suite/qa/requirements.toml`). Builds on the signed acts of every module (module 49), the approval workflows (module 48) and the reminders (module 60).
Code: `meddevice_suite/work_management/work_task.py`, `work_management/my_work.py`, record *Work Task*, report *My Work*; signed acts in `compliance/signing.py`; hooks (list permissions) in `meddevice_suite/hooks.py`; desk entries in `setup/sidebars.py`.
DFD trace: 9B Task and Approval (B1 create task, B2 assign task, B3 execute task, B4 submit for approval, B5 approve or reject, B6 close task); data store D11; master plan section 8 ("PO approval, document approval, QA release, CAPA actions, change control, complaint follow-up").

## 1. Feature overview

**Purpose.** Work outside a module's own records is assigned, done and approved on record; and each person sees, in one place, everything waiting on them.

**Why it matters.** Work that is asked for by word of mouth leaves no evidence of who did it, who checked it, or that it was done at all; approvals scattered over a dozen screens wait unseen.

| Job profile | Here |
| --- | --- |
| Any business job | Raises a task, assigns it, names its approver; sees **My Work** |
| The person assigned | Does it and submits it with what was done |
| The approver (another person) | Approves it, or returns it, with a signature |
| Whoever raised it, or QA | May withdraw it before it is approved |
| QA, Management | See every task |

## 2. Detailed functional explanation

- **Raising a task** (*Work Task › New*): what is to be done, who does it (**Assigned To**), who approves it (**Approver**, never the person assigned, and both a person's own active account), the **Due** date, and optionally the record it is about.
- **Doing it:** the person assigned fills **What Was Done** and clicks **Submit for Approval** (the result is required).
- **Approving:** the approver clicks **Approve** and signs (password and reason): the task is approved and closed for good. **Return** asks for a signed reason; the task goes back to the person assigned, who does it again and submits it again.
- **Withdrawing:** before approval, whoever raised it, or QA, clicks **Withdraw** and says why.
- **Who changes what:** what the task is changes only by whoever raised it, or QA, while it is with the person assigned; the result only by the person assigned; the status only by the steps above. Nothing changes while it waits for approval. A task is never deleted or cancelled.
- **Due date:** tracked by the reminders (category *Work Task*): with the person assigned, then with the approver once submitted, until approved or withdrawn; overdue and escalated like any reminder.
- **My Work** (in every business area of the desk): one list of everything waiting on the person, each line opening its record:

| Waiting | For whom |
| --- | --- |
| Open reminders | the person and their jobs |
| Tasks to do, or to approve | the person assigned; the approver once submitted |
| Revisions to complete, review, approve | the author; the reviewer; QA (never on a revision they authored or reviewed) |
| Change requests to submit, assess, approve | whoever raised the draft; QA; Management |
| Lots waiting for certificate verification or inspection; for the QA decision | QC Inspector; QA |
| Items, BOMs, equipment, operations, workstations, routings, stores in draft; supplier decisions and customer licences to decide | QA |
| Quotations awaiting price approval; purchase orders awaiting approval | Sales Manager; Purchase Manager |

  Every line is read with the person's own permissions, so nobody is shown a record they could not open. It reads the records themselves, so it is never out of step with them.

## 3. Requirements and acceptance criteria

> **URS-PLT-016** Any business job raises a task, assigning it to a person with a due date and naming its approver, another person; the person assigned says what was done and submits it for approval; the approver approves it with an electronic signature, which closes it, or returns it with a signed reason to be done again; until it is approved, whoever raised it, or QA, may withdraw it saying why; who may change what it is, its result and its status is held to these steps, a task is never deleted or cancelled, an approved task is final, and its due date is tracked by the reminders.

> **URS-PLT-017** Each person has one list of everything waiting on them, each line opening its record: their open reminders, the tasks they are to do or approve, the revisions they are to complete, review or approve, the change requests they are to submit, assess or approve, the lots waiting on QC or QA, the masters, supplier decisions and customer licences waiting for QA, and the quotations and purchase orders waiting for their approval; it shows only records the person may open.

| # | Criterion | Verification |
| --- | --- | --- |
| C1 | Raise, do, submit, approve with signature; return and redo; withdraw | `work_management/test_work_task.py` `TestTheFlow.*` |
| C2 | Who changes what; nothing while submitted; never deleted, cancelled or submitted but by approval; visibility | `TestWhoChangesWhat.*` |
| C3 | Due date tracked through each round | `TestItsDueDateIsTracked.*` |
| C4 | My Work: tasks, masters, orders, change requests, revisions by whoever acts next; not to those who may not open them; the report | `work_management/test_my_work.py` |

## 4. Step-by-step browser testing guide

### TK-01 A task through its approval
As Production Supervisor: **Work Task › New**, assigned to the operator, approver QA, due in 3 days. As the operator: **My Work** lists it; open it, **Submit for Approval** with what was done. As QA: **My Work** lists *Approve the task*; **Return** with a reason; as the operator, submit again; as QA, **Approve** and sign. The task is Approved; the signature shows on the form.

### TK-02 My Work as each job
Sign in as each job and open **My Work**: only that job's waiting work is listed; each line opens its record.

## 5. End-to-end business workflow
A need for work → task raised and assigned → done and submitted → approved with a signature (or returned and done again) → closed; meanwhile everything waiting on each person is in their My Work, and what goes overdue is escalated by the reminders.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `work_task.check`, `submit_for_approval`, `approve`, `return_for_rework`, `withdraw`, `query_conditions`, `has_permission`; `my_work.SOURCES`, `waiting_on` |
| Records | *Work Task* (submittable; the signed approval is its only submit) |
| Signed acts | Work Task: Approve (Approved), Return (Rejected) |
| Reminders | category *Work Task* (`reminders.work_tasks_due`, lead time as change actions) |

## 7. Defects, edge cases and risks
- A CAPA, complaint or NCR action (modules 33, 35, 43) is raised as a task about its record until those modules bring their own actions.
- My Work lists masters in draft for QA even when they are not yet complete; the master's form says what its approval still needs.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Task flow with signed approval | Verified (automated) |
| Who changes what | Verified (automated) |
| My Work by job, within permissions | Verified (automated) |
| Manual TK-01, TK-02 | **Not tested** |
