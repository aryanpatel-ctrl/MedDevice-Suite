# Goods receipt, certificate verification, IQC and QA store release (module 16) — feature document and browser test guide

Requirements: **URS-QC-001, URS-QC-002, URS-QC-003, URS-QC-004** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-STK-004 (the lot's status is set only by the signed decision, module 17), URS-STK-010 (the stock path matrix and the disposition of goods a customer sent back, module 17), URS-PUR-001 (full inspection of a conditional supplier's lot, module 4), URS-MST-002 (the inspection reads the item's controlled specification, module 6).
Code: `meddevice_suite/lot_control/receipt.py`, `lot_control/release.py`, `lot_control/doctype/lot_release/` (`lot_release.py`, `.json`, `.js`, `_list.js`), `lot_control/report/lots_awaiting_release/`, `public/js/lot_control.js`, signing dialog `public/js/esign.js`, hooks in `meddevice_suite/hooks.py`; store moves in `lot_control/lot_status.py` (module 17).
State on 2026-10-10: built, reviewed (the review rounds of 2026-10-09/10 that also covered modules 4 and 17: see section 7), server and browser suites green locally on the complete code; GitHub CI on this branch was in progress when this document was written (section 8).
Revised 2026-10-12 (branch `fix/lot-release-scope`): a decision acts only on the stock in the store of the lot's status (never the quantity rejected at the door), the rejection's supplier return is for that quantity only, the newest inspection decides, what the decision rests on is read when QA signs, Hold is signed *Held*, and QA disposes of goods a customer sent back (plan D-25). Tests written, **not yet run** (section 8).

## 1. Feature overview

**Purpose.** Nothing a supplier delivers reaches production unverified. Stores records the supplier's lot number and certificate on each goods receipt line; QC verifies the certificate and inspects the lot against the item's incoming specification, naming the instrument for every measurement; QA then releases, rejects or holds the lot with an electronic signature and a reason. Release moves the stock out of quarantine into the approved store in the same transaction; rejection moves it to the rejected store and drafts the return to the supplier; hold parks it in the hold store until a new accepted inspection. QA and Stores see at any time what is waiting, for what, and for how long.

**Why it matters.** ISO 13485 7.4.3 (verification of purchased product), 7.6 (measuring equipment named), 8.2.6 (inspection records) and 21 CFR 11.50 (signed release). The client's diagrams place this in D2 Incoming QC (IQC) and Quarantine / Approved Stock, D3 Document Verification (COA / COC / ISO) → Incoming Quality Inspection → Accepted / Rejected, and D4 3.1–3.6 (Receive, GRN, Internal Batch Creation, IQC, Accept / Reject / Hold, Putaway). An uninspected lot in production is a regulatory finding; the system makes the path impossible rather than discouraged.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| Stores (`stores@demo.example.com`) | Stock User | Receives goods into the Quarantine store; records Supplier Lot No and attaches the certificate on each line |
| QC Inspector (`qc.inspector@demo.example.com`) | QC Inspector | Verifies the certificates (**Verify Certificates**); records and submits the incoming Quality Inspection |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Decides each lot on a **Lot Release** (Release / Reject / Hold) with a signature |
| Purchase (`purchase@demo.example.com`) | Purchase User | Checks and submits the draft return to the supplier a rejection creates (module 15) |
| Production Supervisor (`production.supervisor@demo.example.com`) | Manufacturing User | Makes the finished lot; reads the decision on it |
| Management (`management@demo.example.com`) | Stock / Purchase / Manufacturing / Sales Manager | Reads decisions and the queue |

Only QA Manager creates a Lot Release or signs it. QC Inspector, Stores, Purchase, Production, Management and System Manager read it. Nobody holds submit, cancel, amend or delete on it: the signed act is the only submit.

**Prerequisites.** Lot status and the role stores (module 17), the approved-supplier decision on the supplier (module 4), the item's incoming specification (module 6), the e-signature service (module 49) and at least one measuring instrument registered as an Asset (demo: *Digital Caliper (Demo)*, item `INST-CALIPER`). All are part of the app's configuration applied by `bench migrate`; the demo masters come from `pilot/demo.py` and `pilot/inspection.py`.

## 2. Detailed functional explanation

### 2.1 Goods receipt: supplier lot number and certificate (URS-QC-001)

Navigation: **Stock › Purchase Receipt › New**, or from the purchase order's **Create › Purchase Receipt**. Route `/desk/purchase-receipt/new`.

Every line lands in the company's **Quarantine** store whatever store the item or form defaulted to (`guard_purchase_receipt`, module 17); quantity rejected at the door must go to the Rejected store (*Line N: quantity rejected at receipt goes to the Rejected store.*). Each line's row form carries the section **Supplier Certificate**:

| Field (label on the line) | Set by | Rule |
| --- | --- | --- |
| Supplier Lot No | Stores | Free text; may still be corrected after submit until verified |
| Supplier Certificate (COA / COC) | Stores | Attached file; may still be replaced after submit until verified |
| Certificate Verified, Verified By, Verified At | QC's verification only | Read-only; any other change of these fields, on the form or the API, is refused *Only QC verifies a supplier certificate.* |
| Inspect Each Lot in Full | system | Set when the line was ordered under, or the supplier is now under, a conditional approval (module 4) |

A delivery comes in as a **new lot**: a line naming a lot that already holds stock is refused (*Line N: lot L came in on <document>; a delivery comes in as a new lot.*, title *New lot needed*). A batch made beforehand without stock (ERPNext's batch selector or a barcode scan) is accepted and becomes this receipt's lot. Where a lot came from is read from the stock ledger (first inward movement), never from the batch's own reference, so a batch made by hand is still known by its receipt.

### 2.2 Certificate verification by QC (URS-QC-001)

On a **submitted** receipt (not a return) that has lines with a certificate not yet verified, QC Inspector and QA Manager see the primary button **Verify Certificates**. It opens the dialog **Verify supplier certificates** (*Tick each line whose certificate you have checked against the purchase specification.*) with a **Lines** list (`<n>. <item> · lot <batch> · supplier lot <no>`, all ticked) and the button **Verify**. Each ticked line is verified through `verify_certificate` (one call per line): the line gets Certificate Verified, Verified By = the inspector, Verified At = now, and the receipt's audit trail records *Updated after submission* under the inspector with the reason *Supplier certificate verified for line N*.

Refusals: Stores or anyone outside QC (*Only QC verifies a supplier certificate.*); a draft receipt (*Verify the certificate once the receipt is submitted.*); a line without a file (*Line N has no supplier certificate attached yet.*); a line verified already (*Line N is already verified.*); an unknown line (*<receipt> has no line <row>.*).

Once verified, the line's certificate and supplier lot number can no longer change (*The supplier certificate of line N has been verified; it cannot be changed.*) and the certificate file is protected evidence that nobody deletes or replaces (`compliance/evidence_files.py`). A certificate found wrong afterwards is handled by rejecting or holding the lot, not by editing.

### 2.3 Incoming inspection (URS-QC-002)

Navigation: **Quality › Quality Inspection › New** or **Stock › Quality Inspection › New** (`/desk/quality-inspection/new`). Fields: Inspection Type = *Incoming*, Reference Type = *Purchase Receipt* (or *Stock Entry* for a material-receipt lot), Reference Name, Item Code, Batch No, Sample Size, Inspected By, Readings. On choosing the item, the form takes the item's incoming specification (module 6): its parameters appear as readings with their limits; the limits cannot be changed on the inspection. Each reading of a measurement (Numeric) must name its **Instrument** (Link to Asset, shown in the readings grid); otherwise *Reading N (<parameter>) is a measurement; name the instrument it was taken with.* ERPNext judges each reading and sets Status *Accepted* or *Rejected* on save; QC submits.

Rules on every save of an Incoming inspection (`guard_incoming_inspection`):

| Condition | Message |
| --- | --- |
| no lot named | *Name the lot this incoming inspection is of.* |
| lot came in on a receipt, inspection references anything else | *Lot L came in on receipt PR; its incoming inspection is made on that receipt.* |
| lot came in on a material receipt, inspection references another document | *Lot L came in on material receipt SE; its incoming inspection is made on that entry.* |
| receipt named does not carry the lot for that item | *Lot L of ITEM did not arrive on receipt PR.* |
| certificate of the line not verified | *Verify the supplier certificate on PR line N before inspecting lot L.* (title *Certificate not verified*) |
| line marked Inspect Each Lot in Full and Sample Size below the quantity received | *…is inspected in full: the sample size is the lot's N.* (module 4) |
| Sample Size below the specification's, on submit | *Specification X samples at least 5; this inspection samples 2.* (module 6) |

The inspection's Child Row Reference is set to the receipt line. Only Incoming inspections are held to these rules; QC Inspector records and submits, QA may correct; Stores cannot create one (no create permission).

### 2.4 The decision: Lot Release (URS-QC-003, URS-STK-004)

Navigation: **Stock › Lot Release** or **Quality › Lot Release** (`/desk/lot-release/new`), or from the lot's form (**Stock › Batch No › <lot>**, `/desk/batch/<lot>`) with the button **Decide Lot** (QA only; offered while the lot is Quarantine, Hold or Approved). Record name `LR-YYYY-NNNNN`; list indicator *Awaiting decision* (grey) for a draft, then *Release* (green) / *Reject* (red) / *Hold* (orange) / *Return to Stock* (green) / *Reject Return* (red).

| Field (label) | Set by | Meaning |
| --- | --- | --- |
| Lot | QA | The only field QA types; required |
| Item, Item Name, Lot Status at Decision, Quantity in Stock | system on save, and again when signed | From the lot and its stock: Quantity in Stock is the stock the decision acts on, in the store of the lot's status (stock rejected at the door and goods a customer sent back are not in it); on signing, the stock that decision acts on |
| Lot Came From, Source, Supplier, On Hold Since | system on save, and again when signed | From the stock ledger (the document that brought the lot in); supplier from the receipt; On Hold Since for a held lot: the later of its status time and its latest signed Hold |
| Decision, Incoming Inspection, Reason, Decided By, Decided At, Stock Moved By, Return to Supplier | signature only | Read-only; values sent with the record are discarded on insert and refused on save (*LR-… is decided by signing it; its details are set by the decision.*) |

**Buttons** (saved, unchanged draft, QA only): **Release** (primary) and **Reject** while the lot is Quarantine or Hold; **Hold** while it is Quarantine or Approved; when the lot has stock in the Returns store, the form says how much and offers **Return to Stock** (lot Approved) and **Reject Return** (module 17, section 2.6). Each opens **Sign: <act>** (`esign.js`) with the meaning text (*Released: I release this for use.* / *Rejected: I reject this.* / *Held: I hold this from use.* for Hold), a required **Reason** and **Your password**, and the button **Sign**. A lot from a supplier whose certificate is not verified shows the orange banner *The supplier certificate of this lot is not verified yet; QC verifies it on the receipt.*

What each signed act does (`release.py`, all in one transaction; the record is row-locked, and so is the lot). Whatever the draft showed, the act reads again, under the lot's lock, the lot's status, the stock it acts on, where the lot came from and when it was held (`describe_lot`), and records those with the signature: a draft made before a hold cannot release the lot without the inspection the hold requires.

The stock a decision acts on is the lot's stock in the store of its status — the Quarantine store for a quarantine lot, the Hold store for a held lot, the approved stores for a hold of an Approved lot — and any approved store still holding a lot not yet Approved. Never the Rejected or Returns store: ERPNext puts the quantity rejected at the door in the Rejected store under the same lot as the accepted quantity, and that stays there whatever QA decides; Stores returns it to the supplier (ERPNext's *Return of rejected qty*) or scraps it, while the lot is in any status.

| Act | Needs | Result |
| --- | --- | --- |
| Release | A lot not made by production: came in on a goods receipt or a material receipt, and the **newest** submitted Incoming inspection made on that document is *Accepted* (for a held lot: the newest made after it was put on hold; a *modified* older inspection does not count; an accepted inspection followed by a rejected one is a rejected lot) | Lot **Approved**; the stock it acts on moved by a Material Transfer to the item's default store (must be a store without a role: *Item X has no approved default store for <company>; set it on the item or name the store.*); Incoming Inspection, Stock Moved By recorded |
| Reject | Reason | Lot **Rejected**; the stock it acts on moved to the Rejected store; for a supplier's lot a **draft** purchase return (Purchase Receipt, Is Return) of that quantity only, from the Rejected store, with nothing as rejected quantity (Received = Accepted), recorded in Return to Supplier, for Purchase to check and submit |
| Hold | Reason; signed *Held* | Lot **Hold**; the stock it acts on moved to the Hold store; released only by a later Release after a new accepted inspection |
| Return to Stock | Lot Approved, not expired; the newest inspection (Incoming or Outgoing) made after the goods came back is accepted; reason; signed *Released* | The lot's stock in the Returns store moved to the item's default store; the lot's status unchanged (module 17, 2.6) |
| Reject Return | Reason; signed *Rejected* | The lot's stock in the Returns store moved to the Rejected store; the lot's status unchanged |

Refusals: *Only QA decides a lot.*; *Say why the lot is approved.* (/ *rejected* / *hold*); *Lot L is Rejected; it cannot be hold.* (a Rejected lot is decided no further); *Lot L came in on <document>; only stock received on a goods receipt or a material receipt is released.*; *Lot L has no accepted incoming inspection; it cannot be released.*; *Lot L has no accepted incoming inspection made after it was put on hold; it cannot be released.*; *LR-… has been decided.*; for the dispositions *Lot L has no stock in the Returns store to decide on.*, *Lot L is Hold; goods that came back go back to stock only once the lot is Approved.*, *Lot L has no accepted inspection made after its goods came back; they cannot go back to stock.*, *Lot L has expired; goods that came back do not go back to stock.* A wrong password signs nothing and changes nothing.

After signing the form shows the banner *Release: <reason>* (green) / *Reject: <reason>* (red) / *Hold: <reason>* (orange) / *Return to Stock: <reason>* (green) / *Reject Return: <reason>* (red) and the **Electronic signatures** table. The lot's form shows Lot Status, Status Set By, Status Set At, Status Reason and Latest Decision (module 17). The transfer the release posted is marked **Posted by a Lot Release** and is never cancelled (*SE was posted by a lot release and is not cancelled; put the lot on hold instead.*).

**Permanence.** A Lot Release is never cancelled (*A decision on a lot is never cancelled; a new decision puts the lot on hold or releases it.*) or deleted (*A lot release is never deleted.*), not even by the Administrator; Frappe's Submit button and the API's submit are refused (*LR-… is decided only by signing it.*). Lot releases are in the 10-year *Device and production records* retention class.

### 2.5 The queue: Lots Awaiting Release (URS-QC-004)

**Stock › Lots Awaiting Release** or **Quality › Lots Awaiting Release** (`/desk/query-report/Lots Awaiting Release`), filter **Item**. One row per store holding a Quarantine or Hold lot with stock the release decides (not the Rejected or Returns store: the quantity rejected at the door is not waiting for release): Lot, Item, Lot Status, Quantity, Store, **Waiting For**, **Days Waiting**, Since, Came From; sorted longest wait first. Waiting For is one of *Supplier certificate*, *Certificate verification by QC*, *Incoming inspection*, *Decision on a rejected inspection*, *QA release*, *Nothing: it did not come in on a receipt* (a lot that came in neither on a receipt, a material receipt nor production, e.g. by a stock reconciliation: it is never released). Days count from the day the status was set (for a new lot, its creation). Readable by Stores, QC, QA, Purchase, Production, Management, Item Manager and System Manager.

### 2.6 The finished lot of a work order (URS-QC-004, interim)

A lot a manufacture entry made waits in the Quarantine store (the work order's Target Warehouse must be the Quarantine store, module 17). QA releases it with the same signed **Release**; no incoming inspection is required and Incoming Inspection stays empty. Final QC and batch release (modules 25, 26) replace this when they ship.

### 2.7 Material-receipt lots (URS-QC-003, decided 2026-10-09)

Opening stock and stock found in a count are loaded as Stock Entries of purpose *Material Receipt*, each a new lot in Quarantine. Such a lot is inspected on that entry (Reference Type *Stock Entry*, Reference Name the entry) and released by QA after the accepted inspection, exactly as a supplier's lot; it has no certificate step and no supplier return.

### 2.8 What is intended but not implemented

- Final QC and batch release of finished lots (modules 25, 26): today the finished lot is released on QA's signature alone (section 2.6).
- Sampling plans (ISO 2859-1) as data: the sample size is the specification's fixed minimum (module 6, demo value 5); since 2026-10-10 it is pre-filled on a new inspection and the inspector may raise it, never lower it.
- Printout: the default print format **Lot Release (Signed)** prints the decision with its signatures (2026-10-10).
- The return to the supplier is drafted only; Purchase's return process (module 15) submits it.
- Email delivery of anything here: the queue is a report, there is no alert for lots waiting.

## 3. Requirements and acceptance criteria

> **URS-QC-001** A goods receipt records, per line, the supplier's lot number and the supplier's certificate (analysis or conformity) as a file attached by Stores; the QC Inspector records its verification against the purchase specification, and incoming inspection cannot start for a lot whose certificate is not verified.
> **URS-QC-002** Incoming quality control is a Quality Inspection of type Incoming per received lot against the item's inspection template, recorded and submitted by a QC Inspector; the sample size, parameters and limits come from the template, each reading records the instrument used, and the result is Accepted or Rejected.
> **URS-QC-003** A lot that enters stock other than from a work order (on a goods receipt, or on a material receipt such as opening stock) is released by QA with an electronic signature only once the newest incoming inspection made on the document that brought it in is accepted: release sets the lot Approved and moves its stock in quarantine to the approved store in one audited transaction, never the quantity rejected at the door, which stays in the rejected store; QA can instead reject it with a signature and a reason, which sets it Rejected, moves that stock to the rejected store and drafts its return to the supplier for Purchase, the accepted quantity only; or hold it, which sets it Hold and moves it to the hold store; a held lot is released only after a new accepted inspection and with a reason. What the decision acts on, its quantity, where the lot came from and when it was held, is read when QA signs, not from the draft.
> **URS-QC-004** QA and Stores see every lot awaiting inspection and every inspected lot awaiting release, with the days each has waited, and the finished lot of a work order is released for dispatch by the same signed QA release until final QC and batch release (modules 25, 26) replace it.

| # | Acceptance criterion | Implementation | Automated verification (`lot_control/test_release.py` unless noted) |
| --- | --- | --- | --- |
| C1 | Stores records supplier lot number and certificate file per line | custom fields `CUSTOM_FIELDS` (`apply_receipt_fields`) | `TestSupplierCertificate.test_stores_records_the_suppliers_lot_number_and_certificate_on_the_line` |
| C2 | Only QC verifies; once; only with a file; only on a submitted receipt; audited under the inspector | `verify_certificate`, `protect_certificate` | `test_qc_verifies_the_certificate_and_the_audit_trail_says_so`, `test_only_qc_verifies_and_only_once_and_only_with_a_certificate`, `test_verification_names_a_real_line_of_a_submitted_receipt`, `test_only_the_qc_verification_marks_a_line_verified` |
| C3 | Verified certificate and lot number fixed; file protected evidence | `protect_certificate`, `evidence_files.EVIDENCE` | `test_a_verified_certificate_and_lot_number_no_longer_change`, `test_a_verified_certificate_file_is_evidence_nobody_deletes` |
| C4 | Inspection cannot start until verified | `guard_incoming_inspection` | `test_inspection_cannot_start_until_the_certificate_is_verified` |
| C5 | Incoming inspection per lot, against the item's template, names lot and receipt line, by QC | `guard_incoming_inspection`, module 6 `choose_specification` | `TestIncomingInspection.test_the_inspection_takes_its_parameters_from_the_template_and_names_the_lot_and_line`, `test_the_inspection_must_name_its_lot_and_the_lot_must_have_arrived_on_that_receipt`, `test_an_item_without_a_template_cannot_be_inspected`, `test_stores_cannot_record_an_inspection`, `test_only_incoming_inspections_are_held_to_the_receipt_rules` |
| C6 | Each measurement names its instrument | `guard_incoming_inspection` | `test_a_measurement_names_its_instrument` |
| C7 | Result Accepted or Rejected | ERPNext status; `accepted_inspection` | `test_a_rejected_reading_rejects_the_inspection`, `test_no_newer_accepted_inspection_is_found_after_a_later_time` |
| C8 | Release only with a signature and an accepted inspection on the document that brought the lot in; lot Approved and moved in one transaction | `release.decide`, `lot_status.move_to_role`, `set_status` | `TestTheSignedDecision.test_release_needs_an_accepted_inspection`, `test_release_signs_approves_and_moves_the_lot_in_one_transaction`, `test_a_wrong_password_decides_nothing`; `test_lot_status.py` `TestTheDecisionMovesTheStock.*` |
| C9 | Reject: signed, reason, Rejected, moved, draft return for Purchase | `reject`, `supplier_return_for` | `test_reject_moves_the_lot_to_the_rejected_store_and_drafts_the_return_for_purchase`, `test_a_rejected_lot_is_decided_no_further` |
| C10 | Hold: Hold, moved to hold store; release after a new accepted inspection, with a reason | `hold`, `accepted_inspection(after=held_since)` | `test_hold_moves_the_lot_to_the_hold_store_and_release_needs_a_new_inspection`, `test_an_inspection_only_changed_after_a_hold_is_not_a_new_inspection`, `test_a_draft_for_a_held_lot_saves_as_the_desk_sends_it` |
| C11 | Only QA; decided only by signing; never cancelled, deleted or edited, not even by the Administrator | controller `LotRelease` | `test_only_qa_creates_or_signs_a_decision`, `test_the_decision_is_never_submitted_unsigned_cancelled_or_deleted`, `test_the_details_of_a_draft_are_set_from_the_lot_not_typed`, `test_nobody_cancels_deletes_or_changes_a_decision_not_even_the_administrator`, `test_the_decisions_of_a_lot_are_indexed` |
| C12 | Lot origin from the stock ledger; delivery as a new lot; scanned/amended lots; no release of a lot that came in any other way or never came in | `origins_of`, `refuse_existing_lots`, `release.decide` | `TestWhereALotCameFrom.*`, `test_a_lot_that_never_came_in_is_not_released` |
| C13 | Material-receipt lot inspected on its entry and released after | `material_receipt_of` | `TestMaterialReceiptLots.*` |
| C14 | Queue shows what each lot waits for and for how long | report `lots_awaiting_release.py` | `TestTheQueueAndTheFinishedLot.test_the_queue_says_what_each_lot_waits_for_and_how_long`; `receipt_lines` empty case in `test_a_lot_that_no_supplier_delivered_has_no_receipt_line` |
| C15 | Finished lot released on the same signature | `made_by_production` | `test_a_lot_a_work_order_made_is_released_on_the_signature_alone`; `pilot/test_standard_flow.py` `TestStandardFlow` |
| C17 | A decision acts on the stock of the lot's status only: a release leaves the quantity rejected at the door in the Rejected store and its quantity counts the decided stock; a rejection drafts the return of that quantity only, which ERPNext accepts; Stores returns or scraps the door-rejected units while the lot is in any status | `release.decided_stock`, `lot_status.decided_kinds`, `supplier_return_for` | `TestADecisionActsOnTheStockOfItsStatus.*`; `test_lot_status.py` `test_a_hold_or_rejection_collects_unreleased_stock_from_an_approved_store` |
| C18 | What the decision rests on is read when signed: a draft made before a hold needs the new inspection; the newest inspection decides; Hold is signed *Held* | `release.decide`, `held_since`, `describe_lot`, `accepted_inspection` | `TestTheDecisionRestsOnTheLotAsItIsWhenSigned.*` |
| C19 | One new lot per receipt line | `refuse_existing_lots`, `lot_status.guard_new_stock` | `TestOneLotOneReceiptLine.test_the_same_new_lot_on_two_lines_is_refused` |
| C20 | Goods a customer sent back: Return to Stock after an inspection made since, for an Approved lot; Reject Return | `release.return_to_stock`, `release.reject_return` | `test_lot_status.py` `TestCustomerReturns.*` |
| C16 | Browser: Stores fills the line, Stores is refused the move, QA refused before verification, QC verifies, QC inspects, QA releases, finished lot released | `lot_control.js`, `lot_release.js` | `cypress/integration/standard_flow.js` steps "[URS-QC-001] QC verifies the supplier certificates on the receipt; QA cannot release before that", "[URS-QC-002] QC inspects each received lot against its template and submits the inspection", "[URS-STK-004] [URS-QC-003] QA releases each inspected lot with a signature; it is Approved in the approved store", "[URS-STK-004] [URS-QC-004] QA releases the finished lot to the finished goods store with a signature" |

**Partial / ambiguous (client to confirm):**
- URS-QC-002 "the sample size … come[s] from the template": fixed 2026-10-10 — pre-filled from the specification (`choose_specification`) and enforced as the minimum on submit (module 6).
- URS-QC-001 names the QC Inspector; the code also lets Quality Manager verify (`QC_ROLES`). Decide whether QA may stand in for QC.
- URS-QC-003 "a held lot is released only after a new accepted inspection": enforced for lots that came in on a receipt or material receipt; a held **production** lot is released on the signature alone (consistent with the interim rule of URS-QC-004).
- Fixed 2026-10-10: the queue's *Waiting For* for a held lot judges by the inspection's creation, as the release does (it compared the *modified* time, so an old inspection edited after a hold showed *QA release* while the release refused it).

**Not covered by this feature:** sampling plans; the supplier return's own process (module 15); final QC and batch release (25, 26).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, `scripts/create_pilot_site.sh`, demo data by `meddevice_suite.pilot.demo.load_demo_data`) or a test site after `prepare_test_site`. Demo data: supplier **Silicone Compounds (Demo)** approved by QA for **RM-SILICONE** (module 4 guide SA-01), item RM-SILICONE (Kg, lot series `RM-SILICONE-.YY..MM.-.####`, shelf life 730 days), specification *RM-SILICONE IQC (Demo)* (Appearance = Conforms; Dimension (mm) 9.5–10.5; sample size 5), instrument *Digital Caliper (Demo)*, stores `Quarantine - <abbr>`, `Stores - <abbr>` (approved), `Hold - <abbr>`, `Rejected - <abbr>`. Never use real supplier data. A small PDF or text file serves as the certificate.

Each test lists: role, preconditions, numbered steps, expected result. Record the actual result and a screenshot (form with its banner/message; the Electronic signatures table where relevant).

### LR-01 Stores receives goods into quarantine with lot number and certificate (C1)
Role: Stores. Preconditions: an approved, submitted Purchase Order for RM-SILICONE qty 10 from Silicone Compounds (Demo) (module 4 guide SA-04).
1. Open the order, **Create › Purchase Receipt**. Expected: new Purchase Receipt; every line's **Accepted Warehouse** = `Quarantine - <abbr>`.
2. Change the line's Accepted Warehouse to `Stores - <abbr>`; Save. Expected: saved, the warehouse is back to `Quarantine - <abbr>` (set, not refused).
3. Open the line's row form; in **Supplier Certificate** enter Supplier Lot No `SUP-2026-0417` and attach the certificate file in **Supplier Certificate (COA / COC)**. Save, Submit. Expected: submitted; a new lot `RM-SILICONE-…` with **Expiry Date** = posting date + 730 days; **Certificate Verified** unticked; no **Verify Certificates** button for Stores.
4. On the submitted receipt, change Supplier Lot No to `SUP-2026-0418` (Update after submit; give the reason the audit trail asks for). Expected: allowed (not yet verified).
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of the line's row form after step 3.

### LR-02 QC verifies the certificate; nobody else can (C2, C3)
1. As **Stores**: on the receipt of LR-01, try to tick **Certificate Verified** on the line (or via the API `frappe.client.set_value` on the Purchase Receipt Item). Expected: refused *Only QC verifies a supplier certificate.*
2. As **QC Inspector**: open the receipt (`/desk/purchase-receipt/<name>`). Expected: primary button **Verify Certificates**.
3. Click it. Expected: dialog **Verify supplier certificates**, line `1. RM-SILICONE · lot RM-SILICONE-… · supplier lot SUP-2026-0418` ticked.
4. Click **Verify**. Expected: form reloads; line shows Certificate Verified ticked, Verified By = qc.inspector, Verified At = now; the button is gone; menu **Audit Trail** shows *Updated after submission* by the inspector with reason *Supplier certificate verified for line 1*.
5. As Stores: change Supplier Lot No or replace the certificate file on the line. Expected: refused *The supplier certificate of line 1 has been verified; it cannot be changed.*
6. As Stores: open the certificate file's record and delete it. Expected: refused (protected evidence).
Actual result: ____  Pass/Fail: ____

### LR-03 A receipt without a certificate cannot be verified; a draft cannot (C2)
Role: QC Inspector. Preconditions: a second submitted receipt for RM-SILICONE qty 4 with no file on the line; a draft receipt with a file.
1. On the receipt without a file: Expected: no **Verify Certificates** button; calling `POST /api/method/meddevice_suite.lot_control.receipt.verify_certificate` with `receipt`, `row` is refused *Line 1 has no supplier certificate attached yet.*
2. On the draft receipt, call the same method. Expected: refused *Verify the certificate once the receipt is submitted.*
Actual result: ____  Pass/Fail: ____

### LR-04 QA cannot release before verification and inspection (C4, C8)
Role: QA Manager. Preconditions: a submitted receipt whose certificate is not yet verified (LR-03 step 1's receipt after Stores attaches a file).
1. **Stock › Lot Release › New**; Lot = that receipt's lot; Save. Expected: Item, Lot Status at Decision = Quarantine, Quantity in Stock = 4, Lot Came From = Purchase Receipt, Source = the receipt, Supplier filled; orange banner *The supplier certificate of this lot is not verified yet; QC verifies it on the receipt.*; buttons **Release**, **Reject**, **Hold**.
2. Click **Release**, Reason `too early`, password, **Sign**. Expected: refused *Lot … has no accepted incoming inspection; it cannot be released.*; record still a draft (*Awaiting decision* in the list).
3. QC verifies the certificate (LR-02 steps 2–4); QA reopens the draft. Expected: banner gone; Release still refused with the same message (no inspection yet).
Actual result: ____  Pass/Fail: ____  Keep the draft for LR-06.

### LR-05 QC inspects the lot (C5, C6, C7)
Role: QC Inspector. Preconditions: LR-02 done for the lot of LR-01.
1. **Quality › Quality Inspection › New**; Inspection Type `Incoming`; Reference Type `Purchase Receipt`; Reference Name = the receipt; Item Code `RM-SILICONE`. Expected: Readings show *Appearance* and *Dimension (mm)* with the specification's limits, read-only limits.
2. Leave Batch No empty; Sample Size 5; Reading Value `Conforms`; Reading 1 `10.00`; Save. Expected: refused *Name the lot this incoming inspection is of.*
3. Batch No = the lot; leave **Instrument** empty on the Dimension row; Save. Expected: refused *Reading 2 (Dimension (mm)) is a measurement; name the instrument it was taken with.*
4. Instrument = *Digital Caliper (Demo)*; Save. Expected: saved, Status *Accepted*.
5. Sample Size 2; Submit. Expected: refused *Specification RM-SILICONE IQC (Demo) samples at least 5; this inspection samples 2.*
6. Sample Size 5; Submit. Expected: submitted.
7. New inspection for the same lot with Reference Type `Stock Entry` and any entry. Expected: refused *Lot … came in on receipt …; its incoming inspection is made on that receipt.*
8. New inspection referencing another receipt that does not carry this lot. Expected: refused *Lot … of RM-SILICONE did not arrive on receipt ….*
9. As **Stores**: **Quality Inspection › New**. Expected: not permitted (no create permission).
Actual result: ____  Pass/Fail: ____

### LR-06 QA releases the lot with a signature (C8, C11)
Role: QA Manager. Preconditions: LR-05 step 6 for the lot of LR-01.
1. Open the lot (`/desk/batch/<lot>`), click **Decide Lot**; Save. Expected: new Lot Release for the lot, no banner.
2. Click **Release**; leave Reason empty; password; **Sign**. Expected: the dialog does not submit (Reason required).
3. Reason `Incoming inspection accepted`, **wrong** password, **Sign**. Expected: refused; record still a draft; lot still Quarantine.
4. Correct password, **Sign**. Expected: form reloads submitted; green banner *Release: Incoming inspection accepted*; Decision = Release, Incoming Inspection = the inspection of LR-05, Decided By = you, Decided At = now, Stock Moved By = a Stock Entry; **Electronic signatures** shows *Released*, your name, the time, *Release · Incoming inspection accepted*.
5. Open Stock Moved By. Expected: Material Transfer from `Quarantine - <abbr>` to `Stores - <abbr>` for the full quantity, **Posted by a Lot Release** ticked, Remarks = the reason; try **Cancel**: refused *… was posted by a lot release and is not cancelled; put the lot on hold instead.*
6. Open the lot. Expected: Lot Status = Approved, Status Set By = you, Status Set At, Status Reason = the reason, Latest Decision = LR-….
7. On the submitted Lot Release: menu **Cancel** → refused *A decision on a lot is never cancelled…*; **Delete** → refused *A lot release is never deleted.*; sign **Hold** again on it → refused *LR-… has been decided.*
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 4, 5 and 6.

### LR-07 QA rejects a lot; the return is drafted for Purchase (C9)
Preconditions: a verified lot with a **Rejected** inspection (LR-05 with Reading 1 `12.00`).
1. As QA: Lot Release for that lot, **Release** with a reason. Expected: refused *… has no accepted incoming inspection; it cannot be released.*
2. **Reject**, Reason `Dimension out of limits`, **Sign**. Expected: submitted; red banner; lot Rejected; stock in `Rejected - <abbr>`; **Return to Supplier** = a draft Purchase Receipt.
3. Open Return to Supplier. Expected: Is Return ticked, Return Against = the original receipt, one line: the lot, quantity −N, warehouse `Rejected - <abbr>`, draft.
4. As **Purchase**: open the same draft. Expected: readable (Purchase User reads Purchase Receipts).
5. As QA: Lot Release for the rejected lot, **Hold**. Expected: on the lot's form **Decide Lot** is not offered; a release made by URL and signed Hold is refused *Lot … is Rejected; it cannot be hold.*
Actual result: ____  Pass/Fail: ____

### LR-08 Hold and release after retest (C10)
Preconditions: an Approved lot (LR-06).
1. As QA: Lot Release for the lot. Expected: only **Hold** offered (Release and Reject hidden for an Approved lot).
2. **Hold**, Reason `label unreadable`, Sign. Expected: orange banner *Hold: label unreadable*; lot Hold; stock moved to `Hold - <abbr>`.
3. New Lot Release for the lot; Save. Expected: **On Hold Since** filled; **Release** and **Reject** offered.
4. **Release**, Reason `looked fine`, Sign. Expected: refused *… has no accepted incoming inspection made after it was put on hold; it cannot be released.*
5. As QC: new Incoming inspection on the receipt for the lot, accepted, submitted. As QA: **Release**, Reason `retested and accepted`, Sign. Expected: submitted; lot Approved; stock back in `Stores - <abbr>`; Incoming Inspection = the new inspection. (That an old inspection merely edited after the hold does not count is covered by the server test `test_an_inspection_only_changed_after_a_hold_is_not_a_new_inspection`; it needs a direct database edit and is not a browser step.)
Actual result: ____  Pass/Fail: ____

### LR-09 Permissions on the decision (C11)
1. As **QC Inspector**: **Stock › Lot Release › New**. Expected: not permitted (no create); an existing LR-… opens read-only with no Release/Reject/Hold buttons.
2. As **Stores**: open an LR-…. Expected: read-only; **Decide Lot** absent on the lot's form.
3. As **QA Manager**: on a draft, set Decision = Release through the form (if editable) or `frappe.client.set_value`; Save. Expected: refused *LR-… is decided by signing it; its details are set by the decision.*
4. As QA: on a draft click Frappe's **Submit** (if ever shown) or call the API submit. Expected: refused *LR-… is decided only by signing it.*
5. As **System Administrator**: try to delete or cancel a submitted LR-…. Expected: refused as in LR-06 step 7.
Actual result: ____  Pass/Fail: ____

### LR-10 New lot per delivery; lot origin (C12)
1. As Stores: new Purchase Receipt for Silicone Compounds (Demo), RM-SILICONE qty 2; on the line tick **Use Serial No / Batch Fields** and pick **Batch No** = the Approved lot of LR-06; Save. Expected: refused, title *New lot needed*: *Line 1: lot … came in on Purchase Receipt …; a delivery comes in as a new lot.*
2. Make a batch by hand first (**Stock › Batch No › New**, Item RM-SILICONE, Batch ID `SCAN-0001`, no stock); on a new receipt line pick that Batch No; Save, Submit. Expected: accepted; a Lot Release for `SCAN-0001` shows Lot Came From = Purchase Receipt, Source = this receipt.
3. Make a batch by hand with no stock and open a Lot Release for it; sign Release. Expected: refused *Lot … came in on an unknown document ; only stock received on a goods receipt or a material receipt is released.*
Actual result: ____  Pass/Fail: ____

### LR-11 Material-receipt lot (opening stock) (C13)
1. As Stores: **Stock › Stock Entry › New**, Stock Entry Type `Material Receipt`, line RM-SILICONE qty 3, Target Warehouse `Quarantine - <abbr>`, Basic Rate 1450; Submit. Expected: a new lot in Quarantine.
2. As QA: Lot Release for it, **Release**. Expected: refused *… has no accepted incoming inspection; it cannot be released.*
3. As QC: Incoming inspection with Reference Type `Purchase Receipt`. Expected: refused *Lot … came in on material receipt SE-…; its incoming inspection is made on that entry.*
4. As QC: Reference Type `Stock Entry`, Reference Name = the entry; accepted; Submit. As QA: **Release**, Reason `Opening stock inspected`. Expected: submitted; lot Approved in `Stores - <abbr>`.
Actual result: ____  Pass/Fail: ____

### LR-12 The queue (C14)
Role: Stores, then QA. Preconditions: lots in the states of LR-01 (no certificate), LR-02 (verified, not inspected), LR-07 step 1 (rejected inspection), LR-05 (ready), LR-06 (released), LR-08 step 2 (held).
1. **Stock › Lots Awaiting Release**, filter Item = RM-SILICONE. Expected: one row per waiting lot with Waiting For = *Supplier certificate* / *Certificate verification by QC* / *Incoming inspection* / *Decision on a rejected inspection* / *QA release*; the held lot *Incoming inspection*; the released lot absent; Days Waiting ≥ 0, longest first; Came From = `Purchase Receipt <name>`.
2. As QA: **Quality › Lots Awaiting Release**. Expected: same rows.
3. As **Sales**: open `/desk/query-report/Lots Awaiting Release`. Expected: Sales User is not among the report's roles; opening is refused.
Actual result: ____  Pass/Fail: ____

### LR-13 Finished lot released on the signature (C15)
Preconditions: a completed work order whose finished lot sits in `Quarantine - <abbr>` (module 17 guide / journey steps).
1. As QA: open the finished lot, **Decide Lot**, Save, **Release**, Reason `Final inspection accepted`, Sign. Expected: submitted; Incoming Inspection empty; stock moved to the product's default store `Finished Goods - <abbr>`; lot Approved.
Actual result: ____  Pass/Fail: ____

### LR-15 A delivery with units rejected at the door (C17)
Preconditions: a new Purchase Receipt for Silicone Compounds (Demo), RM-SILICONE, Received Quantity 8, Accepted Quantity 6, Rejected Quantity 2, Rejected Warehouse `Rejected - <abbr>`, supplier lot and certificate; submitted.
1. **Stock › Stock by Lot Status**, the receipt's lot. Expected: 6 in `Quarantine - <abbr>` and 2 in `Rejected - <abbr>`, one lot.
2. As QA: Lot Release for the lot; Save. Expected: Quantity in Stock = 6. **Lots Awaiting Release** shows the lot once, in `Quarantine - <abbr>`.
3. As Stores, while the lot is Quarantine: on the receipt, **Create › Return of rejected qty**; Submit. Expected: allowed; the 2 leave `Rejected - <abbr>`. (Or Material Issue of the 2 from `Rejected - <abbr>`: allowed, scrap.)
4. As QC and QA: verify, inspect (accepted), sign **Release**. Expected: Stock Moved By moves 6 from `Quarantine - <abbr>` to `Stores - <abbr>`; nothing from `Rejected - <abbr>`.
5. On a second such receipt with a rejected inspection: QA signs **Reject**. Expected: 6 moved to `Rejected - <abbr>` (8 there with the door rejection); Return to Supplier is a draft of −6, Rejected Quantity 0; as Purchase submit it: accepted by ERPNext; then Stores scraps the remaining 2 by Material Issue.
6. As Stores: Material Transfer of the lot from `Rejected - <abbr>` to `Stores - <abbr>`. Expected: refused, whatever the lot's status.
Actual result: ____  Pass/Fail: ____

### LR-16 A stale draft and a newer rejected inspection (C18)
1. As QA: a draft Lot Release for a ready lot; do not sign it. On another Lot Release sign **Hold** (signature *Held*). Back on the first draft sign **Release**. Expected: refused *… has no accepted incoming inspection made after it was put on hold; it cannot be released.*
2. As QC: a new accepted inspection. As QA: sign **Release** on the first draft. Expected: submitted; Lot Status at Decision = Hold, On Hold Since filled, Quantity in Stock = the held quantity.
3. On another ready lot, QC submits a second inspection that is **Rejected**. As QA: **Release**. Expected: refused *… has no accepted incoming inspection; it cannot be released.*
Actual result: ____  Pass/Fail: ____

### LR-14 Lists, filters and audit trail
1. **Stock › Lot Release** list: filter Decision = Release, by Lot; indicator colours as in section 2.4. Expected: filters work.
2. Open a submitted LR-… › **Audit Trail**; **Verify Integrity**. Expected: *Created* and *Submitted* events; the audit trail and signatures are intact.
Actual result: ____  Pass/Fail: ____

### Regression
- The full journey (`cypress/integration/standard_flow.js`) still passes: receipt → refusal of the move → verification → inspection → release → production → finished-lot release → delivery.
- Module 4: a conditional supplier's lot still needs Sample Size = the quantity received (guide SA-05).

## 5. End-to-end business workflow

1. **Purchase** orders 10 Kg RM-SILICONE from Silicone Compounds (Demo) (approved by QA, module 4); **Management** approves (module 48).
2. **Stores** receives the delivery on a **Purchase Receipt**: lot `RM-SILICONE-26-10-0001` in `Quarantine - <abbr>`, Supplier Lot No `SUP-2026-0417`, certificate attached. Data: Purchase Receipt (submitted), Batch (Quarantine, expiry +730 days), Stock Ledger Entries, Serial and Batch Bundle, Audit Events.
3. **QC Inspector** opens the receipt, **Verify Certificates** → the line is verified (audited). Then records the **Quality Inspection** (Incoming, on the receipt, lot named, Sample Size 5, Appearance = Conforms, Dimension 10.00 mm with *Digital Caliper (Demo)*) → Accepted, submitted.
4. **QA Manager** opens the lot, **Decide Lot**, signs **Release** with the reason *Incoming inspection accepted* → Lot Release LR-2026-00001 submitted, Electronic Signature *Released*, lot Approved, Material Transfer Quarantine → Stores (marked Posted by a Lot Release), Batch fields Status Set By/At/Reason/Latest Decision.
5. Had the inspection failed: QA signs **Reject** → lot Rejected, stock in `Rejected - <abbr>`, draft purchase return for **Purchase** to submit. Had QA doubts: **Hold** → lot in `Hold - <abbr>`; a new accepted inspection and a signed Release bring it back.
6. **Production** issues the Approved lot to the work order (only Approved lots can be issued, module 17); the finished lot `FC-2W-SIL-16-…` lands in Quarantine; QA signs its **Release** → Finished Goods; **Stores** delivers it.
7. Throughout, **Lots Awaiting Release** shows Stores and QA what is waiting and for how long.

Cross-module data changes: Purchase Receipt Item (`mds_supplier_lot_no`, `mds_certificate`, `mds_certificate_verified*`, `mds_full_inspection`), Quality Inspection (`child_row_reference`, readings' `mds_instrument`), Lot Release (new), Batch (status fields, module 17), Stock Entry (`mds_lot_release_transfer`), Purchase Receipt return (draft), Electronic Signature, Audit Event, File (evidence).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocType | `Lot Release` (submittable, `LR-{YYYY}-{#####}`, module Lot Control, title field Lot); index `(batch, decided_at)` (`on_doctype_update`) |
| Custom fields | Purchase Receipt Item: `mds_certificate_section`, `mds_supplier_lot_no`, `mds_certificate` (Attach), `mds_certificate_verified` (Check, read-only), `mds_certificate_verified_by`, `mds_certificate_verified_at`; Quality Inspection Reading: `mds_instrument` (Link Asset). Applied by `apply_receipt_fields()` from `setup/install.py` |
| Server modules | `lot_control/receipt.py`: `verify_certificate` (whitelisted POST), `protect_certificate`, `origins_of`, `origin_of`, `material_receipt_of`, `made_by_production`, `receipt_line_of`, `refuse_existing_lots`, `received_quantity`, `guard_incoming_inspection`, `accepted_inspection` (the newest inspection decides); `lot_control/release.py`: `release`, `reject`, `hold`, `return_to_stock`, `reject_return`, `decide`, `decided_stock`, `held_since`, `supplier_return_for`, `locked`, `require`; `DECISIONS` (`Decision`: start statuses, end status or none, store role, whether it acts on the Returns store) |
| Controller | `lot_control/doctype/lot_release/lot_release.py` (`before_insert` QA only + `describe_lot`; `validate` only `batch` changes on a draft; `before_submit`/`before_update_after_submit` only by the signed act; `on_trash`, `before_cancel` refused); `onload` passes `lot` (status, from_supplier, certificate_verified, returned) to the form |
| Hooks (`hooks.py`) | Purchase Receipt `validate`: `lot_status.guard_purchase_receipt`, `receipt.protect_certificate`, `receipt.refuse_existing_lots`, `supplier_approval.mark_full_inspection`; `before_update_after_submit`: `receipt.protect_certificate`. Quality Inspection `before_validate`: `specification.choose_specification`; `validate`: `specification.check_against_specification`, `receipt.guard_incoming_inspection`; `before_submit`: `specification.check_sample_size`. Stock Entry `before_cancel`: `lot_status.refuse_cancel_of_release_transfer` |
| Signing | `compliance/signing.py` `SIGNING_ACTIONS`: ("Lot Release", "Release") → meaning *Released*, `release.release`; ("Lot Release", "Reject") → *Rejected*, `release.reject`; ("Lot Release", "Hold") → *Held*, `release.hold`; ("Lot Release", "Return to Stock") → *Released*, `release.return_to_stock`; ("Lot Release", "Reject Return") → *Rejected*, `release.reject_return`. The meaning *Held* (*I hold this from use.*) is in `esign.MEANINGS`, the Electronic Signature's Meaning options and `esign.js`. Dialog `public/js/esign.js` |
| API routes | `POST /api/method/meddevice_suite.lot_control.receipt.verify_certificate` (`receipt`, `row`); `POST /api/method/meddevice_suite.compliance.signing.sign_action` (`doctype`="Lot Release", `name`, `action`, `password`, `reason`); standard `/api/resource/Lot Release` (create/read under the DocType permissions; submit, cancel, delete refused) |
| Client scripts | `public/js/lot_control.js` (Purchase Receipt **Verify Certificates** + dialog; Batch **Decide Lot**); `lot_release.js` (act buttons, certificate banner, decision banner); `lot_release_list.js` (indicators) |
| Report | `Lots Awaiting Release` (Script Report, ref Batch; `lots_awaiting_release.py` `execute`, `waiting_for`, `latest_inspections`, `receipt_lines`; filter `item_code`); roles: Stock User/Manager, Quality Manager, QC Inspector, Purchase User/Manager, Manufacturing User/Manager, Sales Manager, Item Manager, System Manager |
| Permissions | Lot Release: Quality Manager read/write/create/report/print/export; QC Inspector, Stock User, Purchase User, Manufacturing User, System Manager read/report/print; Stock/Purchase/Manufacturing/Sales Manager + export; nobody submit/cancel/amend/delete/import/share/email. Quality Inspection (`setup/roles.py`): QC Inspector ENTER, Quality Manager APPROVE, Stock/Purchase/Manufacturing User VIEW. Asset: QC Inspector select (the instrument). Stock Entry and Serial and Batch Bundle: Quality Manager write/create/submit for the release's transfer only (`POSTED_BY_SERVICES`) |
| Desk | `setup/sidebars.py` `SIDEBAR_ADDITIONS`: Stock → Lot Release, Lots Awaiting Release, Stock by Lot Status; Quality → Lot Release, Lots Awaiting Release. Areas (`setup/desk.py`): Stock for stock, purchase, sales, production and quality roles and Item Manager; Quality for quality and production roles |
| Compliance registries | `compliance/esign.py` `SYSTEM_MAINTAINED["Lot Release"] = ("docstatus", "stock_entry", "supplier_return")`; `compliance/evidence_files.py` (verified certificate); `compliance/retention.py` *Device and production records*, 10 years (Lot Release, Batch, Quality Inspection); `compliance/audit_scope.py` (Purchase Receipt, Quality Inspection, Batch, Stock Entry, Serial and Batch Bundle regulated; every app DocType regulated) |
| Jobs | none scheduled; everything runs on the user's action |
| Data flow | Receipt save → quarantine store set → existing-lot check (`origins_of`, 2 queries) → full-inspection mark. **Verify** → `verify_certificate` → `doc.save(ignore_permissions)` with `flags.certificate_verification` → audit. Inspection save → specification chosen → `guard_incoming_inspection` (`receipt_line_of` → verified? → `check_full_inspection`) → instrument check. **Sign** → `sign_action` → `release.decide` (lock record + lot → checks → `accepted_inspection` → `esign.sign` → `move_to_role` (Material Transfer under the QA user, `frappe.flags.lot_release_transfer`) → `set_status` → return draft → `submit(ignore_permissions)`) |

## 7. Defects, edge cases and risks

**Confirmed and fixed during review (all with tests; commits 492db989, 314f3e61, e0b839a8, 26ceb59e, 17fc4cce, 96090f29):** Stores could set a line verified through the API; a verified certificate or lot number could still be changed; an old inspection modified after a hold counted as a new one; QA saving a draft decision for a held lot from the desk was refused (datetime sent as text); a batch made beforehand hid where its stock came from, so a received lot could be released uninspected (origin now from the stock ledger); an inspection on another document released a received lot; a lot released "on documents only" (never came in) is now refused; a scanned or amended receipt's lot was refused as "existing" (now only a lot that already holds stock is); material-receipt lots were released uninspected (now inspected on their entry); a count of an existing lot was refused as new stock (now a loss or no change passes, a gain is refused).

**Potential (not verified by a test, listed for the record):**
- Fixed 2026-10-10: the queue's *Waiting For* for a held lot now uses the inspection's creation (`latest_inspections` orders by `creation`), as `accepted_inspection` does; covered by the queue test's held lot.
- `supplier_return_for` sets `use_serial_batch_fields` and the lot on the return; the return is a draft that Purchase submits; ERPNext's own return validation runs then, not at rejection. ERPNext v16 sums every earlier return of a receipt line against its accepted quantity, whatever the store: when Stores has already returned the units rejected at the door on *Return of rejected qty*, QA's draft of the accepted quantity can be refused as over-returned (and the other way round). Submit QA's draft first, or scrap the door-rejected units; an ERPNext limitation, recorded here.
- Quality Manager may verify certificates (`QC_ROLES`), wider than the requirement's wording.
- Fixed 2026-10-12: a Lot Release counted and moved the lot's stock in every store, the Rejected store included, so a release put the units rejected at the door into approved stock and a rejection's return failed ERPNext's checks; the draft's hold time and quantity were signed as they stood when it was made; an older accepted inspection outweighed a newer rejected one; Hold was signed *Approved*; an expired lot's stock was not moved. Now as section 2.4 says. A lot spread over two companies is refused at release (*… has stock in more than one company; move it by hand first.*), tested in module 17.

**Unverified in this document:** the exact wording the desk shows when a role without create permission opens `/desk/lot-release/new` (Frappe's own message); whether ERPNext's batch selector on the receipt line offers the Approved lot of LR-10 step 1 at all (the server refuses it either way).

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch, local test site `test.localhost`, macOS:

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (2026-10-10) |
| Module tests (part of the suite) | `lot_control/test_release.py`: `TestSupplierCertificate` (10), `TestIncomingInspection` (8), `TestTheSignedDecision` (13), `TestTheQueueAndTheFinishedLot` (2), `TestMaterialReceiptLots` (3), `TestWhereALotCameFrom` (8); `pilot/test_standard_flow.py` `TestStandardFlow` (1) — counts read from the files | passed within the suite above |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed (2026-10-10, complete code); the journey steps of C16 among them; `screen_sweep.js` "[URS-PLT-009] <job>: every area, list and form of its work opens without an error" opens the Lot Release list and the two reports as each job that sees them |
| Semgrep (Frappe rules + python correctness) | — | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| CI (fresh site) | GitHub Actions on this branch | **in progress** when this document was written |
| Revision of 2026-10-12 (C17–C20) | `lot_control/test_release.py`, `test_lot_status.py` | **not yet run**; LR-15, LR-16 not executed |
| Manual browser tests LR-01…LR-14 | — | **not executed** (no manual tester session); the automated journey covers LR-01 steps 1–3, LR-02 steps 2–4, LR-04 steps 1–2, LR-05 steps 1, 4 and 6, LR-06 steps 1, 4 and 6, LR-13 |

Untested: LR-07 (reject and return) and LR-08 (hold and retest) in the browser (server tests only); LR-10 step 2 in the browser; the client's real certificates and specifications (pilot uses demo data).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Supplier lot number and certificate on each receipt line; only QC verifies, once, audited; verified data fixed and the file protected | Verified (automated) |
| Incoming inspection waits for verification, names its lot and document, reads the specification, names instruments, Accepted / Rejected | Verified (automated + journey) |
| Release only signed, only with an accepted inspection on the lot's own document; Approved and moved in one transaction | Verified (automated + journey) |
| A decision acts only on the stock of the lot's status; door rejections stay rejected; the return drafted for the accepted quantity only; read again when signed; newest inspection decides; Hold signed *Held*; returns disposition | **Not tested** (tests written 2026-10-12, not yet run) |
| Reject: Rejected, moved, draft return for Purchase | Verified (automated) |
| Hold: Hold, moved; release only after a new inspection made after the hold | Verified (automated) |
| Decision never cancelled, deleted, edited or submitted unsigned, not even by the Administrator | Verified (automated) |
| Lot origin from the stock ledger; new lot per delivery; material-receipt lots inspected; no release of a lot that came in any other way | Verified (automated) |
| Queue with Waiting For and Days Waiting | Verified (automated); report-only hold inconsistency fixed 2026-10-10 |
| Finished lot released on the same signature | Verified (automated + journey) |
| Browser: Verify Certificates dialog, Decide Lot, act buttons, signing dialog, banners | Verified for the journey path (automated); full manual LR-01…LR-14 **Not tested** |
| CI green on a fresh site for the final commit | **Blocked** (in progress) |
| Client decisions: QA verifying in place of QC; held production lot without a new inspection | **Open** |
