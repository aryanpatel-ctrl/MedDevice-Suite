# Equipment master and history card (modules 8 and 62) — feature document and browser test guide

Requirements: **URS-MST-010** (equipment master) and **URS-MST-011** (history card) (`meddevice_suite/qa/requirements.toml`). Builds on URS-MST-001 (version control of masters) and the decision of 10 October 2026 (an Equipment item behind each asset).
Code: `meddevice_suite/master_data/equipment.py`, report `master_data/report/equipment_history_card`, hooks in `meddevice_suite/hooks.py` (Asset `validate`, `onload`; Quality Inspection `validate`), signed act in `compliance/signing.py`, signature content in `compliance/esign.py`, version control in `master_data/versioning.py`, desk in `public/js/master_data.js`, loaders `pilot/inspection.py`, `pilot/demo.py`, `qa/ui_journey.py`.
DFD trace: D10 Equipment Master; 9D Equipment Management (D1 equipment master, D2 history card, D5 performance tracking).

## 1. Feature overview

**Purpose.** Every machine and instrument that makes, measures, tests or sterilizes product is known: what it is, where it is, whether it affects quality, and how it is looked after. QA approves it before it is used, and its history card shows everything that has happened to it.

**Why it matters.** ISO 13485 6.3 and 7.6, and 21 CFR 820.70(g) and 820.72, ask that equipment be suitable, identified, maintained and calibrated, with records. A measurement taken with an unknown or unapproved instrument cannot be trusted.

| Job profile | Here |
| --- | --- |
| QA Manager | Records equipment, approves it with a signature, sets it out of service or retired, reads its history card |
| Maintenance, Production, Stores management | Read the equipment and its history card |
| QC Inspector | Names an approved instrument on a reading |

## 2. Detailed functional explanation

- **The record** is ERPNext's Asset, with an Equipment section: **Equipment Tag** (the plant's label, unique among equipment that is not cancelled; an amendment keeps it), **Equipment Class** (production machine, measuring instrument, test equipment, sterilization equipment, utility), **Manufacturer**, **Model**, **Manufacturer's Serial Number**, **Workstation**, **Affects Product Quality**, **Equipment Status** (in service, out of service, retired), and **Needs Calibration / Preventive Maintenance / Qualification** with the intervals in days.
- **Approval.** QA clicks **Approve Equipment** (meaning *Approved*), with a reason and the password. It needs the tag, class, manufacturer, model, serial number, where it is used (location or workstation) and an interval for each kind of care it needs; a measuring instrument always needs calibration. Approval fields are set only by the signature; a new or copied asset is in draft.
- **Independent of the asset's submit.** ERPNext's asset submit is its accounting (accounting stays in Tally). Equipment bought before the system is approved as it is.
- **Use on inspections.** A reading's instrument must be approved and in service; draft, out-of-service, retired or cancelled equipment is refused, and so is equipment ERPNext marks sold, scrapped, in maintenance, out of order or capitalized.
- **Change control.** Once approved, the tag, class, make, model, serial number, workstation, quality impact and care intervals change only under an approved change request (URS-MST-001), on a submitted asset too. Where the equipment stands is not among them: ERPNext's asset movements change it, and the movement is its record (on the history card). The status changes from day to day and is kept in the audit trail; *Retired* is final.
- **The approval fields** are set only by the signature, on a draft and on a submitted asset alike.
- **A copy is another piece of equipment**: the desk's Duplicate leaves out the tag, the serial number and the approval.
- **History card** (**Quality › Equipment History Card** or **Assets › Equipment History Card**): for one piece of equipment, over its whole life unless a period is chosen, newest first: when it was recorded, QA's signatures, every change (with the reason from the audit trail), movements, maintenance and calibration logs that were done or are overdue (planned ones are the schedule, module 38), repairs with their downtime, and the inspections that used it as their instrument. The summary gives the number of repairs, the total downtime in hours and the number of inspections. Reading it needs read access to the equipment.

## 3. Requirements and acceptance criteria

> **URS-MST-010** Each piece of equipment is an asset recording its unique equipment tag, its class (production machine, measuring instrument, test equipment, sterilization equipment or utility), manufacturer, model and serial number, where it is used, whether it affects product quality, and whether it needs calibration, preventive maintenance and qualification with their intervals, a measuring instrument always needing calibration; QA approves it with an electronic signature only when these are complete, and that signature is the only way it becomes approved; an inspection reading names as its instrument only approved equipment that is in service; once approved, what QA approved changes only under an approved change request.

> **URS-MST-011** Each piece of equipment has a history card that lists, by date, its approval and every change to its record, its movements, its maintenance and calibration logs, its repairs with their downtime, and the inspections that used it as their instrument, with the number of repairs and the total downtime over the period shown.

| # | Criterion | Verification (`master_data/test_equipment.py`) |
| --- | --- | --- |
| C1 | Draft until QA signs; signature recorded and intact | `test_new_equipment_is_a_draft_until_qa_approves_it_with_a_signature` |
| C2 | Only QA; wrong password / no reason; not twice | `test_only_qa_approves`, `test_a_wrong_password_or_no_reason_approves_nothing_and_twice_is_refused` |
| C3 | Approval only by signature, also on insert | `test_the_approval_is_given_only_by_the_signature` |
| C4 | What approval needs, each item | `test_what_approval_needs`; retired: `test_a_retired_piece_of_equipment_is_not_approved` |
| C5 | Unique tag; a copy is new equipment in draft | `TestTheRecord.*` |
| C6 | Approved keeps what approval needed; changes under control; status free | `TestTheRecord.*` |
| C7 | Signature binds the equipment, not its accounting or status | `test_the_signature_binds_the_equipment_not_its_accounting` |
| C8 | Reading's instrument approved and in service, through a real inspection | `test_a_reading_names_only_approved_equipment_in_service` |
| C9 | History card: each kind of event, newest first, summary, period, permission | `TestHistoryCard.*` |
| C10 | On screen: approve with a signature; the card shows it | `cypress/integration/equipment.js` |

## 4. Step-by-step browser testing guide

### EQ-01 Record equipment
Role: QA Manager. **Assets › Asset › + Add**: item *INST-CALIPER*, location, then the Equipment section. Save. Expected: orange banner *Not yet approved by QA… Still needed: …* until everything is filled; then *Ready for QA's approval*.

### EQ-02 Approve
Click **Approve Equipment**; empty reason → refused; wrong password → refused. Correct → green banner *Equipment approved by QA*.

### EQ-03 Use on an inspection
Role: QC Inspector. An incoming inspection reading naming a draft instrument → refused *QA has not approved it*. Set an approved instrument out of service (QA) → refused *it is out of service*.

### EQ-04 Change control
Role: QA Manager. Change the model of approved equipment and save → refused *is in use*, until an approved change request listing it is named.

### EQ-05 History card
Open **Equipment History Card** for the instrument: *Recorded*, *Signed: Approved*, each change with its reason, and the inspections it was used on; the summary shows repairs and downtime.

## 5. End-to-end business workflow
Equipment bought → QA records it with its details and care intervals → QA approves it (signed) → it is used on inspections and production → calibration and maintenance are logged (module 38) → repairs are recorded with their downtime → any change to what was approved goes through change control → the history card shows its whole life → when it is taken out of use, QA retires it.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `master_data/equipment.py`: `missing_for_approval`, `approve`, `signed_content`, `guard_asset`, `usable`, `guard_inspection_instruments`, `asset_onload` |
| Fields | on Asset: `mds_equipment_tag` (unique, not copied), `mds_equipment_class`, `mds_manufacturer`, `mds_model`, `mds_serial_number` (not copied), `mds_workstation`, `mds_affects_quality`, `mds_equipment_status`, `mds_needs_calibration`, `mds_calibration_interval_days`, `mds_needs_maintenance`, `mds_maintenance_interval_days`, `mds_needs_qualification`, approval fields |
| Version control | `versioning.MASTERS["Asset"]`: the controlled fields, in use once approved or submitted |
| Report | `master_data/report/equipment_history_card`: signatures, audit events, movements, maintenance logs, repairs, inspections |
| Test fixture | `setup/test_stock.py` `approve_equipment_for_test` |

## 7. Defects, edge cases and risks
- Independent review findings, fixed with tests before release: on a submitted asset the approval could be set without a signature, since Frappe runs no validate on a change after submit (High); ERPNext's own asset status (scrapped, out of order) did not keep an instrument off inspections (Medium); an asset movement changed the location, a controlled and signed field, past change control and made the signature read as tampered (Medium: the location is no longer controlled or signed); what QA approved could not be changed at all on a submitted asset (Medium: the fields may now change after submit, under a change request); an amendment or split failed on the unique tag, retired equipment could be put back into use, the history card's order within a day, planned logs, its default period and its roles (Low).
- **Open (client decision):** QA holds the submit permission on Asset, but submitting an asset makes ERPNext create an asset movement, which QA may not create (only Stock Manager, Accounts Manager and System Manager may). Equipment is approved without being submitted, so this module does not need it; who books assets is to be decided with the accounting in Tally.
- An asset split into several keeps its tag on the first; the others need their own tag before they are saved.
- Anyone who may read a piece of equipment reads its history card, including the reasons recorded in its audit trail and signatures.
- Calibration and maintenance due dates, and refusing equipment past them on a job card or an inspection, are module 38; qualification records (IQ, OQ, PQ) and refusing unqualified equipment are module 40. This module records whether and how often each is needed.
- Equipment on a job card is known through its workstation; refusing an unapproved machine on a job card belongs with production's gates (module 19).
- Who may set equipment out of service from the floor (Maintenance) is for module 38; today QA does.

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Signed approval and what it needs | Verified (automated) |
| Instrument use on inspections | Verified (automated) |
| Change control of what was approved | Verified (automated) |
| History card | Verified (automated) |
| On screen: approve; the card | Verified (browser test) |
| Manual EQ-01…EQ-05 | **Not tested** |
