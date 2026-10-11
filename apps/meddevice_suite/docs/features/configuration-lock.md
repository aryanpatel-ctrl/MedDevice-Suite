# Configuration lock (module 47) — feature document and browser test guide

Requirement: **URS-CMP-038** (`meddevice_suite/qa/requirements.toml`). Builds on URS-CMP-015 (permission matrix in code), URS-CMP-007/013 (audit trail and its scope), URS-CMP-022 and URS-CMP-029 (workflows and alerts declared in code).
Code: `setup/roles.py` (`CONFIGURATION_PERMISSIONS`, `CONFIGURATION`, synced by `apply_roles`), `compliance/audit_scope.py` (`REGULATED_DOCTYPES`).
DFD trace: 9F Audit Trail & Security (F2 role and permission, F3 action and data logging); D1 System Foundation (Validation & System Security); master plan section 10 ("the audit trail covers masters, records and configuration").

## 1. Feature overview

**Purpose.** What the validated system does is decided by its released code, not by edits on the live site.

**Why it matters.** A field, a script or a form change added on a site changes validated behaviour with no change control and, until now, no record (21 CFR 11.10(a), (e), (k); the engineering rules' "configuration is code").

| Job profile | Here |
| --- | --- |
| System Administrator | May look at custom fields, form changes and scripts; may not create, change or delete them; keeps the settings pages, whose changes are recorded |
| Everyone else | No access to configuration |
| Releases of this app | Bring configuration, applied on install and every migrate |

## 2. Detailed functional explanation

- **Closed to everyone on a site:** Custom Field, Property Setter, Client Script, Server Script (read only for the System Administrator), and the **Customize Form** screen (no access at all; Frappe checks read before running its save). Declared in code and re-applied on every migrate, so a permission added by hand through Role Permissions does not last.
- **The app's own configuration** is unaffected: install and migrate create fields and form changes through code that does not depend on these permissions.
- **Recorded in the audit trail** (who, when, old and new values): those four, print formats, workflows, alerts (notifications), and the Manufacturing, Buying, Selling and Print Settings, whatever made the change (a migrate, code, or the Administrator account).
- **Already in place:** workflows and alerts cannot be changed on a site (URS-CMP-022, URS-CMP-029); standard print formats cannot be edited outside developer mode (Frappe); server scripts are off unless a site's configuration turns them on.
- **Not closed:** the Administrator account holds every permission in Frappe; keeping it out of daily use is the next step (master plan section 10, V9).

## 3. Requirements and acceptance criteria

> **URS-CMP-038** Nobody changes the configuration of a site by hand: no role, the system administrator included, may create, change or delete custom fields, property setters, form customisations or client and server scripts, which change only with a release of this app, applied on install and migrate; a permission added by hand to change them does not last; and every change made to the configuration of the site (these records, print formats, workflows, alerts, and the manufacturing, buying, selling and print settings) is recorded in the audit trail with its old and new values.

| # | Criterion | Verification (`setup/test_configuration.py`) |
| --- | --- | --- |
| C1 | No role creates, changes or deletes them; the administrator may look | `test_no_role_creates_changes_or_deletes_fields_form_changes_or_scripts`, `test_a_field_added_by_hand_is_refused` |
| C2 | Customize Form closed | `test_the_customize_form_screen_is_closed` |
| C3 | The app's own configuration still applied | `test_the_apps_own_configuration_is_still_applied_on_migrate` |
| C4 | A hand-added permission does not last | `test_a_permission_added_by_hand_does_not_last` |
| C5 | Configuration changes audited with old and new values | `TestConfigurationIsAudited.*`; the matrix test in `setup/test_roles.py` |

## 4. Step-by-step browser testing guide

### CL-01 Closed
Role: System Administrator. Open **Customize Form**: refused. **Custom Field › New**: no New button / refused on save.

### CL-02 Recorded
Role: System Administrator. Change a value in **Manufacturing Settings**, save. **Audit Trail** for Manufacturing Settings: the change with old and new value.

## 5. End-to-end business workflow
A configuration need → change request → change in this app's code → release → migrate applies it (and records it) → validated as part of the release.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Permissions | `roles.CONFIGURATION_PERMISSIONS` (Customize Form: none; the four records: System Manager read) synced as Custom DocPerm by `apply_roles` |
| Audit | `audit_scope.REGULATED_DOCTYPES` configuration block; `roles.CONFIGURATION` keeps them outside the business matrix |
| Retention | `retention.RETENTION_SCHEDULE` "System configuration records": 10 years (21 CFR 11.10(d), (e), (g)) |

## 7. Defects, edge cases and risks
- An ERPNext settings page that writes a form change as a side effect (some naming options do) now saves it through ERPNext's own code, recorded in the audit trail; it is still configuration and belongs in a release.
- India Compliance features that add fields when switched on are switched on by a release, not on the site.
- The Administrator account is not limited by permissions; its daily use is closed separately (V9).

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Configuration closed to every role | Verified (automated) |
| The app's configuration still applied | Verified (automated) |
| Configuration changes audited | Verified (automated) |
| Manual CL-01, CL-02 | **Not tested** |
