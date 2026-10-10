# Document control (module 30) — feature document and browser test guide

Requirements: **URS-QMS-001 … URS-QMS-008** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-003, URS-CMP-030 (signatures shown on screen and on printouts; signing from the record), URS-QMS-014 (a new revision of a document in effect needs an approved change request — module 36, documented in `change-control.md`).
Code: `meddevice_suite/qms/document_control.py`, `qms/doctype/controlled_document/`, `qms/doctype/document_revision/`, `qms/print_format/controlled_copy/`, `qms/notification/document_review_due/`, `public/js/esign.js` (signing dialog, signatures table), `workspace_sidebar/documents.json`, hooks in `meddevice_suite/hooks.py`; file protection in `compliance/evidence_files.py`; signed acts registered in `compliance/signing.py`.
State on 2026-10-10: built on 2026-10-07 (commits c953e68f, 15970df7, 49fdd4f8), reviewed in a robustness review whose findings were fixed in 15970df7, merged to `main`; on the current branch (`feature/6-controlled-specifications`) only the change-request lookup was generalised for module 6. Server and browser suites green on 2026-10-10 (section 8).

## 1. Feature overview

**Purpose.** Every document of the quality system (SOP, work instruction, form, specification, packaging and labelling, quality manual, external document) is registered once by QA under a fixed number and issued in numbered revisions. A revision is a file: its author signs it, someone else reviews it, QA approves it, each with an electronic signature and a password; the file is fingerprinted when the author signs and cannot be swapped afterwards. An approved revision takes effect on its date and supersedes the one before; people outside QA see only the revision in effect (and the drafts they write or review), so nobody works from an old copy. QA withdraws a document with a signed reason; nothing is ever deleted. A printout is a controlled copy only while the revision is effective. The process owner and QA are warned 30 days before the periodic review falls due.

**Why it matters.** ISO 13485 4.2.4 requires documents to be reviewed and approved before issue, to be available at the point of use in their current revision, and obsolete documents to be identified and kept; 21 CFR 11.50 and 11.70 require signatures with their meaning, bound to the record. The client's diagram D2 names "Document Control" with review, approval and version control. The numbering, the approver being neither author nor reviewer, and the review period were decided on the client's instruction of 2026-10-07.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Registers documents; approves revisions; returns them; withdraws documents; sees every revision; receives the review alert |
| Production Supervisor (`production.supervisor@demo.example.com`), or any other job named as process owner | Manufacturing User (etc.) | Starts and authors revisions of the documents it owns; receives the review alert as process owner |
| Any desk user named as reviewer (e.g. QC Inspector, `qc.inspector@demo.example.com`) | QC Inspector (etc.) | Signs the review or returns the revision |
| Everyone else (e.g. Sales, `sales@demo.example.com`) | any business role | Reads the effective revision; prints the controlled copy |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Reads; cannot register, revise, sign or withdraw |

Only Quality Manager can create or edit a Controlled Document. Every desk user may read Controlled Documents and may create a Document Revision, but the server lets only the document's process owner or QA start one. There is no submit, cancel, delete, share or import permission for any role on either record type.

**Prerequisites.** The e-signature service (module 49) and the audit trail (module 50) are installed (part of the app's configuration, applied by `bench migrate`); the people have their own accounts and passwords (shared accounts cannot sign); the document to be issued exists as a PDF or other file. Change control (module 36) is needed for every revision after the first of a document in effect.

## 2. Detailed functional explanation

### 2.1 The register: Controlled Document

Navigation: **Documents › Controlled Document** (the Documents area is shown on every job's desk), route `/desk/controlled-document/new`.

| Field (label on screen) | Set by | Rule |
| --- | --- | --- |
| Title | QA | Required; may be corrected later (a correction is audited) |
| Document Type | QA | Required; one of SOP, Work Instruction, Form, Specification, Packaging and Labelling, Quality Manual, External Document; set once |
| Department | QA | Required; one of QA, QC, PRD, STR, PUR, SAL, ENG, HR, MGT, REG; set once |
| Process Owner | QA | Required; a user; may start revisions and receives the review alert |
| Review Every (Years) | QA | Default 2; at least 1 (*A document is reviewed at least every year.*) |
| Status | system | Draft → Effective (first revision in effect) → Obsolete (withdrawn) |
| Effective Revision, Effective Since, Next Review | system | Set when a revision takes effect; Next Review = effective date + Review Every (Years) |
| Withdrawn Because | signature only | Shown once Obsolete |
| Review Alert Sent For | system | Bookkeeping of the review alert; outside the signature |

Record name: `TYPE-DEPARTMENT-###` with type codes SOP, WI (Work Instruction), FRM (Form), SPEC (Specification), PKG (Packaging and Labelling), QM (Quality Manual), EXT (External Document): `SOP-QA-001`, `WI-PRD-003`. Numbers run per type and department.

Status, Effective Revision, Effective Since, Next Review and Withdrawn Because change only through the revisions (*The status and effective revision of a document change only through its revisions.*); a withdrawn document cannot be edited (*SOP-QA-001 has been withdrawn and cannot be changed.*); delete is refused for everyone (*A controlled document is withdrawn, never deleted.*).

**Buttons** on a saved document: **Effective Revision** (opens the revision in effect, when there is one); **Revision in Progress** (opens it, when one exists) or else **New Revision** (process owner or QA; opens a new Document Revision with the document filled in); **Actions › Withdraw** (QA only, only when no revision is in progress; signed, asks a reason). A withdrawn document shows a red banner *Withdrawn: <reason>* and no buttons.

### 2.2 The content: Document Revision

Navigation: **Documents › Document Revision**, or **New Revision** on the document. Route `/desk/document-revision/<document>-REV-<nn>`.

| Field (label on screen) | Set by | Rule |
| --- | --- | --- |
| Controlled Document | author | Required; set once |
| Title, Document Type | system | Copied from the document |
| Revision | system | `00` for the first, then `01`, `02` … (`100` after `99`); record name `<document>-REV-<nn>` |
| Document File | author (draft only) | A **private** file; required before the author signs |
| File Fingerprint (SHA-256) | signature only | Taken when the author signs |
| Summary of Change | author (draft only) | Required for every revision after `00` |
| Change Request | author, on creation | Required when the document already has an effective revision (URS-QMS-014); the lookup offers only approved change requests that list this document |
| Status | signed acts only | Draft, In Review, Awaiting Approval, Approved, Effective, Superseded, Obsolete |
| Author | system | The person who created the revision |
| Reviewer | author (draft only) | Required before the author signs; not the author |
| Approved By | signature only | The QA person who approved |
| Effective Date | QA, while Awaiting Approval | Lifted to the approval day if earlier or empty |
| Superseded By | system | The revision that replaced this one |

**Creation rules** (server, whatever the path): the document must not be withdrawn (*SOP-QA-001 has been withdrawn; it cannot be revised.*); only its process owner or QA may start a revision (*Only the process owner of SOP-QA-001 or QA can start a new revision.*); one revision in progress at a time, "in progress" being Draft, In Review, Awaiting Approval or Approved (*SOP-QA-001 already has a revision in progress.*); values sent for File Fingerprint, Approved By, Effective Date or Superseded By are discarded.

**Edit rules**: while Draft, the author may change Document File, Summary of Change and Reviewer; while Awaiting Approval, QA may set Effective Date; any other change by anyone is refused (*Revision SOP-QA-001-REV-00 is In Review; it can no longer be changed this way.*). Frappe's Submit and any change after submission are refused unless made by a signed act (*Revision … is approved and moved on only by signing it.*), also for code that skips permission checks. Delete (*A document revision is never deleted.*) and cancel (*An approved revision is superseded or withdrawn, never cancelled.*) are refused for everyone, the Administrator included.

### 2.3 Status transitions and who signs what

```
Draft ──Sign as Author──▶ In Review ──Sign as Reviewer──▶ Awaiting Approval ──Approve (QA)──▶ Approved ──effective date──▶ Effective ──next revision──▶ Superseded
  ▲                            │                                   │                                                              │
  └────── Return for Changes ──┴───────────────────────────────────┘                                      Withdraw (document) ──▶ Obsolete
```

| Button (on the revision's form, shown only when the form has no unsaved change) | Offered to | Signature meaning / statement in the dialog | Server checks (message) | Result |
| --- | --- | --- | --- | --- |
| **Sign as Author** (primary) | the author, while Draft | Authored — *I wrote this and it is complete and correct.* | Draft (*Only a draft can be signed by its author.*); author (*Only the author of … can sign it as author.*); file attached (*Attach the document file before signing.*); private (*Attach the document as a private file.*); file present on disk (*The document file … is missing; attach it again.*); reviewer chosen (*Choose the reviewer before signing.*), not the author (*The reviewer cannot be the author.*); summary for revisions after `00` (*Say what changed in this revision.*) | fingerprint stored; In Review |
| **Sign as Reviewer** (primary) | the reviewer, while In Review | Reviewed — *I have reviewed this and found it fit for approval.* | In Review (*Only a revision in review can be signed by its reviewer.*); reviewer (*Only the reviewer of … can sign it as reviewer.*) | Awaiting Approval |
| **Return for Changes** (asks Reason) | the reviewer or QA, while In Review or Awaiting Approval | Rejected — *I reject this.* | status (*Only a revision in review or awaiting approval can be returned.*); person (*Only the reviewer or QA can return … for changes.*); reason (*Say what must change.*) | Draft; the author edits and signs again (a new fingerprint) |
| **Approve** (primary) | QA who is neither author nor reviewer, while Awaiting Approval | Approved — *I approve this.* | status (*Only a reviewed revision can be approved.*); QA (*Only QA can approve a document revision.*); not author (*The author of … cannot approve it.*); not reviewer (*The reviewer of … cannot also approve it.*); file unchanged (*The document file has changed since it was signed by its author; it must be authored again.*) | Effective Date = later of the date set and today; Approved By; submitted (docstatus 1); Approved, or Effective at once if the date is today |
| **Actions › Withdraw** (on the document; asks Reason) | QA, when no revision is in progress | Approved — *I approve this.* (act *Withdraw*) | QA (*Only QA can withdraw a controlled document.*); not already (*… has already been withdrawn.*); reason (*Say why the document is withdrawn.*); nothing in progress (*Finish or return the revision in progress before withdrawing ….*) | document Obsolete with Withdrawn Because, Next Review cleared; its effective revision Obsolete |

The signing dialog (`esign.js`) is titled **Sign: <act>** (*Sign: Author*, *Sign: Review*, *Sign: Return*, *Sign: Approve*, *Sign: Withdraw*), states the meaning, names the record and the signer (*Document Revision SOP-QA-001-REV-00, signed by <name>. Your signature is recorded with the date and time and is legally binding.*), asks **Reason** where the act needs one and **Your password**, and has one button **Sign**. A wrong password signs nothing (*Incorrect password. The signature was not recorded.*), clears the password box and counts towards the login lockout; a shared account (Administrator) or a disabled account cannot sign. Each signature chains to the one before and binds the record's content except the fields the system maintains afterwards (Status, docstatus, Superseded By on a revision; Status, Effective Revision, Effective Since, Next Review on a document).

After a signature the form reloads and shows the **Electronic signatures** section (Meaning, Signed by, Date and time, Act · reason). A banner says whether the revision may be used: green *Effective since <date>. This is the revision in use.*; blue *Approved; takes effect on <date>.*; red *Superseded by <revision>. Do not use this revision.*; red *The document has been withdrawn. Do not use this revision.* Lists and the title show the real status (Draft red, In Review / Awaiting Approval orange, Approved blue, Effective green, Superseded / Obsolete grey), never Frappe's "Submitted".

### 2.4 Taking effect and superseding

`make_effective` runs inside the approval when the effective date is today, and otherwise from the daily job `make_due_revisions_effective` (scheduler `daily`) for every Approved revision whose date has come. It locks the document, refuses a revision that is not Approved (*Only an approved revision can take effect.*) or whose document was withdrawn meanwhile (*SOP-QA-001 has been withdrawn; SOP-QA-001-REV-01 cannot take effect.*), marks **every** earlier Effective revision Superseded with Superseded By, sets the revision Effective and the document Effective with Effective Revision, Effective Since and Next Review. Each revision takes effect in its own savepoint: one that fails is logged in Error Log (*Document revision <name> could not take effect*) and retried the next day without holding back the others.

### 2.5 Who sees which revisions

Outside Quality Manager and System Manager, a person sees in lists, lookups, the form and the API only revisions that are Effective, or that they author or review (`revision_query_conditions`, `revision_has_permission` in `hooks.py`). A draft, superseded or obsolete revision of someone else's document does not exist for them (list count 0, *Not permitted* on the form). Everyone may read every Controlled Document, and the **Effective Revision** button takes them to the revision in use.

### 2.6 The file is evidence

Once the author has signed, the revision's file is an evidence file (`compliance/evidence_files.py`): nobody, not even the Administrator, may delete it, make it public, rename it or re-attach it (*<file> is part of a record's evidence and cannot be changed.*); a draft's file can still be replaced by its author before signing. The fingerprint is checked again at approval; a changed file sends the revision back to be authored.

### 2.7 Controlled copy

The revision's default print format is **Controlled Copy** (Print on the form, or `/printview?doctype=Document%20Revision&name=<revision>&format=Controlled%20Copy`). An Effective revision prints with a green banner *CONTROLLED COPY · Printed on <date and time>. Check the system for the current revision before use.* and its Next review date; any other status prints with a red banner *UNCONTROLLED – NOT FOR USE · Status: <status>* (and *Superseded by <revision>* where it applies) and no review date. Both show document, revision, type, department, effective date, summary of change (*First issue* for `00`), file name, the SHA-256 fingerprint (*Not yet signed by its author* before the author signs) and the **Electronic signatures** table (Meaning, Signed by with name and account, Date and time, Act and reason), or *Not yet signed.*

### 2.8 Periodic review alert

The daily job `send_review_alerts` (scheduler `daily`) sends the system notification **Document Review Due** (subject *SOP-QA-001 is due for review on <date>*; text *SOP-QA-001 <title> is due for its periodic review on <date>. Review it, and issue a new revision or confirm it is still current.*) to the document's Process Owner and everyone with the Quality Manager role, for each Effective document whose Next Review is on or before today + 30 days — an overdue review and a day the job did not run are therefore caught up. Each review date is alerted once (Review Alert Sent For); a new revision taking effect sets a new Next Review, which is alerted afresh. A failed send is logged (*Review alert for document <name> could not be sent*) and retried the next day. Alerts appear in the desk bell; email follows the site's mail set-up (module 51).

### 2.9 What is intended but not implemented

- Linking a revision to the training it requires (module 37) and to the equipment or product it governs — not in URS-QMS-001…008.
- An "External Document" is registered and issued exactly like an internal one (author, reviewer, QA); no separate handling of the external issuer's revision is specified or built.
- Email delivery of the review alert depends on the site's mail configuration (module 51).
- Periodic re-verification of the file on disk after approval: the fingerprint is checked at approval only; afterwards the file is protected through Frappe, not re-hashed.

## 3. Requirements and acceptance criteria

| # | Requirement / acceptance criterion | Implementation | Automated verification (`qms/test_document_control.py` unless noted) |
| --- | --- | --- | --- |
| C1 | URS-QMS-001: registered by QA with a unique number TYPE-DEPARTMENT-### and a process owner | `ControlledDocument.autoname`, `TYPE_CODES`; permissions (create: Quality Manager only) | `TestRegisterAndRevise.test_documents_are_numbered_by_type_and_department` |
| C2 | URS-QMS-001: numbered revisions, one in progress at a time; started only by the process owner or QA | `DocumentRevision.autoname`, `next_revision_number`, `before_insert`, `IN_PROGRESS` | `test_revisions_are_numbered_from_00_one_at_a_time`, `test_only_the_process_owner_or_qa_starts_a_revision`, `TestTakingEffectIsGuarded.test_revision_numbers_continue_past_99`; Cypress `[URS-QMS-001] QA registers a document and its owner drafts the first revision`, `[URS-QMS-001] the process owner is offered a new revision; others are not` |
| C3 | URS-QMS-002: signed by author, reviewed by another, approved by QA who is neither; each a signature | `author`, `review`, `approve`; `SIGNING_ACTIONS` | `TestReviewAndApproval.test_authored_reviewed_and_approved_by_three_people_with_signatures`, `test_the_author_cannot_be_the_reviewer`, `test_only_the_assigned_reviewer_reviews`, `test_only_qa_approves_and_never_its_own_revision`, `TestSeparationOfSigners.test_the_reviewer_cannot_also_approve`; Cypress `[URS-CMP-030] [URS-QMS-002] the author signs from the form; only the author is offered it`, `… the reviewer signs, then QA approves; nobody can just submit` |
| C4 | URS-QMS-002: reviewer or QA returns it with a reason; the reason is kept exactly | `return_for_changes`, `plain_text` | `test_a_returned_revision_goes_back_to_its_author_with_the_reason`, `TestSeparationOfSigners.test_a_reason_is_kept_exactly_as_written` |
| C5 | URS-QMS-002: every revision after the first states what changed | `author` (checked at signing) | `test_every_revision_after_the_first_says_what_changed` |
| C6 | URS-QMS-003: file fingerprinted at authoring; approval refused if changed; file must be private and present | `file_fingerprint`, `approve` | `TestSignedContentIsLocked.test_approval_is_refused_if_the_file_changed_after_the_author_signed`, `test_the_file_must_be_private`, `TestNoWayAroundTheSignedActs.test_a_missing_file_is_reported_not_crashed_on` |
| C7 | URS-QMS-003: file cannot be deleted, made public or replaced once fingerprinted; a draft's can | `compliance/evidence_files.py` `EVIDENCE` | `test_a_fingerprinted_file_cannot_be_deleted_made_public_or_replaced`, `test_a_draft_file_can_still_be_replaced_by_its_author` |
| C8 | URS-QMS-003: no change, submit or change after submit except through the signed acts | `DocumentRevision.validate`, `before_submit`, `before_update_after_submit`, `before_insert` discards | `test_a_signed_revision_cannot_be_edited`, `TestNoWayAroundTheSignedActs.test_frappes_submit_cannot_approve_a_revision`, `test_even_code_skipping_permissions_cannot_submit_or_change_a_revision_unsigned`, `test_an_approved_revision_cannot_be_changed_through_the_api`, `test_what_only_the_signed_acts_set_is_discarded_on_creation`, `TestControlledCopy.test_nobody_may_submit_a_revision_except_through_the_signed_approval` |
| C9 | URS-QMS-004: effective on its date, never before approval; the previous revision superseded; document shows effective revision and next review | `approve`, `make_effective`, `make_due_revisions_effective` | `TestTakingEffect.test_an_approved_revision_takes_effect_and_supersedes_the_previous_one`, `test_a_future_effective_date_waits_for_the_daily_run`, `test_a_revision_never_takes_effect_before_its_approval`, `test_one_failing_revision_does_not_hold_back_the_others`, `TestTakingEffectIsGuarded.test_only_an_approved_revision_of_a_live_document_takes_effect`, `test_every_earlier_effective_revision_is_superseded`, `test_a_document_is_reviewed_at_least_every_year` |
| C10 | URS-QMS-005: outside QA only effective revisions and one's own drafts are seen, on every route | `revision_query_conditions`, `revision_has_permission` | `TestPointOfUse.test_outside_qa_only_effective_revisions_are_seen`, `TestOutsidersCannotReachDrafts.test_no_api_route_shows_an_outsider_a_draft_or_superseded_revision`; Cypress `[URS-QMS-005] someone outside the revision cannot see it` |
| C11 | URS-QMS-005: no document or revision deleted or cancelled | `on_trash`, `before_cancel` on both controllers | `TestPointOfUse.test_nothing_is_deleted_or_cancelled` |
| C12 | URS-QMS-006: QA withdraws with signature and reason, only with nothing in progress; document and revision Obsolete and kept; no further revision or edit | `withdraw`, `lock_document` | `TestWithdrawal.test_qa_withdraws_a_document_with_a_signed_reason`, `test_only_qa_withdraws_and_only_with_a_reason_and_nothing_in_progress`; Cypress `[URS-QMS-006] QA withdraws the document with a signature and a reason` |
| C13 | URS-QMS-007: owner and QA alerted once when review due within 30 days, overdue included, catch-up, again per new date; withdrawn documents not | `send_review_alerts`, Notification *Document Review Due*, `review_alerted_for` | `TestPeriodicReviewAlerts.*` (owner and QA once; overdue alerted and later waits; new date afresh; withdrawn none; one failure does not hold back; runs daily) |
| C14 | URS-QMS-008: controlled copy with signatures only while effective; otherwise uncontrolled; default printout | print format `Controlled Copy`, `signatures_for_print` | `TestControlledCopy.test_an_effective_revision_prints_as_a_controlled_copy_with_its_signatures`, `test_any_other_revision_prints_as_uncontrolled`, `test_it_is_the_revisions_default_printout`; Cypress `[URS-QMS-008] [URS-CMP-003] the effective revision prints as a controlled copy with its signatures` |
| C15 | URS-QMS-008: the form says whether the revision is in use and offers each act only to the person allowed; no other way to approve | `document_revision.js`, `controlled_document.js`, `onload` (`revision_in_progress`) | `TestControlledCopy.test_the_form_knows_whether_a_revision_is_in_progress`, `test_nobody_may_submit_a_revision_except_through_the_signed_approval`; Cypress tests above (buttons offered / not offered, no Submit, banner *This is the revision in use*, status pill) |

**Partial / interpretation (for the record):** C8 — QA may set Effective Date on the form while the revision is Awaiting Approval without a signature; the approval signature then binds that date (Effective Date is not among the system-maintained fields), so the signed act covers it. The requirement's "changed … except through the signed acts" is read as "no unsigned change survives into an approved revision". System Manager sees every revision (module 47's rule that the administrator may read every regulated record), while URS-QMS-005 says "outside QA"; the administrator cannot author, review, approve or withdraw.

**Ambiguous / open (client to decide):** whether an External Document (a supplier's or a standard) should carry the external issuer's own revision identifier; whether the review period should also be adjustable per document after it is effective (today: changing Review Every (Years) does not move Next Review until the next revision takes effect — section 7).

**Not covered by this feature:** training on a new revision (module 37); distribution lists of paper copies; document templates.

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, see `scripts/create_pilot_site.sh`) or a test site after `prepare_test_site`. Use the demo users above with the pilot password; use a throw-away PDF (the browser suite's fixture `cypress/fixtures/controlled-document.pdf` serves). Register test documents under department **QC** with a title starting *Browser test* so they can be told apart; nothing is deleted afterwards (withdraw them in DC-09). Never use a real procedure.

Each test lists: role, preconditions, steps, expected result. Record the actual result and a screenshot (form with its banner, the signing dialog, the Electronic signatures section where relevant).

### DC-01 QA registers a document; only QA can (C1)
Role: QA Manager.
1. Open **Documents › Controlled Document › New** (`/desk/controlled-document/new`). Expected: fields Title, Document Type, Department, Process Owner, Review Every (Years) = 2.
2. Title `Browser test incoming inspection`, Document Type `SOP`, Department `QC`, Process Owner = `production.supervisor@demo.example.com`; set Review Every (Years) = `0`; Save. Expected: refused *A document is reviewed at least every year.*
3. Review Every (Years) = `2`; Save. Expected: saved as `SOP-QC-<nnn>`; Status *Draft*; buttons **New Revision** and **Actions › Withdraw** (QA, nothing in progress); no **Effective Revision** yet.
4. Register a second document, Document Type `Work Instruction`, Department `PRD`. Expected: name `WI-PRD-<nnn>`.
5. As **Sales**: **Documents › Controlled Document › New**. Expected: no create permission (the New button is absent or the save is refused).
Actual result: ____  Pass/Fail: ____  Evidence: screenshot of step 3.

### DC-02 The process owner drafts the first revision; required fields (C2, C6)
Role: Production Supervisor (the process owner). Preconditions: DC-01 step 3.
1. Open `SOP-QC-<nnn>`; click **New Revision**. Expected: a new Document Revision with Controlled Document filled; Change Request not required (no revision in effect).
2. Set Reviewer = `qc.inspector@demo.example.com`; Save without a file. Expected: saved as `SOP-QC-<nnn>-REV-00`, Status *Draft*, Author = you; button **Sign as Author**.
3. Click **Sign as Author**, enter your password, **Sign**. Expected: refused *Attach the document file before signing.*; still Draft.
4. Attach the PDF in **Document File** as a **private** file; set Reviewer = yourself; Save; **Sign as Author**. Expected: refused *The reviewer cannot be the author.*
5. Reviewer = `qc.inspector@demo.example.com`; Save. As the same user, open the document again and click **New Revision**. Expected: the button reads **Revision in Progress** instead and opens REV-00.
6. As **Sales** (not the owner): open `WI-PRD-<nnn>` and look for **New Revision**. Expected: not offered; **Documents › Document Revision › New** with that document is refused *Only the process owner of WI-PRD-<nnn> or QA can start a new revision.*
Actual result: ____  Pass/Fail: ____

### DC-03 Author signs; wrong password; the signed draft is locked (C3, C6, C8)
Role: Production Supervisor. Preconditions: DC-02 step 5.
1. Open REV-00; click **Sign as Author**. Expected: dialog **Sign: Author** with *Authored: I wrote this and it is complete and correct.* and **Your password**; no Reason box.
2. Enter a wrong password; **Sign**. Expected: *Incorrect password. The signature was not recorded.*; dialog stays open with the password cleared; Status still Draft.
3. Enter the correct password; **Sign**. Expected: form reloads; Status *In Review*; **File Fingerprint (SHA-256)** filled; **Electronic signatures** shows *Authored*, your name, the time, act *Author*; no button for you.
4. Change **Summary of Change** and Save. Expected: refused *Revision SOP-QC-<nnn>-REV-00 is In Review; it can no longer be changed this way.*
5. Open the attached file's record (click the file › its File form) and try to delete it or untick *Private*. Expected: refused *<file> is part of a record's evidence and cannot be changed.*
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 1 and 3.

### DC-04 Reviewer returns with a reason; author re-signs; reviewer signs (C3, C4)
1. As **QC Inspector**: open REV-00. Expected: buttons **Sign as Reviewer** and **Return for Changes**; no Sign as Author.
2. Click **Return for Changes**; leave Reason empty; password; **Sign**. Expected: the dialog's Reason is required (refused before sending), or the server refuses *Say what must change.*
3. Reason `Step 4 misses the sampling plan`; password; **Sign**. Expected: Status *Draft*; signatures show *Rejected* with act *Return · Step 4 misses the sampling plan*.
4. As **Production Supervisor**: replace **Document File** with another private PDF; Save; **Sign as Author**. Expected: In Review again; a new *Authored* row; the fingerprint changed.
5. As **QC Inspector**: **Sign as Reviewer**. Expected: dialog **Sign: Review** with *Reviewed: I have reviewed this and found it fit for approval.*; after signing Status *Awaiting Approval*.
6. As **Sales**: open REV-00 by URL. Expected: not permitted (the revision is not effective and you are neither author nor reviewer).
Actual result: ____  Pass/Fail: ____

### DC-05 QA approves; takes effect; no Submit anywhere (C3, C8, C9, C15)
Role: QA Manager. Preconditions: DC-04 step 5.
1. Open REV-00. Expected: buttons **Approve** (primary) and **Return for Changes**; the page's primary button is **not** Submit; Effective Date is editable.
2. Set Effective Date = yesterday; Save; **Approve** with the password. Expected: dialog **Sign: Approve** (*Approved: I approve this.*); afterwards Status *Effective*, Effective Date = today (lifted), Approved By = you; green banner *Effective since <today>. This is the revision in use.*; the title's status pill reads *Effective*.
3. Open `SOP-QC-<nnn>`. Expected: Status *Effective*, Effective Revision = REV-00, Effective Since = today, Next Review = today + 2 years; button **Effective Revision**.
4. On REV-00 try menu **Cancel**. Expected: not offered, or refused *An approved revision is superseded or withdrawn, never cancelled.* Menu **Delete**: refused *A document revision is never deleted.*
5. As **Sales**: open REV-00 and `SOP-QC-<nnn>`. Expected: both readable; banner *This is the revision in use*; no act buttons.
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 2 and 3.

### DC-06 A second revision needs a change request; supersedes the first; visibility (C2, C5, C9, C10)
Preconditions: DC-05; an approved Change Request listing `SOP-QC-<nnn>` (see `change-control.md`, CC-01…CC-03; or skip steps 1–2 if none and record *Blocked*).
1. As **Production Supervisor**: on `SOP-QC-<nnn>` click **New Revision**. Expected: **Change Request** is marked required and its lookup offers only the approved request(s) listing this document.
2. Save without a change request. Expected: refused *SOP-QC-<nnn> is in effect: name the approved change request for this revision.*
3. Pick the request; attach a private PDF; Reviewer = QC Inspector; leave Summary of Change empty; Save; **Sign as Author**. Expected: refused *Say what changed in this revision.*
4. Summary of Change `Sampling plan changed to AQL 1.0`; Save; sign as author; as QC Inspector sign as reviewer; as QA Manager set Effective Date = today + 5 and **Approve**. Expected: REV-01 Status *Approved*, blue banner *Approved; takes effect on <date>.*; REV-00 still Effective.
5. As **Sales**: **Documents › Document Revision** list filtered on the document. Expected: only REV-00 listed; REV-01 not reachable by URL.
6. As System Administrator run `bench --site <site> execute meddevice_suite.qms.document_control.make_due_revisions_effective` after the date (or on a test site set the clock as the suite does). Expected: REV-01 Effective; REV-00 *Superseded* with Superseded By = REV-01 and red banner *Superseded by … Do not use this revision.*; the document's Effective Revision = REV-01, Next Review moved.
Actual result: ____  Pass/Fail: ____  Cleanup: none (revisions are kept).

### DC-07 Approval refused when the file changed after signing (C6)
Preconditions: a test site where the server's private files folder can be reached; a revision Awaiting Approval.
1. Overwrite the revision's file on disk (`sites/<site>/private/files/<file>`) with another PDF.
2. As **QA Manager**: **Approve**. Expected: refused *The document file has changed since it was signed by its author; it must be authored again.*; Status still Awaiting Approval.
3. As QA: **Return for Changes** with a reason; the author re-attaches and signs; the reviewer signs; QA approves. Expected: approved with the new fingerprint.
Actual result: ____  Pass/Fail: ____

### DC-08 Controlled copy (C14)
1. As **Sales**: open the effective revision › Print (or `/printview?doctype=Document%20Revision&name=<revision>&format=Controlled%20Copy`). Expected: format **Controlled Copy** preselected; green *CONTROLLED COPY · Printed on …*; Next review filled; signatures table with Authored, Reviewed, Approved rows (name and account, date and time, act and reason); the SHA-256 fingerprint.
2. As **QA Manager**: print the superseded REV-00 (DC-06) or a Draft. Expected: red *UNCONTROLLED – NOT FOR USE · Status: Superseded · Superseded by …* (or *Status: Draft* with *Not yet signed by its author* / *Not yet signed.*); Next review empty.
Actual result: ____  Pass/Fail: ____  Evidence: both printouts.

### DC-09 Withdrawal (C11, C12)
Role: QA Manager. Preconditions: `WI-PRD-<nnn>` (DC-01 step 4) with a revision in progress (start one as QA); `SOP-QC-<nnn>` effective with nothing in progress.
1. On `WI-PRD-<nnn>`: look for **Actions › Withdraw**. Expected: not offered while a revision is in progress (the API refuses *Finish or return the revision in progress before withdrawing WI-PRD-<nnn>.*).
2. On `SOP-QC-<nnn>`: **Actions › Withdraw**; leave Reason empty. Expected: the dialog requires Reason (*Say why the document is withdrawn.* on the server).
3. Reason `Replaced by a new procedure`; password; **Sign**. Expected: Status *Obsolete*; red banner *Withdrawn: Replaced by a new procedure*; Withdrawn Because filled; Next Review empty; no buttons; the effective revision now *Obsolete* with banner *The document has been withdrawn. Do not use this revision.*
4. Change the Title; Save. Expected: refused *SOP-QC-<nnn> has been withdrawn and cannot be changed.* Menu **Delete**: refused *A controlled document is withdrawn, never deleted.*
5. As Production Supervisor: **New Revision** on the withdrawn document. Expected: not offered; the API refuses *SOP-QC-<nnn> has been withdrawn; it cannot be revised.*
6. As **Production Supervisor**: **Actions › Withdraw** on `WI-PRD-<nnn>`. Expected: not offered (QA only).
Actual result: ____  Pass/Fail: ____

### DC-10 Periodic review alert (C13)
Preconditions: an effective document; a test site where the Administrator may set Next Review directly (otherwise wait for the calendar).
1. Set Next Review = today + 10 (test site only, `frappe.db.set_value`, as the automated test does). Run `bench --site <site> execute meddevice_suite.qms.document_control.send_review_alerts`. Expected: the process owner and QA Manager each see one bell notification *SOP-QC-<nnn> is due for review on <date>*; Sales sees none; Review Alert Sent For = that date.
2. Run the job again. Expected: no second notification.
3. Set Next Review = today − 10 (overdue) and run again. Expected: one new notification (a new date is alerted afresh, overdue included).
4. Withdraw the document (or use a withdrawn one) and run again. Expected: no notification.
Actual result: ____  Pass/Fail: ____  Cleanup: restore Next Review.

### DC-11 Lists, filters and audit trail
1. **Documents › Controlled Document**: filter by Document Type, Department, Status (standard filters); list shows Title, Document Type, Status. **Documents › Document Revision**: filter by Controlled Document and Status; indicators coloured by status. Expected: filters work; no "Submitted" wording.
2. Open a revision › menu **Audit Trail**. Expected: events *Created*, the signed updates and *Submitted* with user and time; **Verify Integrity** reports *The audit trail and the signatures of … are intact.*
Actual result: ____  Pass/Fail: ____

### Regression
- Change control's revision link (`change-control.md` CC-06) and the controlled specification's change-request rule (module 6) still work: run the automated suites (section 8) or `cypress/integration/change_control.js` by hand.

## 5. End-to-end business workflow

Demo data: the pilot loader (`meddevice_suite.pilot.demo`) creates the demo users but no controlled documents; the walk-through registers its own.

1. **QA Manager** registers **SOP-QC-001** *Incoming inspection of silicone* (SOP, QC, owner Production Supervisor, review every 2 years). Data: Controlled Document Draft; Audit Event *Created*.
2. **Production Supervisor** starts **SOP-QC-001-REV-00**, attaches the private PDF, names the QC Inspector as reviewer, signs **Sign as Author**. Data: fingerprint stored; Electronic Signature *Authored*; file protected.
3. **QC Inspector** returns it once (*Step 4 misses the sampling plan*), the author corrects and signs again, the reviewer signs **Sign as Reviewer**. Data: signatures *Rejected*, *Authored*, *Reviewed*.
4. **QA Manager** approves with effective date today → REV-00 Effective, document Effective, Next Review in two years. Data: signature *Approved*; revision submitted; Audit Events.
5. Everyone reads the effective revision in **Documents** and prints the **Controlled Copy** at the point of use.
6. Later, a **Change Request** (module 36) approved for SOP-QC-001 lets the owner start **REV-01** with a summary of change; approved with a future effective date it waits as *Approved* and takes effect on the day through the daily job, superseding REV-00.
7. Thirty days before the review date, the **Document Review Due** alert reaches the owner and QA; a new revision or a confirmation follows. When the procedure is replaced, QA **withdraws** SOP-QC-001 with a signed reason; it and its revisions stay readable as *Obsolete*.

Cross-module data: Controlled Document, Document Revision, File (private, evidence), Electronic Signature, Audit Event, Notification Log, Change Request (link on revisions after the first).

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocTypes | `Controlled Document` (not submittable, named `TYPE-DEPT-.###` by script), `Document Revision` (submittable, named `<document>-REV-<nn>` by script, default print format *Controlled Copy*, title field Title); module QMS (`modules.txt`) |
| Server module | `meddevice_suite/qms/document_control.py`: `author`, `review`, `return_for_changes`, `approve`, `withdraw`, `make_effective`, `make_due_revisions_effective`, `send_review_alerts`, `revision_query_conditions`, `revision_has_permission`, `file_fingerprint`, `lock_document`; constants `IN_PROGRESS`, `REVIEW_WARNING_DAYS = 30` |
| Controllers | `controlled_document.py` (`TYPE_CODES`, `LIFECYCLE_FIELDS`, review period ≥ 1, never deleted, `onload` → `revision_in_progress`); `document_revision.py` (`AUTHOR_FIELDS`, `SET_BY_DOCUMENT_CONTROL`, `before_insert` rules incl. `require_change_for_revision`, `validate`, `before_submit`/`before_update_after_submit` guard, never deleted or cancelled, `next_revision_number`) |
| Signed acts | `compliance/signing.py` `SIGNING_ACTIONS`: ("Document Revision", "Author"/"Review"/"Return"/"Approve"), ("Controlled Document", "Withdraw"); dialog `public/js/esign.js` |
| API routes | `POST /api/method/meddevice_suite.compliance.signing.sign_action` (`doctype`, `name`, `action`, `password`, `reason`); `meddevice_suite.compliance.signing.signing_actions`; standard `/api/resource/Controlled Document` and `/api/resource/Document Revision` under the DocType permissions (submit refused); lookup `meddevice_suite.qms.change_control.approved_changes_for` for the Change Request field |
| Hooks (`hooks.py`) | `permission_query_conditions["Document Revision"]`, `has_permission["Document Revision"]`, `has_permission["File"]` + `File.before_validate` (evidence files), `jinja.methods` `signatures_for_print`, `scheduler_events.daily`: `make_due_revisions_effective`, `send_review_alerts`; `onload` of every record → `signing.onload` (signatures), `audit_timeline.onload` |
| Client scripts | `qms/doctype/controlled_document/controlled_document.js` (banner, Effective Revision / Revision in Progress / New Revision / Withdraw), `document_revision.js` (act buttons, banners, Change Request lookup and required toggle), `document_revision_list.js` (status indicators) |
| Print | `qms/print_format/controlled_copy/controlled_copy.json` (standard Jinja; `signatures_for_print(doc)`) |
| Alert | Notification `Document Review Due` (`qms/notification/document_review_due/`), registered in `compliance/alerts.py` `ALERTS`; recipients: `process_owner` field + role Quality Manager; locked against editing on a site |
| Desk | `workspace_sidebar/documents.json` (Documents: Controlled Document, Document Revision, Change Request); `setup/desk.py` `DESK_AREAS["Documents"] = ("Desk User",)` |
| Permissions (DocType JSON) | Controlled Document: Quality Manager create/read/write/export/print/report; System Manager read/print/report; Desk User read/print. Document Revision: Quality Manager create/read/write/export/print/report; System Manager read/print/report; Desk User create/read/write/print. No submit/cancel/amend/delete/share/import for anyone |
| Compliance registries | `compliance/esign.py` `SYSTEM_MAINTAINED` (Document Revision: status, docstatus, superseded_by; Controlled Document: status, current_revision, effective_since, next_review_date); `compliance/evidence_files.py` `EVIDENCE` (Document Revision `file` once `file_sha256` set); `compliance/retention.py` "Quality system records" 10 years; audit trail by module (QMS belongs to this app) |
| Data flow | Form button → `esign.sign` dialog → `sign_action` → `document_control.<act>` (checks, `esign.sign` chained signature, `save` with `flags.document_control`) → DB; approve → `doc.submit()` → `make_effective` (row lock, supersede, document fields); daily jobs read Approved revisions / Effective documents and act one by one in savepoints |

## 7. Defects, edge cases and risks

**Confirmed and fixed during the review (commit 15970df7, each with a test):** QA could press Frappe's Submit or set an approved revision's status through the API; the fingerprinted file could be deleted or made public; a reason containing `<` or `>` was altered after fingerprinting (now refused: *Write the reason without the characters < and >.*); a wrong signing password signed the user out; a disabled account could sign; a signature bound fields the system changes afterwards; values sent for signature-only fields on creation were kept; a revision could take effect unapproved or for a withdrawn document; only the latest effective revision was superseded; review period 0; a missing file crashed instead of being reported; the approver could be the reviewer. Later: the controlled-copy test needed built assets in CI (36bfeb08); the browser test switched users with the previous form open (d65c28ba).

**Open / potential (not verified by a test; listed for the record):**
- Changing **Review Every (Years)** on an effective document does not move **Next Review**; it applies when the next revision takes effect. Client to confirm or a follow-up change.
- A reviewer can be any user, a disabled or administrative account included; such a reviewer cannot sign (refused by the signing service). Recovery: QA returns the revision and the author names another reviewer.
- QA may change Effective Date while Awaiting Approval without a signature (section 3, C8 interpretation); the approval signature binds the final value.
- A future effective date depends on the daily scheduler; with the scheduler off the revision stays *Approved* and takes effect on the next run (catch-up by design). The same holds for the review alert.
- The fingerprint is verified at approval; a file altered on disk afterwards by someone with server access is not re-hashed later (the audit trail and evidence-file rules cover Frappe, not the file system).
- Email delivery of the review alert depends on the mail set-up (module 51); desk bell only until then.
- System Manager reads every revision (module 47 matrix); the requirement text says "outside QA".

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (`feature/6-controlled-specifications`, local test site, macOS); CI = GitHub Actions on a fresh site:

| Check | Command / run | Result |
| --- | --- | --- |
| Module tests | `bench --site <test-site> run-tests --module meddevice_suite.qms.test_document_control` (classes `TestRegisterAndRevise`, `TestReviewAndApproval`, `TestSignedContentIsLocked`, `TestTakingEffect`, `TestPointOfUse`, `TestWithdrawal`, `TestSeparationOfSigners`, `TestNoWayAroundTheSignedActs`, `TestTakingEffectIsGuarded`, `TestOutsidersCannotReachDrafts`, `TestControlledCopy`, `TestPeriodicReviewAlerts`; 46 `test_` methods) | included in the full suite below |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed (2026-10-10) |
| Browser suite, retries off | `bench --site <test-site> run-ui-tests meddevice_suite --headless --browser electron -- --config retries=0` (`cypress/integration/document_control.js`: 7 tests, titles in section 3) | 59 of 59 passed (2026-10-10) |
| Semgrep (Frappe rules) | after pre-commit formatting | 0 findings |
| Lint/format, traceability | `pre-commit run --all-files`; `python -m meddevice_suite.qa.traceability --check` | pass |
| CI (fresh site) | GitHub Actions on the current branch | in progress when this document was written |
| Manual browser tests DC-01…DC-11 | — | **not executed** (no manual tester session); the automated browser journey covers DC-02 step 1 (New Revision offered only to the owner), DC-03 step 3, DC-04 steps 1 and 5, DC-05 steps 1–2, DC-06 step 5 (outsider), DC-08 step 1 and DC-09 steps 3 and 5 |

Untested: DC-06 step 6 and DC-10 on a real nightly scheduler (the jobs are called directly in the automated tests); DC-07 on a real site (the automated test overwrites the file in place); email delivery; the client's real procedures (the pilot has none loaded).

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Documents numbered TYPE-DEPT-###, registered by QA with a process owner; revisions 00, 01 …, one in progress | Verified (automated + browser) |
| Author, reviewer and QA approver are three people; each act signed; return with a reason; summary of change after the first revision | Verified (automated + browser) |
| File fingerprinted at authoring, private, protected; approval refused when changed; no unsigned submit or change, not even skipping permissions | Verified (automated) |
| Effective on its date, never before approval; previous revision superseded; daily job with per-revision isolation | Verified (automated, job called directly) |
| Outside QA only effective revisions and one's own drafts are visible, on list, form, lookup and API | Verified (automated + browser) |
| Nothing deleted or cancelled; withdrawal signed with a reason, only with nothing in progress | Verified (automated + browser) |
| Review alert once per review date to owner and QA, overdue and catch-up included | Verified (automated, job called directly) |
| Controlled copy only while effective; uncontrolled otherwise; default printout with signatures | Verified (automated + browser) |
| Form banners and act buttons per person; no Submit button | Verified for the browser journey (automated); full manual DC-01…DC-11 **not tested** |
| Scheduler-driven effect and alert on a live site; email delivery | **Not tested** |
| CI green on a fresh site for the current commit | **Pending** |
| Client decisions: review period change after effect, external document revision identifier | **Open** |
