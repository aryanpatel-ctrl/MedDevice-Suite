# Important files repository (module 61) — feature document and browser test guide

Requirements: **URS-PLT-018** (categories and access) and **URS-PLT-019** (versions, evidence, validity) (`meddevice_suite/qa/requirements.toml`). Builds on document control's file fingerprint (module 30), evidence files (module 54), reminders (module 60) and Quality Settings.
Code: `meddevice_suite/files_repository/files.py`; records *File Category* (tree) with *File Category Role*, *Important File* with *Important File Version* (`files_repository/doctype/`); hooks (list permissions) in `meddevice_suite/hooks.py`; evidence in `compliance/evidence_files.py`; reminder category *File Validity* in `work_management/reminders.py`; Quality Settings `file_validity_warning_days`.
DFD trace: 9E Important Files Repository (E1 create category, E2 create sub group, E3 upload document, E4 version control, E5 access control, E6 search and retrieve); data store D9 Documents; master plan section 8.

## 1. Feature overview

**Purpose.** Licences, regulatory submissions, certificates and audit reports — files that are not controlled SOPs — are kept in one place, each with every version it ever had, seen only by the jobs that need them, and renewed before they lapse.

**Why it matters.** A licence or certificate saved in a shared folder can be overwritten, lost or allowed to lapse unnoticed; an auditor asks for the version in force on a date and who placed it there (ISO 13485 4.2.4, 4.2.5; 21 CFR 11.10(c), (e)).

| Job profile | Here |
| --- | --- |
| QA Manager | Keeps the categories and who reads and uploads each; sees and may upload to every category |
| The jobs a category names as uploaders | Add files and new versions; archive files |
| The jobs a category names as readers | Open its files |
| System Administrator | May look at every file; uploads nothing |

## 2. Detailed functional explanation

- **Categories** (*File Category*, a tree QA keeps): a group holds sub-groups; files are kept in a category that is not a group. Each category names the jobs that read its files and the jobs that upload them; a sub-group naming none takes its nearest group's. A category holding files is not made a group or deleted.
- **A file** (*Important File*): title, category, what it is, optionally the record it concerns. **New File** (with **Valid Until** and **What Changed**) is saved as the next version: a private file, fingerprinted (SHA-256) as it is added, with who added it and when. The first version needs no note; every later one says what changed. **Current Version**, **Current File** and **Valid Until** follow from the latest version.
- **Nothing is replaced or deleted:** earlier versions never change, their files are evidence nobody may change or delete, and a file is never deleted. A file no longer needed is **archived** (by its category's uploaders or QA, with a reason) and then does not change. What "does not change" means is checked on the stored values, compared by field type (`compliance.audit.same`): the form sends dates, times and numbers as text, so before 2026-10-15 saving a file's form with its versions untouched was refused as a change to them (*Earlier versions are kept as they were.*); the same for an evidence File's check box (`evidence_files.unchanged`).
- **Who sees what:** the list and each file open only for the jobs the category lets read (its uploaders read too); QA and the System Administrator see all.
- **Validity:** a current version with **Valid Until** within the days QA sets (*File Validity Alert*, 60 until changed) is raised as a reminder for the category's uploading jobs, overdue and escalated like any reminder, until a new version renews it or the file is archived.
- **Search and retrieve:** the list filters by category and status and searches title, category and description.

## 3. Requirements and acceptance criteria

> **URS-PLT-018** QA keeps the important files repository's categories and sub-groups and, for each, the jobs that read its files and the jobs that upload them, a sub-group naming none taking its group's; files are kept in a category, not in a group, and a category holding files is neither made a group nor deleted; people see and open only the files of the categories their jobs read, QA and the system administrator all, and only the category's uploading jobs or QA add to or archive its files.

> **URS-PLT-019** A file in the repository is a record of its versions: each upload is the next version, a private file fingerprinted as it is added, with who added it, when, what changed (from the second version) and, where it has one, until when it is valid; earlier versions are never changed or removed and their files are evidence that cannot be changed or deleted; a file is never deleted, only archived with a reason, and an archived file does not change; a current version's validity is tracked as a reminder by the category's uploading jobs, the days before it ends set by QA in Quality Settings (60 until changed), until a new version renews it or it is archived.

| # | Criterion | Verification (`files_repository/test_files.py`) |
| --- | --- | --- |
| C1 | QA keeps categories; business jobs only; inheritance | `test_qa_keeps_the_categories`, `test_a_sub_group_takes_its_groups_jobs_unless_it_names_its_own` |
| C2 | Visibility by job; uploads and archiving by uploaders or QA only | `test_people_see_only_the_files_their_jobs_read`, `test_only_the_uploading_jobs_or_qa_add_to_a_category`, `test_archived_with_a_reason_never_deleted_and_then_unchanged` |
| C3 | Files in a category, not a group; a category holding files kept | `test_files_are_kept_in_a_category_not_a_group` |
| C4 | Versions: fingerprinted, attributed, next version with a note, private only | `TestVersions.*` |
| C5 | Earlier versions and what follows from them never changed; evidence not deleted; values compared as stored, so a form save that sends dates and numbers as text is not refused (2026-10-15) | `test_earlier_versions_and_what_follows_from_them_never_change_by_hand`, `test_a_versions_file_is_evidence_nobody_changes_or_deletes`, `test_a_form_save_sending_dates_and_numbers_as_text_changes_nothing`, `test_an_evidence_file_saved_with_its_values_as_text_is_not_refused` (2026-10-15, not yet run) |
| C6 | Validity tracked by uploaders within QA's days, completed by renewal or archiving | `TestValidity.*` |

## 4. Step-by-step browser testing guide

### FR-01 Categories
As QA: **File Category › New**: *Licences* (group; readers QA Manager, Stores; uploaders Purchase), then *CDSCO* within it (no jobs). Expected: CDSCO's files are uploaded by Purchase and read by Stores.

### FR-02 Upload and new version
As Purchase: **Important File › New** in CDSCO, attach a private PDF, Valid Until in 30 days, save: version 1. Attach a new file with What Changed, save: version 2; version 1 is still listed.

### FR-03 Access
As Sales: the file is not in the list. As Stores: opens it, cannot save. As QA: opens and may upload.

### FR-04 Renewal
After the daily run: **Reminder** *… is valid until …* for Purchase. Upload a renewed version valid next year: the reminder completes on the next run.

## 5. End-to-end business workflow
QA sets up categories → the responsible job uploads each licence or certificate → it is renewed as a new version before it lapses (reminded and escalated) → superseded versions stay as evidence → a surrendered licence is archived with a reason.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `files.access` (the tree's jobs in two reads), `may`, `check_category`, `check_file`, `add_version`, `archive` (whitelisted), `query_conditions`, `has_permission` |
| Records | *File Category* (tree, `nsm_parent_field` parent_file_category), *File Category Role*, *Important File*, *Important File Version* |
| Evidence | `evidence_files.EVIDENCE` ("Important File Version", "file") |
| Settings | Quality Settings `file_validity_warning_days` (60, 1–365) |
| Retention | Quality system records, 10 years |

## 7. Defects, edge cases and risks
- Regulatory licences (module 41) build on this repository: a licence's file and validity live here.
- A category's jobs changed later change who sees its files at once; that change is audited.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Categories and access by job | Verified (automated) |
| Versions, fingerprint, evidence | Verified (automated) |
| Validity reminders | Verified (automated) |
| Manual FR-01…FR-04 | **Not tested** |
