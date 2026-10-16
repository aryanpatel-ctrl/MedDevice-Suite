# Customer licences (module 5) — feature document and browser test guide

Requirement: **URS-MST-008** (`meddevice_suite/qa/requirements.toml`); the alert lead time is a Quality Setting (URS-PLT-012).
Code: `meddevice_suite/master_data/customer_licence.py`, DocTypes `Customer Licence` and `Customer Licence Type` (`master_data/doctype/`), alert `master_data/notification/customer_licence_expiring`, hooks in `meddevice_suite/hooks.py` (Sales Order and Delivery Note `before_validate`, Customer `onload` and `validate`, the daily job), signed acts in `compliance/signing.py`, desk in `public/js/master_data.js` and `customer_licence.js`, loaders `pilot/demo.py`, `qa/ui_journey.py`.
DFD trace: D1 Customer Master; the sales-order intake gate "only approved customers"; master plan tracker step 9.

## 1. Feature overview

**Purpose.** A medical device is sold only to a buyer allowed to buy it. Sales records each licence a customer holds; QA verifies it with an electronic signature. A sales order or delivery for a customer with no verified licence valid on its date is refused.

**Why it matters.** CDSCO MDR 2017 lets a manufacturer sell to licensed parties; ISO 13485 7.2 and 7.5.8 ask that customer requirements and the distribution record be controlled. A sale to an unlicensed buyer is a regulatory finding and breaks traceability in a recall.

| Job profile | Here |
| --- | --- |
| Sales | Records licences (type, number, authority, dates, copy); sees on the customer whether it may be sold to |
| QA Manager | Verifies or rejects a licence, withdraws a verified one, decides that a customer buys without one, keeps the licence types; all signed |
| Stores, Management, System Administrator | Read |

## 2. Detailed functional explanation

- **Licence types** are a list QA keeps (`Customer Licence Type`), for example a registration for the sale of medical devices. Nothing about the authorities' forms is in code.
- **Recording.** Sales opens the customer and clicks **Record Licence**: licence type, number, issuing authority, valid from, valid until and a copy are all required. The status fields are left empty; only QA's signature sets them.
- **Verification.** QA opens the licence and clicks **Verify** (meaning *Verified*) or **Reject** (meaning *Rejected*), with a reason and the password. A licence that has already expired cannot be verified. The signed act is the licence's only submit; Frappe's Submit is refused for everyone, through the API and with validation skipped.
- **Withdrawal.** When the authority suspends or cancels a licence, QA clicks **Withdraw** on the verified licence (signed). From then on it permits nothing.
- **Selling.** A sales order, a delivery note, and a sales or POS invoice that updates stock are refused when submitted, unless the customer holds a verified licence valid on the document's date and on the day it is submitted. A backdated record does not reach past an expired licence. A draft may be saved. A return, a credit note and an invoice that only bills are not checked. Lines added or raised on a submitted order (ERPNext's **Update Items**) need the licence that day.
- **Renaming and merging.** A customer holding a licence or an exemption QA has decided is not renamed, so a verified licence never passes to another party unsigned. No customer is merged into another at all, decided or not (URS-MST-017, the rule of every controlled master in `versioning.refuse_merge`); a duplicate is disabled instead.
- **Buying without a licence.** QA may decide, with a signature and a reason, that a customer buys without a licence, such as a hospital buying for its own use (**Exempt From Licence** on the customer), and reverse it (**Require Licence**). The signature binds that decision only, not the addresses Sales keeps up to date.
- **Expiry alert.** Each day, Sales and QA are alerted once about a verified licence expiring within the days set in Quality Settings (30 until QA changes it), unless a renewed licence that continues it without a gap is verified already. A failed alert is logged and retried the next day.
- **Never destroyed.** A licence is never cancelled or deleted; a renewal is a new licence record. Licences, their types and copies are kept ten years with the distribution records; a decided licence's copy cannot be changed or deleted.

## 3. Requirements and acceptance criteria

> **URS-MST-008** A customer is sold to only under a licence QA has verified: Sales records each licence of a customer with its type, number, issuing authority, validity dates and a copy; QA verifies or rejects it with an electronic signature and a reason, and withdraws a verified licence with a signature when the authority suspends or cancels it; a sales order or delivery note is refused when submitted for a customer that holds no verified licence valid on the document's date, unless QA has decided with an electronic signature and a reason that the customer buys without one (such as a hospital buying for its own use), a decision QA can reverse the same way; Sales and QA are alerted once before a verified licence expires, unless a renewed licence is already verified.

| # | Criterion | Verification (`master_data/test_customer_licence.py`) |
| --- | --- | --- |
| C1 | Sales records; required fields; dates in order; only Sales records; status only by signature | `TestRecording.*` |
| C2 | QA verifies with signature; only QA; wrong password / no reason; expired refused; decided once | `TestVerification.*` |
| C3 | Signed act is the only submit (QA, Administrator, validation skipped) | `test_the_signed_act_is_the_only_submit` |
| C4 | Rejected licence permits no sale; never cancelled or deleted; changed only by signature | `TestVerification.*` |
| C5 | Withdrawal by QA with signature stops sales; only a verified one; reason | `TestWithdrawal.*` |
| C6 | Sales order, delivery note and stock-updating invoice refused without a verified licence valid on the document's date and on the day; backdating refused; draft allowed; return, credit note and billing-only invoice not checked; skipped validation and inserted-as-submitted checked; Update Items checked | `TestSalesNeedALicence.*` |
| C6a | A customer with a decided licence or exemption is not renamed or merged | `TestRenamingAndMerging.*` |
| C7 | Exemption by QA's signature, reversible; only QA; signature binds the decision | `TestBuyingWithoutALicence.*` |
| C8 | Alert once; follows Quality Settings; not for a renewal without a gap, rejected or withdrawn; a renewal after a gap still alerts; failure retried | `TestExpiryAlert.*` |
| C9 | Customer form says whether it may be sold to | `TestCustomerForm.*`, `cypress/integration/customer_licence.js` |
| C10 | On screen: Sales records, QA verifies with a signature, the customer shows the licence | `cypress/integration/customer_licence.js` |
| C11 | Pilot and journey: the distributor and the journey's customer licensed by the signed act; the pilot hospital exempted by QA's signature | `pilot/test_demo.py`, `pilot/test_standard_flow.py`, `qa/test_ui_journey.py` |

## 4. Step-by-step browser testing guide

### CL-01 A customer without a licence
Role: Sales. **Selling › Customer**, open *Regional Medical Distributor (Demo)* or a new customer. Expected on a new one: red banner *No licence verified by QA is valid today…*; button **Record Licence**.

### CL-02 Sales records a licence
Role: Sales. **Record Licence**: type, number, issuing authority, valid from and until, attach the copy. Save. Expected: orange banner *Awaiting QA's verification*; no Submit.
Try: valid until before valid from → refused.

### CL-03 QA verifies
Role: QA Manager. Open the licence. Expected: **Verify** and **Reject**, no Submit. **Verify** with an empty reason → refused; wrong password → refused, still awaiting. Correct password and a reason → *Verified* (green banner); the signature table shows *Verified*.

### CL-04 Sales order and delivery
Role: Sales. A sales order for a customer without a verified licence: saving works, **Submit** is refused with *Customer … holds no licence verified by QA that is valid on …*. After CL-03 the order submits. Stores: the delivery note follows the same rule.

### CL-05 Withdrawal
Role: QA Manager. On a verified licence, **Withdraw** with a reason and the password → *Withdrawn*. A new sales order for that customer is refused again.

### CL-06 Buying without a licence
Role: QA Manager. On *City Hospital (Demo)*: blue banner *Buys without a licence, as QA decided with a signature*; **Require Licence** reverses it (signed). On another customer, **Exempt From Licence** with a reason. Sales cannot change it.

### CL-07 Expiry alert
Role: QA Manager or Sales. Change *Customer Licence Alert* in Quality Settings under an approved change request (QS steps of the master data guide), or wait for a licence within 30 days: the bell shows *Licence of customer … expires on …* once.

## 5. End-to-end business workflow
Customer set up by Sales → Sales records its licence → QA verifies with a signature (or decides the customer buys without one) → quotations, sales orders and deliveries proceed → 30 days before expiry Sales and QA are alerted → Sales records the renewed licence, QA verifies it → if the authority suspends a licence, QA withdraws it and sales stop.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `master_data/customer_licence.py`: `verify`, `reject`, `withdraw`, `exempt_from_licence`, `require_licence_again`, `licences_valid_on`, `buys_without_licence`, `guard_sales_order`, `guard_delivery_note`, `guard_customer`, `customer_onload`, `customer_signed_content`, `send_expiry_alerts` |
| DocTypes | `Customer Licence` (submittable; nobody holds submit; status fields set only by the acts; index on customer, docstatus, status, valid_until); `Customer Licence Type` (QA keeps) |
| Custom field | `Customer.mds_buys_without_licence` (read-only; set only by QA's signed act) |
| Hooks | Sales Order, Delivery Note, Sales Invoice and POS Invoice `before_validate` (only on submit; runs even when validation is skipped); Sales Order `before_update_after_submit`; Customer `onload`, `validate`, `before_rename`; daily `send_expiry_alerts` |
| Signing | `SIGNING_ACTIONS` for Customer Licence (Verify, Reject, Withdraw) and Customer (Exempt From Licence, Require Licence); `esign.SIGNED_CONTENT["Customer"]`; `SYSTEM_MAINTAINED["Customer Licence"]` |
| Settings | Quality Settings `customer_licence_warning_days` (30; 1 to 365) |
| Records | retention with the distribution records; the licence copy is evidence once decided |
| Test fixture | `setup/test_stock.py` `licensed_for_test` for other modules' tests |

## 7. Defects, edge cases and risks
- Independent review findings, all fixed with tests before release: a sales invoice with Update Stock delivered without a licence check (High); the check used only the document's date, so a backdated order or delivery passed an expired licence (High); merging customers moved a verified licence to another party (Medium); the delivery tests called the check directly instead of submitting (Medium); a renewal after a gap silenced the expiry alert (Medium); Update Items on a submitted order was not checked (Low); the exemption's signature bound the customer's name, which a rename changes (Low); an alert for a licence already expired said "expires" (Low).
- Open, outside this module: an invoice that updates stock also passes the delivery-note rules of the lot module (FEFO, the delivery's own lot checks); raised for its own fix. QA may sign a licence Sales changed after QA opened it, as on every signed form (the dialog does not compare the version opened); raised for the e-signature module.
- First design put the exemption on the customer group. QA cannot edit customer groups (ERPNext gives that to Sales Master Manager), and changing ERPNext's group permissions was a larger change than the rule needs; an exemption per customer, signed, is also the narrower control. Changed before release.
- A licence whose authority limits it to certain device classes or premises is recorded as one licence; the class check is not automated. Open question for the client.
- The licence types are not seeded: QA adds them on a new site before the first licence (the names of the authorities' forms are the client's to confirm).

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Recording, verification, rejection, withdrawal, signatures | Verified (automated) |
| Sales order and delivery gate on the document's date | Verified (automated) |
| Signed exemption, reversible | Verified (automated) |
| Expiry alert, Quality Settings lead time | Verified (automated) |
| On screen: record, verify, customer banner | Verified (browser test) |
| Manual CL-01…CL-07 | **Not tested** |
