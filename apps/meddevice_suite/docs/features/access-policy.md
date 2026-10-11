# Access policy (module 47) — feature document and browser test guide

Requirement: **URS-CMP-037** (`meddevice_suite/qa/requirements.toml`). Builds on URS-PLT-013 (lockout) and URS-PLT-012 (Quality Settings under change control).
Code: `meddevice_suite/compliance/access_policy.py`, the values in `compliance/quality_settings.py` (`SETTINGS`) and the Quality Settings form, hook System Settings `validate`, applied by `setup/install.apply_configuration` and on every save of Quality Settings.
DFD trace: 9F Audit Trail & Security (F1 user management); D1 System Foundation (Validation & System Security); master plan section 10 ("password policy, session timeout, account lockout").

## 1. Feature overview

**Purpose.** Everyone signs in the same controlled way: their own user ID and a password strong enough, replaced regularly, and a session that ends when left unused.

**Why it matters.** 21 CFR 11.10(d) limits access to authorised individuals; 11.300 asks that passwords are unique, aged and protected against misuse; 11.100 that a signature belongs to one person. Frappe's defaults on a new site are a 170-hour session, no password expiry, a medium strength, and sign-in by a link mailed to the user, which lets anyone reading that mailbox in without the password.

| Job profile | Here |
| --- | --- |
| QA Manager | Sets the strength, validity and inactivity values in Quality Settings under an approved change request |
| System Administrator | Cannot loosen them in System Settings |
| Everyone | Signs in under them |

## 2. Detailed functional explanation

- **Fixed rules** (code, not adjustable): password policy on; no sign-in by emailed link; a password reset signs the person out everywhere; the user ID is the email address (no sign-in by user name or mobile number).
- **QA's values** (Quality Settings, *Passwords and sessions*): Minimum Password Strength 3 (3–4; Frappe's zxcvbn score, 3 = safely unguessable), Password Valid For 90 days (30–365), Sign Out After Inactivity 30 minutes (5–480). With the lockout (URS-PLT-013) they are written to System Settings on install, on every migrate and as soon as QA saves them.
- **Hand edits** of any of these in System Settings are refused (*… is set by the access policy: in Quality Settings, under change control*); other System Settings save as before; a change made past the form is undone at the next migrate.
- **What Frappe then does:** refuses a weak new password (user form, password change, reset page); asks for a new password at sign-in once the validity has passed since the last change; ends a session after the inactivity time; sends no sign-in link.
- **Not built:** a password history refusing reuse of earlier passwords (decided 2026-10-11: not required by 11.300 given the rules above; Frappe has no hook for it, so it would replace Frappe's own password change). Two-factor sign-in is not in the requirement.

## 3. Requirements and acceptance criteria

> **URS-CMP-037** A person signs in only with their own user ID (their email address) and password, never by a link mailed to them; a password meets the strength QA sets (strong or very strong) and must be replaced after the number of days QA sets (30 to 365, 90 until QA changes it); a session ends after the minutes of inactivity QA sets (5 to 480, 30 until QA changes it); these values change only in Quality Settings under an approved change request, and a change to them, or to the account lockout, made in the site's System Settings is refused.

| # | Criterion | Verification (`compliance/test_access_policy.py`) |
| --- | --- | --- |
| C1 | Decided values and fixed rules on the site | `test_until_qa_changes_them_the_decided_rules_apply`, `test_the_values_written_are_the_ones_the_policy_decides` |
| C2 | QA changes them under change control; the site follows at once | `test_qa_changes_them_under_change_control_and_the_site_follows_at_once`, `test_a_change_needs_an_approved_change_request` |
| C3 | Bounds keep the policy safe | `test_each_value_stays_within_bounds_that_keep_the_policy_safe` |
| C4 | Hand edits refused, and undone on migrate | `TestNoHandEdit.*` |
| C5 | Weak password refused; new password required after the validity; session expiry; no mailed link | `TestFrappeEnforcesThem.*` |

## 4. Step-by-step browser testing guide

### AP-01 The policy on a new site
Role: System Administrator. **System Settings**. Expected: Session Expiry 00:30, Force User to Reset Password 90, Minimum Password Score 3, Enable Password Policy on, Login with email link off.

### AP-02 No hand edit
Same screen: set Session Expiry 170:00, save. Expected: refused, *Session Expiry is set by the access policy*.

### AP-03 QA changes a value
Role: QA Manager. **Quality Settings**, under an approved change request listing it: Sign Out After Inactivity 60. Expected: version raised; System Settings › Session Expiry 01:00 for sessions started after.

### AP-04 Sign-in
Sign-in page shows no "Login with email link". Change a password to `stores123`: refused as too weak. Leave the desk unused past the inactivity time and click: sent to sign in again.

## 5. End-to-end business workflow
Change request listing Quality Settings → approved → QA saves the new value → System Settings follow at once → Frappe applies them at the next sign-in, password change or new session.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `access_policy.FIXED`, `system_values`, `apply_access_policy`, `guard_system_settings` |
| Values | `quality_settings.SETTINGS`: `password_strength`, `password_expiry_days`, `session_idle_minutes` |
| Hooks | System Settings `validate` → `guard_system_settings`; install and migrate → `apply_access_policy`; Quality Settings `on_update` → `apply_access_policy` |

## 7. Defects, edge cases and risks
- A session started before a change keeps the inactivity time it started with (Frappe stores it with the session); it applies to sessions started after. The first deployment applies 30 minutes to every new sign-in; people already signed in keep their old session until it ends or they sign out.
- Validity counts from the last password change Frappe recorded; a person who has not changed theirs since before the policy is asked at their next sign-in once 90 days have passed.
- Frappe skips the user form's strength check when tests run; the tests use the check the password change and the reset page call.

## 8. Test results and verification summary
See the pull request (module tests, touched modules, mutation check, fresh site).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Decided values and fixed rules applied | Verified (automated) |
| Change control and bounds | Verified (automated) |
| Hand edits refused | Verified (automated) |
| Frappe applies them at sign-in | Verified (automated, through Frappe's own checks) |
| Manual AP-01…AP-04 | **Not tested** |
