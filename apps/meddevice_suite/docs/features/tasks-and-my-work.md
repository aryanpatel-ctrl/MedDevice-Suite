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
| Change requests to submit, assess, approve | whoever raised the draft; QA (never on a change they raised); Management (never the requester or QA's approver) |
| Lots waiting for certificate verification or inspection; for the QA decision | QC Inspector; QA |
| Items, BOMs, equipment, operations, workstations, routings, stores in draft; supplier decisions, customer licences and regulatory licences to decide; training matrices to approve | QA |
| Quotations awaiting price approval; purchase orders awaiting approval | Sales Manager; Purchase Manager |
| Device master record revisions to sign, then to approve | the author; QA (never the author) |
| Risk acceptability policy to review, then to approve | QA; Management (never its reviewer) |
| Risk assessment revisions to sign, review, approve | the author; the reviewer; QA (never the author or reviewer) |
| Risk management reports to sign, then to approve | the author; QA (never the author) |
| Executed IQ, OQ, PQ to approve | QA (never its performer) |

  Every line is read with the person's own permissions, so nobody is shown a record they could not open, and a line is shown only to someone the signed act would accept. It reads the records themselves, so it is never out of step with them.
- **Every signed act is accounted for:** each act in the register of signed acts (`compliance/signing.py`, `SIGNING_ACTIONS`) is either listed by a source above (`my_work.LISTED_BY`), listed through the open reminder kept for it (`my_work.REMINDED_AS`: an effectiveness check, a calibration impact assessment, a training to sign, a risk review to close), or declared not a queue with the reason (`my_work.NOT_WAITING`: acts signed at the moment of the work, such as completing a calibration, and QA's own decisions, such as withdrawing a record). A test fails when an act is added to the register without one of the three, so a new approval cannot be forgotten here.

## 3. Requirements and acceptance criteria

> **URS-PLT-016** Any business job raises a task, assigning it to a person with a due date and naming its approver, another person; the person assigned says what was done and submits it for approval; the approver approves it with an electronic signature, which closes it, or returns it with a signed reason to be done again; until it is approved, whoever raised it, or QA, may withdraw it saying why; who may change what it is, its result and its status is held to these steps, a task is never deleted or cancelled, an approved task is final, and its due date is tracked by the reminders.

> **URS-PLT-017** Each person has one list of everything waiting on them, each line opening its record: their open reminders, the tasks they are to do or approve, the revisions they are to complete, review or approve, the change requests they are to submit, assess or approve, the lots waiting on QC or QA, the masters, supplier decisions, customer and regulatory licences and training matrices waiting for QA, the quotations and purchase orders waiting for their approval, the device master records, risk acceptability policies, risk assessments and risk management reports they are to sign as author, review or approve, and the executed equipment qualifications waiting for QA; every signed act a person waits to do is listed, through its record or its reminder; it shows only records the person may open, and does not ask anyone for a signature the signed act would refuse them, such as an approval of what they authored, reviewed or executed.

| # | Criterion | Verification |
| --- | --- | --- |
| C1 | Raise, do, submit, approve with signature; return and redo; withdraw | `work_management/test_work_task.py` `TestTheFlow.*` |
| C2 | Who changes what; nothing while submitted; never deleted, cancelled or submitted but by approval; visibility | `TestWhoChangesWhat.*` |
| C3 | Due date tracked through each round | `TestItsDueDateIsTracked.*` |
| C4 | My Work: tasks, masters, orders, change requests, revisions by whoever acts next; not to those who may not open them; the report | `work_management/test_my_work.py` |
| C5 | Every signed act in `SIGNING_ACTIONS` is listed by a source, listed through a reminder category that exists, or declared not a queue with a reason; none twice; none stale | `TestEverySignedActIsAccountedFor.*` |
| C6 | Device master records, risk policy, assessments and reports, qualifications, regulatory licences, training matrices wait on whoever signs next, never on the author, reviewer or performer for the approval; a change request never on its requester for approval | `TestDeviceMasterRecordAndRiskQueues.*`, `TestQualificationQueue.*`, `TestRegulatoryLicenceQueue.*`, `TestTrainingMatrixQueue.*`, `test_a_change_request_never_asks_its_requester_to_approve_it` |

## 4. Step-by-step browser testing guide

### TK-01 A task through its approval
As Production Supervisor: **Work Task › New**, assigned to the operator, approver QA, due in 3 days. As the operator: **My Work** lists it; open it, **Submit for Approval** with what was done. As QA: **My Work** lists *Approve the task*; **Return** with a reason; as the operator, submit again; as QA, **Approve** and sign. The task is Approved; the signature shows on the form.

### TK-02 My Work as each job
Sign in as each job and open **My Work**: only that job's waiting work is listed; each line opens its record.

### TK-03 The approval queues of modules 31, 37, 39, 40 and 41
As the Production Supervisor, draft a device master record: **My Work** lists *Complete and sign as author*; sign it Authored. As QA: *Approve the device master record of …*. As QA, draft a risk acceptability policy: QA's **My Work** lists *Review the risk acceptability policy*; after the review, Management's lists *Approve …* and QA's no longer does. As Maintenance, execute an IQ: QA's **My Work** lists *Approve the IQ of …*; Maintenance's does not. As QA, record a regulatory licence and a training matrix: both are listed for QA until verified or approved.

## 5. End-to-end business workflow
A need for work → task raised and assigned → done and submitted → approved with a signature (or returned and done again) → closed; meanwhile everything waiting on each person is in their My Work, and what goes overdue is escalated by the reminders.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `work_task.check`, `submit_for_approval`, `approve`, `return_for_rework`, `withdraw`, `query_conditions`, `has_permission`; `my_work.SOURCES`, `LISTED_BY`, `REMINDED_AS`, `NOT_WAITING`, `waiting_on` |
| Records | *Work Task* (submittable; the signed approval is its only submit) |
| Signed acts | Work Task: Approve (Approved), Return (Rejected) |
| Reminders | category *Work Task* (`reminders.work_tasks_due`, lead time as change actions) |

## 7. Defects, edge cases and risks
- A CAPA, complaint or NCR action (modules 33, 35, 43) is raised as a task about its record until those modules bring their own actions.
- My Work lists masters in draft for QA even when they are not yet complete; the master's form says what its approval still needs.
- Found in the foundation review of 2026-10-12 and fixed: the device master record, risk management, equipment qualification and regulatory licence queues (and the training matrix) were not listed, and a QA person who raised a change request was asked to approve it.
- A draft risk acceptability policy is listed for every QA person, as any of them may review it; drafts of calibration, maintenance and qualification records are not listed for their performer, who signs as the work is done, and the work's due date is tracked by its reminder.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Task flow with signed approval | Verified (automated) |
| Who changes what | Verified (automated) |
| My Work by job, within permissions | Verified (automated) |
| Every signed act accounted for; the queues of modules 31, 37, 39, 40, 41 | Not yet run (automated, added 2026-10-12) |
| Manual TK-01, TK-02, TK-03 | **Not tested** |
