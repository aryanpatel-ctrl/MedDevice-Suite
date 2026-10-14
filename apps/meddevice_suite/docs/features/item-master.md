# Item master (module 1) — feature document and browser test guide

Requirements: **URS-MST-003, URS-MST-004, URS-MST-005, URS-MST-006, URS-MST-016** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-MST-001 (an approved item counts as in use), URS-MST-002 (a specification of an approved item counts as in use), URS-MST-014 (the item's specification approved by QA).
Code: `meddevice_suite/master_data/item_master.py`, hooks in `meddevice_suite/hooks.py`, signed act in `compliance/signing.py`, desk in `public/js/master_data.js`; loaders `pilot/demo.py`, `pilot/inspection.py`, `qa/ui_journey.py`.
DFD trace: D3 Item / BOM / Routing Master; 3.3 Internal Batch Creation; 3.4 IQC Inspection; 5.3 Final QC; 6.2 Label Printing; flowchart "All masters are version controlled and linked with Document Control".
State on 2026-10-10: built; server and browser tests written from the requirement matrix (section 3) before the code; results in section 8. Independent review: see section 8.
State on 2026-10-14: the specification document (URS-MST-016) and the approved specification (URS-MST-006 as changed) added under foundation hardening plan D-33 / D-34, decided 2026-10-14; written with their tests, **not yet run** (section 8).

## 1. Feature overview

**Purpose.** No item is used anywhere until QA has approved it with an electronic signature. Before approving, QA sees on the item's form exactly what is still missing for its type: lot tracking, shelf life and storage condition for anything kept in lots; incoming inspection and the incoming specification, approved by QA, for a purchased material; and, for a finished good, its sterile flag, device class, pack sizes, UDI device identifier (a GTIN) and final specification, approved by QA. A purchased material, a packaging item and a finished good also name the controlled document that defines them — the material specification, the product specification or drawing — in effect when QA approves them (DFD: "All masters are version controlled and linked with Document Control"). From approval the item is locked: its controlled fields change only under an approved change request (module 36, URS-MST-001).

**Why it matters.** ISO 13485 4.2.4 and 7.5.1 require controlled, approved product data before production; CDSCO MDR 2017 (labelling) and 21 CFR 830 require the device identifier on the label. An item a buyer typed in yesterday must not be ordered, received, built or sold until QA has checked it — the system makes that impossible, not merely discouraged.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Creates and completes items; registers their specifications (module 6); approves each item with a signature |
| Production Supervisor | Manufacturing User | Raises the change request when an approved item must change (module 36) |
| Purchase, Stores, Sales, QC, Maintenance, Accounts | (view) | See the item and its approval banner; cannot change items; are refused a draft item on their documents |
| System Manager | System Manager | Reads only (URS-CMP-017) |

**Prerequisites.** The item's specification for its stage exists and is approved by QA (module 6) for raw materials, packaging and finished goods; their specification document is a controlled document of type *Specification* in effect (document control, module 30); the e-signature service (module 49); the change-control module for later changes (module 36). All are part of the app's configuration, applied by `bench migrate`.

## 2. Detailed functional explanation

### 2.1 The item's type (URS-MST-004)
Every item states **Item Type**: Raw Material, Packaging, Sub-assembly (WIP), Finished Good, Consumable, Spare Part or Equipment (the instrument behind an asset, module 8). The field is mandatory; the list starts blank so a type is always chosen, never defaulted. The type decides what QA checks before approving.

### 2.2 What approval needs

| Type | Needed before QA can approve |
| --- | --- |
| Raw Material, Packaging | stock item; lot tracking (Has Batch No); expiry date with a shelf life > 0; storage condition; *Inspection Required before Purchase*; an incoming specification (module 6) **approved by QA**; its **Specification Document** (the material specification) in effect |
| Sub-assembly (WIP) | stock item; lot tracking; expiry with shelf life; storage condition |
| Finished Good | as a sub-assembly, plus *Supplied Sterile* stated Yes or No (never defaulted); device class (MDR 2017: A, B, C, D or Not a device); units per box and boxes per carton (> 0); UDI device identifier (a valid GTIN-8/12/13/14 that no other item carries); a final specification (module 6) **approved by QA**; its **Specification Document** (the product specification or drawing) in effect |
| Consumable, Spare Part, Equipment | the type only |

**The specification document (URS-MST-016, plan D-34).** The field **Specification Document** (after *Quality Inspection Template*) links a controlled document of type *Specification*. Which types need one, and why: a raw material and a packaging item are bought to their material specification (ISO 13485 7.4.2, the purchasing information incoming inspection checks against); a finished good is made to its product specification or drawing (ISO 13485 4.2.3, the medical device file). A sub-assembly is defined by the finished good's documents and by its own approved bill of materials and routing, each operation with its effective work instruction; a consumable, a spare part and the item behind an asset do not become part of the product (the asset has its own master, module 8). A document any item names is checked the same way, needed or not. Messages: *its material or product specification*; *a material or product specification as its document (SOP-PRD-001 is a SOP, not of type Specification)*; *its material or product specification in effect (SPEC-PRD-002 is draft)* (or *obsolete*, once withdrawn). The lookup offers Specification documents not withdrawn.

**The approved specification (URS-MST-006, plan D-33).** A registered specification still in draft is listed as *its incoming specification X approved by QA* (or *final*): QA approves the specification with its own signature first (`docs/features/controlled-specifications.md`, section 2.2).

The item's form shows these in its banner while the item is a draft: *Not yet approved by QA, so used in no record. Still needed: …*, or *Ready for QA's approval*.

### 2.3 The signed approval (URS-MST-003)
- QA Manager clicks **Approve Item** (shown only when nothing is missing, the item is saved, and the user holds Quality Manager). The signing dialog asks for a **Reason** and the **password**; meaning *Approved*.
- The server checks again: QA role, not approved already, not disabled, a reason, not a template, nothing missing; then records the signature (21 CFR 11.50, 11.70) and sets **QA Approval = Approved**, **Approved By**, **Approved At**. The audit trail records the change.
- The signature binds what QA approved: the item code, whether it is a template or a variant and of what, its attribute values, unit conversions and barcodes, its controlled fields, its approval, version and effective date — not what ERPNext keeps up to date with use (last purchase rate, projected quantity) or free fields (description, reorder levels). A later controlled change shows as a change since signing, made under its change request (21 CFR 11.70 content check `is_unchanged_since`).
- These three fields are set by the signed act only. Typing them on the form, sending them through the API, importing them, copying an approved item or making a variant all leave the item in **Draft**.
- A **template** (has variants) is never approved; each **variant** is approved on its own. A variant takes the template's type, storage and device data (Item Variant Settings, applied by migrate) but never its GTIN: each variant carries its own.

### 2.4 Before approval, used nowhere
Every record type that names an item — directly or in one of its tables — refuses a draft item on save, whether made on the form, through the API, by import, or mapped from another document: material request, RFQ, supplier quotation, purchase order, quotation, sales order, purchase receipt and invoice, stock entry and count, lot (Batch), serial and batch bundle, BOM, work order, job card, quality inspection, delivery note, sales invoice, pick list, packing slip, product bundle, item alternative, asset, the stock ledger itself, and any record type a later ERPNext release adds. The message: *Not yet approved by QA, so not used in {record type}: {items}. QA approves an item on its form.*
- A submitted record that takes on items later (ERPNext's **Update Items** on a sales order, purchase order, quotation or supplier quotation) is checked too: the row ERPNext inserts by itself and the order's update after submit.
- Records ERPNext saves without their validation (Production Plan, bulk transactions) are checked before validation too.
- Lots and serial numbers ERPNext makes in bulk — the upload from a file and *create serial numbers* — insert straight into the database; both methods are replaced by checking wrappers (`override_whitelisted_methods`) that refuse an item QA has not approved.
- Corrections of what was recorded before are not refused: the reversal of a stock posting when a document is cancelled, and a valuation repost (*Repost Item Valuation*).
- Automatic reorder (Stock Settings) cannot be switched on while an item held before this module still has reorder levels and is not approved.
- Reorder levels are set once the item is approved: ERPNext's automatic reorder makes one batch of requests for all items, and a draft item in it would stop the batch.
Only setup records are allowed for a draft item, because QA needs them before approving: the item itself, its prices and tax rules, its specification (module 6), the supplier's approval for it (module 4), a change request about it. The list is `SETUP_TYPES` in `item_master.py`.

### 2.5 After approval, locked (URS-MST-001, URS-MST-006)
- The approved item is *in use* for change control: its tables of attribute values (what a variant is), unit conversions and barcodes, and its controlled fields — those of MST-001 plus type, storage condition and temperatures, sterile, device class, pack sizes and UDI — change only when the save names an approved change request listing the item; the version is raised and the approval and its signature stand.
- The **type** of an approved item never changes, even under a change request: another type is another item.
- An approved item cannot lose what its approval needed (e.g. its storage condition or incoming inspection), even under a change request: *… is approved and keeps what its approval needed; it cannot lose …*.
- Its specification is *in use* too (module 6): it changes only under a change request, is never deleted, and is never moved to another item or stage.
- It keeps its name (no rename or merge).
- Its **Specification Document** is part of what QA approved: changing it is a controlled change (a change request listing the item). The document withdrawn later does not undo the approval; the item's next approval needs one in effect.

### 2.6 Items held before this module
On migrate every existing item is set to **Draft**; nothing is approved by migration. Such an item gets its type, storage and device data once — only fields never filled (empty, not 0), and never on a template — while still a draft, without a change request (as a specification made before control gets its first issue, module 6); then QA approves it. On the pilot site the demo loader does this for the demo items.

### 2.7 Items approved before the specification document (plan D-34)
An item QA approved before this rule keeps its approval: nothing is un-approved by the migration, and saving it is not refused for the missing document (the rule that an approved item keeps what its approval needed compares what it lacked before with what it lacks now). QA, or the item's owner, names its document **once without a change request** (`versioning.LINKED_ONCE_FIELDS`): the link adds the document to what QA approved and replaces nothing; changing it afterwards needs a change request. The rule applies in full at the item's next approval (a variant, a copy, a new item). The signature of an earlier approval still verifies: the document is bound only once named (`item_master.signed_content`).

### 2.8 What is intended but not implemented here
- Printing the GTIN on the label and label reconciliation: module 23 (it will read these fields).
- Device master record linking item, BOM, routing, specifications and labels by version: module 31.

## 3. Requirements and acceptance criteria

> **URS-MST-003** An item is set up in draft and approved by QA with an electronic signature before it is used; until then every record that puts it to use (…) is refused for it, from the desk or the API; the approval is given only by the signed act, a variant is approved on its own, and from approval the item's controlled fields change only under an approved change request.
> **URS-MST-004** Every item states its type (…); a raw material, packaging item, sub-assembly or finished good is lot-tracked with a shelf life, an expiry date on every lot and its storage condition, and QA cannot approve it otherwise.
> **URS-MST-005** A finished good states whether it is supplied sterile, its device class under the Medical Devices Rules 2017, its storage condition, its pack sizes and its UDI device identifier, a well-formed GTIN that no other item carries; QA cannot approve a finished good without them. (Printing them on the label is module 23's requirement.)
> **URS-MST-006** A raw material or packaging item is approved only when incoming inspection is required for it and it has a specification for the incoming stage that QA has approved; a finished good only when it has a specification for the final stage that QA has approved; and an approved item cannot lose them. (Changed 2026-10-14, plan D-33: "that QA has approved".)
> **URS-MST-016** A raw material, a packaging item and a finished good name the controlled document that defines them, a document of type Specification (the material specification of what is bought, the product specification or drawing of what is sold); QA approves such an item only while that document is in effect, and any master whose approval names a controlled document is approved only while it is in effect; an item approved before this rule keeps its approval, is given its document once without a change request, and is held to the rule at its next approval.

| # | Acceptance criterion | Implementation | Automated verification (`master_data/test_item_master.py` unless noted) |
| --- | --- | --- | --- |
| C1 | New, copied, API-made items are drafts | `guard_item` | `test_a_new_item_is_a_draft`, `test_an_item_made_approved_starts_as_a_draft`, `test_a_copy_of_an_approved_item_is_a_draft_without_its_identifier` |
| C2 | QA approves with a signature, reason, audit | `approve`, `SIGNING_ACTIONS` | `test_qa_approves_an_item_with_a_signature`, `test_an_approval_says_why` |
| C3 | Only QA; never a shared account; wrong password signs nothing | `approve`, `esign.sign` | `test_only_qa_approves`, `test_a_wrong_password_approves_nothing` |
| C4 | Approval fields only by the signed act | `guard_item` | `test_the_approval_is_given_only_by_the_signed_act`, `test_an_approved_item_is_not_approved_again` |
| C5 | Draft refused on every record type, now and in later releases | `refuse_unapproved` (`*` validate), `SETUP_TYPES` | `test_every_record_type_naming_an_item_refuses_a_draft_one` (meta-driven) and the purchasing, stores, production, sales, bundle/alternative, asset, API and stock-ledger tests in `TestNotUsedBeforeApproval` |
| C6 | Setup records allowed before approval | `SETUP_TYPES` | `test_setup_records_are_made_for_a_draft_item` |
| C7 | Template not approved; variant on its own, own GTIN | `approve`, Item Variant Settings | `test_a_template_is_not_approved_and_each_variant_is_approved_on_its_own` |
| C8 | From approval, controlled change only; approval stands | `versioning.item_in_use`, `MASTERS["Item"]` | `test_from_approval_the_controlled_fields_change_only_under_an_approved_change_request`, `test_a_draft_item_changes_freely`, `test_an_approved_item_keeps_its_name` |
| C9 | Type stated; lot-tracked types need lots, shelf life, storage | `missing_for_approval` | `TestTypeAndLots.*` |
| C10 | Finished good device and label data; GTIN valid and unique; pack sizes counts | `missing_for_approval`, `valid_gtin`, `guard_item` | `TestFinishedGoodData.*` |
| C11 | Incoming inspection and specification; final specification; cannot be lost | `missing_for_approval`, `guard_item`, `specification.in_use` | `TestInspectionBeforeApproval.*` |
| C12 | Existing items become drafts | `apply_item_master_fields` | `test_items_held_before_the_item_master_are_drafts` |
| C13 | Desk banner, button, signing | `master_data.js`, `item_onload` | `TestForm.*`; browser `cypress/integration/item_master.js` |
| C14 | The standard flow runs with approved items | loaders | `pilot/test_standard_flow.py`, `cypress/integration/standard_flow.js` |
| C15 | The item's specification is one QA approved | `specification_missing` | `TestInspectionBeforeApproval.test_its_specification_is_one_qa_has_approved` |
| C16 | Raw material, packaging, finished good: document named, of type Specification, in effect (draft and withdrawn refused) | `missing_for_approval`, `defining_documents.document_missing` | `TestDefiningDocument.test_a_purchased_material_and_a_finished_good_are_approved_only_with_their_document_in_effect` |
| C17 | Other types need none; one they name is in effect | `SPECIFICATION_DOCUMENT_LINK.required_when` | `test_other_types_need_none_but_a_document_they_name_is_in_effect` |
| C18 | Approved before the rule: approval stands; document linked once without a request; later change controlled | `versioning.LINKED_ONCE_FIELDS`, `guard_item` | `test_an_item_approved_before_the_rule_keeps_its_approval_and_is_given_its_document_once` |
| C19 | An earlier signature still verifies; a named document is bound | `signed_content` | `test_the_signature_binds_the_document_once_named` |
| C20 | Every master whose approval names a controlled document is refused while it is not in effect (operation, specification, item; any later master) | `defining_documents.defined_masters` | `master_data/test_defining_documents.py` |
| C21 | Desk: the banner lists the document; QA names it on the form | `item_onload`, `master_data.js` | `test_the_form_says_the_document_is_needed`; browser `item_master.js` *[URS-MST-016] QA names the document that defines the finished good on its form* |

**Decisions (10 October 2026, recorded in the master plan, section 16):**
- Device class and storage condition are kept on the item: MDR 2017 labelling requires the storage conditions on the label and the device class decides the licence; DFD D3 holds the item's regulatory data.
- A template is never approved; each variant (each size) is approved on its own with its own GTIN, specification and BOM: GS1 gives each variant its own GTIN and label.
- Item type *Equipment* for the items assets are made of: ERPNext needs an item behind each asset (module 8).
- Items held before this module are not approved by migration; QA approves each one with a signature at go-live: an approval is a person's signed act (21 CFR 11.50, 11.70; ISO 13485 4.2.4). If the client loads several hundred items, a screen where QA signs each of a selected list in one pass can be added; each item keeps its own signature.

## 4. Step-by-step browser testing guide

Site: the pilot site after `load_demo_data`, or a test site. Use throw-away codes such as `TEST-IM-001`; never real product data.

### IM-01 A new item is a draft and is refused on an order (C1, C5)
Role: QA Manager, then Purchase.
1. QA: **Stock › Item › New**. Item Code `TEST-IM-001`, Item Group *Raw Material*, **Item Type** *Raw Material*, UOM *Nos*, HSN `39269099`. Save. Expected: **QA Approval = Draft**; orange banner *Not yet approved by QA, so used in no record. Still needed: lot tracking …; a shelf life …; its storage condition; incoming inspection …; its incoming specification (module 6).*
2. Purchase: **New Purchase Order**, supplier *Silicone Compounds (Demo)*, item `TEST-IM-001`. Save. Expected: refused — *Not yet approved by QA, so not used in Purchase Order: TEST-IM-001.*
Actual: ____ Pass/Fail: ____ Evidence: screenshots of both messages.

### IM-02 Complete the item and its specification (C9, C11)
Role: QA Manager.
1. On `TEST-IM-001` tick *Has Batch No*, *Automatically Create New Batch*, *Has Expiry Date*, Shelf Life 365; Storage Condition `Below 30 °C, dry`; tick *Inspection Required before Purchase*. Save. Expected: banner lists only *its incoming specification*.
2. **Quality › Quality Inspection Template › New**: Item `TEST-IM-001`, Stage *Incoming*, Sample Size 5, two parameters with method and limits. Save. Reload the item. Expected: *Ready for QA's approval* and the **Approve Item** button.

### IM-02a The specification document (C15–C17)
Role: QA Manager. Preconditions: IM-02 done; a controlled document of type *Specification* in effect (e.g. *Specification: RM-SILICONE (demo)* on the pilot) and an SOP in effect.
1. On `TEST-IM-001` before IM-02 step 2's approval of the specification: banner lists *its incoming specification TEST-IM-001 IQC … approved by QA* while the specification is in draft. Approve the specification (controlled-specifications SP-06).
2. Banner lists *its material or product specification*. Set **Specification Document** to the SOP; Save. Expected: *a material or product specification as its document (… is a SOP, not of type Specification)*.
3. Set it to the Specification document; Save. Expected: *Ready for QA's approval*.
4. A sub-assembly item: no Specification Document asked for; with a draft Specification named, refused *… its material or product specification in effect …*.
Actual: ____ Pass/Fail: ____

### IM-03 QA approves with a signature (C2–C4)
Role: QA Manager.
1. Click **Approve Item**. Expected: dialog *Sign: Approve*, *I approve this.*, Reason, Your password.
2. Wrong password → refused, still Draft. Empty reason → refused.
3. Reason `Item data and specification checked`, correct password → **QA Approval = Approved**, Approved By = you, Approved At = now; green banner *Approved by QA (name, time) …*; the **Electronic signatures** table shows *Approved*.
4. Try to set *QA Approval* back by any means (field is read-only; via `/api/resource/Item/TEST-IM-001` PUT `{"mds_approval_status":"Draft"}`). Expected: refused.

### IM-04 Approved item is used, and locked (C5, C8)
1. Purchase: the order of IM-01 now saves.
2. QA: change *Shelf Life* to 730 and save. Expected: refused — *Item TEST-IM-001 is in use: name the approved change request that lists it to change it.*
3. Raise and approve a change request listing `TEST-IM-001` (module 36), pick it in **Change Request** on the item, save. Expected: saved, **Version** 2, QA Approval still *Approved*.
4. Under a change request, clear the Storage Condition. Expected: refused — *… cannot lose its storage condition.*

### IM-05 Finished good data and GTIN (C10)
Role: QA Manager. New item `TEST-IM-FG`, type *Finished Good*, lot-tracked with shelf life and storage.
1. Enter GTIN `4006381333932` (wrong check digit). Expected: *… is not a GTIN …*. Enter `4006381333931`: saved.
2. A second finished good with the same GTIN. Expected: *GTIN … identifies TEST-IM-FG already …*.
3. Banner lists device class, pack sizes, final specification until each is entered; then **Approve Item** appears.

### IM-06 Variants (C7)
A template with an attribute (*has variants*) shows *A template: each of its variants is approved by QA on its own* and no button. Make a variant: it is Draft, has the template's type/storage/class, no GTIN; it is approved after its own GTIN and specification.

### Regression
Run the standard flow (pilot `pilot/test_standard_flow.py`, browser `standard_flow.js`): all its items are approved by QA's demo user and the journey runs end to end.

## 5. End-to-end business workflow

1. **QA** creates the item with its type and data; registers its specification and approves it with a signature (module 6); names the item's specification document, issued through document control (module 30). Data: Item (Draft, version 1), Quality Inspection Template (Approved), Controlled Document (Effective).
2. **QA** approves the item with a signature. Data: Item (Approved, approved by/at), Electronic Signature, Audit Event.
3. **Purchase** approves the supplier for it (module 4) — possible before or after — and orders it; **Stores** receives it; **QC** inspects; **QA** releases the lot (modules 16, 17).
4. **Production** builds a BOM on approved items (module 2) and makes the product; **Sales** quotes and sells only approved finished goods.
5. A change to the approved item goes through a **change request** (module 36): Item version raised, Audit Event with the request.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Custom fields (Item) | `mds_specification_document` (Link Controlled Document, after *Quality Inspection Template*; shown for the types that need it or when set; a controlled field, copied to variants), `mds_item_type` (Select, mandatory), `mds_approval_status` (Draft/Approved, read-only, no copy), `mds_approved_by`, `mds_approved_at`; section *Storage and Device Data*: `mds_storage_condition`, `mds_min_temperature`, `mds_max_temperature`, `mds_sterile`, `mds_device_class`, `mds_units_per_box`, `mds_boxes_per_carton`, `mds_udi_di` (no copy), `mds_gtin14` (hidden, the GTIN as 14 digits, unique in the database: GTIN-12/13/14 of one identifier are one; two saves at the same moment cannot both take it); `mds_sterility` is a Select Yes/No. Applied by `apply_item_master_fields()` from `setup/install.py`, which also adds the type/device fields to *Item Variant Settings* |
| Server module | `master_data/item_master.py`: `apply_item_master_fields`, `valid_gtin`, `specification_missing`, `missing_for_approval`, `guard_item`, `item_onload`, `approve`, `item_fields_of`, `items_named_by`, `refuse_unapproved`; constants `ITEM_TYPES`, `LOT_TRACKED_TYPES`, `PURCHASED_TYPES`, `DEFINED_BY_DOCUMENT`, `SPECIFICATION_DOCUMENT_LINK`, `CONTROLLED_FIELDS`, `SETUP_TYPES`; the shared check `master_data/defining_documents.py` |
| Hooks | `"*" validate` and `"*" before_update_after_submit` → `refuse_unapproved` (rows written by themselves included; reversals and reposts skipped) (returns at once for setup types, tables and record types naming no item); Item `validate` → `guard_item`; Item `onload` → `item_onload` |
| Change control | `versioning.item_in_use` counts approval as use; `MASTERS["Item"].fields` includes `CONTROLLED_FIELDS`; first fill of the device data on a pre-module draft allowed once; `specification.in_use` counts an approved item |
| Signing | `SIGNING_ACTIONS[("Item", "Approve")]` → `item_master.approve`, meaning *Approved*; `esign.SIGNED_CONTENT["Item"]` → `item_master.signed_content` (what the signature binds) |
| API | `POST /api/method/meddevice_suite.compliance.signing.sign_action` (`doctype=Item`, `name`, `action=Approve`, `password`, `reason`); `/api/resource/Item` under Item permissions (Item Manager edits; approval fields refused) |
| Desk | `public/js/master_data.js`: Item banner and **Approve Item** button (Quality Manager, nothing missing, saved) |
| Loaders | `pilot/demo.py` `item_master_data`, `demo_gtin` (GS1 prefix 20, restricted circulation), `approve_as`; `pilot/inspection.py` `approve_instrument_item`; `qa/ui_journey.py` `approve_as_qa` (run's QA user and password) |
| Test fixture | `setup/test_stock.py` `approve_for_test` — records an approval without the signed act, for other modules' tests only; the act is proved in `test_item_master.py`. `specification_document_for_test` (a Specification in effect) and `approve_specification_for_test`, used by `test_item_master.py` `with_specification` |
| Loaders (D-34) | `pilot/documents.py` `ensure_specification_document` issues *Specification: {item} (demo)* through document control and names it on a draft material or product, before `approve`; `qa/ui_journey.py` `ensure_item_document`, `prepare_effective_document` (browser) |

## 7. Defects, edge cases and risks

- **Existing site data:** on the first migrate every item becomes Draft and every transaction on it is refused until QA approves it. On the client's live site this is the intended go-live state (master data is loaded and approved before use); on the pilot, re-run `load_demo_data`, which approves the demo items.
- **ERPNext's own tests** create items without a type; they fail on a site with this app (the field is mandatory). Upgrade testing (rules.md section 8) runs them on a site without the app or must account for this.
- **Raw database writes** (`frappe.db.set_value`, SQL) bypass every document rule; not used by ERPNext for item approval; detected by the audit trail's integrity check only where audited.
- **Variant GTIN:** a variant made from a template has no GTIN; a finished-good variant cannot be approved until one is entered (by design).
- **Template with variants in use:** its controlled change reaches the variants under the template's request (MST-001); approval stays per variant.
- **Performance:** `refuse_unapproved` runs on every save that names items: one indexed query per save (`name in (...)`); the item fields of each record type are worked out once per request.
- **Production Plan** makes work orders and purchase orders with ERPNext's `ignore_validate`: for a BOM made before this module that names a draft component, such drafts can be created; submitting them is refused (their submit validates). Accepted.
- **Template BOMs:** a template is never approved, so a BOM on a template item (which ERPNext offers to its variants) cannot be made; each variant has its own. Client to confirm.

**Found by the second independent review (2026-10-11) and fixed, each with a test:** lots and serial numbers made in bulk through ERPNext's API for a draft item (High); the journey set-up signed the browser out (the session was replaced instead of restored in place, and its test checked the copy); records saved with `ignore_validate` skipped the check; pack sizes and temperatures could not be "not stated" (Frappe stores a number as 0), so the first fill and a one-ended temperature range failed — temperatures are now text checked as numbers; old reorder levels could stop automatic reorder; a variant's attribute values, unit conversions and barcodes were outside the signature and change control; non-ASCII digits and all zeros were taken as a GTIN; the 14-digit key backfill could stop a migrate. Unit conversions are controlled from now on: a factor sets the quantity of every order and receipt in that unit (decided 2026-10-11).

**Plan D-34 (2026-10-14), risks and open points:** a sub-assembly needs no document of its own (decided with the reason in section 2.2); if the client wants drawings for sub-assemblies, `DEFINED_BY_DOCUMENT` takes the type. A variant takes its template's document (Item Variant Settings) unless given its own. A document withdrawn after approval leaves the item approved; nothing on the item flags it until its next approval or a change of its document (not covered by an automated test; a check at the point of use, as for an operation's work instruction, is not in plan D-34).

**Open for the client:** one QA Manager both sets up and approves an item (the job holds Item Manager and Quality Manager); a second person for approval is not in the requirement and would need another job to set up items.

**Found by the independent review of 2026-10-10 and fixed (each with a test):** Update Items took a draft item into a submitted sales order; a specification could be moved away from its approved item; an approved item's type could change; the approval signature reported a change after ordinary use; one GTIN at two lengths, or two simultaneous saves, could give two items the same identifier; cancelling a posting of an item held before the module was refused; the guard ran before the fields existed on a first migrate; one draft item with reorder levels could stop automatic reorders; the first-fill exception treated 0 as empty and applied to templates; sterility defaulted to "not sterile"; a disabled item could be approved.

## 8. Test results and verification summary

Filled in from the runs of 2026-10-10 (see the pull request for the CI run).

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_item_master` | 43 tests OK (2026-10-10); with D-33/D-34 (2026-10-14): **not yet run** |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | see pull request |
| Browser | `item_master.js`, `approvals.js`, `standard_flow.js` | see pull request |
| Lint / format / traceability | `pre-commit run` | pass |
| Independent review | read-only review against the requirement matrix | see pull request |
| Manual browser tests IM-01…IM-06 | — | **not executed** (no manual tester session) |

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Draft item refused on every record type, desk and API | Verified (automated) |
| QA's signed approval with reason; wrong password / non-QA refused | Verified (automated, browser) |
| Approval fields only by the act; copies and variants drafts | Verified (automated) |
| Type, lot, storage, finished-good data, GTIN, specifications before approval | Verified (automated) |
| Specification approved by QA; specification document in effect; items approved before the rule (D-33, D-34) | **Written, not yet run** |
| Locked from approval; change only under change request; nothing lost | Verified (automated) |
| Pilot and journey items approved through the signed act | Verified (automated) |
| Manual IM-01…IM-06 | **Not tested** |
| Decisions on device data, variants, Equipment type, existing items (section 3) | Decided 2026-10-10, recorded in the master plan |
