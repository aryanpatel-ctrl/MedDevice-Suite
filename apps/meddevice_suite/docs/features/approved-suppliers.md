# Approved suppliers (module 4) — feature document and browser test guide

Requirement: **URS-PUR-001** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-QC-001, URS-QC-003 (receipt and release rules that this feature tightened).
Code: `meddevice_suite/master_data/supplier_approval.py`, `master_data/doctype/supplier_approval/`, `master_data/doctype/supplier_approval_item/`, `master_data/notification/supplier_certificate_expiring/`, `public/js/master_data.js`, hooks in `meddevice_suite/hooks.py`; receipt-side rules in `lot_control/receipt.py`.
State on 2026-10-10: built, reviewed in six independent review rounds, server and browser suites green locally; final CI run and final verification of the last fix were still running when this document was written (section 8 says exactly what was and was not executed).

## 1. Feature overview

**Purpose.** Nothing is bought from a supplier that QA has not approved, and nothing is bought from an approved supplier except the items QA approved it for. The decision is QA's, signed electronically, with a reason, and it carries the supplier's quality-system certificate. A conditionally approved supplier (for example a new supplier on trial) is held to extra controls: every order needs a written reason and every lot it delivers is inspected in full. QA and Purchase are warned 30 days before a supplier's certificate expires.

**Why it matters.** ISO 13485 7.4.1 requires purchased product to come only from evaluated and approved suppliers, and the DFD places vendor management in process 2 (2.5 Vendor Management, data store D2 Vendor Master). In a device factory an unapproved supplier's material in production is a regulatory finding; the system makes that path impossible rather than merely discouraged.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Prepares and signs the decision on a supplier |
| Purchase (`purchase@demo.example.com`) | Purchase User | Places purchase orders; sees the decision; receives the certificate alert |
| Management (`management@demo.example.com`) | Purchase Manager (+ others) | Approves purchase orders; receives the certificate alert |
| Stores (`stores@demo.example.com`) | Stock User | Receives goods; the receipt is checked against the decision |
| QC Inspector (`qc.inspector@demo.example.com`) | QC Inspector | Inspects received lots; a conditional supplier's lot must be inspected in full |

Only QA Manager can create or edit a Supplier Approval. Purchase, Management, QC and System Manager can read it. Stores and Sales cannot open it (they see the decision on the supplier's form instead).

**Prerequisites.** The supplier and the items exist; QA has the supplier's certificate as a file; the purchase-order approval workflow (module 48) and the e-signature service (module 49) are installed (they are part of the app's configuration, applied by `bench migrate`).

## 2. Detailed functional explanation

### 2.1 The decision record: Supplier Approval

Navigation: **Buying › Supplier Approval** or **Quality › Supplier Approval** (both areas list it), or from a supplier's form with the button **Decide Supplier Approval** (QA only; it opens a new decision with the supplier filled in). Route `/desk/supplier-approval/new`.

| Field (label on screen) | Set by | Rule |
| --- | --- | --- |
| Supplier | QA | Required |
| Supplier Name | system | from the supplier |
| Conditional Until | QA | Only for a conditional approval; must be a day after today; must be empty for Approve / Not Approve |
| Quality System Certificate No, Certificate Expiry, Certificate (file) | QA | All three required for Approve and Approve Conditionally; expiry must be after today |
| Items (table: Item, Item Name) | QA | Required for Approve / Approve Conditionally, each item once; must be empty for Not Approve |
| Decision, Reason, Decided By, Decided At | signature only | Read-only; set when QA signs; sent values are discarded on insert and refused on save |
| Certificate Expiry Alerted | system | Bookkeeping of the alert; outside the signature |

Record name: `SA-YYYY-NNNNN`.

**Buttons** (on a saved, unchanged draft, QA only): **Approve** (primary), **Approve Conditionally**, **Not Approve**. Each opens the signing dialog **Sign: <act>** with a **Reason** box (required) and **Your password**; the **Sign** button records an Electronic Signature (meaning *Approved* for the first two, *Rejected* for Not Approve), fills the decision fields and submits the record in one transaction. A wrong password signs nothing and the record stays a draft. Frappe's own Submit button is never offered and the API's submit is refused: the signed act is the only submit.

After signing, the form shows a coloured banner: *Approved: <reason>* (green), *Conditional until <date>: <reason>* (orange), *Not Approved: <reason>* (red), and the **Electronic signatures** table (meaning, signer, date and time, reason).

**Printout.** The record's default print format, **Supplier Approval (Signed)**, prints the decision as stored with the **Electronic signatures** table (meaning, act, signer, date and time, reason); a draft prints with *DRAFT – not signed* (URS-CMP-003).

**Permanence.** A decision is never cancelled or deleted (both refused, also for the Administrator). A newer decision replaces it: *the decision in force* is the newest submitted one for that supplier (by Decided At). The certificate file of a submitted decision is protected evidence (cannot be changed or deleted). Supplier approvals are in the 10-year "Purchasing and supplier records" retention class.

### 2.2 The supplier's form

Every Supplier form (`/desk/supplier/<name>`) shows a banner with the decision in force:

- red *Not yet approved by QA: nothing can be ordered from this supplier.* (no decision), or *Not approved by QA (SA-…): nothing can be ordered from this supplier.*, or *Conditional approval SA-… ended on <date>: QA decides the supplier again.*
- orange *Approved conditionally until <date> (SA-…) for: <items>*
- green *Approved by QA (SA-…) for: <items>*

QA Manager also sees **Decide Supplier Approval**.

### 2.3 Purchase orders

On every save of a Purchase Order (desk, API, Data Import, orders made from a Material Request, Supplier Quotation, Blanket Order, etc.) and again when it is sent for approval and approved through the workflow, the order is refused (red message, title *Supplier not approved* / *Supplier certificate expired* / *Reason needed*) when:

| Condition | Message |
| --- | --- |
| no decision, or decision Not Approved | *Supplier X is not approved by QA; nothing can be bought from it.* |
| conditional approval ended (Conditional Until before today) | *The conditional approval of supplier X ended on <date>; QA decides the supplier again before anything is bought from it.* |
| certificate expired (Certificate Expiry before today) | *The quality system certificate of supplier X expired on <date>; QA decides the supplier again on its renewed certificate before anything is bought from it.* |
| an item not in the approval's items | *Supplier X is not approved for A, B; QA approves it for them before they are bought.* |
| supplier conditional and the order has no reason | *Supplier X is approved only conditionally, until <date>; say why this order is placed with it.* |

An accepted order records **Supplier Approval** (`mds_supplier_approval`, read-only, which decision it was placed under) and, for a conditional supplier, **Why This Conditionally Approved Supplier** (`mds_conditional_reason`, required). Both sit under the Supplier field.

A submitted order edited with ERPNext's **Update Items** is checked line by line again; any other change of a submitted order (delivery date, supplier's confirmation number) is allowed even if the supplier has since been stopped, because it buys nothing new. A duplicated, amended or auto-repeated order is a new order and is checked afresh. An order created by a tool that skips validation (production plan, bulk tools) is stopped when it is sent for approval.

Boundary: the last day is still valid — an order on the Conditional Until date or the Certificate Expiry date itself is allowed; the day after, refused.

### 2.4 Goods receipt

On every save of a Purchase Receipt (not a return):

1. **Every line** needs the supplier approved for its item **now**, whatever order it was placed under (a supplier QA has since stopped is decided again first). Decided 2026-10-09.
2. A line counts as *ordered* only when it names a line of this supplier's submitted order for the same item (checked in one query); such a line was checked when the order was placed. A line naming another supplier's order line, or an order but no order line, is checked as if unordered.
3. **Inspect Each Lot in Full** (`mds_full_inspection`, read-only, on the line) is set when the order line's order was placed under a Conditional decision, or the supplier is conditionally approved now.
4. **Goods enter only on a goods receipt.** A Purchase Invoice with *Update Stock* ticked for a stock item is refused (*Goods are received on a goods receipt, not on a purchase invoice; untick Update Stock and book the invoice against the receipt.*). Returns are excepted.
5. A delivery comes in as a **new lot**: a line naming a lot that already holds stock is refused (*Line N: lot L came in on <document>; a delivery comes in as a new lot.*). A batch made beforehand without stock (scanned on the receipt) is accepted.

### 2.5 Incoming inspection of a conditional supplier's lot

An incoming Quality Inspection of a lot whose receipt line is marked *Inspect Each Lot in Full* is refused on save unless **Sample Size** is at least the quantity the line received (*Lot L was ordered from a conditionally approved supplier and is inspected in full: the sample size is the lot's N.*). A received lot is inspected only on its receipt (an inspection referencing any other document is refused) and only such an inspection lets QA release it (module 16).

### 2.6 Certificate expiry alert

A daily job (`send_certificate_alerts`, scheduler `daily`) sends the system notification **Supplier Certificate Expiring** (*Certificate of supplier X expires on <date>*) to everyone with the Quality Manager, Purchase User or Purchase Manager role, for each decision in force that is Approved or Conditional and whose certificate expires within 30 days (today + 30 or sooner, so a missed day is caught up). Each decision is alerted once (`Certificate Expiry Alerted`); a decision replaced by a newer one is not alerted. A failed send is logged in Error Log and retried the next day. Email is sent only once the site's mail server is configured (module 51); until then the alert appears in the desk bell.

### 2.7 What is intended but not implemented

- Vendor rating / scorecard (module 4's title) — not in URS-PUR-001; ERPNext's Supplier Scorecard is untouched and independent.
- "Approved for" is per item; whether approving a template item covers its variants is an open client question (section 7).
- Email delivery of the alert depends on the site's mail setup (not part of this feature).

## 3. Requirements and acceptance criteria

URS-PUR-001 text (as updated with the client decisions of 2026-10-08/09):

> Every supplier has an approval status, Approved, Conditional or Not Approved, set by QA with an electronic signature and a reason, with the items it is approved for and the expiry date of its certificate; a purchase order for an item from a supplier not approved for it is refused; goods are received only on a goods receipt, never by a purchase invoice that updates stock, and each line only from a supplier approved for its item at the time of receipt, whatever order it was placed under; a Conditional approval has an expiry date, each purchase order under it needs a reason and its lots are inspected at 100 percent; QA and Purchase are alerted once, 30 days before a supplier certificate expires.

| # | Acceptance criterion | Implementation | Automated verification (`master_data/test_supplier_approval.py` unless noted) |
| --- | --- | --- | --- |
| C1 | A supplier's status is one of Approved / Conditional / Not Approved; no decision = nothing can be bought | `current_approval`, `approval_in_force` | `test_an_order_from_a_supplier_without_a_decision_is_refused`, `…not_approved_is_refused` |
| C2 | Only QA sets it; nobody sets it by hand, not even the Administrator | controller `SupplierApproval`, `decide` | `test_only_qa_prepares_or_decides`, `test_the_decision_is_set_only_by_signing`, `test_permission_overrides_do_not_let_others_decide`, `test_not_even_the_administrator_sets_a_decision_by_hand`, `test_a_decision_sent_with_the_record_is_not_taken` |
| C3 | Each decision is e-signed (who, when, meaning) | `esign.sign` via `SIGNING_ACTIONS` | `test_qa_approves_a_supplier_for_its_items_with_a_signature`, `test_the_decision_is_signed_with_its_meaning_and_reason_and_audited`, `test_a_wrong_password_decides_nothing` |
| C4 | Each decision has a reason | `decide` | `test_a_decision_needs_a_reason` |
| C5 | Items approved for, each once; none when Not Approved | `decide` | `test_an_approval_needs_items_and_a_current_certificate`, `test_a_supplier_not_approved_lists_no_items` |
| C6 | Certificate number, expiry and file; not already expired | `decide` | `test_an_approval_needs_items_and_a_current_certificate` |
| C7 | PO refused for unapproved supplier or item, on every path | `guard_purchase_order`, `guard_order_update`, workflow re-check | `TestPurchaseOrder.*`, `TestRequirementMatrix` (material request, supplier quotation, past validation, copy/amend, Update Items, supplier changed on a draft, re-check at approval) |
| C8 | Conditional approval has its own end date, after today | `decide` | `test_a_conditional_approval_holds_until_a_day_to_come`, `test_only_a_conditional_approval_has_an_end_date` |
| C9 | Each PO under a conditional approval needs a reason | `guard_purchase_order` | `test_a_conditional_supplier_needs_a_reason_on_each_order` |
| C10 | Lots from a conditional supplier are inspected 100% | `mark_full_inspection`, `check_full_inspection`, receipt rules | `TestFullInspection.*`, `test_a_received_lot_is_inspected_only_on_its_receipt` |
| C11 | Goods only on a goods receipt; line checked at receipt, whatever order | `refuse_stock_on_invoice`, `mark_full_inspection` | `test_goods_are_received_only_on_a_goods_receipt`, `test_goods_ordered_earlier_are_refused_once_qa_stops_the_supplier`, `test_a_receipt_line_naming_*` |
| C12 | Alert once, 30 days before expiry, to QA and Purchase; catch-up after a missed day | `send_certificate_alerts` | `TestCertificateAlert.*`, `test_a_certificate_inside_30_days_is_alerted_once_even_after_a_missed_day` |
| C13 | Expired certificate / ended condition refuse buying; last day still valid | `approval_in_force` | `test_an_expired_certificate_refuses_the_order`, `test_an_ended_conditional_approval_refuses_the_order`, `test_a_conditional_approval_and_a_certificate_hold_through_their_last_day` |
| C14 | Decision never cancelled or deleted; certificate protected evidence | controller, `evidence_files` | `test_a_decision_is_never_cancelled_or_deleted`, `test_the_decided_certificate_is_protected_evidence` |
| C15 | Browser: QA decides on screen with a signature; PO shows the approval | `supplier_approval.js`, `master_data.js` | `cypress/integration/standard_flow.js` step "QA approves the supplier for the raw materials…" and the purchase step's `mds_supplier_approval` check |
| C16 | The printout shows the signatures (URS-CMP-003) | print format `Supplier Approval (Signed)`, default for the record type | `test_the_printout_shows_the_decision_and_its_signature` |

**Ambiguous / open (client to decide):** whether approving a template item covers its variants (today: no, each variant must be listed); who exactly receives the alert (today: the three roles); whether Purchase Invoices with Update Stock are used anywhere in the client's process (today: refused).

**Not covered by this feature:** vendor rating; supplier audits as records (module 4 title mentions rating; no requirement yet).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, see `scripts/create_pilot_site.sh`) or a test site after `prepare_test_site`. Test data below uses the demo supplier **Silicone Compounds (Demo)** and demo item **RM-SILICONE**; create a throw-away supplier **TEST-SUP-UNAPPROVED** for the refusal cases. Never use real supplier data.

Each test lists: role, preconditions, steps, expected result. Record the actual result and a screenshot (form with its banner/message, and the Electronic signatures table where relevant).

### SA-01 QA approves a supplier with a signature (C1–C6, C15)
Role: QA Manager. Preconditions: supplier *Silicone Compounds (Demo)* exists; a PDF to attach.
1. Open **Buying › Supplier › Silicone Compounds (Demo)**. Expected: red banner *Not yet approved by QA…* (if no decision yet) and button **Decide Supplier Approval**.
2. Click **Decide Supplier Approval**. Expected: a new **Supplier Approval** form with Supplier filled.
3. Enter Quality System Certificate No `ISO13485-DEMO-2026`, Certificate Expiry = today + 365 days, attach the PDF in **Certificate**; in **Items** add `RM-SILICONE`. Click **Save**. Expected: saved as draft `SA-…`; buttons **Approve**, **Approve Conditionally**, **Not Approve** appear.
4. Click **Approve**. Expected: dialog **Sign: Approve** with the text *I approve this.*, a **Reason** box and **Your password**.
5. Leave Reason empty, enter the password, click **Sign**. Expected: refused, record still draft (reason required).
6. Enter Reason `Audited on site; certificate checked`, a **wrong** password, click **Sign**. Expected: refused (wrong password); record still draft, Decision empty.
7. Enter the correct password, click **Sign**. Expected: the form reloads submitted; green banner *Approved: Audited on site; certificate checked*; Decision = Approved, Decided By = you, Decided At = now; **Electronic signatures** table shows meaning *Approved*, your name, the time, the reason.
8. Return to the supplier's form. Expected: green banner *Approved by QA (SA-…) for: RM-SILICONE*.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 7 and 8.

### SA-02 Required fields and refusals of the decision (C5, C6, C8)
Role: QA Manager.
1. New Supplier Approval for *Silicone Compounds (Demo)* with **no items** and no certificate; Save; click **Approve**, sign with reason and password. Expected: refused *List the items the supplier is approved for.* (or the certificate message), record stays draft.
2. Add item RM-SILICONE twice; sign Approve. Expected: refused *Each item is listed once…*.
3. Set Certificate Expiry = yesterday, one item, certificate number and file; sign Approve. Expected: refused *…certificate expired on <date>…*.
4. Set expiry = today + 365; set **Conditional Until** = today; sign **Approve Conditionally**. Expected: refused *A conditional approval holds until a day still to come.*
5. Set Conditional Until = today + 60; sign **Approve**. Expected: refused *Only a conditional approval has an end date; clear Conditional Until.*
6. Sign **Approve Conditionally** with reason `New supplier on trial`. Expected: submitted; orange banner *Conditional until <date>: New supplier on trial*.
Actual result: ____  Pass/Fail: ____  Cleanup: this decision now replaces SA-01's; redo SA-01 step 3–7 afterwards if later tests need an unconditional approval.

### SA-03 Only QA decides; nobody edits a decision (C2, C14)
1. As **Purchase**: open **Buying › Supplier Approval › New**. Expected: not permitted (no create permission), or the save is refused *Only QA decides a supplier.* if the form opens.
2. As **Purchase**: open an existing SA-… Expected: read-only; no Approve buttons.
3. As **QA Manager**: open a submitted SA-…, try **Cancel** (menu). Expected: refused *A decision on a supplier is never cancelled; a new decision replaces it.*
4. As **System Administrator**: open a draft SA-… and try to set Decision via the form or the API (`frappe.client.set_value`). Expected: refused *… is decided by signing it…*
Actual result: ____  Pass/Fail: ____

### SA-04 Purchase order refused for an unapproved supplier or item (C7)
Role: Purchase. Preconditions: TEST-SUP-UNAPPROVED has no decision; Silicone Compounds (Demo) approved for RM-SILICONE only (SA-01).
1. **Buying › Purchase Order › New**; Supplier `TEST-SUP-UNAPPROVED`; Required By = today + 7; item RM-SILICONE qty 10; Save. Expected: refused, title *Supplier not approved*: *Supplier TEST-SUP-UNAPPROVED is not approved by QA; nothing can be bought from it.*
2. Change Supplier to *Silicone Compounds (Demo)*; add a second line with an item it is not approved for (e.g. `RM-CONNECTOR`); Save. Expected: refused *… is not approved for RM-CONNECTOR; QA approves it for them before they are bought.*
3. Remove that line; Save. Expected: saved; field **Supplier Approval** shows SA-… of SA-01; **Why This Conditionally Approved Supplier** is empty.
4. **Send for Approval** (Actions); as **Management**, **Approve**. Expected: order submitted, Awaiting Approval → Approved.
Actual result: ____  Pass/Fail: ____  Cleanup: cancel the order with a reason, or leave it for SA-06.

### SA-05 Conditional supplier: reason on the order, 100% inspection (C9, C10)
Preconditions: SA-02 step 6 made the supplier conditional.
1. As Purchase, new Purchase Order for the supplier, RM-SILICONE qty 5; Save. Expected: refused, title *Reason needed*: *…approved only conditionally, until <date>; say why this order is placed with it.*
2. Fill **Why This Conditionally Approved Supplier** = `Only source of this grade this month`; Save; Send for Approval; Management approves. Expected: submitted.
3. As Stores: on the order, **Create › Purchase Receipt**; on the line fill Supplier Lot No and attach a certificate; Save, Submit. Expected: line shows **Inspect Each Lot in Full** ticked (open the line's row form).
4. As QC Inspector: **Verify Certificates** on the receipt; then **Quality › Quality Inspection › New**, type Incoming, reference the receipt, item RM-SILICONE, the new batch; **Sample Size** = 1; fill readings (with instrument); Save. Expected: refused, title *Inspect the whole lot*: *…inspected in full: the sample size is the lot's 5.*
5. Set Sample Size = 5; Save, Submit. Expected: accepted.
Actual result: ____  Pass/Fail: ____

### SA-06 A supplier stopped after orders were placed (C11)
Preconditions: an approved, submitted purchase order exists (SA-04).
1. As QA Manager: new Supplier Approval for the supplier, no items, sign **Not Approve**, reason `Failed the re-audit`. Expected: submitted; supplier banner turns red.
2. As Stores: from the old order, **Create › Purchase Receipt**; Save. Expected: refused *Supplier … is not approved by QA; nothing can be bought from it.*
3. As Purchase: open the old submitted order and change **Order Confirmation No** (update after submit; give the reason asked for). Expected: allowed (buys nothing new).
4. As Purchase: **Duplicate** the old order; Save. Expected: refused (new order checked afresh).
Actual result: ____  Pass/Fail: ____  Cleanup: QA re-approves the supplier for RM-SILICONE (SA-01 steps 3–7).

### SA-07 Goods only on a goods receipt; no stock into an existing lot (C11)
1. As Accounts/Purchase: **Accounts › Purchase Invoice › New**, supplier approved, item RM-SILICONE, tick **Update Stock**; Save. Expected: refused, title *Receive on a goods receipt*.
2. Untick Update Stock; Save. Expected: saved.
3. As Stores: new Purchase Receipt for the approved supplier, item RM-SILICONE, and in the line's **Batch No** pick a lot that already holds stock; Save. Expected: refused, title *New lot needed*.
Actual result: ____  Pass/Fail: ____

### SA-08 Certificate expiry alert (C12)
Preconditions: a decision in force whose Certificate Expiry is within 30 days (QA makes one with expiry = today + 10).
1. As System Administrator run the daily job once: `bench --site <site> execute meddevice_suite.master_data.supplier_approval.send_certificate_alerts` (or wait for the nightly scheduler). Expected: QA Manager, Purchase and Management each see one bell notification *Certificate of supplier … expires on <date>*; the decision's **Certificate Expiry Alerted** is ticked.
2. Run the job again. Expected: no second notification.
3. QA signs a new decision with a renewed certificate (expiry + 365). Run the job. Expected: no notification for the old decision; none for the new one (not within 30 days).
Actual result: ____  Pass/Fail: ____

### SA-09 Dates: last valid day (C13)
Preconditions: a test site where the Administrator may set dates directly, or wait for the calendar.
1. Decision with Conditional Until = today (set by the Administrator after signing, test sites only). Purchase saves an order with a reason. Expected: allowed.
2. Set Conditional Until = yesterday. Save a new order. Expected: refused *…ended on <date>…*.
3. Same with Certificate Expiry = yesterday on an Approved decision. Expected: refused *…certificate … expired on <date>…*.
Actual result: ____  Pass/Fail: ____  Cleanup: restore the dates.

### SA-10 Lists, search and audit trail
1. **Buying › Supplier Approval** list: filter by Decision = Approved, by Supplier; sort by Decided At. Expected: filters and sorting work; the list shows Supplier, Decision, Certificate Expiry.
2. Open a submitted decision › menu **Audit Trail**. Expected: events *Created* and *Submitted* with your user and time; **Verify Integrity** reports *The audit trail and the signatures of … are intact.*
Actual result: ____  Pass/Fail: ____

### Regression
- Receiving goods, incoming inspection, QA lot release and the full production journey still work for an approved supplier: run the automated journey (section 8) or walk steps 1–18 of `cypress/integration/standard_flow.js` by hand.

## 5. End-to-end business workflow

1. **QA** evaluates the supplier (audit, certificate) → signs **Supplier Approval** SA-… (Approved for RM-SILICONE, certificate valid to 2027-10). Data: SA record submitted, Electronic Signature, Audit Events; supplier banner green.
2. **Purchase** raises a **Purchase Order** for RM-SILICONE → accepted, `mds_supplier_approval` = SA-…; sends for approval; **Management** approves (workflow, module 48). Data: PO submitted, workflow state Approved.
3. **Stores** receives the goods on a **Purchase Receipt** (new lot into the Quarantine store, supplier lot number and certificate on the line; `mds_full_inspection` 0 for an unconditional supplier). Data: Batch, Stock Ledger Entries, lot status Quarantine (module 17).
4. **QC** verifies the certificate (**Verify Certificates**), then records the **Quality Inspection** against the item's incoming specification (module 6) with sample size per the specification.
5. **QA** releases the lot (**Lot Release**, signed) → Approved, moved to the approved store (module 16).
6. Daily, the **Supplier Certificate Expiring** alert warns QA and Purchase 30 days before the certificate's expiry; QA signs a new decision on the renewed certificate.

Cross-module data changes: Supplier Approval (new), Purchase Order (`mds_supplier_approval`, `mds_conditional_reason`), Purchase Receipt Item (`mds_full_inspection`), Quality Inspection (sample size rule), Notification Log (alert), Electronic Signature, Audit Event.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocTypes | `Supplier Approval` (submittable, `SA-{YYYY}-{#####}`), child `Supplier Approval Item` |
| Custom fields | Purchase Order: `mds_supplier_approval` (Link), `mds_conditional_reason` (Small Text); Purchase Receipt Item: `mds_full_inspection` (Check). Applied by `apply_supplier_approval_fields()` from `setup/install.py` |
| Server module | `meddevice_suite/master_data/supplier_approval.py`: `approve`, `approve_conditionally`, `not_approve`, `decide`, `current_approval`, `approval_in_force`, `guard_purchase_order`, `guard_order_update`, `mark_full_inspection`, `ordered_lines`, `refuse_stock_on_invoice`, `check_full_inspection`, `supplier_onload`, `send_certificate_alerts` |
| Controller | `master_data/doctype/supplier_approval/supplier_approval.py` (only QA; decision fields only by signature; never cancel/delete); index on `(supplier, docstatus, decided_at)` |
| Hooks (`hooks.py`) | Purchase Order `validate`, `before_update_after_submit`; Purchase Receipt `validate` (`mark_full_inspection`, with `lot_control.receipt.refuse_existing_lots`); Purchase Invoice `validate`; Supplier `onload`; Quality Inspection `validate` (`guard_incoming_inspection` → `check_full_inspection`); scheduler `daily` → `send_certificate_alerts` |
| Signing | `compliance/signing.py` `SIGNING_ACTIONS`: ("Supplier Approval", "Approve" / "Approve Conditionally" / "Not Approve"); desk dialog `public/js/esign.js` |
| API routes | `POST /api/method/meddevice_suite.compliance.signing.sign_action` (`doctype`, `name`, `action`, `password`, `reason`); standard `/api/resource/Supplier Approval` (create/read under the DocType permissions; submit refused) |
| Client scripts | `public/js/master_data.js` (supplier banner, button), `master_data/doctype/supplier_approval/supplier_approval.js` (act buttons, banner) |
| Alert | Notification `Supplier Certificate Expiring` (`master_data/notification/supplier_certificate_expiring/`), registered in `compliance/alerts.py` `ALERTS`; recipients by role |
| Compliance registries | `compliance/esign.py` `SYSTEM_MAINTAINED["Supplier Approval"] = ("docstatus", "certificate_alerted")`; `compliance/evidence_files.py` (certificate of a submitted decision); `compliance/retention.py` (10 years); sidebars `setup/sidebars.py` (Buying, Quality) |
| Data flow | Form → `sign_action` → `decide()` (checks, `esign.sign`, submit) → DB; Purchase Order save → `guard_purchase_order` → `current_approval` (1 query + 1 for items) → refuse or set fields; receipt save → `ordered_lines` (1 join) → `approval_in_force` → marks |

## 7. Defects, edge cases and risks

**Confirmed and fixed during review (all have tests):** receipt without an order skipped the check; expired certificate still allowed orders; inspection on another document released the lot; a receipt line naming another supplier's order line, or an order but no order line, skipped the check; Update Items added an unapproved item; a stopped supplier's order could not take harmless edits; a received lot could be released when its batch was made beforehand (origin now read from the stock ledger); stock could be added to an approved lot (now refused; counts allowed).

**Open / potential (not defects of this feature, listed for the record):**
- Item variants: approving a template item does not cover its variants; client to decide (section 3).
- Email delivery of the alert depends on mail configuration (module 51); desk bell only until then.
- Administrator is held to the same rules (verified by test); the rules do not apply to raw SQL or `frappe.db.set_value` by a developer — covered by the audit trail's integrity check, not by this feature.
- Alert timing when the scheduler is off for more than 30 days: the alert is sent on the first run after (catch-up), possibly after expiry — by design.

## 8. Test results and verification summary

Executed in this development cycle (dates 2026-10-09/10, local test site `test.localhost`, macOS; CI = GitHub Actions on a fresh site):

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site test.localhost run-tests --module meddevice_suite.master_data.test_supplier_approval` | 47 tests OK (2026-10-10) |
| Full server suite, PR #5 branch alone | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 587 integration OK (2026-10-10, before the stock-count fix); re-run with the count fix was in progress when this document was written — see the PR's CI |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed (2026-10-10 11:44 wall time, complete code before the count fix); re-run in progress |
| CI (fresh site) | run 37971685492 on commit 314f3e6: UI tests success, server tests failure (opening-stock test fixed in 26ceb59); run on 26ceb59: server success (UI cancelled by the next push); run on 17fc4cc: in progress | partial — final run pending |
| Semgrep (Frappe rules + python correctness) | `semgrep --config <rules> --config r/python.lang.correctness --error …/master_data …/lot_control` | 0 findings |
| Lint/format | `pre-commit run --files …` | pass |
| Traceability | `python -m meddevice_suite.qa.traceability --check` | pass |
| Coverage | last measured 99 % overall, `supplier_approval.py` 100 % (2026-10-09, before later changes); not re-measured locally since — CI measures it |
| Independent code review | six read-only review rounds against the code and ERPNext source; final verification of the last fix pending | all Medium+ findings fixed; see section 7 |
| Manual browser tests SA-01…SA-10 | — | **not executed** (no manual tester session); the automated journey covers SA-01 steps 2–7 and SA-04 step 3 (`standard_flow.js`, passed) |

Untested: SA-08 on a real nightly scheduler (only the job called directly); email delivery; the client's real suppliers and certificates (pilot uses demo data).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| QA signs Approve / Approve Conditionally / Not Approve with reason; wrong password signs nothing | Verified (automated + journey) |
| Purchase order refused for unapproved supplier / item / ended condition / expired certificate, on all order paths | Verified (automated) |
| Conditional: reason on each order; lots inspected in full | Verified (automated) |
| Goods only on a goods receipt; receipt checked at receipt; new lot per delivery | Verified (automated) |
| 30-day alert once, with catch-up | Verified (automated, job called directly) |
| Decision never cancelled/deleted; certificate protected | Verified (automated) |
| Browser: supplier banner, Decide button, act buttons, signing dialog | Verified for the journey path (automated); full manual SA-01…SA-10 **not tested** |
| CI green on a fresh site for the final commit | **Pending** |
| Client decisions: variants, alert recipients, Purchase Invoice usage | **Open** |
