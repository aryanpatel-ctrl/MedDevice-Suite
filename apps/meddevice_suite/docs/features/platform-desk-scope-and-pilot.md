# Platform (module 0): desk scope, configuration as code, test and pilot sites, browser checks and CI gates — feature document and browser test guide

Requirements: **URS-PLT-001 … URS-PLT-009** (`meddevice_suite/qa/requirements.toml`, module 0 = platform, applies across modules). Touched by: URS-CMP-014/015 (job profiles and the permission matrix, module 47), URS-CMP-036 (lookups), URS-CMP-022/029 (workflows and alerts restored on migrate).
Code: `meddevice_suite/setup/desk.py`, `setup/sidebars.py`, `setup/modules.py`, `setup/install.py`, `setup/site_flags.py`, `qa/site_setup.py`, `qa/ui_users.py`, `qa/ui_journey.py`, `qa/traceability.py`, `qa/__init__.py`, `pilot/site.py`, `pilot/demo.py`, `pilot/samples.py`, `pilot/flow.py`, `pilot/inspection.py`; `hooks.py`; browser specs `cypress/integration/desk_scope.js`, `screen_sweep.js`, `standard_flow.js`; repository `scripts/create_pilot_site.sh`, `scripts/ci/create_test_site.sh`, `scripts/ci/start_bench.sh`, `scripts/ci-local.sh`, `scripts/verify_vendored.sh`, `.github/workflows/ci.yml`, `linter.yml`, `.pre-commit-config.yaml`; rules in `rules.md` §3–7 and the two READMEs.
State on 2026-10-10: built and in use by every other module; server and browser suites green locally on the current branch (section 8); GitHub CI for the branch still in progress when this document was written.

## 1. Feature overview

**Purpose.** The platform layer makes the system *show only what the client's requirement uses*, per job; *configures itself from code* on install and on every `bench migrate` without touching Frappe, ERPNext or India Compliance; *proves every implemented requirement with a test* through a generated traceability matrix; *tests on a disposable test site* that refuses to be anything else; gives the client a *pilot site with fictional demo data* that can never be loaded anywhere else; and *walks the standard flow and every job's screens in a real browser* on every change, with the same gates locally and in CI.

**Why it matters.** ISO 13485 4.1.6 and 21 CFR 11.10(a) require validated software with documented evidence; an unscoped desk invites work outside the validated process (and errors from records a job may not read); configuration done by hand on a site is an unvalidated change. The README's engineering rules (configuration is code, official apps read-only, every change traced) are enforced here, not only written down.

**Who uses it.** Every job (the desk they see); the System Administrator (`system.administrator@demo.example.com`, migrate and site preparation through bench commands); developers and CI (test site, gates); the client (pilot site, demo users `<profile>@demo.example.com`).

**Prerequisites.** A bench built by `scripts/bootstrap.sh`; a site with erpnext, india_compliance and meddevice_suite installed; for tests `"allow_tests": true` in the site's `site_config.json`; for the pilot `"pilot_site": 1` and `pilot_user_password` (set by `scripts/create_pilot_site.sh`).

## 2. Detailed functional explanation

### 2.1 Desk scope: areas and entries (URS-PLT-001)

`setup/desk.py` `DESKTOP_ICONS_SHOWN` lists the 26 standard desk areas the requirement uses (Framework, Accounting and India Compliance as the folders that hold them; CRM, Selling, Buying, Stock, Manufacturing, Quality, Assets, Organization; Accounts Setup, Invoicing, Banking, Taxes, Financial Reports, GST India, Income Tax India; this app's Documents and Compliance; ERPNext Settings, Users, System, Data, Email, Printing). Every other **Desktop Icon** is hidden for everyone, including one a future release adds: Projects, Website, Budget, Share Management, Subscription, My Workspaces, Payments, Subcontracting, Automation, Integrations, Support and Build are hidden on purpose. Icons belong to the owning apps and are hidden with `db.set_value` (never saved as documents, which in developer mode would export them into `apps/frappe` or `apps/erpnext`). A user's saved **Desktop Layout** is enforced too, on save (hook `Desktop Layout.validate` → `enforce_on_desktop_layout`) and on every migrate; order and arrangement stay the user's.

`setup/sidebars.py` `SIDEBAR_SCOPE` is an allowlist per area of the sidebar entries kept (e.g. Selling keeps Quotation, Sales Order, Item, Price List … and drops POS, Loyalty Program, Coupon Code, Sales Partner; Quality keeps Quality Inspection, Quality Feedback, Feedback Template, Quality Inspection Template and drops ERPNext's Non Conformance, Quality Action, Quality Goal); section headings left empty are removed. `SIDEBAR_ADDITIONS` adds entries the requirement needs that the shipped sidebar lacks: **Employee** (Organization); **Lot Release**, **Lots Awaiting Release**, **Stock by Lot Status** (Stock); **Lot Release**, **Lots Awaiting Release**, **Supplier Approval** (Quality); **Supplier Approval** (Buying). This app's own sidebars and icons (Documents, Compliance) ship as files under `meddevice_suite/workspace_sidebar/` and `desktop_icon/`.

### 2.2 Each job's desk (URS-PLT-008)

`DESK_AREAS` says which roles work in each shown area; `set_area_roles` writes the **Has Role** rows of the area's Desktop Icon and Workspace, so the desk loads and shows a job only the areas of its work, and an area's address (`/desk/<area>`) is refused to others. Areas no job uses (any Workspace not in `DESK_AREAS`) are closed to everyone but the built-in `Administrator`. Examples: Sales → CRM, Selling, Stock, Documents; Stores → Buying, Stock, Documents; Operator → Manufacturing, Documents; QA Manager → Selling, Buying, Stock, Manufacturing, Quality, Assets, Organization, Documents, Compliance; Accounts → the finance areas; System Administrator → Organization, Accounting, Accounts Setup, Compliance, Framework, ERPNext Settings, Users, System, Data, Email, Printing, Documents. **Documents** is for every desk user.

Opening a record type the job may not read as if it were a page is answered plainly: `hooks.py` overrides `frappe.desk.desk_page.getpage` with `setup.desk.getpage`, which throws *You are not permitted to open <Record Type>.* (HTTP 403) for a signed-in user; a guest gets Frappe's own behaviour; real pages load unchanged. `test_everything_in_a_jobs_areas_opens_for_that_job` holds every chart, number card, shortcut and link of each job's areas to a report or record type the job may read.

### 2.3 Configuration as code (URS-PLT-002, URS-PLT-005)

`setup/install.py` `apply_configuration()` is the single entry point, run by `after_install` and `after_migrate` (`hooks.py`). In order: `apply_desk_configuration`, `apply_sidebar_scope`, `apply_stock_settings`, `apply_store_roles`, `apply_lot_status`, `apply_receipt_fields`, `apply_supplier_approval_fields`, `apply_specification_fields`, `apply_roles`, `apply_workflows`, `apply_retention`, `apply_archive_indexes`, `apply_alerts`. Every step is idempotent (an unchanged site writes nothing; verified for desk, sidebars, roles, workflows). `before_migrate` runs `setup.modules.sync_module_defs`: Module Def rows for this app match `modules.txt` before DocTypes sync (a module added later reaches existing sites; an undeclared module is removed only when it owns nothing); Frappe's cached module map is rebuilt when `modules.txt` changed. Nothing under `apps/frappe`, `apps/erpnext`, `apps/india_compliance` is ever written (`test_owning_apps_source_is_never_modified`, `test_shipped_definitions_and_timestamps_are_untouched`; CI `scripts/verify_vendored.sh`).

### 2.4 Traceability (URS-PLT-003)

`qa/__init__.py` `verifies("URS-…")` marks a test function or class; a Cypress test carries the id in its title (`it("[URS-PLT-001] …")`). `qa/traceability.py` loads `requirements.toml` (61 modules in scope, 58 withdrawn and never reused; ids `URS-<AREA>-<nnn>`, status planned/implemented/withdrawn), collects markers from source without importing the tests, optionally reads a JUnit report, and `--check` exits 1 when an implemented requirement has no test, a test cites an unknown or withdrawn id, or a cited test failed, was skipped or is missing from the report; `--output` writes the Markdown matrix. It runs as a pre-commit hook (`.pre-commit-config.yaml` *Requirement traceability*, on changes to tests, `requirements.toml` or Cypress specs), in CI before anything else and again with the JUnit results, and as a unit test of the live registry (`TestProjectTraceability`).

### 2.5 Test site preparation (URS-PLT-004)

`qa/site_setup.py` `prepare_test_site` (`bench --site <test-site> execute meddevice_suite.qa.site_setup.prepare_test_site`) completes the setup wizard once with the fixed company **MedDevice Test Company** (abbr MTC, India, INR, Asia/Kolkata, FY 2026-04-01 to 2027-03-31, audit trail on) and refuses a site without the `allow_tests` flag: *<site> does not allow tests; refusing to prepare it as a test site*. `setup/site_flags.py` `site_flag` counts a flag only when the site's own config sets it to exactly `1`/`true` (never `"1"`, never from the bench-wide `common_site_config.json`). CI and `scripts/ci-local.sh` build the site with `scripts/ci/create_test_site.sh` (`bench new-site … --install-app erpnext`, india_compliance, meddevice_suite, `set-config allow_tests true`, then `prepare_test_site`).

### 2.6 Pilot site and demo data (URS-PLT-006)

`scripts/create_pilot_site.sh [site]` (default `pilot.localhost`; needs `MARIADB_ROOT_PASSWORD`, `ADMIN_PASSWORD`, `PILOT_USER_PASSWORD`) creates the site, sets `pilot_site 1` and `pilot_user_password`, then runs `pilot.site.prepare_pilot_site` (wizard with **Demo Medical Devices**, abbr DMD), `pilot.demo.load_demo_data` and `pilot.samples.load_sample_records`. All three refuse a site without the flag: *<site> is not a pilot site; refusing to load demo data*; the loader also refuses without the password in site_config. Demo data (all fictional, parties marked "(Demo)"): stores Quarantine, Rejected, Hold beside ERPNext's Stores/Work In Progress/Finished Goods; eight raw materials (RM-SILICONE … RM-CARTON) and six products (FC-2W-SIL-14/16, FC-2W-LTX-16, FC-3W-SIL-18, UB-2000-TAP, UB-2000), batch-tracked with shelf life and HSN; the **Foley Catheter Line** and **Urine Bag Line** routings with the D3 process steps and a default BOM per product (batch of 100); five suppliers, two customers, price lists; inspection parameters, instrument and specifications (`pilot/inspection.py`); one demo user per job profile. Running again adds only what is missing. The sample loader (`pilot/samples.py`) runs the standard flow once as the demo users and leaves one record at every stage: delivered (FC-2W-SIL-16 × 200), in production with open job cards (UB-2000-TAP × 300), a quotation awaiting price approval (FC-3W-SIL-18 × 150), a purchase order awaiting approval (RM-LATEX), latex in quarantine, a rejected lot of PVC film.

### 2.7 The standard flow, by job (URS-PLT-007)

`pilot/flow.py` runs each step as the demo user of its job (`as_job`, allowed only on a pilot site or in tests): Sales enquiry → quotation → price approval → sales order; Stores purchase request; QA approves the supplier with a signature; Purchase order → approval; Stores goods receipt into quarantine by batch with the supplier's lot number and certificate on each line; QC verifies certificates and inspects each lot; QA releases each lot with a signature; Production work order on the line's routing → material issue → operations on job cards (Operator logs, supervisor submits) → finished lot with its expiry; QA releases the finished lot; Stores delivers. `pilot/test_standard_flow.py` runs it through code; `cypress/integration/standard_flow.js` runs it through the real screens in 18 steps, signed in as each job, on masters made by `qa/ui_journey.py` `prepare_journey` (every record named "UIJ"; leftover stock of an earlier run written off; a later-expiring lot of silicone and of the product put in store first so the run shows expiry-first issue). Every step fails on any refused request or error message it did not expect.

### 2.8 Every job's screens (URS-PLT-009)

`qa/ui_users.py`: `prepare_job_users` (Administrator only, test site only) makes `<job>@ui-tests.example.com` for each of the 12 profiles with a password generated per run; `reachable()` tells the signed-in user's browser the areas their desk loads, the lists and query/script reports the sidebars of their desk link to, the new forms they may fill in (`fillable_form`: not tables, singles, "in create" types, system-written logs, nor types created only through transactions or a service), and the lookups left closed on purpose (`setup/lookups.py` `REVIEWED_CLOSED`). `screen_sweep.js` signs in as each job, opens every area, list, report and new form, searches every open lookup as the field would, and collects refused/failed requests, script errors and red/orange messages; only the reviewed ERPNext quirks in `QUIRKS` (exact page, path and error) are accepted. Evidence per job is written to `reports/screen_sweep/<job>.json`.

### 2.9 Quality gates (rules.md §7)

| Gate | Where it runs |
| --- | --- |
| Official apps unchanged (`scripts/verify_vendored.sh`) | CI job *integrity*; locally before a PR |
| Lint and format (`pre-commit run --all-files`: ruff, prettier, eslint, traceability hook) | pre-commit; CI *Linters* (`linter.yml`), which also runs Frappe's Semgrep rules + `r/python.lang.correctness` and `pip-audit` |
| Server tests with coverage and JUnit | CI *server* (`bench --site test.localhost run-tests --app meddevice_suite --coverage --junit-xml-output …`) |
| Coverage ≥ 90 % (`pyproject.toml` `fail_under = 90`) | CI *server*; locally `env/bin/coverage report …` |
| Traceability `--check` (+ `--junit`, `--output reports/traceability.md`) | pre-commit; CI *integrity* and *server* (matrix on the run summary; `server-test-evidence` artifact with junit, coverage and the matrix) |
| UI tests (`bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron`) | CI *ui* (bench started by `scripts/ci/start_bench.sh`, scheduler and watcher off); locally, or in `scripts/ci-local.sh` (Docker, 2 CPUs at 0.7 CPU-time, 7 GB, Linux, same Electron; `up` / `test [cypress args]` / `run` / `shell` / `down`; screenshots and bench log in `ci-local-results/`) |
| Migrates cleanly (`bench --site <test-site> migrate`) | locally; in `ci-local.sh test`; CI step *Migrates cleanly* before the server tests (since 2026-10-10) |

### 2.10 Intended but not implemented / outside this feature

- A release tag with archived evidence (rules.md §10) is a process step, not code.
- `bench migrate` as a separate CI step does not exist (`ci.yml` installs on a fresh site; `ci-local.sh test` migrates an existing one).
- The pilot's demo data stops at delivery; sterilization, final QC and batch release arrive with their modules (24–26).
- The "Welcome Workspace" Frappe shows a job whose areas are sidebars only (e.g. Training Coordinator: Organization) is Frappe's empty start page, accepted by `test_each_jobs_desk_loads_only_the_areas_of_its_work`.

## 3. Requirements and acceptance criteria

| # | Requirement | Implementation | Automated verification |
| --- | --- | --- | --- |
| C1 | URS-PLT-001: only the standard areas and entries the requirement uses are shown; every other, including future ones, hidden for all users | `DESKTOP_ICONS_SHOWN`, `apply_desk_configuration`, `enforce_declared_visibility`; `SIDEBAR_SCOPE`, `SIDEBAR_ADDITIONS`, `apply_sidebar_scope` | `setup/test_desk.py` `TestDeskConfiguration.test_declared_visibility_is_applied`, `test_an_area_a_future_release_adds_is_hidden`, `test_every_declared_area_exists`; `TestSavedDesktopLayouts.*` (3); `setup/test_sidebars.py` `TestSidebarScope.*` (10), `TestSectionHeadings.*` (2); `setup/test_install.py` `TestApplyConfiguration.test_entry_point_restores_declared_desk`; `desk_scope.js` "[URS-PLT-001] shows the areas the client requirement uses", "… hides every out-of-scope area, including inside folders", "… lists only in-scope entries inside an area" |
| C2 | URS-PLT-002: configuration applied automatically and idempotently on install and every migrate, without modifying the official apps | `apply_configuration`, hooks `after_install`/`after_migrate` | `TestInstallHooks.test_configuration_runs_on_install_and_every_migrate`; `TestDeskConfiguration.test_reapplying_is_idempotent`, `test_owning_apps_source_is_never_modified`, `test_change_does_not_touch_modified`, `test_unknown_icon_is_ignored`; `test_sidebars` `test_reapplying_changes_nothing`, `test_shipped_definitions_and_timestamps_are_untouched`; (module 47/48: `test_an_unchanged_matrix_writes_nothing`, `test_an_unchanged_workflow_is_not_saved_again`) |
| C3 | URS-PLT-003: every implemented requirement verified by a test; matrix generated from source | `qa/traceability.py`, `verifies` | `qa/test_traceability.py` (7 classes: marker, registry, collectors, find_problems, reports, command line, project traceability — 28 test methods) |
| C4 | URS-PLT-004: tests on a disposable site brought to a known state; preparation refuses a site that does not allow tests | `qa/site_setup.py`, `setup/site_flags.py` | `qa/test_site_setup.py` `TestPrepareTestSite.*` (4), `TestSiteFlags.*` (2); `qa/test_ui_users.py` and `test_ui_journey.py` `test_refuses_a_site_that_does_not_allow_tests` |
| C5 | URS-PLT-005: declared modules exist before DocTypes sync; undeclared empty modules removed | `setup/modules.py`, hook `before_migrate` | `setup/test_modules.py` `TestModuleSync.*` (6) |
| C6 | URS-PLT-006: pilot site loadable with demo data following the client's process; one demo user per job; never on another site | `pilot/site.py`, `pilot/demo.py`, `pilot/samples.py`, `refuse_unless_pilot_site`, `create_pilot_site.sh` | `pilot/test_demo.py` `TestOnlyOnAPilotSite.*` (4), `TestDemoData.*` (8); `pilot/test_samples.py` `TestSampleRecords.*`, `TestSamplesOnlyOnAPilotSite`; `pilot/test_standard_flow.py` `TestActingAsAJobOnlyOnAPilotSite`; `TestSiteFlags` |
| C7 | URS-PLT-007: the standard flow runs end to end with each step by its job, in the audit trail | `pilot/flow.py`; `qa/ui_journey.py` | `pilot/test_standard_flow.py` `TestStandardFlow.test_enquiry_to_delivery_with_each_job_doing_its_part`; `pilot/test_samples.py`; `qa/test_ui_journey.py` `TestBrowserJourney.*` (7); `standard_flow.js` 18 steps "[URS-PLT-007] …" |
| C8 | URS-PLT-008: each job's desk shows only its areas; everything in them opens; a record type one may not use is refused plainly | `DESK_AREAS`, `set_area_roles`, `UNUSED_AREA`, `getpage` | `setup/test_desk.py` `TestEachJobsDesk.*` (9: areas say their jobs, each job sees/loads only its areas, icons, everything opens, roles restored on migrate, plain refusal, guest, real pages); `setup/test_lookups.py` `test_the_reports_of_a_jobs_areas_can_read_their_records` |
| C9 | URS-PLT-009: signed in as each job the browser opens every area, list and new form with every lookup without a refused request; checked on every change | `qa/ui_users.py`, `screen_sweep.js` | `screen_sweep.js` 12 tests "[URS-PLT-009] <job>: every area, list and form of its work opens without an error"; `qa/test_ui_users.py` `TestBrowserTestUsers.*` (8); `setup/test_roles.py` `TestOwnFormsAreUsable.test_a_job_creates_without_a_form_only_what_its_transactions_create`, `test_a_service_posts_for_a_job_what_the_job_never_fills_in`; `setup/test_lookups.py` `test_forms_can_read_what_they_fetch_on_opening` |

**Ambiguous / partial:**
- URS-PLT-001 "every other standard area … hidden for all users": the Administrator account still reaches unused workspaces by address (`UNUSED_AREA = ("Administrator",)`), by design for installation.
- URS-PLT-009 "every such form can be filled in": the sweep checks that at least one field is writable and that every lookup searches; it does not fill and save each form (the journey does for its 18 steps).
- URS-PLT-007 "all recorded in the audit trail": the server test checks *Submitted* events for the receipt, work order and delivery; the browser journey checks the cancel reason and the submitting user of six records; not every record of the flow is asserted.
- "Migrates cleanly" has its own CI step since 2026-10-10 (section 2.10).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`) for PL-01…PL-05 and PL-09 (demo users); a test site (`test.localhost`, `allow_tests`) for PL-06…PL-08, which need bench commands. Record the actual result and a screenshot (the desk, the sidebar, the refusal message).

### PL-01 The desk shows only in-scope areas (C1)
Role: QA Manager (sees most areas).
1. Sign in; open `/desk`. Expected: areas Selling, Buying, Stock, Manufacturing, Quality, Assets, Organization, Documents, Compliance (and no CRM — QA is not a sales role); none of Projects, Website, Budget, Payments, Subcontracting, Support, Automation, Integrations, Build.
2. Open `/desk/projects` by address. Expected: refused / not found (the area is closed to everyone but Administrator).
3. Open **Selling**. Expected: the sidebar lists Quotation, Sales Order, Item, Price List …; no POS, Loyalty Program, Coupon Code, Sales Partner.
4. Open **Quality**. Expected: Quality Inspection, Quality Feedback, Feedback Template, Quality Inspection Template, Lot Release, Lots Awaiting Release, Supplier Approval; no Non Conformance / Quality Action / Quality Goal.
5. Open **Stock**. Expected: the added entries **Lot Release**, **Lots Awaiting Release**, **Stock by Lot Status** are present.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 1, 3, 4.

### PL-02 Each job's desk is its own (C8)
1. As **Sales**: `/desk`. Expected: CRM, Selling, Stock, Documents only. `/desk/buying` by address. Expected: refused.
2. As **Operator**: `/desk`. Expected: Manufacturing and Documents only. Open **Manufacturing**; click each shortcut/card (Work Order, Job Card, …). Expected: each opens without a permission error.
3. As **Sales**: open `/desk/stock-entry` by address. Expected: red message *You are not permitted to open Stock Entry.*; the desk stays usable.
4. As **Accounts**: `/desk`. Expected: Accounting, Accounts Setup, Invoicing, Banking, Taxes, Financial Reports, India Compliance, GST India, Assets, Documents; no Income Tax India (Accounts Manager only).
5. As **System Administrator**: `/desk`. Expected: Users, System, Data, Email, Printing, ERPNext Settings, Framework, Organization, Accounting, Accounts Setup, Compliance, Documents; no Selling/Buying/Stock/Manufacturing/Quality.
Actual result: ____  Pass/Fail: ____

### PL-03 A customised desk keeps hidden areas hidden (C1)
Role: QA Manager.
1. On `/desk` use Frappe's desk customisation (drag an icon to another position; if the desk offers an "edit"/"customize" mode, use it) so that a Desktop Layout is saved. Expected: the order changes and survives a reload.
2. Try to show a hidden area through the customisation (if offered). Expected: it is not shown after save; hidden areas stay hidden.
Actual result: ____  Pass/Fail: ____  Note: the exact customisation controls depend on Frappe v16's desk; the enforcement is on the saved layout.

### PL-04 Demo data and users on the pilot (C6)
Role: System Administrator and each demo user.
1. Sign in as each `<profile>@demo.example.com` (12) with `PILOT_USER_PASSWORD`. Expected: each signs in; last name "(Demo)"; the desk of its job (PL-02).
2. As QA Manager: **Stock › Warehouse** list. Expected: *Quarantine - DMD*, *Rejected - DMD*, *Hold - DMD* beside *Stores - DMD*, *Work In Progress - DMD*, *Finished Goods - DMD*.
3. **Stock › Item**: Expected: RM-SILICONE … RM-CARTON (Raw Material, batch-tracked, expiry, HSN) and the six products.
4. **Manufacturing › Routing**: *Foley Catheter Line* (Tube Extrusion → … → Labelling), *Urine Bag Line* (Film Blowing → … → Labelling). **BOM**: one submitted default BOM per product with the routing.
5. **Buying › Supplier**: five "(Demo)" suppliers; **Selling › Customer**: City Hospital (Demo), Regional Medical Distributor (Demo).
Actual result: ____  Pass/Fail: ____

### PL-05 Sample records at every stage (C6, C7)
Role: Management / Stores / Production Supervisor as fitting.
1. **Selling › Quotation**, filter Workflow State = Awaiting Price Approval. Expected: a quotation for FC-3W-SIL-18 × 150.
2. **Buying › Purchase Order**, Workflow State = Awaiting Approval. Expected: an order for RM-LATEX × 25.
3. **Selling › Sales Order**: an order for FC-2W-SIL-16 × 200 with *Delivered 100 %*; **Stock › Delivery Note** submitted for it.
4. **Manufacturing › Work Order**: UB-2000-TAP × 300 *In Process*; its **Job Cards** open (draft), one per operation of the Urine Bag Line.
5. **Stock › Stock by Lot Status** report (or Batch list): latex in Quarantine; a rejected PVC-film lot in the Rejected store; **Compliance › Audit Trail** (as QA) shows the sample records' events by the demo users.
Actual result: ____  Pass/Fail: ____

### PL-06 Test site preparation refuses a working site (C4)
Role: developer with a bench shell (no browser).
1. On a site **without** `allow_tests`: `bench --site <site> execute meddevice_suite.qa.site_setup.prepare_test_site`. Expected: *<site> does not allow tests; refusing to prepare it as a test site*.
2. On the test site (`allow_tests: true` in its own site_config): the same command. Expected: completes the wizard with *MedDevice Test Company* (or does nothing when already complete); `/desk` opens on the desk, not the setup wizard.
3. Put `"allow_tests": 1` only in `sites/common_site_config.json` (then remove it). Expected: the command still refuses (a bench-wide flag never marks a site).
Actual result: ____  Pass/Fail: ____

### PL-07 Demo data never loads elsewhere (C6)
Role: developer with a bench shell.
1. On the test site (not a pilot): `bench --site test.localhost execute meddevice_suite.pilot.demo.load_demo_data`. Expected: *test.localhost is not a pilot site; refusing to load demo data*.
2. On the pilot site without `pilot_user_password`: Expected: *Set pilot_user_password in the pilot site's site_config before loading demo data.*
3. `scripts/create_pilot_site.sh` run a second time on the existing pilot. Expected: "pilot.localhost exists: adding only what is missing"; counts of items, BOMs, users, suppliers unchanged; sample records not duplicated.
Actual result: ____  Pass/Fail: ____

### PL-08 Migrate restores the configuration (C2, C5, C8)
Role: developer with a bench shell and the Administrator in the browser.
1. In the browser as Administrator, hide an in-scope icon or re-show a hidden one by hand (e.g. **Desktop Icon** list: set *Projects* hidden = 0); and delete the **Has Role** rows of the Buying workspace (Permission on the Workspace record).
2. `bench --site <test-site> migrate`. Expected: migrate ends without error; `apply_configuration` ran (after_migrate).
3. Reload `/desk` as QC Inspector. Expected: Projects hidden again; Buying not visible/openable to QC.
4. `bench --site <test-site> console` → `frappe.get_all("Module Def", filters={"app_name": "meddevice_suite"}, pluck="name")`. Expected: exactly the modules in `meddevice_suite/modules.txt`.
Actual result: ____  Pass/Fail: ____

### PL-09 Browser journey and screen sweep (C7, C9)
Role: developer; test site; bench started.
1. `bench --site test.localhost add-to-hosts` once; then `env -u ELECTRON_RUN_AS_NODE NODE_OPTIONS=--dns-result-order=ipv4first bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --spec cypress/integration/screen_sweep.js`. Expected: 12 passing tests, one per job; `apps/meddevice_suite/reports/screen_sweep/<job>.json` written with `failures: []`.
2. Same with `--spec cypress/integration/standard_flow.js --config retries=0`. Expected: 18 passing steps in order; each step's problems list empty.
3. Same with `--spec cypress/integration/desk_scope.js`. Expected: 4 passing tests (3 × URS-PLT-001, 1 × URS-CMP-035).
Actual result: ____  Pass/Fail: ____

### PL-10 Traceability and CI gates (C3)
Role: developer.
1. In `apps/meddevice_suite`: `python3 -m meddevice_suite.qa.traceability --check --output reports/traceability.md`. Expected: exit 0; the matrix lists every URS id with its tests.
2. Temporarily add `[[requirement]]` with `status = "implemented"` and no test; run again. Expected: exit 1, *URS-…: implemented but not verified by any test*. Revert.
3. `pre-commit run --all-files`. Expected: all hooks pass, including *Requirement traceability*.
4. `scripts/verify_vendored.sh`. Expected: official apps byte-for-byte their pinned releases.
Actual result: ____  Pass/Fail: ____

### Regression
- Any change to `desk.py`, `sidebars.py`, `roles.py`: run `desk_scope.js`, `screen_sweep.js` and the server tests of `setup/`; CI runs all gates on every push and pull request.

## 5. End-to-end business workflow

1. A developer clones the bench (`scripts/bootstrap.sh --dev`), creates a working site and a test site, runs `prepare_test_site` on the test site (`allow_tests`), and `bench migrate` applies the whole configuration (desk scope, sidebars, roles, workflows, retention, alerts, lot control).
2. The client's pilot is created by `scripts/create_pilot_site.sh`: wizard, demo masters, demo users, sample records at every stage; the client walks the standard flow as `sales@`, `purchase@`, `stores@`, `qc.inspector@`, `qa.manager@`, `production.supervisor@`, `operator@`, `management@demo.example.com`, each seeing only the areas of their job.
3. Every change: requirement first in `requirements.toml` → code in `apps/meddevice_suite` → tests citing the id → pre-commit (lint, traceability) → server tests on the test site → UI tests (`desk_scope`, `screen_sweep` as 12 jobs, `standard_flow` 18 steps, module specs) → pull request → CI on a fresh site (integrity, server with coverage and matrix, UI) and linters (pre-commit, Semgrep, pip-audit) → merge → release tag with the `server-test-evidence` artifact as the validation record.
4. A CI-only failure is reproduced in `scripts/ci-local.sh` (Docker at the runner's pace) before a fix is pushed.

Data touched by the platform: Desktop Icon (hidden), Workspace / Desktop Icon Has Role rows, Workspace Sidebar Item rows, Desktop Layout, Module Def, Role / Role Profile / Custom DocPerm (module 47), Workflow (48), Notification (51); on the pilot: the demo masters and sample transactions.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Desk scope | `setup/desk.py`: `DESKTOP_ICONS_SHOWN` (26), `DESK_AREAS` (area → roles), `UNUSED_AREA`, `apply_desk_configuration`, `set_desktop_icon_visibility`, `set_area_roles`, `getpage` (whitelisted, `allow_guest`, overrides `frappe.desk.desk_page.getpage` via `override_whitelisted_methods`), `enforce_on_desktop_layout`, `enforce_declared_visibility` |
| Sidebars | `setup/sidebars.py`: `SIDEBAR_SCOPE` (21 sidebars), `SIDEBAR_ADDITIONS`, `apply_sidebar_scope`, `scope_sidebar`, `has_entries`, `add_entries`, `sidebar_rows`; own sidebars/icons `meddevice_suite/workspace_sidebar/{documents,compliance}.json`, `desktop_icon/{documents,compliance}.json` |
| Install / migrate | `setup/install.py` `apply_configuration` (13 steps); `setup/modules.py` `sync_module_defs`, `refresh_module_map`, `owns_anything`; hooks `before_migrate`, `after_install`, `after_migrate`; `Desktop Layout.validate` |
| Site flags | `setup/site_flags.py` `site_flag(key)`: `allow_tests`, `pilot_site` (exactly 1/true, in the site's own config) |
| Test site | `qa/site_setup.py` `TEST_COMPANY`, `prepare_test_site`, `complete_setup_wizard`; `scripts/ci/create_test_site.sh`; `scripts/ci/start_bench.sh` |
| Pilot | `pilot/__init__.py` `refuse_unless_pilot_site`; `pilot/site.py` `PILOT_COMPANY`, `prepare_pilot_site`; `pilot/demo.py` `EXTRA_STORES`, `SUPPLIERS`, `CUSTOMERS`, `RAW_MATERIALS`, `PRODUCTS`, `ROUTINGS`, `FINISHING`, `BOM_QUANTITY`, `load_demo_data`, `ensure*`, `demo_user_email`; `pilot/samples.py` `load_sample_records`, `DELIVERED`, `IN_PRODUCTION`, `AWAITING_PRICE_APPROVAL`, `AWAITING_PURCHASE_APPROVAL`, `IN_QUARANTINE`, `REJECTED`; `pilot/flow.py` `as_job` and the step functions; `pilot/inspection.py`; `scripts/create_pilot_site.sh` |
| Browser helpers (whitelisted) | `POST /api/method/meddevice_suite.qa.ui_users.prepare_job_users` (Administrator, test site) → `{password, users}`; `/api/method/meddevice_suite.qa.ui_users.reachable` → `{areas, lists, reports, create, closed_lookups}`; `POST /api/method/meddevice_suite.qa.ui_journey.prepare_journey` (Administrator, test site); `/api/method/meddevice_suite.qa.ui_journey.lots_of` (`doctype`, `name`; read permission on the record) |
| Browser specs | `cypress/integration/desk_scope.js` (SHOWN/HIDDEN/HIDDEN_ENTRIES mirror `desk.py`/`sidebars.js`), `screen_sweep.js` (JOBS mirrors `JOB_PROFILES`; `QUIRKS`; evidence in `reports/screen_sweep/`), `standard_flow.js` (18 `it` steps, `retries: 0`, stops after the first failure) |
| Traceability | `qa/traceability.py` (`REGISTRY_PATH`, `PACKAGE_ROOT`, `UI_TEST_ROOT`, `collect_python_tests`, `collect_ui_tests`, `load_junit_results`, `find_problems`, `render_markdown`, `main` with `--junit`, `--output`, `--check`); `qa/__init__.py` `verifies` |
| CI | `.github/workflows/ci.yml` jobs *integrity* → *server*, *ui* (MariaDB 11.8, Redis services; `.github/actions/setup-bench` runs `scripts/bootstrap.sh` and `scripts/ci/create_test_site.sh`; artifacts `server-test-evidence`, `ui-test-screenshots`); `.github/workflows/linter.yml` (pre-commit, Semgrep, pip-audit); `scripts/ci-local.sh` (`up`, `test`, `run`, `shell`, `down`; env `CI_LOCAL_CPUS`, `CI_LOCAL_CPU_TIME`, `CI_LOCAL_MEMORY`) |
| Routes | `/desk`, `/desk/<area-slug>`, `/desk/<doctype-slug>`, `/desk/<doctype-slug>/new`, `/desk/query-report/<report>`; `/login`; `/api/method/frappe.desk.desk_page.getpage` (overridden) |
| Data flow | migrate → `sync_module_defs` (before) → DocType sync → `apply_configuration` (after) → cache keys `desktop_icons`, `bootinfo` cleared → each sign-in builds the desk from Desktop Icon/Workspace roles and the scoped sidebars; browser tests → `prepare_job_users`/`prepare_journey` (Administrator) → sign in as each job → `reachable()` → pages opened → failures collected in the spec |

## 7. Defects, edge cases and risks

**Confirmed and fixed during development (git history; each with a test or a spec change):** hidden areas re-shown by a user's saved desk (`440d2742`); areas no job uses greeted people with errors of records they may not read (`60080aef`, `565aa057`: per-job areas and the plain refusal); report and page permission errors found in the live pilot test of 2026-10-07 (`efc93b3e`, `9d7d097e`: screen sweep and lookups); placeholder module left behind by `bench new-app` (`b68c6379`); browser-test timing on the 2-CPU runner (typed values wiped, lookup lists left open, reload after a workflow action: `1cc0c5f8`, `4c640986`, `7ff66f35`, `2e752759`); user switch with a page still open ending the new session (`d65c28ba`); CI UI job reproduced locally (`4d0ee728`).

**Potential / unverified:**
- Reviewed ERPNext quirks accepted by the sweep (`QUIRKS`): new Customer/Supplier primary address lookups answer 404 before save; Asset Maintenance Log task search refused before the maintenance is chosen; *Sales Pipeline Analytics* and *Serial No and Batch Traceability* run on opening and say a filter is mandatory; the GST *Purchase Reconciliation Tool* refuses its empty period on load; Frappe's `removeChild` redraw error on report charts (and once on the Account list in CI, 2026-10-09). Each is the owning app's behaviour; a fix belongs there.
- The desk is scoped by hiding, not deleting: a person who knows a hidden record type's address and has ERPNext's default permission on a non-regulated type (e.g. a Project) can still open its list; only regulated types have the declared matrix. Not a requirement gap (URS-PLT-001 is about the desk), noted for the record.
- `screen_sweep.js` depends on the `JOBS` list matching `JOB_PROFILES` (it stops if they differ) and on `reachable()`, which is computed from Frappe's permissions: a job whose desk is empty would pass trivially except for the `areas not empty` assertion.
- The standard-flow journey and sweep are long (minutes per run on 2 CPUs); CI cancels an in-progress run on a new push (`concurrency: cancel-in-progress`), so a final commit must get its own green run.
- Coverage is measured in CI only (local runs are optional); the last local figure cited by the module 4 document was 99 % overall.
- `standard_flow.js` logs the second and later job cards through the API, not the screens (noted in the spec), to keep the run short.

## 8. Test results and verification summary

Automated results below were executed by the lead on 2026-10-10 on the current branch (local test site `test.localhost`); this document cites them and did not re-run anything.

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (includes `setup/test_desk.py` 19 test methods, `test_sidebars.py` 12, `test_modules.py` 6, `test_install.py` 2, `qa/test_site_setup.py` 6, `test_ui_users.py` 8, `test_ui_journey.py` 7, `test_traceability.py` 28, `pilot/test_demo.py` 12, `test_samples.py` 6, `test_standard_flow.py` 3 — counted in source) |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed: `desk_scope.js` 4, `screen_sweep.js` 12 (all jobs), `standard_flow.js` 18 steps, module specs |
| Semgrep (Frappe rules + python correctness) | as in `.github/workflows/linter.yml` | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| GitHub CI (fresh site) | `.github/workflows/ci.yml`, `linter.yml` | in progress when this document was written |
| Manual tests PL-01…PL-10 | — | **not executed** (no manual tester session). Automated coverage of them: PL-01 steps 1–4 (`desk_scope.js` as Administrator; `TestEachJobsDesk`), PL-02 (`TestEachJobsDesk`, screen sweep as each job), PL-03 (`TestSavedDesktopLayouts`, server side), PL-04/05 (`test_demo.py`, `test_samples.py` on the test site, not on a real pilot), PL-06/07 (unit tests with patched config), PL-08 (`apply_configuration` called directly, not a real `bench migrate`), PL-09 (the suites themselves), PL-10 (`TestCommandLine`, `TestProjectTraceability`, CI) |

Untested: a real `bench migrate` after hand changes (PL-08); the pilot script end to end on a fresh machine in this cycle (PL-04/05/07 — the loaders are tested on the test site under a patched `pilot_site` flag); Frappe's desk customisation controls (PL-03).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Only in-scope areas and sidebar entries shown; future areas hidden; saved layouts enforced | Verified (automated + browser) |
| Each job's desk shows only its areas; everything in them opens; plain refusal for a record type one may not open | Verified (automated + screen sweep) |
| Configuration applied on install and every migrate, idempotent, official apps untouched | Verified (automated, `apply_configuration` direct); real `bench migrate` after hand changes **Not tested** this cycle |
| Modules synced before DocTypes | Verified (automated) |
| Traceability: every implemented requirement has a passing test; matrix generated; gate in pre-commit and CI | Verified (automated; CI run **Pending**) |
| Test site preparation refuses non-test sites; bench-wide flag ignored | Verified (automated, patched config) |
| Pilot site, demo data, demo users, sample records; refused elsewhere | Verified (automated on the test site); fresh pilot creation **Not tested** this cycle |
| Standard flow end to end by job: server and 18-step browser journey | Verified (automated) |
| Every job's areas, lists, new forms and lookups open in the browser | Verified (screen sweep, 12 jobs) |
| Manual cases PL-01…PL-10 | **Not tested** |
| CI green on a fresh site for the final commit | **Pending** |
