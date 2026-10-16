# Version-controlled masters (URS-MST-001) — feature document and browser test guide

Requirement: **URS-MST-001** (`meddevice_suite/qa/requirements.toml`).
Code: `meddevice_suite/master_data/versioning.py`, hooks in `meddevice_suite/hooks.py`, lookup `qms/change_control.py` `approved_changes_for`, client script `public/js/master_data.js`; the specification's own change control is in `master_data/specification.py`.
State on 2026-10-10: built on branch `feature/mst-001-master-versions` (on top of the module 6 pull request), reviewed in the same six independent rounds, module and full server suites green locally, browser suite green on the complete code; **not yet in a pull request** — section 8.

## 1. Feature overview

**Purpose.** The nine masters the client's diagram names — item, bill of materials, routing, operation and work centre (Workstation), supplier, customer, warehouse, equipment (Asset), inspection template — each carry a **Version** and a **Version Effective From** date. Once a master is in use, a change to what it controls is made only under an approved change request that lists it; the change raises the version, records the request it was made under, and the audit trail keeps the previous values. Before a master is in use (still being set up) changes are free.

**Why it matters.** ISO 13485 4.2.4 (control of documents) and 7.3.9 (control of design changes); the DFD's data stores D1 Customer Master, D2 Vendor Master, D3 Item/BOM/Routing Master, D10/D11 Equipment Master, and the older diagram's "All masters are version controlled and linked with Document Control".

**Who uses it.** The owner of each master (Item Manager / QA for items and specifications; Purchase for suppliers; Sales for customers; Production for routings, operations, workstations and BOMs; Stores for warehouses; Maintenance for assets) makes the change; the requester raises the change request and QA (and Management for a Major change) approves it (module 36). Everyone with read access sees the version.

**Prerequisites.** Change control (module 36) installed; the masters exist.

## 2. Detailed functional explanation

### 2.1 Version fields

Every one of the nine masters has a collapsible section **Version Control** with:

| Field | Rule |
| --- | --- |
| Version | read-only; 1 on creation (also for every record that existed before this feature, effective from its creation date); +1 on each controlled change |
| Version Effective From | read-only; today on creation and on each controlled change |
| Change Request for This Change | the approved change request a change is made under; named for **each** change; cleared on save (except on a submitted BOM or Asset, where it stays staged for a cancel) |
| Last Changed Under | read-only; the request the last controlled change was made under |

The specification (Quality Inspection Template) has its own two request fields from module 6 and gets Version / Effective From from here.

Nobody sets Version, Version Effective From or Last Changed Under by hand (refused: *The version of X is raised only by a change under change control.*), not even the Administrator, and not by saving the same record again after a controlled change.

### 2.2 What each master controls, and when it is "in use"

| Master | Controlled fields (a change needs a request once in use) | In use when |
| --- | --- | --- |
| Item | item name, item group, stock UOM, is stock item, has batch no, has expiry date, shelf life, HSN code, quality inspection template, inspection required before purchase / delivery, disabled | any non-cancelled stock movement, or a submitted BOM (as product or component), purchase order line, sales order line or quality inspection names it — or any of its variants |
| BOM | is active, is default | submitted |
| Routing | disabled; rows of operations (operation, workstation, workstation type, time, sequence) | a submitted BOM uses it |
| Operation | workstation, is corrective, quality inspection template, batch size | a submitted BOM operation or job card names it |
| Workstation | workstation type, production capacity, warehouse, disabled | a submitted BOM operation or job card names it |
| Supplier | supplier name, type, group, country, tax ID, disabled (**not** GSTIN: India Compliance writes it from the address directly; documented decision) | a submitted purchase order or receipt names it |
| Customer | customer name, type, group, tax ID, disabled (not GSTIN, as above) | a submitted quotation, sales order or delivery note names it |
| Warehouse | parent, is group, warehouse type, disabled | a non-cancelled stock movement in it |
| Asset (equipment) | asset name | submitted |
| Quality Inspection Template | per module 6 | a submitted inspection names it |

Fields not in the list (descriptions, notes, contacts, addresses, costing fields of a BOM that ERPNext allows to change after submit) change freely and do not raise the version.

Plan D-34 (2026-10-14): an item's **Specification Document** is a controlled field too (`item_master.CONTROLLED_FIELDS`); an item approved before it existed is given it once without a request (`LINKED_ONCE_FIELDS`), and a change of it afterwards needs one. A specification's test method is controlled by module 6 the same way, and any change of what QA approved returns the specification to draft (plan D-33, `docs/features/controlled-specifications.md`).

### 2.3 A controlled change

On save (desk, API, Data Import, bulk edit) of a master in use with a controlled field changed:

1. **Change Request for This Change** must name a change request, set in this save (a request left on the record earlier does not count), with status *Approved*, whose *Affected Records* lists this record. Messages: *<Doctype> <name> is in use: name the approved change request that lists it to change it.* / *Change request CR-… is not approved for implementation.* / *Change request CR-… does not list <name>.*
2. The save records the request in **Last Changed Under**, clears the input, raises **Version**, sets **Version Effective From** = today.
3. The audit trail (module 50) records the update with every old and new value, including `mds_changed_under`.

The form's lookup for the request offers only approved change requests that list this record.

### 2.4 ERPNext's own paths, held to the rule

- **Default BOM.** Submitting a new active default BOM switches the item's previous default off (ERPNext does this directly in the database). That is a change of the BOM in use: the new BOM must name an approved request that lists the **displaced** BOM (*X is the default bill of materials of ITEM: name the approved change request that lists it to replace it.*); after submit the displaced BOM's version is raised and an audit event records `is_default 1 → 0` with the request. The same applies when a submitted BOM is made default by update after submit. An inactive new default changes nothing and needs no request.
- **Cancelling** a submitted BOM or Asset takes it out of force: needs the staged request (*X is in use: name the approved change request that lists it to cancel it.*); the request's own link to the record does not block the cancel.
- **BOM Update Tool › Replace BOM** is refused (*A bill of materials is not replaced inside approved ones; make the changed one as a new bill of materials under an approved change request.*); *Update Cost* stays allowed. Decided 2026-10-09.
- **Item variants.** A template item is in use when any variant is. A controlled change of the template under a request reaches its variants under that request (each variant's version raised, Last Changed Under = the template's request). Only the fields changed on the template in that save are copied; a field a variant was changed on by itself under its own request is kept unless the template's request also lists that variant. Table fields (UOMs, taxes, defaults) copied by ERPNext are compared by content. This copy runs inside the save (not ERPNext's background job) for templates whose variants are in use.
- **Rename.** A master in use keeps its name (*… is in use: it keeps its name.*); a Quality Inspection Parameter read by an in-use specification likewise. A master not in use can be renamed.
- **Merge (URS-MST-017).** No master of the table above (the specification included) and no Quality Inspection Parameter is ever merged into another, in use or not, approved or not, even during migration: a merge would move the approvals and history of the record merged away (a supplier's approval, an item's specification and supplier rows, a customer's licence) onto the record kept, past QA (*Supplier X is not merged into Y: approvals and history stay with the record they were made for. Disable the duplicate instead.*). One rule, `versioning.refuse_merge`, run by the before_rename of every record; the customer's own licence guard leaves merging to it. Found by the independent review of 2026-10-15; ISO 13485 4.2.5, 7.4.1.

### 2.5 Intended but not implemented / outside this feature

- Asset `maintenance_required` is not a controlled field (client decision pending).
- Performance for very large variant families (hundreds of variants in use) is not measured; the copy runs in the web request and could hit its time limit (unverified).
- Templates whose variants are **not** in use still use ERPNext's own variant update (background job above 30 variants); if a variant comes into use between the save and the job, the job rolls back and the error is only in the Error Log (Low, documented).

## 3. Requirements and acceptance criteria

URS-MST-001: *Each of the nine masters … carries a version number and an effective date; a change to the controlled fields of a master in use is made only under an approved change request that lists it, raises the version, and is recorded with the previous values in the audit trail; ERPNext's BOM Update Tool does not replace a bill of materials inside approved ones, a changed bill of materials being a new one under change control.*

| # | Acceptance criterion | Implementation | Automated verification (`master_data/test_versioning.py`) |
| --- | --- | --- | --- |
| C1 | The nine masters carry Version and Version Effective From; new = version 1 today; existing records got version 1 | `custom_fields`, `apply_master_versions`, `first_version` | `test_the_nine_masters_carry_a_version_and_its_effective_date`, `test_a_new_master_is_version_1_in_effect_from_today` |
| C2 | Not in use ⇒ free change, version unchanged; non-controlled fields free | `version_master` | `test_a_master_not_yet_in_use_changes_freely`, `test_what_a_master_does_not_control_changes_freely` |
| C3 | In use ⇒ approved request listing the record, named per change; draft request refused; version +1; audit keeps old values and the request — on **each** of the nine masters and through the API | `version_master`, `require_approved_change`, `record_change` | `test_a_master_in_use_changes_only_under_an_approved_change_request_that_lists_it`, `test_each_change_names_its_change_request_again`, `test_a_request_left_on_the_record_does_not_cover_a_later_change`, `TestRequirementMatrix.test_every_master_in_use_changes_only_under_an_approved_request_and_gets_a_new_version` (Item, Routing rows, Operation, Workstation, Supplier, Customer, Warehouse), BOM and Asset cases below, `test_a_request_not_yet_approved_does_not_allow_a_change`, `test_the_api_changes_a_master_under_the_same_rule`; specification: module 6 tests |
| C4 | Version record set only by a controlled change (also effective date, last-changed-under, repeated save) | `version_master` | `test_the_version_is_set_only_by_a_controlled_change`, `test_the_version_record_is_set_only_by_a_controlled_change` |
| C5 | BOM: default switch and cancel under control; audit of the displaced default; inactive default needs none | `guard_new_default_bom`, `record_displaced_default`, `guard_cancel` | `test_a_submitted_bill_of_materials_changes_which_is_in_force_under_control`, `test_a_new_default_bill_of_materials_replaces_the_one_in_force_only_under_control`, `test_a_bill_of_materials_is_cancelled_only_under_control` |
| C6 | Asset: name change after submit and cancel under control | `version_master`, `guard_cancel`, `let_change_requests_not_block_cancel` | `test_an_equipment_record_in_use_is_cancelled_only_under_control`, `TestWiredIn.test_a_submitted_assets_name_changes_only_under_a_request` |
| C7 | BOM Update Tool replace refused (real path) | `refuse_bom_replace` | `test_the_bom_update_tool_does_not_replace_a_bill_of_materials`, `TestWiredIn.test_the_bom_update_tool_is_refused_its_replace` |
| C8 | Rename refused in use; merge refused always (URS-MST-017) | `refuse_rename_in_use`, `refuse_merge` | `test_a_master_in_use_keeps_its_name`, `TestNoControlledMasterIsMerged.*` |
| C9 | Variants follow the template's controlled change; hand edits need their own request; a variant's own decision survives; template tables reach variants | `update_variants_under_control`, `following_template` | `test_a_variant_follows_its_templates_controlled_change`, `test_a_variant_edited_by_hand_does_not_ride_on_its_templates_request`, `test_a_template_saves_after_a_variant_was_changed_on_its_own`, `test_a_variants_own_decision_survives_a_controlled_change_of_its_template`, `test_a_templates_table_change_reaches_its_variants_in_use` |
| C10 | In-use checks return true for every master once a submitted record depends on it | `MASTERS[*].in_use` | `test_each_masters_in_use_check_runs_on_a_record_not_yet_used`, `test_a_routings_operations_are_controlled`, `test_a_warehouse_with_stock_movements_is_in_use` |
| C11 | Form offers only approved requests listing the record | `approved_changes_for` | `test_the_form_offers_the_approved_change_requests_that_list_the_record` |

**Ambiguous / open (client):** is "operation and work centre" one master or two (implemented as two: Operation and Workstation); is Asset the equipment master (yes until module 8 says otherwise); should `maintenance_required` be controlled; should one approved request cover several separately-named changes to the same record while it stays Approved (today: yes, each named again); should a future effective date defer a change (today: no, effective today).

## 4. Step-by-step browser testing guide

Site and users as in the module 4 document. Data: demo item **RM-SILICONE** (in use: it has stock), a new item **TEST-ITEM-FREE** (not in use), demo BOMs of the Foley catheter line, a test asset copied from *Digital Caliper (Demo)*.

### VC-01 Version on creation; free change before use (C1, C2)
Role: QA Manager (Item Manager).
1. **Stock › Item › New** `TEST-ITEM-FREE` (stock item, group Raw Material, UOM Nos, HSN); Save. Expected: section *Version Control* shows Version 1, Version Effective From = today.
2. Change Shelf Life (days) to 730; Save. Expected: saved; Version still 1.
3. Open **Buying › Supplier › Silicone Compounds (Demo)** (in use). Expected: Version 1 (from cut-over), Version Effective From = its creation date.
Actual result: ____  Pass/Fail: ____

### VC-02 A master in use changes only under an approved change request (C3, C4)
Role: QA Manager for the item; a requester for the change request.
1. **Stock › Item › RM-SILICONE**; change Shelf Life to 900; Save. Expected: refused, title *Change control*: *Item RM-SILICONE is in use: name the approved change request that lists it to change it.*
2. Change only the Description; Save. Expected: saved; Version unchanged.
3. Raise a Change Request (Quality › Change Request › New; Record Type `Item`, Record `RM-SILICONE`; Send for Assessment); as QA assess (Minor) and approve with signatures. Expected: status Approved.
4. On the item: **Change Request for This Change** = the CR (the lookup shows only it); Shelf Life 900; Save. Expected: saved; Version 2; Version Effective From today; Last Changed Under = CR; Change Request for This Change empty.
5. Change Shelf Life to 1000 without a request; Save. Expected: refused (request per change).
6. Try to type Version 7 (System Administrator via API `frappe.client.set_value Item RM-SILICONE mds_version 7`). Expected: refused *The version of RM-SILICONE is raised only by a change under change control.*
7. Menu › **Audit Trail** on the item. Expected: the update shows `shelf_life_in_days 365 → 900` and `mds_changed_under → CR-…`.
8. Repeat step 1 and 4 for: Supplier (change Supplier Group), Customer (Customer Group), Workstation (Production Capacity), Operation (Batch Size), Warehouse (Warehouse Type), Routing (an operation's Operation Time) — each refused without, accepted with, a request listing it. Expected: as in steps 1 and 4.
Actual result: ____  Pass/Fail: ____

### VC-03 Bill of materials (C5)
Role: Production Supervisor (BOM) and QA (change request).
1. Open the active default BOM of the demo product; **Duplicate**; keep *Is Default* ticked; Save; Submit. Expected: refused *… is the default bill of materials of <item>: name the approved change request that lists it to replace it.*
2. Raise and approve a Change Request listing the **old** BOM. On the new BOM set Change Request for This Change = CR; Save; Submit. Expected: submitted; the old BOM shows Is Default unticked, Version 2, Last Changed Under = CR; its Audit Trail shows `is_default 1 → 0`.
3. On the old BOM: menu › **Cancel** (give the reason). Expected: refused *… is in use: name the approved change request that lists it to cancel it.* Set Change Request for This Change (update after submit, reason) to an approved CR listing it; Cancel again. Expected: cancelled; Last Changed Under = CR.
4. **Manufacturing › BOM Update Tool**: Replace BOM, current = old BOM, new = new BOM; Submit. Expected: refused *A bill of materials is not replaced inside approved ones…* Update Cost: allowed.
Actual result: ____  Pass/Fail: ____

### VC-04 Equipment (Asset) (C6)
Role: Maintenance / QA.
1. Copy *Digital Caliper (Demo)* as a new Asset, Submit. Change Asset Name (update after submit, reason); Save. Expected: refused *Asset … is in use: name the approved change request…*
2. With an approved CR listing it: Save. Expected: Version 2, Last Changed Under = CR.
3. Cancel without a staged request. Expected: refused. Stage an approved CR; Cancel. Expected: cancelled.
Actual result: ____  Pass/Fail: ____

### VC-05 Rename, merge (C8)
1. On RM-SILICONE: menu › Rename to `RM-SILICONE-2`. Expected: refused *Item RM-SILICONE is in use: it keeps its name.*
2. On TEST-ITEM-FREE (not in use): Rename. Expected: renamed.
3. Quality › Quality Inspection Parameter › *Dimension (mm)* › Rename. Expected: refused (read by an in-use specification).
4. On TEST-ITEM-FREE: Rename to the code of another item with *Merge with existing* ticked. Expected: refused *Item … is not merged into …: approvals and history stay with the record they were made for. Disable the duplicate instead.* The same on a supplier with an approval merged into one without, and on any customer.
Actual result: ____  Pass/Fail: ____

### VC-06 Variants (C9)
Preconditions: an Item Attribute with values; a template item with *Has Variants*; one variant received into stock (in use).
1. On the template: change Shelf Life; Save. Expected: refused (its variant is in use).
2. With an approved CR listing the template: Save. Expected: template Version 2; open the variant: Shelf Life changed, Version 2, Last Changed Under = the template's CR.
3. On the variant with its own approved CR: tick Disabled; Save. Expected: Version 3 on the variant.
4. On the template: change only the Description; Save. Expected: saved; the variant stays Disabled.
5. On the variant: untick Disabled without a request; Save. Expected: refused.
Actual result: ____  Pass/Fail: ____

### VC-07 Permissions and lists
1. As Stores: open an Item; Version Control section visible, fields read-only; no change possible to controlled fields (Stores has no write on Item). Expected: read-only.
2. Item list: filter by Version, by Version Effective From. Expected: filters work.
Actual result: ____  Pass/Fail: ____

### Regression
- Full automated journey (section 8) passes with versioning on: receiving, inspection, release, work order, job cards, delivery.

## 5. End-to-end business workflow

A supplier changes the silicone grade → Production raises a **Change Request** (lists Item RM-SILICONE and the Foley BOM) → QA assesses and approves (Major: Management too) → QA changes the item's controlled fields naming the request (version 2) → Production makes a new BOM revision naming the request and submits it as default; the old BOM drops to not-default (version 2, audit) → later work orders use the new BOM → the change request's actions are recorded done and its effectiveness checked (module 36). Every step is signed or audited; the versions and *Last Changed Under* tie each master to the change that altered it.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Custom fields | on each master: `mds_version_section`, `mds_version` (Int), `mds_effective_from` (Date), `mds_change_request` (Link Change Request, no_copy), `mds_changed_under` (Link, read-only); `allow_on_submit` on BOM and Asset. Applied by `apply_master_versions()` (also backfills version 1 with one UPDATE per master) |
| Server module | `master_data/versioning.py`: `MASTERS` (controlled fields, tables, `in_use`), `first_version`, `changed_fields`, `version_master`, `drop_unused_request`, `following_template`, `update_variants_under_control`, `record_change`, `raise_version`, `displaced_default`, `guard_new_default_bom`, `record_displaced_default`, `guard_cancel`, `let_change_requests_not_block_cancel`, `refuse_bom_replace`, `refuse_rename_in_use` |
| Hooks | `*`: `before_insert` → `first_version`; `validate` and `before_update_after_submit` → `version_master`; `before_rename` → `refuse_rename_in_use`. BOM: `before_submit` → `guard_new_default_bom`; `on_submit`, `on_update_after_submit` → `record_displaced_default`; `before_cancel` → `guard_cancel`; `on_cancel` → `let_change_requests_not_block_cancel`. Asset: `before_cancel`, `on_cancel`. BOM Update Log `validate` → `refuse_bom_replace`. Item `on_update` → `update_variants_under_control` |
| Change control | `qms/change_control.py`: `require_approved_change`, `approved_changes_for` (lookup, permission-aware); `Change Request Affected Record` record types now include Customer; `AFFECTED_TYPES` |
| Client | `public/js/master_data.js` sets the lookup query of `mds_change_request` on the nine masters |
| Audit | `compliance/audit.record` used for the displaced-default event; ordinary saves are audited by the module 50 hooks |
| Order of hooks | doctype-specific hooks run before `*` hooks (Frappe `document.py` `composer`), so module 6's template guard raises the version before the tamper check runs |

## 7. Defects, edge cases and risks

**Fixed during review (with tests):** default-BOM switch and BOM cancel bypassed control; a change request stayed on the record and covered later changes; in-use variants blocked template saves; rename/merge uncontrolled; India Compliance GSTIN write (field removed from control); crash without India Compliance; tamper flag never reset; Asset could never be cancelled; a hand-edited variant rode on its template's request; template table changes never reached variants; a draft request named earlier counted later.

**Open / potential:** large variant families (performance, unverified); background job race for not-in-use variants (Low); `maintenance_required` on Asset (client decision); one request covering several named changes (client policy).

## 8. Test results and verification summary

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_versioning` | 29 tests OK (2026-10-10) |
| Full server suite on the complete code (this branch) | `bench --site test.localhost run-tests --app meddevice_suite` | 46 + 620 OK (2026-10-10, before the stock-count fix of module 4/16); to be re-run after rebasing on the merged module 6 PR |
| Browser suite, retries off, complete code | `… run-ui-tests … --config retries=0` | 59 of 59 (2026-10-10) |
| CI | not yet run for this branch (no PR yet) | **pending** |
| Semgrep / lint / traceability | as for module 4 | 0 findings / pass / pass |
| Manual tests VC-01…VC-07 | — | **not executed** |

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Version fields on the nine masters; version 1 on creation and cut-over | Verified (automated) |
| Controlled change needs an approved, listed request named per change; version raised; audit | Verified (automated, all nine masters) |
| BOM default switch, BOM/Asset cancel, BOM Update Tool, rename/merge | Verified (automated, real ERPNext paths) |
| Variants | Verified (automated) |
| Manual browser cases VC-01…VC-07 | **Not tested** |
| CI on a fresh site | **Pending** (PR to be opened after module 6 merges) |
| Client decisions (equipment master, maintenance_required, request reuse policy) | **Open** |
