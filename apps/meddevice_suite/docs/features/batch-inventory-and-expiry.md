# Batch inventory, expiry, FEFO picking and putaway (module 18) — feature document and browser test guide

Requirements: **URS-STK-001, URS-STK-002, URS-STK-003**, and since 2026-10-11 **URS-STK-008** (expired lots) and **URS-STK-009** (first expiry, first out for lots named by hand) (`meddevice_suite/qa/requirements.toml`; code `lot_control/expiry.py`, report *Lots Taken Out of Expiry Order*). Also touched: URS-CMP-027 (lot expiry alerts, module 51: the alert is the only expiry-driven behaviour this app adds), URS-STK-004/005 (a lot's status and store, module 17, which keep expiry-first picking to approved stock), URS-PLT-007 (the journey that proves expiry-first issue and delivery).
Code: `meddevice_suite/setup/stock.py` (locked Stock Settings, negative-stock refusal), `compliance/alerts.py` with `compliance/notification/lot_expiring_soon/` and `lot_expired/` (expiry alerts), hooks in `meddevice_suite/hooks.py`; the lot stores and status that putaway relies on in `lot_control/lot_status.py` and `lot_control/stores.py`; test fixtures `qa/ui_journey.py`, `pilot/demo.py`, `pilot/flow.py`. Module approach in the register: **ready** (standard ERPNext, configured by this app), so most behaviour here is ERPNext's, pinned and locked by configuration.
State on 2026-10-10: configured since the Phase 0/1 baseline (commits d4b2b3b6, 02c5f213, a11c586c) and reused by modules 16 and 17; server and browser suites green locally on the complete code; GitHub CI on this branch was in progress when this document was written (section 8).

## 1. Feature overview

**Purpose.** Every lot of material and product is an ERPNext Batch with an expiry date, on every site, so each lot can be followed from receipt through production to the customer. When stock is picked or issued and nobody names a lot, the lot that expires first is taken first (FEFO). Stock never goes below zero for any item or lot, and nobody on a site can switch that off. Stores and QA are warned 30 days before a lot with stock expires and again when it has expired. Putaway is fixed by lot status (module 17): goods land in the Quarantine store, a release moves the lot to the item's approved default store, a hold or rejection to the Hold or Rejected store.

**Why it matters.** ISO 13485 7.5.8 and 7.5.9 (identification and traceability by lot), ALCOA+ data integrity (a negative balance would break the traceability of every lot it touches); D2 Inventory by Batch (Lot Control); D3 Batch / Lot Traceability Flow and Putaway in Finished Goods Warehouse (FIFO / FEFO).

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| Stores (`stores@demo.example.com`) | Stock User | Receives lots with their expiry; issues, transfers and delivers; sees the expiry alerts in the desk bell |
| Production Supervisor (`production.supervisor@demo.example.com`) | Manufacturing User | Starts work orders; ERPNext issues the earliest-expiring approved lots; the finished lot gets its expiry from the product's shelf life |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Sets Has Batch No, Has Expiry Date and Shelf Life In Days on items; corrects a lot's Expiry Date (audited); receives the expiry alerts |
| Management (`management@demo.example.com`) | Stock Manager (+ others) | Reads batch reports; counts stock |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Runs `bench migrate`, which re-applies the locked settings; cannot change them on the site |

**Prerequisites.** `bench migrate` (→ `apply_configuration` → `apply_stock_settings`, `apply_store_roles`, `apply_lot_status`, `apply_alerts`); items configured with **Has Batch No**, **Automatically Create New Batch**, a **Batch Number Series**, **Has Expiry Date** and **Shelf Life In Days** (the demo loader does this for every demo item: raw materials 365–1825 days, products 1825 days); the scheduler running for the daily alert.

## 2. Detailed functional explanation

### 2.1 Every lot is a batch (URS-STK-001)

`setup/stock.py` fixes these Stock Settings on every site (`STOCK_SETTINGS`), applied at install and after every migrate and refused when changed on the site:

| Stock Settings field (label) | Value | Why |
| --- | --- | --- |
| Activate Serial / Batch No for Item | on | ERPNext v16 ships batch tracking switched off |
| Pick Serial / Batch Based On | *Expiry* | the lot that expires first is picked first (URS-STK-002) |
| Auto create Serial and Batch Bundle for outward | on | ERPNext applies the choice above only while it picks the lots itself |
| Allow negative stock | off | URS-STK-003 |
| Allow negative stock for Batch | off | URS-STK-003 |
| Allow to make Quality Inspection after Purchase / Delivery | on | goods are received into quarantine first and inspected afterwards (module 16); ERPNext's own gate (an inspection before the receipt) stays off |

Changing any of them on **ERPNext Settings › Stock Settings** is refused: *<labels> are part of the validated configuration and cannot be changed here.* (`protect_stock_settings`; other Stock Settings stay editable). A change made behind the application's back (SQL, `set_single_value`) is undone by the next migrate.

The item master (QA's, module 1) carries the per-item switches; the demo loader (`pilot/demo.py` `ensure_item`) sets them on every demo item:

| Item field (label) | Demo value | Effect |
| --- | --- | --- |
| Has Batch No | ticked | every stock movement of the item names a lot |
| Automatically Create New Batch | ticked | a receipt line or manufacture entry makes the lot itself |
| Batch Number Series | `<code>-.YY..MM.-.####` | lot names, e.g. `RM-SILICONE-26-10-0001` |
| Has Expiry Date | ticked | the lot carries an Expiry Date |
| Shelf Life In Days | 730 (silicone), 365 (latex), 1825 (connectors, pouches, cartons, products) | Expiry Date = posting date + shelf life |
| Allow Negative Stock | unticked, cannot be ticked | URS-STK-003 |

With this, an item marked **Has Batch No** gets a Batch per receipt line or manufacture entry (ERPNext creates it with the job's own create permission, module 17 `CREATED_THROUGH_TRANSACTIONS`), named by the item's **Batch Number Series** (demo: `RM-SILICONE-.YY..MM.-.####` → `RM-SILICONE-26-10-0001`); ERPNext v16 keeps the lots of each stock transaction in a Serial and Batch Bundle. The lot's **Expiry Date** is set by ERPNext from the item's **Shelf Life In Days** counted from the document's posting date when the item has **Has Expiry Date** (an item with Has Expiry Date and no shelf life is refused by ERPNext at batch creation). ERPNext's batch reports stay in the Stock sidebar: **Batch Item Expiry Status**, **Batch-Wise Balance History**, **Serial No and Batch Traceability**, **Stock Ledger**, **Stock Balance**; this app adds **Stock by Lot Status** (module 17).

### 2.2 Expiry-first picking and issue (URS-STK-002)

When a Stock Entry (Material Issue, Material Transfer, Material Transfer for Manufacture from a work order's **Start**), a Pick List or a Delivery Note names an item and a store but no lot, ERPNext builds the bundle itself and, with *Pick Serial / Batch Based On = Expiry*, orders the available lots of that store by Expiry Date (a lot without a date last) and takes them in that order; lots already expired are left out of the automatic pick. Because every lot that is not Approved sits in a role store (module 17), the pick from an approved store only ever sees approved lots.

What this app verifies on top of ERPNext: the setting is applied and locked (section 2.1); the journey issues the earliest-expiring lot although an older, later-expiring lot of the same material was in the store before (`pilot/test_standard_flow.py TestExpiryFirst`; `qa/ui_journey.py` puts a lot of the raw material and of the product in store that expires 3650 days out, then the browser receives, makes and delivers newer lots and checks the one that expires first was taken).

### 2.3 Stock never below zero (URS-STK-003)

Three layers: the two Stock Settings above (locked); the per-item **Allow Negative Stock** and per-lot **Allow Negative Stock for Batch** switches are refused on save (*Stock can never go below zero, for any item or lot; Allow Negative Stock cannot be switched on.*, `refuse_negative_stock` on Item and Batch validate); and ERPNext's stock ledger raises `NegativeStockError` on submit of any document that would take an item or lot below zero (the desk shows ERPNext's own message, e.g. *Batch No X of an Item Y has negative stock…*, which the journey asserts by pattern).

### 2.4 Expiry alerts (URS-CMP-027, module 51; shown here because expiry is this module's)

A daily job (`send_expiry_alerts`, scheduler `daily`) sends two system notifications to everyone with the Stock User or Quality Manager role, for each lot with stock (Batch Quantity > 0), not disabled, with an Expiry Date:

| Alert (Notification) | When | Subject | Message |
| --- | --- | --- | --- |
| **Lot Expiring Soon** | expiry within today + 30 days (a lot received already inside the window is alerted on the next run; a missed day is caught up) | *Lot <lot> expires on <date>* | *Lot <lot> of item <item> expires on <date>, and <qty> <unit> of it is still in stock. Plan its use, or its hold, before then.* |
| **Lot Expired** | expiry before today | *Lot <lot> has expired with stock remaining* | *… expired on <date>, and <qty> <unit> of it is still in stock. It can no longer be issued or dispatched; move it to the hold store.* |

Each lot is alerted once per alert (field **Expiry Alert Sent** on the lot, bookkeeping outside the audit trail); a changed Expiry Date clears it so the new date is alerted afresh; a failed send is logged in Error Log and retried the next day without holding back the others. The two notifications are configuration as code (re-imported on every migrate, cannot be edited, switched off or deleted on a site). Email follows once the site's mail server is configured (module 51); until then the alerts appear in the desk bell and notification list.

### 2.5 Putaway by lot status (module 17, summarised)

Goods receipts always land in the company's Quarantine store (whatever the item or the form defaulted to); a work order's finished lot lands in the Quarantine store; QA's Release moves the lot to the item's **Default Warehouse** (an approved store, e.g. `Stores - <abbr>` for raw materials, `Finished Goods - <abbr>` for products in the demo data); Hold and Reject move it to the Hold or Rejected store. ERPNext's own **Putaway Rule** master is not part of the scope: it is not listed in the Stock sidebar (`setup/sidebars.py` allowlist) and the demo data defines none.

### 2.5a Expired lots (URS-STK-008) and lots named by hand (URS-STK-009), since 2026-10-11

Both are checked on the stock ledger entry, after the store and lot-status checks, so every document, the form and the API are held alike (`lot_control/expiry.py`).

- **An expired lot** (Expiry Date before the posting date, as ERPNext counts it; a lot expiring today is still usable) leaves its store only into the hold, rejected or returns store, by QA's own decision, back to its supplier, or as counted. A Material Issue, a transfer to production, consumption, a delivery and a move to another usable store are refused: *Lot X expired on D; it cannot be issued, used or delivered. Move it to the hold store; QA decides it.* (ERPNext already refused it on a delivery and some production entries; a material issue or a move to a usable store went through.)
- **First expiry, first out for a lot named by hand:** when stock goes to production (issue, transfer for manufacture, consumption, repack, subcontracting) or to a customer (delivery note, invoice that updates stock), a lot is refused while an earlier-expiring Approved lot of the same item, not expired, would still be in the same store after the document (what the document itself takes of it is counted). A lot without an expiry date goes after every lot that has one. A move between stores is not a use.
- **Exception:** the document states its **FEFO Exception Reason** (for example, the customer asked for one lot); a blank reason is none. The reason stays on the document and in its audit trail, and **Stock › Lots Taken Out of Expiry Order** (also in Quality) lists every such document for QA.
- Decided 2026-10-11 by the project, on the instruction to decide by the standard flow (to be confirmed with the client): an expired lot never goes to use; FEFO is enforced with a stated reason as the only exception, since a customer may ask for one lot and FEFO protects shelf life rather than the product's conformity.

### 2.6 What is intended but not implemented / outside this feature

- **Putaway Rule / bin locations.** Not used; stores are the only locations.
- **Forcing lot control per item.** Nothing refuses a stock item without Has Batch No; the demo loader sets it on every item and the item master is QA's (module 1, Item Manager).
- **Supplier's own expiry date.** ERPNext sets the lot's expiry from the shelf life; if the supplier's certificate states an earlier date, QA (Item Manager) corrects **Expiry Date** on the lot, which is audited and re-arms the alert. There is no field for it on the receipt line.

## 3. Requirements and acceptance criteria

> **URS-STK-001** Every lot of material and product can be tracked as a batch on every site.
> **URS-STK-002** When stock is picked or issued, the lot that expires first is taken first.
> **URS-STK-003** Stock can never go below zero, for any item or lot.
> **URS-STK-008** A lot whose expiry date has passed is never issued, sent to production, consumed or delivered, from any document, the form or the API; it leaves its store only into the hold, rejected or returns store, by QA's own decision, back to its supplier, or as counted.
> **URS-STK-009** When stock goes to production or to a customer, the lot that expires first goes first: a lot is refused while an earlier-expiring Approved lot of the same item that has not expired would still be in the same store after the document, unless the document states its FEFO exception reason, which is kept with it and in its audit trail and listed for QA; moving stock from one store to another is not held to it.

| # | Acceptance criterion | Implementation | Automated verification |
| --- | --- | --- | --- |
| C1 | Batch tracking is on, on every site; a batch-tracked item with expiry can be made | `STOCK_SETTINGS`, `apply_stock_settings` | `setup/test_stock.py` `TestStockSettings.test_the_settings_are_applied`, `TestLotsAreBatches.test_a_batch_tracked_item_can_be_created` |
| C2 | Each receipt line and manufacture entry makes a new lot with its expiry from the shelf life | ERPNext (configured) | `pilot/test_standard_flow.py` `TestStandardFlow.test_enquiry_to_delivery_with_each_job_doing_its_part` (receipt lots have an expiry; finished lot expiry = today + 1825 days); browser: `standard_flow.js` "Stores receives the order into quarantine; each material gets a new lot with its expiry", "Production finishes the work order; the finished lot is made with its expiry date" |
| C3 | Picking and issue take the lot that expires first, even if received later | *Pick Serial / Batch Based On = Expiry* + auto bundle | `pilot/test_standard_flow.py` `TestExpiryFirst.test_the_lot_that_expires_first_is_issued_first_even_if_received_later`; browser: "[URS-STK-002] Production plans the work order on its routing and starts it; the lots that expire first are issued", "[URS-STK-002] Stores delivers the order; the lot delivered is the one just made, which expires first" (with `qa/ui_journey.py` `put_later_expiring_lots`) |
| C4 | The settings cannot be changed on a site; a change behind the back is undone by migrate; other settings stay editable | `protect_stock_settings` | `TestStockSettingsAreLocked.test_the_declared_settings_cannot_be_changed_on_a_site`, `test_other_stock_settings_stay_editable`, `TestStockSettings.test_a_manual_change_is_undone_on_the_next_migrate` |
| C5 | Issuing more than is in stock is refused | ERPNext `NegativeStockError` (settings off) | `TestStockNeverGoesNegative.test_issuing_more_than_is_in_stock_is_refused`; browser: "[URS-STK-003] Stores cannot move the released lots out of quarantine again, for none is left" |
| C6 | No item and no lot may be allowed negative stock | `refuse_negative_stock` (Item and Batch validate) | `TestNoItemOrLotMayGoNegative.test_an_item_cannot_be_allowed_negative_stock`, `test_a_lot_cannot_be_allowed_negative_stock` |
| C7 | Expiry alerts: 30 days, once, catch-up, expired, changed date, no stock / disabled / undated excluded, one failure does not stop the rest, daily, indexed | `send_expiry_alerts`, `reset_expiry_alert` | `compliance/test_alerts.py` `TestLotExpiryAlerts.*` (12 tests, URS-CMP-027) |
| C9 | An expired lot is never issued, sent to production, moved to a usable store or delivered; it goes to the hold store, back to its supplier, or is counted; a lot expiring today is usable | `expiry.guard_stock_ledger_entry` | `lot_control/test_expiry.py` `TestExpiredLots.*` (URS-STK-008) |
| C10 | A later-expiring lot named by hand is refused while an earlier Approved, unexpired lot stays in the store after the document; a stated reason lets it through and is reported; expired, unapproved and other stores' lots do not count; undated lots go last; moves between stores are not uses | `expiry.guard_stock_ledger_entry`, report *Lots Taken Out of Expiry Order* | `lot_control/test_expiry.py` `TestFirstExpiryFirstOut.*` (URS-STK-009) |
| C8 | Putaway: receipts into Quarantine, release to the approved default store, finished lot into Quarantine | module 17 | `lot_control/test_lot_status.py` `test_goods_are_received_into_the_quarantine_store_whatever_store_was_named`, `TestTheDecisionMovesTheStock.test_release_moves_the_stock_to_the_approved_store_and_approves_the_lot`, `test_a_work_order_draws_from_an_approved_store_and_finishes_into_quarantine` |

**Partial / ambiguous (client to decide):**
- Decided 2026-10-11: a lot named by hand is held to FEFO, with a stated reason as the exception (URS-STK-009); an expired lot is never used (URS-STK-008). Section 2.5a.
- Open with the client: a minimum remaining shelf life at dispatch (some customers refuse lots near expiry) is not a requirement today.
- URS-STK-001 "every lot … on every site": per-item batch tracking is configured by data (QA on the item master), not enforced.
- "Putaway" in the module title is realised as the fixed store per status (module 17); ERPNext Putaway Rules are out of scope unless the client asks for bin-level locations.

**Not covered by this feature:** traceability reports across the chain (module 55); the sterilization expiry of finished devices (module 24); physical labelling of lots (module 23).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`) or a test site after `prepare_test_site`. Demo data: items **RM-SILICONE** (Kg, shelf life 730 days, default store `Stores - <abbr>`), **RM-CONNECTOR** (Nos, 1825 days), product **FC-2W-SIL-16** (1825 days, default `Finished Goods - <abbr>`), supplier **Silicone Compounds (Demo)** (approved for RM-SILICONE, module 4 guide SA-01), customer **City Hospital (Demo)**. Make lots Approved as in the module 16 guide (LR-01 → LR-06). Never use real data.

### BI-01 Settings are applied and locked (C1, C4)
Role: System Administrator.
1. **ERPNext Settings › Stock Settings**. Expected: Activate Serial / Batch No for Item ticked; Pick Serial / Batch Based On = Expiry; Auto create Serial and Batch Bundle for outward ticked; Allow negative stock unticked; Allow negative stock for Batch unticked; Allow to make Quality Inspection after Purchase / Delivery ticked.
2. Set Pick Serial / Batch Based On = FIFO; Save. Expected: refused *Pick Serial / Batch Based On are part of the validated configuration and cannot be changed here.*
3. Tick Allow negative stock; Save. Expected: refused likewise (label *Allow negative stock*).
4. Change an unrelated setting (e.g. Item Naming By); Save. Expected: saved.
Actual result: ____  Pass/Fail: ____  Cleanup: restore step 4.

### BI-02 A lot is made per receipt line with its expiry (C2)
Role: Stores. Preconditions: an approved Purchase Order for RM-SILICONE qty 10 and RM-CONNECTOR qty 100.
1. Receive it (**Create › Purchase Receipt**, fill the certificate fields as in module 16 LR-01), Submit. Expected: each line has a new lot (`RM-SILICONE-26-10-…`, `RM-CONNECTOR-26-10-…`); opening each lot (`/desk/batch/<lot>`) shows Item, **Expiry Date** = posting date + 730 days for silicone and + 1825 days for the connector, Batch Quantity = the received quantity, Lot Status = Quarantine.
2. On a new receipt line, pick the lot of step 1 in **Batch No** (tick Use Serial No / Batch Fields); Save. Expected: refused *New lot needed* (module 16 / 17: a delivery is a new lot).
3. **Stock › Batch-Wise Balance History** and **Batch Item Expiry Status** for RM-SILICONE. Expected: the lot with its quantity and expiry.
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of a lot's form.

### BI-03 No item or lot may be allowed negative stock (C6)
Role: QA Manager (Item Manager).
1. **Stock › Item › RM-SILICONE**, tick **Allow Negative Stock**; Save. Expected: refused *Stock can never go below zero, for any item or lot; Allow Negative Stock cannot be switched on.*
2. Open a lot of RM-SILICONE, tick **Allow Negative Stock for Batch**; Save. Expected: refused *… Allow Negative Stock for Batch cannot be switched on.*
Actual result: ____  Pass/Fail: ____

### BI-04 Stock never goes below zero (C5)
Role: Stores. Preconditions: Approved lot L1 of RM-SILICONE with exactly 6 in `Stores - <abbr>`.
1. **Stock › Stock Entry › New**, Material Issue, line RM-SILICONE, Source Warehouse `Stores - <abbr>`, Qty 7, Batch No L1 (Use Serial No / Batch Fields); Save (allowed, a draft moves nothing); Submit. Expected: refused with ERPNext's negative-stock message naming the lot; the entry stays a draft.
2. Qty 6; Submit. Expected: submitted; L1 holds 0 in Stores.
3. Repeat step 2's entry (another Material Issue of 1 from L1). Expected: refused (nothing left).
Actual result: ____  Pass/Fail: ____

### BI-05 The lot that expires first is issued first (C3)
Role: Stores, then Production Supervisor. Preconditions: two Approved lots of RM-SILICONE in `Stores - <abbr>`: L-OLD received first with Expiry Date = today + 600 days (QA sets it on the lot after release) and L-NEW received later with Expiry Date = today + 200 days.
1. As Stores: Material Issue, line RM-SILICONE, Source Warehouse `Stores - <abbr>`, Qty 1, **no** Batch No; Save, Submit. Expected: the line's Serial and Batch Bundle holds L-NEW (expires first), not L-OLD.
2. As Production Supervisor: **Manufacturing › Work Order › New** for FC-2W-SIL-16 qty 100 (Source `Stores - <abbr>`, WIP `Work In Progress - <abbr>`, Target `Quarantine - <abbr>`), Submit, **Start**, Create, Submit the Material Transfer for Manufacture. Expected: the silicone line's bundle holds L-NEW first; L-OLD only if L-NEW does not cover the quantity.
3. As Stores: **Stock › Pick List › New** for RM-SILICONE qty 1 from `Stores - <abbr>` without a lot; Save. Expected: the location row's Batch No = L-NEW.
4. As Stores: Material Issue naming **L-OLD** by hand, Qty 1. Expected: refused, *Lot L-NEW expires on …, before lot L-OLD* (URS-STK-009). Fill **FEFO Exception Reason** and submit again: allowed; **Lots Taken Out of Expiry Order** lists it with the reason.
Actual result: ____  Pass/Fail: ____  Cleanup: QA restores the expiry dates if changed.

### BI-06 Delivery takes the earliest-expiring finished lot (C3)
Preconditions: two Approved lots of FC-2W-SIL-16 in `Finished Goods - <abbr>`: an older one expiring later and the lot just made (today + 1825 days); a Sales Order for 100.
1. As Stores: from the Sales Order **Create › Delivery Note**; line Warehouse `Finished Goods - <abbr>`, no lot; Save, Submit. Expected: the delivered bundle is the lot that expires first; the other stays.
Actual result: ____  Pass/Fail: ____

### BI-06a An expired lot is not used (C9)
Preconditions: an Approved lot whose Expiry Date QA set to yesterday, in `Stores - <abbr>`.
1. As Stores: Material Issue naming it. Expected: refused, *Lot … expired on …; move it to the hold store*.
2. Material Transfer of it to `Finished Goods - <abbr>`. Expected: refused. 3. Material Transfer of it to `Hold - <abbr>`. Expected: allowed.
Actual result: ____  Pass/Fail: ____

### BI-07 Expiry alerts (C7; URS-CMP-027)
Preconditions: a lot of RM-SILICONE with stock and Expiry Date = today + 10 (QA sets it); a second with Expiry Date = yesterday; a third with stock 0.
1. As System Administrator run `bench --site <site> execute meddevice_suite.compliance.alerts.send_expiry_alerts` (or wait for the nightly scheduler). Expected: Stores and QA Manager each see one bell notification *Lot <first> expires on <date>* and one *Lot <second> has expired with stock remaining*; nothing for the empty lot; Sales sees nothing; each alerted lot shows **Expiry Alert Sent** = *Lot Expiring Soon* / *Lot Expired*.
2. Run it again. Expected: no new notifications.
3. As QA: change the first lot's Expiry Date to today + 20; run the job. Expected: a second *expires on* notification for it.
4. As System Administrator: **System › Notification › Lot Expiring Soon**, untick Enabled; Save. Expected: refused *The alert Lot Expiring Soon is part of the validated configuration and cannot be changed here.*
Actual result: ____  Pass/Fail: ____

### BI-08 Putaway by status (C8)
1. As Stores: receive goods with Accepted Warehouse `Stores - <abbr>`. Expected: set to `Quarantine - <abbr>` (module 17 LS-04).
2. As QA: release the lot (module 16 LR-06). Expected: stock in `Stores - <abbr>` (the item's Default Warehouse).
3. As Production: finish a work order. Expected: finished lot in `Quarantine - <abbr>`; after QA's release, in `Finished Goods - <abbr>`.
4. As Stores: tick **Apply Putaway Rule** on a new Purchase Receipt (no rules exist); Save. Expected: lines still land in `Quarantine - <abbr>` — record the result (unverified by a test, section 7).
Actual result: ____  Pass/Fail: ____

### BI-09 Lists, reports and permissions
1. As Stores: **Stock › Batch No** list, filter Item = RM-SILICONE, sort by Expiry Date. Expected: lots with Expiry Date and Lot Status.
2. As Stores: open a lot, try to change Expiry Date. Expected: no write permission (Stock User creates lots through transactions only; QA corrects).
3. As QA: change a lot's Expiry Date (give the reason the audit trail asks for). Expected: saved; **Audit Trail** shows the change; **Expiry Alert Sent** cleared.
4. As **Sales**: `/desk/query-report/Stock by Lot Status`. Expected: opens (Sales User is among the report's roles).
Actual result: ____  Pass/Fail: ____

### BI-10 Cross-module consistency (lot, bundle, ledger, status)
Role: Stores, then QA. Preconditions: the Material Issue of BI-05 step 1.
1. Open the issue's line, follow **Serial and Batch Bundle**. Expected: one entry, Batch No = L-NEW, quantity −1, warehouse `Stores - <abbr>`.
2. **Stock › Stock Ledger**, filter Item = RM-SILICONE, Batch No = L-NEW. Expected: the inward entry of its receipt, the release's transfer (Quarantine → Stores) and the issue; running balance never below zero.
3. **Stock › Stock by Lot Status**, Item = RM-SILICONE. Expected: L-NEW's remaining quantity in `Stores - <abbr>`, Lot Status Approved, Expiry = its Expiry Date.
4. As QA: open L-NEW. Expected: Batch Quantity equals the report's quantity; Lot Status, Status Set By/At/Reason and Latest Decision from the release.
Actual result: ____  Pass/Fail: ____

### Regression
- Modules 16 and 17 guides LR-06 and LS-02 to LS-06 still pass; the full journey passes (receipt with expiry → release → expiry-first issue → finished lot with expiry → release → expiry-first delivery).

## 5. End-to-end business workflow

1. **Stores** receives 10 Kg RM-SILICONE on 2026-10-10 → lot `RM-SILICONE-26-10-0001`, Expiry Date 2028-10-09 (730 days), Quarantine. A second delivery on 2026-10-20 → lot `-0002`, Expiry Date 2028-10-19. QA notes the supplier's certificate gives the second lot an earlier retest date and sets its Expiry Date to 2027-10-19 (audited).
2. **QC** inspects, **QA** releases both (modules 16, 17) → both Approved in `Stores - <abbr>`.
3. **Production** starts a work order for 100 FC-2W-SIL-16 → the Material Transfer for Manufacture takes 1.4 Kg from lot `-0002` (expires first), although `-0001` arrived earlier. The finished lot `FC-2W-SIL-16-26-10-0001` gets Expiry Date = today + 1825 days and waits in Quarantine; QA releases it to `Finished Goods - <abbr>`.
4. **Stores** delivers 100 to City Hospital (Demo) → the earliest-expiring finished lot is delivered.
5. On 2027-09-19 the daily job warns Stores and QA: *Lot RM-SILICONE-26-10-0002 expires on 2027-10-19* (if any stock remains); on 2027-10-20, *has expired with stock remaining*; QA holds the lot.
6. A count that would take a lot below zero, or an issue of more than is in stock, is refused everywhere.

Cross-module data changes: Stock Settings (locked), Item (batch and shelf-life fields, module 1), Batch (`expiry_date` by ERPNext, `mds_expiry_alert` by the job, status fields by module 17), Serial and Batch Bundle (ERPNext), Notification Log (alerts), Audit Event (lot changes).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Configuration | `setup/stock.py` `STOCK_SETTINGS` (6 fields), `apply_stock_settings()` (called by `setup/install.py apply_configuration`, which runs `after_install` and `after_migrate`), `protect_stock_settings` (Stock Settings `validate`; passes when `flags.declared_by_code`, `in_install` or `in_migrate`), `refuse_negative_stock` (Item and Batch `validate`; `NEGATIVE_STOCK_SWITCH`) |
| Custom fields | Batch: `mds_expiry_alert` *Expiry Alert Sent* (Select; `compliance/alerts.py` `CUSTOM_FIELDS`, `apply_alerts()`); index `Batch(expiry_date)` added by `apply_alerts` |
| Alerts | Notifications `Lot Expiring Soon`, `Lot Expired` (`compliance/notification/<name>/<name>.json`, document type Batch, channel System Notification, recipients by role Stock User and Quality Manager), registered in `ALERTS`; `send_expiry_alerts()`, `send()`, `reset_expiry_alert` (Batch `validate`), `protect` (Notification `before_validate`, `on_trash`); the Quality Settings lead time (URS-PLT-012) = 30 |
| Hooks (`hooks.py`) | Item `validate`: `setup.stock.refuse_negative_stock`; Batch `validate`: `compliance.alerts.reset_expiry_alert`, `setup.stock.refuse_negative_stock`, `lot_control.lot_status.guard_batch`; Stock Settings `validate`: `setup.stock.protect_stock_settings`; Notification `before_validate` / `on_trash`: `compliance.alerts.protect`; `scheduler_events.daily`: `compliance.alerts.send_expiry_alerts` |
| ERPNext behaviour relied on (`apps/erpnext`, read-only) | `stock/doctype/batch/batch.py` sets `expiry_date` = manufacturing/posting date + `shelf_life_in_days`; `stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py` `get_auto_batch_nos` orders by `expiry_date` when `based_on == "Expiry"` and keeps `expiry_date >= today` or unset; `stock/doctype/pick_list/pick_list.py` reads the same setting; `stock/doctype/stock_entry/stock_entry.py` `validate_batch` refuses an expired lot on manufacture-type entries; `stock/stock_ledger.py` `NegativeStockError` |
| Permissions (`setup/roles.py`) | Item: Item Manager EDIT, others VIEW; Batch: Item Manager EDIT, Stock User and Manufacturing User VIEW + create; Stock Settings: Frappe's own (`ACCESS_CONTROL`), read for Stock User and Manufacturing User (`SUPPORTING_PERMISSIONS`); Stock Settings and Batch are regulated and audited (`audit_scope.py`); Batch in *Device and production records*, 10 years (`retention.py`) |
| Desk | Stock sidebar keeps Batch No, Serial and Batch Bundle, Stock Ledger, Stock Balance, Batch-Wise Balance History, Batch Item Expiry Status, Serial No and Batch Traceability, Stock Settings (`setup/sidebars.py`); Putaway Rule is not listed |
| Jobs | `daily`: `meddevice_suite.compliance.alerts.send_expiry_alerts` |
| Test fixtures | `qa/ui_journey.py` `prepare_journey` (`LOTS_IN_STORE`, `LATER_EXPIRY_DAYS` = 3650, `put_later_expiring_lots`, `write_off_leftover_stock`, whitelisted `lots_of`); `pilot/demo.py` `ensure_item` (`has_batch_no`, `create_new_batch`, `batch_number_series`, `has_expiry_date`, `shelf_life_in_days`); `pilot/flow.py` `buy`, `release_received_lots`, `issue_materials`, `deliver` |
| Data flow | migrate → `apply_stock_settings` (save with `declared_by_code`) → settings locked; receipt/manufacture submit → ERPNext makes the Batch (expiry from shelf life) → `guard_batch` (Quarantine) → bundle and ledger; outward document without a lot → ERPNext `get_auto_batch_nos` (expiry order, approved store only by module 17) → bundle → ledger guard (module 17) → `NegativeStockError` if short; daily job → `send_expiry_alerts` → Notification Log per role member → `mds_expiry_alert` set |

## 7. Defects, edge cases and risks

**Confirmed and fixed earlier (all with tests):** the expiry alert used Frappe's "Days Before" event, which fires on one exact date, so a lot received inside the window or a day the scheduler missed was never alerted (b6e8ecc0 / a11c586c: daily job with catch-up and once-per-lot bookkeeping); Stock Settings could be changed on a site (02c5f213: locked, undone by migrate); a quarantined lot was visible to expiry-first picking until role stores kept non-approved stock apart (2684416a, module 17); the demo items defaulted to the quarantine store, so a release had nowhere approved to go (2684416a: default store = the approved store).

**Potential (not verified by a test, listed for the record):**
- FEFO is not enforced when a lot is named by hand (section 2.6).
- An expired Approved lot can be issued or delivered by naming it (section 2.6); ERPNext blocks it only on manufacture-type entries and in automatic picking.
- **Apply Putaway Rule** on a receipt or stock entry: ERPNext would re-assign warehouses in its own validate; module 17's `guard_purchase_receipt` runs in the hooks' validate afterwards and sets the Quarantine store, so the rule should have no effect on receipts. Not tested; no Putaway Rules exist in the demo data (BI-08 step 4).
- A lot's expiry is the posting date + shelf life; the supplier's own expiry or retest date is not captured at receipt and depends on QA correcting the lot (section 2.6).
- Alert timing when the scheduler is off for more than 30 days: alerted on the first run after, possibly as *expired* only — by design (URS-CMP-027).
- `send_expiry_alerts` reads `batch_qty` on the Batch, which ERPNext maintains from stock postings; a lot whose `batch_qty` is stale (direct SQL) would be mis-alerted. ERPNext's own bookkeeping; not this app's.

**Unverified in this document:** ERPNext's exact negative-stock wording on the desk (the journey matches the pattern *Batch No … of an Item … has negative stock*); whether ERPNext's batch selector on a receipt line offers an existing lot at all (the server refuses it either way, BI-02 step 2). (The sidebar entry *Batch No* → Batch and the Stock Settings label *Item Naming By* were verified in ERPNext's `workspace_sidebar/stock.json` and `stock_settings.json`.)

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch, local test site `test.localhost`, macOS:

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (2026-10-10) |
| Module tests (part of the suite) | `setup/test_stock.py`: `TestStockSettings` (2), `TestLotsAreBatches` (1), `TestStockNeverGoesNegative` (1), `TestNoItemOrLotMayGoNegative` (2), `TestStockSettingsAreLocked` (2); `pilot/test_standard_flow.py`: `TestExpiryFirst` (1), `TestStandardFlow` (1); `compliance/test_alerts.py`: `TestLotExpiryAlerts` (12) — counts read from the files | passed within the suite above |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed (2026-10-10, complete code), including the five journey steps cited in C2, C3 and C5 |
| Semgrep (Frappe rules + python correctness) | — | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| CI (fresh site) | GitHub Actions on this branch | **in progress** when this document was written |
| Manual browser tests BI-01…BI-09 | — | **not executed** (no manual tester session); the automated journey covers BI-02 step 1, BI-04 (as the refused second transfer out of quarantine), BI-05 step 2, BI-06 step 1 and BI-08 steps 1–3 |

Untested: BI-07 on the real nightly scheduler (the job is called directly in tests); email delivery; BI-05 step 4 and BI-08 step 4 (open points); the client's real shelf lives and supplier expiry dates.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Batch tracking on, expiry-first picking, no negative stock, inspection after receipt: applied on every site and locked | Verified (automated) |
| Each receipt line and manufacture entry makes a new lot with its expiry | Verified (automated + journey) |
| The lot that expires first is issued and delivered first when ERPNext picks | Verified (automated + journey) |
| FEFO when a lot is named by hand; issue of an expired lot | **Open** (client decision; not enforced) |
| Issuing more than is in stock is refused; no item or lot may be allowed negative stock | Verified (automated + journey) |
| Expiry alerts: 30 days, once, catch-up, expired, re-armed on a changed date, locked configuration | Verified (automated, job called directly); nightly scheduler **Not tested** |
| Putaway: Quarantine on receipt and manufacture, approved default store on release, Hold / Rejected stores | Verified (automated + journey) |
| Apply Putaway Rule has no effect on receipts | **Not tested** |
| Manual BI-01…BI-09 | **Not tested** |
| CI green on a fresh site for the final commit | **Blocked** (in progress) |
