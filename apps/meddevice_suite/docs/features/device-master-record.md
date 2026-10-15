# Device master record / medical device file (module 31) — feature document and browser test guide

Requirements: **URS-QMS-026 to URS-QMS-031** (`meddevice_suite/qa/requirements.toml`). Builds on the item master (1), bill of materials (2), routings (3), specifications (6), document control (30), change control (36), risk management (39), e-signature (49), reminders (60) and the shared pinned records (`qms/pinned_records.py`).
Code: `meddevice_suite/qms/device_master_record.py`; record *Device Master Record* (with *Device Master Record Heading*, *Device Master Record Document*, *Device Master Record Element*); print format *Device Master Record*; alert *Device Master Record Out of Date*; reminder category *DMR Re-approval*; work order guard in `hooks.py`; device lifetime on the item (`master_data/item_master.py`).
DFD trace: master plan; flowchart DMR structure (D2 "All masters are version controlled and linked with Document Control"). Decisions: plan D-15 (contents), D-16 (device lifetime), Q7 (no further mandatory documents) and Q8 (production blocked without a Current record), 2026-10-11; plan D-23 (every finished good in a risk management file whose report is in force before its record is approved), decided 2026-10-12.

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
  - the default bill of materials (approved, active, default) and, one level down, the bill of materials each of its sub-assembly lines names;
  - the bill's routing, each operation and each of its sub-operations, and the work instruction of each;
  - the final specification, and the in-process specification when any operation of the routing carries an in-process check (module 3 says the check's limits are the in-process specification);
  - **the risk management report in force of the item's risk management file (plan D-23, decided 2026-10-12): mandatory.** An item in no file, or in a file without a report in force, cannot be signed or approved, and the message says which;
  - the effective controlled documents the author chooses, at least one of them a Packaging and Labelling document (plan Q7: nothing further is mandatory).

  Each element carries its version (a document's revision, a master's version number, a bill's state, a report's approval) and the change request that version was made under. Nobody types a version: anything typed is overwritten.
- **Signed acts:**
  - **Author** (QA or Production Supervisor, the one who drafted it): refused while an element is missing, a heading is empty, the item is not approved, or a pinned record is not in force.
  - **Approve** (QA, not the author): the record is assembled again from the live records, read with row locks so that no pinned record changes until the approval is committed, and refused if anything is missing or out of force, if a record moved on since the author signed (its version, title, label or change request; return it, and the author signs again), or if another revision is in progress. The revision becomes **Current** and the one before it **Superseded**, in one transaction.
  - **Return** and **Withdraw** (QA), each with a reason. Withdraw is for an abandoned draft or a discontinued product.
- **Never deleted or cancelled.** Frappe's own Submit is refused; after approval only the acts and the system's out-of-date process change the record. One revision is in progress, and one is Current, per item.
- **Out of date (URS-QMS-029):** a new revision of a pinned document, a new version of a pinned master (item, routing, operation or sub-operation) or specification, a document withdrawn, a bill or sub-assembly bill switched off or replaced as the default, or a risk report superseded makes the Current record **Out of Date** in the same transaction. So does a risk management report approved for the item's file that the record does not pin, or a revision of the file taking effect (which leaves it without a report until the next is approved, and may list the item newly or no longer): *Other Changes Since Approval* says which. Each changed element shows *What Changed*, its version now and the change request. A daily sweep catches changes written straight to the database. QA is alerted once, with the item's open work orders (submitted, not completed, stopped or closed) to decide on, and the form lists them too; a *DMR Re-approval* reminder is open until a new revision is approved (due in *DMR Re-approval Due* days, 14 by default, Quality Settings). A record already out of date keeps recording each later change, with its own audit entry, and alerts nobody again. The signatures bind the item, headings and pinned elements, not the status, so they stay intact.
- **Start New Revision** copies the headings and documents of the last approved revision and, from an out-of-date one, says in *What Changed in This Revision* every change since approval: those recorded, and any the hooks and the sweep have not recorded yet.
- **Print (URS-QMS-030):** only a Current record prints as *CONTROLLED INDEX* with its signatures; any other prints *NOT FOR USE* with its status.
- **Production (URS-QMS-031):** a work order for a finished good is submitted only if the item's record is Current, still matches the live records, and pins the bill of materials the order uses; the order's *Device Master Record* field (read-only, not copied) then names the record it was released on. A draft work order is allowed (planning). Sub-assemblies are not held to it.
- **Making a finished good (URS-QMS-031):** a *Manufacture* or *Repack* stock entry that makes a finished good (its finished line, or any line into a store) is submitted only on a work order of that item, and only while the item's record is still Current and pins the order's bill. A finished good is never made without a work order.

## 3. Requirements and acceptance criteria

| # | Requirement | Verification |
| --- | --- | --- |
| D1 | QMS-026 headings and elements, sub-operations and sub-assembly bills among them; risk report mandatory (D-23); device lifetime, never shorter than the shelf life | `TestAssembly.*` (`test_the_risk_management_report_in_force_is_pinned`, `test_sub_operations_and_sub_assembly_bills_are_pinned`, `test_device_lifetime_is_controlled_on_the_item_and_shown_on_the_record`, `test_an_item_approved_before_the_lifetime_existed_is_unchanged_since_its_signature`) |
| D2 | QMS-027 pins from live records, read with locks at approval; missing or out-of-force elements and unapproved items refused | `TestAssembly.*`, `TestSignatures.test_approval_assembles_again`, `TestSignatures.test_an_element_out_of_force_blocks_approval`, `qms/test_pinned_records.py` |
| D3 | QMS-028 author, QA approval by another person, supersession, return and withdrawal with reasons, never deleted (a draft neither, by the Administrator neither), never put back in force by a save | `TestSignatures.*`, `TestPermissionsAndRegistration.*` |
| D4 | QMS-029 out of date in the same transaction (item, routing, operation, sub-operation, sub-assembly bill, documents, specifications, risk report superseded, risk revision taking effect, report not pinned) and by the sweep; later changes recorded; alert once with the open work orders; reminder until re-approved; signatures intact | `TestOutOfDate.*`, `TestAssembly.test_a_risk_revision_taking_effect_puts_it_out_of_date`, `TestAssembly.test_a_record_that_pins_no_report_goes_out_of_date_when_one_is_approved`, `TestSignatures.test_signature_excludes_status` |
| D5 | QMS-030 controlled index only when Current | `TestPrint.*` |
| D6 | QMS-031 work order needs the Current record pinning its bill and keeps it; a finished good is made only on such an order | `TestWorkOrders.*`; `lot_control/test_release.py` (a lot made on a released work order); pilot `pilot/test_standard_flow.py`, `pilot/test_demo.py` |
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
Try to submit a work order for the product. Expected: refused; the record's form and QA's alert list the product's open work orders. **Start New Revision**, sign as author, approve as QA, then submit the work order. Expected: accepted, and its *Device Master Record* field names the new revision. Try to post a *Manufacture* entry for the product without a work order. Expected: refused.

### DMR-06 Risk management report (plan D-23)
Draft a record for a finished good that is in no risk management file and **Sign as Author**. Expected: refused, "a risk management file listing …". Approve the file's assessment and report (module 39), then sign. Expected: accepted. Approve the file's next report. Expected: the record is *Out of Date*, with the old report *no longer in force* and the new one under *Other Changes Since Approval*.

### DMR-05 Print
Print a Current record (*CONTROLLED INDEX* with signatures) and a superseded one (*NOT FOR USE*).

## 5. End-to-end business workflow
Item, bill, routing, work instructions, specifications and labelling approved → the item's risk management file with its assessment and report approved (module 39, D-23) → Production Supervisor drafts and signs the DMR → QA approves (Current) → work orders released on it, finished goods made on them → any pinned record changes under change control, or a new risk report takes effect → DMR Out of Date, QA alerted with the open work orders, production stops (no new work order, no manufacture entry) → new revision signed and approved → production resumes.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Signed acts | Author (Authored), Approve (Approved), Return (Rejected), Withdraw (Rejected) |
| Signed content | `device_master_record.signed_content` (`esign.SIGNED_CONTENT`): item, revision, headings, elements with pinned versions; not status, stale flags or versions now |
| Pinned records | owner *Device Master Record* in `pinned_records.PIN_OWNERS` (`element_pins` over Current and Out of Date records, `elements_changed`); *Risk Management Report* pinnable; `records_changed` called by `risk_management.report_takes_effect` and `take_effect`; `version_tokens(..., lock=True)` reads with row locks, one query per record type |
| Risk management (D-23) | `device_master_record.risk_files_changed(items)`, called by `risk_management.report_takes_effect` and `take_effect` (items listed before and after), in the same transaction; raised, not logged |
| System process | `mark_out_of_date`: saved as an update after submission with a system reason (audited); alert on Current → Out of Date only; later changes recorded on an Out of Date record; *Other Changes Since Approval* (`other_changes`, system-set, not signed) |
| Work orders | `guard_work_order` on Work Order `before_validate` (when submitting) and `before_submit`; custom field `Work Order.mds_device_master_record` (read-only, no copy, set at submit; the Work Order is audited, so the field is in its audit trail; work orders carry no signature) |
| Stock entries | `guard_stock_entry` on Stock Entry `before_validate` (when submitting) and `before_submit`: Manufacture and Repack that make a finished good |
| Alert | *Device Master Record Out of Date* lists `open_work_orders(item)` (its `get_context`); the form's onload too |
| Setting | `dmr_reapproval_days` 14 (1 to 90) |
| Item | `mds_device_lifetime_years` (Int, default 5, 1 to retention − 2 years, at least the shelf life in years rounded up), controlled; left out of the item's signed content only when it is the default 5 |
| Pilot and journey | `setup/fixtures/risk.py` (policy, a file per product family, assessment and report, each signed by its own person; a second QA demo user); `setup/fixtures/device_master.py`; `qa/ui_journey.ensure_masters`, `prepare_device_master_record` (a file of its own per product), `prepare_risk_management`, `revise_dmr_specification`; test fixtures `setup/test_stock.risk_report_for_test`, `device_master_record_for_test` |

## 7. Defects, edge cases and risks
- Production is blocked at go-live for every finished good without a Current record (plan Q8). Each product's DMR must be approved before its first work order on the working site.
- One change can put many records out of date (a shared labelling document). Each record is marked once and alerted once.
- A record that goes out of date while a work order is already submitted does not stop that order's job cards or material transfers, but the finished good is not made on it (its Manufacture entry is refused) until a new revision is approved. QA decides on each open order the alert lists.
- A *Material Receipt* of a finished good (opening stock, stock found in a count) does not make it and is not held to the record; its lot stays in quarantine until an accepted inspection on that entry and QA's release (module 17).
- **Subcontracting (not in scope):** a finished good received from a subcontractor on a Subcontracting Receipt is not held to the device master record. The client makes its devices in house; if a device is ever subcontracted, the receipt needs the same check.
- A bill of materials pinned by any device master record cannot be cancelled: Frappe's link check refuses it, as it does for a bill a risk control pins. It is switched off or replaced as the default under change control instead, which puts the record out of date.
- A sub-assembly bill a line names that is not the sub-assembly's default is not in force, so the record cannot be approved until the parent bill names the default (ERPNext's BOM Update Tool).
- A pinned record changed in a transaction that started before an approval was committed may miss the newly Current record (the database's snapshot); the daily sweep marks it the next day.
- Plan D-23 at go-live: every finished good needs its family's risk management file with an approved assessment and report before its first device master record is approved.
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
