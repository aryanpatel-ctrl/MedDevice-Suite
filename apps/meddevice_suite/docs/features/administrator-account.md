# The Administrator account (module 47) — feature document and browser test guide

Requirement: **URS-CMP-039** (`meddevice_suite/qa/requirements.toml`). Builds on URS-CMP-034 (whoever administers holds no business role), URS-CMP-037 (access policy), URS-CMP-038 (configuration lock).
Code: `meddevice_suite/compliance/administrator.py`; hooks `on_login` and `auth_hooks` in `meddevice_suite/hooks.py`.
DFD trace: 9F Audit Trail & Security (F1 user management); master plan section 10 ("the built-in super-user disabled for daily use"); go-live check V9 of the 2026-10-07 review.

## 1. Feature overview

**Purpose.** Every action on a working site is a named person's.

**Why it matters.** Frappe's built-in Administrator belongs to nobody in particular and holds every permission, past the permission matrix and the configuration lock. 21 CFR 11.10(d) limits access to authorised individuals and 11.100(a) makes each signature and action one person's.

| Who | Here |
| --- | --- |
| System Administrator (named account) | Administers the site day to day |
| IT (server access) | Opens a time-limited maintenance window when Administrator itself is needed |
| Administrator | Not used on a working site |

## 2. Detailed functional explanation

- **Once the site is set up** (its setup wizard complete), Administrator is refused at sign-in, on the login page and through the API's login, before any session is made: *The Administrator account is not used on this site. Sign in with your own System Administrator account; for maintenance, IT opens a time-limited window on the server.*
- **A session or API key Administrator already holds** is refused at its next request.
- **Maintenance window:** IT sets `"administrator_login_until": "YYYY-MM-DD HH:MM"` in the site's configuration on the server. Administrator may sign in until that time; the window closes by itself. A value that cannot be read counts as closed. Every sign-in is in the login history, kept for its retention period (URS-CMP-024).
- **Kept** on development sites (developer mode), test sites, the pilot, and a site still in its setup wizard. Each flag must be set in that site's own configuration, not the bench-wide one, so it can never reach a working site by accident.
- **Not covered:** commands run on the server (`bench`), which need server access and are outside the application.

## 3. Requirements and acceptance criteria

> **URS-CMP-039** Once a working site is set up, the built-in Administrator account does not sign in, by the login page or the API, and a session it already holds is refused at its next request, so that every action on the site is a named person's; for maintenance, a window opened on the server for a stated time lets it in until that time; development, test and pilot sites, and a site whose setup is not complete, keep it.

| # | Criterion | Verification (`compliance/test_administrator.py`) |
| --- | --- | --- |
| C1 | Refused at sign-in; named accounts unaffected | `test_administrator_does_not_sign_in`, `test_a_named_account_signs_in_as_before` |
| C2 | Refused on every request, session or API key | `test_a_session_or_api_key_of_administrator_is_refused_on_every_request` |
| C3 | Maintenance window until its time; unreadable is closed | `test_a_maintenance_window_opens_it_until_its_time` |
| C4 | Setup wizard; development, test and pilot sites | `test_the_setup_wizard_runs_as_administrator`, `test_development_test_and_pilot_sites_keep_it` |
| C5 | Through Frappe's own login trigger and request authentication | `TestFrappeRunsTheChecks.*` |

## 4. Step-by-step browser testing guide

### AA-01 On a working site (staging, before go-live)
Sign in as Administrator. Expected: refused with the message above. Sign in as the named System Administrator: works.

### AA-02 Maintenance window
On the server, set `administrator_login_until` to one hour ahead; sign in as Administrator: works. After the time: refused.

## 5. End-to-end business workflow
Go-live: named System Administrator accounts made → setup complete → Administrator closed → maintenance needing it: IT opens a dated window, records why in the IT change record, the window closes itself.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Service | `administrator.administrator_allowed`, `maintenance_window_open`, `refuse_administrator_login` (`on_login`), `refuse_administrator_requests` (`auth_hooks`) |
| Site configuration | `administrator_login_until`; `developer_mode`, `allow_tests`, `pilot_site` read through `setup.site_flags.site_flag` |

## 7. Defects, edge cases and risks
- A working site with no named System Administrator account would have no administrator once set up: make the named accounts before completing setup (go-live checklist).
- The window is set on the server; who opens it, and why, belongs in IT's own change record.
- Verified through Frappe's login trigger and request authentication with the site flags of a working site; a sign-in on a real staging site (AA-01) remains for the go-live checks.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Refused at sign-in and on every request | Verified (automated) |
| Maintenance window | Verified (automated) |
| Development, test, pilot and setup kept | Verified (automated) |
| AA-01, AA-02 on a staging site | **Not tested** (needs the hosting server) |
