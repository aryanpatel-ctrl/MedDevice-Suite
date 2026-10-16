# Roles and permissions (module 47) — feature document and browser test guide

Requirements: **URS-CMP-014, URS-CMP-015, URS-CMP-016, URS-CMP-017, URS-CMP-018, URS-CMP-034, URS-CMP-035, URS-CMP-036** (`meddevice_suite/qa/requirements.toml`). Touched by: URS-PLT-008/009 (each job's desk and screens, see the platform document) and URS-CMP-013 (which record types are regulated, `compliance/audit_scope.py`).
Code: `meddevice_suite/setup/roles.py` (job profiles, permission matrix, separation of administration), `setup/lookups.py` (lookups a job may not search), `public/js/sign_out.js` (clean sign-in page), wired in `meddevice_suite/hooks.py`; applied by `setup/install.py` `apply_configuration`.
State on 2026-10-10: built, in the server and browser suites that the lead ran green on the current branch (section 8); GitHub CI for the branch still in progress when this document was written.
Changed 2026-10-12 (foundation review, branch `fix/immutable-records`): a permission row is no longer made by hand on the app's own record types, and none may let anyone write the Electronic Signature or the Audit Event (section 2.2). Tests: `compliance/test_immutable_records.TestPermissionsOnTheAppsRecordTypes` and the edited `compliance/test_quality_settings.test_a_hand_added_permission_does_not_last` — written, **not yet run** (section 8).
Changed 2026-10-15 (independent review, branch `fix/review-access-evidence`): the refusal covers every record type whose permissions the app declares, not only its own: a row on a regulated record (Item, Purchase Receipt, Batch, …), a supporting record type (Account, …) or the site configuration (Custom Field, …) cannot be added, changed, removed or reset by hand; and a job profile cannot be changed or deleted by hand (section 2.2). Before, a system manager could grant delete, share or import there, or add a role to a job, in force until the next migrate. Tests: `setup/test_roles.TestPermissionsAreNotChangedByHand`, `TestJobProfilesAreNotChangedByHand`, the edited `setup/test_configuration.test_a_permission_added_by_hand_does_not_last` — written, **not yet run**.

## 1. Feature overview

**Purpose.** Every person is given one *job profile* (a Frappe Role Profile) that grants exactly the roles the job needs. For every regulated business record type the roles that may read, create, submit, cancel and amend it are declared in code and applied on every site exactly, replacing ERPNext's defaults; nobody deletes, shares or imports a regulated record; system administrators read but never do or approve regulated work, and may not hold a business role at all. After a sign-out the next person starts on a clean sign-in page, and every lookup a job's own forms need can be searched by that job.

**Why it matters.** 21 CFR 11.10(d) and (g) require that only authorised individuals use the system and that authority checks enforce who may do each step; ISO 13485 4.2.4/4.2.5 require controlled, retained records. An ERPNext upgrade or a hand change in the Permission Manager that widened access would be an unnoticed validation gap; here the matrix in code is the specification and every `bench migrate` restores it.

**Who uses it.** Everyone, through the job they are given. The twelve jobs (`JOB_PROFILES`), with the pilot's demo users `<profile>@demo.example.com` (`pilot/demo.py` `demo_user_email`):

| Job profile | Demo user | Roles granted |
| --- | --- | --- |
| Sales | `sales@demo.example.com` | Sales User |
| Purchase | `purchase@demo.example.com` | Purchase User |
| Stores | `stores@demo.example.com` | Stock User |
| Production Supervisor | `production.supervisor@demo.example.com` | Manufacturing User |
| Operator | `operator@demo.example.com` | Operator (new role) |
| QC Inspector | `qc.inspector@demo.example.com` | QC Inspector (new role) |
| QA Manager | `qa.manager@demo.example.com` | Quality Manager, Item Manager |
| Maintenance | `maintenance@demo.example.com` | Maintenance User |
| Training Coordinator | `training.coordinator@demo.example.com` | HR User |
| Accounts | `accounts@demo.example.com` | Accounts User |
| Management | `management@demo.example.com` | Sales Manager, Purchase Manager, Stock Manager, Manufacturing Manager |
| System Administrator | `system.administrator@demo.example.com` | System Manager |

The jobs were decided from the departments in the client's diagrams on the client's instruction of 2026-10-07 (the diagrams name no job roles). Job titles can be renamed without changing what each job may do.

**Prerequisites.** The app is installed (`apply_configuration` ran on install or on the last `bench migrate`). No hand configuration: Role Profiles, roles and Custom DocPerm rows are written by code.

## 2. Detailed functional explanation

### 2.1 Job profiles (URS-CMP-014)

`apply_roles()` creates the two roles ERPNext lacks (`NEW_ROLES`: **QC Inspector**, **Operator**, both with desk access) and makes each Role Profile hold exactly the roles declared in `JOB_PROFILES` (`sync_job_profile`: unchanged profiles are not saved again). A person is given a job on the **User** form, tab/section *Roles* (Frappe's field **Role Profile**, table `role_profiles`); Frappe then fills the user's roles from the profile. A role added to a user outside their profile is dropped by Frappe on save, never granted (verified by `test_an_administrator_cannot_be_given_a_business_role_later`).

Navigation for the administrator: **Users › User** (`/desk/user/<email>`), **Users › Role** (`/desk/role`). Role Profiles are not listed in the Users sidebar as this app scopes it (`setup/sidebars.py` keeps User, Role, User Permission, Permission Manager, Permission Inspector, Activity Log, Access Log, Permission Log); they open by address, `/desk/role-profile/<job>` (not verified in the browser, section 7).

### 2.2 The permission matrix (URS-CMP-015, -016, -017, -018)

`PERMISSIONS` declares, for each of the 27 regulated standard record types (`REGULATED_DOCTYPES` minus the 7 access-control types), which roles get which flags at permission level 0. The flag sets:

| Set | Flags | Meaning |
| --- | --- | --- |
| PICK | select | choose it in a form, never open it |
| VIEW | select, read, report, print | read it |
| REVIEW | VIEW + export | read and export |
| EDIT | VIEW + write, create | masters (never submitted) |
| ENTER | EDIT + submit | enter and submit a transaction |
| CORRECT | ENTER + cancel, amend | and correct it by cancel and amend |
| APPROVE | CORRECT + export | the approver / manager of the record type |

`oversight()` gives every record type **Quality Manager** REVIEW, the four management roles REVIEW and **System Manager** VIEW, then adds the roles that work with it. Examples that carry the requirement texts:

| Record type | Declared rows (beyond oversight) |
| --- | --- |
| Quality Inspection | QC Inspector ENTER; Quality Manager APPROVE (only QA cancels/amends); Stock, Purchase, Manufacturing User VIEW |
| Job Card | Manufacturing User CORRECT; **Operator VIEW + write** (records their own operations, cannot create/submit/cancel); QC Inspector VIEW |
| Work Order | Manufacturing User CORRECT; Manufacturing Manager APPROVE; Operator VIEW |
| Quotation | Sales User EDIT + cancel + amend (no submit); Sales Manager APPROVE (module 48) |
| Sales Order (regulated since 2026-10-12, URS-CMP-041) | Sales User CORRECT; Sales Manager APPROVE; Stock User, Accounts User VIEW; Manufacturing User select, read, report. ERPNext's delete, share, import and email are gone, as are its Maintenance User and Projects rows (no job takes orders there) |
| Purchase Order | Purchase User EDIT + amend (no submit, no cancel); Purchase Manager APPROVE; Stock, Accounts User VIEW |
| Purchase Receipt | Stock User CORRECT; Stock Manager APPROVE; Purchase User, QC Inspector, Accounts User VIEW |
| BOM | Manufacturing User EDIT (drafts); Quality Manager APPROVE (submits) |
| Batch | Item Manager EDIT; Stock User and Manufacturing User VIEW + create (a lot is made by the receipt or production entry: `CREATED_THROUGH_TRANSACTIONS`) |
| Stock Entry, Serial and Batch Bundle | Quality Manager REVIEW + write, create, submit, posted only by QA's lot release service (`POSTED_BY_SERVICES`, module 17) |
| Item, Warehouse | Operator PICK (the item and stores on a job card); Maintenance User PICK on Warehouse |

Nobody has **delete**, **share** or **import** on any regulated record type (URS-CMP-016); **System Manager** has VIEW only (URS-CMP-017). `HIGHER_LEVELS` adds permission-level-1 rows (Customer: Sales Manager read/write, Sales User read; Purchase Order: Purchase Manager; Purchase Receipt and Delivery Note: Stock Manager; Sales Order: Sales Manager, ERPNext's own row for ignoring pricing rules).

`sync_permissions` makes the record type's **Custom DocPerm** rows exactly the declared ones: missing rows are inserted, differing rows updated, extra rows deleted, and an unchanged matrix writes nothing (so migrate records no audit noise). Each change to a Custom DocPerm is itself a regulated change recorded in the audit trail (Custom DocPerm is in `REGULATED_DOCTYPES`).

**The app's own record types** (Quality Settings, Change Request, Electronic Signature, Audit Event and every other DocType this app defines) carry their permissions in their DocType definition. Since 2026-10-12 a **Custom DocPerm** row on one of them is refused as it is made, through Role Permissions Manager or any other code (`refuse_hand_added_permission`, `Custom DocPerm` `before_validate`, which runs even when validation is skipped): *The permissions on <record type> are part of the validated configuration and change only with a release of this app.* Install and migrate are left alone. On the evidence records (`audit_scope.SELF_RECORDING`: Electronic Signature, Audit Event, and since 2026-10-12 Audit Anchor) no row may grant more than select, read, report, export or print, at any time, migrate included: *<record type> records are written only by the system; no role may be given <flags> on them.* Before, a system manager could grant create there and post a signature or an audit event in another person's name until the next migrate. A row written past the check, straight to the database, is still removed on every migrate by `remove_hand_added_permissions`.

**Every record type the matrix governs (2026-10-15).** Undoing a hand change at the next migrate left it in force until then. The refusal now covers `governed_doctypes()`: the regulated record types of `PERMISSIONS`, the supporting record types of `SUPPORTING_PERMISSIONS`, the configuration record types of `CONFIGURATION_PERMISSIONS`, and the app's own. On them a **Custom DocPerm** row is refused when it is added or changed (`before_validate`: Role Permissions Manager's *Add a New Rule* and every tick) and when it is removed (`on_trash`: *Remove*, and *Reset to defaults*, which would bring back ERPNext's rows granting delete and share), with the message above. The configuration's own writes (`sync_permissions`, `remove_hand_added_permissions`) carry the mark `declared_by_code`, so `apply_roles` still converges on the matrix whenever it runs; install and migrate are left alone as before. Other record types keep Frappe's Role Permissions Manager.

**Job profiles (2026-10-15).** A job profile (the Role Profiles of `JOB_PROFILES`) is changed only by `sync_job_profile`, which marks its save `declared_by_code`; a change or deletion from the form or the API is refused (`refuse_hand_changed_job_profile`, Role Profile `validate` and `on_trash`): *The job profile <name> is part of the validated configuration and changes only with a release of this app.* Other role profiles stay the administrator's, held to the separation of administration below.

`SUPPORTING_PERMISSIONS` adds flags to record types outside the regulated scope that a job cannot work without, keeping ERPNext's own rows (e.g. Stock User and System Manager may *select* an **Account**; Stores and Production may *read* **Stock Settings**; the company's letter head is readable by Production, QC, QA and Maintenance).

### 2.3 Separation of system administration from business roles (URS-CMP-034)

`separate_administration` runs on **User** `validate` (hook in `hooks.py`). A user who would hold **System Manager** together with any role of a business job profile (`BUSINESS_ROLES`) is refused: *`<user>` is a system administrator and cannot also hold `<roles>`. Give the business work a separate account.* The built-in `Administrator` and `Guest` accounts, install and migrate are exempt. `TestProfileRules.test_the_administrator_profile_holds_no_business_role` holds the declaration itself to this.

### 2.4 Sign-out behaviour (URS-CMP-035)

Frappe sends a user who signs out to `/login?redirect-to=<page>`, so the next person on a shared terminal would land on the previous person's record (or meet a permission error). `public/js/sign_out.js` (in `meddevice_suite.bundle.js`, loaded on every desk page) overrides `frappe.Application.prototype.redirect_to_login`: after a deliberate sign-out (`logged_out` set by Frappe's `logout()`) the browser goes to plain `/login`; an expired session still returns its own user to their page. Frappe's confirmation dialog *Are you sure you want to log out?* is unchanged.

### 2.5 Lookups a job can search (URS-CMP-036)

A link field searches its target record type, which needs at least *select*. `setup/lookups.py` computes, for every job, each Link field of every form the job fills in (regulated types it may write or create by hand, and the record types its desk sidebars list that it may create) whose target the job can neither select nor read, and holds that set to the reviewed list `REVIEWED_CLOSED` (features the client's process does not use: coupons, loyalty, shipping rules, delivery trips, serial numbers, cost centres on stock documents, …). A new closed lookup fails `setup/test_lookups.py`. Picking never opens: an Operator can search Employee and Item on a job card but has no read on either.

### 2.6 Intended but not implemented / outside this feature

- Permissions of this app's own record types (Supplier Approval, Lot Release, Change Request, Document Revision, Audit Event, Electronic Signature, Record Archive …) are declared in their DocType definitions and tested in their own modules; `TestOwnFormsAreUsable` only checks that a role that may create one may also write it. They are not in `PERMISSIONS`.
- Field-level (permlevel ≥ 1) restrictions exist for four record types only (`HIGHER_LEVELS`); no requirement asks for more.
- User Permissions (restricting a role to a company or warehouse) are not used; a single-company pilot.
- Password policy, session length and login-attempt limits are Frappe's System Settings (locked by `setup/stock.py`/`protect_*` only for stock, log and desk settings); no requirement in module 47 covers them.

## 3. Requirements and acceptance criteria

| # | Requirement / acceptance criterion | Implementation | Automated verification (`setup/test_roles.py` unless noted) |
| --- | --- | --- | --- |
| C1 | URS-CMP-014: each person gets a job profile with exactly its roles; profiles declared in code and applied on every site | `JOB_PROFILES`, `NEW_ROLES`, `apply_roles`, `sync_job_profile` | `TestJobProfiles.test_each_job_profile_grants_exactly_its_roles`, `test_roles_erpnext_lacks_are_created`; `TestProfileRules.*`; `TestPermissionMatrix.test_missing_roles_profiles_and_permissions_are_recreated_on_the_next_migrate` |
| C2 | URS-CMP-015: read/create/submit/cancel/amend per regulated record type declared in code, applied exactly, defaults replaced, manual change undone on next migrate | `PERMISSIONS`, `HIGHER_LEVELS`, `declared_rows`, `sync_permissions` | `TestPermissionMatrix.test_every_regulated_business_record_type_has_a_declared_matrix`, `test_applied_permissions_are_exactly_the_declared_ones`, `test_a_manual_change_is_undone_on_the_next_migrate_and_audited`, `test_an_unchanged_matrix_writes_nothing`; `TestDailyWork.*`; `TestSupportingPermissions.*` |
| C3 | URS-CMP-016: no role deletes, shares or imports a regulated record | flag sets never include delete/share/import | `TestNoDeletion.test_no_job_may_delete_share_or_import_a_regulated_record` (all 12 jobs × 27 types) |
| C4 | URS-CMP-017: system administrators read but never create, submit, cancel or amend | `oversight()` gives System Manager VIEW | `TestAdministrators.test_system_administrators_read_but_never_change_regulated_records` (also refuses write) |
| C5 | URS-CMP-018: QC records and submits inspections, only QA corrects; operators record operations but cannot create, submit or cancel production records | Quality Inspection and Job Card rows | `TestSegregationOfDuties.test_inspectors_record_inspections_and_only_qa_corrects_them`, `test_operators_record_their_operations_only` |
| C6 | URS-CMP-034: whoever administers holds no business role | `separate_administration` (User validate) | `TestAdministrationIsSeparate.*` (4 tests, incl. the built-in Administrator left alone) |
| C7 | URS-CMP-035: after sign-out the next person starts on a clean sign-in page | `public/js/sign_out.js` | `cypress/integration/desk_scope.js` "[URS-CMP-035] starts the next person on a clean sign-in page" (browser only; no server test possible) |
| C8 | URS-CMP-036: every lookup a job's forms use can be searched, apart from the reviewed list; looking up never opens | `setup/lookups.py`, `SUPPORTING_PERMISSIONS` PICK rows | `setup/test_lookups.py` `TestEveryJobCanUseItsForms.test_only_the_reviewed_lookups_stay_closed`, `test_the_lookups_found_closed_in_the_pilot_now_search`, `test_picking_never_opens_the_record`, `test_maintenance_picks_a_task_through_the_forms_own_query`; `screen_sweep.js` searches every open lookup as each job (URS-PLT-009) |
| C9 | Each job can still do its daily work under least privilege | `DAILY_WORK` table in the test | `TestDailyWork.test_each_job_can_do_its_daily_work`, `test_jobs_do_not_do_each_others_work` |
| C10 | A job holding create without write only for records its transactions create; a service posts for a job what it never fills in | `CREATED_THROUGH_TRANSACTIONS`, `POSTED_BY_SERVICES`, `fills_in_by_hand` | `TestOwnFormsAreUsable.test_a_job_creates_without_a_form_only_what_its_transactions_create`, `test_a_service_posts_for_a_job_what_the_job_never_fills_in` (URS-PLT-009) |
| C11 | URS-CMP-015 (2026-10-12): no permission row made by hand on the app's own record types; none ever opens the evidence records to writing; one written past the check removed on migrate | `refuse_hand_added_permission`, `own_doctypes`, `SEE_ONLY`, `remove_hand_added_permissions` | `compliance/test_immutable_records.TestPermissionsOnTheAppsRecordTypes` (4), `compliance/test_quality_settings.test_a_hand_added_permission_does_not_last` — **not yet run** |
| C12 | URS-CMP-015 (2026-10-15): on every record type the matrix governs no permission row is added, changed, removed or reset by hand; the configuration still applies the matrix outside a migrate | `governed_doctypes`, `declared_by_code`, `refuse_hand_added_permission`, `refuse_hand_removed_permission`, `refuse_permission_change` | `setup/test_roles.TestPermissionsAreNotChangedByHand` (4), `setup/test_configuration.test_a_permission_added_by_hand_does_not_last` — **not yet run** |
| C13 | URS-CMP-014 (2026-10-15): a job profile is not changed or deleted by hand, through the form or the API; other role profiles are; the configuration still restores a job profile | `refuse_hand_changed_job_profile`, `sync_job_profile` | `setup/test_roles.TestJobProfilesAreNotChangedByHand` (4) — **not yet run** |

**Ambiguous / partial:**
- URS-CMP-015 "every regulated business record type": the matrix covers the 27 standard ERPNext types; this app's own regulated types are declared in their DocType JSON (section 2.6). The requirement is met as "declared in code", but not through one table.
- URS-CMP-035 is verified in the browser only, and only for the Administrator signing out from `/desk/user/Administrator`; the second sign-in by a different person is not part of the automated test (manual RP-08 covers it).
- URS-CMP-034: the rule is enforced on the User record. A role profile edited to include System Manager next to business roles would be caught only when a user holding it is saved (not tested; section 7).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, `scripts/create_pilot_site.sh`) with the demo users above, or a test site after `prepare_test_site` with users made by `qa/ui_users.py`. The Administrator account or `system.administrator@demo.example.com` is used for administration steps. Never use real personal data. Record the actual result and a screenshot per test.

### RP-01 A job profile grants exactly its roles (C1)
Role: System Administrator.
1. Open `/desk/role-profile/Stores` by address (not in the Users sidebar). Expected: the profile lists exactly **Stock User**.
2. Open `/desk/role-profile/Management`. Expected: Sales Manager, Purchase Manager, Stock Manager, Manufacturing Manager, nothing else.
3. Open **Users › User › stores@demo.example.com**. Expected: Role Profile = Stores; the roles table shows Stock User plus Frappe's automatic roles only.
4. Open **Users › Role**; search `QC Inspector` and `Operator`. Expected: both exist with *Desk Access* ticked.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 1–3.

### RP-02 The permission matrix is applied and restored (C2)
Role: System Administrator; a bench shell for step 3.
1. **Users › Permission Manager**; choose *Quality Inspection*. Expected: rows for QC Inspector (read, write, create, submit, report, print, select — no cancel/amend/delete/share/import), Quality Manager (adds cancel, amend, export), Quality Manager/Management/System Manager read rows; no row grants Delete, Share or Import.
2. In the Permission Manager tick **Delete** for QC Inspector on Quality Inspection. Expected (since 2026-10-15): *The permissions on Quality Inspection are part of the validated configuration and change only with a release of this app.*; after a reload the tick is clear. The same for *Add a New Rule*, a row's remove button and *Reset to defaults*, on Quality Inspection, Item, Batch and Account.
3. On a test site, change a row past the check from a bench console (`frappe.db.set_value("Custom DocPerm", <row>, "delete", 1)`), then run `bench --site <site> migrate`. Reload the Permission Manager. Expected: the Delete tick is gone; the rows are as in step 1.
4. As Administrator, open **Compliance › Audit Trail** (or the list `/desk/audit-event`) filtered by *Reference Doctype = Custom DocPerm*. Expected: an *Updated* event for the restored row.
5. (2026-10-12) In the Permission Manager choose *Quality Settings*, then *Electronic Signature*, and **Add a New Rule** or tick any box. Expected: *The permissions on <record type> are part of the validated configuration and change only with a release of this app.*; on *Electronic Signature* ticking **Create** names the flag: *Electronic Signature records are written only by the system; no role may be given create on them.* Nothing changes.
6. (2026-10-15) Open `/desk/role-profile/Stores`, add the role *Stock Manager*, **Save**. Expected: *The job profile Stores is part of the validated configuration and changes only with a release of this app.*; the profile still holds Stock User only. Deleting it is refused the same way. A new role profile of another name can still be made, changed and deleted.
Actual result: ____  Pass/Fail: ____

### RP-03 Nobody deletes, shares or imports a regulated record (C3)
1. As **Stores**: open a submitted Purchase Receipt. Expected: the form menu (⋯) offers no **Delete**; the sidebar has no **Share** control for the record (Frappe hides it without the share flag).
2. As **Stores**: open `/desk/purchase-receipt` list, select a draft receipt, open *Actions*. Expected: no **Delete** action.
3. As **QA Manager**: open `/desk/data-import/new` by address. Expected: refused or no regulated record type offered (QA has no import flag; the Data area is the administrator's).
4. As **System Administrator**: **Data › Data Import › New**, Document Type *Purchase Order*. Expected: the type is not offered / the import is refused (no role has import on Purchase Order).
Actual result: ____  Pass/Fail: ____

### RP-04 A system administrator reads but never changes a regulated record (C4)
Role: System Administrator.
1. Open **Buying › Purchase Order** (`/desk/purchase-order`). Expected: the list opens; no **+ Add Purchase Order** button.
2. Open an existing order. Expected: read-only form; no Save/Submit/Cancel/Amend.
3. From the browser console or an API client, `POST /api/resource/Purchase Order` with a minimal body. Expected: HTTP 403 *Not permitted*.
Actual result: ____  Pass/Fail: ____

### RP-05 Inspections: QC enters, only QA corrects (C5)
Preconditions: a received lot with its certificate verified (module 4/16 guides).
1. As **QC Inspector**: **Quality › Quality Inspection › New**; fill and **Submit**. Expected: submitted.
2. Still as QC Inspector: open the submitted inspection. Expected: no **Cancel** button.
3. As **QA Manager**: open it; **Cancel**; dialog *Reason for cancelling QI-…*; enter a reason; **Continue**. Expected: cancelled; then **Amend** is offered to QA.
4. As **Stores**: `/desk/quality-inspection` opens read-only (VIEW); `/desk/quality-inspection/new`. Expected: no usable new form (Stores has no create: Frappe reports the form as not permitted; exact wording not verified — the plain refusal of section 2.2 applies only to types the job cannot read at all).
Actual result: ____  Pass/Fail: ____

### RP-06 Operators record operations only (C5)
Preconditions: a work order in process with open job cards (pilot sample "in production").
1. As **Operator**: **Manufacturing › Job Card**; open an open card; add a *Time Logs* row (From Time, To Time, Completed Qty); **Save**. Expected: saved; the primary button never offers **Submit**.
2. As Operator: `/desk/work-order` opens read-only (VIEW); `/desk/work-order/new`. Expected: no usable new form (no create; Frappe's own not-permitted handling, wording not verified).
3. As Operator: `/desk/stock-entry`. Expected: refused *You are not permitted to open Stock Entry.*
4. As **Production Supervisor**: open the same job card; **Submit**. Expected: submitted, status Completed.
Actual result: ____  Pass/Fail: ____

### RP-07 Administration is separate from business roles (C6)
Role: Administrator.
1. **Users › User › system.administrator@demo.example.com**; add Role Profile *Management*; **Save**. Expected: refused *system.administrator@demo.example.com is a system administrator and cannot also hold Manufacturing Manager, Purchase Manager, Sales Manager, Stock Manager. Give the business work a separate account.*
2. New User `test.admin@example.test`, roles **System Manager** and **Purchase Manager** (roles table, no profile); Save. Expected: refused with the same message naming Purchase Manager.
3. Same user with **System Manager** only. Expected: saved. Cleanup: disable the user.
Actual result: ____  Pass/Fail: ____

### RP-08 Sign-out leaves a clean sign-in page (C7)
Roles: two demo users on one browser.
1. As **Stores**: open `/desk/purchase-receipt/<a receipt>`. Use the user menu's log-out entry; confirm *Are you sure you want to log out?*. Expected: the browser is at `/login` with no `?redirect-to=` in the address.
2. Sign in as **Operator**. Expected: the Operator's own desk (`/desk`), not the receipt, and no permission error.
3. As Operator, let the session expire or clear the `sid` cookie, then reload a form. Expected: Frappe's own `/login?redirect-to=…` (an expired session returns its own user to their page).
Actual result: ____  Pass/Fail: ____

### RP-09 Every lookup a job needs can be searched; picking never opens (C8)
1. As **Operator**: open a job card, *Time Logs* row, click the **Employee** lookup. Expected: the list of employees appears (no permission error).
2. Still as Operator: `/desk/employee`. Expected: refused *You are not permitted to open Employee.*
3. As **Stores**: new Purchase Receipt, field **Cost Center** on a line (a reviewed closed lookup). Expected: the lookup is refused — this is on the reviewed list `REVIEWED_CLOSED["Stores"]`, not a defect.
4. As **Maintenance**: new Asset Maintenance Log, choose an **Asset Maintenance**, then the **Task** lookup. Expected: tasks of that maintenance are listed.
Actual result: ____  Pass/Fail: ____

### RP-10 Lists and filters
1. As **Management**: `/desk/quotation` and `/desk/purchase-order`: filter by *Workflow State*; sort by Modified. Expected: lists open with the filters; the Export option is present (REVIEW/APPROVE), the Delete action absent.
2. As **Accounts**: `/desk/purchase-receipt`, `/desk/delivery-note` and `/desk/stock-entry` open read-only (VIEW); `/desk/work-order` refused *You are not permitted to open Work Order.* (no read).
Actual result: ____  Pass/Fail: ____

### Regression
- Run the screen sweep (`cypress/integration/screen_sweep.js`, 12 jobs) and the standard-flow journey (`standard_flow.js`); both passed on 2026-10-10 (section 8).

## 5. End-to-end business workflow

1. The **System Administrator** creates a person on **Users › User** with Role Profile *Stores*; the audit trail records the User change. The administrator holds no business role, so cannot themselves receive goods or approve anything.
2. **Stores** signs in, sees only the Buying and Stock areas (URS-PLT-008), receives a Purchase Receipt (CORRECT) that creates the lot (Batch create through the transaction).
3. **QC Inspector** submits the Quality Inspection (ENTER); **QA Manager** alone could cancel/amend it (APPROVE) and signs the Lot Release, whose service posts the Stock Entry under QA's account (POSTED_BY_SERVICES).
4. **Operator** logs time on the Job Card (write only); the **Production Supervisor** submits it.
5. **Management** approves the purchase order and the quotation price (module 48, APPROVE on those types); **Sales**/**Purchase** can only draft, amend and (Sales) cancel.
6. The person signs out; the next person on the terminal lands on `/login` and then on their own desk.
7. A hand change to the matrix or a job profile is refused (2026-10-15); after an upgrade, or a change written past the refusal straight to the database, `bench migrate` re-applies profiles and the matrix; differences are audited on Custom DocPerm.

Data touched: Role, Role Profile, Has Role, Custom DocPerm (configuration); User (hook); Audit Event (changes to those).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Declarations | `setup/roles.py`: `NEW_ROLES`, `JOB_PROFILES` (12), `FLAGS` (16 flags set explicitly), `PICK/VIEW/REVIEW/EDIT/ENTER/CORRECT/APPROVE`, `MANAGEMENT`, `oversight()`, `PERMISSIONS` (27 types), `HIGHER_LEVELS`, `SUPPORTING_PERMISSIONS`, `CREATED_THROUGH_TRANSACTIONS`, `POSTED_BY_SERVICES`, `ACCESS_CONTROL`, `SYSTEM_ADMINISTRATION`, `BUSINESS_ROLES` |
| Apply | `apply_roles()` ← `setup/install.py` `apply_configuration()` ← hooks `after_install`, `after_migrate`; `sync_job_profile`, `declared_rows`, `supporting_rows`, `sync_permissions` (Custom DocPerm rows exact; `frappe.clear_cache(doctype=…)` on change) |
| Hooks (`hooks.py`) | `User.validate` → `setup.roles.separate_administration`; `User.before_rename` → `compliance.audit.keep_user_id` and `User.on_trash` → `compliance.audit.keep_user_with_history` (module 50); `Custom DocPerm.before_validate` → `setup.roles.refuse_hand_added_permission` (2026-10-12), `Custom DocPerm.on_trash` → `setup.roles.refuse_hand_removed_permission` (2026-10-15); `Role Profile.validate` → `setup.roles.refuse_hand_changed_job_profile` (2026-10-15), `separate_administration_in_profile`; `Role Profile.on_trash` → `refuse_hand_changed_job_profile` |
| Desk | `public/js/sign_out.js` (bundle `meddevice_suite.bundle.js`): `redirect_to_login` → `/login` when `logged_out` |
| Lookups | `setup/lookups.py`: `REVIEWED_CLOSED`, `closed_lookups()`, `job_forms()`, `granted()`; served to the browser by `qa/ui_users.reachable` (`closed_lookups`) |
| Regulated scope | `compliance/audit_scope.py` `REGULATED_DOCTYPES` (34 incl. the 7 access-control types) |
| Routes | `/desk/user/<email>`, `/desk/role`, `/desk/role-profile/<job>` (by address), `/desk/permission-manager` (Users sidebar *Permission Manager*); standard REST `/api/resource/<DocType>` honours the matrix; page loader `/api/method/frappe.desk.desk_page.getpage` overridden by `setup/desk.py` `getpage` for the plain refusal *You are not permitted to open <DocType>.* |
| Pilot / tests | demo users `pilot/demo.py` `ensure_demo_user` (password from site_config `pilot_user_password`); browser-test users `qa/ui_users.py` `prepare_job_users` (`<job>@ui-tests.example.com`, password per run); server tests `setup/test_roles.py` `user_with_profile` (`<job>@job-profiles.example.com`) |
| Data flow | migrate → `apply_roles` → Role / Role Profile / Custom DocPerm rows (audited) → Frappe permission cache cleared → `frappe.has_permission` on every desk and API request; User save → `separate_administration` → refuse or save |

## 7. Defects, edge cases and risks

**Confirmed and fixed during development (from the git history, each with a test):** lookups the pilot found closed on 2026-10-07 (operator time logs, stock entry type, inspection parameters, maintenance tasks) now searchable (`9d7d097e`); sign-out redirect to the previous person's record (`f842cf51`); the record archive's create-only permission left a blank form (`TestOwnFormsAreUsable`); stores and production could not create lots or pick party accounts (`893e43eb`).

**Potential / unverified:**
- A Role Profile edited by hand to add System Manager beside business roles is refused on the profile's own save since 2026-10-10 (`separate_administration_in_profile`; `test_a_job_profile_cannot_combine_administration_with_business_roles`, which since 2026-10-15 uses a profile other than a job's, as a job profile is not edited by hand at all), and still when a user holding it is saved.
- `Role Profile` is not in the scoped Users sidebar; the administrator reaches it by address only. Not verified in the browser.
- Frappe's `Administrator` account is exempt from the separation rule by design (it installs the system); its use for daily work is a procedural control, not a system one.
- URS-CMP-035 is tested with the Administrator only; the second person's clean landing (RP-08 step 2) is manual.
- Printing: VIEW includes *print*, so a draft (unapproved) quotation or order can be printed by its job; whether the print carries a draft mark is Frappe's behaviour and not verified here (relevant to module 48).
- The `email` flag is granted to no role on regulated records; emailing a record from the desk is therefore not possible for anyone. Whether the client expects Sales to email quotations from the system is an open question (module 48 document, section 3).

## 8. Test results and verification summary

Automated results below were executed by the lead on 2026-10-10 on the current branch (local test site `test.localhost`); this document cites them and did not re-run anything.

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (includes `setup/test_roles.py`: 10 classes, 24 test methods counted in source; `setup/test_lookups.py`: 7) |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed: `desk_scope.js` "[URS-CMP-035] …", screen sweep as all 12 jobs, 18-step standard flow |
| Semgrep (Frappe rules + python correctness) | as in `.github/workflows/linter.yml` | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| GitHub CI (fresh site) | `.github/workflows/ci.yml` | in progress when this document was written |
| Manual browser tests RP-01…RP-10 | — | **not executed** (no manual tester session). The automated journey and sweep cover: every job's lists/new forms/lookups open (RP-09 steps 1, 4), Operator logs without Submit and the supervisor submits (RP-06 steps 1, 4), Sales/Purchase not offered Approve (module 48) |

2026-10-12 changes (section 1): tests written without a site and **not yet run**; RP-02 step 5 **not executed**.

Untested: RP-02 step 3 on a real `bench migrate` of a site changed by hand (the test calls `apply_configuration` directly); RP-08 step 2 with a second person; Data Import refusal (RP-03 steps 3–4).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| 12 job profiles with exactly their roles; QC Inspector and Operator roles created | Verified (automated) |
| Permission matrix applied exactly to 27 regulated types; hand change undone on migrate and audited | Verified (automated, `apply_configuration` called directly) |
| No delete/share/import for any job on any regulated type | Verified (automated) |
| System administrator reads only; cannot hold a business role | Verified (automated) |
| QC enters inspections, QA corrects; operators write job cards only | Verified (automated + journey) |
| Clean sign-in page after sign-out | Verified (browser, Administrator); second person's landing **Not tested** |
| Every lookup of each job's forms searchable except the reviewed list | Verified (automated + screen sweep) |
| Manual browser cases RP-01…RP-10 | **Not tested** |
| Role Profile reachable only by address | **Not tested** (section 7) |
| Hand-edited profile refused on save | Verified (automated, 2026-10-10) |
| Hand-made permission on the app's own record types refused; evidence records never opened to writing (2026-10-12) | **Not tested** (tests written, not yet run) |
| CI green on a fresh site for the final commit | **Pending** |
