# Bill of materials (module 2) — feature document and browser test guide

Requirement: **URS-MST-007** (`meddevice_suite/qa/requirements.toml`). Builds on URS-MST-001 (which bill is in force and cancelling one are under change control) and URS-MST-003 (only QA-approved items on any record).
Code: `meddevice_suite/master_data/bom.py`, hooks in `meddevice_suite/hooks.py` (BOM `before_submit`, `onload`; Work Order `validate`), signed act in `compliance/signing.py`, signature content in `compliance/esign.py`, desk in `public/js/master_data.js`, loaders `pilot/demo.py`, `qa/ui_journey.py`.
DFD trace: D3 Item / BOM / Routing Master; 2.2 MRP Calculation; 4.1 Create Production Order.

## 1. Feature overview

**Purpose.** A bill of materials says what a product is made of. Production drafts it; QA approves it with an electronic signature, and that signature is the only way it is submitted. QA can approve it only when it is complete, and a work order is made only from an approved, active bill of its own product.

**Why it matters.** ISO 13485 7.5.1 requires production to follow documented, approved requirements; the BOM is part of the device master record (4.2.3; FDA's medical device file). A work order built on an unapproved or wrong bill produces a non-conforming device.

| Job profile | Here |
| --- | --- |
| Production Supervisor | Drafts the bill: product, quantity, components, operations, process loss |
| QA Manager | Approves it with a signature; replaces or cancels one in force under change control |
| Stores, Purchase, QC Inspector | Read |

## 2. Detailed functional explanation

- **Draft.** Production saves the bill as usual. Only QA-approved items can be its product or components (module 1 refuses any other on save).
- **What approval needs:** a finished good or sub-assembly as its product, approved by QA; at least one component; every component approved by QA with a quantity above zero; a process loss between 0 and 100 percent. The form's banner lists what is still missing, or *Ready for QA's approval*.
- **Approval.** QA clicks **Approve BOM**, gives a reason and the password (meaning *Approved*). The bill is submitted by that act, and the signature is taken inside that submit, after ERPNext has validated the bill and every other check has passed. A refused approval keeps nothing: no signature and no submit. A plain Submit is refused for everyone, QA, Production and the Administrator alike, on the form, through the API, by saving or inserting a bill as submitted, or with validation skipped. The form does not offer it.
- **What the signature binds:** the recipe of the device, as stored. That is the product, quantity, unit, company and process loss; the routing, operations (sequence, workstation, times, batch size, semi-finished goods, inspection) and how materials are transferred; the components (quantities, units, conversion, sub-bill, operation, alternatives, explosion, supplier-sourced); secondary items such as scrap and co-products; whether alternatives or inspection are allowed. ERPNext's later cost updates do not alter it. A change to any bound field after signing shows as a change since signing. A field ERPNext renames in an upgrade fails a test.
- **In force and cancelled.** Making another bill the default, or cancelling one, needs an approved change request (URS-MST-001). An amended bill is a new draft awaiting its own signature.
- **Work orders.** A work order is refused unless its bill is approved (submitted), active and of its own product. ERPNext skips its "must be submitted" check while tests run, and accepts a bill that merely contains the product as a component. Production Plan makes its work orders with validation skipped. The app's check runs on every one of them.
- **The form.** A submitted bill shows *Approved by QA*, or *Approved by QA but inactive* when it is no longer active.

## 3. Requirements and acceptance criteria

> **URS-MST-007** A bill of materials is drafted by Production and approved by QA with an electronic signature, which is its only submit, for the Administrator too; QA approves it only when its product is a QA-approved finished good or sub-assembly, it has at least one component, every component is a QA-approved item with a quantity above zero, and its process-loss allowance is between 0 and 100 percent; the signature binds what was approved (product, quantity, components, operations, process loss), not the costs ERPNext updates; a work order is made only from an approved, active bill of its own product.

| # | Criterion | Verification (`master_data/test_bom.py`) |
| --- | --- | --- |
| C1 | Production drafts, QA signs, signature recorded | `test_production_drafts_and_qa_approves_with_a_signature` |
| C2 | Only QA | `test_only_qa_approves` |
| C3 | The signed act is the only submit (QA, Administrator, API) | `test_the_signed_approval_is_the_only_submit` |
| C4 | Wrong password, no reason, approved twice | `test_a_wrong_password_or_no_reason_approves_nothing`, `test_an_approved_bill_is_not_approved_again` |
| C5 | Amended bill needs its own signature | `test_an_amended_bill_is_a_draft_awaiting_its_own_signature` |
| C6 | Product type and approval; components; quantities; process loss | `TestWhatApprovalNeeds.*` |
| C7 | Signature binds the bill as stored, every part of the recipe, not the costs | `TestSignatureBindsTheBill.*` |
| C7a | Plain submit refused also when saved or inserted as submitted, or with validation skipped | `test_the_signed_approval_is_the_only_submit`, `test_a_bill_inserted_as_submitted_is_refused` |
| C7b | An approval refused in the submit leaves no signature | `test_an_approval_refused_after_the_password_leaves_no_signature` |
| C8 | Work order only from an approved, active bill of its product, also when made without validation (Production Plan) | `TestWorkOrders.*` |
| C9 | Pilot and journey approve their bills by the signed act | `pilot/test_standard_flow.py`, `qa/test_ui_journey.py` |
| C10 | On the form: the draft offers no Submit; QA approves with **Approve BOM** and a signature | `cypress/integration/bill_of_materials.js` |

## 4. Step-by-step browser testing guide

### BOM-01 Production drafts a bill
Role: Production Supervisor. Items: an approved finished good (IM-05) and an approved raw material (IM-04).
1. **Manufacturing › BOM › + Add**: Item = the finished good, Quantity 1, a row with the raw material, Qty 2. Save. Expected: saved as a draft; orange banner *Ready for QA's approval*; **no Submit** button.
2. Try a draft item as a component. Expected: refused, *Not yet approved by QA, so not used in BOM: …*

### BOM-02 QA approves with a signature
Role: QA Manager.
1. Open the draft. Expected: **Approve BOM** button.
2. Click it; empty reason → refused; wrong password → refused, still draft.
3. Reason `Components and quantities checked`, correct password. Expected: submitted; green banner *Approved by QA…*; the signature table shows *Approved*.

### BOM-03 What approval needs
Role: QA Manager. A bill whose product is a raw material shows *Still needed: a finished good or sub-assembly as its product* and no Approve button. Process loss 120 % → refused on save by ERPNext, or listed as needed.

### BOM-04 Work orders
Role: Production Supervisor. A work order on a draft bill → refused, *A work order is made from an approved, active bill of materials of …*. On the approved bill → saved.

### BOM-05 Changing the bill in force
Role: QA Manager. Make a second bill default, or cancel the first → refused without an approved change request listing them (VC-04 of the master data guide).

## 5. End-to-end business workflow
Item approved (module 1) → Production drafts the bill → QA signs **Approve BOM** → MRP and work orders use it → a change of the bill in force goes through a change request (module 36) → the old bill stays readable for the device history.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `master_data/bom.py`: `missing_for_approval` (one query for all items), `approve` (savepoint), `sign_approval`, `only_by_approval`, `signed_content` with `SIGNED_FIELDS` / `SIGNED_ROW_FIELDS`, `bom_onload`, `guard_work_order` |
| Hooks | BOM `before_validate` (`only_by_approval`), `before_submit` (`versioning.guard_new_default_bom`), `on_submit` (`sign_approval`, after `versioning.record_displaced_default`), `onload`; Work Order `before_validate` (`bom.guard_work_order`) |
| Signing | `SIGNING_ACTIONS[("BOM", "Approve")]`; `esign.SIGNED_CONTENT["BOM"]` |
| Permissions | Production (Manufacturing User) edit; QA approve (keeps Frappe's submit permission: naming a change request on an approved bill is an update after submit); the plain submit is refused by the hook |
| Desk | `master_data.js`: banner, **Approve BOM**, the plain Submit not offered on a draft |
| Test fixture | `setup/test_stock.py` `submit_bom_for_test` — submits as the act would, for other modules' tests |

## 7. Defects, edge cases and risks
- BOM Creator submits each bill it makes. That submit is refused, so BOM Creator stops: it marks itself Failed and leaves only the first bill it made, as a draft. Bills are made on the BOM form and approved by QA (by design).
- A template item is never approved (module 1), so ERPNext's template bills are not used; each variant has its own.
- Review findings fixed before release: the signature was first taken before ERPNext's validation had filled a component's sub-bill, so it could disagree with the record at once; the rules ran at steps Frappe skips for a record saved without validation (Production Plan's work orders). Both now run at `before_validate`, and the signature is taken in the submit.
- Known gap outside this module: the store checks on a work order (module 17, `lot_status.guard_work_order`) still run at `validate`, which Production Plan's work orders skip. Raised for its own fix.
- First design removed QA's submit permission; that also stopped QA naming a change request on an approved bill (Frappe treats it as an update after submit). Corrected before release: QA keeps the permission and the hook is the control.

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Signed approval only; plain submit refused for all | Verified (automated) |
| Completeness checks before approval | Verified (automated) |
| Signature binds the bill, not costs | Verified (automated) |
| Work orders only from approved, active, own bills | Verified (automated) |
| On-screen draft and signed approval | Verified (browser test, `bill_of_materials.js`) |
| Manual BOM-01…BOM-05 | **Not tested** |
