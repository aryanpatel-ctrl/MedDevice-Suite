# Sampling plans (module 6) — feature document and browser test guide

Requirement: **URS-MST-009** (`meddevice_suite/qa/requirements.toml`). Builds on URS-MST-002 (controlled specifications) and URS-QC-002 (incoming inspection).
Code: `meddevice_suite/master_data/sampling.py`, the tables `master_data/iso2859_1.json`, the specification fields in `master_data/specification.py`, hooks on Quality Inspection in `meddevice_suite/hooks.py`.
DFD trace: D2 IQC, IPQC and final QC sampling; master plan decision "sample sizes follow ISO 2859-1 single sampling at AQL unless the specification says 100 percent"; tracker step 11.

## 1. Feature overview

**Purpose.** How many units an inspection examines, and how many may fail before the lot is rejected, follows the standard instead of a guess. Each specification says how its stage is sampled; for ISO 2859-1 the system works out the plan from the size of the lot.

**Why it matters.** ISO 13485 7.4.3 and 8.2.6 ask that acceptance activities follow documented, statistically valid sampling plans. A sample too small for the lot lets defects through; a plan typed by hand cannot be audited.

| Job profile | Here |
| --- | --- |
| QA Manager | Chooses the sampling of each specification: a fixed sample size, every unit, or ISO 2859-1 with an inspection level and an AQL; under change control once in use |
| QC Inspector | Inspects the sample the plan gives and records the nonconforming units found |

## 2. Detailed functional explanation

- **Three ways to sample**, on each specification (**Sampling**):
  - *Fixed sample size*: as before. The inspection samples at least that many and its readings decide it. Existing specifications stay on this.
  - *Every unit*: the whole lot is inspected; one nonconforming unit rejects it.
  - *ISO 2859-1*: single sampling, normal inspection, at an **Inspection Level** (S-1 to S-4, I, II, III; II is the usual one) and an **AQL**.
- **The lot is counted, not typed.** For a lot a supplier delivered, the quantity received; for an in-process inspection, the quantity of its job card, which must make the item inspected; otherwise the lot's quantity in stock in every store, reserved or not. It is worked out again on every save and at submit, from the system's records, whatever the record was sent with. A lot of one unit is that unit; a lot that is not a whole number of units is not sampled by ISO 2859-1. The inspection shows **Lot Size**, **Sample Size Code Letter**, **Accept Number**, **Reject Number** and **Every Unit Inspected**, all set by the system.
- **The plan.** Table 1 gives the code letter from the lot size and level; Table 2-A gives the sample size and the accept and reject numbers at the AQL. An arrow in Table 2-A leads to the first plan below or above it, and that plan's code letter gives the sample size. When the sample would reach the lot, every unit is inspected.
- **The decision.** The inspector records **Nonconforming Units Found**. Up to the accept number the lot is accepted; at the reject number it is rejected. A reading out of its limits counts, judged as ERPNext judges it: the count can never be lower than the readings show. An inspector who rejects the lot by hand (manual inspection) is never overruled. A lot accepted with nonconforming units found is submitted only with remarks saying how those units were set apart.
- **A conditionally approved supplier's lot** (module 4) is inspected in full: every unit, accepting none nonconforming.
- **Sample at least the plan.** The inspector may sample more, never fewer.
- **Change control.** The sampling, level and AQL are controlled fields of the specification: once it is in use they change only under an approved change request.

## 3. Requirements and acceptance criteria

> **URS-MST-009** Each specification states how its stage is sampled: a fixed sample size, every unit, or ISO 2859-1 single sampling for normal inspection at an inspection level and an AQL; for ISO 2859-1 the inspection's sample size and its accept and reject numbers follow from the size of the lot inspected, as the system counts it, by the standard's tables and its arrows, and every unit is inspected when the sample would reach the lot; the inspector records the nonconforming units found, the lot is accepted only when they do not exceed the accept number and rejected when they reach the reject number, a reading out of its limits counts as a nonconforming unit, and the sampling plan of a specification in use changes only under an approved change request.

| # | Criterion | Verification (`master_data/test_sampling.py`) |
| --- | --- | --- |
| C1 | Every plan of Table 2-A, arrows followed, equals an independent ISO 2859-1 calculator (416 cells) | `test_every_plan_matches_the_independent_calculator` |
| C2 | Table 1 code letters at range edges and levels | `test_code_letters` |
| C3 | An arrow takes the sample size of the letter it leads to | `test_an_arrow_takes_the_sample_size_of_the_letter_it_leads_to` |
| C4 | Every unit when the sample would reach the lot | `test_a_lot_the_sample_would_reach_is_inspected_unit_by_unit`, `test_a_small_lot_is_inspected_unit_by_unit` |
| C5 | Levels, AQLs and lot sizes outside the standard refused | `test_what_is_not_in_the_standard_is_refused` |
| C6 | The plan follows from the lot; the lot is counted by the system (stock, job card) | `test_the_plan_follows_from_the_lot`, `test_the_lot_is_counted_by_the_system_not_typed`, `test_an_in_process_lot_is_the_job_cards_quantity`, `test_a_lot_the_system_cannot_count_is_not_sampled_by_its_size` |
| C7 | Accept up to Ac, reject at Re; count within the sample; readings out of limits counted | `TestPlanOnTheInspection.*` |
| C8 | Sample at least the plan's | `test_the_sample_is_at_least_the_plans` |
| C9 | Every unit; fixed sample size as before | `test_every_unit_of_the_lot`, `test_a_fixed_sample_size_is_decided_by_the_readings_as_before` |
| C10 | Specification: ISO needs level and AQL; plan of a specification in use only under change control | `TestPlanOnTheSpecification.*` |
| C11 | On screen: level and AQL asked only under ISO 2859-1 | `cypress/integration/sampling.js` |

## 4. Step-by-step browser testing guide

### SP-AQL-01 Choose the sampling
Role: QA Manager. **Quality › Quality Inspection Template**, a new specification. Expected: *Sampling* = *Fixed sample size*, *Sample Size* shown. Choose *ISO 2859-1*: *Inspection Level* and *AQL* appear, *Sample Size* hides. Save without them → refused, *does not yet state the inspection level, the AQL*.

### SP-AQL-02 An inspection under the plan
Role: QC Inspector. On a lot of 600 units whose incoming specification is ISO 2859-1, level II, AQL 1.0: the inspection shows Lot Size 600, code letter J, Sample Size 80, Accept 2, Reject 3. Record 2 nonconforming → *Accepted*; 3 → *Rejected*. Lower the sample size below 80 → it returns to 80 on save.

### SP-AQL-03 A small lot
A lot of 6 under the same plan: Sample Size 6, *Every Unit Inspected* ticked, Accept 0, Reject 1.

### SP-AQL-04 Change control
Change the AQL of a specification in use without a change request → refused, *is in use*.

## 5. End-to-end business workflow
QA writes the specification with its sampling (under document and change control) → Stores receives a lot → QC inspects the sample the plan gives, records the readings and the nonconforming units → the lot is accepted or rejected by the plan → QA releases or rejects the lot (module 16).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Tables | `master_data/iso2859_1.json`: Table 1, sample sizes, Table 2-A (normal, single); provenance in its `verified` note |
| Service | `master_data/sampling.py`: `code_letter`, `table_plan`, `plan_for`, `lot_size_of`, `apply_plan`, `judge`, `check_plan_on_submit` |
| Specification fields | `mds_sampling`, `mds_inspection_level`, `mds_aql` (controlled); `mds_sample_size` for a fixed size |
| Inspection fields | `mds_lot_size`, `mds_code_letter`, `mds_accept_number`, `mds_reject_number`, `mds_every_unit` (set by the plan), `mds_nonconforming` (entered) |
| Hooks | Quality Inspection `before_validate` (`specification.choose_specification`, then `sampling.apply_plan`); `validate` (`sampling.judge` after the readings are judged); `before_submit` (`specification.check_sample_size`) |
| Independent check | `master_data/test_iso2859_1_reference.json`: an ISO 2859-1 calculator's 416 plans |

## 7. Defects, edge cases and risks
- Independent review findings, fixed with tests before release: a fixed-sample inspection could not be submitted once saved (an empty reject number reads back as 0) (High); at submit the plan was taken from the record as sent, so it could be shrunk (High); the lot in stock shrank with reservations and moves, now the quantity received for a delivered lot (Medium); full inspection of a conditional supplier's lot kept the plan's accept number (Medium); an inspector's manual rejection was overruled (Medium); limits were read differently from ERPNext, a lot of one and fractional lots, a job card of another item (Low).
- Open: a lot accepted with nonconforming units is released whole once the units are set apart by hand; opening a nonconformance for them is module 33.
- The tables were confirmed cell by cell against two or more independent sources (MIL-STD-105E, MIL-STD-105D, the AQLSchemes package, an ISO 2859-1 chart, an online calculator). A plan rebuilt from memory would have been wrong in row R and the top-right block of Table 2-A. **The client's QA signs the tables off against its own copy of ISO 2859-1 before go-live** (the standard is copyrighted; this system does not reproduce its text).
- Normal inspection only: the switching rules to tightened or reduced inspection are not built (decided 2026-10-11).
- Only one count for the whole sample: critical, major and minor classes with their own AQLs are not built; a specification with a critical characteristic uses *Every unit* or a low AQL.
- A lot accepted with nonconforming units does not yet open a nonconformance automatically (module 33).

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Tables against an independent source, all 416 plans | Verified (automated) |
| Plan from the lot; every unit; decision by count | Verified (automated) |
| Change control of the plan | Verified (automated) |
| On screen: the specification's fields | Verified (browser test) |
| Manual SP-AQL-01…04 | **Not tested** |
| QA sign-off of the tables against ISO 2859-1 | **Open (client)** |
