# Tamper-evident audit trail (module 50) — feature document and browser test guide

Requirement: **URS-CMP-007, URS-CMP-008, URS-CMP-009, URS-CMP-010, URS-CMP-011, URS-CMP-012, URS-CMP-013, URS-CMP-031, URS-CMP-040** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-004 (signature chains survive a rename), URS-CMP-014 (QA reviews the evidence), URS-CMP-020/021 (workflow cancel asks a reason).
Code: `meddevice_suite/compliance/audit.py` (recording and the chain), `compliance/audit_scope.py` (which record types are regulated), `compliance/audit_reason.py` (reason for change), `compliance/audit_timeline.py` (review on the form, verification), `compliance/fingerprint.py`, `compliance/doctype/audit_event/`, `compliance/audit_anchor.py` and `compliance/doctype/audit_anchor/` (anchors, 2026-10-12), `public/js/audit_trail.js`, `public/js/audit_reason.js`, hooks in `meddevice_suite/hooks.py`.
State on 2026-10-10: built (`fbaba7af` 2026-10-06 onwards; edge cases of the robustness review closed in `31f756ac`, `2ffeb96e`, `9a5d2019` on 2026-10-07), server and browser suites green on the current branch; GitHub CI in progress (section 8).
Changed 2026-10-12 (foundation review, branch `fix/immutable-records`): an audit event is inserted only by the audit trail; no permission row may open it to writing; a user the trail names is never deleted, only disabled (21 CFR 11.300(b)). Tests: `compliance/test_immutable_records.py` — written, **not yet run** (section 8).
Changed 2026-10-12 (foundation review, branch `fix/audit-anchor-and-scope`): removing the newest audit events or signatures is detected by anchors of the whole trail (section 2.8, URS-CMP-040). Tests: `compliance/test_audit_anchor.py` — written, **not yet run** (section 8).

## 1. Feature overview

**Purpose.** Every create, change, submit, cancel, amend, discard, rename and delete of a regulated record is written automatically as an **Audit Event**: who (and the real user when impersonating), when (server time), which action, which record, and the old and new value of every changed field including child rows; creation and deletion keep the whole record. Cancelling, amending or changing a submitted record needs a reason that goes into the event. Events can never be edited or deleted. Each record's events form a fingerprinted chain that QA can verify from the record, and every hour the head of the whole trail is anchored in the database and in an append-only file, so that removing the newest entries is detected too; QA reviews, searches and exports the trail but cannot change it.

**Why it matters.** 21 CFR 11.10(e) requires a secure, computer-generated, time-stamped audit trail that does not obscure previously recorded information; ISO 13485 4.2.5 requires records to stay legible and retrievable; EU GMP Annex 11 §9 requires the reason for a change. Frappe's own Version log can be deleted and is not fingerprinted; this module replaces it for regulated records.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| Everyone with a business role | own role | Their actions are recorded without doing anything; they are asked a reason before cancelling, amending or changing a submitted record |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager | Reviews the trail on each record (timeline, **Audit Trail**, **Verify Integrity**), searches and exports the Audit Event list |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Same review rights as QA; cannot change events either |
| Management, Stores, Sales, … | — | No read on Audit Event: the form shows no timeline events and no buttons |

**Prerequisites.** None on the site: hooks on `*` and the DocType come with the app. A record type is audited from the moment it is regulated (section 2.1).

## 2. Detailed functional explanation

### 2.1 Which records are regulated (URS-CMP-013)

`audit_scope.is_regulated(doctype, module)` is true for (a) the standard record types named in `REGULATED_DOCTYPES` (masters: Item, BOM, Routing, Operation, Workstation, Supplier, Customer, Warehouse, Batch, Quality Inspection Template, Asset, Employee; transactions: Quotation, Material Request, Purchase Order, Purchase Receipt, Quality Inspection, Stock Entry, Stock Reconciliation, Work Order, Job Card, Serial and Batch Bundle, Pick List, Delivery Note, Packing Slip, Asset Maintenance Log, Asset Repair; access control: User, Role, Role Profile, User Permission, Custom DocPerm, System Settings, Stock Settings) and (b) **every DocType whose module belongs to this app**, except the evidence itself (`SELF_RECORDING`: Audit Event, Electronic Signature, and since 2026-10-12 Audit Anchor). Child tables are recorded inside their parent's events. Accounting documents are left to India Compliance's audit trail (by design, documented in the module docstring).

### 2.2 What is recorded and when (URS-CMP-007, URS-CMP-008)

Hooks on every DocType (`hooks.py`, `doc_events["*"]`): `before_insert`, `before_change` (Frappe runs it from `db_set`), `before_cancel`, `before_update_after_submit`, `on_change`, `on_trash`, `after_rename`. Each returns at once for non-regulated types.

| Action (label in the **Action** field) | When | **Changes** content |
| --- | --- | --- |
| Created | first save of a draft | `snapshot`: the whole record |
| Submitted | submit, or a record created already submitted | field diff, or `snapshot` when created submitted |
| Amended | first save of an amendment (`amended_from` set) | `snapshot`; reason required |
| Updated | save of a draft with changes; a value `db_set` by code after a save | `changed` `[field, old, new]`, `added` / `removed` `[table, row]`, `row_changed` `[table, row name, [[field, old, new]…]]` |
| Updated after submission | save of a submitted record | diff; reason required unless caused by another record |
| Cancelled | cancel | diff (`docstatus` 1 → 2); reason required |
| Discarded | discard of a draft | diff |
| Renamed | rename | `old_name`, `new_name`, `merged` |
| Deleted | delete | `snapshot` |

Exactly once per action: a value code sets on the record while it is being saved or submitted (a status set in `on_submit`) is folded into that one event; a `db_set` after the save records only what changed since the last event; saving without a change records nothing; code writing to a record never stored records nothing. Values are compared as stored (dates, times and numbers by value, blank and None equal). Not recorded: values ERPNext recalculates on another record with `frappe.db.set_value` (documented limitation, section 2.7).

Each event stores **Document Type**, **Document**, **Document Name at Event**, **Action**, **User**, **Impersonated By** (from `frappe.session.data.impersonated_by`), **Date and Time** (server `now_datetime`), **Reason**, **Changes** (JSON), **Previous Event**, **Event Fingerprint**. A `caused_by` entry in Changes names the record whose action changed this one (a receipt marking its order received; a job card rolling up to its work order); a `command` entry names the status command used (Close, Hold, Stop, Re-open) when run on its own.

### 2.3 Reason for change (URS-CMP-011)

**On the desk** (`public/js/audit_reason.js`): before Frappe's own cancel, before a workflow action whose next state is cancelled (module 48), before saving an amendment, and before **Update** of a submitted record, a dialog asks:

| Element | Label / text |
| --- | --- |
| Title | **Reason for cancelling <name>** / **Reason for amending <name>** / **Reason for changing <name>** |
| Field **Reason** (required) | description *Recorded in the audit trail with your name and the time.* |
| Button | **Continue** |

Closing the dialog leaves the record unchanged. The reason is staged on the server (`audit_reason.stage_reason`, POST, 15 minutes, per user and record; needs the action's permission: cancel/amend/write) and taken when the action runs. Only submittable regulated record types ask (`requires_reason`).

**On the server**, however the action arrives (desk, REST, background job, `ignore_validate`): `reason_for` refuses the action with *Give a reason to cancel|amend|change <record type> <name>. It is recorded in the audit trail.* (title *Reason required*) when there is none. Other refusals: *Enter a reason.*, *Keep the reason within 1000 characters.*, *Write the reason without the characters < and >.*, *Unknown action <x>.*, *<record type> is not a regulated record type that needs a reason.* Code in this app sets `doc.flags.audit_reason` before it cancels or changes a record. Records cancelled by **Cancel All** or by ERPNext's dependent cancellation take the reason of the record that caused them, and their event's `caused_by` names it; a submitted record changed while another record is saved needs no reason and names that record; a submitted record changed by hand still needs one. One reason explains one action.

### 2.4 Permanence (URS-CMP-009, URS-CMP-031)

`AuditEvent.validate` refuses every save after the first (*An audit event cannot be changed.*) and `on_trash` refuses deletion (*An audit event cannot be deleted.*), both permission errors, for every user including Administrator and `ignore_permissions`. The DocType is `in_create`; no role has create/write/delete; `ignore_links_on_delete` lets the audited record itself be deleted (recorded as Deleted) without touching its events. Renaming a User is refused (`audit.keep_user_id`).

Since 2026-10-12 (foundation review):
- An audit event cannot be inserted past the audit trail: `AuditEvent.before_insert` refuses one without the mark `audit.record` sets in the record's flags (*An audit event is recorded only by the audit trail.*), which a request cannot set, even with permissions and validation skipped. Before, a system manager could grant create in Role Permissions Manager and add an entry in another person's name; that grant is refused too (`setup/roles.refuse_hand_added_permission`: no row on the Audit Event may grant more than select, read, report, export or print, even on migrate).
- A user the trail names is never deleted (`audit.keep_user_with_history`, User `on_trash`, which runs before Frappe's link check that `ignore_links_on_delete` would otherwise let pass): one with an event as the user or as the real user acting for someone, one who signed, or one who owns a record of a regulated type (records made before the trail was installed). *<user> has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.* Administrator and Guest are skipped (Frappe never deletes them). Frappe's own `User.on_trash` runs first and signs the user out before the refusal; the rest of the deletion is rolled back. Who created a record and when (`owner`, `creation`) cannot be changed through the form or the API (verified by `test_approvals.TestAttributionIsPermanent`; enforced by Frappe's save, no app code — section 7).

### 2.5 The chain and its verification (URS-CMP-010)

- **Event Fingerprint** = SHA-256 of the canonical JSON of `[previous event's fingerprint or "", Document Type, Document Name at Event, Action, User, Impersonated By, Date and Time, Reason, Changes]`.
- **Previous Event** = the record's newest event at recording time, found by the links (the event no other event names as its predecessor) under `FOR UPDATE` (`fingerprint.chain_head`), so a clock set back does not reorder the chain and two writers serialise.
- `audit.verify_events(doctype, name)` reads the record's events in `event_at, creation` order and reports, per event: *<event>: the event before it is missing*, *<event>: a second event claims <prev> as its predecessor*, *<event>: content does not match its fingerprint*. An empty list means intact. It detects an altered entry, a removed entry, and an entry inserted before the newest. A record's newest entries removed, or its whole history, leave a shorter chain that still verifies: since 2026-10-12 the anchors detect that (section 2.8). A forged entry appended at the very end with a correct fingerprint is protected by the rule that only the audit trail inserts events (URS-CMP-009), access control and the periodic archives (module 54), not by this check.
- `audit_timeline.verify_record` (whitelisted) adds `signatures_intact` from module 49 and, since 2026-10-12, `anchors`: what the anchors show about the whole trail (section 2.8); it refuses non-reviewers with *You cannot review the audit trail of <record type>.*

### 2.6 Review on the record, search and export (URS-CMP-012)

For users who may read Audit Event (Quality Manager, System Manager) and only on regulated, non-child record types:

- **Timeline** (`additional_timeline_content`): the newest 50 events, newest first, each as **<Action> by <full name>** (or *<impersonator>, acting as <user>*) with a **Details** link to the event, and up to 5 lines: *Reason: …*, *Caused by <type> <name>*, *<Field label>: <old> → <new>* (values cut at 80 characters, blank shown as *empty*), *<Table label>: n rows added, n changed, n removed*, *Renamed from a to b*, then *and N more*. Values are escaped, never rendered as markup.
- Buttons under **View**: **Audit Trail** (opens `/desk/audit-event` filtered by Document Type and Document) and **Verify Integrity** (green *Integrity verified* / *The audit trail and the signatures of <name> are intact.*, or red *Integrity problem found* listing the problems and *The signatures on this record do not verify.*).
- The **Audit Event** list (`Compliance › Audit Event`, `/desk/audit-event`): standard filters on Document Type, Document, Action, User, Date and Time; sort `event_at desc`; **Menu › Export** (export permission). Indexes: `(reference_doctype, reference_name, event_at)`, `(user, event_at)`, `(event_at)`, `(previous_event)`.

### 2.7 What is intended but not implemented

- Values ERPNext writes to *another* record with `frappe.db.set_value` (per-received percentages, statuses recalculated from linked documents) are not recorded; the transaction that caused them is. Documented in `audit.py`; the requirement's "values that code writes through the record itself" is met, derived values on other records are out of scope by design.
- Accounting documents (invoices, payments, journal entries) are not in `REGULATED_DOCTYPES` (left to India Compliance).
- Export is the list's standard export (CSV/Excel of the chosen columns); there is no dedicated audit report with the Changes JSON rendered per field.
- A forged tail entry is not detectable by verification (section 2.5; accepted in the requirement text).
- An integrity problem found by the hourly anchoring goes to the Error Log only; no alert reaches QA yet (module 51 would need a new alert).
- Impersonation is recorded when Frappe's session carries `impersonated_by`; no test logs in through Frappe's real impersonation screen (unit-level patch only).

### 2.8 Anchors: removing the newest entries is detected (URS-CMP-040)

**The gap.** Each record's chain proves that every event follows the one before it. Deleting a record's newest N events (or its whole history) at database level leaves a shorter chain that still verifies; the same holds for a record's electronic signatures. 21 CFR 11.10(e) requires that a change never obscures what was recorded, and ALCOA+ that the record is complete and enduring, so the removal itself must be detectable.

**The design.** An **Audit Anchor** records the head of a whole trail (Audit Event, Electronic Signature) at one moment: the newest entry by `creation`, its fingerprint, the time it was recorded, and the number of entries up to it. Each anchor is fingerprinted with the one before it (`previous_anchor`, `anchor_hash`).

- **When:** every hour (`scheduler_events["hourly"]` → `audit_anchor.anchor_trail`), so at most an hour of entries is ever unanchored, and on request (**Compliance › Audit Anchor › Anchor Now**, `audit_anchor.anchor_now`, POST, Quality Manager and System Manager). A trail with nothing new since its last anchor is not anchored again.
- **Where, and why twice:** a database-level deletion of audit rows can delete anchor rows with them, so each anchor is (1) an Audit Anchor record, protected as the Audit Event is (only the service inserts, marked in the document's flags; no role may create, write or delete; `validate` and `on_trash` refuse every change and deletion), and (2) a JSON line appended to `sites/<site>/private/files/audit-anchors.jsonl`, which the app opens only with `O_APPEND` and never rewrites, and which Frappe never serves (no File record names it). Removing entries unreported then needs write access to the server's file system as well as the database. The line is written after the transaction commits (`frappe.db.after_commit`), so a rolled-back anchor never reaches the file. Further copies: the site's file backups and the period archives of module 54 (anchors are archived as records). On the production server the file can be made append-only for every user with `chattr +a` (an operating-system control outside the app; recommended, not applied by code).
- **The count is carried forward:** a new anchor records at least the previous anchor's count plus the entries added since, so an anchor taken after entries were removed keeps reporting them instead of recording the shorter trail as the new truth. An entry whose transaction commits after an anchor with an earlier time is picked up by the next full count.
- **Verification** (`audit_anchor.verify_trail`, shown under **Verify Integrity** on every record after *Across the whole audit trail:*, and by **Verify Audit Trail** on the Audit Anchor list) compares the newest anchor of each trail, from the database and from the end of the file, with the trail:
  - *<trail>: entry <name>, the newest when anchor <anchor> was recorded, is missing: the newest entries have been removed.*
  - *<trail>: entry <name> has changed since anchor <anchor> was recorded.*
  - *<trail>: <n> of the <count> entries recorded up to anchor <anchor> are missing.*
  - *Anchor <anchor> of the <trail> is in the anchor file but no longer in the database.* / *Anchor <anchor> differs from its copy in the anchor file.* / *Anchor <anchor>: content does not match its fingerprint.* / *Anchor <anchor>: the anchor before it is missing.*
  - *The anchor file audit-anchors.jsonl is missing.* (anchors exist in the database) / *The anchor file audit-anchors.jsonl has a damaged line.*
- The hourly job verifies before it anchors and writes any problem to the **Error Log** (*Audit trail integrity problem*).
- **Retention:** anchors are compliance evidence, 10 years (`retention.py`), as long as the entries they count.
- **Not detected:** entries removed after the newest anchor and before the next one (at most an hour); entries removed together with the anchors in the database *and* the lines in the file (needs file-system access; mitigated by backups, archives and `chattr +a`).
- **Scale:** anchoring and verification read the newest entry and count entries through Frappe's index on `creation`; the file is read from its end in 64 KB blocks only; one extra index `(ledger, creation)` on Audit Anchor.

## 3. Requirements and acceptance criteria

| # | Requirement / criterion | Implementation | Automated verification (`compliance/test_audit*.py`) |
| --- | --- | --- | --- |
| C1 | URS-CMP-007: every action recorded automatically and exactly once, incl. values code writes through the record | `audit.before_insert/before_change/on_change/on_trash/after_rename`, `creation_event`, `differences` | `TestEveryActionIsRecorded` (12 tests, e.g. `test_create_change_submit_update_after_submit_and_cancel`, `test_amendment`, `test_rename`, `test_deletion`, `test_saving_without_a_change_records_nothing`, `test_record_types_that_are_not_regulated_are_not_audited`); `TestValuesSetByCode` (9 tests, e.g. `test_value_set_while_submitting_is_part_of_the_one_submit_event`, `test_discarding_a_draft`) |
| C2 | URS-CMP-008: user, real user, server time, action, record, old/new of every field incl. child rows; whole record on create/delete | `record`, `differences`, `field_changes`, `row_content`, `same`/`as_value` | `TestEventContent` (4: `test_records_who_when_and_old_and_new_values`, `test_creation_and_deletion_keep_the_whole_record`, `test_child_table_rows_added_changed_and_removed_are_recorded`, `test_the_real_user_is_recorded_when_acting_for_someone_else`); `TestValuesComparedAsStored` (unit) |
| C3 | URS-CMP-009: never edited/deleted, never overwrite earlier entries | controller `AuditEvent`, `in_create`, permissions | `TestEventsAreImmutable` (3); `test_audit_review.TestSearchAndExport.test_reviewer_may_export_but_never_change` |
| C4 | URS-CMP-010: fingerprinted chain; altered/removed/inserted-before-newest entry detected by a verification reviewers run on the record | `event_fingerprint`, `last_event`/`chain_head`, `verify_events`, `verify_record`, `audit_trail.js` | `TestChain` (6: `…altered…`, `…removed…`, `…inserted…`, `test_a_clock_set_back_does_not_break_the_chain`, `test_renaming_the_record_keeps_its_chain_verifiable`); `TestReviewersVerifyARecord` (3); Cypress `audit_trail.js` "[URS-CMP-010] verifies the record's audit trail and signatures on request" |
| C5 | URS-CMP-011: reason for cancel/amend/update after submit, stored in the event; caused-by named; status command recorded | `audit_reason.py`, `audit_reason.js`, `on_change` (`caused_by`, `command`) | `TestReasonIsRequired` (8), `TestReasonGivenOnTheForm` (7), `TestActionsCausedByAnother` (9, e.g. `test_a_status_command_is_recorded_with_the_command_used`, `test_cancel_all_linked_without_a_reason_is_refused`); Cypress `audit_trail.js` four "[URS-CMP-011] …" tests, `approvals.js` "[URS-CMP-011] cancelling an approved order through the workflow asks for a reason", `standard_flow.js` "[URS-PLT-007] [URS-CMP-011] [URS-CMP-020] Management cancels an approved purchase order only with a reason, which the audit trail keeps" |
| C6 | URS-CMP-012: reviewers search by record/user/action/date, see it on the record, export, cannot change | `audit_timeline.py`, Audit Event permissions, indexes | `TestRecordTimeline` (11), `TestSearchAndExport` (4), `TestQAReviewsTheEvidence` (2), `test_audit.TestReviewStaysFastAtScale`; Cypress `audit_trail.js` "[URS-CMP-012] shows the record's audit trail and opens its full history" |
| C7 | URS-CMP-013: regulated types declared in code incl. every type this app builds; audited from declaration | `audit_scope.py` | `TestScope` (3), `TestScopeRules` (3) |
| C8 | URS-CMP-031: creator and creation time never change via form or API | Frappe's save (no app code); covered by test | `compliance/test_approvals.TestAttributionIsPermanent` (2) |
| C10 | URS-CMP-040: removing the newest entries of the audit trail or the signatures is detected; anchors hourly and on request, in the database and an append-only file; anchors made only by the service, never changed or deleted | `audit_anchor.py`, `AuditAnchor` controller, `hooks.py` hourly job, `audit_timeline.verify_record` (`anchors`), `audit_trail.js`, `audit_anchor_list.js` | `compliance/test_audit_anchor.py`: `TestRemovedEntriesAreDetected` (6, incl. `test_removing_a_records_newest_events_is_detected` deleting the newest events with `frappe.db.delete`), `TestAnchorsAreKeptOutsideTheDatabase` (6), `TestAnchoring` (5), `TestAnchorsArePermanent` (3); `test_immutable_records.TestPermissionsOnTheAppsRecordTypes.test_every_evidence_record_type_is_read_only_for_every_role` — **not yet run** |
| C9 | Foundation review 2026-10-12: no event past the audit trail (URS-CMP-009); no hand-made permission on it (URS-CMP-015); a user the trail names is disabled, never deleted (URS-CMP-006, 21 CFR 11.300(b)) | `AuditEvent.before_insert`, `audit.record` flag, `roles.refuse_hand_added_permission`, `audit.keep_user_with_history`/`has_history` | `compliance/test_immutable_records.py`: `TestAuditEventsComeOnlyFromTheTrail` (3), `TestPermissionsOnTheAppsRecordTypes` (4), `TestUserIdsInTheTrailAreNeverDeleted` (6) — **not yet run** |

**Partial / by design:** C1 for derived values on other records (section 2.7). **Ambiguous:** URS-CMP-012 "export" — met by the list export; confirm with the client whether a formatted audit report is expected. **Unverified mechanism:** C8 relies on platform behaviour whose Frappe code path was not located in this review (test passes).

## 4. Step-by-step browser testing guide

Site: pilot (`pilot.localhost`) or a test site. Use a Purchase Order of the demo supplier as the regulated record (Purchase raises it, Management approves, module 48), or any submittable regulated record the tester's job may cancel. Record actual results and screenshots (dialog, timeline, Audit Event form, Verify Integrity message).

### AT-01 Actions are recorded automatically (C1, C2)
Role: Purchase (`purchase@demo.example.com`) acts; QA Manager reviews. Preconditions: the demo supplier is approved by QA for RM-SILICONE (module 4, `approved-suppliers.md` SA-01), or the order is refused before it is saved.
1. As Purchase: **Buying › Purchase Order › New** for *Silicone Compounds (Demo)*, one line RM-SILICONE qty 10, Required By = today + 7; **Save**. Then change qty to 12, **Save**.
2. As QA Manager open the order. Expected: timeline shows **Updated by Purchase** with *Quantity … rows changed* line (or *Items: 0 rows added, 1 changed, 0 removed*) and **Created by Purchase**; each has a **Details** link.
3. Click **Details** of the Updated event. Expected: Audit Event form, all fields read-only: Document Type *Purchase Order*, Document, Action *Updated*, User = Purchase's ID, Date and Time, Changes JSON with `row_changed` showing `qty` 10 → 12, Previous Event = the Created event, Event Fingerprint (64 hex).
4. Open the Created event. Expected: Changes holds `snapshot` with the whole order including its item row.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 2–4.

### AT-02 Reason before cancelling; no reason leaves the record unchanged (C5)
Role: Management (`management@demo.example.com`). Preconditions: the order of AT-01 sent for approval by Purchase and approved by Management.
1. Open the approved order › **Actions › Cancel**. Expected: dialog **Reason for cancelling <name>** with field **Reason** (*Recorded in the audit trail with your name and the time.*) and button **Continue**.
2. Click **Continue** with Reason empty. Expected: *Missing Values Required* on Reason; the dialog stays; the order stays Approved/submitted.
3. Close the dialog (×). Expected: no change; status still Approved.
4. **Cancel** again, Reason `Supplier cannot deliver`, **Continue**; confirm Frappe's *Permanently Cancel* with **Yes** if shown. Expected: order Cancelled.
5. As QA Manager: order › **View › Audit Trail**. Expected: list `/desk/audit-event` filtered to this order; events Created, Updated, Submitted (or Updated after submission for the workflow moves), Cancelled; the Cancelled event's Reason = `Supplier cannot deliver`.
Actual result: ____  Pass/Fail: ____

### AT-03 Reason before changing a submitted record and before an amendment (C5)
Role: Management (may update after submit) and Purchase (amends).
1. As Management on a submitted order change a field allowed after submit (e.g. **Order Confirmation No**), click **Update**. Expected: dialog **Reason for changing <name>**; after Reason `Confirmation received` + **Continue** the update saves.
2. As Purchase on the cancelled order of AT-02 click **Amend**, then **Save**. Expected: dialog **Reason for amending <name of the cancelled order>**; after Reason `Quantity corrected` + **Continue** the amendment `…-1` is saved as a draft.
3. As QA Manager: Audit Trail of the amendment. Expected: first event **Amended** with Reason `Quantity corrected` and a snapshot.
Actual result: ____  Pass/Fail: ____

### AT-04 The API cannot skip the reason (C5)
Role: Management, browser console on the desk.
1. `frappe.call("frappe.client.cancel", {doctype:"Purchase Order", name:"<approved order>"})`. Expected: *Reason required — Give a reason to cancel Purchase Order <name>. It is recorded in the audit trail.*; order unchanged.
2. `frappe.xcall("meddevice_suite.compliance.audit_reason.stage_reason", {doctype:"Purchase Order", name:"<name>", action:"cancel", reason:"from <A> to <B>"})`. Expected: *Write the reason without the characters < and >.*
3. Stage a valid reason, then cancel through the API as in step 1. Expected: cancelled; the event carries the staged reason. Repeat the cancel call on another approved order without staging. Expected: refused (a reason explains one action only).
Actual result: ____  Pass/Fail: ____

### AT-05 Events cannot be changed or deleted (C3)
Role: System Administrator (`system.administrator@demo.example.com`) and Administrator.
1. **Compliance › Audit Event** › open any event. Expected: no editable field, no Save, no **New** in the list.
2. Console: `frappe.client.set_value("Audit Event", "<name>", "reason", "x")`. Expected: *An audit event cannot be changed.*
3. Select the row in the list › **Actions › Delete**. Expected: *An audit event cannot be deleted.* Same as Administrator.
Actual result: ____  Pass/Fail: ____

### AT-06 Verify Integrity, intact and tampered (C4)
1. As QA Manager open any regulated record with history › **View › Verify Integrity**. Expected: green **Integrity verified**, *The audit trail and the signatures of <name> are intact.*
2. Test site only, database access: change `reason` of one of its events in `tabAudit Event`; verify again. Expected: red **Integrity problem found**, *<event>: content does not match its fingerprint*.
3. Test site only: delete an event that is not the newest from the table; verify. Expected: *<event>: the event before it is missing*.
Actual result: ____  Pass/Fail: ____  Cleanup: discard the test site or restore the rows.

### AT-07 Who sees the trail (C6)
1. As Management open the order of AT-01. Expected: timeline shows only Frappe's own comments/activity, no **Audit Trail** / **Verify Integrity** under View.
2. As Management visit `/desk/audit-event`. Expected: refused plainly (no permission; no Compliance area on the desk).
3. As QA Manager: **Compliance › Audit Event** list; filter User = Purchase's ID, Action = Updated, Date and Time between today and today; sort by Date and Time. **Menu › Export** (CSV). Expected: filters work; the export downloads the listed events.
4. Console as QA Manager: `frappe.xcall("meddevice_suite.compliance.audit_timeline.verify_record", {doctype:"Purchase Order", name:"<name>"})` returns `{audit_trail: [], signatures_intact: true}`; as Management the same call. Expected: *You cannot review the audit trail of Purchase Order.*
Actual result: ____  Pass/Fail: ____

### AT-08 Actions caused by another record; status commands (C5)
Role: Stores / Management as needed.
1. Receive goods against a submitted order (Purchase Receipt submitted). As QA Manager check the order's Audit Trail. Expected: an **Updated after submission** event on the order with no reason and *Caused by Purchase Receipt <name>* in the timeline (per-received fields changed by ERPNext).
2. On a submitted order run **Status › Close** (Management). Expected: an Updated after submission event (status → Closed) whose Changes contain a `command` entry naming the server method the Close button called (the exact ERPNext method string was not verified in this review; record what appears).
Actual result: ____  Pass/Fail: ____

### AT-09 Rename, delete and user ID (C1, C3)
1. As QA Manager rename a draft Item (**Menu › Rename**). Expected: Audit Event **Renamed** with *Renamed from A to B* in the timeline; **Verify Integrity** still green.
2. As System Administrator delete a draft regulated record no role protects (a test DocType on a test site). Expected: Audit Event **Deleted** with a snapshot; the events remain after the record is gone.
3. Rename a User. Expected: *User IDs are permanent in the audit trail. Create a new account instead of renaming <user>.*
4. (2026-10-12) As System Administrator delete a demo user who has worked on records. Expected: *<user> has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.*; untick **Enabled** and save: saved. Delete a user just created who has done nothing: deleted.
5. (2026-10-12) From the browser console: `frappe.xcall("frappe.client.insert", {doc: {doctype: "Audit Event", reference_doctype: "Item", reference_name: "<item>", action: "Updated", user: "qa.manager@demo.example.com"}})`. Expected: *An audit event is recorded only by the audit trail.* In **Role Permissions Manager** › *Audit Event*, adding a rule or ticking **Create**. Expected: refused (module 47, `roles-and-permissions.md`).
Actual result: ____  Pass/Fail: ____

### AT-10 Removing the newest entries is detected (C10) — 2026-10-12
Test site only, database access. Role: QA Manager.
1. **Compliance › Audit Anchor** › **Anchor Now**. Expected: an alert *Audit Event: anchor ANC-…, <n> entries* (and one for Electronic Signature if any signature exists); the list shows the new anchors, read-only, with no **New** button. On the server: `sites/<site>/private/files/audit-anchors.jsonl` ends with the same anchors.
2. Open a regulated record with several events › **View › Verify Integrity**. Expected: green **Integrity verified**.
3. Delete the record's newest two rows from `tabAudit Event` (`DELETE … ORDER BY creation DESC LIMIT 2`). **Verify Integrity** again. Expected: red **Integrity problem found** with *Across the whole audit trail:* and *Audit Event: entry <name>, the newest when anchor <anchor> was recorded, is missing: the newest entries have been removed.* and *Audit Event: 2 of the <n> entries recorded up to anchor <anchor> are missing.*; the record's own chain shows no problem.
4. Also delete the newest row from `tabAudit Anchor`; **Verify Audit Trail** on the Audit Anchor list. Expected: additionally *Anchor <anchor> of the Audit Event is in the anchor file but no longer in the database.*
5. Console: `frappe.client.delete` / `set_value` on an Audit Anchor. Expected: *An audit anchor cannot be deleted.* / *An audit anchor cannot be changed.* In Role Permissions Manager, give a role **Create** on Audit Anchor. Expected: refused.
6. As Sales: `frappe.xcall("meddevice_suite.compliance.audit_anchor.anchor_now")`. Expected: permission error.
Actual result: ____  Pass/Fail: ____  Cleanup: discard the test site.

### Regression
- The full journey (`standard_flow.js`) and the approvals journey (`approvals.js`) still run: every record they touch is regulated, so any double or missing event would surface as a failing reason dialog or a failed cancel.

## 5. End-to-end business workflow

1. **Purchase** raises a Purchase Order → *Created*; sends for approval → *Updated* (workflow state); **Management** approves → *Submitted* (module 48 alert to Purchase, module 51).
2. **Stores** receives on a Purchase Receipt → receipt *Created*/*Submitted*; the order gets *Updated after submission* with `caused_by` = the receipt.
3. **QC** inspects, **QA** releases the lot with a signature (module 49) → Lot Release *Created*, *Submitted*; Electronic Signature chained to the record.
4. **Management** cancels an order that cannot be fulfilled → reason dialog → *Cancelled* with the reason; Purchase amends → *Amended* with its reason.
5. **QA** (monthly review or audit) opens the records, reads the timeline, runs **Verify Integrity**, exports the Audit Event list for the period; module 54's archive copies every event with its record.

Cross-module data: Audit Event (new, one per action), the reason cache (15 minutes), Activity Log (login history, module 54), Electronic Signature (module 49, verified together).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocType | `Audit Event` (autoname hash, `in_create`, sort `event_at desc`); permissions: System Manager, Quality Manager read/report/export/print only; indexes in `audit_event.on_doctype_update` |
| Recording | `compliance/audit.py`: `before_insert`, `before_change`, `before_cancel`, `before_update_after_submit`, `on_change`, `on_trash`, `after_rename`, `keep_user_id`, `should_audit`, `creation_event`, `differences`, `field_changes`, `row_content`, `same`, `as_value`, `record`, `last_event`, `event_fingerprint`, `verify_events`, `as_data`; `ACTION_FOR` maps Frappe's `_action` to labels |
| Scope | `compliance/audit_scope.py`: `REGULATED_DOCTYPES`, `SELF_RECORDING`, `is_regulated(doctype, module)` (`frappe.local.module_app`) |
| Reason | `compliance/audit_reason.py`: `requires_reason` (whitelist), `stage_reason` (POST), `reason_for`, `saved_around`, `saving`, `records_being_saved` (call-stack walk of `insert`/`_save`/`run_before_save_methods`/`run_post_save_methods`), `reason_used`, `action_finished`, `linked_cancellation`, `take_staged`, `staged_key` (`meddevice_suite:audit_reason:<user>:…`), `clean` (≤1000 chars, no `<`/`>`), `verb`; constants `STAGED_ACTION`, `PERMISSION_FOR`, `STAGE_SECONDS` = 900 |
| Review | `compliance/audit_timeline.py`: `onload` (sets `__onload.audit_trail`), `verify_record` (whitelist), `timeline_content` (limit 50), `shows_audit_trail`, `describe`, `change_lines`, `shown` (80 chars) |
| Hooks (`hooks.py`) | `doc_events["*"]`: `before_insert`, `before_change`, `before_cancel`, `before_update_after_submit` (after `approvals.before_update_after_submit`), `onload` (`audit_timeline.onload`), `on_change`, `on_trash`, `after_rename`; `User.before_rename` → `keep_user_id`; `User.on_trash` → `keep_user_with_history` (2026-10-12); `additional_timeline_content["*"]` → `timeline_content`; `ignore_links_on_delete = ["Audit Event"]` |
| API routes | `POST /api/method/meddevice_suite.compliance.audit_reason.stage_reason` (`doctype`, `name`, `action` ∈ cancel/amend/update_after_submit, `reason`); `GET …audit_reason.requires_reason?doctype=`; `GET …audit_timeline.verify_record?doctype=&name=`; `/api/resource/Audit Event` read-only |
| Desk | `public/js/audit_reason.js` (wraps `frm.savecancel`, `frm.states.handle_workflow_action`, `frm.save`), `public/js/audit_trail.js` (**View › Audit Trail**, **View › Verify Integrity**); list `/desk/audit-event`, and since 2026-10-12 `/desk/audit-anchor` (`audit_anchor_list.js`: **Anchor Now**, **Verify Audit Trail**), in the **Compliance** area (`workspace_sidebar/compliance.json`; Quality Manager and System Manager, `setup/desk.py`) |
| Chain | event fingerprint = `sha256([previous_hash, reference_doctype, document_name_at_event, action, user, impersonated_by, str(event_at), reason, changes])`; head found by `fingerprint.chain_head("Audit Event", "previous_event", "event_hash", …)` with `FOR UPDATE`; verification walks `event_at asc, creation asc` and recomputes |
| Anchors (2026-10-12) | DocType `Audit Anchor` (`ANC-YYYY-######`, `in_create`; System Manager and Quality Manager read/report/export/print only; index `(ledger, creation)`); `compliance/audit_anchor.py`: `anchor_trail` (hourly), `anchor_now` (POST, reviewers), `verify_audit_trail` (reviewers), `anchor_ledger`, `newest_anchor`, `newest_entry`, `count_entries`, `anchor_fingerprint`, `append_to_file` (after commit, `O_APPEND`), `verify_trail`, `check_ledger`, `check_stored`, `check_against_database`, `newest_in_file`; file `sites/<site>/private/files/audit-anchors.jsonl`, one JSON object per line; anchor fingerprint = `sha256([previous anchor's fingerprint, ledger, head_entry, head_fingerprint, str(head_created), entry_count, str(anchored_at)])` |
| Retention / archive | "Compliance evidence" 10 years (`retention.py`, Audit Anchor included); each record's `audit_events` copied into its archive file; deletions archived from their *Deleted* event (`archive.py`) |
| Data flow | Frappe save → hooks → `on_change` → `differences(before, after)` → `record()` → lock head → insert Audit Event → `action_finished`; reason: dialog → `stage_reason` → cache → `reason_for` at `before_cancel`/`before_update_after_submit`/`before_insert` → event's Reason |

## 7. Defects, edge cases and risks

**Confirmed and fixed (git history, all with tests):** a value set by code during a save produced a second event (`7e292be0`); a receipt's update of its order asked Stores for a reason (`2ffeb96e`, `9a5d2019`: `caused_by` instead); the newest event was found by time, so a clock set back broke the chain (`31f756ac`: found by links); values the desk sends as text (`"9:30:00"` vs `"09:30:00"`, dates) were recorded as changes (`same`/`as_value`); renaming a record rewrote `reference_name` and broke fingerprints (`document_name_at_event`); a `<`/`>` in a reason was stripped after fingerprinting (refused now); a workflow action to a cancelled state skipped the dialog (`732f3c01`); "Cancel All" cancelled linked records before the reason was staged (cancelling key); a failed bulk action left its reason to the next record (`test_a_failed_action_leaves_its_reason_to_no_other_record`).

**Potential:**
- `records_being_saved` walks the Python call stack by function name (`insert`, `_save`, …); a Frappe upgrade that renames those methods would make every ERPNext-caused change ask a reason again (fails safe, noisy). Re-check on each upgrade (rules.md section 8).
- The `command` recorded is `frappe.form_dict.cmd`, i.e. the server method path, not the button label the person clicked.
- A forged event appended at the tail is not detected by verification (accepted by the requirement; mitigated by no-delete, permissions and archives).
- The staged reason lives in the cache for 15 minutes per user and record; a reason staged and then abandoned is used by that user's next cancel of the same record within 15 minutes (test `test_staged_reason_is_used_for_the_cancellation_once` shows it is used once; an abandoned one is a known edge).
- Derived values on other records are not recorded (section 2.7).
- Anchors (section 2.8): up to an hour of entries is unanchored at any moment; a site restored from a database backup without its files reports *The anchor file … is missing.* until the file is restored with it (restore both together); the hourly job's finding reaches the Error Log, not QA; the file's append-only attribute (`chattr +a`) is an operating-system step for hosting, not applied by the app.

**Unverified in this review:** the Frappe code path that protects `owner`/`creation` (C8, test passes); impersonation through Frappe's real "Impersonate" screen; AT-08 step 2's exact command string on screen.

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (local test site; CI = GitHub Actions on a fresh site):

| Check | Scope | Result |
| --- | --- | --- |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed |
| Module tests inside it | `test_audit.py`: TestEveryActionIsRecorded 12, TestValuesSetByCode 9, TestEventContent 4, TestEventsAreImmutable 3, TestChain 6, TestScope 3, TestScopeRules 3 (unit), TestSignaturesSurviveRename 1, TestReviewStaysFastAtScale 1, TestValuesComparedAsStored 1 (unit); `test_audit_reason.py`: TestReasonIsRequired 8, TestReasonGivenOnTheForm 7, TestActionsCausedByAnother 9; `test_audit_review.py`: TestRecordTimeline 11, TestSearchAndExport 4, TestQAReviewsTheEvidence 2, TestReviewersVerifyARecord 3; `test_approvals.TestAttributionIsPermanent` 2 | passed (part of the suite above) |
| Browser suite, retries off | `run-ui-tests … --config retries=0`; module 50 titles: `audit_trail.js` [URS-CMP-011] ×4, [URS-CMP-012] ×1, [URS-CMP-010] ×1; `approvals.js` [URS-CMP-011] ×1; `standard_flow.js` [URS-PLT-007] [URS-CMP-011] [URS-CMP-020] ×1 | 59 of 59 passed |
| Semgrep (Frappe rules) | app | 0 findings |
| pre-commit, traceability | app | pass |
| GitHub CI | current branch | in progress |
| Manual browser tests AT-01…AT-09 | — | **not executed** (no manual tester session); the automated browser tests cover AT-02 steps 1–5, AT-03 (as Administrator on a test DocType), AT-06 step 1, AT-07 step 3's list opening |

2026-10-12 changes (section 1): `compliance/test_immutable_records.py` written without a site and **not yet run**; AT-09 steps 4–5 **not executed**.

2026-10-12, branch `fix/audit-anchor-and-scope`: `compliance/test_audit_anchor.py` (20 tests), the new evidence-contract test and the adapted `test_audit_review` test were written without running a site: **not yet run**; pre-commit and Semgrep only. AT-10 **not executed**; the Anchor Now / Verify Audit Trail buttons have no browser test yet.

Untested: AT-06 steps 2–3 (database tampering; server tests cover the logic), AT-08 (caused-by and status command in the browser; server tests cover the logic), AT-07 as each job in the browser (the screen sweep opens the Audit Event list as QA Manager and System Administrator only), real impersonation.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Every action recorded once, automatically, with old/new values and child rows; whole record on create/delete | Verified (automated) |
| User, real user, server time recorded | Verified (automated; impersonation by unit patch only) |
| Events never edited/deleted, not even by Administrator; user IDs permanent; owner/creation permanent | Verified (automated) |
| Events never inserted past the trail; no hand-made permission opens them; a user the trail names is disabled, not deleted (2026-10-12) | **Not tested** (tests written, not yet run) |
| Chain fingerprints; altered, removed and inserted-before-newest entries detected; rename and clock change safe | Verified (automated + browser Verify Integrity) |
| Reason required for cancel/amend/update after submit on every path; caused-by and command recorded | Verified (automated + browser dialogs) |
| Reviewers see timeline, Audit Trail, Verify Integrity; search and export; others see nothing | Verified (automated; browser as Administrator) |
| Regulated types declared in code incl. every app DocType | Verified (automated) |
| Removing the newest audit events or signatures detected by anchors (DB + append-only file); anchors permanent (2026-10-12) | **Not tested** (tests written, not yet run) |
| Derived values on other records | **Not covered** by design (section 2.7) |
| Manual AT-01…AT-09 | **Not tested** |
| CI green for the final commit | **Blocked** (in progress) |
| Client confirmation: formatted audit report wanted? accounting left to India Compliance? | **Open** |
