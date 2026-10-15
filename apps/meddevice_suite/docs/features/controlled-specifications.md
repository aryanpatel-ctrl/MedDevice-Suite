# Controlled specifications (module 6) — feature document and browser test guide

Requirements: **URS-MST-002**, **URS-MST-014** (QA's signed approval), **URS-MST-015** (the test method) (`meddevice_suite/qa/requirements.toml`); versioning of a specification comes from URS-MST-001.
Code: `meddevice_suite/master_data/specification.py`, hooks in `meddevice_suite/hooks.py`; shared approval act `master_data/master_approval.py`; shared document check `master_data/defining_documents.py`; shared change-control check `qms/change_control.py` `require_approved_change`; migration `patches/v0_1/approve_specifications_in_use.py`; desk `public/js/master_data.js`; demo specifications `setup/fixtures/inspection.py`, their approval `setup/fixtures/documents.py` `approve_specification`.
State on 2026-10-10: built, reviewed (same six review rounds as module 4), server and browser suites green locally; final CI run pending (section 8).
State on 2026-10-14: QA's signed approval and the test method added (foundation hardening plan D-33, D-34, decided 2026-10-14); written with their tests, **not yet run** (section 8).

## 1. Feature overview

**Purpose.** What QC measures on an item — the parameters, the method, the limits and the sample size — is a controlled specification, one per item and inspection stage (Incoming, In Process, Final). QA approves each specification with an electronic signature once it names its **test method**, a controlled document in effect; an inspection is made only against an approved specification, as it stands, and cannot change its limits. Any change of what QA approved returns the specification to draft until QA approves it again. Once a specification has been used by a submitted inspection it is *in use*: changing it needs an approved change request that lists it as well; the change is recorded with the old values in the audit trail and raises the specification's version.

**Why it matters.** ISO 13485 7.1 and 8.2.6 require defined acceptance criteria and records of who authorised changes to them. DFD: 3.4 IQC Inspection, 5.1 IPQC, 5.3 Final QC, 5.4 Test / Sterility Test read data store D3 (Item / BOM / Routing Master) and write D6 (QC records); "All masters are version controlled and linked with Document Control".

**Who uses it.** QA Manager (Quality Manager role) creates, changes and approves specifications (approval is a signed act, QA only); QC Inspector, Stores and Production can read them; QC Inspector records inspections against approved ones; the requester of a change raises the change request (module 36) and QA approves it.

**Prerequisites.** Items exist; Quality Inspection Parameters exist (ERPNext master, e.g. *Appearance*, *Dimension (mm)*); the test method is a controlled document in effect (document control, module 30); the e-signature service (module 49); change control (module 36) is installed; measuring instruments exist as Assets (readings name their instrument, module 16).

## 2. Detailed functional explanation

### 2.1 The specification: Quality Inspection Template

Navigation: **Quality › Quality Inspection Template** (`/desk/quality-inspection-template/new`). ERPNext's template gained these fields:

| Field (label) | Rule |
| --- | --- |
| Item | the item the specification is for |
| Inspection Stage | Incoming / In Process / Final |
| Sample Size | fewest units an inspection examines (≥ 1) |
| Test Method | the controlled document that says how the parameters are tested: type *SOP*, *Work Instruction* or *External Document* (an adopted standard, such as the test methods of ISO 20696). Needed for QA's approval, and in effect then. The lookup offers documents of these types not withdrawn |
| Change Request for This Change | once in use: the approved change request a change is made under; named for **each** change; cleared on save |
| Last Changed Under | read-only; the request the last change was made under |
| Approval by QA, Approved By, Approved At (section *Approval by QA*, after the parameters) | read-only; set only by QA's signed **Approve** (URS-MST-014); a new or copied specification is *Draft* |
| Version, Version Effective From (section *Version Control*) | from URS-MST-001; read-only |
| Parameters table: Specification (parameter), **Method**, Numeric, Min Value, Max Value, Value, Formula based criteria, Acceptance formula | each row states its method; a numeric row needs at least one limit, a non-numeric row an accepted value (or a formula) |

**Completeness.** A template that names an item, a stage or a sample size, or that was already a specification, must state everything; otherwise saving is refused: *Specification X does not yet state the item, the inspection stage, the sample size, the method of Dimension (mm), the limits of …, the accepted value of …* An ERPNext template with none of the three fields (a legacy template) can still be saved as before; it becomes a specification when QA completes it — its first issue, which needs no change request even if inspections already used it.

**One per item and stage.** A second complete specification for the same item and stage is refused: *X already holds the incoming specification of ITEM; change that one instead.*

**In use.** A specification is in use once a submitted Quality Inspection names it. Then:
- any change to the item, stage, sample size, or to the parameter rows (parameter, method, limits, value, formula; adding or removing rows) needs **Change Request for This Change** set to an approved change request (status *Approved*) whose *Affected Records* lists this template (record type *Quality Inspection Template*). Otherwise: *Specification X is in use: name the approved change request that lists it to change it.* / *Change request CR-… is not approved for implementation.* / *Change request CR-… does not list X.*
- a successful change records the request in **Last Changed Under**, clears the input field (the next change names its own request), raises **Version** by 1 and sets **Version Effective From** to today; the audit trail keeps the previous values.
- it cannot be deleted (*Specification X is in use and is never deleted.*), renamed (*… is in use: it keeps its name.*); no specification or inspection parameter is ever merged into another, in use or not (URS-MST-017, *… is not merged into …: … Disable the duplicate instead.*).
- a Quality Inspection Parameter that an in-use specification reads cannot be renamed.

Changes to a specification not yet in use are free (no request, version unchanged); a change of one QA approved still returns it to draft (section 2.2).

### 2.2 QA's signed approval (URS-MST-014, URS-MST-015; plan D-33, D-34)

The specification uses the same act and fields as an operation, workstation, routing or store (`master_data/master_approval.py`).

- **Draft → Approved.** A new specification, or one made by copying (*Duplicate*), is *Draft*. QA Manager clicks **Approve** on its form (shown only when nothing is missing, the form is saved and the user holds Quality Manager); the signing dialog asks for the **Reason** and the **password**; meaning *Approved*. The server checks again: QA only (*Only QA approves a quality inspection template.*), not approved already, a reason, and nothing missing, then records the signature and sets **Approval by QA = Approved**, Approved By and Approved At. The approval fields are set by the act alone: typing them, the API, an import or a copy are refused or reset (*The approval of X is given only by QA's signed approval.*).
- **What approval needs.** Everything section 2.1 lists (item, stage, sampling, each parameter's method and limits) and the **test method**: named (*its test method*), of type SOP, Work Instruction or External Document (*a test method as its document (FRM-QC-001 is a Form, not of type SOP / Work Instruction / External Document)*) and in effect (*its test method in effect (SOP-QC-002 is draft)*). Message: *X cannot be approved yet; it lacks …*. The form's banner lists the same: *Not yet approved by QA, so it is not used yet. Still needed: …*, or *Ready for QA's approval*.
- **What the signature binds.** The item, stage, sampling, test method, every parameter with its method and criteria, and the version (`specification.signed_content`); a later change shows as a change since signing (21 CFR 11.70).
- **A change returns it to draft.** Any change of what QA approved — a limit, a method, a parameter added or removed, the sample size or sampling plan, the item, the stage, the test method — sets it back to *Draft* and clears Approved By and Approved At, in the same save; QA approves it again with a signature. A specification in use still needs its change request for the change, as section 2.1 says; the request authorises the change, the new signature approves the result. A save that changes nothing it states (none of these) leaves the approval as it is. Who may change a specification is unchanged: QA (there is no rule that the approver is another person than the last editor; the master-approval pattern has none).
- **First naming of the test method.** A specification that names no test method yet (made before this rule, or approved by the migration) is given one once **without a change request**, as a template made before control gets its first issue; it is a change of what QA approved, so it returns to draft and QA approves it with the test method. Changing the test method afterwards is a change like any other.
- **My Work.** A specification in draft that names its item waits on QA in My Work (*Approve the specification*), not on anyone else; a legacy template naming no item does not.
- **Who relies on it.** A device master record and a risk control rely on a specification only while it is approved (`pinned_records.in_force`); a change request that changed a specification is implemented once QA has approved it again (`change_control.in_force_rules`).

### 2.3 Existing specifications on the release that brings the rule

The patch `patches/v0_1/approve_specifications_in_use.py` runs once on `bench migrate`:
- each **specification a submitted inspection was already made against** (it names its item and stage) is set to *Approved* **by migration**: Approved By stays empty, Approved At is the migration's time, and the audit trail records the change with the reason *Migration: in use by a submitted inspection before specifications were approved by QA (plan D-33); QA approves it with a signature at its next change*. Its form says *Approved by migration: it was in use before QA approved specifications. A change returns it to draft until QA approves it with a signature.* Inspections against it continue, so receiving and production are not stopped on the go-live day.
- **every other specification stays Draft**, including one only an approved item names: QA approves it with a signature before it is inspected against. A legacy template naming no item is not a specification and is never approved by migration.
- run again (it is idempotent), the patch approves nothing it approved once, even when that specification was changed since and is in draft.

Why: an approval is a person's signed act (21 CFR 11.50); the migration does not sign for QA, it records that the specification was already in use, by whose rule, and why. Each such specification gets QA's signature at its next change, which also names its test method (section 2.2).

### 2.4 Inspections against the specification

On every Quality Inspection of type Incoming, In Process or Outgoing (= Final):

1. **Which specification.** The inspection is made against the item's specification for its stage; the form's template field is set to it (`choose_specification`). If the item has none: *Item X has no incoming specification; QA registers it before the item is inspected.* If it is in draft (new, or changed since QA approved it): *Specification Y of X is in draft: new, or changed since QA approved it. QA approves it with a signature before X is inspected against it.* (title *Specification not approved*), on save and again on submit. If its test method was withdrawn since: *The test method Z of specification Y is no longer in effect. QA names the method in effect and approves the specification again before X is inspected.* If readings were already taken against another template: *Inspect X against its incoming specification Y.*
2. **Readings = parameters.** The inspection reads each parameter of the specification exactly once (*An inspection against X reads each of its parameters once: A, B.*), with the specification's criteria (numeric, min, max, value, formula) unchanged (*The limits of A come from specification X and are not changed on an inspection.*). For an In Process inspection from a Job Card, ERPNext copies the item master's own template limits into the readings; the specification's limits replace them and the readings are judged again.
3. **Sample size.** The specification's sample size is filled in on a new inspection; the inspector may sample more, never less: on submit the inspection's Sample Size must be at least the specification's (*Specification X samples at least 5; this inspection samples 2.*).
4. Module 16's rules still apply to incoming inspections (lot named, inspected on its receipt, certificate verified, instruments named; a conditional supplier's lot inspected in full — module 4).

### 2.5 Intended but not implemented / outside this feature

- Which stages the production flow demands (an In Process inspection per operation, a Final inspection before release) is the production and batch-release modules' work (19, 20, 25); this feature provides the specifications and holds any inspection to them.
- A draft inspection made before a specification change is **not** re-laid out automatically when the specification changes (its stored readings stay); on save `check_against_specification` compares its readings with the specification as it then stands, so a stale draft is refused until its readings are redone. Not covered by an automated test (section 7).
- Formula-based criteria are accepted as ERPNext defines them; no additional validation of the formula text.
- An inspection draft saved while its specification was approved is refused on its next save or submit once the specification is back in draft; nothing tells the inspector beforehand.
- The test method names the document, not its revision: a new revision of it takes effect through document control (module 30) without a change of the specification, as an operation's work instruction does.

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

URS-MST-014: *A specification is in draft until QA approves it with an electronic signature, which only QA can give and which is the only way it becomes approved; an inspection is made only against an approved specification, and one in draft is refused naming it; any change of what QA approved returns the specification to draft until QA approves it again, a specification in use still changing only under an approved change request; on the release that brings this rule, each specification a submitted inspection was already made against is approved by migration, recorded in the audit trail with the reason, and every other specification stays in draft.*

URS-MST-015: *A specification names its test method, a controlled document of type SOP, Work Instruction or External Document; QA approves the specification only while that document is in effect, and no inspection is made against a specification whose test method is no longer in effect.*

| # | Acceptance criterion | Implementation | Automated verification (`master_data/test_specification.py` unless noted) |
| --- | --- | --- | --- |
| C9 | New is draft; QA approves with signature, reason, password; signature verifies and binds the content | `master_approval.approve`, `specification.approve`, `signed_content` | `TestApproval.test_a_new_specification_is_a_draft_that_qa_approves_with_a_signature` |
| C10 | Only QA (not QC, not Production), right password, a reason, once | `master_approval.approve` | `test_only_qa_approves_with_password_and_reason_and_once` |
| C11 | Approval fields only by the act; made approved or copied → draft | `master_approval.guard_approval` (in `guard_template`) | `test_the_approval_is_given_only_by_the_signed_act` |
| C12 | Inspection only against an approved specification, refused naming it (hook and real save) | `refuse_unless_approved` in `choose_specification` | `test_an_inspection_is_made_only_against_an_approved_specification` |
| C13 | Any change of what was approved → draft; unchanged save keeps approval; the old signature shows the change | `master_approval.return_to_draft` in `guard_template` | `test_a_change_returns_an_approved_specification_to_draft` |
| C14 | In use: change still needs its request; version raised; draft until approved again | `guard_template` | `test_a_change_of_a_specification_in_use_needs_its_request_and_returns_it_to_draft` |
| C15 | Test method named once without a request; later change needs one | `guard_template` (`first_issue`) | `test_naming_the_test_method_for_the_first_time_needs_no_request` |
| C16 | Migration: in use → approved by migration with audit reason; others draft; legacy template untouched; never again | patch `approve_specifications_in_use` | `TestApprovalByMigration.*` |
| C17 | Form banner and button; My Work queue; act offered to QA | `specification.onload`, `master_data.js`, `my_work.waiting_for_qa` | `test_the_form_shows_the_approval_or_what_stands_in_its_way`, `test_qa_is_offered_the_approval_and_a_draft_waits_on_qa`; `work_management/test_my_work.py` guardrail |
| C18 | Test method required: none, wrong type, draft, withdrawn refused; SOP / WI / External accepted | `missing_for_approval`, `defining_documents.document_missing` | `TestTestMethod.test_a_specification_is_approved_only_with_its_test_method_in_effect`; `master_data/test_defining_documents.py` |
| C19 | No inspection against a specification whose test method was withdrawn | `refuse_unless_approved` | `test_no_inspection_is_made_against_a_specification_whose_test_method_is_withdrawn` |
| C20 | Relied on only while approved (device master record, risk control, change request implemented) | `pinned_records.in_force_names`, `change_control.in_force_rules` | `qms/test_pinned_records.py` `test_in_force_each_type` |
| C21 | Browser: the form says the test method is missing; QA names it and approves with a signature | desk | `cypress/integration/specification.js` |

**Ambiguous / open:** whether the specification's approver must be another person than the QA user who wrote it (today: no; QA writes and approves, as for operations and items); whether the existing specifications approved by migration should be signed by QA in one pass at go-live rather than at their next change (today: at the next change);
whether inspectors may ever deviate from a limit with a signed reason (today: refused — SPC-14 decided as refused); whether open draft inspections must take new limits automatically (today: refused until redone); whether the Quality Inspection Parameter master itself is version-controlled (today: rename refused when in use; its other fields free).

## 4. Step-by-step browser testing guide

Site and users as in the module 4 document. Data: item **RM-SILICONE** (demo incoming specification *RM-SILICONE IQC (Demo)*: Appearance = Conforms; Dimension (mm) 9.5–10.5; sample size 5), parameters *Appearance*, *Dimension (mm)*.

### SP-06 QA approves a specification with its test method (C9–C11, C13, C18)
Role: QA Manager. Preconditions: an SOP in effect (e.g. *Incoming inspection test method (demo)* on the pilot), a Form in effect, and the Final specification of SP-01 (*TEST-SPEC-FINAL RM-SILICONE*), not yet in use.
1. Open the specification. Expected: banner *Not yet approved by QA, so it is not used yet. Still needed: its test method.*; no **Approve** button.
2. Set **Test Method** to the Form; Save; reload. Expected: banner lists *a test method as its document (… is a Form, not of type SOP / Work Instruction / External Document)*.
3. Set it to the SOP; Save; reload. Expected: *Ready for QA's approval*, **Approve** button.
4. **Approve**: wrong password → refused, still Draft; empty reason → refused; reason and password → **Approval by QA = Approved**, Approved By you; banner *Approved by QA; inspections are made against it. A change returns it to draft until QA approves it again.*
5. Change the Dimension Max Value; Save. Expected: Approval by QA back to *Draft*, Approved By empty; banner *Not yet approved* / *Ready for QA's approval*.
6. As QC Inspector, open it. Expected: no **Approve** button; via `POST /api/method/meddevice_suite.compliance.signing.sign_action` (doctype Quality Inspection Template, action Approve) → *Only QA approves a quality inspection template.*
Actual result: ____  Pass/Fail: ____

### SP-07 An inspection only against an approved specification (C12, C19)
Role: QC Inspector, then QA. Preconditions: SP-02's receipt and lot; *RM-SILICONE IQC (Demo)* approved.
1. As QA, change a limit of *RM-SILICONE IQC (Demo)* under an approved change request (SP-03 steps 2–4). Expected: saved, Version raised, Approval by QA *Draft*.
2. As QC, new incoming inspection of the lot. Expected: refused, title *Specification not approved*: *Specification RM-SILICONE IQC (Demo) of RM-SILICONE is in draft: …*
3. As QA, **Approve** it (signature). As QC, repeat step 2. Expected: laid out from the specification as in SP-02.
4. (Test site only) Withdraw its test method (Controlled Document › **Withdraw**, signature). As QC, new inspection. Expected: refused, title *Test method not in effect*.
Actual result: ____  Pass/Fail: ____

### SP-08 Existing specifications after the migration (section 2.3)
Role: System Administrator on a copy of a site with inspections made before this release, then QA.
1. `bench --site <copy> migrate`. Open a specification a submitted inspection names. Expected: Approval by QA *Approved*, Approved By empty, banner *Approved by migration …*; its Audit Trail shows the update with the migration's reason.
2. Open a specification no submitted inspection names. Expected: *Draft*.
3. Name its test method and save (no change request needed for the first naming). Expected: *Draft*; QA approves it with a signature.
Actual result: ____  Pass/Fail: ____

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

QA registers the incoming specification of RM-SILICONE (version 1), names its test method and approves it with a signature → QA approves the item (module 1) → Stores receives a lot → QC verifies the certificate → QC's incoming inspection is laid out from the specification (readings, limits, sample ≥ 5) → QA releases the lot (module 16) → the specification is now in use → an improvement needs a **Change Request** (raised, assessed and approved with signatures, module 36) → QA changes the limit naming the request → version 2 effective today, audit trail keeps 10.5 → 10.8, the specification back in draft → inspections are refused until QA approves version 2 with a signature → later inspections use the new limit; the released lots keep the inspection that was made against version 1 (its readings carry the limits as they stood).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Custom fields | Quality Inspection Template: `mds_item` (Link Item), `mds_stage` (Select), `mds_sample_size` (Int), `mds_test_method` (Link Controlled Document), `mds_change_request` (Link Change Request, no_copy), `mds_changed_under` (Link, read-only); after the parameters `mds_master_approval` (Draft/Approved, read-only, no copy), `mds_master_approved_by`, `mds_master_approved_at` (`master_approval.approval_fields`); Item Quality Inspection Parameter: `mds_method` (Data); plus `mds_version`, `mds_effective_from` from versioning. Applied by `apply_specification_fields()`, which also sets a template with no approval value to Draft |
| Server module | `master_data/specification.py`: `missing_from`, `changed`, `in_use`, `guard_template`, `protect_template`, `missing_for_approval`, `approve`, `onload`, `signed_content`, `refuse_unless_approved`, `specification_for`, `specification_for_inspection` (whitelisted), `choose_specification`, `check_against_specification`, `check_sample_size`; constants `TEST_METHOD`, `TEST_METHOD_DOCUMENT` |
| Shared | `master_data/master_approval.py` (`approve`, `guard_approval`, `return_to_draft`, `onload`); `master_data/defining_documents.py` (`DefiningDocument`, `document_missing`, `not_in_effect`, `defined_masters`, the guardrail's list) |
| Signing | `SIGNING_ACTIONS[("Quality Inspection Template", "Approve")]` → `specification.approve`, meaning *Approved*; `esign.SIGNED_CONTENT` → `specification.signed_content`; My Work `waiting_for_qa`, `LISTED_BY` |
| Hooks | Quality Inspection `before_validate` → `choose_specification` (with `refuse_unless_approved`); `validate` → `check_against_specification`, then `lot_control.receipt.guard_incoming_inspection`; `before_submit` → `check_sample_size`. Quality Inspection Template `validate` → `guard_template`, `onload` → `specification.onload`, `on_trash` → `protect_template`. `*` `before_rename` → `versioning.refuse_rename_in_use` (templates and parameters) |
| Migration | `patches/v0_1/approve_specifications_in_use.py` (section 2.3) |
| Change control link | `qms/change_control.py` `require_approved_change(request, doctype, name)` (status Approved and the record listed in Change Request Affected Record); lookup `approved_changes_for` used by the form (`public/js/master_data.js` set_query) |
| API | `GET/POST /api/method/meddevice_suite.master_data.specification.specification_for_inspection?item_code=…&inspection_type=…`; standard `/api/resource/Quality Inspection Template` (rules apply on save) |
| Demo data | `setup/fixtures/inspection.py` `ensure_inspection_template`, `ensure_in_process_specification` make complete specifications (`STAGES`, `METHODS`, `SAMPLE_SIZE`) for the pilot and test sites and approve them through the caller's `approve`: `setup/fixtures/documents.py` `approve_specification` (the stage's test method, an SOP, issued through document control, then QA's signature) on the pilot and the browser journey; `setup/test_stock.py` `approve_specification_for_test` (recorded as the act leaves it, with `inspection_method_for_test`) in other modules' tests |
| Data flow | Inspection save → `before_validate` picks the specification by (item, stage) → ERPNext lays out readings → `validate` compares each reading's criteria with the template rows → refuse or accept; template save → `guard_template` (completeness, uniqueness, in-use check via one `exists` query, change-request check) → `raise_version` |

## 7. Defects, edge cases and risks

**Plan D-33 / D-34 (2026-10-14), risks:**
- On go-live every specification not yet inspected against is in draft: incoming, in-process and final inspections of those items are refused until QA approves each one with its test method. This is intended (approved before use) but must be planned into the go-live: QA approves the specifications of the items to be received first.
- A specification approved by migration carries no signature until its next change; an auditor sees *Approved by migration* and the audit reason.
- Returning to draft on every change means a change made under a change request stops inspections of that item until QA signs again; QA should approve right after the change (the browser journey and the pilot do).
- The guardrail `master_data/test_defining_documents.py` holds every master with an approval act that links a controlled document to the declared list; the document types each accepts are a decision of plan D-34 (test method: SOP, Work Instruction, External Document).

**Fixed during review (with tests):** the change request stayed on the template and covered later changes; a Job Card's in-process inspection was refused because ERPNext copied the item master's template limits.

**Potential / unverified:**
- Draft inspections open at the moment of a specification change are not refreshed automatically (section 2.3); no automated test.
- ERPNext forces readings of a Job Card inspection to *Accepted* before our check re-judges them; a reading marked *Rejected* by hand on such an inspection is judged by the limits, not by the hand mark (ERPNext behaviour; Low).
- Very large parameter tables: the comparison is in memory; no performance test.

## 8. Test results and verification summary

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_specification` | 20 tests OK (2026-10-10); with D-33/D-34 (2026-10-14): **not yet run** |
| Guardrail | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_defining_documents` | **not yet run** |
| Browser | `cypress/integration/specification.js` | **not yet run** |
| Full server suite / browser suite / CI / Semgrep / lint / traceability | as in the module 4 document (same branch, same runs) | see there; final CI run pending |
| Manual tests SP-01…SP-05 | — | **not executed**; the automated journey covers SP-02 step 1 and 5 (incoming inspection laid out from the specification and submitted) |

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Specification completeness and one-per-item-and-stage | Verified (automated) |
| Inspection laid out from, and held to, the specification; sample size | Verified (automated + journey) |
| Change only under an approved change request; request per change; version raised; audit | Verified (automated) |
| Never deleted / renamed in use; parameter rename refused | Verified (automated) |
| Manual browser cases SP-01…SP-08 | **Not tested** |
| QA's signed approval, return to draft, inspection only against approved, migration, test method (D-33, D-34) | **Written, not yet run** |
| Stale draft inspections after a change | **Not tested** |
| CI green for the final commit | **Pending** |
