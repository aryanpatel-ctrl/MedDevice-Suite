# Approval workflows (module 48) — feature document and browser test guide

Requirements: **URS-CMP-019, URS-CMP-020, URS-CMP-021, URS-CMP-022, URS-CMP-032, URS-CMP-033** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-028 (alerts to approver and requester, module 51), URS-CMP-011 (reason to cancel or amend, module 50), URS-CMP-031 (attribution), URS-CMP-015 (who may submit, module 47).
Code: `meddevice_suite/setup/workflows.py` (the two workflows as code, protection), `compliance/approvals.py` (rules Frappe's workflow does not enforce), `compliance/alerts.py` + `compliance/notification/*approval*` (alerts), `compliance/audit_reason.py` + `public/js/audit_reason.js` (reason dialog); wired in `meddevice_suite/hooks.py`; applied by `setup/install.py` `apply_configuration`.
State on 2026-10-10: built, in the server and browser suites the lead ran green on the current branch (section 8); GitHub CI for the branch still in progress when this document was written.

## 1. Feature overview

**Purpose.** The client's flow map (D1) marks two approval points: *Internal Price Approval?* before a quotation goes to the customer, and *Purchase Approval?* before a purchase order is issued. Each is a Frappe workflow in which the approver's action is what submits the record. Management approves every quotation and every purchase order (client's instruction of 2026-10-07); the requester can never approve their own record, by the form or the API; while a record awaits approval only the approver can change it; once approved, items, quantities and prices stand, and a change means cancel and amend, which is approved again. The workflows are configuration as code and cannot be altered, switched off or replaced on a site.

**Why it matters.** 21 CFR 11.10(f) (operational system checks enforcing the sequence of steps) and (g) (authority checks). A quotation sent at an unapproved price, or an order issued without approval, is a process deviation; ERPNext alone lets anyone with submit permission skip the workflow through the API.

**Who uses it.**

| Job profile (pilot user) | Role in the workflow |
| --- | --- |
| Sales (`sales@demo.example.com`, Sales User) | Drafts the quotation, sends it for price approval, revises a returned one, may cancel an approved one with a reason |
| Purchase (`purchase@demo.example.com`, Purchase User) | Drafts the purchase order, sends it for approval, revises, amends a cancelled one |
| Management (`management@demo.example.com`, Sales Manager + Purchase Manager …) | Approves or returns for revision; cancels an approved purchase order with a reason; receives the "waiting" alerts |
| QA Manager, System Administrator | Read quotations and orders (oversight); the administrator cannot edit the workflow records |

**Prerequisites.** Job profiles and the permission matrix (module 47); alerts (module 51); for a purchase order, the supplier approved by QA for its items (module 4, else the order cannot be saved).

## 2. Detailed functional explanation

### 2.1 The two workflows as declared (`WORKFLOWS`)

| | Quotation Price Approval | Purchase Order Approval |
| --- | --- | --- |
| Record type | Quotation | Purchase Order |
| States (docstatus; who may edit in it) | Draft (0; Sales User) → **Awaiting Price Approval** (0; Sales Manager) → **Approved** (1; Sales Manager) → Cancelled (2; Sales Manager) | Draft (0; Purchase User) → **Awaiting Approval** (0; Purchase Manager) → **Approved** (1; Purchase Manager) → Cancelled (2; Purchase Manager) |
| Actions (from → to; allowed role) | **Send for Price Approval** (Draft → Awaiting; Sales User); **Approve** (Awaiting → Approved; Sales Manager); **Return for Revision** (Awaiting → Draft; Sales Manager); **Cancel** (Approved → Cancelled; Sales User *and* Sales Manager) | **Send for Approval** (Draft → Awaiting; Purchase User); **Approve** (Awaiting → Approved; Purchase Manager); **Return for Revision** (Awaiting → Draft; Purchase Manager); **Cancel** (Approved → Cancelled; Purchase Manager only) |
| Self-approval | `allow_self_approval = 0` on the approving transition only (Draft-state → submitted-state); 1 on the others, so a requester may send their own record | same |
| Emails | `send_email_alert = 0` (alerts come from module 51, section 2.4) | same |

On the form the actions sit under the **Actions** button (Frappe's workflow button); the state shows in the form's indicator pill (*Awaiting Approval*, *Approved*, *Cancelled*). A quotation's Cancel is open to Sales as well because a customer's revision request is quoted again and approved again (D1 "Revision"); a purchase order is cancelled only by the approver (URS-CMP-020).

### 2.2 Rules Frappe's workflow does not enforce (`compliance/approvals.py`)

Frappe checks roles and the self-approval rule only when the workflow button is pressed. Three hooks on `*` close the gaps for the two record types (they return at once for any type without an active workflow):

| Hook | Rule | Refusal (title *Approval required*, `WorkflowPermissionError`) |
| --- | --- | --- |
| `on_submit` | A submit must be a declared transition from the record's current state into the submitted state | *Quotation QTN-… can only be submitted by approving it.* |
| | …taken by a role allowed to take it | *You are not allowed to approve Purchase Order PUR-ORD-….* |
| | …and never by the record's owner unless the transition allows self-approval | *You cannot approve Purchase Order PUR-ORD-…, because you raised it.* |
| `validate` (on save) | A new record starts in the first state | *A new Quotation starts as Draft.* |
| | The state always matches the docstatus the workflow gives it | *Purchase Order PUR-ORD-… cannot be marked Approved without the action that makes it so.* |
| | Only the role allowed to edit in the current state changes the record | *Purchase Order PUR-ORD-… is Awaiting Approval; only Purchase Manager can change it now.* |
| `before_update_after_submit` | Items (`item_code, qty, uom, conversion_factor, rate, discount_percentage` per row) and totals (`total_qty, net_total, grand_total`) never change after approval | *Quotation QTN-… has been approved; its items, quantities and prices cannot change. Cancel and amend it, and the amendment is approved again.* |

The submit check runs on every submit path (form, `frappe.client.submit`, `apply_workflow`, REST); a refused submit rolls back with the request. Install, migrate and patches (`set_up_by_code`) skip the save-time checks. The permission matrix (module 47) is the first line: Sales User and Purchase User have no submit permission; `on_submit` is the second line should that ever change (`test_submit_permission_alone_does_not_approve`).

### 2.3 Cancel, amend and revise

- **Return for Revision** puts the record back to Draft with the requester as editor; the requester changes it (e.g. the rate) and sends it again; the approver approves the revised record (`test_a_returned_quotation_is_revised_and_sent_again`).
- **Cancel** of an approved record is a workflow action; because the target state has docstatus 2, `public/js/audit_reason.js` first asks **Reason for cancelling <name>** (field **Reason**, button **Continue**), stages it (`audit_reason.stage_reason`) and the audit trail stores it on the *Cancelled* event (URS-CMP-011). Without a reason the dialog does not continue (*Missing Values Required: Reason*); an API cancel without a staged reason is refused *Give a reason to cancel … It is recorded in the audit trail.*
- **Amend**: Frappe's Amend on a cancelled record (Sales User / Purchase User hold *amend*) asks **Reason for amending <name>**; the amended copy starts as Draft (`workflow_state` is a no-copy field) and goes through the approval again (`test_an_amended_purchase_order_needs_approval_again`). A **Duplicate** of an approved record also starts as Draft; a copy sent with state Approved is refused.
- The approver may correct a record while it awaits approval (e.g. lower a rate) and then approve it; the change is audited (`test_the_approver_may_correct_a_record_before_approving_it`).

### 2.4 Alerts to approver and requester (URS-CMP-028, module 51)

Four standard Notifications (`compliance/notification/`, registered in `alerts.py` `ALERTS`, channel *System Notification*, i.e. the desk bell), fired on *Value Change* of `workflow_state`:

| Alert | Condition | Recipients | Subject |
| --- | --- | --- | --- |
| Quotation Awaiting Price Approval | state = Awaiting Price Approval | role Sales Manager | *Quotation QTN-… is waiting for your price approval* |
| Purchase Order Awaiting Approval | state = Awaiting Approval | role Purchase Manager | *Purchase order PUR-ORD-… is waiting for your approval* |
| Quotation Price Approval Decided | state = Approved, or back to Draft after creation | the record's owner | *Quotation QTN-… was approved* / *… was returned for revision* |
| Purchase Order Approval Decided | same | owner | *Purchase order PUR-ORD-… was approved* / *… was returned for revision* |

Email delivery waits for the site's mail server (module 51 / hosting); until then the bell only.

### 2.5 Protection of the configuration (URS-CMP-022)

`apply_workflows()` ensures the Workflow States and Workflow Action Masters exist and makes each Workflow record exactly the declaration, including settings the desk hides (`STATE_DEFAULTS`, `TRANSITION_DEFAULTS`: no condition, no field update, no email to creator, no optional state); an unchanged workflow is not saved again. The `Workflow` hooks `before_validate` and `on_trash` (`setup.workflows.protect`) refuse any change on a site: *The workflow Purchase Order Approval is part of the validated configuration and cannot be changed here.*; and a second active workflow on Quotation or Purchase Order: *Purchase Order is approved through its declared workflow; another workflow cannot replace it.* The administrator may open `/desk/workflow/Purchase%20Order%20Approval` to read it.

### 2.6 Intended but not implemented / outside this feature

- "Sent to the customer" (URS-CMP-019) is enforced as "submitted only by approval": the system does not itself send quotations. No role holds the *email* flag on regulated records, so emailing from the desk is not possible; printing a draft is possible for Sales (VIEW includes print) and not blocked — whether a draft print is acceptable is an open client point (section 3).
- No amount threshold: every record is approved (client decision 2026-10-07); a threshold would be a change to an implemented requirement (rules.md §8).
- A task/approval engine shared by every module (module 59) is a later module; these two workflows are Frappe's standard workflow mechanism.
- Sales Order, Material Request and the other submittable records have no approval workflow (D1 marks none).

## 3. Requirements and acceptance criteria

| # | Requirement / acceptance criterion | Implementation | Automated verification (`compliance/test_approvals.py` unless noted) |
| --- | --- | --- | --- |
| C1 | URS-CMP-019: a quotation is submitted only after price approval; approver approves or returns for revision | workflow *Quotation Price Approval* | `TestQuotationPriceApproval.test_an_approved_quotation_can_be_sent`, `test_a_returned_quotation_is_revised_and_sent_again`, `test_sales_cannot_approve_their_own_price`; `standard_flow.js` steps 1–2 "[URS-CMP-019]" |
| C2 | URS-CMP-020: a purchase order is issued only after approval; approve or return; only the approver cancels | workflow *Purchase Order Approval*; matrix: Purchase User no cancel | `TestPurchaseApproval.test_an_approved_purchase_order_is_issued`, `test_a_returned_purchase_order_goes_back_to_purchasing`, `test_only_the_approver_cancels_and_gives_a_reason`; `approvals.js` "[URS-CMP-020] purchasing sends an order for approval and management approves it"; `standard_flow.js` steps 5, 6, 18 |
| C3 | URS-CMP-021: submitted only from the awaiting state, only by an approving role, never by the requester, form or API | `approvals.on_submit`; `allow_self_approval` | `TestApprovalCannotBeBypassed.*` (8 tests: draft never sent, API submit, created already approved, both-roles user via workflow and via direct submit, requester submit, submit permission alone, requesters' own records); `approvals.js` "[URS-CMP-021] the person who raised an order is not offered the approval"; `standard_flow.js` steps 1, 5 |
| C4 | URS-CMP-022: declared in code, applied on every site; cannot be changed, switched off, deleted or replaced; restored on migrate | `apply_workflows`, `sync_workflow`, `as_declared`, `protect` | `setup/test_workflows.py` `TestDeclaredWorkflows.*` (7 tests: applied exactly, only approving steps forbid self-approval, manual change undone, deleted workflow and state recreated, unchanged not saved, cannot be changed/switched off/deleted, no rival workflow) |
| C5 | URS-CMP-032: while awaiting approval only the approver changes it; state always matches submission | `approvals.validate` | `TestApprovalStateIsTruthful.*` (4 tests) |
| C6 | URS-CMP-033: once approved, items, quantities, prices unchanged; change by cancel + amend, approved again | `approvals.before_update_after_submit`, `approved_terms` | `TestApprovedPricesStand.*` (4 tests: PO rate/qty via Update Items, quotation rate, amended PO needs approval again, copy starts as draft); `standard_flow.js` step 2 (Update Items on an approved quotation refused on screen) |
| C7 | URS-CMP-028: approver alerted when waiting; requester alerted on approval or return | the four Notifications | `compliance/test_alerts.py` `TestApprovalAlerts.*` (5 tests); `TestAlertsAreDeclared.*` (URS-CMP-029) |
| C8 | URS-CMP-011: cancel through the workflow needs a reason kept in the audit trail | `audit_reason.js` wraps `handle_workflow_action`; `audit_reason.reason_for` | `test_only_the_approver_cancels_and_gives_a_reason`; `approvals.js` "[URS-CMP-011] cancelling an approved order through the workflow asks for a reason"; `standard_flow.js` step 18 |
| C9 | URS-CMP-031: the API cannot pass a record off as someone else's to approve it | `audit.py` owner/creation guard | `TestAttributionIsPermanent.*` (2 tests) |

**Ambiguous / open (client to decide):**
- Whether printing or emailing a *draft* quotation to a customer must be blocked (today: no email flag for anyone; print allowed to Sales; nothing stops a PDF of a draft).
- Whether Sales should be able to cancel an approved quotation (today: yes, with a reason; a purchase order only by Management).
- Whether the approver may edit the record before approving (today: allowed and audited — section 7).

**Not covered:** amount-based routing; a second approval level; approvals on other record types (module 59).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`) with the demo users, or a test site with the users of `qa/ui_users.py`. Demo data: customer **City Hospital (Demo)**, product **FC-2W-SIL-16**, supplier **Silicone Compounds (Demo)** (QA-approved for RM-SILICONE by the sample loader), item **RM-SILICONE**. The pilot's sample records include a quotation for FC-3W-SIL-18 awaiting price approval and a purchase order for RM-LATEX awaiting approval (`pilot/samples.py`). Record the actual result and a screenshot (form with its state pill and the Actions menu; the reason dialog).

### AW-01 Quotation: send for price approval, requester not offered Approve (C1, C3)
Role: Sales.
1. **Selling › Quotation › New** (`/desk/quotation/new`); Party = `City Hospital (Demo)`; item `FC-2W-SIL-16`, Qty 100, Rate 98; **Save**. Expected: saved as Draft; pill *Draft*; **Actions** offers **Send for Price Approval** only; no Submit button.
2. **Actions › Send for Price Approval**. Expected: pill *Awaiting Price Approval*; still docstatus 0 (not submitted); **Actions** no longer visible to Sales; the form's line fields are read-only.
3. Try `POST /api/method/frappe.client.submit` with the quotation (browser console: `frappe.call({method:"frappe.client.submit", args:{doc: cur_frm.doc}})`). Expected: refused, HTTP 403 (no submit permission) — the record stays *Awaiting Price Approval*.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 2 and 3.

### AW-02 Management approves or returns; requester alerted (C1, C7)
Role: Management. Preconditions: AW-01.
1. Open the desk bell. Expected: *Quotation QTN-… is waiting for your price approval*.
2. Open the quotation; **Actions › Return for Revision**. Expected: pill *Draft*. As **Sales**: bell shows *Quotation QTN-… was returned for revision*; the rate can be changed; **Save**; **Actions › Send for Price Approval** again.
3. As **Management**: **Actions › Approve**. Expected: pill *Approved*; docstatus 1 (the form shows a submitted record); Sales' bell: *Quotation QTN-… was approved*.
Actual result: ____  Pass/Fail: ____

### AW-03 Approved prices stand; cancel and amend (C6, C8)
Role: Management, then Sales.
1. On the approved quotation click **Update Items**; change the line's Rate to 105; **Update**. Expected: refused *Quotation QTN-… has been approved; its items, quantities and prices cannot change. Cancel and amend it, and the amendment is approved again.* Rate unchanged on reload.
2. **Actions › Cancel**; dialog **Reason for cancelling QTN-…**; click **Continue** with an empty Reason. Expected: *Missing Values Required: Reason*; nothing cancelled.
3. Enter `Customer asked for a revised price`; **Continue**. Expected: pill *Cancelled*; docstatus 2.
4. As **Sales**: open the cancelled quotation; **Amend**; dialog **Reason for amending QTN-…**; give a reason; the amended form opens as `QTN-…-1`; change the rate; **Save**. Expected: pill *Draft*; **Actions** offers Send for Price Approval; it needs approval again (repeat AW-02 step 3).
5. As Administrator: `/desk/audit-event` filtered Reference Name = the cancelled quotation, Action = Cancelled. Expected: one event with the reason of step 3 and Management's user.
Actual result: ____  Pass/Fail: ____

### AW-04 Purchase order approval and who may cancel (C2, C3, C5)
Role: Purchase, then Management. Preconditions: supplier approved for the item (module 4).
1. **Buying › Purchase Order › New**; Supplier `Silicone Compounds (Demo)`; Required By = today + 7; item `RM-SILICONE`, Qty 10; **Save**; **Actions › Send for Approval**. Expected: pill *Awaiting Approval*; Actions hidden; no Save button.
2. Still as Purchase, open a line (click its row number). Expected: Rate is shown read-only. Console: `frappe.call({method:"frappe.client.set_value", args:{doctype:"Purchase Order Item", name:cur_frm.doc.items[0].name, fieldname:"rate", value: cur_frm.doc.items[0].rate + 1}})`. Expected: refused *Purchase Order PUR-ORD-… is Awaiting Approval; only Purchase Manager can change it now.*
3. As **Management**: bell *Purchase order PUR-ORD-… is waiting for your approval*; open it; lower the Rate on the line; **Save**; **Actions › Approve**. Expected: saved (approver may correct), then pill *Approved*, docstatus 1.
4. As **Purchase**: open the approved order. Expected: no **Cancel** in Actions and no Cancel button (Purchase User has no cancel permission).
5. As **Management**: **Actions › Cancel**; reason `Supplier cannot deliver`; **Continue**. Expected: pill *Cancelled*.
Actual result: ____  Pass/Fail: ____

### AW-05 The requester cannot approve, even holding both roles (C3)
Role: Administrator (test site only — creates a throw-away user).
1. **Users › User › New**: `both.roles@example.test`, roles **Purchase User** and **Purchase Manager** (no System Manager). Save.
2. Sign in as that user; new Purchase Order as in AW-04 step 1; **Actions › Send for Approval**. Expected: Awaiting Approval.
3. Open **Actions**. Expected: **Approve** is not offered (Frappe hides a self-approval that is not allowed). Console: `frappe.xcall("frappe.model.workflow.apply_workflow", {doc: cur_frm.doc, action: "Approve"})`. Expected: refused (self-approval not allowed); state unchanged.
4. Console: `frappe.call({method:"frappe.client.submit", args:{doc: cur_frm.doc}})`. Expected: refused *You cannot approve Purchase Order PUR-ORD-…, because you raised it.*
Actual result: ____  Pass/Fail: ____  Cleanup: disable the user.

### AW-06 State cannot be faked (C5)
Role: Management, on a draft order never sent for approval (test site).
1. Console on the draft: `frappe.call({method:"frappe.client.set_value", args:{doctype:"Purchase Order", name: cur_frm.doc.name, fieldname:"workflow_state", value:"Approved"}})`. Expected: refused *… cannot be marked Approved without the action that makes it so.*
2. Console: `frappe.call({method:"frappe.client.submit", args:{doc: cur_frm.doc}})`. Expected: refused *Purchase Order PUR-ORD-… can only be submitted by approving it.*; still Draft.
Actual result: ____  Pass/Fail: ____

### AW-07 The workflow cannot be changed on the site (C4)
Role: System Administrator.
1. Open `/desk/workflow/Purchase%20Order%20Approval`. Expected: the record opens (read); untick **Is Active**; **Save**. Expected: refused *The workflow Purchase Order Approval is part of the validated configuration and cannot be changed here.*
2. Menu › **Delete**. Expected: refused with the same message.
3. `/desk/workflow/new`: Document Type *Purchase Order*, Is Active ticked, one state; Save. Expected: refused *Purchase Order is approved through its declared workflow; another workflow cannot replace it.*
Actual result: ____  Pass/Fail: ____

### AW-08 Lists and filters
1. As **Management**: `/desk/purchase-order?workflow_state=Awaiting%20Approval`. Expected: the list shows the orders waiting (the pilot sample for RM-LATEX among them).
2. As **Sales**: `/desk/quotation`, filter *Workflow State = Awaiting Price Approval*. Expected: the FC-3W-SIL-18 sample quotation.
Actual result: ____  Pass/Fail: ____

### Regression
- Run the automated journey (`standard_flow.js` steps 1, 2, 5, 6, 18) and `approvals.js` (3 tests); supplier approval (module 4) must still be checked when an order is sent and approved (SA-04 of the approved-suppliers document).

## 5. End-to-end business workflow

1. **Sales** records the enquiry (Opportunity) and makes the **Quotation** (`pilot/flow.py` `quote`: `make_quotation`, valid 30 days, `apply_workflow(…, "Send for Price Approval")`). Data: Quotation draft, state Awaiting Price Approval; Notification Log for Sales Manager.
2. **Management** approves (`approve_price`: `apply_workflow(…, "Approve")`). Data: Quotation submitted, state Approved; Notification Log for the owner; Audit Event *Submitted* by Management.
3. **Sales** books the **Sales Order** from the approved quotation (no workflow).
4. **Stores** raises the **Material Request**; **QA** approves the supplier (module 4); **Purchase** makes the **Purchase Order** from the request and sends it (`order_materials`); Management approves (`approve_purchase`). Data: PO submitted, state Approved, `mds_supplier_approval` set.
5. **Stores** receives against the approved order (module 16/17); the rest of the flow follows.
6. A supplier that cannot deliver: **Management** cancels the order with a reason (audit event *Cancelled*, reason kept); **Purchase** amends it and sends the amendment for approval again.

The pilot's sample loader leaves one quotation (FC-3W-SIL-18, 150) awaiting price approval and one purchase order (RM-LATEX, 25) awaiting approval for the walk-through.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Workflow records | `Workflow` *Quotation Price Approval* (Quotation), *Purchase Order Approval* (Purchase Order); Workflow States Draft, Awaiting Price Approval, Awaiting Approval, Approved, Cancelled; Workflow Action Masters Send for Price Approval, Send for Approval, Approve, Return for Revision, Cancel; state field `workflow_state` |
| Declaration / apply | `setup/workflows.py`: `WORKFLOWS`, `STATE_DEFAULTS`, `TRANSITION_DEFAULTS`, `apply_workflows()` ← `setup/install.py` `apply_configuration()`; `declared_workflow`, `approves`, `sync_workflow`, `as_declared`, `stored`, `protect` |
| Enforcement | `compliance/approvals.py`: `on_submit`, `validate`, `before_update_after_submit`, `approved_terms`, `APPROVED_TOTALS`, `APPROVED_ITEM_FIELDS`, `set_up_by_code` |
| Hooks (`hooks.py`) | `doc_events["*"]`: `validate` → `approvals.validate`; `on_submit` → `approvals.on_submit`; `before_update_after_submit` → `approvals.before_update_after_submit` (before the audit reason hook, so the approval refusal comes first). `Workflow`: `before_validate`, `on_trash` → `setup.workflows.protect`. `Notification`: `before_validate`, `on_trash` → `alerts.protect` |
| Permissions (module 47) | Quotation: Sales User EDIT + cancel + amend, Sales Manager APPROVE; Purchase Order: Purchase User EDIT + amend, Purchase Manager APPROVE |
| Alerts | `compliance/notification/quotation_awaiting_price_approval/`, `purchase_order_awaiting_approval/`, `quotation_price_approval_decided/`, `purchase_order_approval_decided/` (event Value Change on `workflow_state`, channel System Notification); registered in `alerts.ALERTS`, re-imported and enabled by `apply_alerts()` |
| Reason | `compliance/audit_reason.py` `requires_reason`, `stage_reason` (whitelisted POST), `reason_for`; `public/js/audit_reason.js` dialog titles *Reason for cancelling {0}* / *Reason for amending {0}* / *Reason for changing {0}*, button **Continue** |
| API routes | `POST /api/method/frappe.model.workflow.apply_workflow` (`doc`, `action`) — the only way to approve; `POST /api/method/frappe.client.submit` refused around the approval; `POST /api/method/meddevice_suite.compliance.audit_reason.stage_reason` (`doctype`, `name`, `action`, `reason`); standard `/api/resource/Quotation`, `/api/resource/Purchase Order` (saves run `approvals.validate`) |
| Desk routes | `/desk/quotation/<name>`, `/desk/purchase-order/<name>`; `/desk/workflow/<name>` (administrator, read only in practice) |
| Pilot | `pilot/flow.py` `quote`, `approve_price`, `order_materials`, `approve_purchase`; `pilot/samples.py` `AWAITING_PRICE_APPROVAL`, `AWAITING_PURCHASE_APPROVAL` |
| Data flow | Send: form → `apply_workflow` → Frappe checks role/self-approval → save → `approvals.validate` (state ↔ docstatus, editor) → Notification (approver). Approve: `apply_workflow` → `doc.submit()` → `approvals.on_submit` (transition, role, owner) → Audit Event → Notification (owner). Update Items / update after submit → `before_update_after_submit` compares `approved_terms(before)` with the new terms → refuse |

## 7. Defects, edge cases and risks

**Confirmed and fixed during development (git history; each with a test):** approval bypass through the API and by a requester holding submit permission (`b6e8ecc0`, robustness review 2026-10-07: URS-CMP-021/032/033 strengthened); a draft labelled Approved by `set_value`; an approved record relabelled Cancelled without cancelling; requester changing a record awaiting approval; Update Items changing an approved order; the reason dialog on a workflow cancel (`732f3c01`); forged owner on API submit (URS-CMP-031); hand changes to hidden workflow settings (condition, email to creator, optional state) restored on migrate.

**Potential / unverified:**
- The approver may edit a record awaiting approval and then approve it (section 2.3): the "four-eyes" check is on the requester, not on the approver's own correction. Audited, allowed by design; the client has not been asked.
- `approved_terms` compares the listed item fields and three totals; a change to taxes or a discount that leaves `grand_total`, `net_total` and `total_qty` unchanged (unlikely in practice) would not be caught. No test.
- A draft quotation can be printed by Sales before approval (print flag); no draft watermark verified.
- `Quotation Price Approval Decided` fires on *Draft after creation* (return for revision) by the condition `doc.modified != doc.creation`; a draft saved for the first time right after creation is not alerted (`test_creating_a_record_alerts_nobody`), but a requester who saves a never-sent draft a second time is not alerted either because the state did not change (Value Change on `workflow_state`). Behaviour as intended; edge of the condition not separately tested.
- Email alerts depend on the mail server (module 51); bell only until then.
- The Cypress `approvals.js` spec creates its users with a fixed password in the spec (test site only; `screen_sweep.js` and `standard_flow.js` use per-run passwords).

## 8. Test results and verification summary

Automated results below were executed by the lead on 2026-10-10 on the current branch (local test site `test.localhost`); this document cites them and did not re-run anything.

| Check | Command / run | Result |
| --- | --- | --- |
| Full server suite | `bench --site test.localhost run-tests --app meddevice_suite` | 46 unit + 588 integration passed (includes `compliance/test_approvals.py`: 6 classes, 24 test methods counted in source; `setup/test_workflows.py`: 7; `compliance/test_alerts.py` `TestApprovalAlerts`: 5, `TestAlertsAreDeclared`: 3) |
| Browser suite, retries off | `bench --site test.localhost run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` | 59 of 59 passed: `approvals.js` (3 tests: URS-CMP-020, -021, -011); `standard_flow.js` steps 1, 2, 5, 6, 18 (URS-CMP-019/020/021/011) |
| Semgrep (Frappe rules + python correctness) | as in `.github/workflows/linter.yml` | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| GitHub CI (fresh site) | `.github/workflows/ci.yml` | in progress when this document was written |
| Manual browser tests AW-01…AW-08 | — | **not executed** (no manual tester session). The automated journey covers AW-01 steps 1–2, AW-02 step 3, AW-03 step 1 (Update Items refused on screen), AW-04 steps 1–3 and 5 (set_value refused, approve, cancel with reason, audit event); `approvals.js` covers AW-04 step 4's absence of Actions for the requester and the reason dialog |

Untested: AW-02 bell contents on screen (alerts verified in Notification Log by server tests only); AW-03 step 4 (Amend on screen); AW-05 both-roles user on screen (server test only); AW-07 on screen (server test only).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Quotation submitted only by price approval; return for revision; Sales not offered Approve | Verified (automated + journey) |
| Purchase order submitted only by approval; only Management cancels; reason kept | Verified (automated + journey) |
| No bypass: API submit, direct submit, both-roles user, faked state, created-as-approved | Verified (automated) |
| Workflows declared in code; cannot be changed/switched off/deleted/replaced; restored on migrate | Verified (automated) |
| Awaiting approval: only the approver edits; state matches docstatus | Verified (automated + journey) |
| Approved items/quantities/prices stand; amend needs approval again | Verified (automated + journey) |
| Alerts to approver (waiting) and requester (approved / returned) | Verified (automated, Notification Log); bell on screen **Not tested** |
| Manual browser cases AW-01…AW-08 | **Not tested** |
| Draft quotation printing; approver's own corrections; Sales cancelling quotations | **Open** (client decisions) |
| CI green on a fresh site for the final commit | **Pending** |
