# Record retention and archival (module 54) — feature document and browser test guide

Requirement: **URS-CMP-023, URS-CMP-024, URS-CMP-025, URS-CMP-026** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-010 (archives are the periodic protection of the audit chain's newest entries), URS-CMP-016 (no role deletes a regulated record), URS-CMP-001 (failed signings in the login history).
Code: `meddevice_suite/compliance/retention.py` (schedule, login history), `compliance/report/retention_schedule/` (report **Retention Schedule**), `compliance/archive.py` (building and verifying archives), `compliance/doctype/record_archive/` (DocType **Record Archive**, `record_archive.js`), `compliance/evidence_files.py` (the archive file is protected evidence), hooks in `meddevice_suite/hooks.py`, applied by `setup/install.py` (`apply_retention`, `ensure_indexes`).
State on 2026-10-10: built (`28536c94`, `9d5eeb26` 2026-10-06; scale, damage and stopped-job robustness in `9f7a6efd`; period fields on the form `7db7c9a2` 2026-10-07), server and browser suites green on the current branch; GitHub CI in progress (section 8).

## 1. Feature overview

**Purpose.** Every regulated record type, the audit trail, the electronic signatures and the login history have a declared retention period (ten years) with its legal basis, visible to QA as a report. The login history is kept for that period instead of Frappe's 90 days. QA can produce an archive copy of every regulated record changed in a period, each in full with its audit history and signatures, plus the period's login history, as one ZIP file readable without the system; the records stay in the system. Every archive carries a SHA-256 of the whole file and of every file in it, can be verified at any time, and can never be changed or deleted.

**Why it matters.** ISO 13485 4.2.5 (records retained at least the device lifetime, never less than two years from release) and EU MDR Art. 10(8) (ten years) set the period; the client fixed ten years on 2026-10-07 (covers an eight-year shelf life). 21 CFR 11.10(b) requires accurate and complete copies in human-readable and electronic form for inspection; 11.10(c) requires record protection for the retention period. D3 of the client's diagrams is "Retention & Archival".

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager | Reads **Retention Schedule**; requests a **Record Archive** for a period; verifies archives; downloads the file for off-system safekeeping |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Same as QA for archives and the report; sees **Log Settings** (cannot shorten the login history); watches Error Log for failed archives |
| Everyone else | — | No access to the Compliance area, the report or Record Archive |

**Prerequisites.** A background worker (`long` queue) and the daily scheduler run on the site; the site's `private/files` has room for the ZIP. Nothing to configure by hand: Log Settings and indexes are applied by `bench migrate`.

## 2. Detailed functional explanation

### 2.1 The retention schedule (URS-CMP-023)

`retention.RETENTION_SCHEDULE` declares, per category, the years, the legal basis and the record types:

| Category | Years | Basis | Record types |
| --- | --- | --- | --- |
| Device and production records | 10 | ISO 13485 4.2.5; 21 CFR 820; EU MDR Art. 10(8); CDSCO MDR 2017 | Item, BOM, Routing, Operation, Workstation, Warehouse, Batch, Quality Inspection Template, Quality Inspection, Lot Release, Work Order, Job Card, Stock Entry, Stock Reconciliation, Serial and Batch Bundle, Pick List |
| Sales records | 10 | same | Quotation |
| Quality system records | 10 | same | Controlled Document, Document Revision, Change Request |
| Purchasing and supplier records | 10 | same | Supplier, Supplier Approval, Material Request, Purchase Order, Purchase Receipt |
| Distribution records | 10 | same | Customer, Delivery Note, Packing Slip |
| Equipment and personnel records | 10 | same | Asset, Asset Maintenance Log, Asset Repair, Employee |
| Access control and system settings records | 10 | 21 CFR 11.10(d), (e), (g) | User, Role, Role Profile, User Permission, Custom DocPerm, System Settings, Stock Settings |
| Compliance evidence | 10 | 21 CFR 11.10(c), (e); 11.70 | Audit Event, Electronic Signature, Audit Anchor (since 2026-10-12: kept as long as the entries it counts, which verifying them needs), Activity Log, Record Archive |

The schedule must cover every regulated record type (module 50's `REGULATED_DOCTYPES`), every non-child DocType this app defines, the evidence and the login history, each once; the test `test_every_regulated_record_type_and_the_evidence_is_scheduled_once` fails the build when a new DocType is added without a retention class. Nothing is ever deleted at the end of the period: no role may delete a regulated record (module 47) and the compliance records refuse deletion, so the period is the minimum the records stay retrievable.

QA sees it as the script report **Compliance › Retention Schedule** (`/desk/query-report/Retention Schedule`, roles Quality Manager and System Manager) with columns **Category**, **Record type** (link to the DocType), **Kept for (years)**, **Counted from** (*The date the record was made*), **Legal basis**.

### 2.2 Login history kept for the period (URS-CMP-024)

Frappe clears **Activity Log** (logins, logouts, failed logins, impersonations; module 49 also writes refused signings there) after 90 days through **Log Settings › Logs to Clear**. `apply_retention()` (every migrate) sets the Activity Log row's **Clear Logs After (days)** to 10 × 366 = 3660 days, adding the row if it is missing and leaving the settings untouched when already right. Saving Log Settings with the row removed or below 3660 days is refused (`protect_login_history`, Log Settings validate): *The login history is kept for 3660 days and cannot be cleared sooner.* Audit Event and Electronic Signature are not in Logs to Clear at all. System Administrator sees Log Settings under the **System** sidebar; Activity Log under **Users**.

### 2.3 Requesting an archive (URS-CMP-025)

**Compliance › Record Archive › New** (`/desk/record-archive/new`): fields **Records Changed From** and **Records Changed To** (dates, required). On **Save** the period is checked (*The period must end on or after the day it starts.*; *The period cannot end in the future.*), the record `ARC-YYYY-NNNN` is created with **Status** *Queued*, and `archive.build_archive` is queued on the `long` worker (six-hour limit; run inline in tests). The job sets Status *Running* (committed so it is visible), writes `private/files/ARC-….zip`, registers it as a private **File** attached to the record without copying it (so Frappe's upload-size limit and duplicate copy do not apply), and sets Status *Completed*, **Records Archived** (count), **Completed At**, **Archive File** (link to download) and **Archive Fingerprint (SHA-256)**. On any error: Status *Failed*, **Error** holds the last 2000 characters of the traceback, the partial ZIP is removed, and the failure is in **Error Log** (*Record archive ARC-… failed*). A job that stopped without finishing (worker restart) is marked Failed by the daily `fail_stalled_archives` after 12 hours in Queued/Running (*The archive job stopped before it finished. Request a new archive.*). The form offers **Verify** once Status is *Completed*.

Only Quality Manager and System Manager may create or read Record Archive (write is granted only so the desk shows the period fields on a new record; `validate` refuses every save after the first: *A record archive cannot be changed.*; `on_trash`: *A record archive cannot be deleted.*).

### 2.4 What the archive contains (URS-CMP-025)

One ZIP, readable with any unzip tool:

| Entry | Content |
| --- | --- |
| `records/<record_type>/<name>.json` | one file per regulated record whose last change (`modified`) falls in the period (for single DocTypes such as System Settings, when their `modified` does): `record_type`, `record` (the full record with child rows, dates as text), `audit_events` (every Audit Event of the record, oldest first, all fields), `electronic_signatures` (every signature, all fields) |
| `deleted/<record_type>/<name> <event>.json` | one per *Deleted* audit event in the period for an archived record type: `deleted` (by, at, audit event), `record` (the snapshot taken at deletion), plus the same history |
| `index.csv` | `record_type, name, docstatus, last_changed, file, sha256` for every record file above (`docstatus` = `deleted` for deletions) |
| `logins.csv` | the period's Activity Log rows: `creation, name, user, full_name, operation, status, subject, ip_address` |
| `manifest.json` | archive name, period (from 00:00:00 to 23:59:59.999999), `made_at`, `made_by`, `record_counts` per type, the full retention schedule, and `files`: the SHA-256 of `index.csv` and `logins.csv` |

Archived record types are every scheduled type except Audit Event, Electronic Signature, Activity Log and Record Archive (they are inside the record files or files of their own). Records are read 500 at a time by `(modified, name)` through an index `ensure_indexes` adds to every archived table; file names are URL-encoded, shortened past 150 characters with a fingerprint suffix, and reserved Windows names (CON, NUL, COM1 …) are prefixed with `_`. The records are copies; nothing is removed from the system.

### 2.5 Verifying an archive (URS-CMP-026)

**Verify** on a Completed archive (`archive.verify`, POST) recomputes: the SHA-256 of the whole ZIP against **Archive Fingerprint (SHA-256)**; the SHA-256 of `index.csv` and `logins.csv` against the manifest; the SHA-256 of every record file against its index line; and that no entry exists outside manifest + index + listed files. Messages (desk `record_archive.js`):

- green **Archive verified** — *Every file is exactly as it was when the archive was made.*
- red **Archive has changed** listing: *The archive file has changed since it was made.*, *<entry> has changed.*, *<entry> is missing from the archive.*, *<entry> was added to the archive.*, *<entry> cannot be read.*, *The archive file cannot be read.*, *The archive file is missing.*
- blue **Verification started** — *This archive is large, so it is verified in the background. You will be notified of the result.* for files above 256 MB; the result arrives as a desk notification *Archive ARC-… verified: every file is as it was made* or *Archive ARC-… has changed: <first 20 problems>*.

A damaged ZIP (corrupted compressed data, wrong manifest shape) is reported, never crashed on. Only Quality Manager and System Manager may verify; an archive that is not Completed cannot be verified (*Only a completed archive can be verified.*).

### 2.6 The archive cannot be changed or deleted (URS-CMP-026)

The Record Archive refuses every change and deletion (2.3). Its file is protected evidence (`evidence_files.EVIDENCE` lists `("Record Archive", "archive_file")`): the File permission hook refuses write and delete for everyone, and `File.before_validate` refuses any change of URL, privacy, name, content hash or attachment (*<file name> is part of a record's evidence and cannot be changed.*), so it can neither be deleted nor made public — also for QA Manager and System Administrator (tested). The built-in Administrator account is outside these hooks (Frappe grants it everything before apps are asked; documented in the module; that account is for installation only, URS-CMP-034).

### 2.7 What is intended but not implemented

- **Human-readable form:** the archive is JSON and CSV, readable in any text editor or spreadsheet; there is no PDF rendering of each record. The requirement asks for "a file that can be read without the system", which JSON/CSV meets; confirm with the client whether printed-form copies are expected for inspections.
- **Off-system storage:** the ZIP stays in the site's private files; copying it to external media or cloud storage is the client's procedure (download from the record's **Archive File**). No automatic export.
- **Scheduled archives:** archives are requested by QA; there is no automatic monthly archive (the requirement says "QA can produce").
- **Disposal at the end of the period:** nothing is deleted or flagged after ten years (by design: records are never destroyed, rules.md 1.6).
- Record Archive is not submittable (it is sealed by its controller instead, since the archive is produced by a job).
- Only the record's *last* change selects it into a period; a record changed in the period and again after it appears in the later period's archive (its history, including the earlier change, is complete in that file).

## 3. Requirements and acceptance criteria

| # | Requirement / criterion | Implementation | Automated verification (`compliance/test_retention.py`, `compliance/test_archive.py`) |
| --- | --- | --- | --- |
| C1 | URS-CMP-023: every regulated type, audit trail, signatures, login history have ≥ 10-year period with legal basis | `RETENTION_SCHEDULE`, `must_be_scheduled`, `retention_years` | `TestRetentionSchedule` (3: `test_every_regulated_record_type_and_the_evidence_is_scheduled_once`, `test_record_types_outside_the_schedule_have_no_period`, `test_every_period_is_at_least_ten_years_with_a_legal_basis`) |
| C2 | URS-CMP-023: QA can view it in the system | report Retention Schedule (roles Quality Manager, System Manager) | `TestRetentionScheduleReport` (2: `test_qa_sees_the_period_and_basis_of_every_scheduled_record_type`, `test_jobs_outside_qa_cannot_open_it`) |
| C3 | URS-CMP-024: login/logout/failed-login/impersonation kept for the period, not 90 days | `apply_retention`, `protect_login_history`, `login_history_days` | `TestLoginHistoryIsKept` (6: `…kept_for_its_retention_period`, `test_a_year_old_login_survives_frappes_log_clean_up`, `test_a_manual_change_is_undone_on_the_next_migrate`, `test_a_missing_entry_is_added_back_and_an_unchanged_one_left_alone`, `test_the_login_history_cannot_be_shortened_or_removed_on_a_site`, `test_the_audit_trail_and_signatures_are_not_logs_frappe_clears`) |
| C4 | URS-CMP-025: archive of every regulated record changed in a period, in full, with audit history and signatures, plus login history, readable without the system; records stay | `build_archive`, `write_archive`, `record_file`, `history`, `login_history`, manifest | `TestArchiveContent` (5), `TestArchiveAtScale` (6, e.g. `test_records_deleted_in_the_period_are_archived_with_their_history`, `test_record_names_become_file_names_any_system_opens`), `TestArchiveRequests` (4); Cypress `record_archive.js` "[URS-CMP-025] lets QA request an archive from its form", "[URS-CMP-025] [URS-CMP-026] builds an archive in the background and verifies it" |
| C5 | URS-CMP-026: fingerprint of the whole file and of every file; verification detects changed/removed/added files | `file_digests`, index + manifest hashes, `verify`, `problems_in`, `entry_problems` | `TestArchiveIntegrity` (6: `test_a_new_archive_verifies`, `test_a_changed_record_file_is_detected`, `test_removed_and_added_files_are_detected`, `test_a_changed_index_or_login_history_is_detected`, `test_a_missing_or_unreadable_file_is_reported`, `test_an_archive_cannot_be_changed_or_deleted`); `TestArchiveIsProtectedAndRecoverable` (6) |
| C6 | URS-CMP-026: cannot be changed or deleted (record and file) | controller `RecordArchive`, `evidence_files` | `test_an_archive_cannot_be_changed_or_deleted`, `test_the_archive_file_cannot_be_deleted_or_made_public` |
| C7 | Robustness (design): large archives, stopped jobs, failures leave no partial file, background verification | paging, `fail_stalled_archives`, `unlink`, `verify_in_background` | `test_large_periods_are_read_page_by_page_without_gaps_or_repeats`, `test_an_archive_whose_job_stopped_is_marked_failed`, `test_a_failed_archive_leaves_no_partial_file`, `test_a_large_archive_is_verified_in_the_background_and_the_requester_told`, `test_damaged_compressed_data_is_reported_not_crashed_on` |

**Partial / ambiguous:** C4 "read without the system" is met by JSON/CSV, not by printed-form copies (section 2.7, client to confirm). **Not covered:** off-system storage and disposal procedures (client SOP).

## 4. Step-by-step browser testing guide

Site: pilot (`pilot.localhost`) or a test site with a running worker and scheduler. Archives of the pilot's demo data are small (seconds). Record actual results and screenshots (report, archive form after completion, Verify message, the ZIP opened on the tester's computer).

### RA-01 Retention Schedule report (C1, C2)
Role: QA Manager.
1. Open **Compliance › Retention Schedule**. Expected: a table with columns **Category**, **Record type**, **Kept for (years)**, **Counted from**, **Legal basis**; every row shows 10 years and *The date the record was made*; categories as in section 2.1; *Audit Event*, *Electronic Signature*, *Audit Anchor*, *Activity Log*, *Record Archive* under *Compliance evidence*.
2. Click a **Record type** link (e.g. *Supplier Approval*). Expected: opens the DocType record (read-only for QA) — or a permission refusal if QA may not read DocType; note which.
3. As Sales (`sales@demo.example.com`) visit `/desk/query-report/Retention Schedule`. Expected: refused plainly.
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of step 1.

### RA-02 Login history retention (C3)
Role: System Administrator.
1. **System › Log Settings**. Expected: in **Logs to Clear** a row **Log DocType** = *Activity Log* with **Clear Logs After (days)** = 3660; no rows for Audit Event or Electronic Signature.
2. Change the Activity Log row to 90 days, **Save**. Expected: *The login history is kept for 3660 days and cannot be cleared sooner.*
3. Delete the Activity Log row, **Save**. Expected: same refusal.
4. Sign out and in twice, and once with a wrong password; open **Users › Activity Log**. Expected: Login *Success* and *Failed* entries for your user with time and IP.
Actual result: ____  Pass/Fail: ____

### RA-03 Request and build an archive (C4)
Role: QA Manager. Preconditions: some regulated records changed today (e.g. the AT-01/SA-01 tests of the other documents, or the pilot flow).
1. **Compliance › Record Archive › New**. Expected: fields **Records Changed From** and **Records Changed To** editable; Status not shown or *Queued*.
2. To = today, From = today + 1; **Save**. Expected: *The period must end on or after the day it starts.*
3. From = today, To = tomorrow; **Save**. Expected: *The period cannot end in the future.*
4. From = To = today; **Save**. Expected: saved as `ARC-YYYY-NNNN`; Status pill *Queued* then *Running* then *Completed* (reload after a few seconds); **Records Archived** > 0; **Completed At**; **Archive File** link `/private/files/ARC-….zip`; **Archive Fingerprint (SHA-256)** 64 hex characters; button **Verify** appears.
5. Click **Archive File** to download; open the ZIP on your computer. Expected: folders `records/<record_type>/…json`, possibly `deleted/…`, files `index.csv`, `logins.csv`, `manifest.json`; open one record JSON in a text editor: `record`, `audit_events`, `electronic_signatures`; `logins.csv` lists today's logins; `manifest.json` names the archive, period, your user as `made_by`, counts per type and the retention schedule.
6. Open **Buying › Supplier Approval** (or any archived record). Expected: still in the system, unchanged.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 4 and 5.

### RA-04 Verify an intact archive (C5)
Role: QA Manager. 1. Open the archive of RA-03 › **Verify**. Expected: green **Archive verified** — *Every file is exactly as it was when the archive was made.*
2. As System Administrator: same. Expected: same.
Actual result: ____  Pass/Fail: ____

### RA-05 Tampering is detected (C5) — test site only, file access
1. On the server, re-zip `ARC-….zip` with one record file edited (change a value). **Verify**. Expected: red **Archive has changed** listing *The archive file has changed since it was made.* and *records/…/<name>.json has changed.*
2. Remove a record file and add an extra file `notes.txt`; **Verify**. Expected: *… is missing from the archive.* and *notes.txt was added to the archive.*
3. Replace `manifest.json` with `{"files": ["x"]}`; **Verify**. Expected: *The archive file cannot be read.*
4. Delete the ZIP from `private/files`; **Verify**. Expected: *The archive file is missing.*
Actual result: ____  Pass/Fail: ____  Cleanup: discard the test site.

### RA-06 Archive and file cannot be changed or deleted (C6)
1. As QA Manager open a Completed archive; try to change **Records Changed To** (fields are read-only after the first save; if editable, **Save**). Expected: *A record archive cannot be changed.*
2. List › select the archive › **Actions › Delete**. Expected: *A record archive cannot be deleted.*
3. Open the attachment (sidebar paperclip) › try to delete it, and try to switch it to public. Expected: refused (*… is part of a record's evidence and cannot be changed.* or a permission error); the file remains private. Repeat as System Administrator. Expected: same.
Actual result: ____  Pass/Fail: ____

### RA-07 Failed and stalled archives (C7) — test site only
1. Stop the `long` worker, request an archive, wait. Expected: Status stays *Queued*; after 12 hours (or run `bench --site <site> execute meddevice_suite.compliance.archive.fail_stalled_archives` after setting the record's `modified` back on a test site) Status *Failed*, **Error** = *The archive job stopped before it finished. Request a new archive.*; no partial ZIP in `private/files`.
2. Start the worker, request a new archive. Expected: Completed.
3. Try **Verify** on a Failed archive. Expected: no Verify button; the API `meddevice_suite.compliance.archive.verify` returns *Only a completed archive can be verified.*
Actual result: ____  Pass/Fail: ____

### RA-08 Permissions and lists
1. As Sales: visit `/desk/record-archive`. Expected: refused plainly; no Compliance area on the desk.
2. As QA Manager: **Compliance › Record Archive** list shows **Records Changed From**, **Records Changed To**, **Status**; filter Status = Completed; sort by creation. Expected: works; **Menu › Export** available.
Actual result: ____  Pass/Fail: ____

### Regression
- An archive of a period containing deleted records (a draft deleted on a test site) lists them under `deleted/` with their snapshot; the other modules' journeys are unaffected (the archive only reads).

## 5. End-to-end business workflow

1. During the month, every job's work creates and changes regulated records (modules 4, 16, 17, 30, 36, 48 …); module 50 records each action, module 49 the signatures, Frappe the logins (kept 3660 days).
2. At month end **QA** opens **Compliance › Record Archive › New**, enters the month's period, saves; the worker builds `ARC-YYYY-NNNN.zip` (records, deletions, `index.csv`, `logins.csv`, `manifest.json`) and records its SHA-256.
3. **QA** clicks **Verify** (green), downloads the ZIP and stores it per the client's archival SOP (off-site media, WORM storage); the fingerprint on the record lets anyone later confirm the stored copy is the one made.
4. Before an audit, **QA** verifies the archives of the inspected period again and opens **Retention Schedule** to show the auditor the declared periods and bases; records are still live in the system and can be opened with their audit trail (module 50) and signatures (module 49).
5. If a worker was restarted mid-build, the daily check marks the archive Failed and QA requests it again.

Cross-module data: Record Archive (new), File (private, attached, protected), Notification Log (background verification result), Error Log (failures), Log Settings (Activity Log row), indexes on every archived table.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocType | `Record Archive` (`ARC-.YYYY.-.####`): **Records Changed From** / **To** (Date, required), **Status** (Queued / Running / Completed / Failed, read-only, default Queued), **Records Archived** (Int), **Completed At**, **Archive File** (Attach), **Archive Fingerprint (SHA-256)**, **Error**; permissions Quality Manager and System Manager: read, write, create, report, export, print (write only for the new form; sealed by `validate`) |
| Controller | `record_archive.py`: `validate` (sealed after first save; period in order and not in the future), `after_insert` (enqueue `build_archive`, queue `long`, timeout 6 h, after commit), `on_trash` (refused); `record_archive.js` **Verify** button when Completed |
| Archive service | `compliance/archive.py`: `build_archive`, `fail_stalled_archives` (daily), `ensure_indexes` (migrate), `write_archive`, `file_name`, `write_entry`, `archived_doctypes`, `changed_records` (paged by `(modified, name)`, 500), `record_file`, `deleted_record_file`, `history`, `deletions` (paged by `(event_at, name)`), `login_history` (paged by `(creation, name)`), `verify` (POST, whitelisted), `verify_in_background`, `archive_file_path`, `problems_in`, `entry_problems`, `file_digests` (SHA-256 + MD5 in 1 MB chunks), `as_json`; constants `PAGE_SIZE`, `INLINE_VERIFY_BYTES` (256 MB), `STALLED_AFTER_HOURS` (12), `NAME_LENGTH` (150), `RESERVED_FILE_NAMES`, `UNREADABLE` |
| Retention | `compliance/retention.py`: `RETENTION_SCHEDULE`, `retention_years`, `scheduled_doctypes`, `must_be_scheduled`, `login_history_days` (= years × 366), `protect_login_history`, `apply_retention`; `LOGIN_HISTORY = "Activity Log"` |
| Report | `compliance/report/retention_schedule/retention_schedule.py` `execute` (Script Report, ref DocType Audit Event, roles Quality Manager, System Manager) |
| Evidence file | `compliance/evidence_files.py`: `EVIDENCE` incl. `("Record Archive", "archive_file", {})`, `is_evidence`, `has_permission` (File; refuses write/delete), `protect` (File before_validate; `IDENTITY` fields) |
| Hooks (`hooks.py`) | `Log Settings.validate` → `retention.protect_login_history`; `File.before_validate` → `evidence_files.protect`; `has_permission["File"]` → `evidence_files.has_permission`; `scheduler_events.daily` → `archive.fail_stalled_archives` |
| Setup | `setup/install.py`: `apply_retention()`, `apply_archive_indexes()` (= `archive.ensure_indexes`: `(modified, name)` on each archived table, `(creation, name)` on Activity Log, `(action, event_at)` on Audit Event) |
| API routes | `POST /api/method/meddevice_suite.compliance.archive.verify` (`archive`); `/api/resource/Record Archive` (create/read under the DocType permissions; update/delete refused); the ZIP at `/private/files/ARC-….zip` (private, session required) |
| Desk | **Compliance** area (`workspace_sidebar/compliance.json`: Record Archive, Retention Schedule; visible to Quality Manager and System Manager, `setup/desk.py`); routes `/desk/record-archive`, `/desk/record-archive/new`, `/desk/query-report/Retention Schedule`; **System › Log Settings**, **Users › Activity Log** (System Manager) |
| Fingerprints | whole file: SHA-256 of the ZIP bytes (`archive_hash`); per entry: SHA-256 of each record file (index line), of `index.csv` and `logins.csv` (manifest); verification recomputes all and lists extras; all hashing streams in 1 MB chunks |
| Data flow | form Save → `validate` → insert → commit → worker `build_archive` → `db_set(Running)` + commit → `write_archive` (ZIP streamed, spooled index) → digests → File registered in place → `db_set(Completed…)`; Verify → `verify` → inline or background `problems_in` → message / Notification Log |

## 7. Defects, edge cases and risks

**Confirmed and fixed (git history, with tests):** Frappe read the whole ZIP into memory, refused it above the upload limit and wrote a second copy (`copy_from_existing_file`, `9f7a6efd`); a stopped worker left an archive *Running* forever (daily stall check); a failed build left a partial ZIP (now unlinked); a damaged ZIP crashed verification (now *cannot be read*); record names with `/` or reserved Windows names produced unopenable files (`file_name`); very long names exceeded unzip limits (shortened with a fingerprint); QA saw no period fields on a new archive because the role lacked write (`7db7c9a2`); Frappe's 90-day log clean-up was put back after the row was removed (`apply_retention` re-adds it, `protect_login_history` refuses removal).

**Potential:**
- The built-in Administrator account bypasses the File protection (Frappe grants it everything before app hooks); mitigated by URS-CMP-034 (administration separated) and by the archive fingerprint on the record. Not a defect of this module.
- The archive selects records by their last change; a record changed in two periods appears only in the later one (its full history is in that file). An auditor reconstructing a period must look at later archives too (documented in 2.7).
- `login_history_days` is 3660; Frappe's clean-up compares `creation` with days, so entries are kept at least ten calendar years plus leap days. A future Frappe that changes Log Settings' schema would need `apply_retention` revisited (rules.md section 8).
- Background verification notifies only the requester (Notification Log); the result is not stored on the archive record, so a later reviewer must verify again.
- Disk space: every archive is a full copy of the period's records; nothing removes old archives (by design). Monitor `private/files`.
- The ZIP is downloadable by anyone with read on Record Archive (QA, System Manager) through `/private/files/…`; Frappe checks the File's attachment permission.

**Unverified in this review:** archive of a period with millions of records (paging is tested with `PAGE_SIZE` = 2 only); real worker restart (test simulates by patching `build_archive`); download and unzip on Windows (file-name rules tested server-side only); whether QA may open the DocType link in the report (RA-01 step 2).

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (local test site; CI = GitHub Actions on a fresh site):

| Check | Scope | Result |
| --- | --- | --- |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed |
| Module tests inside it | `compliance/test_retention.py`: TestRetentionSchedule 3, TestRetentionScheduleReport 2, TestLoginHistoryIsKept 6; `compliance/test_archive.py`: TestArchiveContent 5, TestArchiveIntegrity 6, TestArchiveRequests 4, TestArchiveAtScale 6, TestArchiveIsProtectedAndRecoverable 6 | passed (part of the suite above) |
| Browser suite, retries off | `run-ui-tests … --config retries=0`; module 54 titles: `record_archive.js` "[URS-CMP-025] lets QA request an archive from its form", "[URS-CMP-025] [URS-CMP-026] builds an archive in the background and verifies it" (run as Administrator; the worker builds the archive, the form shows Completed, Verify shows *Archive verified*) | 59 of 59 passed |
| Semgrep (Frappe rules) | app | 0 findings |
| pre-commit, traceability | app | pass |
| GitHub CI | current branch | in progress |
| Manual browser tests RA-01…RA-08 | — | **not executed** (no manual tester session); the automated browser test covers RA-03 steps 1, 4 and RA-04 step 1; the screen sweep (`screen_sweep.js`) opens the Record Archive list and the Retention Schedule report as QA Manager and System Administrator |

Untested: RA-05 and RA-07 on a real site (server tests cover the logic), RA-03 step 5 on the tester's computer, RA-01 step 2, scale beyond the test's paging.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Every regulated type, evidence and login history scheduled once, ≥ 10 years, with basis; report for QA; others refused | Verified (automated; report opened by the screen sweep) |
| Login history kept 3660 days; cannot be shortened or removed; restored on migrate; audit trail and signatures never cleared | Verified (automated) |
| Archive of the period's records with history and signatures, deletions, login history, manifest; records stay | Verified (automated + browser request/completion) |
| Whole-file and per-file SHA-256; changed, removed, added, unreadable, missing files detected; large archives verified in the background | Verified (automated + browser Verify on an intact archive) |
| Archive record and file never changed or deleted (QA, System Administrator) | Verified (automated) |
| Failed and stalled archives marked Failed with the reason, no partial file | Verified (automated) |
| Manual RA-01…RA-08 | **Not tested** |
| CI green for the final commit | **Blocked** (in progress) |
| Client confirmation: JSON/CSV copies acceptable for inspection; off-system storage SOP; archive frequency | **Open** |
