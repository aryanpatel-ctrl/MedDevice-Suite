# Warehouses (module 7) — feature document and browser test guide

Requirement: **URS-MST-013** (`meddevice_suite/qa/requirements.toml`). Builds on URS-MST-001 (version control of masters) and module 17's store roles (URS-STK-004 to 007).
Code: `meddevice_suite/master_data/warehouse.py`, the shared approval `master_data/master_approval.py`, hooks in `meddevice_suite/hooks.py` (Warehouse `validate`, `onload`; Stock Ledger Entry `validate`; `before_tests`), signed act in `compliance/signing.py`, version control in `master_data/versioning.py`, loaders `pilot/demo.py`, test sites `qa/site_setup.py`, `qa/ui_users.py`.
DFD trace: D4 Inventory & Batch Ledger; master plan section 3 (Warehouse: raw, quarantine, approved, WIP, finished goods, rejected and returns areas; masters approved before use).

## 1. Feature overview

**Purpose.** Stock is kept only in stores QA has approved: their purpose, labelling and conditions checked before the first unit goes in.

**Why it matters.** ISO 13485 6.4 and 7.5.11 ask that product is kept in suitable, controlled conditions. A store set up in the system without QA knowing of it is stock outside the quality system.

| Job profile | Here |
| --- | --- |
| QA Manager (Item Manager) | Sets up stores and approves each with a signature; changes after approval go through change control |
| Everyone moving stock | Moves it only through approved stores |

## 2. Detailed functional explanation

- **Approval.** QA opens a store and clicks **Approve** (meaning *Approved*), with a reason and the password. A group of stores holds no stock and is not approved; a disabled store is not approved. Approval fields are set only by the signature; a new or copied store is in draft.
- **Use.** No stock moves into or out of a store QA has not approved, whatever the document: the check is on the stock ledger, the one place every receipt, issue, transfer, delivery, production entry and stock count (opening stock included) passes. Reversing a movement by cancelling it is not refused.
- **Change control.** Once approved, a store's parent, group, type, disabled flag, company and role (quarantine, hold, rejected, returns, or none for approved stock) change only under an approved change request (URS-MST-001); the signature binds them.
- **Roles** (quarantine, hold, rejected, returns, or none for approved stock) stay module 17's; bin locations within a store are module 18's putaway.
- **Test and pilot sites.** On a pilot site the loader approves the demo stores by QA's signed act. On test sites a test fixture marks the enabled stores approved before the tests run (no signature); the act itself, and stores left unapproved, are tested in this module's own tests.

## 3. Requirements and acceptance criteria

> **URS-MST-013** Each store is approved by QA with an electronic signature, once it is a store that holds stock of a company, and that signature is the only way it becomes approved; no stock moves into or out of a store QA has not approved, whatever the document; once approved, what QA approved changes only under an approved change request.

| # | Criterion | Verification (`master_data/test_warehouse.py`) |
| --- | --- | --- |
| C1 | QA approves with a signature | `test_qa_approves_a_store_with_a_signature` |
| C2 | Only QA, password, reason, once; group and disabled not approved | `test_only_qa_approves_with_password_and_reason_and_once`, `test_a_group_or_a_disabled_store_is_not_approved` |
| C3 | Approval only by signature | `test_the_approval_is_given_only_by_the_signature` |
| C4 | Change control after approval; an approved store stays one that holds stock | `test_what_qa_approved_changes_only_under_change_control`, `test_an_approved_store_stays_one_that_holds_stock` |
| C5 | Nothing comes in or leaves an unapproved store | `TestStockMovesOnlyThroughApprovedStores.*` |
| C6 | Signature binds what QA approved | `test_the_signature_binds_what_qa_approved` |
| C7 | Every other module's stock movements run through approved stores | the full server suite and the browser journey |

## 4. Step-by-step browser testing guide

### WH-01 Approve a store
Role: QA Manager. **Stock › Warehouse**, a new store. Expected: orange banner *Not yet approved by QA… Ready for QA's approval*. **Approve** with a reason and the password → green *Approved by QA*.

### WH-02 Stock only through approved stores
A material receipt into a store not yet approved → refused *Store … is not approved by QA; no stock moves into or out of it*.

### WH-03 Change control
Disable an approved store → refused *is in use* without an approved change request.

## 5. End-to-end business workflow
Store set up → QA checks its area, labels and conditions and approves it → stock moves through it under the lot rules (module 17) → a change to it goes through change control.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `master_data/warehouse.py`: `missing_for_approval`, `approve`, `guard`, `onload`, `guard_stock_ledger_entry` |
| Fields | on Warehouse: `mds_master_approval`, `mds_master_approved_by`, `mds_master_approved_at` |
| Hooks | Warehouse `validate`, `onload`; Stock Ledger Entry `validate` (before the lot rules); `before_tests` → `qa.site_setup.approve_test_stores` (test sites only) |
| Desk | the shared approval banner and **Approve** button (`public/js/master_data.js`) |

## 7. Defects, edge cases and risks
- Independent review findings, fixed with tests before release: a stock count or opening stock of an item without lots put quantity into an unapproved store (ERPNext writes that quantity after validation) (High); a store's role, and its company, could change after approval without a change request or a change to what the signature binds (High, Low); the test fixture approved disabled stores and its description claimed a signature it does not make; a disabled store stopped the pilot loader; one uncached read per ledger entry (Low).
- **An existing site (the pilot, a UAT site) after it migrates to this version:** every store is in draft, so no stock moves until QA approves each store on its form (**Approve**); on the pilot site, loading the demo data again approves the demo stores by signature. Plan the cutover so QA approves the stores first.
- Go-live: QA approves every store once (the company's default stores and the quarantine, hold, rejected and returns stores the configuration makes) before the first receipt; until then nothing can be received. This is intended.
- Storage conditions per store checked against the item's storage condition are not in the plan or the DFD; raised with the client before anything is built.

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Signed approval of stores | Verified (automated) |
| Stock only through approved stores | Verified (automated) |
| Change control after approval | Verified (automated) |
| Manual WH-01…WH-03 | **Not tested** |
