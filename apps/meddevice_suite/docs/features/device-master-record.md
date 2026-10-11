# Device master record / medical device file (module 31) — feature document and browser test guide

Requirements: **URS-QMS-026 to URS-QMS-031** (`meddevice_suite/qa/requirements.toml`). Builds on the item master (1), bill of materials (2), routings (3), specifications (6), document control (30), change control (36), risk management (39), e-signature (49), reminders (60) and the shared pinned records (`qms/pinned_records.py`).
Code: `meddevice_suite/qms/device_master_record.py`; record *Device Master Record* (with *Device Master Record Heading*, *Device Master Record Document*, *Device Master Record Element*); print format *Device Master Record*; alert *Device Master Record Out of Date*; reminder category *DMR Re-approval*; work order guard in `hooks.py`; device lifetime on the item (`master_data/item_master.py`).
DFD trace: master plan; flowchart DMR structure (D2 "All masters are version controlled and linked with Document Control"). Decisions: plan D-15 (contents), D-16 (device lifetime), Q7 (no further mandatory documents) and Q8 (production blocked without a Current record), 2026-10-11.

## 1. Feature overview

**Purpose.** Each finished good has one device master record in force: the index of everything the device is made, checked, labelled and released by, each record pinned at the version QA approved. When any of those records moves on, the index says so at once, and production stops until QA approves a new revision.

**Why it matters.** ISO 13485 4.2.3 requires a medical device file for each device type; 21 CFR 820.181 requires a device master record; CDSCO MDR 2017 requires a Device Master File for the licence. A DMR that silently lags behind a changed specification or label means the plant makes a device that is not the one approved.

| Job profile | Here |
| --- | --- |
| QA Manager | Drafts and authors (signed); approves another person's revision (signed); returns or withdraws (signed, with a reason); alerted when a record goes out of date |
| Production Supervisor | Drafts a revision and signs it as author; starts the next revision; reads |
| QC Inspector, Management | Read |
| Others | No access |

## 2. Detailed functional explanation

- **Headings (plan D-15):** general description and intended use; labelling and instructions for use; product specifications; manufacture with packaging, storage, handling and distribution; measuring and monitoring; installation and servicing; risk management. Each heading is written, or ticked *Not Applicable* with the reason (installation and servicing, for these single-use devices).
- **Elements, filled by the system** each time the draft is saved, and again when it is approved (`assemble`):
  - the item itself, with its device class, sterility, UDI and **device lifetime** (5 years unless QA states another, plan D-16);
  - the default bill of materials (approved, active, default);
  - the bill's routing, each operation, and each operation's work instruction;
  - the final specification, and the in-process specification when any operation of the routing carries an in-process check (module 3 says the check's limits are the in-process specification);
  - the risk management report in force of the item's risk management file, when the item is in one (module 39);
  - the effective controlled documents the author chooses, at least one of them a Packaging and Labelling document (plan Q7: nothing further is mandatory).

  Each element carries its version (a document's revision, a master's version number, a bill's state, a report's approval) and the change request that version was made under. Nobody types a version: anything typed is overwritten.
- **Signed acts:**
  - **Author** (QA or Production Supervisor, the one who drafted it): refused while an element is missing, a heading is empty, the item is not approved, or a pinned record is not in force.
  - **Approve** (QA, not the author): the record is assembled again from the live records and refused if anything is missing or out of force, if a record moved on since the author signed (return it, and the author signs again), or if another revision is in progress. The revision becomes **Current** and the one before it **Superseded**, in one transaction.
  - **Return** and **Withdraw** (QA), each with a reason. Withdraw is for an abandoned draft or a discontinued product.
- **Never deleted or cancelled.** Frappe's own Submit is refused; after approval only the acts and the system's out-of-date process change the record. One revision is in progress, and one is Current, per item.
- **Out of date (URS-QMS-029):** a new revision of a pinned document, a new version of a pinned master or specification, a document withdrawn, a bill switched off or replaced as the default, or a risk report superseded makes the Current record **Out of Date** in the same transaction. Each changed element shows *What Changed*, its version now and the change request. A daily sweep catches changes written straight to the database. QA is alerted once, and a *DMR Re-approval* reminder is open until a new revision is approved (due in *DMR Re-approval Due* days, 14 by default, Quality Settings). The signatures bind the item, headings and pinned elements, not the status, so they stay intact.
- **Start New Revision** copies the headings and documents of the last approved revision and, from an out-of-date one, says what changed in *What Changed in This Revision*.
- **Print (URS-QMS-030):** only a Current record prints as *CONTROLLED INDEX* with its signatures; any other prints *NOT FOR USE* with its status.
- **Production (URS-QMS-031):** a work order for a finished good is submitted only if the item's record is Current, still matches the live records, and pins the bill of materials the order uses. A draft work order is allowed (planning). Sub-assemblies are not held to it.

## 3. Requirements and acceptance criteria

| # | Requirement | Verification |
| --- | --- | --- |
| D1 | QMS-026 headings and elements; risk report pinned; device lifetime | `TestAssembly.*` |
| D2 | QMS-027 pins from live records; missing or out-of-force elements and unapproved items refused | `TestAssembly.*`, `TestSignatures.test_approval_assembles_again`, `TestSignatures.test_an_element_out_of_force_blocks_approval`, `qms/test_pinned_records.py` |
| D3 | QMS-028 author, QA approval by another person, supersession, return and withdrawal with reasons, never deleted | `TestSignatures.*`, `TestPermissionsAndRegistration.*` |
| D4 | QMS-029 out of date in the same transaction and by the sweep; alert once; reminder until re-approved; signatures intact | `TestOutOfDate.*`, `TestSignatures.test_signature_excludes_status` |
| D5 | QMS-030 controlled index only when Current | `TestPrint.*` |
| D6 | QMS-031 work order needs the Current record pinning its bill | `TestWorkOrders.*`; pilot `pilot/test_standard_flow.py`, `pilot/test_demo.py` |
| UI | Author and QA sign on the form; spec revised → out of date → re-approved | `cypress/integration/device_master_record.js` |

## 4. Step-by-step browser testing guide

### DMR-01 Draft and sign (Production Supervisor)
1. **Manufacturing › Device Master Record › New**: choose an approved finished good.
2. Write each heading (tick *Not Applicable* for installation and servicing, with the reason). Add the Packaging and Labelling document under *Documents*. Save.
3. Expected: *Elements* list the item, bill of materials, routing, operations, work instructions and specifications, each with a version. **Sign as Author**.

### DMR-02 Approve (QA Manager)
Open the record and **Approve** with your password. Expected: status *Current*; the previous revision, if any, *Superseded*.

### DMR-03 Out of date
Revise the product's final specification under an approved change request (or issue a new revision of its labelling document). Expected: the record shows *Out of Date* with the changed element and its change request; QA has one alert and a *DMR Re-approval* reminder.

### DMR-04 Production blocked, then re-approved
Try to submit a work order for the product. Expected: refused. **Start New Revision**, sign as author, approve as QA, then submit the work order. Expected: accepted.

### DMR-05 Print
Print a Current record (*CONTROLLED INDEX* with signatures) and a superseded one (*NOT FOR USE*).

## 5. End-to-end business workflow
Item, bill, routing, work instructions, specifications and labelling approved → Production Supervisor drafts and signs the DMR → QA approves (Current) → production runs on it → any pinned record changes under change control → DMR Out of Date, QA alerted, production stops → new revision signed and approved → production resumes.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Signed acts | Author (Authored), Approve (Approved), Return (Rejected), Withdraw (Rejected) |
| Signed content | `device_master_record.signed_content` (`esign.SIGNED_CONTENT`): item, revision, headings, elements with pinned versions; not status, stale flags or versions now |
| Pinned records | owner *Device Master Record* in `pinned_records.PIN_OWNERS` (`element_pins`, `elements_changed`); *Risk Management Report* now pinnable; `records_changed` called by `risk_management.approve_report` and `take_effect` |
| System process | `mark_out_of_date`: saved as an update after submission with a system reason (audited), alert once |
| Work orders | `guard_work_order` on Work Order `before_validate` (when submitting) and `before_submit` |
| Setting | `dmr_reapproval_days` 14 (1 to 90) |
| Item | `mds_device_lifetime_years` (Int, default 5, 1 to retention − 2 years), controlled; in the item's signed content only when not the default |
| Pilot and journey | `pilot/device_master.py`; `qa/ui_journey.prepare_device_master_record`, `revise_dmr_specification`; test fixture `setup/test_stock.device_master_record_for_test` |

## 7. Defects, edge cases and risks
- Production is blocked at go-live for every finished good without a Current record (plan Q8). Each product's DMR must be approved before its first work order on the working site.
- One change can put many records out of date (a shared labelling document). Each record is marked once and alerted once.
- A record that goes out of date while a work order is already submitted does not stop that order's job cards and stock entries; only new work orders are blocked.
- The device lifetime's upper bound follows the 10-year retention (lifetime + 2 ≤ 10). A longer-lived device needs a longer retention first.
- The DMR is not itself a change-control affected record type: its revisions follow the changes of the records it pins, each made under its own change request, which the record shows.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Assembly, required elements, signed author and approval | **Not run yet** (automated tests written) |
| Out of date, alert, reminder, signatures intact | **Not run yet** (automated tests written) |
| Print controlled / not for use | **Not run yet** (automated tests written) |
| Work order block; pilot and journey on a Current record | **Not run yet** (automated tests written) |
| Cypress `device_master_record.js` | **Not run yet** |
| Manual DMR-01…DMR-05 | **Not tested** |
