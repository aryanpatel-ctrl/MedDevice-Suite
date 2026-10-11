# Supplier rating (module 4) — feature document and browser test guide

Requirement: **URS-PUR-002** (`meddevice_suite/qa/requirements.toml`). Builds on URS-PUR-001 (approved suppliers), URS-QC-003 (QA's lot decisions) and URS-PLT-012 (Quality Settings under change control).
Code: `meddevice_suite/master_data/supplier_rating.py`, the record `master_data/doctype/supplier_rating/` (with *Supplier Rating Lot* and *Supplier Rating Line*), the alert `master_data/notification/supplier_rating_below_threshold/`, the values in `compliance/quality_settings.py`, the supplier form in `public/js/master_data.js`, hooks in `meddevice_suite/hooks.py` (daily job; Supplier Scorecard `before_validate`), the call from `supplier_approval.decide`.
DFD trace: workflow diagram D2 "Purchase Management (PO, GRN, Vendor Rating)"; DFD 2.6 Vendor Management, D2 Vendor Master; master plan section 3 (vendor master: rating).

## 1. Feature overview

**Purpose.** Each supplier's performance is measured every month from what actually happened (the lots QA released or rejected, and whether the orders arrived in full on time), and a poor month makes QA re-evaluate the supplier.

**Why it matters.** ISO 13485 7.4.1 asks that suppliers are monitored against criteria and that the results feed their re-evaluation, with records kept. A rating typed by hand, or one that can be cancelled, is not evidence.

| Job profile | Here |
| --- | --- |
| QA Manager | Reads ratings; re-evaluates a supplier due for it with a signed Supplier Approval decision; sets weight and threshold in Quality Settings under change control |
| Purchase, Purchase Manager | Read ratings; alerted with QA |
| Management, Stores manager, Production manager, QC | Read |
| Nobody | Makes, changes, cancels or deletes a rating |

## 2. Detailed functional explanation

- **When:** the daily job rates every supplier for each finished month (looking back a year, so a month missed while the job did not run is rated later) that holds a QA decision on one of its lots or one of its order lines due. A month is rated once; a month with nothing of the supplier's is not rated.
- **Quality:** of the supplier's received lots QA decided in the month (Lot Release, signed), the share **released** rather than **rejected**. A **hold** is not a decision: the lot counts in the month it is finally released or rejected. Lots of other suppliers, and finished lots, are not counted.
- **Delivery:** of the supplier's order lines (submitted purchase orders) **required by** a day in the month, the share **received in full by that day** on goods receipts. Partial and late lines are not on time; a return to the supplier does not undo a delivery (a rejected lot did arrive).
- **Score:** quality × weight + delivery × (100 − weight), weight 70 until QA changes it; a month with only lots or only lines is scored on that one.
- **Standing:** *Satisfactory* at or above the threshold (80 until QA changes it); below it, *Re-evaluation Due*, and QA and Purchase are alerted once (*Supplier Rating Below Threshold*).
- **Re-evaluation:** QA's next signed decision on the supplier (Supplier Approval: approve, approve conditionally or not approve) marks every rating then due *Re-evaluated*, naming the decision. The rating does not stop orders itself; QA's decision does, through the approved-supplier rules.
- **The record** lists every lot and order line it counted (with the QA decision, and the quantities by the required date), the weight, threshold and Quality Settings version it used. It is made submitted by the system only, and never changed, cancelled or deleted (the *Re-evaluated* mark is set by the system when QA decides).
- **On the supplier's form:** below QA's decision, the latest month's score, or the ratings still due for re-evaluation in red.
- **ERPNext's Supplier Scorecard is not used** and is refused on save: its quality figures read receipt rejections this process never records (goods go to quarantine; QA rejects them by a signed decision), its on-time count treats a late receipt as on time, its periods can be cancelled without trace and its standings block orders outside QA's decision (decided 2026-10-11). It is removed from the desk.

## 3. Requirements and acceptance criteria

> **URS-PUR-002** Each supplier is rated by the system for every finished calendar month in which QA decided any of its received lots or any of its order lines fell due: quality is the share of those lots QA released rather than rejected, a hold not counting until the lot is decided; delivery is the share of those lines received in full by their required date on goods receipts; the score weighs them as QA sets in Quality Settings (quality 70 percent until changed), or is the one measure the month holds; each rating lists the lots and lines it counted and the settings it used, and is never changed, cancelled or deleted; it is shown on the supplier; a score below the threshold QA sets (80 until changed) makes the supplier due for re-evaluation and alerts QA and Purchase, and only QA's next signed decision on the supplier closes it; ERPNext's supplier scorecard is not used.

| # | Criterion | Verification (`master_data/test_supplier_rating.py`) |
| --- | --- | --- |
| C1 | Quality from QA's decisions; holds wait; only this supplier's lots | `TestQuality.*` |
| C2 | Delivery in full by the required date; returns do not undo a delivery | `TestDelivery.*` |
| C3 | Weighting as QA set; one-measure months | `test_the_score_weighs_quality_and_delivery_as_qa_set`, `test_quality_is_the_share_of_its_lots_qa_released`, `test_delivery_is_the_share_of_lines_received_in_full_by_their_required_date` |
| C4 | Threshold, alert to QA and Purchase only | `test_a_score_at_the_threshold_is_satisfactory`, `test_below_the_threshold_qa_and_purchase_are_alerted_to_re_evaluate` |
| C5 | Closed only by QA's next signed decision on this supplier | `test_qas_next_signed_decision_on_the_supplier_closes_the_re_evaluation` |
| C6 | Rated once, finished months only, missed months caught up | `TestTheRecord.test_a_month_is_rated_once_and_only_once_it_is_over`, `test_the_daily_job_rates_finished_months_it_has_not_rated` |
| C7 | Never made, changed, cancelled or deleted by a person; read permissions | `test_nobody_makes_changes_cancels_or_deletes_a_rating`, `test_qa_and_purchase_read_ratings_and_nobody_creates_one` |
| C8 | Shown on the supplier | `test_the_supplier_form_shows_its_ratings` |
| C9 | Scorecard refused | `TestNoScorecard.*` |
| C10 | Weight and threshold bounds, change control | `compliance/test_quality_settings.py` (all settings) |

## 4. Step-by-step browser testing guide

### SR-01 A rating is made
On the pilot, with lots decided and order lines due last month: run the daily job (or wait a day). Role: QA Manager. **Buying › Supplier Rating**. Expected: one rating per supplier for last month, with its lots and order lines listed; the figures match the lines.

### SR-02 Below the threshold
A supplier with a rejected lot and nothing released in the month. Expected: standing *Re-evaluation Due*; QA and Purchase have the alert; the supplier's form shows the rating in red.

### SR-03 QA re-evaluates
Role: QA Manager. From the supplier's form, **Decide Supplier Approval**, sign. Expected: the rating shows *Re-evaluated* and names the decision.

### SR-04 Nobody edits
Open a rating: no Save, Cancel or Delete; **Supplier Scorecard** is not on the desk, and making one is refused.

## 5. End-to-end business workflow
Order lines fall due and lots are received → QA releases or rejects each lot (module 16) → the month ends → the system rates the supplier → below the threshold, QA and Purchase are alerted → QA re-evaluates with a signed decision (approve, conditional or not approved) → that decision governs the next orders.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `supplier_rating.lots_decided`, `lines_due`, `received_by_required_date`, `score_of`, `rate`, `rate_finished_months` (daily), `close_re_evaluations`, `recent`, `refuse_scorecard` |
| Record | *Supplier Rating* (submittable, made submitted, `rating_key` unique per supplier and month), *Supplier Rating Lot*, *Supplier Rating Line* |
| Values | Quality Settings `supplier_rating_quality_weight` (70, 0–100), `supplier_rating_threshold` (80, 50–100) |
| Retention | Purchasing and supplier records, 10 years |

## 7. Defects, edge cases and risks
- Delivery compares stock quantities (ordered against received by the required date), so a line ordered in another unit is compared correctly; a line closed short is not on time.
- An order line's required date moved after the month was rated does not change the rating: a rating is final when made.
- A supplier with no activity in a month has no rating for it; the form shows the latest month that has one.
- The rating informs QA's decision and does not block orders itself (decided 2026-10-11); blocking stays with QA's signed approval.

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Monthly rating from QA's decisions and receipts | Verified (automated) |
| Threshold, alert and re-evaluation by QA's signed decision | Verified (automated) |
| Record never changed by a person | Verified (automated) |
| Scorecard refused | Verified (automated) |
| Manual SR-01…SR-04 | **Not tested** |
