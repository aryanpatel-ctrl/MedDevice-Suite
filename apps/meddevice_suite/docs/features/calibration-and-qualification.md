# Calibration, maintenance and equipment qualification (modules 38 and 40) — feature document and browser test guide

Requirements: **URS-EQP-001 to URS-EQP-013**, and URS-MST-011 (history card) updated in place (`meddevice_suite/qa/requirements.toml`); URS-EQP-001, 003, 004, 005, 008, 010 and 012 updated in place for the correction rule (decided 2026-10-12, plan D-29); URS-EQP-003 updated in place again for the job cards of each workstation the instrument was at (foundation review 2026-10-12). Builds on the equipment master (module 8), routing (module 3), e-signature (49), evidence files (54), reminders (60).
Code: `meddevice_suite/qms/calibration.py`, `qms/preventive_maintenance.py`, `qms/qualification.py`, `qms/equipment_care.py` (fitness, and the correction: `correct`, `only_by_correction`, `being_corrected`); records *Calibration Record* (with *Calibration Point*, *Calibration Standard*, *Calibration Impact*), *Maintenance Record* (*Maintenance Check*), *Equipment Qualification* (*Equipment Qualification Test*); report *Equipment Care Schedule*; alert *Calibration Out of Tolerance*; `master_data/equipment.py` (`usable`, requalification interval); history card; hooks in `meddevice_suite/hooks.py`.
DFD trace: 9D Equipment Management (D3 maintenance schedule, D4 maintenance execution, D5 performance tracking, D6 calibration / validation); flowchart *Calibration & Maintenance* and *Equipment Qualification (IQ/OQ/PQ)*; production stage 3.

## 1. Feature overview

**Purpose.** Equipment is used only while it is calibrated, maintained and qualified; each calibration, maintenance and qualification is a signed record; and an instrument found out of tolerance makes QA look at what it measured.

**Why it matters.** ISO 13485 7.6 asks that measuring equipment is calibrated at intervals, traceable to standards, protected from invalid use, and that when it is found not to conform the validity of earlier results is assessed and recorded; 6.3 and 7.5.6 ask for maintenance and qualification. An overdue gauge that keeps measuring makes every result since doubtful.

| Job profile | Here |
| --- | --- |
| Maintenance | Records and completes calibrations and maintenance (signed); executes qualifications |
| Production Supervisor | Executes qualifications |
| QA Manager | Completes calibrations and maintenance too; approves qualifications (never one it executed); assesses an out-of-tolerance impact (signed); corrects a completed record by its signed **Correct** (plan D-29) |
| QC, Management | Read |

## 2. Detailed functional explanation

- **Calibration Record:** the equipment, the date, in-house (an effective SOP or work instruction, and reference standards) or by an external laboratory (laboratory, certificate number, certificate file); per point the nominal value, limits, the value found and the value left. The system marks each point in or out of tolerance, the **result** on the values left (the instrument as it goes back into use) and, for a pass, the **next due date** from the interval QA approved on the equipment. **Complete** is the performer's signature (meaning *Verified*). It is never deleted and does not change afterwards (except QA's signed assessment); it is corrected only by QA's signed **Correct** (below).
- **Reference standards:** each is approved equipment, not the instrument itself, and was itself in calibration on the day; its due date is recorded with the calibration.
- **Out of tolerance:** a value found outside its limits flags the record, lists the inspections that named the instrument and the job cards at each workstation it was at since its last good calibration, each workstation only for the days the instrument was there and each job card row naming its workstation, alerts QA, and waits for QA's signed **Assess: No Product Impact** or **Assess: Product Action Needed**, with the assessment as the reason. The performer's signature still verifies afterwards.
- **Maintenance Record:** its checks (OK or not), what was done, downtime hours; satisfactory only when every check is OK; only a satisfactory record sets the next due date. Completed by signature.
- **Equipment Qualification:** IQ, OQ or PQ against an effective SOP or form, with its tests; passes only when every test passes. The performer **Executes** it (signed); a change afterwards needs execution again; QA, never the performer, **Approves** it (signed, completing it) or **Returns** it with a signed reason. An OQ needs an approved passed IQ; a PQ an approved passed OQ done on or after it.
- **Correction** (decided 2026-10-12, plan D-29): a completed calibration, maintenance record or approved qualification is corrected only by QA's signed **Correct** (meaning *Rejected*: the record as completed no longer stands), with a reason. The act cancels the record under that signature, with the reason in the audit trail, and opens its amendment as a new draft (`amended_from` set), which the form opens. The amendment is completed again by its own signed act: *Complete* for a calibration or maintenance, *Execute* and *Approve* for a qualification. Frappe's own **Cancel** and the API are refused on these three records, whatever the permissions, and no role holds Frappe's cancel any more; Frappe's **Amend** of a cancelled record still works, and the draft it makes carries nothing the signed acts set (completion, due date, execution, approval, impact assessment), so it is completed and assessed afresh. Why: before, QA could cancel a failed calibration with only a reason, the pass before it became the latest and the instrument counted as fit again.
- **While a correction is pending** the equipment is not fit (*its calibration CAL-… is being corrected*; likewise *its preventive maintenance …*, *its qualification …*), from the moment the record is cancelled until its amendment is completed or approved by its signed act, whatever the records before it say. A draft amendment, or one discarded unfinished, does not end it.
- **Where the instrument was** (foundation review 2026-10-12): read from the audit trail, which records every change of the asset's *Workstation* with its time and old and new values and is tamper-evident (`calibration.workstation_history`, one query per instrument however often it moved). On the first day of the period it was where the first change since then moved it from, or where it is now if nothing changed. The day of a move counts at both workstations: the trail knows the time, a job card only its day, and a day in doubt is listed rather than missed. Why: the list used to read the workstation as it is now, so an instrument moved in the period missed the job cards of the workstation it left and took in its new workstation's earlier ones. ERPNext's Version is not used, being neither tamper-evident nor the regulated history here.
- **Now, rightly:** the checks at the point of use (`equipment_care.equipment_at` for job cards and work orders, `routing.not_usable`), QA's approval of a workstation (`routing.workstation_missing`) and the *Equipment Care Schedule* read where equipment is now, because they decide what may be used, or show what stands, today. The history card already lists each workstation change from the audit trail. Qualification after a move (URS-EQP-013) reads ERPNext's asset movements, not the workstation.
- **An open impact assessment survives a correction:** a calibration found out of tolerance and withdrawn for correction keeps its open assessment on the cancelled record, listed in the *Calibration Impact* reminders, until QA signs **Assess: No Product Impact** or **Assess: Product Action Needed** on that record. It is not carried to the amendment, which is the more auditable choice: the finding, its list of inspections and job cards and QA's signature stay on the record that found them, a correction can neither erase the finding (by entering different values found) nor restart the reminder's overdue and escalation days (a new reminder for a new record would), and the cancelled record's certificate stays evidence. The amendment, if it too finds a value out of tolerance, has an assessment of its own.
- **Fitness** (`equipment_care.unfit`): needing calibration (or maintenance), equipment is fit while its latest completed record passed and its next due date has not passed; never done, it is not fit. Needing qualification, while its latest approved IQ, OQ and PQ each passed, in order, and the PQ is within the requalification interval (a new field on the equipment, 0 = does not expire). Under an open repair, or while a record is being corrected, it is not fit.
- **After a repair or a move** (URS-EQP-013; decided 2026-10-11, plan decision D-11): a completed ERPNext *Asset Repair* makes equipment that needs calibration unfit until it is calibrated on or after the day the repair was completed, and equipment that needs qualification unfit until a PQ is done on or after it. A submitted *Asset Movement* that transfers it (purpose Transfer, or Transfer and Issue) makes equipment that needs qualification unfit until it is qualified again from the IQ on or after the day it moved. A move alone does not need a calibration: an instrument is carried by hand, and its interval covers it. A receipt (its first placing) and an issue to a person are not moves. The qualification's occasion *After relocation or repair* records why it was done.
- **Where it is enforced (block, never warn):** an inspection reading names only fit instruments; a job card is refused at every save (validated or not), submit and change after submit, and when time is logged on it, while any equipment at its workstation is not fit; a work order at submit likewise.
- **ERPNext's time log:** ERPNext's `job_card.make_time_log` writes the time log row straight to the database, past every document hook and without a permission check. It is overridden (`override_whitelisted_methods`, engineering rules 3.3) to check write permission and run the job card's rules (routing and equipment) before calling ERPNext's own. Without it a time log could be added on unapproved or unfit equipment; the routing guard had the same gap.
- **ERPNext's Asset Maintenance is not used** and refused on save (URS-EQP-009): its next due date is cleared for a task with an end date after one completion, it rewrites completed logs, allows changes after completion, has no as-found or as-left values and is not signed. This departs from the module scope note, which planned to customise it.
- **Schedule and reminders:** *Equipment Care Schedule* shows each approved piece of equipment's next calibration, maintenance and requalification, as in date, due soon, overdue, never done or not needed, and in *Not Fit Because* every reason it may not be used now (an open repair, a repair or move since it was last calibrated or qualified). Reminders *Calibration Due*, *Maintenance Due*, *Requalification Due* (lead times in Quality Settings: 15, 7, 30 days) and *Calibration Impact* complete only when a record shows the work done.
- **History card:** now lists calibrations, maintenance and qualifications as signed, the downtime of repairs and maintenance, and the number found out of tolerance.

## 3. Requirements and acceptance criteria

The texts are in `requirements.toml` (URS-EQP-001 to 012, URS-MST-011).

| # | Requirement | Verification (`qms/test_equipment_care.py`) |
| --- | --- | --- |
| C1 | EQP-001 calibration record, result, next due, signature, never changed or deleted, certificate evidence | `TestCalibration.*` |
| C2 | EQP-002 reference standards | `TestReferenceStandards.*` |
| C3 | EQP-003 out of tolerance: impact list, QA alert, signed assessment, completion signature intact; job cards of each workstation the instrument was at, only while there (moved once, twice, just before or after the period, not moved), each row naming its workstation, bound by the completion signature, read in a fixed number of queries; a record signed before rows named a workstation still verifies (CMP-004) | `TestOutOfTolerance.*`, `TestImpactFollowsTheInstrument.*` |
| C4 | EQP-004 maintenance | `TestMaintenance.*` |
| C5 | EQP-005 fitness | `TestFitness.*` |
| C6 | EQP-006 refused at inspection, job card (save, unvalidated save, time log) | `TestRefusedAtTheInspection.*`, `TestRefusedAtTheWorkstation.*` |
| C7 | EQP-007 schedule; EQP-009 Asset Maintenance refused; MST-011 history card | `TestScheduleAndRecords.*` |
| C8 | EQP-008 reminders | `TestReminders.*` |
| C9 | EQP-010 to 012 qualification, order, requalification; old approvals still verify | `TestQualification.*` |
| C10 | EQP-013 after a repair: recalibration and a PQ; after a move: IQ, OQ, PQ again; a receipt or issue is no move; the schedule says why | `TestAfterRepairOrMove.*`, `TestQualification.test_after_*`, `TestQualification.test_a_receipt_or_an_issue_is_not_a_move` |
| C11 | EQP-001, 003, 004, 005, 008, 010, 012 correction (D-29): Frappe's cancel refused on all three; QA's signed Correct cancels and opens the amendment; not fit until the amendment is completed or approved, fit when it passes; the open impact assessment stays listed and its reminder open until QA signs it; only QA, always with a reason; Frappe's Amend carries nothing the signed acts set | `TestCorrection.*` |
| C12 | Every submittable record type of the app (found, not listed): Frappe's own submit, cancel, change after submit and delete refused unless its signed act runs, and no role holds those permissions; the one exception, Training Matrix cancel, is listed with its reason | `compliance/test_regulated_records.py` (`TestRegulatedRecordContract.*`) |

## 4. Step-by-step browser testing guide

### CQ-01 Calibrate
As Maintenance: **Assets › Calibration Record › New** for the demo caliper, external laboratory, certificate, one point in tolerance; **Complete** and sign. Expected: Pass, next due in 365 days; the caliper's history card lists it.

### CQ-02 Out of tolerance
A point found outside its limits, left inside: Pass, flagged; QA has *Out of tolerance: …*; the record lists the inspections since the last good calibration. On a test site, first change the instrument's *Workstation* under an approved change request: a job card at the old workstation from before the change and one at the new workstation from after it are listed, each row naming its workstation; one at the new workstation from before it is not. As QA: **Assess: No Product Impact** with the reasoning.

### CQ-03 Overdue blocks use
Let a calibration fall overdue (or set a past next due on a test site): an inspection naming the instrument is refused; a job card at a workstation whose machine is overdue for maintenance cannot be saved or have time logged.

### CQ-04 Qualify
For equipment needing qualification: IQ, then OQ, then PQ, each executed (Maintenance) and approved (QA). Before the PQ is approved, the machine's job cards are refused.

### CQ-05 After a repair or a move
Complete an *Asset Repair* on the calibrated demo caliper. Expected: *Equipment Care Schedule* says it has not been calibrated since the repair, and an inspection naming it is refused. Calibrate it again (CQ-01): it is fit. On a qualified machine, transfer it by an *Asset Movement*: its job cards are refused until IQ, OQ and PQ are approved again.

### CQ-06 Correct a completed record
As QA, open a completed calibration (CQ-01). Expected: no **Cancel** button; **Correct** asks for a reason and your password. Sign it with the reason. Expected: the record is cancelled with your *Rejected* signature, its amendment opens as a draft, and *Equipment Care Schedule* shows the caliper not fit, *its calibration CAL-… is being corrected*; an inspection naming it is refused. As Maintenance, correct the amendment and **Complete** it: the caliper is fit again if it passed. For a calibration found out of tolerance (CQ-02), correct it before assessing: the cancelled record still shows **Assess Impact**, *Calibration Impact* is still open in the reminders, and it completes only once QA signs the assessment on the cancelled record. Repeat **Correct** on a maintenance record and on an approved PQ (the machine is not fit until the amendment is completed, or executed and approved). As Maintenance, there is no **Correct** button.

## 5. End-to-end business workflow
Equipment approved (module 8) → calibrated, maintained, qualified by signed records → used while fit; reminders before due; overdue → blocked at inspection and production → an out-of-tolerance finding → QA assesses what it measured → product action through the NCR (module 33, later).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Impact list | `calibration.impact_since`, `calibration.workstation_history` (audit trail) |
| Fitness | `equipment_care.unfit`, `repaired_and_moved`, `latest`, `guard_job_card`, `guard_work_order`, `make_time_log` (override) |
| Signed acts | Calibration Record: Complete, Assess: No Product Impact, Assess: Product Action Needed, Correct; Maintenance Record: Complete, Correct; Equipment Qualification: Execute, Approve, Return, Correct (Correct: `equipment_care.correct`, meaning *Rejected*, returns the amendment's name, which `signing.sign_action` and the signing dialog pass on to the form) |
| Correction | `equipment_care.only_by_correction` (before_cancel of the three records), `being_corrected` (read by `unfit`); permissions: Quality Manager keeps *amend*, has no *cancel*; the cancelled calibration's certificate stays evidence (`compliance/evidence_files.py`) |
| Fields | Asset `mds_requalification_interval_days` (controlled; in the signed content only when set, so earlier approvals verify); *Calibration Impact* `workstation` (a job card's; bound by the calibration's signatures only when set, `esign.BOUND_WHEN_SET`, so records signed before it verify) |
| Settings | `calibration_warning_days` 15, `maintenance_warning_days` 7, `requalification_warning_days` 30 |
| Test fixture | `setup/test_stock.care_in_date_for_test` (from `approve_equipment_for_test`); the pilot and browser journey calibrate the demo caliper by the Maintenance job's signed act |

## 7. Defects, edge cases and risks
- ERPNext's daily asset status update looks pending repairs up by the asset's display name, not its id, and can clear *Out of Order*; fitness reads open repairs itself.
- A work order without operations names no workstation, so no equipment check applies to it.
- A machine going overdue in the middle of a job stops its time logging (intended); reminders give notice.
- Measurements made outside inspection readings are not in the impact list.
- A workstation change is read from the audit trail, which started with the app: a change made before it was installed is not known, and the instrument counts as at its present workstation back to the start of the period. A job card is placed by its posting date, not its time logs.
- Decided 2026-10-11 in place of the client's answers (plan section 16, D-11 and D-12): the due date runs from the last calibration; in-house calibration is done by Maintenance, or by an ISO/IEC 17025 laboratory; a repair needs recalibration and a PQ, a move a new IQ, OQ and PQ; lead times 15, 7 and 30 days.
- A repair or move is read from ERPNext's own records by day: a calibration earlier on the day of the repair counts as after it.
- An asset movement that only changes the custodian, with purpose Transfer, counts as a move.
- Correction (plan D-29): the amendment is checked as any draft when the act opens it, so a calibration is corrected only while no later calibration of the instrument is completed (calibrations are recorded in order), and a record of retired or no longer approved equipment cannot be corrected; the act is then refused and nothing changes. A later calibration supersedes an older one instead.
- A record cancelled through Frappe's own Cancel before this rule (it carries its completion signature and has no completed amendment) also counts as being corrected, so the equipment stays not fit until the record is amended and completed.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Signed calibration, maintenance, qualification | Verified (automated) |
| Out-of-tolerance impact and QA assessment | Verified (automated) |
| Impact list follows the instrument's workstation history | Written (automated); not yet run |
| Blocked at inspection, job card, time log, work order | Verified (automated) |
| Correction by QA's signed act; Frappe's cancel refused; not fit while pending; impact assessment kept | Written (automated); not yet run |
| Regulated record contract over every submittable record type | Written (automated); not yet run |
| Manual CQ-01…CQ-06 | **Not tested** |
