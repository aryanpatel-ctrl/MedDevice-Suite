# Quality Settings — feature document and browser test guide

Requirements: **URS-PLT-012** (alert lead times, default effectiveness check), **URS-PLT-013** (login and signature lockout), the password and session values of **URS-CMP-037** (see `access-policy.md`). Also touched: URS-CMP-001 (lockout), URS-CMP-027 (lot expiry alert), URS-PUR-001 (supplier certificate alert), the document review and change-control alerts (module 30, 36).
Code: `meddevice_suite/compliance/quality_settings.py`, `compliance/doctype/quality_settings/`, readers in `compliance/alerts.py`, `compliance/esign.py`, `master_data/supplier_approval.py`, `qms/document_control.py`, `qms/change_control.py`, `qms/doctype/change_request/change_request.py`.
State on 2026-10-11: built; tests written from the requirement matrix before the code; results in section 8.

## 1. Feature overview

**Purpose.** The numbers that tune the system — how many days ahead each alert warns, how long after a change its effectiveness is checked, how many wrong passwords lock an account and for how long — are on one screen QA controls, instead of in the code. Each value has fixed bounds. A change is a change to the validated system, so it is saved only under an approved change request that lists Quality Settings; the version is raised and the audit trail keeps the old and new values.

**Why it matters.** The client's QA can tune the system to their practice without a software release (the user's instruction of 2026-10-11), while ISO 13485 4.1.6 and 21 CFR 11.10(a) still hold: the validated state changes only under change control.

**What is not a setting** (decided 2026-10-11, kept in code): the rules themselves, signature meanings, lot statuses, item types and what they require, roles and permissions, workflows, who receives an alert (people are chosen by the roles they hold; the alerts are protected by URS-CMP-029), retention periods, device classes, GTIN rules.

| Job profile | Here |
| --- | --- |
| QA Manager | Reads and changes the settings, under an approved change request |
| System Administrator | Reads only |
| Everyone else | No access; they see only the effect (when alerts arrive, the lockout) |

## 2. Detailed functional explanation

| Setting | Default | Bounds | Used by |
| --- | --- | --- | --- |
| Lot Expiry Alert (days) | 30 | 1–365 | daily lot expiry alert to Stores and QA (URS-CMP-027) |
| Supplier Certificate Alert (days) | 30 | 1–365 | daily alert to QA and Purchase before a supplier's certificate expires (module 4) |
| Document Review Alert (days) | 30 | 1–365 | daily alert to the document owner and QA before a review date (module 30) |
| Change Action Due Alert (days) | 7 | 1–90 | daily alert to an action's owner before it is due (module 36) |
| Effectiveness Check After (days) | 90 | 0–730 | the default given to a new change request; the assessor may set another |
| Wrong Passwords Before Lockout | 5 | 3–10 | login and electronic signature (21 CFR 11.300(d)) |
| Locked For (minutes) | 15 | 5–1440 | how long a locked account stays locked |
| Minimum Password Strength | 3 | 3–4 | how hard a new password must be to guess (URS-CMP-037) |
| Password Valid For (days) | 90 | 30–365 | after this, a new password is required at sign-in (URS-CMP-037) |
| Sign Out After Inactivity (minutes) | 30 | 5–480 | a session left unused this long ends (URS-CMP-037) |

- Until QA saves a value, its default applies (the values decided with the client on 2026-10-07/08 and 2026-10-10).
- **Change control:** saving a changed value needs **Change Request** = an approved request listing *Quality Settings* (the lookup offers only those). The request is recorded in **Last Changed Under**, **Version** goes up by one, **Effective From** becomes today, and the Change Request field is cleared for the next change. These three are set by the rule only.
- **One change, one request:** a request that already changed the settings (the one recorded now, or an earlier one) is refused; each change is raised and approved on its own.
- **Lockout, passwords and sessions:** on save, and on every migrate, the site's System Settings are set to the lockout values and to the password strength, password validity and inactivity sign-out (URS-CMP-037, `access-policy.md`); changing any of them in System Settings by hand is refused at once (*… is set by the access policy: in Quality Settings, under change control*).
- **Naming the settings on a change request:** every job that raises change requests may pick *Quality Settings* (and *Controlled Document*) as a record type without being able to read it; the record is filled in by itself, because a settings record has no list to look it up in. Before 2026-10-11 nobody could pick either type on screen.
- **Alerts** read the setting each time they run: a change applies from the next daily run.

## 3. Requirements and acceptance criteria

> **URS-PLT-012** The lead times of the alerts (lot expiry, supplier certificate expiry, document review, change-request action due) and the default effectiveness check of a change are settings QA changes on screen, each within fixed bounds, only under an approved change request that lists the settings; the change is recorded with its request and a new version and applies from the next alert run; until QA changes a value the decided one applies (30, 30, 30, 7 and 90 days).
> **URS-PLT-013** The number of consecutive wrong passwords that locks an account (3 to 10) and how long it stays locked (5 to 1440 minutes) are settings QA changes on screen under an approved change request; login and signing follow them at once and again on every migrate, so a hand edit of the site's settings does not last.

| # | Criterion | Verification (`compliance/test_quality_settings.py` unless noted) |
| --- | --- | --- |
| C1 | Defaults apply until changed | `test_until_qa_changes_them_the_decided_values_apply` |
| C2 | Bounds, each setting, both sides | `test_each_value_stays_within_its_bounds` |
| C3 | Only under an approved request listing the settings | `test_a_change_needs_an_approved_change_request_listing_the_settings` |
| C4 | Recorded: request, version, date; request named afresh | `test_a_change_is_recorded_with_its_request_and_a_new_version` |
| C5 | Record of changes set by the rule only | `test_the_record_of_changes_is_set_by_the_rule_only` |
| C6 | Audit trail keeps old/new values and the request | `test_the_audit_trail_keeps_the_old_and_new_values_and_the_request` |
| C7 | Only QA changes; administrator reads | `test_only_qa_changes_them_and_the_administrator_reads_them` |
| C8 | Each alert follows its setting | `test_alerts.py::test_the_warning_follows_quality_settings`, `test_supplier_approval.py::test_the_certificate_alert_follows_quality_settings`, `test_document_control.py::test_the_review_alert_follows_quality_settings`, `test_change_control.py::test_the_action_alert_follows_quality_settings` |
| C9 | New change request takes the default check | `test_change_control.py::test_a_new_request_takes_its_effectiveness_check_from_quality_settings` |
| C10 | Lockout follows at once and on migrate | `TestLockout.*`, `test_esign.py::test_the_site_locks_an_account_after_wrong_passwords` |

## 4. Step-by-step browser testing guide

### QS-01 QA changes a lead time under change control
Role: Production Supervisor (raise), QA Manager (approve, change).
1. Raise a change request *Shorten the lot expiry warning*, type *System*, Record Type **Quality Settings** / Record **Quality Settings**; QA assesses and approves it (module 36).
2. QA: **Quality › Quality Settings**. Expected: the seven values with their defaults, Version 1.
3. Set **Lot Expiry Alert** to 20, leave Change Request empty, **Save**. Expected: refused — *Quality Settings change only under an approved change request that lists them.*
4. Pick the approved request in **Change Request**, **Save**. Expected: saved; Version 2; Last Changed Under = the request; Effective From = today; Change Request empty.
5. Set it to 400 (with another approved request). Expected: refused — *Lot Expiry Alert is between 1 and 365.*

### QS-02 Lockout
1. Under an approved request set **Wrong Passwords Before Lockout** = 3. Expected: System Settings › *Allow Consecutive Login Attempts* = 3.
2. Sign in as a test user with a wrong password three times. Expected: the account is locked for the set minutes; signing is refused too.

### QS-03 Who may
System Administrator opens Quality Settings: read only. Stores: no access.

### Regression
The alert tests of modules 4, 30, 36 and 51 and the journey (`standard_flow.js`) run unchanged.

## 5. End-to-end business workflow
Change request (System) listing Quality Settings → assessed and approved → QA changes the value, naming the request → version 2, audit event → the next daily alert run uses it; or, for lockout, the site's login and signing at once.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocType | `Quality Settings` (single, module Compliance); permissions declared in `setup/roles.py` `PERMISSIONS` and reset on every migrate: Quality Manager select/read/write, System Manager read, every desk user select only |
| Service | `compliance/quality_settings.py`: `SETTINGS` (default, bounds), `raw`, `stored`, `value`, `validate`, `as_text`, `on_update` |
| Readers | `alerts.send_expiry_alerts`, `supplier_approval.send_certificate_alerts`, `document_control.send_review_alerts`, `change_control.send_change_alerts`, `ChangeRequest.before_insert`, `access_policy.apply_access_policy` (through `esign.apply_lockout` too) |
| Change control | `change_control.AFFECTED_TYPES` and the action's target and result lists include *Quality Settings* |
| Desk | `quality_settings.js` (change request lookup), Quality workspace sidebar |

## 7. Defects, edge cases and risks

**Found by the independent review of 2026-10-11 and fixed, each with a test:** nobody could pick Quality Settings, nor a controlled document, as a record type on a change request on screen (the form offers only types a person may select; the server tests built requests in code, so did not see it) — select added, single records named by their type, and a test of each job's selectable types; one approved request could cover any number of later changes — each change now needs its own; the System Administrator could grant itself write through Role Permissions and keep it — the permissions are now in the synced matrix; a hand edit of the lockout in System Settings lasted until the next migrate — refused at once; the lockout was not tested in use — three wrong passwords now lock an account in a test; version 0 could be stored; two saves at once could both write the same version — the save takes a lock.

- A single's unset values read as 0 or 0001-01-01 in Frappe; the service reads the stored values directly so "never set" is told apart (found during the build, covered by the tests).
- The lockout is now the setting's value, whatever was set by hand on System Settings (previously a stricter hand value stood): one source of truth.
- A change applies from the next daily run of an alert, not retroactively.

## 8. Test results and verification summary
See the pull request for the runs of this branch (module tests, full suite, mutation check, fresh site, review).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Defaults, bounds, change control, record of changes, audit | Verified (automated) |
| Alerts and default check follow their settings | Verified (automated) |
| Lockout follows at once and on migrate | Verified (automated) |
| Manual QS-01…QS-03 | **Not tested** |
