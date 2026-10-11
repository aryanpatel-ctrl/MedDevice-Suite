# Employee master (module 9) — feature document and browser test guide

Module 9 uses ERPNext's **Employee** as it is (master plan: "Used as is: routing and work centres, warehouses, employee"). It is not one of the nine version-controlled masters of URS-MST-001, and it has no requirement of its own. What this app adds to it comes from the platform requirements below.
Code: permissions in `meddevice_suite/setup/roles.py` (Employee), audit scope in `compliance/audit_scope.py`, retention in `compliance/retention.py`, sidebar in `setup/sidebars.py` (Organization), lookups in `setup/lookups.py`.
DFD trace: no data store of its own; people appear as the users of each process. Training and operator qualification come from the ERP flowchart (D2) and are module 37.

## 1. Feature overview

**Purpose.** The plant's people, as ERPNext keeps them: name, department, designation, the user account they sign in with. Operators name themselves on the time they log against a job card.

**Why it matters.** ISO 13485 6.2 asks that people doing work affecting quality are competent; that is recorded and enforced by the training module (37), which builds on this master. 21 CFR 11.10(d) and (g) ask that only authorised people use the system, which the job profiles and accounts do (module 47).

| Job profile | Here |
| --- | --- |
| Training Coordinator (HR User) | Records and keeps employees |
| Production Supervisor | Reads employees |
| Operator | Picks themselves on a job card's time log, never opens the record |
| QA, management, system administrator | Read |

## 2. Detailed functional explanation

- **The record** is ERPNext's Employee; nothing is added to it.
- **Who may do what** is set by the job profiles (URS-CMP-014): the Training Coordinator edits, Production reads, an Operator can only pick an employee in a lookup (URS-CMP-036).
- **Audit trail.** Every change to an employee is recorded with who, when, old and new values (module 50).
- **Retention.** Employee records are kept ten years with the equipment and personnel records (module 54).
- **Not here:** training records, competency and the gate that refuses an untrained operator on a job card (module 37 and module 19); the user accounts and job profiles (module 47).

## 3. Requirements and acceptance criteria

| Requirement | What it gives this master | Verification |
| --- | --- | --- |
| URS-CMP-014 | Job profiles grant exactly the roles a job needs | `setup/test_roles.py` |
| URS-CMP-036 | Lookups searchable by the job filling the form, without opening the record | `setup/test_lookups.py` |
| Audit trail (module 50) | Changes recorded | `compliance/test_audit.py`, audit scope |
| Retention (module 54) | Kept ten years | `compliance/test_retention.py` |

## 4. Step-by-step browser testing guide

### EM-01 Record an employee
Role: Training Coordinator. **Organization › Employee › + Add**: name, gender, date of birth, date of joining, department, designation, user. Save. Expected: saved.

### EM-02 Who may change it
Role: Production Supervisor: the employee opens read-only. Role: Operator: the employee cannot be opened; on a job card's time log the operator can still pick themselves.

### EM-03 Audit trail
Change the employee's designation as the Training Coordinator; open its audit trail as QA: the old and new value, who and when.

## 5. End-to-end business workflow
Person joins → the Training Coordinator records the employee and the system administrator gives them a user with their job profile → training records and qualifications (module 37) → the operator logs time on job cards for operations they are qualified for (modules 19, 37) → records kept ten years.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Permissions | `setup/roles.py` `PERMISSIONS["Employee"]`: HR User edit, Manufacturing User view, Operator pick, oversight roles read |
| Audit | `compliance/audit_scope.py` includes Employee |
| Retention | `compliance/retention.py`: equipment and personnel records, 10 years |
| Desk | Organization sidebar: Employee |

## 7. Defects, edge cases and risks
- No demo employees are loaded yet, and the demo job-card time logs name no employee; the client's real employee list is an open item of the master plan (item 11, with the final routing and warehouse data).
- HRMS is not installed: leave, attendance and payroll are outside the client's scope.

## 8. Test results and verification summary
No code changes in this module; the tests above run in the full suite of every pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Permissions per job | Verified (automated) |
| Audit trail and retention | Verified (automated) |
| Manual EM-01…EM-03 | **Not tested** |
| Client's employee list loaded | **Open (client data)** |
