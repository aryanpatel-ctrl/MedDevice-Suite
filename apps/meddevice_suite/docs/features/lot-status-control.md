# Lot status control (module 17) — feature document and browser test guide

Requirements: **URS-STK-004, URS-STK-005, URS-STK-006, URS-STK-007** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-QC-003 (the signed decision that changes a status, module 16), URS-STK-002 (expiry-first picking sees only approved stores, module 18).
Code: `meddevice_suite/lot_control/lot_status.py`, `lot_control/stores.py`, `lot_control/report/stock_by_lot_status/`, `patches/v0_1/set_initial_lot_status.py` (listed in `patches.txt` under `[post_model_sync]`), hooks in `meddevice_suite/hooks.py`, permissions in `setup/roles.py`; the decision record itself is module 16 (`lot_control/release.py`, `doctype/lot_release/`).
State on 2026-10-10: built (commits 2684416a, f4c12685, 492db989 and the origin/new-lot fixes of 2026-10-09/10), reviewed with modules 4 and 16, server and browser suites green locally on the complete code; GitHub CI on this branch was in progress when this document was written (section 8).

## 1. Feature overview

**Purpose.** Every lot is Quarantine, Approved, Rejected or Hold. A lot is born Quarantine, whether a supplier delivered it or a work order made it, and only QA moves it on, with a reason and an electronic signature, through a Lot Release; nobody edits the status on the lot, not on the form, not through the API, not the Administrator. Only an Approved lot can be issued to production, moved to an approved store, picked or delivered. Each status has a store of its own (Quarantine, Hold, Rejected, Returns), so what is not approved is physically and logically apart from approved stock, and the lot and the stock reports say who set its status, when and why. Stock comes into a lot only on the document that made it: more stock, a count gain or opening stock is a new lot. At cutover, every lot that already exists is given a status from the store holding it, by a migration recorded in the audit trail.

**Why it matters.** ISO 13485 7.5.8 (identification and status of product) and 8.3 (control of nonconforming product); 21 CFR 11.50 (the status change is signed); D2 Quarantine / Approved Stock and Material Issue (From Approved Stock); D3 QC Approval / Rejection, Putaway in Warehouse (Batch/Location); D4 3.5 Accept / Reject / Hold, 3.3 Internal Batch Creation, 4.6 Batch Creation. The rule "no code path issues, consumes or dispatches stock from a lot that is not Approved" is a non-negotiable of `rules.md` section 5.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| Stores (`stores@demo.example.com`) | Stock User | Receives into Quarantine; moves, issues, picks and delivers Approved lots; is refused anything else, plainly |
| Production Supervisor (`production.supervisor@demo.example.com`) | Manufacturing User | Draws materials from an approved store; finishes into Quarantine |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Sets a status only by signing a Lot Release; sets a store's **Store Role** (Item Manager edits warehouses) |
| QC Inspector (`qc.inspector@demo.example.com`) | QC Inspector | Reads lots and the stock report |
| Management (`management@demo.example.com`) | Stock Manager (+ others) | Counts stock (Stock Reconciliation); reads the report |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Runs `bench migrate` (cutover patch); reads only |

**Prerequisites.** The app's configuration (`bench migrate` → `apply_configuration`): store roles and one store per role per company (`apply_store_roles`), the status fields on Batch (`apply_lot_status`), the permission matrix (`apply_roles`); the e-signature service (module 49) and the Lot Release record (module 16). Every item that is lot-controlled has an approved default store (**Item › Item Defaults › Default Warehouse**, a store without a role) for the release to move it to.

## 2. Detailed functional explanation

### 2.1 The status on the lot (URS-STK-004, URS-STK-006)

Navigation: **Stock › Batch No › <lot>** (`/desk/batch/<lot>`). Section **Lot Status** (after Expiry Date):

| Field (label) | Set by | Rule |
| --- | --- | --- |
| Lot Status | the release service only | *Quarantine* / *Approved* / *Rejected* / *Hold*; default Quarantine; in the list view and standard filters; description *Changed only by QA through a lot release or disposition.* |
| Status Set By, Status Set At, Status Reason, Latest Decision | the release service only | Who, when, why (plain text, at most 1000 characters) and the Lot Release that set it |

A new lot is Quarantine whatever was sent with it (status fields sent on insert are discarded). Any change of these five fields by the form or the API is refused, for everyone: *The status of lot L changes only through a QA release or disposition.* Other fields of the lot (Batch Description, Expiry Date) stay editable by whoever may write a Batch (Item Manager; Stores and Production only create lots through their transactions). The one writer is `set_status`, called by the signed decision (module 16) after its own checks; it records the change in the audit trail with the reason (*Updated* event on the Batch with the old and new status). A lot made before the status existed counts as Quarantine.

### 2.2 Stores by role (URS-STK-005)

Navigation: **Stock › Warehouse › <store>** (`/desk/warehouse/<name>`). Field **Store Role**: empty (an approved store), *Quarantine*, *Hold*, *Rejected* or *Returns*; shown in the list and filters; description *Which lot status this store holds. A store with no role holds approved stock.* Each company gets one store of each role, created under the company's root store and named after the role (`Quarantine - <abbr>`, `Hold - <abbr>`, `Rejected - <abbr>`, `Returns - <abbr>`) as soon as the company exists, or on the next migrate; an existing store already named that way takes the role instead. The client may rename a store; the role stays.

Rules on a warehouse: one store per role per company (*X is already the Quarantine store of <company>.*); a group cannot carry a role (*A Quarantine store holds stock itself; a group of stores cannot be one.*); a store holding stock keeps its role (*X holds stock; move the stock before changing what the store is for.*). A missing role store is refused plainly when needed (*<company> has no Quarantine store; run the site configuration.*).

### 2.3 Only an Approved lot leaves (URS-STK-005)

The rule is enforced at the **Stock Ledger Entry** (`guard_stock_ledger_entry`), where every movement passes with its document stored, and earlier on the forms so the person is refused before submitting: Stock Entry (`guard_stock_entry`), Delivery Note (`guard_delivery_note`), Pick List (`guard_pick_list`), Work Order (`guard_work_order`), Purchase Receipt (`guard_purchase_receipt`). The message, title *Lot not approved*:

> *Lot L is Quarantine and cannot leave <store>; only an Approved lot can be issued, moved, picked or delivered. QA releases it after incoming inspection.*

with the hint *QA releases or rejects it.* for Hold and *It can only be returned to its supplier or scrapped.* for Rejected.

What a lot that is not Approved may still do (`movement_allowed`):

| Status | Allowed | Everything else |
| --- | --- | --- |
| Approved | any movement | — |
| Quarantine | Material Transfer into the Quarantine store; a stock count (Stock Reconciliation) | refused |
| Hold | Material Transfer into the Hold store; a count | refused |
| Rejected | Material Transfer into the Rejected or Returns store; Material Issue (scrap); a purchase return (Purchase Receipt / Purchase Invoice with Is Return) to the supplier; a count | refused |
| any | the transfer a Lot Release posts (`mds_lot_release_transfer` set by the release, within its own transaction) | — |

Further rules: goods are received into the company's Quarantine store whatever store the form named (set, not refused) and quantity rejected at the door goes to the Rejected store (*Line N: quantity rejected at receipt goes to the Rejected store.*); a work order's Source Warehouse and Work-in-Progress Warehouse must be approved stores (*X is a Quarantine store; a work order draws materials from an approved store.*) and its Target Warehouse must be the Quarantine store (*The finished lot goes to the Quarantine store until QA releases it; X is not one.*); only Stores and Production post stock entries by hand (*Stock is posted by Stores and Production; QA moves a lot through its release.*); only a release marks its transfer (*Only a lot release posts a lot release transfer.*); a release's transfer is never cancelled (*SE was posted by a lot release and is not cancelled; put the lot on hold instead.*). A cancelled document's reversal entries are not checked (they undo, they do not move).

### 2.4 Stock into a lot only on the document that made it (URS-STK-004, decided 2026-10-09)

On every inward ledger entry (`guard_inward`): a lot already in stock accepts more stock only from the document that first brought it in. A material receipt, a goods receipt or a count gain into an existing lot is refused: *Lot L came in on <document> <name>; new stock comes in as a new lot.* (title *New lot needed*). Excepted: a transfer between stores, goods coming back on a return, and a stock count that puts back no more than the same line took out (a loss, an unchanged line, a corrected valuation). The goods receipt form says the same earlier (module 16, `refuse_existing_lots`).

### 2.5 The decision moves the stock (URS-STK-004, URS-STK-005)

A signed Lot Release (module 16) calls `move_to_role` then `set_status` in one transaction: one Material Transfer, posted under the QA user (so the audit trail names the person who decided), moves all the lot's stock from every store holding some into the store of the new status — the item's default store for Approved (it must be a store without a role: *Item X has no approved default store for <company>; set it on the item or name the store.*), the Hold or Rejected store otherwise. Nothing is posted when the stock is already there (e.g. opening stock loaded straight into the approved store, released without a transfer). The transfer carries **Posted by a Lot Release** and the reason as Remarks. A lot with stock in more than one company is refused (*Lot L has stock in more than one company; move it by hand first.*).

### 2.6 Stock by Lot Status (URS-STK-006)

**Stock › Stock by Lot Status** (`/desk/query-report/Stock by Lot Status`); filters **Lot Status**, **Item**, **Store**. One row per item, lot and store with stock: Item, Lot, Lot Status, Store, Quantity, Expiry, Status Set By, Status Set At, Status Reason. Old-style lots (lot on the ledger entry) and bundled lots (ERPNext v16's Serial and Batch Bundle) are counted alike in two grouped queries (`stock_by_lot`). Readable by Stock, Quality, QC, Purchase, Manufacturing, Sales, Accounts users and managers, Item Manager and System Manager. The Batch list itself can be filtered by Lot Status.

### 2.7 Cutover: initial status (URS-STK-007)

Patch `meddevice_suite.patches.v0_1.set_initial_lot_status` runs once at `bench migrate` after the schema sync. It applies the store roles and status fields itself (patches run before the after-migrate configuration), then for every lot whose Status Set At is empty: stock in a Rejected store → Rejected; else in a Hold store → Hold; else in a Quarantine store → Quarantine; else (approved stores only) → Approved; a lot with no stock → Quarantine. Each change goes through `set_status`, so it is an audit event with the reason *Migration: initial lot status from the store holding the lot*. Running it again changes nothing for lots already set.

### 2.8 What is intended but not implemented

- A disposition record of its own (NCR / rework / use-as-is, module 33) — today the only dispositions are Release, Reject and Hold on the Lot Release.
- Packing Slip is guarded on save as well (2026-10-10): a slip of a lot that is not Approved is refused, for the lines of a Delivery Note and for the packed items of a product bundle; the stock movement is guarded at the ledger too.
- Returning a Quarantine or Hold lot to the supplier directly: only a Rejected lot goes back (section 3, open point).

## 3. Requirements and acceptance criteria

> **URS-STK-004** Every lot carries a status, Quarantine, Approved, Rejected or Hold; a lot received or produced starts in Quarantine, and the status changes only through a signed QA release or disposition, never by editing the lot; stock comes into a lot only on the document that made it, lots moved between stores and returns aside, so more stock, a count gain or opening stock comes in as a new lot.
> **URS-STK-005** No stock of a lot that is not Approved can be issued to production, transferred to an approved store, picked, packed or delivered, through the form or the API; a Quarantine, Hold or Rejected lot moves only into the quarantine, hold, rejected or returns stores, or back to its supplier.
> **URS-STK-006** A lot shows its status, who set it, when and why, and the stock reports show quantities by lot status, so Stores and QA see at any time what is in quarantine, on hold, rejected or approved.
> **URS-STK-007** When lot status is installed on a site that already holds stock, every lot is given a status by a migration recorded in the audit trail: lots in the approved and finished goods stores become Approved, every other lot Quarantine.

| # | Acceptance criterion | Implementation | Automated verification (`lot_control/test_lot_status.py` unless noted) |
| --- | --- | --- | --- |
| C1 | Four statuses; a new lot is Quarantine whatever it was sent with; a received lot is Quarantine | `guard_batch`, `STATUSES` | `TestALotIsBornInQuarantine.test_a_received_lot_starts_in_quarantine_whatever_it_was_sent_with`, `test_a_lot_from_a_purchase_receipt_is_quarantine`; `test_release.py` `test_a_wrong_password_decides_nothing` |
| C2 | Status never edited on the lot or via the API, not even by QA; other fields still editable | `guard_batch` | `test_the_status_cannot_be_edited_on_the_lot`, `test_the_status_cannot_be_set_through_the_api_even_by_qa`, `test_other_fields_of_the_lot_can_still_be_edited_by_qa` |
| C3 | The service records who, when, why in the audit trail; refuses empty, unknown, endless | `set_status`, `clean_reason` | `test_the_service_records_who_when_and_why_in_the_audit_trail`, `test_the_service_refuses_an_empty_reason_an_unknown_status_and_an_endless_reason` |
| C4 | Status changes only by the signed decision | module 16 controller and `release.decide` | `test_release.py` `TestTheSignedDecision.*` |
| C5 | Not Approved: cannot be issued, moved to an approved store, delivered, picked; refused at the ledger whatever the form let through | `guard_stock_entry`, `guard_delivery_note`, `guard_pick_list`, `guard_stock_ledger_entry` | `TestOnlyAnApprovedLotLeaves.test_a_quarantined_lot_cannot_be_issued`, `…cannot_be_moved_to_the_approved_store`, `…cannot_be_delivered`, `…cannot_be_picked`, `test_the_ledger_refuses_what_a_form_let_through` |
| C6 | What a non-approved lot may still do: own-role store, returns store, scrap, purchase return | `movement_allowed` | `test_what_a_lot_may_still_do_within_its_own_kind_of_store`, `test_a_held_lot_waits_in_the_hold_store`, `test_a_rejected_lot_goes_back_to_its_supplier_or_is_scrapped_and_nowhere_else` |
| C7 | Approved lot is issued, moved, delivered; can be put on hold and comes back to the hold store | `movement_allowed`, `DECISIONS` | `test_an_approved_lot_is_issued_moved_and_delivered`, `test_an_approved_lot_can_be_put_on_hold_and_comes_back_to_the_hold_store` |
| C8 | Work order draws from an approved store and finishes into Quarantine; receipts land in Quarantine; door rejects go to the Rejected store | `guard_work_order`, `guard_purchase_receipt` | `test_a_work_order_draws_from_an_approved_store_and_finishes_into_quarantine`, `test_goods_are_received_into_the_quarantine_store_whatever_store_was_named`, `test_quantity_rejected_at_the_door_goes_to_the_rejected_store` |
| C9 | The decision moves the stock in one transfer under QA; QA posts nothing else; the transfer is never cancelled; no transfer when already there; approved default store required; two companies refused | `move_to_role`, `approved_store`, `refuse_cancel_of_release_transfer` | `TestTheDecisionMovesTheStock.*`, `test_only_the_release_marks_its_transfer` |
| C10 | Stock into a lot only on its own document; count loss / no change allowed; gain and new receipts refused; delivery as a new lot | `guard_inward`, `counted_back`; module 16 `refuse_existing_lots` | `test_release.py` `TestWhereALotCameFrom.test_no_stock_is_added_to_a_lot_already_in_stock`, `test_a_count_records_a_loss_or_no_change_on_approved_lots`, `test_a_delivery_does_not_come_in_on_a_lot_that_holds_stock`, `test_a_lot_scanned_on_the_receipt_is_a_new_lot_of_that_receipt`, `test_an_amended_receipt_brings_its_lot_in_again`; `test_the_ledger_refuses_what_a_form_let_through` |
| C11 | Report by lot status with store, quantity, who/when/why; old-style and bundled lots alike | `stock_by_lot`, report | `TestStockByLotStatus.test_the_report_shows_each_lot_with_its_status_store_and_reason`, `test_stock_by_lot_counts_old_style_and_bundled_lots_alike` |
| C12 | One store per role per company, created when the company exists; a second refused; no group; rename keeps the role; stock keeps the role; missing store refused plainly | `stores.py` | `test_stores.py` `TestStoreRoles.*` (11 tests) |
| C13 | Cutover: status from the store, most cautious wins, audited, idempotent | patch, `initial_status` | `TestInitialStatusAtCutover.test_lots_take_their_status_from_the_store_holding_them`, `test_running_the_patch_again_changes_nothing`, `test_the_most_cautious_store_decides` |
| C14 | Browser: Stores is refused the move plainly; QA's release approves and moves; Stores cannot move the lots again; the finished lot waits in Quarantine | `lot_status.py` messages, journey | `cypress/integration/standard_flow.js` steps "[URS-STK-005] Stores cannot move the quarantined lots to the approved store; the form refuses it plainly", "[URS-STK-004] [URS-QC-003] QA releases each inspected lot…", "[URS-STK-003] Stores cannot move the released lots out of quarantine again, for none is left", "[URS-STK-004] [URS-QC-004] QA releases the finished lot…" |

**Partial / ambiguous (client to decide):**
- URS-STK-005 "packed": fixed 2026-10-10 — a packing slip of a lot that is not Approved is refused on save (`guard_packing_slip`; `test_a_lot_held_after_the_note_was_saved_cannot_be_packed`).
- URS-STK-005 "or back to its supplier": only a **Rejected** lot may go back (purchase return from the Rejected store); a Quarantine or Hold lot is rejected first. Confirm this reading.
- URS-STK-007 "lots in the approved and finished goods stores become Approved": implemented as "any store without a role" (Finished Goods, Stores, Work In Progress all count), the most cautious role store deciding when stock is split.
- A stock count (Stock Reconciliation, Stock Manager) is allowed on any lot (a counted correction, audited); it is not an issue, but the client should confirm counts of quarantined stock are acceptable.

## 4. Step-by-step browser testing guide

Site and users as in the module 16 document. Demo data: item **RM-SILICONE** (default store `Stores - <abbr>`), product **FC-2W-SIL-16** (default store `Finished Goods - <abbr>`), supplier **Silicone Compounds (Demo)**, stores `Quarantine - <abbr>`, `Hold - <abbr>`, `Rejected - <abbr>`, `Returns - <abbr>`, `Work In Progress - <abbr>`. Make lots as in the module 16 guide (LR-01: a Quarantine lot; LR-06: an Approved lot). Never use real data.

### LS-01 A new lot is Quarantine and its status cannot be edited (C1, C2, C3)
1. As **Stores**: receive goods (module 16 LR-01). Open the lot (`/desk/batch/<lot>`). Expected: section **Lot Status**: Lot Status = Quarantine; Status Set By / At / Reason / Latest Decision empty; Lot Status read-only.
2. As **QA Manager**: open the lot; call `frappe.client.set_value` on Batch with `mds_lot_status` = `Approved` (browser console or REST). Expected: refused *The status of lot … changes only through a QA release or disposition.*
3. As QA: edit Batch Description = `silicone, drum 3`; Save. Expected: saved (other fields stay editable).
4. As **System Administrator**: repeat step 2. Expected: refused with the same message.
5. **Stock › Batch No › New** as QA: Item RM-SILICONE, Batch ID `TEST-STATUS-1`, Expiry Date = today + 30; Save. Expected: Lot Status = Quarantine (the form offers no way to set it).
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of step 1.

### LS-02 Stores is refused every movement of a Quarantine lot (C5)
Role: Stores. Preconditions: Quarantine lot L1 with stock in `Quarantine - <abbr>`.
1. **Stock › Stock Entry › New**, Stock Entry Type `Material Transfer`, line RM-SILICONE, Source Warehouse `Quarantine - <abbr>`, Target Warehouse `Stores - <abbr>`, Batch No L1 (tick Use Serial No / Batch Fields), Qty 1; Save. Expected: refused, title *Lot not approved*: *Lot L1 is Quarantine and cannot leave Quarantine - <abbr>; only an Approved lot can be issued, moved, picked or delivered. QA releases it after incoming inspection.*
2. Same with Stock Entry Type `Material Issue`. Expected: refused likewise.
3. **Stock › Delivery Note › New**, Customer *City Hospital (Demo)*, line RM-SILICONE qty 1, Warehouse `Quarantine - <abbr>`, Batch No L1; Save. Expected: refused likewise.
4. **Stock › Pick List › New**, Purpose `Material Transfer`, line RM-SILICONE qty 1, Warehouse `Quarantine - <abbr>`, Batch No L1; Save. Expected: refused likewise.
5. Confirm step 1's message names the lot, its status and the store it cannot leave, and that nothing was saved (the form is still new).
Actual result: ____  Pass/Fail: ____

### LS-03 Hold and Rejected lots (C6, C7)
Preconditions: a Hold lot L2 (module 16 LR-08 step 2) and a Rejected lot L3 (LR-07).
1. As Stores: Material Transfer L2 from `Hold - <abbr>` to `Stores - <abbr>`. Expected: refused *… is Hold and cannot leave …; … QA releases or rejects it.*
2. As Stores: Material Transfer L3 from `Rejected - <abbr>` to `Stores - <abbr>`. Expected: refused *… is Rejected … It can only be returned to its supplier or scrapped.*
3. As Stores: Material Transfer L3 from `Rejected - <abbr>` to `Returns - <abbr>`, qty 1; Submit. Expected: allowed.
4. As Stores: Material Issue L3 from `Rejected - <abbr>` qty 1; Submit. Expected: allowed (scrap).
5. As Stores: open the draft return of LR-07 (**Return to Supplier**); Submit. Expected: allowed; stock of L3 leaves `Rejected - <abbr>`.
6. As Stores: on an Approved lot L4, Material Transfer `Stores - <abbr>` → `Work In Progress - <abbr>` qty 2; Material Issue qty 1; Delivery Note qty 1. Expected: all allowed.
Actual result: ____  Pass/Fail: ____

### LS-04 Receipts and work orders land in the right stores (C8)
1. As Stores: new Purchase Receipt with **Accepted Warehouse** `Stores - <abbr>` on the header and line; Save. Expected: both set back to `Quarantine - <abbr>`.
2. Line with Received Quantity 2, Accepted Quantity 1, Rejected Quantity 1, Rejected Warehouse `Hold - <abbr>`; Save. Expected: refused *Line 1: quantity rejected at receipt goes to the Rejected store.*; with `Rejected - <abbr>`: saved.
3. As **Production Supervisor**: **Manufacturing › Work Order › New**, Item To Manufacture FC-2W-SIL-16, Qty 100, Source Warehouse `Quarantine - <abbr>`; Save. Expected: refused *Quarantine - <abbr> is a Quarantine store; a work order draws materials from an approved store.*
4. Source Warehouse `Stores - <abbr>`, Work-in-Progress Warehouse `Work In Progress - <abbr>`, Target Warehouse `Finished Goods - <abbr>`; Save. Expected: refused *The finished lot goes to the Quarantine store until QA releases it; Finished Goods - <abbr> is not one.*
5. Target Warehouse `Quarantine - <abbr>`; Save, Submit. Expected: saved.
Actual result: ____  Pass/Fail: ____

### LS-05 The decision moves the stock; QA posts nothing else (C9)
1. As QA: release a ready lot (module 16 LR-06). Open **Stock Moved By**. Expected: Material Transfer `Quarantine - <abbr>` → `Stores - <abbr>`, full quantity, **Posted by a Lot Release** ticked, Remarks = the reason; its audit trail names the QA user.
2. As QA: **Stock › Stock Entry › New**, Material Transfer of that lot `Stores - <abbr>` → `Work In Progress - <abbr>`; Save. Expected: refused *Stock is posted by Stores and Production; QA moves a lot through its release.*
3. As Stores: new Stock Entry, tick **Posted by a Lot Release** (if the field is reachable; it is read-only) or set it via the API; Save. Expected: refused *Only a lot release posts a lot release transfer.*
4. As **Management** (Stock Manager): open the release's transfer, **Cancel**. Expected: refused *… was posted by a lot release and is not cancelled; put the lot on hold instead.*
5. As QA: Lot Release on an item whose Default Warehouse is `Quarantine - <abbr>` (make a throw-away item `TEST-NO-STORE` with that default and an incoming specification), after an accepted inspection; sign Release. Expected: refused *Item TEST-NO-STORE has no approved default store for <company>; set it on the item or name the store.*
Actual result: ____  Pass/Fail: ____

### LS-06 Stock into a lot only on its own document; counts (C10)
Preconditions: Approved lot L4 with 6 in `Stores - <abbr>`.
1. As Stores: Material Receipt line RM-SILICONE qty 5, Target Warehouse `Stores - <abbr>`, Batch No L4; Submit. Expected: refused, title *New lot needed*: *Lot L4 came in on Purchase Receipt …; new stock comes in as a new lot.*
2. As Management: **Stock › Stock Reconciliation › New**, line RM-SILICONE, Warehouse `Stores - <abbr>`, Batch No L4, Quantity 5 (a loss), Valuation Rate 1450; Submit. Expected: allowed; L4 holds 5.
3. Same with Quantity 5 and Valuation Rate 1500 (a corrected value). Expected: allowed; still 5.
4. Same with Quantity 7 (a gain). Expected: refused *… new stock comes in as a new lot.*
5. As Stores: Material Transfer L4 `Stores - <abbr>` → `Work In Progress - <abbr>` qty 1. Expected: allowed (moved stock is the same stock).
Actual result: ____  Pass/Fail: ____

### LS-07 Store roles (C12)
Role: QA Manager (Item Manager). 
1. **Stock › Warehouse** list: filter Store Role = Quarantine. Expected: exactly one per company, `Quarantine - <abbr>`, not a group.
2. New Warehouse `Second Quarantine`, Store Role Quarantine; Save. Expected: refused *Quarantine - <abbr> is already the Quarantine store of <company>.*
3. New Warehouse `Hold Group`, **Is Group Warehouse** ticked, Store Role Hold; Save. Expected: refused *A Hold store holds stock itself; a group of stores cannot be one.*
4. Open `Quarantine - <abbr>` (holds stock), clear Store Role; Save. Expected: refused *Quarantine - <abbr> holds stock; move the stock before changing what the store is for.*
5. Rename `Hold - <abbr>` to `QA Hold Area - <abbr>` (menu Rename). Expected: role kept; a Hold decision still moves stock there.
6. As **System Administrator**: create a company `Test Company Two` (abbr `TC2`). Expected: `Quarantine - TC2`, `Hold - TC2`, `Rejected - TC2`, `Returns - TC2` exist at once.
Actual result: ____  Pass/Fail: ____  Cleanup: rename the store back; disable the test company.

### LS-08 Stock by Lot Status (C11)
1. As Stores: **Stock › Stock by Lot Status**, Lot Status = Hold. Expected: the Hold lot with Store `Hold - <abbr>`, Quantity, Status Set By = qa.manager, Status Set At, Status Reason; Approved lots absent.
2. Filter Item = RM-SILICONE, Store = `Stores - <abbr>`. Expected: only Approved lots held there, with Expiry.
3. As QA: same report from **Stock**. As **QC Inspector**: same. Expected: readable.
4. **Stock › Batch No** list, filter Lot Status = Quarantine. Expected: the quarantined lots.
Actual result: ____  Pass/Fail: ____

### LS-09 Permissions (C2, C9)
1. As **Stores**: open a lot; try to edit Batch Description. Expected: no write permission (Stock User creates lots through transactions only).
2. As **QC Inspector**: open a lot. Expected: read-only.
3. As **Purchase**: **Stock › Stock Entry › New**. Expected: not permitted.
Actual result: ____  Pass/Fail: ____

### LS-10 Cutover patch (C13)
Preconditions: a test site only, prepared with lots in `Stores - <abbr>`, `Quarantine - <abbr>`, `Hold - <abbr>`, `Rejected - <abbr>` and one lot with stock split between Stores and Hold, all with Status Set At empty (made before the patch, or cleared by the Administrator on a test site).
1. Run `bench --site <test-site> execute meddevice_suite.patches.v0_1.set_initial_lot_status.execute`. Expected: Stores → Approved, Quarantine → Quarantine, Hold → Hold, Rejected → Rejected, split → Hold, no stock → Quarantine; each lot's Status Reason = *Migration: initial lot status from the store holding the lot*; each lot's **Audit Trail** shows an *Updated* event with that reason.
2. Run it again. Expected: Status Set At unchanged on every lot.
Actual result: ____  Pass/Fail: ____

### Regression
- Module 16 LR-06 to LR-08 (release, reject, hold) and the full journey still pass.
- Module 18: expiry-first picking from `Stores - <abbr>` still picks the earliest-expiring Approved lot (journey step "[URS-STK-002] Production plans the work order…").

## 5. End-to-end business workflow

1. **Stores** receives 10 Kg RM-SILICONE → lot `RM-SILICONE-26-10-0001`, Quarantine, in `Quarantine - <abbr>`. Stores tries to move it to `Stores - <abbr>` → refused *Lot not approved*.
2. **QC** verifies and inspects (module 16). **QA** signs **Release** → Material Transfer Quarantine → Stores under the QA user; lot Approved with Status Set By/At/Reason and Latest Decision LR-2026-00001.
3. **Production** raises a work order (Source `Stores - <abbr>`, WIP `Work In Progress - <abbr>`, Target `Quarantine - <abbr>`); the Approved lot is issued; the finished lot `FC-2W-SIL-16-26-10-0001` lands in Quarantine.
4. A customer complaint: **QA** signs **Hold** on an Approved lot → Material Transfer Stores → Hold; Stores cannot issue it; after a new accepted inspection QA signs **Release** → back to Stores.
5. A failed lot: **QA** signs **Reject** → Rejected store; Stores moves it to `Returns - <abbr>` or **Purchase** submits the purchase return; or Stores scraps it by Material Issue.
6. Month-end count by **Management**: losses and unchanged lines are posted; a gain is refused and comes in as a new lot via a Material Receipt, inspected and released.
7. At cutover the migration set every existing lot's status from its store, audited; **Stock by Lot Status** shows the position at any time.

Cross-module data changes: Batch (`mds_lot_status`, `mds_status_set_by`, `mds_status_set_at`, `mds_status_reason`, `mds_lot_release`), Warehouse (`mds_store_role`), Stock Entry (`mds_lot_release_transfer`), Stock Ledger Entry (guarded), Lot Release (module 16), Audit Event.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Custom fields | Batch: `mds_lot_status_section`, `mds_lot_status` (Select, default Quarantine, indexed), `mds_status_set_by`, `mds_lot_status_column`, `mds_status_set_at`, `mds_status_reason`, `mds_lot_release` (Link Lot Release) — `apply_lot_status()`; Warehouse: `mds_store_role` (Select Quarantine/Hold/Rejected/Returns) — `apply_store_roles()`; Stock Entry: `mds_lot_release_transfer` (Check, read-only). All from `setup/install.py apply_configuration` |
| Server modules | `lot_control/lot_status.py`: `guard_batch`, `status_of`, `set_status`, `clean_reason`, `movement_allowed`, `refuse`, `lots_of_bundle`, `lots_of_row`, `guard_stock_ledger_entry`, `guard_inward`, `counted_back`, `guard_stock_entry`, `refuse_cancel_of_release_transfer`, `guard_delivery_note`, `guard_pick_list`, `guard_work_order`, `guard_purchase_receipt`, `locked`, `move_to_role`, `holdings_of`, `approved_store`, `stock_by_lot`, `initial_status`; constants `STATUSES`, `ROLE_FOR_STATUS`, `STOCK_POSTING_ROLES`, `MAX_REASON` = 1000, `LotNotApproved`. `lot_control/stores.py`: `apply_store_roles`, `ensure_role_stores`, `stores_for_new_company`, `root_store`, `store_for`, `role_of`, `protect_store_role`, `holds_stock`; `ROLES` |
| Hooks (`hooks.py`) | Batch `validate`: `alerts.reset_expiry_alert`, `stock.refuse_negative_stock`, `lot_status.guard_batch`; Warehouse `validate`: `stores.protect_store_role`; Company `on_update`: `stores.stores_for_new_company`; Stock Ledger Entry `validate`: `guard_stock_ledger_entry`; Stock Entry `validate`: `guard_stock_entry`, `before_cancel`: `refuse_cancel_of_release_transfer`; Delivery Note `validate`: `guard_delivery_note`; Pick List `validate`: `guard_pick_list`; Work Order `validate`: `guard_work_order`; Purchase Receipt `validate`: `guard_purchase_receipt` (first of four) |
| Patch | `patches/v0_1/set_initial_lot_status.py` (`[post_model_sync]` in `patches.txt`); `REASON` constant |
| Report | `Stock by Lot Status` (Script Report, ref Batch; `stock_by_lot_status.py` `execute`; filters `lot_status`, `item_code`, `warehouse`) |
| Permissions (`setup/roles.py`) | Batch: Item Manager EDIT; Stock User and Manufacturing User VIEW + create (`CREATED_THROUGH_TRANSACTIONS`); Purchase/Sales/QC/Accounts VIEW; QA, Management, System Manager via `oversight`. Warehouse: Item Manager EDIT, others VIEW/select. Stock Entry: Stock User CORRECT, Stock Manager APPROVE, Manufacturing User CORRECT, Quality Manager REVIEW + write/create/submit (`POSTED_BY_SERVICES`). Stock Reconciliation: Stock Manager APPROVE. Pick List, Delivery Note: Stock User CORRECT, Stock Manager APPROVE (+ Manufacturing User / Sales User ENTER) |
| Desk | Stock sidebar additions: Lot Release, Lots Awaiting Release, Stock by Lot Status (`setup/sidebars.py`); Stock area visible to stock, purchase, sales, production, quality roles and Item Manager (`setup/desk.py`) |
| Compliance registries | Batch, Warehouse, Stock Entry, Stock Reconciliation, Pick List, Delivery Note, Packing Slip, Serial and Batch Bundle regulated (`audit_scope.py`); Batch, Stock Entry, Stock Reconciliation, Serial and Batch Bundle, Pick List in *Device and production records* (10 years), Delivery Note and Packing Slip in *Distribution records* (`retention.py`) |
| Jobs | none |
| Data flow | Form save → `guard_*` (statuses in one query via `status_of`) → refuse or continue; submit → ERPNext posts Stock Ledger Entries → `guard_stock_ledger_entry` (outward: `movement_allowed` with the stored document's purpose, return flag, target store role, release flag; inward: `guard_inward` → `origins_of` → `counted_back`). Release → `move_to_role` (holdings from `get_batch_qty`, one transfer, `frappe.flags.lot_release_transfer` set during submit) → `set_status` (row lock, `flags.lot_control`, audited save) |

## 7. Defects, edge cases and risks

**Confirmed and fixed during development and review (all with tests):** a new site's company had no Quarantine store until the next migrate (f4c12685: stores made on company creation; empty and null role read alike); stock could be added to an existing lot by a material receipt or a count gain and would take its status uninspected (26ceb59e: refused at the ledger); every stock count of an existing lot was then refused as new stock (17fc4cce: a loss or no change passes); the count check summed a ledger entry's batch field and its bundle (96090f29: counted one way); a lot's origin was read from the batch's own reference (314f3e61, module 16).

**Potential (not verified by a test, listed for the record):**
- Packing Slip guarded on save since 2026-10-10 (section 3).
- `guard_pick_list` refuses any status other than Approved, without the `movement_allowed` exceptions; a pick list of a Rejected lot to the Returns store is refused — acceptable, a return is not picked.
- Stores may transfer an **Approved** lot into the Hold or Rejected store (any movement of an Approved lot is allowed); the status stays Approved and the lot can still be issued from there. A hold is QA's signed act, not a physical move.
- An expired Approved lot is not refused by this module (expiry is module 18 / the alerts of module 51); ERPNext excludes expired lots only from its automatic picking and from manufacture entries.
- Re-running the cutover patch by hand on a live site (`bench execute`) would give post-cutover lots with Status Set At empty (every new lot) a status from their store: a lot that a Lot Release never touched but that sits in an approved store would become Approved without a signature. Frappe runs the patch once; nobody should run it by hand after cutover.
- `stores_for_new_company` relies on ERPNext having created the company's root store first (its own `on_update` runs before ours); verified by `test_a_company_created_after_the_configuration_has_its_role_stores_at_once`, not by a test that removes the root store.

**Unverified in this document:** whether ERPNext's form offers the read-only **Posted by a Lot Release** field at all (LS-05 step 3 is therefore worded for the API); the exact label of the rename action in the desk menu.

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch, local test site `test.localhost`, macOS:

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (2026-10-10) |
| Module tests (part of the suite) | `lot_control/test_lot_status.py`: `TestALotIsBornInQuarantine` (7), `TestOnlyAnApprovedLotLeaves` (13), `TestTheDecisionMovesTheStock` (6), `TestStockByLotStatus` (2), `TestInitialStatusAtCutover` (3); `lot_control/test_stores.py` `TestStoreRoles` (11); the origin and count tests in `test_release.py` `TestWhereALotCameFrom` (8) — counts read from the files | passed within the suite above |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed (2026-10-10, complete code); the four journey steps of C14 among them; the screen sweep opens the Batch list, the Lot Release list and Stock by Lot Status as each job that sees the Stock area |
| Semgrep (Frappe rules + python correctness) | — | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| CI (fresh site) | GitHub Actions on this branch | **in progress** when this document was written |
| Manual browser tests LS-01…LS-10 | — | **not executed** (no manual tester session); the automated journey covers LS-02 step 1 (as a Material Transfer from the receipt's **Create › Make Stock Entry**), LS-05 step 1 and the Quarantine landing of LS-04 |

Untested: LS-07 step 6 on a real second company in the browser (server test only); LS-10 on a site with real cutover stock (server test only); the client's real stores and lot volumes.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| A lot is born Quarantine; status fields never edited on the lot or via the API, not even by the Administrator | Verified (automated) |
| Status changes only through the signed Lot Release, with who/when/why in the audit trail | Verified (automated + journey) |
| Not Approved: issue, transfer to approved store, pick, delivery refused on the form and at the ledger; own-role store, returns, scrap and supplier return still allowed | Verified (automated + journey for the transfer) |
| Receipts land in Quarantine; door rejects in Rejected; work orders draw from approved stores and finish into Quarantine | Verified (automated + journey) |
| One store per role per company, made when the company exists; roles protected | Verified (automated) |
| Stock into a lot only on its own document; counts allowed (loss / no change), gains refused | Verified (automated) |
| Stock by Lot Status with who/when/why; old-style and bundled lots | Verified (automated); browser **Not tested** beyond the screen sweep opening it |
| Cutover patch: status by store, audited, idempotent | Verified (automated) |
| Packing Slip guard | Verified (automated, 2026-10-10) |
| Return of a Quarantine/Hold lot to the supplier; counts of quarantined stock | **Open** (client decision) |
| Manual LS-01…LS-10 | **Not tested** |
| CI green on a fresh site for the final commit | **Blocked** (in progress) |
