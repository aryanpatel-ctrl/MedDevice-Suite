# Alerts and notifications (module 51) — feature document and browser test guide

Requirement: **URS-CMP-027, URS-CMP-028, URS-CMP-029** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-019/020 (the approval workflows the approval alerts follow, module 48), URS-PUR-001, URS-QMS-* (alerts other modules declare through this module's register).
Code: `meddevice_suite/compliance/alerts.py`, the six Notification definitions in `compliance/notification/` (`lot_expiring_soon`, `lot_expired`, `quotation_awaiting_price_approval`, `quotation_price_approval_decided`, `purchase_order_awaiting_approval`, `purchase_order_approval_decided`), hooks in `meddevice_suite/hooks.py`, applied by `setup/install.py` (`apply_alerts`).
State on 2026-10-10: built (`a11c586c` 2026-10-06; exact-date and robustness gaps closed in `b6e8ecc0` 2026-10-07), server suite green on the current branch; no browser test carries this module's ids (section 8); GitHub CI in progress.

## 1. Feature overview

**Purpose.** People are told, in the desk, when something needs them: Stores and QA when a lot with stock is within 30 days of expiry and again when it has expired; the approver when a quotation or purchase order waits for approval; the person who raised it when it is approved or returned for revision. The alerts are part of the validated configuration: they ship with the app, are re-applied on every migrate, and cannot be edited, switched off or deleted on a site.

**Why it matters.** D2 "Alerts & Notifications" of the client's diagram; an expired lot issued to production is a GMP failure, and an approval nobody notices stalls purchasing and sales. ISO 13485 4.1.6 requires validated configuration to stay as validated, so an alert quietly switched off on the site would be a finding.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they receive |
| --- | --- | --- |
| Stores (`stores@demo.example.com`) | Stock User | *Lot Expiring Soon*, *Lot Expired* |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager | *Lot Expiring Soon*, *Lot Expired* |
| Management (`management@demo.example.com`) | Sales Manager, Purchase Manager (+ others) | *Quotation Awaiting Price Approval*, *Purchase Order Awaiting Approval* |
| Sales (`sales@demo.example.com`), Purchase (`purchase@demo.example.com`) | Sales User / Purchase User | *… Approval Decided* for the records they raised |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Sees the alert definitions under **System › Notification**; cannot change them; *Audit Trail Problem Found* (with QA, since 2026-10-12) |

**Prerequisites.** The scheduler runs daily on the site (`bench` scheduler enabled) for the lot alerts; the approval workflows of module 48 are applied (they are, by `bench migrate`). Email delivery needs the site's mail server, which is not part of this module (section 2.6).

## 2. Detailed functional explanation

### 2.1 Where alerts appear

All six alerts use Frappe's channel **System Notification**: they appear in the desk bell (the notification list at the top of every page, Frappe's **Notification Log**, route `/desk/notification-log`) for each recipient, with a subject and a message that links the record. No email is sent until an Email Account is configured on the site (Frappe then also emails system notifications according to the user's Notification Settings; not verified here).

### 2.2 Lot expiry (URS-CMP-027)

A daily job (`alerts.send_expiry_alerts`, scheduler `daily`) runs two passes over **Batch** (ERPNext's lot record):

| Pass | Lots selected | Alert sent |
| --- | --- | --- |
| Expired | Expiry Date set and before today; **Expiry Alert Sent** ≠ *Lot Expired*; Batch Qty > 0; not Disabled | **Lot Expired** — subject *Lot <lot> has expired with stock remaining*; message *Lot <lot> of item <item> expired on <date>, and <qty> <uom> of it is still in stock. It can no longer be issued or dispatched; move it to the hold store.* |
| Expiring soon | Expiry Date between today and today + 30; Expiry Alert Sent not in (*Lot Expiring Soon*, *Lot Expired*); Batch Qty > 0; not Disabled | **Lot Expiring Soon** — subject *Lot <lot> expires on <date>*; message *Lot <lot> of item <item> expires on <date>, and <qty> <uom> of it is still in stock. Plan its use, or its hold, before then.* |

Recipients: everyone with the role **Stock User** or **Quality Manager** (`receiver_by_role`). After each send the lot's custom field **Expiry Alert Sent** (`mds_expiry_alert`, read-only Select, after Expiry Date, applied by `apply_alerts`) is set to the alert's name without touching `modified` and without an audit event (bookkeeping of the alert, documented in the code). So each lot is warned once and alerted as expired once, however many days the job runs; a lot received already inside the 30 days is warned on the next run; a day the scheduler missed is caught up because the selection is by date range, not by "today minus 30". A lot expiring today gets *Expiring Soon* today and *Expired* the next day. Changing a lot's **Expiry Date** clears Expiry Alert Sent (`alerts.reset_expiry_alert`, Batch validate), so the new date is alerted afresh. The dates are compared as dates (`compliance.audit.same`, field type Date): the form sends the date as text and the database returns a date, so before 2026-10-15 saving a lot from its form with the date untouched cleared the field and the lot was alerted again (found in the browser walk-through of the independent review). Lots without expiry date, with zero stock, or Disabled are skipped.

Failure of one lot's send is rolled back to a savepoint, logged in **Error Log** (*Expiry alert for lot <lot> could not be sent*) and retried the next day; the other lots are still alerted. The daily lookup is indexed on `Batch.expiry_date`.

### 2.3 Approvals (URS-CMP-028)

Four standard Notifications fire on **Value Change** of `workflow_state` (the module 48 workflows):

| Alert | Record | Condition | Recipient | Subject / message |
| --- | --- | --- | --- | --- |
| Quotation Awaiting Price Approval | Quotation | `workflow_state == "Awaiting Price Approval"` | role Sales Manager | *Quotation <name> is waiting for your price approval* / *Quotation <name> for <customer>, <grand total>, is waiting for internal price approval.* |
| Purchase Order Awaiting Approval | Purchase Order | `workflow_state == "Awaiting Approval"` | role Purchase Manager | *Purchase order <name> is waiting for your approval* / *Purchase order <name> to <supplier>, <grand total>, is waiting for purchase approval.* |
| Quotation Price Approval Decided | Quotation | `workflow_state == "Approved"` or (`"Draft"` and modified ≠ creation) | the record's owner (who raised it) | *Quotation <name> was approved* / *The price of quotation <name> was approved; it can be sent to the customer.* — or *… was returned for revision* / *Quotation <name> was returned for revision.* |
| Purchase Order Approval Decided | Purchase Order | same pattern | owner | *Purchase order <name> was approved* / *Purchase order <name> was approved and can be issued to the supplier.* — or *… was returned for revision* |

Creating a quotation or order alerts nobody; the requester is not alerted by their own "Send for Approval".

### 2.4 Configuration as code (URS-CMP-029)

`alerts.ALERTS` is the register of every alert in the system with the module that ships it. `apply_alerts()` (from `apply_configuration`, on install and every migrate) force-imports each from its JSON/HTML files and sets `enabled = 1`, so an alert edited or switched off behind the application's back is restored. On a site, saving or deleting a declared Notification is refused in `before_validate`/`on_trash` (`alerts.protect`) with *The alert <name> is part of the validated configuration and cannot be changed here.* — also in developer mode and for System Manager. Imports during install/migrate are allowed.

### 2.5 Alerts other modules add through the register

| Alert | Shipped by | Module |
| --- | --- | --- |
| Lot Expiring Soon, Lot Expired, Quotation Awaiting Price Approval, Purchase Order Awaiting Approval, Quotation Price Approval Decided, Purchase Order Approval Decided | Compliance | 51 (this document) |
| Document Review Due | QMS | 30 document control |
| Change Request Awaiting Assessment, Change Request Awaiting Approval, Change Request Awaiting Management Approval, Change Request Decided, Change Effectiveness Check Due | QMS | 36 change control |
| Supplier Certificate Expiring | Master Data | 4 approved suppliers |
| Audit Trail Problem Found (2026-10-12; to Quality Manager and System Manager, once per finding) | Compliance | 50 audit trail (`audit-trail.md` section 2.8, URS-CMP-042) |

Each is protected and re-applied exactly like the six above; their content is documented with their own module.

### 2.6 What is intended but not implemented

- **Email** delivery depends on the site's mail server (hosting task F11 in the code's docstring); the alerts are desk-bell only until then. Not part of this module.
- Module 51's title "Alerts and notifications" also covers alerts for calibration, training, licences, CAPA and certificates; those come with their own modules (section 2.5 lists the ones shipped so far). Not a gap of URS-CMP-027..029.
- No alert digest, escalation or reminder on an approval still waiting after N days (module 60 reminders, separate).
- Recipients are by role; there is no per-site choice of who receives a lot alert (the requirement names Stores and QA).

## 3. Requirements and acceptance criteria

| # | Requirement / criterion | Implementation | Automated verification (`compliance/test_alerts.py`) |
| --- | --- | --- | --- |
| C1 | URS-CMP-027: Stores and QA alerted once at ≤ 30 days before expiry, including a lot received that close | `send_expiry_alerts` (between today and today+30), roles Stock User + Quality Manager, `mds_expiry_alert` | `TestLotExpiryAlerts.test_stores_and_qa_are_warned_30_days_before_expiry`, `test_a_lot_received_inside_the_warning_period_is_warned_at_once`, `test_a_lot_further_off_is_not_warned_yet`, `test_a_lot_is_warned_once_however_often_the_alerts_run`, `test_the_alert_says_what_to_do` |
| C2 | URS-CMP-027: once when expired, even after a day the alerts did not run | Expired pass (`< today`), range selection | `test_a_lot_expiring_today_is_warned_and_alerted_as_expired_the_next_day`, `test_an_expiry_the_alerts_missed_is_still_alerted` |
| C3 | URS-CMP-027: changed expiry date alerted afresh; an unchanged one saved from the form is not | `reset_expiry_alert` (Batch validate), `compliance.audit.same` | `test_a_changed_expiry_date_is_alerted_afresh`, `test_an_unchanged_expiry_date_saved_from_the_form_is_not_alerted_again` (2026-10-15, not yet run) |
| C4 | URS-CMP-027: no stock, disabled → no alert (and no expiry date) | filters `batch_qty > 0`, `disabled = 0`, `expiry_date is set` | `test_lots_without_stock_disabled_or_without_expiry_raise_no_alert` |
| C5 | Robustness: one failing lot does not stop the rest; runs daily; indexed | savepoint + Error Log; `scheduler_events.daily`; `add_index` | `test_one_lot_failing_does_not_hold_back_the_others`, `test_the_expiry_alerts_run_daily`, `test_the_daily_lookup_by_expiry_date_is_indexed` |
| C6 | URS-CMP-028: approver alerted when waiting; requester alerted on approve / return | the four Value Change notifications | `TestApprovalAlerts` (5: `test_the_approver_is_told_a_purchase_order_is_waiting`, `test_the_price_approver_is_told_a_quotation_is_waiting`, `test_the_requester_is_told_of_the_approval`, `test_the_requester_is_told_of_a_return_for_revision`, `test_creating_a_record_alerts_nobody`) |
| C7 | URS-CMP-029: declared in code, applied on every site, cannot be edited on a site | `ALERTS`, `apply_alerts`, `protect` | `TestAlertsAreDeclared` (3: `test_every_alert_is_installed_standard_and_enabled`, `test_an_alert_cannot_be_edited_switched_off_or_deleted_on_a_site`, `test_an_alert_changed_behind_the_applications_back_is_restored_on_migrate`) |

**Partial:** none against the requirement text. **Ambiguous:** URS-CMP-027 "Stores and QA" is read as the roles Stock User and Quality Manager (Management, who also hold Stock Manager, are not alerted); confirm with the client. "Alerted" is read as a desk notification; email needs the mail server. **Missing browser test:** no Cypress test carries URS-CMP-027/028/029; the alerts are verified server-side only (section 8).

## 4. Step-by-step browser testing guide

Site: pilot (`pilot.localhost`) or a test site. Lot alerts need a lot with stock: use a lot received in the pilot flow (RM-SILICONE lots in the quarantine store) and let the System Administrator set its **Expiry Date** on the Batch form for the test (regulated change: it is audited; use a test site). Record actual results and screenshots of the bell list and of the Notification Log entries.

### AL-01 Lot expiring within 30 days is alerted once (C1, C5)
Role: System Administrator sets up; Stores and QA Manager observe.
1. As System Administrator open **Stock › Batch › <lot with stock>**; set **Expiry Date** = today + 10; **Save**. Expected: saved; **Expiry Alert Sent** is empty.
2. Run the job once: `bench --site <site> execute meddevice_suite.compliance.alerts.send_expiry_alerts` (or wait for the nightly scheduler). Expected: no error.
3. As Stores open the desk bell. Expected: one notification *Lot <lot> expires on <date>*; clicking it opens the Batch; the text says *… still in stock. Plan its use, or its hold, before then.*
4. As QA Manager: same notification once. As Sales: none.
5. Reopen the Batch as System Administrator. Expected: **Expiry Alert Sent** = *Lot Expiring Soon* (read-only).
6. Run the job again. Expected: no second notification for the lot.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 3 and 5.

### AL-02 Boundaries: 31 days, today, expired, missed day (C1, C2)
1. Lot A: Expiry Date = today + 31; run the job. Expected: no alert.
2. Lot B: Expiry Date = today; run the job. Expected: *Lot B expires on <today>*. Next day (or run with the site date advanced on a test site) run again. Expected: *Lot B has expired with stock remaining*; message ends *… move it to the hold store.*
3. Lot C: Expiry Date = today − 3 (never alerted before); run the job. Expected: one *has expired* alert, no *expires on* alert; Expiry Alert Sent = *Lot Expired*.
Actual result: ____  Pass/Fail: ____

### AL-03 Changed expiry date is alerted afresh (C3)
1. Lot of AL-01 (already alerted): set **Expiry Date** = today + 25; **Save**. Expected: **Expiry Alert Sent** cleared on save.
2. Run the job. Expected: a second *expires on <new date>* notification for Stores and QA.
3. (2026-10-15) Open the lot again, change nothing but another field (or nothing), **Save**; run the job. Expected: **Expiry Alert Sent** keeps its value; no further notification.
Actual result: ____  Pass/Fail: ____

### AL-04 Lots that raise no alert (C4)
1. A lot with Expiry Date = today + 5 and **Batch Qty** 0 (fully consumed). Run the job. Expected: nothing.
2. A lot with stock, Expiry Date = today + 5, **Disabled** ticked. Run the job. Expected: nothing.
3. A lot with stock and no Expiry Date. Run the job. Expected: nothing.
Actual result: ____  Pass/Fail: ____

### AL-05 Purchase order approval alerts (C6)
Preconditions: demo supplier approved for RM-SILICONE (module 4).
1. As Purchase: new **Purchase Order** (RM-SILICONE qty 10), **Save**. Expected: no notification to anyone (check Management's bell).
2. **Actions › Send for Approval**. Expected: Management's bell shows *Purchase order <name> is waiting for your approval*; Purchase gets nothing.
3. As Management open the order › **Actions › Return for Revision**. Expected: Purchase's bell shows *Purchase order <name> was returned for revision*.
4. As Purchase **Send for Approval** again; as Management **Approve**. Expected: Purchase's bell shows *Purchase order <name> was approved* with message *… can be issued to the supplier.*; Management got a second *waiting* alert for the second send.
Actual result: ____  Pass/Fail: ____  Cleanup: cancel the order with a reason or keep it for other tests.

### AL-06 Quotation price approval alerts (C6)
1. As Sales: new **Quotation** for a demo customer with one product line, **Save**, **Actions › Send for Price Approval**. Expected: Management's bell: *Quotation <name> is waiting for your price approval* (*… waiting for internal price approval.*).
2. As Management **Approve**. Expected: Sales' bell: *Quotation <name> was approved*, message *The price of quotation <name> was approved; it can be sent to the customer.*
3. Repeat with **Return for Revision** on another quotation. Expected: *Quotation <name> was returned for revision*.
Actual result: ____  Pass/Fail: ____

### AL-07 Alerts cannot be changed on the site (C7)
Role: System Administrator.
1. **System › Notification › Lot Expiring Soon**; untick **Enabled**, **Save**. Expected: refused *The alert Lot Expiring Soon is part of the validated configuration and cannot be changed here.*
2. Change **Subject**, **Save**. Expected: same refusal.
3. List › select *Purchase Order Awaiting Approval* › **Actions › Delete**. Expected: same refusal; the alert remains.
4. Open a non-declared Notification (if any exists on the site). Expected: editable as usual (only declared alerts are protected).
Actual result: ____  Pass/Fail: ____

### AL-08 Migrate restores a declared alert (C7) — test site only, bench access
1. With database access set `enabled = 0` and `subject = 'x'` on *Purchase Order Awaiting Approval* directly in `tabNotification`.
2. Run `bench --site <site> migrate`. Expected: the alert is enabled again and its Subject is the shipped text *Purchase order {{ doc.name }} is waiting for your approval*.
Actual result: ____  Pass/Fail: ____

### AL-09 Failure of one lot does not stop the others (C5) — observation only
A single lot's send cannot be made to fail from the browser; the behaviour is covered by the server test `test_one_lot_failing_does_not_hold_back_the_others`. On the pilot: after each nightly run, as System Administrator open **System › Error Log** and filter Method = *Expiry alert for lot …*. Expected: empty; if an entry exists, the named lot is alerted on the next run and the other lots were alerted on time.
Actual result: ____  Pass/Fail: ____

### Regression
- The approvals journey (`approvals.js`, `standard_flow.js`) sends and approves orders and quotations; a broken Notification template would appear as *Failed to send Notification* in Error Log — check it is empty after the automated run.

## 5. End-to-end business workflow

1. **Sales** sends a quotation for price approval → **Management** is alerted → approves → **Sales** is alerted and sends the quotation to the customer (module 48, URS-CMP-019).
2. **Purchase** raises a purchase order for the approved supplier → sends for approval → **Management** alerted → approves → **Purchase** alerted and issues the order (URS-CMP-020).
3. **Stores** receives the goods as a new lot with its expiry date; QC inspects; QA releases (modules 16/17).
4. 30 days before the lot's expiry the nightly job alerts **Stores** and **QA** (*Lot Expiring Soon*); they plan its use or hold. If it expires with stock, *Lot Expired* tells Stores to move it to the hold store (module 17 refuses issuing a lot that is not Approved anyway).
5. A lot whose expiry is corrected (QA-controlled change, audited) is alerted again for the new date.

Cross-module data: Notification Log (one per recipient and alert), Batch `mds_expiry_alert`, Error Log on failures; the workflow state changes of module 48 trigger the approval alerts; no audit event for the alert bookkeeping (documented).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Definitions | `compliance/notification/<alert>/<alert>.json` + `.html` (standard Notifications, module Compliance, channel System Notification): Lot Expiring Soon, Lot Expired (event Custom, sent by the job); Quotation Awaiting Price Approval, Purchase Order Awaiting Approval, Quotation Price Approval Decided, Purchase Order Approval Decided (event Value Change on `workflow_state`, with `condition`) |
| Register | `compliance/alerts.py` `ALERTS` (alert → shipping module), the lead time from Quality Settings (`quality_settings.value(...)`, URS-PLT-012), `ALERT_FIELD = "mds_expiry_alert"`, `CUSTOM_FIELDS` (Batch: **Expiry Alert Sent**, Select, read-only, no-copy, after `expiry_date`) |
| Functions | `apply_alerts()` (import with `force=True`, `enabled = 1`, index on `Batch.expiry_date`), `send_expiry_alerts()`, `send(notification, lot, alert)` (savepoint, `frappe.log_error`), `reset_expiry_alert(doc)`, `protect(doc)` |
| Hooks (`hooks.py`) | `scheduler_events.daily` → `compliance.alerts.send_expiry_alerts`; `Notification.before_validate` and `on_trash` → `alerts.protect`; `Batch.validate` → `alerts.reset_expiry_alert` (first of three Batch validators) |
| Setup | `setup/install.py` `apply_configuration()` → `apply_alerts()` (after `apply_workflows`, `apply_retention`, `apply_archive_indexes`) |
| Recipients | by role: Stock User, Quality Manager (lot alerts); Sales Manager, Purchase Manager (awaiting); by document field `owner` (decided) |
| Desk | bell / Notification Log (`/desk/notification-log`); alert definitions under **System › Notification** (`/desk/notification`, System Manager only, `setup/sidebars.py` SIDEBAR_SCOPE "System") |
| API | none of its own; the job can be run with `bench --site <site> execute meddevice_suite.compliance.alerts.send_expiry_alerts` |
| Data flow | daily → two `frappe.get_all("Batch", …)` by date range and `mds_expiry_alert` → `Notification.send(batch)` per lot → Notification Log rows → `set_value(mds_expiry_alert)`; workflow action → `workflow_state` changes → Frappe evaluates Value Change notifications → Notification Log |

## 7. Defects, edge cases and risks

**Confirmed and fixed (git history, with tests):** the first version used Frappe's "Days Before" event, which fires on one exact date, so a lot received inside the warning window or a missed scheduler day was never alerted (replaced by the daily range job, `b6e8ecc0`); a lot expiring today was neither warned nor marked expired (now warned today, expired the next day); a disabled standard alert stayed off after migrate (now `enabled` forced on); a Notification edit was possible in developer mode (now refused regardless).

**Potential:**
- A lot whose `batch_qty` ERPNext has not yet refreshed (ERPNext updates it from stock ledger entries) could be alerted or skipped a day late; by design the job reads the stored quantity.
- `mds_expiry_alert` is set with `update_modified=False` and is not audited; an auditor comparing the Batch snapshot with the field will see it change without an event (documented in code as bookkeeping).
- Email notifications depend on each user's Notification Settings once mail is configured; a user who has switched system-notification email off will get the bell only.
- Recipients by role include every user with that role on every company; multi-company sites (not the client's case) would over-notify.
- Value Change alerts fire on every state change: a record sent for approval twice alerts the approver twice (correct, but worth telling users).

**Unverified in this review:** the real nightly scheduler on the pilot (only the job called directly in tests); the bell rendering and link of each alert in the browser (no Cypress test); email delivery.

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (local test site; CI = GitHub Actions on a fresh site):

| Check | Scope | Result |
| --- | --- | --- |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed |
| Module tests inside it | `compliance/test_alerts.py`: TestLotExpiryAlerts 12, TestApprovalAlerts 5, TestAlertsAreDeclared 3 (each alert test also asserts no *Failed to send Notification* error was logged) | passed (part of the suite above) |
| Browser suite, retries off | `run-ui-tests … --config retries=0` | 59 of 59 passed — **no test title carries URS-CMP-027/028/029**; the approval journeys exercise the workflow states that trigger the alerts without asserting the notifications |
| Semgrep (Frappe rules) | app | 0 findings |
| pre-commit, traceability | app | pass |
| GitHub CI | current branch | in progress |
| Manual browser tests AL-01…AL-09 | — | **not executed** (no manual tester session) |

Untested: scheduler on a live site; bell UI; email; AL-08 (migrate on a tampered site, server test covers `apply_configuration`).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Lot warned once ≤ 30 days before expiry, incl. lots received inside the window and after a missed day | Verified (automated, job called directly) |
| Lot alerted once when expired; changed expiry alerted afresh; no-stock/disabled/undated skipped | Verified (automated) |
| Approver alerted when waiting; requester alerted on approve / return; nobody on create | Verified (automated) |
| Alerts declared in code, enabled and restored on migrate; cannot be edited, switched off or deleted on a site | Verified (automated) |
| Alerts of other modules registered and protected the same way | Verified (automated, `ALERTS` loop) |
| Bell display, links and wording in the browser | **Not tested** (no Cypress test; manual AL-01…AL-06 not executed) |
| Nightly scheduler on the pilot; email delivery | **Not tested** / depends on hosting |
| CI green for the final commit | **Blocked** (in progress) |
| Client confirmation: lot-alert recipients (Stock User + Quality Manager only); email wanted | **Open** |
