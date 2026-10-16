# Change control (module 36) — feature document and browser test guide

Requirements: **URS-QMS-009 … URS-QMS-016, URS-QMS-032, URS-QMS-033** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-QMS-001/004 (document control: a new revision of a document in effect is started under an approved change request), URS-MST-002 (a specification in use changes only under an approved change request — module 6, `controlled-specifications.md`), URS-MST-001 (planned, `version-controlled-masters.md`), URS-CMP-003, URS-CMP-030.
Code: `meddevice_suite/qms/change_control.py`, `qms/doctype/change_request/`, `qms/doctype/change_request_action/`, `qms/doctype/change_request_affected_record/`, `qms/notification/change_request_awaiting_assessment/`, `…awaiting_approval/`, `…awaiting_management_approval/`, `…decided/`, `qms/notification/change_effectiveness_check_due/`, `workspace_sidebar/documents.json`, `public/js/esign.js`, hooks in `meddevice_suite/hooks.py`; signed acts registered in `compliance/signing.py`; link from document control in `qms/doctype/document_revision/document_revision.py` and `.js`.
State on 2026-10-10: built on 2026-10-08 (commits 2b7f24ed, a950d677, d65c28ba), merged to `main`; on the current branch (`feature/6-controlled-specifications`) `require_approved_change` was generalised to any record type for module 6 (commit 7653a364). Server and browser suites green on 2026-10-10 (section 8).
Updated 2026-10-12 on branch `fix/change-control-and-document-review` for two defects found in the foundation review of 2026-10-12: a change request reached Implemented while what it changed was not yet in force (URS-QMS-032), and one approved request authorised repeated changes of the same record (URS-QMS-033). Tests written; not yet run on the test site when this was written (section 8).

## 1. Feature overview

**Purpose.** Nothing in the quality system — a document, a product or bill of materials, a process or routing, equipment, a supplier, the system — changes on somebody's say-so. A change is raised as a numbered request that says what changes, why, and which records it touches; QA assesses its impact on product quality, risk, regulatory notification, validation, training, documents and stock, and classes it Minor or Major; QA approves a Minor change, QA and then Management a Major one, each with a password-backed signature, and never the person who raised it. The approved request carries the actions that implement it, each with an owner and a due date; once every action is recorded done and everything the change made is in force (the new revision effective, the record approved, the device master record approved again) the change is implemented, and QA closes it only after a signed effectiveness check 90 days later (or the number of days QA set). A request is never deleted; the requester can withdraw it until it is approved. The approved request is the key that unlocks the controlled records it lists: a new revision of a document in effect, a change to a specification in use — once each: the request records the record each change was made in, and authorises no second change of the same record.

**Why it matters.** ISO 13485 4.1.4 and 7.3.9 require changes to be reviewed, verified, approved before implementation and their effect evaluated; CDSCO MDR 2017 requires notification of certain changes, hence "regulatory notification ⇒ Major". The client's diagram D2 names Change Request, Impact Assessment, Review & Approval, Implementation, Effectiveness Check and Change History. Who may raise and approve, the 90-day check, Management for Major changes and withdrawal by the requester were decided under the client's instruction of 2026-10-07 (design approved 2026-10-08).

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| Any business job, e.g. Production Supervisor (`production.supervisor@demo.example.com`) | Manufacturing User (etc.) | Raises the request, sends it for assessment, withdraws it; owns actions; hears the decision |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Assesses and classifies; approves (first approval of a Major change); returns or rejects; records any action done; checks effectiveness and closes; sees every request |
| Management (`management@demo.example.com`) | Sales Manager, Purchase Manager, Stock Manager, Manufacturing Manager | Second approval of a Major change; may return or reject a Major change awaiting it; sees every request |
| Action owners, e.g. Stores (`stores@demo.example.com`), QC Inspector | Stock User, QC Inspector | Record their own action done; see the requests they have an action on; alerted when it falls due |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Reads every request; cannot raise, edit, sign or withdraw |

Every business role (all roles of the job profiles except System Manager) may create, read and write a Change Request; the controller limits what each person may change at each stage. System Manager reads only. No role holds submit, cancel, amend, delete, share or import.

**Prerequisites.** E-signature (module 49) and audit trail (module 50) installed; people with their own accounts; the records to be listed exist (a change request names existing records). Document control (module 30) for document changes.

## 2. Detailed functional explanation

### 2.1 The request: Change Request

Navigation: **Documents › Change Request** (the Documents area is on every job's desk), route `/desk/change-request/new`. Record name `CR-YYYY-####` (e.g. `CR-2026-0001`), numbered in sequence within the year.

| Field (label on screen) | Set by | Rule |
| --- | --- | --- |
| Title | requester (draft) | Required |
| Type of Change | requester (draft), QA (assessment) | Required; chosen from Document, Product or BOM, Process or Routing, Equipment, Supplier, System (the blank first option is refused) |
| What Is to Change, Why | requester (draft) | Required |
| Status | acts only | Draft, Under Assessment, Awaiting Approval, Awaiting Management Approval, Approved, Implemented, Closed, Rejected, Withdrawn |
| Classification | QA (assessment) | Minor or Major; required to assess |
| Raised By, Raised On | system | The requester and the day it was raised |
| Reason Given | acts only | Why it was last returned, rejected or withdrawn |
| **Records Affected** › Affected Records (Record Type, Record, Note; Changed In, Changed On) | requester (draft), QA (assessment); Changed In and Changed On by the system only | Record Type one of `AFFECTED_TYPES`; the record must exist; each record listed once (*Controlled Document SOP-QC-001 is listed twice; list each record once.*, at Send for Assessment and at Assess); the form offers only the types the person may look up; needed to send for assessment unless Type of Change = System. **Changed In** (the record the change was made in: a Document Revision, a Risk Assessment, or the master itself) and **Changed On** are written when the change is saved under the request, never typed (*The change made to a listed record is recorded only as the change is made.*), and discarded on a new request |
| **Impact Assessment** (*Recorded by QA before the change can be approved.*): Product Quality, Risk (ISO 14971), Regulatory Notification (CDSCO), Validation, Training, Documents, Stock in Hand and Work in Progress | QA (assessment) | Each Yes or No; all seven needed to assess |
| Effectiveness Check After (Days) | QA (assessment) | Default 90; 0 to 730 |
| Assessment | QA (assessment) | Required to assess |
| **Implementation** › Actions (Action, Owner, Due Date, Record Type, Record It Changes; Done, Done On, Done By, What Was Done, Resulting Record Type, Resulting Record, Due Alert Sent For) | requester (draft), QA (assessment); completion by `Record Action Done` only | At least one action to approve; owner an active user; due date today or later (checked at Assess and at each Approve); Record It Changes optional; completion fields never editable and discarded on creation |
| **Approval and Closure**: Assessed By, Assessed On, Approved by QA, Approved by Management, Approved On, Implemented On, Effectiveness Check Due, Effectiveness, Effectiveness Check, Closed On, Effectiveness Alert Sent | acts only | Read-only; values sent on creation are discarded |

**Who edits what.** While Draft, the requester may change Title, Type of Change, What Is to Change, Why, Affected Records and Actions; nobody else edits a draft. While Under Assessment, QA may change Type of Change, Affected Records, Actions, the seven impact fields, Effectiveness Check After (Days), Classification and Assessment; the requester's text is read-only. In every other status every field is read-only; a change by any other route is refused (*Change request CR-2026-0001 is Awaiting Approval; it can no longer be changed this way.*). Marking an action done on the form is refused (*An action is recorded as done only once the change is approved.*). Frappe's Submit is never offered and is refused (*Change request … is approved and moved on only through its acts.*); delete (*A change request is never deleted.*) and cancel (*An approved change is implemented and closed, never cancelled.*) are refused for everyone, the Administrator included.

A new request is created only by someone in a business job (*A change request is raised by someone in a business job.* — refused for System Manager even through code that skips permissions); Raised By, Raised On and Status Draft are set by the system; Effectiveness Check After (Days) is reset to 90.

### 2.2 Status transitions and who does what

```
Draft ──Send for Assessment──▶ Under Assessment ──✍Assess (QA)──▶ Awaiting Approval ──✍Approve (QA)──▶ Approved ──every action done──▶ Implemented ──✍Verify (QA)──▶ Closed
  ▲                                  │                                  │ Major                              ▲
  │                                  │                                  ▼                                   │
  └────────── ✍Return ───────────────┴──────────── Awaiting Management Approval ──✍Approve (Management)─────┘
        ✍Reject ▶ Rejected (from any of the three waiting states)        Withdraw (requester) ▶ Withdrawn (until approved)
```

| Button (shown only when the form has no unsaved change) | Offered to | Signed? (meaning) | Server checks (message) | Result and alert |
| --- | --- | --- | --- | --- |
| **Send for Assessment** (primary) | the requester, while Draft | no | Draft (*Only a draft change request can be sent for assessment.*); requester (*Only the person who raised … can send it for assessment.*); affected records unless type System (*List the records the change affects.*) | Under Assessment; alert **Change Request Awaiting Assessment** to Quality Manager |
| **Actions › Withdraw** (dialog *Withdraw CR-…*, Reason, button **Withdraw**) | the requester, while Draft, Under Assessment, Awaiting Approval or Awaiting Management Approval | no | requester (*Only the person who raised … can withdraw it.*); before approval (*… is Approved; it can no longer be withdrawn.*); reason (*Say why the change is withdrawn.*) | Withdrawn; Reason Given |
| **Assess** (primary) | QA, while Under Assessment | yes (Reviewed — *I have reviewed this and found it fit for approval.*) | status (*Only a change request under assessment can be assessed.*); QA (*Only QA can assess a change request.*); seven impacts (*Record the impact on: Risk (ISO 14971), Training.*); *Summarise the assessment.*; *Classify the change as Minor or Major.*; *A change that needs regulatory notification is a Major change.*; *Check the effectiveness within 730 days of implementation.*; plan: *Plan at least one action to implement the change.* / *The owner of action 2 is not an active user.* / *Action 1 is due in the past; set a new due date.* | Awaiting Approval; Assessed By/On; alert **Change Request Awaiting Approval** to Quality Manager |
| **Return for Changes** (asks Reason) | QA while Under Assessment, Awaiting Approval or Awaiting Management Approval; Management while Awaiting Management Approval | yes (Rejected) | status (*Only a change request under assessment or awaiting approval can be returned or rejected.*); person (*Only QA, or Management for a Major change awaiting its approval, can decide ….*); *Say what must change.* | Draft; Reason Given; Assessed By/On and Approved by QA cleared (assessed afresh); banner *Returned: <reason>*; alert **Change Request Decided** to the requester (*… was returned*) |
| **Reject** (asks Reason) | as Return | yes (Rejected) | as Return; *Say why the change is rejected.* | Rejected (final, docstatus 0); alert to the requester (*… was rejected*) |
| **Approve** (primary) | QA while Awaiting Approval; Management while Awaiting Management Approval; never the requester, never the QA approver again | yes (Approved — *I approve this.*) | not requester (*The person who raised … cannot approve it.*); plan re-checked (dates still today or later); Awaiting Approval → QA only (*Only QA can approve this change request.*); Awaiting Management Approval → Management only (*Only Management can approve a Major change request.*), a different person (*… needs a second approver from Management; you have approved it for QA.*); other status (*Only an assessed change request awaiting approval can be approved.*) | Minor: Approved, submitted (docstatus 1), Approved by QA, Approved On, alert **Change Request Decided** to the requester (*… was approved*). Major, first approval: Awaiting Management Approval, Approved by QA, alert **Change Request Awaiting Management Approval** to the four Management roles; second approval: Approved, submitted, Approved by Management, alert to the requester |
| **Record Action Done** (primary; dialog *Record an action as done*: Action, What was done, Resulting record type, Resulting record, *Listed records found to need no change* (a tick per listed record not yet marked); button **Record as Done**) | the owner of an undone action, or QA, while Approved | no (audited with the note) | Approved (*Actions are done once the change is approved, and only until it is implemented.*); the action exists (*… has no such action.*) and is not done (*Action 1 of … is already done.*); owner or QA (*Only the owner of action 1 or QA can record it as done.*); note (*Say what was done.*; ≤ 1000 characters; no `<` `>`); result, if given, a declared type that exists and the person may read (*Name the record the action produced.* / *Document Revision … does not exist.*); a record ticked as needing no change is one the request lists (*CR-… lists no such record.*), not changed under it (*Controlled Document … was changed under CR-…; it cannot also need no change.*) and not marked already (*… is already marked as needing no change.*) | Done, Done On, Done By, What Was Done, Resulting Record; audit reason *Action 1 done: <note>*; when all are done: Implemented, Implemented On = today, Effectiveness Check Due = today + days — **only if what the change made is in force** (URS-QMS-032); otherwise the last action is refused and nothing is recorded: *CR-… is implemented once what it changed is in force. Not yet in force: Document Revision SOP-QC-001-REV-01 (Approved); Device Master Record DMR-… (out of date with this change; QA approves its next revision). Record action 2 as done once it is.*; and **only once each listed record was changed under the request or ticked as needing no change** (independent review 2026-10-15; a change given up, such as an abandoned revision, does not count): *CR-… is implemented once each record it lists is changed under it, or recorded as needing no change with the reason. Not changed: Controlled Document SOP-QC-001. Record action 2 as done once it is, marking any that need no change.* A ticked record shows No Change Needed with What was done as Why No Change Was Needed; the request no longer authorises a change of it (*Change request CR-… records that Controlled Document … needs no change; each change has its own approved change request.*). |
| **Verify: Effective** (primary) / **Verify: Not Effective** (ask Reason = the check note) | QA, while Implemented and once today ≥ Effectiveness Check Due | yes (Verified — *I have verified this.*) | Implemented (*Only an implemented change can be checked for effectiveness.*); QA (*Only QA can check the effectiveness of a change.*); date (*The effectiveness of … is checked on or after <date>.*); note (*Say how the effectiveness was checked.*) | Closed; Effectiveness = Effective / Not effective; Effectiveness Check; Closed On; audit reason *Effectiveness checked: <outcome>* |

Signing uses the dialog of module 49 (**Sign: Assess**, **Sign: Approve**, **Sign: Return**, **Sign: Reject**, **Sign: Verify Effective**, **Sign: Verify Not Effective**; statement, record and signer named, **Reason** where asked, **Your password**, button **Sign**). A wrong password records nothing (*Incorrect password. The signature was not recorded.*). The signature binds the request and its tables except Status, docstatus, Implemented On, Effectiveness Check Due, Effectiveness Alert Sent, the completion fields of actions and Changed In / Changed On of the affected records, so doing the actions and making the changes leaves the approval signature verifiable. The **Electronic signatures** section lists every signature with meaning, signer, time and act · reason.

Every request is row-locked for the duration of an act, so two people acting at once act one after the other on the latest state.

### 2.3 Banners and lists

The form headline says where the change stands: Draft after a return, orange *Returned: <reason>*; Under Assessment, blue *Waiting for QA to assess its impact.*; Awaiting Approval, orange *Assessed as a Minor change; waiting for QA approval.*; Awaiting Management Approval, orange *A Major change approved by QA; waiting for Management approval.*; Approved, blue *Approved on <date>. Carry out the actions: 1 of 2 done.*; Implemented, blue *Implemented on <date>; its effectiveness is checked on or after <date>.*; Closed, green *Closed on <date>: Effective.* (orange for *Not effective*); Rejected, red *Rejected: <reason>*; Withdrawn, grey *Withdrawn: <reason>*. The list and the title show the status with its colour (Draft red; the three waiting states orange; Approved and Implemented blue; Closed green; Rejected and Withdrawn grey), never Frappe's "Submitted". Standard filters: Type of Change, Status, Classification; search by title.

### 2.4 Who sees which requests

Quality Manager, the four Management roles and System Manager see every request. Everyone else sees, in lists, lookups, the form and the API, only the requests they raised or have an action on (`query_conditions`, `has_permission` in `hooks.py`); another person's request does not exist for them (count 0, not permitted on the form), and the person cannot act on it.

### 2.5 Alerts

Sent at the act, as system notifications (desk bell; email per the site's mail set-up, module 51):

| Notification | When | To | Subject |
| --- | --- | --- | --- |
| Change Request Awaiting Assessment | Send for Assessment | Quality Manager | *Change request CR-… is waiting for your assessment* |
| Change Request Awaiting Approval | Assess | Quality Manager | *Change request CR-… is waiting for your approval* |
| Change Request Awaiting Management Approval | QA approves a Major change | Sales Manager, Purchase Manager, Stock Manager, Manufacturing Manager | *Major change request CR-… is waiting for Management approval* |
| Change Request Decided | Approve (final), Return, Reject | Raised By | *CR-… was approved* / *was returned* / *was rejected*, with the reason in the text |

The daily job `send_change_alerts` (scheduler `daily`) adds, each in its own savepoint: for every undone action of an Approved request whose Due Date is within 7 days or past, one bell alert to its owner (*Action 2 of change request CR-… is due on <date>*, the action's text as body), once per due date (Due Alert Sent For), so an overdue action and a day the job did not run are caught up; and for every Implemented request whose Effectiveness Check Due has come and that was not yet alerted, **Change Effectiveness Check Due** to Quality Manager (*The effectiveness of change CR-… is due to be checked*; text *CR-… <title> was implemented on <date>; check whether it was effective, then close it.*), once (Effectiveness Alert Sent). A failure is logged in Error Log (*Change control alert for … could not be sent*) and retried the next day.

### 2.6 What an approved request unlocks

- **Document control (URS-QMS-014).** A new Document Revision of a Controlled Document that has an Effective Revision must name, on creation, a **Change Request** that is Approved and whose Affected Records lists that document (Record Type *Controlled Document*): *SOP-QC-001 is in effect: name the approved change request for this revision.* / *Change request CR-… is not approved for implementation.* / *Change request CR-… does not list SOP-QC-001.* The revision form marks Change Request required as soon as such a document is chosen, and its lookup (`approved_changes_for`) offers only Approved requests listing that document among those the person may see. The first revision of a new document needs none; the field is set once and never asked again later (revisions started before the rule keep their empty field).
- **Masters and specifications.** `require_approved_change(change_request, doctype, name)` is the one check every controlled record type reuses: module 6 calls it for a Quality Inspection Template in use (`master_data/specification.py`), and the planned module 1 versioning (`version-controlled-masters.md`) will call it for the nine masters. An Approved request is the only status that unlocks a change; Implemented or Closed requests no longer do, which keeps "nothing changes under it until it is approved" and ties each change to a request still being implemented.
- The **Resulting record** of an action (a Document Revision, say) is recorded when the action is done, closing the loop from request to record.
- **Once each (URS-QMS-033).** `require_approved_change(change_request, doctype, name, made_in=…)` also refuses a request that has already changed the record: *Change request CR-… has already changed Controlled Document SOP-QC-001 (in Document Revision SOP-QC-001-REV-01); each change has its own approved change request.* The check notes the change on the record being saved; the `"*"` `on_change` hook `record_changes_made` writes it to the listing (Changed In, Changed On; audit reason *Controlled Document SOP-QC-001 changed in Document Revision SOP-QC-001-REV-01*) only once that save has gone through, so a save refused later spends nothing. The request row is locked while it is checked, so two changes naming it at once are taken one after the other. A change given up before it took effect — a withdrawn risk assessment, or a document revision QA abandoned before approval (independent review 2026-10-15) — leaves the listing unused, free for another revision under the same request. A variant taking on its template's change rides on the template's listing (`part_of`). The lookups (`approved_changes_for`) no longer offer a request whose listing of the record is spent. Patch `patches/v0_1/record_changes_made_under_approved_requests.py` records, on requests still Approved when a site is upgraded, the changes already made under them (a revision or assessment naming the request; a master, specification or Quality Settings last changed under it).
- **In force before Implemented (URS-QMS-032).** `not_in_force` lists what the change made that is not yet in use: each record its changes were made in and each Resulting Record, by `in_force_rules` — a Document Revision Effective (or Superseded since), a Controlled Document Effective, a Risk Assessment Approved (or Superseded), a Risk Management File In Force, an Item, routing, operation, workstation, warehouse or equipment approved by QA, a BOM approved (submitted; cancelled under the change counts) — and each Device Master Record still Out of Date with an element changed under this request. A supplier, customer, specification, Quality Settings or licence has no approval of its own and is in force once saved.

### 2.7 What is intended but not implemented

- An action's **Record It Changes** is optional: an action such as training changes no record (the requirement's wording was clarified on 2026-10-10 to "where one exists, the record it changes").
- Printout: the default print format **Change Request (Signed)** prints the request with its assessment and approval signatures (2026-10-10).
- **Customer** is not among the affected record types on this branch (the master list of URS-MST-001 names it; added on branch `feature/mst-001-master-versions`, not yet merged).
- Email delivery of the alerts depends on the site's mail set-up (module 51).
- A change history report across requests (D2 "Change History") is the list with its filters; no separate report is specified.

## 3. Requirements and acceptance criteria

| # | Requirement / acceptance criterion | Implementation | Automated verification (`qms/test_change_control.py` unless noted) |
| --- | --- | --- | --- |
| C1 | URS-QMS-009: anyone in a business job raises it, numbered CR-YYYY-####; the administrator cannot | `ChangeRequest.before_insert`, `in_business_job`, autoname | `TestRaising.test_anyone_in_a_business_job_raises_a_numbered_draft`, `test_requests_are_numbered_in_sequence`, `test_the_system_administrator_cannot_raise_a_change`, `TestKeptAndSeen.test_every_business_job_may_raise_one_and_the_administrator_only_reads`; Cypress `[URS-QMS-009] [URS-QMS-016] the requester sends the change for assessment; an outsider cannot see it` |
| C2 | URS-QMS-009: what, why, type and affected records; declared record types; records must exist | DocType required fields, `submit_for_assessment`, `AFFECTED_TYPES` | `test_what_why_and_type_are_required`, `test_it_is_sent_for_assessment_only_with_the_records_it_affects`, `test_the_affected_records_are_the_declared_record_types`, `test_an_affected_record_must_exist`, `test_every_job_finds_change_requests_on_its_desk` |
| C3 | URS-QMS-009: nothing changes under it until approved; only the requester sends a draft; drafts edited by the requester only; system-set fields discarded | `complete_action`, `require_approved_change`, `validate`, `before_insert` | `test_nothing_is_done_under_it_before_approval`, `test_only_the_person_who_raised_it_sends_it_for_assessment`, `test_only_a_draft_is_sent_for_assessment`, `test_the_person_who_raised_it_edits_the_draft_and_nothing_after`, `test_nobody_else_edits_a_draft`, `test_what_the_system_sets_is_discarded_on_a_new_request`, `test_an_action_is_not_marked_done_on_a_draft`, `test_an_action_cannot_arrive_done` |
| C4 | URS-QMS-010: QA assesses every request before approval, recording the seven impacts, a summary and Minor/Major; regulatory ⇒ Major; check days bounded; plan checked | `assess`, `IMPACT_AREAS`, `check_plan` | `TestAssessment.*` (signed assessment; every area; summary and classification; regulatory always Major; within 730 days; plan needs an owned, active, future action; only QA; only under assessment; QA edits only assessment and plan; assessed request no longer edited); Cypress `[URS-QMS-010] QA records the impact on the form and signs the assessment` |
| C5 | URS-QMS-011: Minor by QA; Major by QA then Management, two people, each signed; never the requester; each approval its own role | `approve`, `is_management`, `MANAGEMENT` | `TestApproval.test_qa_approves_a_minor_change`, `test_a_major_change_needs_management_after_qa`, `test_one_person_cannot_approve_a_major_change_twice`, `test_the_person_who_raised_it_never_approves_it`, `test_each_approval_is_its_own_role`, `test_only_an_assessed_request_is_approved`, `test_a_wrong_password_records_nothing`; Cypress `[URS-QMS-011] QA approves, then Management, each offered only their own approval` |
| C6 | URS-QMS-011: QA, or Management for a Major change awaiting it, returns or rejects with signature and reason; a returned request is assessed afresh; rejected is final | `return_for_changes`, `reject`, `require_decider` | `test_qa_returns_it_with_a_reason_and_it_is_assessed_afresh`, `test_returning_or_rejecting_needs_a_reason`, `test_a_rejected_request_is_final`, `test_management_returns_or_rejects_only_a_major_change_awaiting_it`; Cypress `[URS-QMS-011] [URS-QMS-016] a returned change shows the reason, and the requester may withdraw it` |
| C7 | URS-QMS-011/016: Frappe's Submit never approves; no submit/cancel/amend/delete/share/import permission; the signed acts are declared | `before_submit`, DocType permissions, `SIGNING_ACTIONS` | `test_frappes_own_submit_never_approves_it`, `test_nobody_may_submit_cancel_delete_share_or_import_a_change_request`, `test_the_signed_acts_are_declared` |
| C8 | URS-QMS-012: each action done by its owner or QA with a note, once; implemented once all are done; check date set; resulting record must exist; audited with the note; signatures still verify | `complete_action`, `SYSTEM_MAINTAINED` | `TestImplementation.test_each_owner_records_their_action_and_the_change_is_implemented`, `test_qa_may_record_any_action_and_nobody_else`, `test_a_note_is_needed_and_an_action_is_done_once`, `test_a_resulting_record_must_exist`, `test_doing_the_actions_keeps_what_was_signed`, `test_each_action_done_is_in_the_audit_trail_with_its_note`; Cypress `[URS-QMS-012] each owner records their action done; the change is then implemented` |
| C9 | URS-QMS-012: actions have owner, due date and the record they change; none done before approval or changed after | child DocType (Owner, Due Date required; Record It Changes optional), `validate` | `test_the_approved_plan_cannot_be_changed`, `test_nothing_is_done_under_it_before_approval` — **partial**: the record an action changes is not required |
| C10 | URS-QMS-013: closed only by QA's signed effectiveness check, effective or not, with a note, on or after the check date (90 days unless QA set another) | `verify`, the Quality Settings lead time (URS-PLT-012), `MAX_CHECK_DAYS` | `TestEffectivenessCheck.*` (signed close; not effective; not before the date; only QA and with a note; only implemented; closed never changed); Cypress `[URS-QMS-013] QA verifies the change was effective and closes it` |
| C11 | URS-QMS-014: a new revision of a document in effect only under an approved request listing it; the first revision needs none; earlier revisions unaffected; the lookup offers only such requests the person may see | `require_change_for_revision`, `require_approved_change`, `approved_changes_for`, `document_revision.js` | `TestRevisionsUnderChangeControl.*`; Cypress `[URS-QMS-014] the new revision of the procedure offers the approved change` |
| C12 | URS-QMS-015: alerts to QA (assess, approve), Management (Major), requester (decided), owners once when due within 7 days or overdue, QA once when the check is due; declared and daily | `alert`, `send_change_alerts`, the five Notifications | `TestAlerts.*` (QA to assess then approve; Management for Major; requester hears approve/return/reject; owner once with catch-up; done or unapproved raise none; effectiveness once; one failure does not hold back; declared and daily) |
| C13 | URS-QMS-016: never deleted or cancelled; requester withdraws with a reason until approved; nobody else; visibility by role or involvement; kept 10 years and audited | `on_trash`, `before_cancel`, `withdraw`, `query_conditions`, `has_permission`, retention | `TestKeptAndSeen.*` (never deleted; never cancelled; withdraw until approved; nobody else; QA/Management/administrator see all; others only theirs; safe query conditions; ten years and audited); Cypress tests above (outsider count 0; withdraw) |

**Resolved 2026-10-10:** C9 — the requirement now reads "and, where one exists, the record it changes"; `test_an_action_that_changes_no_record_is_approved_and_done` covers an action without a record.

**Ambiguous / open (client to decide):** whether Management should also be alerted when a Major change is returned or rejected by QA (today: only the requester); whether a change found *Not effective* should open a CAPA automatically (module 34, not built); whether Customer belongs to the affected record types before module 1 ships.

| C14 | URS-QMS-032: Implemented only once everything changed under it is in force and each listed record was changed under it or recorded as needing no change with the reason; the last action is refused, naming each record not in force or not changed | `complete_action`, `not_in_force`, `in_force_rules`, `records_out_of_date_by`, `mark_no_change_needed`, `listed_not_changed` | `TestImplementedOnlyOnceEachListedRecordIsSettled.*`, `TestImplementedOnlyOnceInForce.*` (revision approved for a later date, then effective; revision still in draft; resulting document not in effect; a change that made nothing new; every rule names a field that exists); `qms/test_device_master_record.py` `TestOutOfDate.test_the_change_is_implemented_only_once_the_record_is_approved_again` |
| C15 | URS-QMS-033: one change of each listed record; recorded once saved; refused again; listed once; a withdrawn assessment does not count; lookups skip spent requests; signatures intact; migration records earlier changes | `require_approved_change`, `record_changes_made`, `record_change`, `check_listing`, `approved_changes_for`, the patch | `TestEachListedRecordChangedOnce.*`; `master_data/test_versioning.py` `test_a_request_changes_each_master_it_lists_once`; `qms/test_risk_management.py` `test_a_change_request_revises_the_file_once_and_a_withdrawn_revision_does_not_count` |

**Not covered by this feature:** CAPA (module 34), training on a change (module 37), a cross-request change history report.

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, `scripts/create_pilot_site.sh`) or a test site after `prepare_test_site`. Use the demo users of section 1 with the pilot password. Preconditions for CC-06 need a Controlled Document in effect owned by the Production Supervisor (see `document-control.md`, DC-01…DC-05); the other cases need only an existing record to list (demo item `RM-SILICONE` or the document). Prefix titles with *Browser test*; nothing is deleted afterwards (withdraw or reject the leftovers).

Each test lists: role, preconditions, steps, expected result. Record the actual result and a screenshot (banner, dialog, Electronic signatures section where relevant).

### CC-01 Raise a request; required fields; send for assessment (C1, C2, C3)
Role: Production Supervisor.
1. **Documents › Change Request › New**. Expected: the form with Title, Type of Change, What Is to Change, Why, Records Affected, Impact Assessment (read-only), Implementation.
2. Title `Browser test: hardness check`; leave Type of Change blank; What Is to Change `Add the hardness check to the incoming inspection`; Why `A supplier lot passed with the wrong hardness`; Save. Expected: refused, Type of Change is required.
3. Type of Change `Document`; Save. Expected: saved as `CR-2026-<nnnn>`, Status *Draft*, Raised By = you, Raised On = today; buttons **Send for Assessment** (primary) and **Actions › Withdraw**; no Assess.
4. Click **Send for Assessment** with no Affected Records. Expected: refused *List the records the change affects.*
5. Add Affected Records: Record Type `Controlled Document`, Record = the effective SOP (or `Item` / `RM-SILICONE`); add Actions: `Issue the new revision`, Owner = you, Due Date = today; `Train the stores team`, Owner = `stores@demo.example.com`, Due Date = today + 10. Save; **Send for Assessment**. Expected: Status *Under Assessment*; blue banner *Waiting for QA to assess its impact.*; your fields now read-only.
6. As **QA Manager**: desk bell shows *Change request CR-… is waiting for your assessment*.
7. As **System Administrator**: **Documents › Change Request › New**. Expected: no create permission (or *A change request is raised by someone in a business job.*).
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of step 5.

### CC-02 QA assesses; validation of the assessment (C4)
Role: QA Manager. Preconditions: CC-01 step 5.
1. Open the request. Expected: the seven impact selects, Effectiveness Check After (Days) = 90, Assessment and Classification editable; Title/What/Why read-only; buttons **Assess** (primary), **Return for Changes**, **Reject**.
2. Click **Assess** with nothing filled; password; **Sign**. Expected: refused *Record the impact on: Product Quality, Risk (ISO 14971), …*
3. Fill all seven with `No` except Regulatory Notification (CDSCO) = `Yes`; Assessment `Inspection procedure and training change`; Classification `Minor`; Save; **Assess**. Expected: refused *A change that needs regulatory notification is a Major change.*
4. Regulatory Notification = `No`; Effectiveness Check After (Days) = `731`; Save; **Assess**. Expected: refused *Check the effectiveness within 730 days of implementation.*
5. Days = `0`; remove every row of Actions; Save; **Assess**. Expected: refused *Plan at least one action to implement the change.*
6. Restore the two actions; Classification `Major`; Save; **Assess** (dialog **Sign: Assess**, *Reviewed: …*). Expected: Status *Awaiting Approval*; orange banner *Assessed as a Major change; waiting for QA approval.*; Assessed By = you; signatures show *Reviewed · Assess*.
7. Change Classification now; Save. Expected: refused *Change request CR-… is Awaiting Approval; it can no longer be changed this way.*
Actual result: ____  Pass/Fail: ____

### CC-03 Approval: QA then Management; nobody approves their own; no Submit (C5, C7)
Preconditions: CC-02 step 6 (Major).
1. As **Production Supervisor** (requester): open the request. Expected: no **Approve**; **Actions › Withdraw** still offered.
2. As **QA Manager**: the primary page button is not Submit. Click **Approve**; wrong password. Expected: *Incorrect password. The signature was not recorded.*; still Awaiting Approval.
3. Correct password. Expected: Status *Awaiting Management Approval*; banner *A Major change approved by QA; waiting for Management approval.*; Approved by QA = you; **Approve** no longer offered to you; Return/Reject still offered.
4. As **Management**: bell shows *Major change request CR-… is waiting for Management approval*; open it; **Approve**; sign. Expected: Status *Approved*; status pill *Approved*; banner *Approved on <today>. Carry out the actions: 0 of 2 done.*; Approved by Management = you; signatures *Reviewed*, *Approved*, *Approved*.
5. As **Production Supervisor**: bell shows *CR-… was approved*; **Actions › Withdraw** no longer offered (API: *… is Approved; it can no longer be withdrawn.*).
6. As QA: menu **Cancel** / **Delete**. Expected: refused *An approved change is implemented and closed, never cancelled.* / *A change request is never deleted.*
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 3 and 4.

### CC-04 Return and reject with reasons; Management only on a Major change awaiting it (C6)
1. Raise a second request as Production Supervisor (`Browser test: drying time`, one action due today); Send for Assessment. As **Management**: open it; look for Return/Reject. Expected: not offered (Under Assessment is QA's).
2. As **QA Manager**: **Return for Changes**; Reason empty. Expected: the dialog requires Reason. Reason `Give the validation data for the shorter time`; sign. Expected: Status *Draft*; the requester's form shows orange *Returned: Give the validation data…*; the requester hears *CR-… was returned*.
3. As requester: edit What Is to Change; Save; **Send for Assessment** again. As QA assess as `Minor`. Expected: Awaiting Approval (assessed afresh; Assessed By set again).
4. As QA: **Reject** with reason `Not needed: covered by SOP-QC-002`. Expected: Status *Rejected*; red banner *Rejected: Not needed…*; the requester hears *CR-… was rejected*; the requester cannot Send for Assessment again nor edit (refused).
Actual result: ____  Pass/Fail: ____

### CC-05 Implementation by the action owners; immutable plan (C8, C9)
Preconditions: CC-03 step 4 (Approved).
1. As **Sales** (`sales@demo.example.com`, no action): open `/desk/change-request/CR-…`. Expected: not permitted; the list does not show it.
2. As **Stores**: the list shows the request (you own an action); open it; button **Record Action Done**. Click it. Expected: dialog *Record an action as done* listing only `2. Train the stores team (due <date>)`; What was done required.
3. Note `Stores team trained on the new check`; **Record as Done**. Expected: banner *… 1 of 2 done*; row 2 Done ticked with Done On, Done By, What Was Done; **Record Action Done** no longer offered to you.
4. As **Production Supervisor**: **Record Action Done**, note `Procedure checked: it already states the hardness check; no new revision`, leave the procedure unticked under *Listed records found to need no change*; **Record as Done**. Expected: refused *CR-… is implemented once each record it lists is changed under it, or recorded as needing no change with the reason. Not changed: Controlled Document SOP-QC-….* (If CC-06 was done and its revision is effective, the procedure was changed under the request and this refusal does not appear.) Tick the procedure; **Record as Done**. Expected: Status *Implemented*; the listed row shows No Change Needed with the note as its reason; banner *Implemented on <today>; its effectiveness is checked on or after <today>* (days = 0 from CC-02).
5. As QA: on an Approved request change an action's Due Date; Save. Expected: refused (the approved plan cannot change).
Actual result: ____  Pass/Fail: ____

### CC-06 The document's next revision needs the approved request (C11)
Preconditions: a Controlled Document in effect owned by the Production Supervisor and listed by the Approved request of CC-03 (do this case before CC-05 step 4 implements it, or raise and approve another request listing the document).
1. As **Production Supervisor**: on the document click **New Revision** (or `/desk/document-revision/new` and pick the document). Expected: **Change Request** becomes required.
2. Type the request's number in Change Request. Expected: the lookup offers it; a draft or rejected request listing the document is not offered.
3. Save without a change request. Expected: refused *SOP-QC-<nnn> is in effect: name the approved change request for this revision.*
4. Pick the request; attach a private PDF; Reviewer; Summary of Change; Save. Expected: saved; Change Request filled and read-only afterwards.
5. On a brand-new document (no revision in effect): New Revision. Expected: Change Request not required.
Actual result: ____  Pass/Fail: ____

### CC-07 Effectiveness check and closure (C10)
Preconditions: CC-05 step 4 (Implemented, check due today).
1. As **Production Supervisor**: open the request. Expected: no **Verify** buttons.
2. As **QA Manager**: buttons **Verify: Effective** (primary) and **Verify: Not Effective**. Click **Verify: Effective**; Reason empty. Expected: Reason required. Reason `No hardness rejects since the change`; sign (**Sign: Verify Effective**, *Verified: I have verified this.*). Expected: Status *Closed*; green banner *Closed on <today>: Effective.*; Effectiveness = Effective; Effectiveness Check = the note; signatures end with *Verified · Verify Effective*.
3. On a request implemented with Effectiveness Check After (Days) = 30 (raise, assess Minor with 30, approve, record its action done): as QA look for the Verify buttons. Expected: not offered before the date (API: *The effectiveness of CR-… is checked on or after <date>.*).
4. As QA: edit Effectiveness Check on the closed request; Save. Expected: refused.
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of step 2.

### CC-08 Withdrawal by the requester (C13)
1. Raise `Browser test: withdraw me` as Production Supervisor; Send for Assessment. As **QA**: **Actions › Withdraw**. Expected: not offered (API: *Only the person who raised CR-… can withdraw it.*).
2. As the requester: **Actions › Withdraw**; dialog *Withdraw CR-…*; Reason empty; **Withdraw**. Expected: Reason required (*Say why the change is withdrawn.*).
3. Reason `The supplier changed the material instead`; **Withdraw**. Expected: Status *Withdrawn*; grey banner *Withdrawn: The supplier changed the material instead*; Reason Given filled; the record stays in the list.
Actual result: ____  Pass/Fail: ____

### CC-09 Due-date and effectiveness alerts (C12)
Preconditions: an Approved request with an action due within 7 days (CC-03) and an Implemented request whose check is due (CC-05); scheduler access.
1. Run `bench --site <site> execute meddevice_suite.qms.change_control.send_change_alerts`. Expected: each owner of an undone action due within 7 days sees one bell alert *Action N of change request CR-… is due on <date>*; QA sees *The effectiveness of change CR-… is due to be checked* for the implemented one; Due Alert Sent For / Effectiveness Alert Sent set.
2. Run it again. Expected: no duplicates.
3. On a test site set an action's Due Date to today − 3 (`frappe.db.set_value`, as the automated test does) and run again. Expected: its owner is alerted once (overdue caught up).
Actual result: ____  Pass/Fail: ____  Cleanup: restore the date.

### CC-10 Lists, filters, visibility and audit trail (C13)
1. As **QA Manager**: **Documents › Change Request**; filter Status = Approved, Classification = Major, Type of Change = Document; search a title. Expected: filters work; indicators coloured by status.
2. As **Management**: the same list shows every request. As **Stores**: only requests with a Stores action. As **Sales**: none of the browser-test requests.
3. Open a closed request › menu **Audit Trail**. Expected: *Created*, the updates with reasons *Action 1 done: …*, *Submitted*, *Effectiveness checked: Effective*; **Verify Integrity** reports *The audit trail and the signatures of … are intact.*
Actual result: ____  Pass/Fail: ____

### CC-11 Implemented only once what the change made is in force (C14)
Preconditions: an Approved request listing an effective document, one action owned by the Production Supervisor.
1. As **Production Supervisor**: start the document's new revision under the request (CC-06 step 4). Expected: the request's Records Affected row shows Changed In = the revision.
2. Take the revision through author and review; as **QA** set Effective Date = today + 10 and **Approve**. Expected: the revision is *Approved*, not Effective.
3. As **Production Supervisor**: **Record Action Done**. Expected: refused *CR-… is implemented once what it changed is in force. Not yet in force: Document Revision …-REV-01 (Approved). Record action 1 as done once it is.*; the action stays undone; Status *Approved*.
4. On a test site run `bench --site <site> execute meddevice_suite.qms.document_control.make_effective --args "['<revision>']"` (or wait for the date). Record the action again. Expected: Status *Implemented*.
5. With a finished good whose device master record is Current and pins the document: after step 4's revision took effect the record shows *Out of Date*; recording the last action is refused naming the device master record until QA approves its next revision.
Actual result: ____  Pass/Fail: ____

### CC-12 One approved request changes each record it lists once (C15)
1. After CC-11 step 1, as **Production Supervisor** start another revision of the same document (once the first is effective) naming the same request. Expected: the lookup no longer offers it; typed in, refused *Change request CR-… has already changed Controlled Document … (in Document Revision …); each change has its own approved change request.*
2. On an Approved request listing an item in use: change a controlled field naming the request; Save. Expected: saved, version raised, Changed In = the item. Change another controlled field naming the same request. Expected: refused *… has already changed Item …*.
3. Raise a request listing the same record twice; **Send for Assessment**. Expected: refused *… is listed twice; list each record once.*
4. Open the request › Audit Trail. Expected: an *Updated after submission* event *… changed in …* for each change; **Verify Integrity** reports the signatures intact.
Actual result: ____  Pass/Fail: ____

### Regression
- Document control's full cycle (`document-control.md`, DC-02…DC-06) and the controlled specification's change-request rule (module 6) still work: run the automated suites (section 8).

## 5. End-to-end business workflow

Demo data: the pilot loader creates the demo users; it loads no change requests or controlled documents, so the walk-through raises its own against the demo item `RM-SILICONE` and a registered SOP.

1. **Production Supervisor** raises **CR-2026-0001** *Clarify the silicone inspection* (Document; affects SOP-QC-001; actions: issue the new revision — owner Production, due today; train the stores team — owner Stores, due +10) and sends it for assessment. Data: Change Request Draft → Under Assessment; Audit Event; bell alert to QA.
2. **QA Manager** records the seven impacts, classes it Major, sets the check at 90 days, signs **Assess**. Data: signature *Reviewed*; alert to QA to approve.
3. **QA Manager** signs **Approve** (first approval) → Awaiting Management Approval; **Management** signs **Approve** → Approved, submitted. Data: signatures *Approved* ×2; alert to Management, then to the requester.
4. **Production Supervisor** starts **SOP-QC-001-REV-01** naming CR-2026-0001 (module 30), takes it through author, review and QA approval; records action 1 done with Resulting Record = the revision. **Stores** records action 2 done → Implemented, Effectiveness Check Due = +90 days. Data: Audit Events with the notes; approval signature still verifies.
5. Daily, owners whose action is due within 7 days are alerted; on the 90th day QA is alerted and signs **Verify: Effective** (*No hardness rejects in 3 months*) → Closed. Data: signature *Verified*; Closed On.
6. A request that was mistaken is **withdrawn** by its requester before approval, or **rejected** by QA with a reason; both stay in the list and the audit trail.

Cross-module data: Change Request (+ Affected Records, Actions), Electronic Signature, Audit Event, Notification Log, Document Revision (`change_request`), Quality Inspection Template (module 6 request fields).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocTypes | `Change Request` (submittable, `CR-.YYYY.-.####`, title field Title, search fields title and status; indexes on status, requested_by, effectiveness_due_on); children `Change Request Affected Record` (Record Type Select of `AFFECTED_TYPES`, Record Dynamic Link, Note) and `Change Request Action` (Action, Owner, Due Date required; Record Type/Record It Changes; completion fields read-only, allow-on-submit) |
| Server module | `meddevice_suite/qms/change_control.py`: unsigned `submit_for_assessment`, `withdraw`, `complete_action` (whitelisted POST, permission checked); signed `assess`, `approve`, `return_for_changes`, `reject`, `verify_effective`, `verify_not_effective`; `require_change_for_revision`, `require_approved_change`, `approved_changes_for` (whitelisted lookup); `send_change_alerts`; `query_conditions`, `has_permission`; helpers `check_plan`, `note`, `locked`, `require_decider`; constants `AFFECTED_TYPES`, `RESULT_TYPES`, `IMPACT_AREAS`, `BEFORE_APPROVAL`, the lead time from Quality Settings (`quality_settings.value(...)`, URS-PLT-012), `MAX_CHECK_DAYS = 730`, `MAX_NOTE = 1000`, the lead time from Quality Settings (`quality_settings.value(...)`, URS-PLT-012) |
| Controller | `qms/doctype/change_request/change_request.py`: `REQUESTER_FIELDS`, `ASSESSOR_FIELDS`, `SET_BY_CHANGE_CONTROL`, `COMPLETION_FIELDS`; `before_insert` (business job, system fields), `validate` (who edits what, compared as stored values incl. tables), `before_submit` / `before_update_after_submit` guard, never deleted or cancelled |
| Changes made (URS-QMS-032, 033) | `change_control.py`: `CHANGE_RECORD_FIELDS`, `PENDING_FLAG`, `ABANDONED`; `require_approved_change(…, made_in, part_of)`, `listing_of`, `record_changes_made` (hooks `doc_events["*"]["on_change"]`), `record_change`, `check_listing`, `in_force_rules`, `not_in_force`, `records_out_of_date_by`, `abandoned`; callers pass `made_in`: `document_revision.py` (via `require_change_for_revision`), `risk_management.require_reason_to_revise`, `master_data/versioning.py` (`version_master`, `guard_new_default_bom`, `guard_cancel`), `master_data/specification.py`, `compliance/quality_settings.py`; patch `patches/v0_1/record_changes_made_under_approved_requests.py` |
| Signed acts | `compliance/signing.py` `SIGNING_ACTIONS`: ("Change Request", "Assess" Reviewed / "Approve" Approved / "Return" Rejected / "Reject" Rejected / "Verify Effective" Verified / "Verify Not Effective" Verified); dialog `public/js/esign.js` |
| API routes | `POST /api/method/meddevice_suite.compliance.signing.sign_action`; `POST /api/method/meddevice_suite.qms.change_control.submit_for_assessment` (`name`), `…withdraw` (`name`, `reason`), `…complete_action` (`name`, `action` row name, `note_text`, `result_doctype`, `result_name`); `GET …change_control.approved_changes_for` (link query with `filters.controlled_document`); standard `/api/resource/Change Request` under the DocType permissions (submit refused) |
| Hooks (`hooks.py`) | `permission_query_conditions["Change Request"]`, `has_permission["Change Request"]`; `scheduler_events.daily` → `send_change_alerts`; `onload` of every record → `signing.onload`, `audit_timeline.onload` |
| Client scripts | `change_request.js` (editable fields per stage, record types the person may look up, act buttons, Withdraw and Record Action Done dialogs, banners), `change_request_list.js` (status indicators); `document_revision.js` (Change Request required toggle and lookup) |
| Alerts | Notifications `Change Request Awaiting Assessment`, `Change Request Awaiting Approval`, `Change Request Awaiting Management Approval`, `Change Request Decided`, `Change Effectiveness Check Due` (`qms/notification/…`), registered in `compliance/alerts.py` `ALERTS` and locked on a site; action-due alerts by `enqueue_create_notification` to the owner |
| Desk | `workspace_sidebar/documents.json` (Documents › Change Request); `setup/desk.py` `DESK_AREAS["Documents"] = ("Desk User",)` |
| Permissions (DocType JSON) | create/read/write/report/print/select for Accounts User, HR User, Item Manager, Maintenance User, Manufacturing Manager, Manufacturing User, Operator, Purchase Manager, Purchase User, QC Inspector, Quality Manager, Sales Manager, Sales User, Stock Manager, Stock User (export for the managers and Quality Manager); System Manager read/report/print/select; no submit/cancel/amend/delete/share/import for anyone |
| Compliance registries | `compliance/esign.py` `SYSTEM_MAINTAINED["Change Request"]` (status, docstatus, implemented_on, effectiveness_due_on, effectiveness_alerted), `["Change Request Affected Record"]` (change_record_type, change_record, changed_on) and `["Change Request Action"]` (done, done_on, done_by, completion_note, result_doctype, result_name, alerted_for); `compliance/fingerprint.plain_text` (no `<` `>` in notes); `compliance/retention.py` "Quality system records" 10 years; audit trail by module (QMS) |
| Roles | `setup/roles.py` `BUSINESS_ROLES` (every job-profile role but System Manager), `MANAGEMENT` (Sales Manager, Purchase Manager, Stock Manager, Manufacturing Manager), `SYSTEM_ADMINISTRATION` |
| Data flow | Form → `frappe.xcall` (unsigned acts) or signing dialog → `sign_action` → `change_control.<act>` (`locked` row lock + read permission, checks, `esign.sign`, `save` with `flags.change_control`, `alert`) → DB; final approve → `doc.submit()`; `complete_action` → row update, Implemented when all done; daily job → undone actions due ≤ today + 7 on Approved requests, Implemented requests with the check due, each in a savepoint |

## 7. Defects, edge cases and risks

**Confirmed and fixed during build and review (commits 2b7f24ed, a950d677, d65c28ba; each with a test):** a signature bound a table row's docstatus, which the parent's submission changes, so an approval read as tampered once the actions were done; a required Select took its first option silently (Type of Change must now be chosen); the Management browser user was signed out when the previous person's form stayed open while sessions changed.

**Found in the foundation review of 2026-10-12 and fixed on `fix/change-control-and-document-review` (each with a test that fails without the fix):** the last action could be recorded, and the change implemented, while the revision it made waited for its effective date, or the device master record it put out of date was not yet approved again (URS-QMS-032); one approved request authorised any number of changes of a record it listed (URS-QMS-033).

**Open / potential (not verified by a test; listed for the record):**
- A device master record out of date names, on each changed element, the request of the latest change of that element. When a second request changes the same element before the record is approved again, the first request no longer finds the record and is not held back by it (the second is).
- A master changed again under another request before the upgrade hides the earlier request; the migration patch leaves that listing unrecorded and logs it (*Change requests: listings with no change found under them*) for QA to look at.
- A variant taking on its template's controlled change does not spend a listing of the variant itself in the same request.
- The plan's due dates are re-checked at every approval. A Major change assessed with an action due *today* and approved by Management on a later day is refused (*Action 1 is due in the past; set a new due date.*); the plan cannot be edited while awaiting approval. Recovery: Return for Changes → the requester re-sends → QA re-assesses with new dates. A follow-up could check the dates at assessment only, or let QA re-date the plan while a request awaits approval.
- *Record It Changes* on an action is optional by requirement (wording clarified 2026-10-10).
- Customer is not an affected record type on this branch; a change to a customer master cannot yet be listed (module 1 branch adds it).
- A person holding both Quality Manager and a Management role can give the first approval of a Major change as QA but not the second (verified by test); a second such person is still needed for Major changes if Management is one person.
- Alerts reach the desk bell; email depends on mail set-up (module 51). The daily job catches up missed days; with the scheduler off, due alerts wait for the next run.
- An action owner who leaves (account disabled) after approval cannot record the action; QA records it on their behalf (by design) — the approved plan cannot be re-assigned.
- System Manager reads every request (module 47 matrix) though the requirement names QA and Management; the administrator cannot act on one.
- Withdrawal is unsigned (the requirement asks for a reason only); it is audited with the reason.

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (`feature/6-controlled-specifications`, local test site, macOS); CI = GitHub Actions on a fresh site:

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site <test-site> run-tests --module meddevice_suite.qms.test_change_control` (classes `TestRaising` URS-QMS-009, `TestAssessment` 010, `TestApproval` 011, `TestImplementation` 012, `TestEffectivenessCheck` 013, `TestRevisionsUnderChangeControl` 014, `TestAlerts` 015, `TestKeptAndSeen` 016; 76 `test_` methods; every test also asserts no alert template failed) | included in the full suite below |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed (2026-10-10) |
| Browser suite, retries off | `bench --site <test-site> run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` (`cypress/integration/change_control.js`: 7 tests, titles in section 3, run as requester, QA, Management, Stores and an outsider) | 59 of 59 passed (2026-10-10) |
| Semgrep (Frappe rules) | after pre-commit formatting | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| CI (fresh site) | GitHub Actions on the current branch | in progress when this document was written |
| Manual browser tests CC-01…CC-10 | — | **not executed** (no manual tester session); the automated browser journey covers CC-01 steps 3 and 5 (and the outsider of CC-05 step 1), CC-02 step 6, CC-03 steps 1, 3 and 4, CC-04 step 2 (return), CC-05 steps 2–4, CC-06 steps 1–2, CC-07 step 2, CC-08 steps 2–3 |

**2026-10-12 (URS-QMS-032, URS-QMS-033):** tests added in `qms/test_change_control.py` (`TestImplementedOnlyOnceInForce`, `TestEachListedRecordChangedOnce`), `qms/test_device_master_record.py`, `qms/test_risk_management.py`, `master_data/test_versioning.py`; pre-commit and the Frappe Semgrep rules pass on the changed files. Server tests, migrate and the browser suite were **not run** by the author (the shared test site was in use); CC-11 and CC-12 not executed.

Untested: CC-09 on a real nightly scheduler (the job is called directly in the automated tests); email delivery; the due-date edge case of section 7; the client's real change requests (the pilot has none loaded).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Any business job raises CR-YYYY-#### with what, why, type and affected records; the administrator cannot; nothing is done under it before approval | Verified (automated + browser) |
| QA records the seven impacts, summary and Minor/Major with a signature; regulatory ⇒ Major; check days 0–730; plan checked | Verified (automated + browser) |
| Minor: QA approves; Major: QA then Management, two people, never the requester; return or reject with signed reason; Frappe Submit refused | Verified (automated + browser) |
| Actions done by owner or QA with a note, once; plan immutable after approval; Implemented when all done; audited; signatures still verify | Verified (automated + browser) |
| Closed only by QA's signed effectiveness check on or after the due date, Effective or Not effective | Verified (automated + browser) |
| A new revision of a document in effect needs an approved request listing it; first revision needs none; lookup offers only those | Verified (automated + browser) |
| Alerts to QA, Management, requester; owners once when due within 7 days or overdue; QA once when the check is due; catch-up and isolation | Verified (automated, job called directly) |
| Never deleted or cancelled; requester withdraws with a reason until approved; visibility by role or involvement; 10-year retention | Verified (automated + browser) |
| Form banners, editable fields per stage, act buttons per person | Verified for the browser journey (automated); full manual CC-01…CC-10 **not tested** |
| Scheduler-driven alerts on a live site; email delivery | **Not tested** |
| Implemented only once the revision, record and device master record are in force; the last action refused naming each (URS-QMS-032) | Tests written; **not yet run** |
| One change of each listed record, recorded on the request; refused again; lookups skip spent requests; migration of earlier changes (URS-QMS-033) | Tests written; **not yet run** |
| CI green on a fresh site for the current commit | **Pending** |
| Client decisions: action record required, Management alerted on return/reject, CAPA on *Not effective*, Customer as affected type | **Open** |
