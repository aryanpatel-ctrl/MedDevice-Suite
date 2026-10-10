# Controlled specifications (module 6) — feature document and browser test guide

Requirement: **URS-MST-002** (`meddevice_suite/qa/requirements.toml`); versioning of a specification comes from URS-MST-001.
Code: `meddevice_suite/master_data/specification.py`, hooks in `meddevice_suite/hooks.py`; shared change-control check `qms/change_control.py` `require_approved_change`; demo specifications `pilot/inspection.py`.
State on 2026-10-10: built, reviewed (same six review rounds as module 4), server and browser suites green locally; final CI run pending (section 8).

## 1. Feature overview

**Purpose.** What QC measures on an item — the parameters, the method, the limits and the sample size — is a controlled specification, one per item and inspection stage (Incoming, In Process, Final). An inspection is made against the specification as it stands and cannot change its limits. Once a specification has been used by a submitted inspection it is *in use*: changing it needs an approved change request that lists it; the change is recorded with the old values in the audit trail and raises the specification's version.

**Why it matters.** ISO 13485 7.1 and 8.2.6 require defined acceptance criteria and records of who authorised changes to them. DFD: 3.4 IQC Inspection, 5.1 IPQC, 5.3 Final QC, 5.4 Test / Sterility Test read data store D3 (Item / BOM / Routing Master) and write D6 (QC records); "All masters are version controlled and linked with Document Control".

**Who uses it.** QA Manager (Quality Manager role) creates and changes specifications; QC Inspector, Stores and Production can read them; QC Inspector records inspections against them; the requester of a change raises the change request (module 36) and QA approves it.

**Prerequisites.** Items exist; Quality Inspection Parameters exist (ERPNext master, e.g. *Appearance*, *Dimension (mm)*); change control (module 36) is installed; measuring instruments exist as Assets (readings name their instrument, module 16).

## 2. Detailed functional explanation

### 2.1 The specification: Quality Inspection Template

Navigation: **Quality › Quality Inspection Template** (`/desk/quality-inspection-template/new`). ERPNext's template gained these fields:

| Field (label) | Rule |
| --- | --- |
| Item | the item the specification is for |
| Inspection Stage | Incoming / In Process / Final |
| Sample Size | fewest units an inspection examines (≥ 1) |
| Change Request for This Change | once in use: the approved change request a change is made under; named for **each** change; cleared on save |
| Last Changed Under | read-only; the request the last change was made under |
| Version, Version Effective From (section *Version Control*) | from URS-MST-001; read-only |
| Parameters table: Specification (parameter), **Method**, Numeric, Min Value, Max Value, Value, Formula based criteria, Acceptance formula | each row states its method; a numeric row needs at least one limit, a non-numeric row an accepted value (or a formula) |

**Completeness.** A template that names an item, a stage or a sample size, or that was already a specification, must state everything; otherwise saving is refused: *Specification X does not yet state the item, the inspection stage, the sample size, the method of Dimension (mm), the limits of …, the accepted value of …* An ERPNext template with none of the three fields (a legacy template) can still be saved as before; it becomes a specification when QA completes it — its first issue, which needs no change request even if inspections already used it.

**One per item and stage.** A second complete specification for the same item and stage is refused: *X already holds the incoming specification of ITEM; change that one instead.*

**In use.** A specification is in use once a submitted Quality Inspection names it. Then:
- any change to the item, stage, sample size, or to the parameter rows (parameter, method, limits, value, formula; adding or removing rows) needs **Change Request for This Change** set to an approved change request (status *Approved*) whose *Affected Records* lists this template (record type *Quality Inspection Template*). Otherwise: *Specification X is in use: name the approved change request that lists it to change it.* / *Change request CR-… is not approved for implementation.* / *Change request CR-… does not list X.*
- a successful change records the request in **Last Changed Under**, clears the input field (the next change names its own request), raises **Version** by 1 and sets **Version Effective From** to today; the audit trail keeps the previous values.
- it cannot be deleted (*Specification X is in use and is never deleted.*), renamed or merged (*… keeps its name and is never merged into another.*).
- a Quality Inspection Parameter that an in-use specification reads cannot be renamed.

Changes to a specification not yet in use are free (no request, version unchanged).

### 2.2 Inspections against the specification

On every Quality Inspection of type Incoming, In Process or Outgoing (= Final):

1. **Which specification.** The inspection is made against the item's specification for its stage; the form's template field is set to it (`choose_specification`). If the item has none: *Item X has no incoming specification; QA registers it before the item is inspected.* If readings were already taken against another template: *Inspect X against its incoming specification Y.*
2. **Readings = parameters.** The inspection reads each parameter of the specification exactly once (*An inspection against X reads each of its parameters once: A, B.*), with the specification's criteria (numeric, min, max, value, formula) unchanged (*The limits of A come from specification X and are not changed on an inspection.*). For an In Process inspection from a Job Card, ERPNext copies the item master's own template limits into the readings; the specification's limits replace them and the readings are judged again.
3. **Sample size.** The specification's sample size is filled in on a new inspection; the inspector may sample more, never less: on submit the inspection's Sample Size must be at least the specification's (*Specification X samples at least 5; this inspection samples 2.*).
4. Module 16's rules still apply to incoming inspections (lot named, inspected on its receipt, certificate verified, instruments named; a conditional supplier's lot inspected in full — module 4).

### 2.3 Intended but not implemented / outside this feature

- Which stages the production flow demands (an In Process inspection per operation, a Final inspection before release) is the production and batch-release modules' work (19, 20, 25); this feature provides the specifications and holds any inspection to them.
- A draft inspection made before a specification change is **not** re-laid out automatically when the specification changes (its stored readings stay); on save `check_against_specification` compares its readings with the specification as it then stands, so a stale draft is refused until its readings are redone. Not covered by an automated test (section 7).
- Formula-based criteria are accepted as ERPNext defines them; no additional validation of the formula text.

## 3. Requirements and acceptance criteria

URS-MST-002: *An item's inspection template is a controlled specification that states, for each inspection stage (incoming, in-process, final), the parameters, method, limits and sample size, and a template in use changes only under an approved change request.*

| # | Acceptance criterion | Implementation | Automated verification (`master_data/test_specification.py`) |
| --- | --- | --- | --- |
| C1 | One specification per item and stage; stages Incoming / In Process / Final | `guard_template`, `specification_for` | `test_one_specification_per_item_and_stage`, `test_the_inspection_form_finds_the_specification_for_its_stage` |
| C2 | States parameters, method per parameter, limits (or accepted value / formula), sample size; incomplete refused | `missing_from` | `test_a_specification_states_its_item_stage_sample_size_methods_and_limits` |
| C3 | An inspection is made against the item's specification for its stage, reads every parameter once, limits unchanged | `choose_specification`, `check_against_specification` | `test_an_inspection_is_made_against_the_items_specification_for_its_stage`, `test_readings_taken_against_another_template_are_refused`, `test_an_item_without_a_specification_for_the_stage_is_not_inspected`, `test_the_limits_come_from_the_specification`, `test_each_parameter_is_read`, `test_a_job_cards_inspection_is_judged_on_the_in_process_specification` |
| C4 | Sample size comes from the specification and is at least it | `choose_specification`, `check_sample_size` | `test_the_sample_size_comes_from_the_specification`, `test_the_sample_is_at_least_the_specifications` |
| C5 | In use ⇒ change only under an approved change request listing it; request named per change; draft/unapproved request refused; version raised; last request recorded | `guard_template`, `require_approved_change`, `raise_version` | `test_a_specification_in_use_changes_only_under_an_approved_change_request_listing_it`, `test_each_change_names_its_change_request_again`, `test_the_request_a_change_was_made_under_is_recorded_only_by_the_change`, `test_a_request_not_yet_approved_does_not_allow_a_change`, `test_removing_a_parameter_of_a_specification_in_use_needs_a_request`, `test_the_api_changes_a_specification_in_use_under_the_same_rule` |
| C6 | Not in use ⇒ changes freely; legacy template completed once without a request | `guard_template` | `test_a_specification_not_yet_used_changes_freely`, `test_a_template_made_before_the_rule_is_completed_once_without_a_change_request` |
| C7 | In use ⇒ never deleted; parameter rename refused | `protect_template`, `versioning.refuse_rename_in_use` | `test_a_specification_in_use_is_never_deleted`, `test_a_parameter_read_by_a_specification_in_use_keeps_its_name` |
| C8 | Browser: QC's incoming inspection is laid out from the specification | journey | `standard_flow.js` step "QC inspects each received lot against its template and submits the inspection" (passed) |

**Ambiguous / open:** whether inspectors may ever deviate from a limit with a signed reason (today: refused — SPC-14 decided as refused); whether open draft inspections must take new limits automatically (today: refused until redone); whether the Quality Inspection Parameter master itself is version-controlled (today: rename refused when in use; its other fields free).

## 4. Step-by-step browser testing guide

Site and users as in the module 4 document. Data: item **RM-SILICONE** (demo incoming specification *RM-SILICONE IQC (Demo)*: Appearance = Conforms; Dimension (mm) 9.5–10.5; sample size 5), parameters *Appearance*, *Dimension (mm)*.

### SP-01 QA registers a specification (C1, C2)
Role: QA Manager.
1. **Quality › Quality Inspection Template › New**. Name `TEST-SPEC-FINAL RM-SILICONE`; Item `RM-SILICONE`; Inspection Stage `Final`; leave Sample Size empty; add one parameter row *Dimension (mm)*, Numeric ticked, no limits, no method. Save. Expected: refused *Specification … does not yet state the sample size, the method of Dimension (mm), the limits of Dimension (mm).*
2. Set Sample Size 3; Method `Digital caliper`; Min 9.5, Max 10.5; Save. Expected: saved; section *Version Control* shows Version 1, Version Effective From = today.
3. New template, Item `RM-SILICONE`, Stage `Final`, complete rows; Save. Expected: refused *TEST-SPEC-FINAL RM-SILICONE already holds the final specification of RM-SILICONE; change that one instead.*
Actual result: ____  Pass/Fail: ____  Cleanup: the Final specification can stay (not in use) or be deleted.

### SP-02 Inspection follows the specification (C3, C4)
Role: QC Inspector. Preconditions: a received, certificate-verified lot of RM-SILICONE (module 4 test SA-05 steps 3–4 or the demo flow).
1. **Quality › Quality Inspection › New**; Inspection Type `Incoming`; Reference Type `Purchase Receipt`, the receipt; Item `RM-SILICONE`; Batch the lot. Expected: Quality Inspection Template fills with *RM-SILICONE IQC (Demo)* and the Readings table shows Appearance and Dimension (mm) with the specification's limits.
2. Change Dimension's Max Value to 99 in the readings; Save. Expected: refused, title *Limits changed*.
3. Reload; delete the Appearance row; Save. Expected: refused *… reads each of its parameters once: Appearance, Dimension (mm).*
4. Reload; Sample Size 2; readings inside limits with the instrument; Save; Submit. Expected: save ok; submit refused, title *Sample too small*: *… samples at least 5; this inspection samples 2.*
5. Sample Size 5; Submit. Expected: submitted, status Accepted.
6. New inspection, type `In Process`, item RM-SILICONE. Expected: refused *Item RM-SILICONE has no in process specification; QA registers it before the item is inspected.*
Actual result: ____  Pass/Fail: ____

### SP-03 A specification in use changes only under change control (C5, C7)
Role: QA Manager; the requester role for the change request can be any user with a job (e.g. Production Supervisor). Preconditions: SP-02 submitted an inspection against *RM-SILICONE IQC (Demo)*.
1. Open *RM-SILICONE IQC (Demo)*; change Dimension Max to 10.8; Save. Expected: refused, title *Change control*: *… is in use: name the approved change request that lists it to change it.*
2. **Quality › Change Request › New** (as the requester): Title `Widen silicone tube limit`, Change Type `Product or BOM`, description, reason; *Affected Records*: Record Type `Quality Inspection Template`, Record `RM-SILICONE IQC (Demo)`; an action row; Save; **Send for Assessment**.
3. As QA: open the change, fill the assessment (classification Minor), Save; **Assess** (sign); **Approve** (sign). Expected: status Approved.
4. Back on the specification: set **Change Request for This Change** to the new CR (the lookup offers only approved requests listing this specification); Dimension Max 10.8; Save. Expected: saved; Version 2; Version Effective From today; **Last Changed Under** = the CR; Change Request for This Change empty again.
5. Change Max to 10.9 without naming a request; Save. Expected: refused again (the request is named per change).
6. Name a draft or rejected change request; Save. Expected: refused *Change request … is not approved for implementation.* Name an approved request that lists another record. Expected: refused *… does not list …*
7. Menu › Delete the specification. Expected: refused *… is in use and is never deleted.* Rename it (menu › Rename). Expected: refused.
8. Menu › **Audit Trail**. Expected: the update event shows the old max 10.5 → 10.8 and `mds_changed_under`.
Actual result: ____  Pass/Fail: ____  Cleanup: a new CR to set the limit back to 10.5, or leave it on a test site.

### SP-04 Legacy template completed once (C6)
Role: QA Manager. Preconditions: a plain ERPNext template with rows but no Item/Stage/Sample Size, already used by a submitted inspection (System Administrator can prepare this on a test site).
1. Open it; set Item, Stage `In Process`, Sample Size 4, methods; Save. Expected: saved without a change request (first issue).
2. Change a limit; Save. Expected: refused (now a specification in use).
Actual result: ____  Pass/Fail: ____

### SP-05 Permissions
1. As QC Inspector: open a specification; try to edit. Expected: read-only (no Save).
2. As Stores: Quality › Quality Inspection Template list opens read-only.
3. As Purchase: the list is not offered in the sidebar / not permitted.
Actual result: ____  Pass/Fail: ____

### Regression
- Module 16 flows (certificate verification, hold/retest, release) unchanged: automated journey steps 8–11.

## 5. End-to-end business workflow

QA registers the incoming specification of RM-SILICONE (version 1) → Stores receives a lot → QC verifies the certificate → QC's incoming inspection is laid out from the specification (readings, limits, sample ≥ 5) → QA releases the lot (module 16) → the specification is now in use → an improvement needs a **Change Request** (raised, assessed and approved with signatures, module 36) → QA changes the limit naming the request → version 2 effective today, audit trail keeps 10.5 → 10.8 → later inspections use the new limit; the released lots keep the inspection that was made against version 1 (its readings carry the limits as they stood).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Custom fields | Quality Inspection Template: `mds_item` (Link Item), `mds_stage` (Select), `mds_sample_size` (Int), `mds_change_request` (Link Change Request, no_copy), `mds_changed_under` (Link, read-only); Item Quality Inspection Parameter: `mds_method` (Data); plus `mds_version`, `mds_effective_from` from versioning. Applied by `apply_specification_fields()` |
| Server module | `master_data/specification.py`: `missing_from`, `changed`, `in_use`, `guard_template`, `protect_template`, `specification_for`, `specification_for_inspection` (whitelisted), `choose_specification`, `check_against_specification`, `check_sample_size` |
| Hooks | Quality Inspection `before_validate` → `choose_specification`; `validate` → `check_against_specification`, then `lot_control.receipt.guard_incoming_inspection`; `before_submit` → `check_sample_size`. Quality Inspection Template `validate` → `guard_template`, `on_trash` → `protect_template`. `*` `before_rename` → `versioning.refuse_rename_in_use` (templates and parameters) |
| Change control link | `qms/change_control.py` `require_approved_change(request, doctype, name)` (status Approved and the record listed in Change Request Affected Record); lookup `approved_changes_for` used by the form (`public/js/master_data.js` set_query) |
| API | `GET/POST /api/method/meddevice_suite.master_data.specification.specification_for_inspection?item_code=…&inspection_type=…`; standard `/api/resource/Quality Inspection Template` (rules apply on save) |
| Demo data | `pilot/inspection.py` `ensure_inspection_template` makes complete specifications (`STAGES`, `METHODS`, `SAMPLE_SIZE`) for the pilot and test sites |
| Data flow | Inspection save → `before_validate` picks the specification by (item, stage) → ERPNext lays out readings → `validate` compares each reading's criteria with the template rows → refuse or accept; template save → `guard_template` (completeness, uniqueness, in-use check via one `exists` query, change-request check) → `raise_version` |

## 7. Defects, edge cases and risks

**Fixed during review (with tests):** the change request stayed on the template and covered later changes; a Job Card's in-process inspection was refused because ERPNext copied the item master's template limits.

**Potential / unverified:**
- Draft inspections open at the moment of a specification change are not refreshed automatically (section 2.3); no automated test.
- ERPNext forces readings of a Job Card inspection to *Accepted* before our check re-judges them; a reading marked *Rejected* by hand on such an inspection is judged by the limits, not by the hand mark (ERPNext behaviour; Low).
- Very large parameter tables: the comparison is in memory; no performance test.

## 8. Test results and verification summary

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_specification` | 20 tests OK (2026-10-10) |
| Full server suite / browser suite / CI / Semgrep / lint / traceability | as in the module 4 document (same branch, same runs) | see there; final CI run pending |
| Manual tests SP-01…SP-05 | — | **not executed**; the automated journey covers SP-02 step 1 and 5 (incoming inspection laid out from the specification and submitted) |

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Specification completeness and one-per-item-and-stage | Verified (automated) |
| Inspection laid out from, and held to, the specification; sample size | Verified (automated + journey) |
| Change only under an approved change request; request per change; version raised; audit | Verified (automated) |
| Never deleted / renamed in use; parameter rename refused | Verified (automated) |
| Manual browser cases SP-01…SP-05 | **Not tested** |
| Stale draft inspections after a change | **Not tested** |
| CI green for the final commit | **Pending** |
