# Routings, operations and workstations (module 3) — feature document and browser test guide

Requirement: **URS-MST-012** (`meddevice_suite/qa/requirements.toml`). Builds on URS-MST-001 (version control of masters), module 30 (document control) and URS-MST-010 (equipment).
Code: `meddevice_suite/master_data/routing.py`, the shared approval `master_data/master_approval.py`, hooks in `meddevice_suite/hooks.py` (Operation, Workstation, Routing `validate` and `onload`; BOM `before_submit`; Work Order and Job Card `before_validate`), signed acts in `compliance/signing.py`, version control in `master_data/versioning.py`, desk in `public/js/master_data.js`, loaders `pilot/documents.py`, `pilot/demo.py`, `qa/ui_journey.py`.
DFD trace: D3 Item / BOM / Routing Master; D2 "all masters are version controlled and linked with Document Control"; master plan section 3 and stage 3.

## 1. Feature overview

**Purpose.** How a product is made (its routing, each operation and the workstation that does it) is approved by QA before production uses it, and each operation points at the work instruction the operator follows.

**Why it matters.** ISO 13485 4.2.4 and 7.5.1, and 21 CFR 820.70(a), ask that production follows documented, approved procedures. An operation done to an unapproved or superseded instruction makes a nonconforming device.

| Job profile | Here |
| --- | --- |
| Production Supervisor | Records operations (with their work instruction), workstations and routings |
| QA Manager | Approves each with a signature; changes after approval go through change control |
| Operator, QC, Maintenance | Read |

## 2. Detailed functional explanation

- **Operation.** Production records it with its **Work Instruction** (a controlled document of type *Work Instruction*) and **In-Process Check** (*Yes* or *No*). QA approves it (**Approve**, meaning *Approved*, with a reason and the password) once the work instruction is in effect, the in-process check is stated, and its own workstation, if it names one, and each of its sub-operations are approved. Its sub-operations are part of what QA approved: adding or changing one goes through change control.
- **Workstation.** Approved once every piece of equipment at it (module 8, an asset naming the workstation) is approved.
- **Routing.** Approved once each of its operations, and the workstation of each, is approved.
- **Use.** A bill of materials is submitted only if its routing, and with operations its operations and workstations, are approved. A work order (Production Plan's too, made without validation) and a job card are made only on what is approved **and still usable**: each operation and its sub-operations approved, its work instruction still in effect; each workstation approved, not disabled and with all its equipment approved; the routing approved and not disabled. The check runs before ERPNext's validation and again after it, since ERPNext adds a work order's operations and a job card's sub-operations and workstation there.
- **After approval** what QA approved changes only under an approved change request (URS-MST-001). An operation held before this module, not yet approved, gets its work instruction and in-process check once without a change request (its first issue).
- **The in-process check** says whether the operation is inspected in process; the parameters and limits are the item's in-process specification (module 6). Holding the job card to it is module 20.

## 3. Requirements and acceptance criteria

> **URS-MST-012** Each operation, workstation and routing is approved by QA with an electronic signature before it is used, and that signature is the only way it becomes approved: an operation only when it names its work instruction, an effective controlled document of type Work Instruction, and states whether it carries an in-process check; a workstation only when every piece of equipment at it is approved; a routing only when each of its operations and their workstations is approved; a bill of materials with an unapproved routing, operation or workstation is not submitted, and no work order or job card is made on an unapproved operation or workstation; once approved, what QA approved changes only under an approved change request.

| # | Criterion | Verification (`master_data/test_routing.py`) |
| --- | --- | --- |
| C1 | QA approves an operation with a signature | `test_qa_approves_an_operation_with_a_signature` |
| C2 | Work instruction: present, of type Work Instruction, in effect; in-process check stated; own workstation approved | `TestOperation.*` |
| C3 | Only QA, password, reason, once; approval only by signature | `test_only_qa_approves_with_password_and_reason_and_once`, `test_the_approval_is_given_only_by_the_signature` |
| C4 | Change control after approval; first issue of the new fields | `test_what_qa_approved_changes_only_under_change_control`, `test_an_operation_held_before_module_3_gets_its_new_fields_once` |
| C5 | Signature binds what QA approved | `test_the_signature_binds_what_qa_approved` |
| C6 | Workstation needs its equipment approved; routing its operations and workstations | `TestWorkstationAndRouting.*` |
| C7 | BOM, work order (also without validation) and job card refused on unapproved masters | `TestUse.*` |
| C8 | On screen: QA approves an operation on its form | `cypress/integration/routing.js` |
| C9 | Pilot and journey issue their work instructions through document control and approve their masters by signature | `pilot/test_demo.py`, `pilot/test_standard_flow.py`, `qa/test_ui_journey.py` |

## 4. Step-by-step browser testing guide

### RT-01 An operation and its work instruction
Role: Production Supervisor. **Manufacturing › Operation › + Add**: name, Work Instruction (an effective *Work Instruction*), In-Process Check. Save. Expected: orange banner *Not yet approved by QA… Ready for QA's approval* (or what is still needed).

### RT-02 QA approves
Role: QA Manager. Open the operation, **Approve**: empty reason or wrong password → refused; correct → green *Approved by QA*.

### RT-03 What approval needs
A work instruction still in draft → *its work instruction in effect*; an SOP instead → *a work instruction as its document*; no in-process answer → listed.

### RT-04 Workstation and routing
A workstation whose machine (equipment) is not approved → *its equipment … approved*. A routing with an unapproved operation → listed.

### RT-05 Use
Submit a bill of materials whose routing is not approved → refused *made only on what QA has approved*. A work order or job card on an unapproved operation → refused.

## 5. End-to-end business workflow
Work instruction authored, reviewed and approved (document control) → Production records the operation with it, the workstation and the routing → equipment at the workstation approved (module 8) → QA approves workstation, operation, routing → QA approves the bill of materials (module 2) → work orders and job cards run on approved masters → a change goes through change control.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Shared approval | `master_data/master_approval.py`: fields, `approve`, `guard_approval`, `unapproved`, `signed_content` (binds what versioning controls) |
| Service | `master_data/routing.py`: `operation_missing`, `workstation_missing`, `routing_missing`, `approve`, `guard`, `guard_bom`, `guard_work_order`, `guard_job_card` |
| Fields | Operation: `mds_work_instruction`, `mds_in_process_check`; Operation, Workstation, Routing: `mds_master_approval`, `mds_master_approved_by`, `mds_master_approved_at` |
| Version control | `versioning.MASTERS`: the new fields controlled; in use once approved; `FIRST_ISSUE_FIELDS` |
| Loaders | `pilot/documents.py`: work instructions through document control, signed approvals |
| Test fixture | `setup/test_stock.py` `approve_masters_for_test`, `work_instruction_for_test` |

## 7. Defects, edge cases and risks
- Independent review findings, fixed with tests before release: sub-operations were neither approved nor controlled, and ERPNext put them on job cards unchecked (High); a work instruction withdrawn later, equipment placed unapproved at an approved workstation, and a disabled workstation or routing were not refused at the point of use; ERPNext's own additions in its validate (a work order's operations again, a job card's sub-operations) came after the check (Medium); a bill naming an unapproved routing without operations, the demo loader on a revision left part-way (Low).
- A work instruction withdrawn later leaves the operation approved but unusable until it names an effective one under a change request; the operation's form cannot be saved until then (it keeps what its approval needed). The signature names the work instruction, not its revision: a new revision of it takes effect through document control's own change control (module 30), without a change request on the operation.
- Holding a job card to its operation's in-process check is module 20; recording machine, operator and times per operation and the training gate are modules 19 and 37.
- Warehouses are approved the same way in module 7 (next).

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Signed approvals and what they need | Verified (automated) |
| Use refused on unapproved masters | Verified (automated) |
| Change control after approval | Verified (automated) |
| On screen: approve an operation | Verified (browser test) |
| Manual RT-01…RT-05 | **Not tested** |
