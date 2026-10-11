# Training and competence (module 37) — feature document and browser test guide

Requirements: **URS-TRN-001 to URS-TRN-006** (`meddevice_suite/qa/requirements.toml`). Builds on document control (module 30), e-signature (49), evidence files (54), reminders (60), routing (module 3).
Code: `meddevice_suite/qms/training.py`; records *Training Matrix* (with *Training Matrix Item*) and *Training Record*; report *Training Status*; reminder *Training Due*; `pilot/training.py` (demo operator); hooks in `meddevice_suite/hooks.py`.
DFD trace: D2 Training (training records, expiry alerts, QMS linkage); production stage 3 ("trained operator"). Decisions: plan section 16, D-07 to D-10 and D-17 to D-19 (2026-10-11). HRMS is not used. Training is built here on ERPNext's Employee and Designation.

## 1. Feature overview

**Purpose.** People do regulated work only once they are trained, and assessed where needed, on the revision of the document in effect. Every training is a signed record, and production refuses an operator who is not competent.

**Why it matters.** ISO 13485 6.2 requires training needs to be determined, training given, its effectiveness evaluated and records kept. 21 CFR 11.10(i) requires that people using electronic records are trained. An operator working to a superseded work instruction, or one never assessed, is a direct product risk.

| Job profile | Here |
| --- | --- |
| QA Manager | Approves each job's training matrix (signed); enters paper history (signed); revokes a competence (signed); assesses |
| Training Coordinator (HR User) | Drafts matrices; plans each training (method, date, trainer, assessor, assessment) |
| Production Supervisor | Assesses (signed Competent / Not Competent) |
| Every employee with an account | Signs that they were trained, or read and understood; sees only their own records |
| Management | Read |

## 2. Detailed functional explanation

- **Training Matrix:** one per job (Designation). It lists the controlled documents, whether reading is enough for each, and the validity in months (0 means it does not expire). It takes effect only when QA signs **Approve**. A change is a cancel and amend, approved again (D-17). A withdrawn document cannot be listed, and no document can be listed twice.
- **What is needed:** for each active employee, every document their job's approved matrix names. The record is opened on the revision **in effect**, and also on a revision **approved to take effect later**, so people are trained before it applies (D-07). Its due date is that revision's effective date. Otherwise the due date is today plus *Training Due Within* (14 days, Quality Settings).
- **One rule keeps this true** (`training.reconcile`). It opens what is needed and not covered, and closes what is open and no longer needed: *Withdrawn* when the person left or changed job, or the row was removed; *Superseded* when the revision is no longer in effect or about to take effect. It runs when a revision is approved or takes effect, when a document is withdrawn, when an employee joins, changes job, leaves or returns, when a matrix is approved or cancelled, and when an assessment or revocation needs retraining. It also runs every day, which repairs any failure: a failure is logged and never holds back the change that caused it.
- **Completing a record** (D-08 to D-10):
  1. The coordinator plans it: method, date, trainer, assessor and assessment method.
  2. The **trainee**, on their own account (no shared logins), signs **Trained**. What was planned is then fixed.
  3. The **named assessor** signs **Competent** or **Not Competent** with a reason. The assessor holds the Production Supervisor or QA role and is never the trainee.
  4. Where the matrix allows reading, *Read and Understand* plus the trainee's signature completes the record without an assessor.
  5. A Not Competent assessment opens a *Retraining* record.
- **Paper history:** only QA enters a *Historical* record. It needs the training date, the method and a private certificate, which becomes evidence, and QA's **Record History** signature. Nobody enters their own history.
- **Validity and refresher:** a record is valid until the end of its row's validity, and has no end where the row sets none. A *Refresher* opens when the end falls within *Training Refresher Opens* (30 days, Quality Settings), due on that day.
- **Revoke** (D-18): QA signs **Revoke** with a reason, for example after a nonconformity. The person is refused for that work and a *Retraining* record opens.
- **Never deleted or cancelled.** Status changes only through these acts or the system. A completed record changes only by revocation.
- **At the workstation** (URS-TRN-005): every time log on a job card must name an active employee. That employee must hold a completed, unexpired Competent record on the **revision in effect** of the work instruction of the operation they do (the sub-operation if logged). This is checked at every save, including saves that skip validation, after submit, and in ERPNext's `make_time_log` (overridden, see module 38). A finished time log is not judged again by later training. Block, never warn.
- **Reminders:** *Training Due*, to the trainee until they sign, then to the assessor; the Training Coordinator and QA see all of them.
- **Training Status report:** each employee and each document their job needs, by revision (in effect, or to take effect), with its state (Not started, Open, Trained, Competent, Expired, Not Competent, Revoked), the due date and the end of validity.
- **Use of the ERP** (D-19): the client's "Use of the ERP" SOP goes in every job's matrix. It is client data, loaded at migration.

## 3. Requirements and acceptance criteria

| # | Requirement | Verification (`qms/test_training.py`) |
| --- | --- | --- |
| T1 | TRN-001 needs open and close: matrix approval, new employee, other jobs and inactive none, revision to take effect, superseded, job change, leaver, no duplicate or manual open, withdrawn document, failure repaired daily | `TestNeedsOpen.*` |
| T2 | TRN-002 trainee then assessor; only the trainee, only on their account; assessor named, with the role, never the trainee; assessor named before signing; wrong password; reading only where allowed; Not Competent with a reason; never edited, cancelled or deleted; plan fixed; QA history with certificate; own records only | `TestSignedCompletion.*` |
| T3 | TRN-003 validity, refresher once within the warning days, setting, reminders | `TestValidityAndRefresher.*` |
| T4 | TRN-004 QA approval by signature, one per job, cancel and amend, no duplicates | `TestMatrixApproval.*` |
| T5 | TRN-005 competent accepted; untrained, unassessed, unnamed, previous revision, expired, inactive refused; history counts; unvalidated save; finished time not judged; ERPNext's method; every ERPNext time-log path | `TestJobCardGate.*` |
| T6 | TRN-006 QA revokes with a reason; retraining opens | `TestRevocation.*` |

## 4. Step-by-step browser testing guide

### TR-01 Approve a matrix (QA)
**Quality › Training Matrix › New**: job *Machine Operator*, add the work instructions. Then **Approve** and sign. Expected: each active employee in that job gets an open Training Record per document.

### TR-02 Train and assess
1. As the Training Coordinator, open a record and set the method, date, trainer, assessor (the Production Supervisor) and assessment.
2. As the operator, use **Sign: Trained**.
3. As the Production Supervisor, use **Competent**.

Expected: the record is completed with two signatures, and the *Training Due* reminder closes.

### TR-03 Refused at the workstation
As an untrained operator, log time on a job card of that operation. Expected: *work is logged only by trained, competent operators*.

### TR-04 New revision
Issue a new revision of the work instruction with a later effective date. Expected: each operator gets an open record due on that date, and their current competence still works until then.

### TR-05 Paper history and revocation (QA)
Create a Training Record with *Why = Historical*, attach the certificate, then **Record History** and sign. Then **Revoke Competence** with a reason. Expected: the operator is refused, and a Retraining record opens.

## 5. End-to-end business workflow
Document approved (module 30) → QA's matrix says who needs it → records open → trained, assessed and signed → operator works (production stage 3) → new revision, refresher, or revocation → retrained.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Rule | `training.reconcile` (`needed`, `revisions_to_train`), `reconcile_safely`, `daily` |
| Signed acts | Training Matrix: Approve (Approved). Training Record: Trained (Trained), Competent (Competent), Not Competent (Rejected), Record History (Verified), Revoke (Rejected) |
| New signature meanings | *Trained*, *Competent* (module 49) |
| Gate | `training.guard_job_card` (Job Card before_validate, before_update_after_submit), `refuse_new_operators` (from `equipment_care.make_time_log`) |
| Hooks | Employee on_update; document control approve, take effect, withdraw; daily after revisions take effect |
| Settings | `training_due_days` 14, `training_expiry_warning_days` 30 |
| Permissions | Designation: HR edits, QA and production pick; Training Record row-level for others (`query_conditions`, `has_permission`) |
| Pilot | `pilot/training.py` trains the demo operator through the real acts; the flow names that operator on each time log |

## 7. Defects, edge cases and risks
- When a revision takes effect without prior training, every operator on it is refused at once. Approve with a future effective date (records open at approval).
- Paper history must be entered before go-live, or the gate refuses everyone (D-07 migration).
- Every revision is trained, even when its change request says there is no training impact (decided 2026-10-11, D-20). Use a future effective date for minor changes, and a matrix row that allows reading where appropriate.
- An ERPNext upgrade could add a new way to log time; `test_every_whitelisted_time_log_path_is_guarded` fails until it is reviewed.
- Records open only for employees with a job. An employee without a designation needs no training and cannot pass the gate.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Needs opened and closed by one idempotent rule | Verified (automated) |
| Trainee and assessor signatures; history; revocation | Verified (automated) |
| Gate on every time-log path | Verified (automated) |
| Pilot and browser journey use a trained operator | Verified (automated) |
| Manual TR-01…TR-05 | **Not tested** |
