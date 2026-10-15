# Performance at volume (platform) — feature document and test guide

Requirement: **URS-PLT-021** (`meddevice_suite/qa/requirements.toml`), source: foundation hardening plan, query budgets and volume harness; the owner's instruction on long data, 2026-10-12 ("long data manage, so no future impact"); rules.md section 4 (no queries inside loops over records; indexes on fields used in filters).
Code: `meddevice_suite/qa/test_query_budgets.py` (the query budgets, run in CI), `meddevice_suite/qa/volume.py` (the volume harness, run locally), and the fixes in section 2.3.
State on 2026-10-15: built on branch `chore/volume-and-query-budgets`; the open items were closed later the same day (section 7). The tests and the harness are written; **neither has been run yet** (section 8).

## 1. Feature overview

**Purpose.** The plant will keep years of data: lots, stock ledger entries, audit events, signatures, reminders, training records and documents. The system must stay correct and fast as they pile up. Two things hold it to that:

- **Query budgets (CI).** Every hot path has to issue the same number of database queries on a site with five times the records. A test fails if it issues more. A query inside a loop over records shows up as a dozen extra queries.
- **Volume harness (local).** The harness fills a test site with about three years of data, times each hot path on it against a budget, and checks that the indexes those paths rely on are present.

**Why it matters.** A check that reads once per lot is fast on a new site and slow after three years. A check that runs on every stock movement is also slow for every user. Because it degrades gradually, nobody notices until stores or QA wait on every save. For a regulated system this is a validation risk as well as a speed problem (21 CFR 11.10(a), ISO 13485 4.1.6): the validated state must hold as data grows.

**Who uses it.** The developer, through CI and the harness. No end user sees it, except that the system stays fast.

**Prerequisites.** A test site with `"allow_tests": 1` in its own `site_config.json`, prepared as `rules.md` section 7 says. The harness refuses any other site.

## 2. Detailed functional explanation

### 2.1 The hot paths and their budgets in CI (`qa/test_query_budgets.py`)

Each test works the same way:

1. Build a small data set (SMALL = 3 records of the kind that grows).
2. Run the path once to warm Frappe's caches, then count its queries.
3. Grow the data to LARGE = 15.
4. Run the path again under Frappe's own `IntegrationTestCase.assertQueryCount`, with the small count as the budget.

The counter (`qa.volume.counted`) counts queries exactly as Frappe's assertion does. `assertQueryCount` only accepts a fixed number, so the small count has to be measured first. Daily jobs are measured on an ordinary day: after growing the data they run once to raise and send what the new records call for, which is one write per new item, and are measured on the next run.

| Hot path | Runs | Test |
| --- | --- | --- |
| Guards on a stock movement's ledger entries: approved stores (`master_data/warehouse.py`), finished goods (`lot_control/finished_goods.py`), stock path rule and new stock (`lot_status.py`: `guard_stock_ledger_entry`, `status_of`, `movement_of`), expired lot and first expiry first out (`expiry.py`), stock history of a lot (`lot_dates.py`) | every stock posting | `TestStockMovementBudget.test_the_guards_of_a_movement_do_not_grow_with_the_item_s_lots` (grows the item's lots in another store, all expiring before the lot moved) |
| A stock count's lots counted back (`lot_status.counted_back`) | every count line | `test_a_stock_count_reads_its_lots_at_once` |
| The lots of a document's lines (`lot_status.lots_of_rows`), used by the stock entry, delivery, invoice, pick list, packing slip, goods receipt and count guards | every save of those forms | `test_a_document_s_lines_read_their_lots_at_once` |
| Audit event written on a save, and a record's chain verified (`compliance/audit.py` `record`, `verify_events`) | every save of a regulated record | `TestAuditTrailBudget.test_writing_and_verifying_an_event_does_not_grow_with_the_record_s_history` |
| Hourly anchor and its check (`audit_anchor.anchor_trail`, `verify_trail`) | hourly, and on review | `test_anchoring_and_checking_the_trail_does_not_grow_with_it` |
| Signature and its verification (`compliance/esign.py` `sign`, `verify_signatures`) | every signed act | `TestSignatureBudget` |
| My Work (`work_management/my_work.py` `waiting_on`) | every opening, by every job | `TestPagesAndReportsBudget.test_my_work_does_not_grow_with_what_waits` |
| Stock by Lot Status, Lots Awaiting Release | on opening | `test_the_lot_reports_do_not_grow_with_the_lots` |
| Licence Register | on opening | `test_the_licence_register_does_not_grow_with_the_licences` |
| Each reminder category's due items, and the daily reminder run (`work_management/reminders.py`) | daily | `TestDailyJobsBudget.test_each_reminder_category_reads_alike_whatever_stays_due`, `test_the_daily_reminder_run_reads_alike_on_an_ordinary_day` |
| Daily supplier certificate, customer licence, change action and document review alerts | daily | `test_the_daily_alerts_read_alike_on_an_ordinary_day` |
| Monthly supplier ratings: which are made already | daily | `test_the_monthly_supplier_ratings_read_what_is_rated_at_once` |
| Training reconciliation (`qms/training.py` `reconcile`) | daily and on every change of a job | `TestTrainingBudget.test_the_daily_reconciliation_reads_alike_whatever_the_staff` |
| Operators of a job card competent (`training.not_competent`, from `guard_job_card` and `make_time_log`) | every job card save and time log | `test_the_operators_of_a_job_card_are_checked_at_once` |
| Equipment at a workstation fit, instruments of an inspection usable (`equipment_care.refuse_unusable`, `equipment.usable_all`) | every job card save, work order submit and inspection | `TestEquipmentBudget` |
| Items approved (`item_master.refuse_unapproved`), operations and workstations approved (`routing.not_usable`, `master_approval.unapproved`) | every record naming them | `TestMasterDataBudget` |
| The app's own growing tables are indexed | migrate | `TestIndexes` |

Some paths rightly scale with their own input: one ledger entry per line of the document posted, one reminder raised per new item due, one alert per item falling due. They are budgeted per line or per new item, by measuring with the input fixed while the history grows.

`TestTheBudgetCatchesALoop` checks the check itself: a query per record fails the budget, and one query for all of them keeps within it.

### 2.2 The volume harness (`qa/volume.py`)

```
bench --site <test-site> execute meddevice_suite.qa.volume.run --kwargs "{'scale': 1}"
bench --site <test-site> execute meddevice_suite.qa.volume.run --kwargs "{'generate_data': False}"   # measure again
```

**What `generate(scale)` makes.** At scale 1, about:

| Kind | Count |
| --- | --- |
| Items | 1,000 |
| Lots | 2,000 |
| Stock ledger entries | 20,000 |
| Audit events | 50,000 |
| Signatures | 2,000 |
| Reminders | 1,000 |
| Training records | 500 |
| Controlled documents | 300 |

The history spans three years. Three "hot" raw materials hold about 45% of the lots (hundreds of lots each, as a catheter's silicone has). One record has a 5,000-event audit chain and one lot has a 100-signature chain: these are the worst cases a chain-head lookup and a verification face.

**How each kind is made, and why.**

- **Audit events and signatures go through their own services.** Events are written with `audit.record`. Signatures are written with `esign.sign`, using a harness QA user whose password is generated for the run and kept only in memory. Each entry is fingerprinted together with the one before it on its record. A row bulk-inserted any other way would break the chain that `verify_events`, `verify_signatures` and the anchors check, and would measure a trail no real site can have. The harness then runs the hourly anchor.
- **Reminders and training records also go through their services.** These are `reminders.make` and `complete`, and `training.reconcile` (from employees and approved matrices). They are the only writers of those records.
- **Items, lots, controlled documents with their revisions, and the stock ledger are bulk-inserted**, as a test fixture writes them. They carry no chained fingerprint, and the history they stand for already met the rules their forms enforce.
  - The ledger is consistent with itself. Each store's running quantity, value and FIFO queue follow from the entries before it, in posting order. The bins hold the final balances. Every entry is dated before today, so a movement posted now follows the history as ERPNext expects.
  - This creation is not in the audit trail. On a test site, that is what a fixture means.
- **The movements measured are real.** A lot of a hot item is released, received and issued now, through ERPNext and every guard of this app. Its expiry is the soonest of the item's lots in stock, so first expiry, first out lets it go while still reading every other lot of the item in the store.

**What `measure()` does.** It times each hot path (median of up to five runs), counts its queries, and prints a table of path, time, budget, queries and PASS or FAIL. A path that fails is shown as ERROR with its message, and is logged in the Error Log. Everything `measure` writes is rolled back, so it can be run again on the same data.

**What `check_indexes()` does.** It prints, for every index the hot paths rely on, whether the site has it (section 6).

**Refusal.** `run`, `generate` and `measure` refuse any site whose own configuration does not set `"allow_tests": 1`. They follow `pilot.refuse_unless_pilot_site` and `qa.site_setup.prepare_test_site`, through `setup.site_flags.site_flag`, so a flag set in the bench-wide `common_site_config.json` does not count.

### 2.3 Loop queries found and fixed

| # | Where | What grew | Fix |
| --- | --- | --- | --- |
| 1 | `lot_control/expiry.py` `earlier_lot_left` (every stock issue and delivery) | Every lot of the item that expired before the lot taken was read once per lot of the entry: ERPNext's `get_batch_qty` was called for each such lot, used up or in another store. That is hundreds of lots per item after a few years, on every movement. | `lots_left`: one call of ERPNext's lot quantities for the item in the store, then one read of status and expiry for only the lots with stock left. This runs once per ledger entry, whatever the item's history. |
| 2 | `expiry.taken_by_document` | It filtered Serial and Batch Bundle by `voucher_no`, which ERPNext does not index (its only index on a bundle's document is `voucher_type`, shared by half the table). | `voucher_no` is now indexed by configuration (section 2.4), and the query reads through that index. |
| 3 | `lot_status.counted_back` (every stock count line) | Three queries per lot in the count line's bundle. | Two queries for all the lots. |
| 4 | `lot_status.lots_of_row` in the guards of Stock Entry, Delivery Note, Sales/POS Invoice, Pick List, Packing Slip, Purchase Receipt (`receipt.refuse_existing_lots`) and Stock Reconciliation (`finished_goods.rows_bringing_stock_in`) | One bundle query per line of the document. | `lots_of_rows`: one query for every line's bundle. |
| 5 | `work_management/reminders.py` `supplier_certificates_expiring` (daily) | The decision in force was read for every approval whose certificate had expired. A replaced decision stays in that list for good. | `supplier_approval.newest_decisions`: one query. |
| 6 | `reminders.customer_licences_expiring` (daily) | Whether a licence was renewed was checked once per licence; a renewed licence stays in the list for good. | `customer_licence.renewed_licences`: one query. |
| 7 | `reminders.change_actions_due` (daily) | The change request's status was read per action. The actions of a change rejected or closed undone stay in the list for good. | `approved_changes`: one query. |
| 8 | `reminders.run` follow-ups (daily) | Every overdue and escalated reminder was loaded every day, including escalated ones that never change again. | `to_follow_up`: only the reminders that change today (Open, or Overdue past the escalation days). |
| 9 | `supplier_approval.send_certificate_alerts` (daily) | Same as 5. | `newest_decisions`. |
| 10 | `customer_licence.send_expiry_alerts` (daily) | Same as 6. | `renewed_licences`. |
| 11 | `change_control.send_change_alerts` (daily) | Same as 7. | One query for the requests' status. |
| 12 | `document_control.send_review_alerts` (daily) | Per document, a read of what it was last alerted for. | Read with the documents. |
| 13 | `supplier_rating.rate_finished_months` (daily) | An `exists` per supplier and month (12 × suppliers every day). | One query per month. |
| 14 | `equipment_care.refuse_unusable` (every job card save, work order submit), `equipment.guard_inspection_instruments` (every inspection) | About ten queries per piece of equipment (`usable`, then `unfit` for one asset). | `equipment.usable_all`: one read of the assets and one `unfit` for all. |
| 15 | `training.not_competent` (every job card save and time log) | A refusal read each operator's revision number. | One query for the revisions named. |
| 16 | `training.reconcile` (daily) | Not a query, but processing time: for each need opened it scanned every training record, which grows with the square of the history. | A set built once. |
| 17 | Lots Awaiting Release report and My Work (`latest_inspections`), and the QA release (`receipt` newest inspection) | Quality Inspection by `batch_no`, which ERPNext does not index. | `batch_no` is now indexed by configuration (section 2.4), and both read through it. |
| 18 | Every ledger entry of a document (`lot_status.movement_of`, `expiry.describe`, `finished_goods.guard_stock_ledger_entry`) | The checks re-read the voucher header 4–5 times per line, plus the entry's line. | `lot_status.voucher_header` and `stock_entry_line` read each once per document. The values are kept for the transaction only: they are cleared on commit or rollback, and a document's own values are cleared when it is submitted or cancelled again (the `"*"` `before_submit`/`before_cancel` hooks). `test_a_document_s_header_is_read_once_whatever_its_lines` checks that a document of 3 or of 15 lines reads its header once and its lines once. |
| 19 | Daily `pinned_records.sweep` → `device_master_record.elements_changed` | Every record out of date was locked, loaded and described every day, only to find nothing new. | `unrecorded`: one query compares the changes with what the element rows already record, using the same comparison as `mark_out_of_date`. Only a record with something new is loaded. `TestPinnedRecordSweepBudget`. |
| 20 | `evidence_files.is_evidence` (every File save and permission check) | Eight lookups on file columns that had no index. | The columns are indexed by configuration (section 2.4); each lookup goes through its index. |
| 21 | `reminders.run` recurring schedules, `send_notices` job members, `my_work.customer_returns` | One load-and-check per schedule, one role lookup per reminder told, and two ledger queries per Returns store. | One query for the schedules plus one for the occurrences already raised; one query for the members of every role named; one `stock_by_lot` call for all Returns stores. |

### 2.4 Indexes

**Added on the app's own doctype:**

- `Document Revision.controlled_document` (`search_index`). Revisions are read by document in training reconciliation (`revisions_to_train`).
- `Document Revision.status` (`search_index`). My Work's review and approval queues and the daily `make_due_revisions_effective` read revisions by status.

**Configured on ERPNext's tables and on file columns (`setup/indexes.py`, called by `apply_configuration` on every install and migrate; idempotent).** ERPNext's files are never edited.

- **`Quality Inspection.batch_no` and `Serial and Batch Bundle.voucher_no`.** A Property Setter marks each field `search_index`, and the index is made under Frappe's own name for it, `<field>_index`.
  - Why this way: when an ERPNext upgrade changes a doctype, Frappe's schema sync drops any one-column index that the field's definition does not mark as indexed (`frappe/database/schema.py`). A bare `add_index` would therefore be dropped on the upgrade, and would only come back at the end of the migrate.
  - The Property Setter is part of the doctype's definition. It is stored in the database and applied on top of every ERPNext release, so the sync keeps the index, and recreates it if it is ever missing.
- **The evidence file columns** (`Document Revision.file`, `Record Archive.archive_file`, `Purchase Receipt Item.mds_certificate`, `Supplier Approval.certificate`, `Customer Licence.licence_copy`, `Important File Version.file`, `Calibration Record.certificate`, `Training Record.certificate`). Each gets a `<field>_file_index` on the first 255 characters of the address, followed by the record's name.
  - Why this way: Attach columns are stored as text. MariaDB indexes text only by a prefix, which `search_index` cannot express, and Frappe's schema sync never adds or drops an index on a text column, so this index survives every upgrade unchanged.
  - Why the name is included: with two columns, Frappe does not also record a field-level search index, which it does for a one-column index made outside a migrate and which a text field cannot hold.
  - Why 255 is enough: an address is far shorter than 255 characters, so a lookup by address is exact.

**Checked present:** the app's indexes are listed in `qa/volume.py` `APP_INDEXES` and held there by `TestIndexes`. ERPNext's are in `ERPNEXT_INDEXES`. Section 6 has the full list.

### 2.5 What is intended but not done (open)

These are listed in section 7, each with the reason it was left.

## 3. Requirements and acceptance criteria

> **URS-PLT-021** The checks that run on every stock movement, on every save and signature of a regulated record and on every opening of My Work and of the lot and licence reports, and the hourly and daily jobs, each issue a number of database queries that does not grow with the lots, ledger entries, audit events, signatures, reminders, training records and documents the site holds, and every filter they use on a growing table of this app is served by an index.

| # | Acceptance criterion | Implementation | Automated verification (`qa/test_query_budgets.py`) |
| --- | --- | --- | --- |
| C1 | A query per record fails the budget, and one query for all of them passes it | `QueryBudget.assert_does_not_grow` | `TestTheBudgetCatchesALoop` (2 tests) |
| C2 | Stock movement guards, count lines and line lots do not grow with the item's lots; a document's header is read once whatever its lines | fixes 1–4, 18 | `TestStockMovementBudget` (4) |
| C3 | Audit write, verification, anchor and anchor check do not grow with the trail | `audit.py`, `audit_anchor.py` (indexed already) | `TestAuditTrailBudget` (2) |
| C4 | Signing and verification do not grow with a record's signatures | `esign.py` (indexed already) | `TestSignatureBudget` (1) |
| C5 | My Work and the lot and licence reports do not grow with what waits | `my_work.py`, reports, fix 17 | `TestPagesAndReportsBudget` (3) |
| C6 | The daily reminder run, its categories, the daily alerts, the monthly ratings and the pinned-record sweep read alike whatever stays due or out of date, and however many schedules are planned | fixes 5–13, 19, 21 | `TestDailyJobsBudget` (4), `TestPinnedRecordSweepBudget` (1) |
| C7 | Training reconciliation and the job card's competence check do not grow with the staff | fixes 15, 16 | `TestTrainingBudget` (2) |
| C8 | Equipment at a workstation and an inspection's instruments are judged at once | fix 14 | `TestEquipmentBudget` (2) |
| C9 | Items, operations and workstations of a record are checked at once | `item_master.py`, `routing.py`, `master_approval.py` (batched already) | `TestMasterDataBudget` (2) |
| C10 | The growing tables carry the indexes their filters need, the app's own and those it configures on ERPNext's tables, in a way that outlives an ERPNext upgrade | doctype JSON, `on_doctype_update`, `setup/indexes.py` | `TestIndexes` (3) |
| C11 | On about three years of data, each hot path completes within its time budget | `qa/volume.py` | Volume harness, run locally (not CI) |

## 4. Step-by-step testing guide

This feature has no screen of its own. It is tested from the command line, and then checked in the browser on the volume site.

### PV-01 Query budgets (C1–C10)

1. Run only this feature's tests: `bench --site <test-site> run-tests --app meddevice_suite --module meddevice_suite.qa.test_query_budgets`.
2. Expected: every test passes. A failure prints the queries of the run that grew. Look for one repeated with a different record name: that is the loop.

### PV-02 Volume harness (C11)

1. On a test site (`"allow_tests": 1` in its own `site_config.json`): `bench --site <test-site> execute meddevice_suite.qa.volume.run --kwargs "{'scale': 1}"`.
2. Expected: progress lines, then the table of hot paths with PASS in every row, then the index list with no MISSING row for the app.
3. Run it again with `{'generate_data': False}`. Expected: the same table. `measure` rolled back what it wrote.
4. Refusal: run it on a site without `allow_tests`. Expected: "… is not a test site; refusing to load volume data", and nothing written.

### PV-03 Browser on the volume site (as each job)

Signed in as QA Manager, Stores and Production Supervisor in turn, open My Work, the Batch list filtered by Lot Status, Stock by Lot Status, Lots Awaiting Release, the Audit Event list and a lot with a long audit history (its timeline and Verify Integrity). Expected: each opens within about a second, and Verify Integrity reports the chain intact.

### Regression

Run the full suite once per pull request (memory: targeted test runs). The fixes change code paths tested by `lot_control/test_expiry.py`, `test_lot_status.py`, `test_release.py`, `test_finished_goods.py`, `work_management/test_reminders.py`, `master_data/test_supplier_approval.py`, `test_customer_licence.py`, `test_supplier_rating.py`, `test_equipment.py`, `qms/test_change_control.py`, `test_document_control.py`, `test_equipment_care.py` and `test_training.py`.

## 5. End-to-end business workflow

None of its own. The guards and jobs above are steps of the standard flow: receipt → release → issue → production → delivery, the daily reminders and alerts, and training. Each step is held to its budget whatever the history behind it.

## 6. Technical reference

**Time budgets (`qa/volume.py` `BUDGET_MS`), in milliseconds, on scale-1 data:**

| Path | Budget |
| --- | --- |
| stock movement: guards on its ledger entries | 150 |
| stock movement: whole issue through ERPNext | 3000 |
| stock count: lots counted back (100 lots) | 50 |
| lot dates: stock history of a lot | 20 |
| audit trail: write an event (longest chain) | 50 |
| audit trail: verify a record (longest chain, 5,000 events) | 1000 |
| audit trail: hourly anchor / check the anchors | 1000 each |
| signature: sign (longest chain, with password check) / verify | 1000 / 200 |
| My Work | 1000 |
| reminders: due items of a category (each) / daily run | 500 / 20000 |
| alerts: daily supplier, licence, change and review alerts | 2000 |
| training: daily reconciliation / operators of a job card (50) | 3000 / 200 |
| master data: items of a 100-line record / operations and workstations (50) | 100 / 100 |
| report: Stock by Lot Status / Lots Awaiting Release / Licence Register | 3000 / 3000 / 500 |

**Indexes checked (`APP_INDEXES`, held by `TestIndexes`, which also requires the Property Setters behind the configured ones and that applying them again changes nothing):**

- Audit Event: (reference_doctype, reference_name), previous_event, user, event_at, creation.
- Electronic Signature: (reference_doctype, reference_name), previous_signature, signer, creation.
- Audit Anchor: (ledger, creation).
- Reminder: category, status, reminder_key.
- Work Task: status, assigned_to, approver.
- Training Record: employee, document_revision, status.
- Document Revision: controlled_document, status (both added).
- Lot Release: batch.
- Supplier Approval: supplier.
- Customer Licence: customer.
- Batch: mds_lot_status and expiry_date. These are this app's indexes on ERPNext's table.
- Configured: Quality Inspection.batch_no, Serial and Batch Bundle.voucher_no, and the eight evidence file columns (section 2.4). A dropped index fails CI.

**ERPNext indexes checked, read-only (`ERPNEXT_INDEXES`):**

- Present:
  - Stock Ledger Entry: batch_no, (voucher_no, voucher_type), (item_code, warehouse, …), serial_and_batch_bundle.
  - Serial and Batch Entry: parent, batch_no.
  - Bin: (item_code, warehouse).
  - The parent index of every line table read.
  - Employee: status, designation.
- **Absent before this change:** Serial and Batch Bundle.voucher_no and Quality Inspection.batch_no. Both are now configured (section 2.4), so they are listed with the app's indexes.

`check_indexes` prints an ERPNext index that is still absent as "no (ERPNext, not changed)". Any index this app needs is configured, never written into ERPNext's files: rules.md section 1.

## 7. Defects, edge cases and risks

Closed on 2026-10-15:

- The per-line voucher header re-read is fix 18.
- The daily re-load of device master records that are out of date is fix 19.
- The configuration-scaled reads are fix 21.
- The missing indexes on `Quality Inspection.batch_no`, `Serial and Batch Bundle.voucher_no` and the evidence file columns are fixes 2, 17 and 20 (section 2.4).

**Reads that loop over configuration, each confirmed to issue one query per record type, not one per record:**

- The daily run's reminder categories, `CATEGORIES`: 16 declared in code.
- The pinned-record owners, `PIN_OWNERS`: 2, in code.
- The anchor's two ledgers.
- The two kinds of expiry alert.
- The 12 months of supplier ratings.
- The care record types of equipment fitness.
- The sources of My Work.

Each of these loops over a fixed list in the code. The schedules, job members and Returns stores, which are configured on the site, are each read in one query (fix 21).

| # | Item | Status / reason |
| --- | --- | --- |
| R1 | Each ledger entry still reads its own lots (`lots_of_bundle`, once in the lot status check and once in the expiry check) and its item's approval (`item_master.refuse_unapproved`, through the `"*"` before_validate and validate hooks) | Accepted. These are one or two indexed reads per line, about the line's own lot and item, and they do not grow with history. They are budgeted per line. |
| R2 | The anchor counts entries up to its head (`count_entries`) through the `creation` index | Accepted. The query count is constant, but the time grows with the trail. The harness measures it against 1 s. |
| R3 | Training reconciliation reads every training record that is not cancelled | Accepted. The query count is constant, but the rows grow with employees × documents × revisions. The harness measures it against 3 s. |
| R4 | Stock by Lot Status and Lots Awaiting Release aggregate the whole stock ledger (`stock_by_lot`) | Accepted for now. The query count is constant, but the time grows with the ledger. The harness measures it against 3 s. If that budget fails, the remedy is a table of balances per lot. |
| R5 | A budget measured at SMALL may include a one-off write, such as an anchor finding alerted once | Handled. Such paths are run before measuring, and the assertion is "not more than", so a one-off at the small size cannot hide growth larger than itself. |
| R6 | Branch `fix/impact-list-past-workstations` changes `equipment_care.repaired_and_moved` | Compatible. This branch changes only `refuse_unusable`, which calls `equipment.usable_all` → `unfit`, and `unfit` keeps calling `repaired_and_moved(assets)` once for all the assets. |

## 8. Test results and verification summary

| Check | Command / run | Result |
| --- | --- | --- |
| Query budgets | `bench --site <test-site> run-tests --app meddevice_suite --module meddevice_suite.qa.test_query_budgets` | **Not yet run** (written 2026-10-15) |
| Full suite, fresh site | CI | **Not yet run** |
| Volume harness, scale 1 | `bench --site <test-site> execute meddevice_suite.qa.volume.run --kwargs "{'scale': 1}"` | **Not yet run** |
| Lint and format | `pre-commit run --files <changed>` | pass |
| Semgrep (Frappe rules + python correctness) | `semgrep scan --error …` | 0 findings |

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Hot paths budgeted in CI, each with `@verifies("URS-PLT-021")` | Written; **not run** |
| Loop queries found are fixed (section 2.3) | Done; existing module tests **not run** on the changes |
| Indexes added on the app's own doctype (Document Revision) and configured on ERPNext's tables and the evidence file columns (`setup/indexes.py`), checked by test | Done; **not run** (needs migrate) |
| Voucher header read once per document; device master record sweep; configuration-scaled reads | Done, with budgets; **not run** |
| Volume harness generates scale-1 data, refuses non-test sites, prints PASS/FAIL per budget and the index check | Written; **not run** |
| Browser check on the volume site as each job (PV-03) | **Not tested** |
| Remaining items R1–R6 | Accepted with reasons (section 7) |
