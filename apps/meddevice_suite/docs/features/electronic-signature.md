# 21 CFR Part 11 electronic signature (module 49) — feature document and browser test guide

Requirement: **URS-CMP-001, URS-CMP-002, URS-CMP-003, URS-CMP-004, URS-CMP-005, URS-CMP-006, URS-CMP-030** (`meddevice_suite/qa/requirements.toml`). Also touched: URS-CMP-010 (the record's **Verify Integrity** button also verifies its signatures), URS-CMP-035 (clean sign-in page, module 47; `public/js/sign_out.js`).
Code: `meddevice_suite/compliance/esign.py` (the signing service), `compliance/signing.py` (the register of signed acts and the desk entry point), `compliance/fingerprint.py` (shared with module 50), `compliance/doctype/electronic_signature/`, `public/js/esign.js`, hooks in `meddevice_suite/hooks.py`; the acts themselves live in the modules that sign (`qms/document_control.py`, `qms/change_control.py`, `lot_control/release.py`, `master_data/supplier_approval.py`).
State on 2026-10-10: built (first commit `904153ee` 2026-10-05, desk dialog `a1c8e5cf` 2026-10-07, review gaps closed in `15970df7` and `b6e8ecc0`), server and browser suites green on the current branch; GitHub CI for the branch in progress (section 8).
Changed 2026-10-12 (foundation review, branch `fix/immutable-records`): a signature is inserted only by signing and only under the signer's own account; no permission row may open the Electronic Signature to writing; a user who signed is never deleted; Controlled Document **Withdraw** is signed *Withdrawn* and Customer **Require Licence** *Rejected*; the declared meaning is enforced from the dialog to the record; the chain is verified by its links, not by time. Tests: `compliance/test_immutable_records.py` — written, **not yet run** (section 8).

## 1. Feature overview

**Purpose.** Every regulated decision in the system (approve a document, release a lot, decide a supplier, approve a change) is signed by one identified person who re-enters their own password at the moment of signing. The signature records who, when, what act and what it means, is bound to the exact content signed and to the previous signature on the record, and can never be changed or deleted. Anyone who may open the record sees its signatures; a tampered record or signature is detected by verification.

**Why it matters.** 21 CFR 11.50 (signature manifestation), 11.70 (signature/record linking), 11.100 (one person per signature), 11.200 (password re-entry) and 11.300(d) (lockout on failed attempts) are the conditions under which an electronic signature is accepted as a handwritten one. The client's DFD marks the signed points as "E-sign" on D1 and "QA Review & Approval (E-Signature)" on D2. Without a compliant signature service every signed act in modules 4, 16, 17, 30 and 36 would be a finding.

**Who uses it.**

| Job profile (pilot user) | Role(s) | What they do here |
| --- | --- | --- |
| QA Manager (`qa.manager@demo.example.com`) | Quality Manager, Item Manager | Signs approvals, releases, supplier decisions; reviews the Electronic Signature list; runs **Verify Integrity** |
| Production Supervisor, QC Inspector, Sales, Purchase, Stores … | their own role | Sign the acts their job owns (author/review a document, assess a change) from the record's form |
| System Administrator (`system.administrator@demo.example.com`) | System Manager | Reads the Electronic Signature list; cannot sign business acts (holds no business role) |
| Administrator, Guest | — | Refused: shared accounts cannot sign |

Only the signing service writes an Electronic Signature; no role has create, write, delete, submit or amend on it. Quality Manager and System Manager have read, report, export and print; nobody else can open the list.

**Prerequisites.** The signer has their own account with a password (demo users are created by `pilot/demo.py` with the site-config password); the record type declares the act in `SIGNING_ACTIONS`; the signer holds the permission the act requires on the record. Nothing is configured on the site: the DocType and its permissions come with the app and `bench migrate`.

## 2. Detailed functional explanation

### 2.1 The signing dialog (URS-CMP-030, URS-CMP-001)

A signed act is offered as a button on the record's form by the module that owns it (verified labels: Supplier Approval **Approve** / **Approve Conditionally** / **Not Approve**; Lot Release **Release** / **Reject** / **Hold** / **Return to Stock** / **Reject Return**; Document Revision **Sign as Author** / **Sign as Reviewer** / **Approve**; Controlled Document **Withdraw**; Change Request acts per `qms/doctype/change_request/change_request.js`). Each button calls `meddevice_suite.esign.sign(frm, action, meaning, {ask_reason})` in `public/js/esign.js`, which opens the dialog:

| Element | Label / text on screen |
| --- | --- |
| Title | **Sign: <act>** (e.g. *Sign: Approve*) |
| Statement | **<Meaning>**: <meaning text>, then *<Record type> <name>, signed by <your full name>. Your signature is recorded with the date and time and is legally binding.* |
| Meaning texts | Authored *I wrote this and it is complete and correct.*; Reviewed *I have reviewed this and found it fit for approval.*; Approved *I approve this.*; Rejected *I reject this.*; Released *I release this for use.*; Verified *I have verified this.*; Trained *I was trained in this, and I understand it.*; Competent *I have assessed this person and found them competent.*; Withdrawn *I withdraw this from use.* (2026-10-12) |
| **Reason** (Small Text, required) | only when the act asks for one (`ask_reason: true`: Lot Release, Supplier Approval, Withdraw; Document Revision and Change Request per their own options) |
| **Your password** (Password, required) | the signer's own account password |
| Primary button | **Sign** |

The dialog posts to `meddevice_suite.compliance.signing.sign_action` (POST only), with the meaning it showed; the server refuses a meaning other than the declared one (*<act> is now signed as <declared>, not as <shown>. Reload the page and sign again.*), so nobody signs under a meaning they were not shown (11.50(a)(3)). On success the dialog closes and the form reloads; on refusal the password box is cleared and the dialog stays open with the server's message. Closing the dialog signs nothing.

Server side (`signing.sign_action`): the (record type, act) pair must be in `SIGNING_ACTIONS` or it is refused with *<act> cannot be signed on <record type>.* (permission error); the signer must be able to read the record; then the act's `perform` function runs its own checks, calls `esign.sign(...)` and performs the act in the same transaction. `signing.signing_actions(doctype)` tells the desk which acts exist for a record type.

### 2.2 What `esign.sign` checks and records (URS-CMP-001, 002, 006)

In order:

1. Session user is not **Administrator** or **Guest** → else *Sign in with your own account to sign; shared accounts cannot sign.*
2. The account is enabled → else *Your account is disabled; it cannot sign.*
3. Meaning is one of Authored, Reviewed, Approved, Rejected, Released, Verified, Trained, Competent, Withdrawn → else *Unknown signature meaning: <x>*; an empty act → *A signature needs the action being signed.*; an act declared in `SIGNING_ACTIONS` is signed only with its declared meaning → else *<act> on <record type> is signed as <declared>, not as <given>.* (2026-10-12).
4. The reason contains no `<` or `>` → else *Write the reason without the characters < and >.* (Frappe would strip them after fingerprinting).
5. The signer holds the act's permission on the record (`doc.check_permission(permission)`, default `write`; the calling act may pass another).
6. Password re-entry through Frappe's own `check_password`, with the login-attempt tracker: a locked account is refused before the password is even looked at (`SecurityException`); a wrong password adds one failed attempt to the same counter as a failed login (System Settings *Allow Consecutive Login Attempts* / *Allow Login After Fail*), writes an **Activity Log** entry (operation Login, status Failed, subject *Electronic signature refused, wrong password: <act> on <record type> <name>*) through a background job so it survives the rolled-back request, and raises *Incorrect password. The signature was not recorded.* This is a `SigningRefused` validation error, not an authentication error, so the person is **not** signed out. A correct password resets the counter.
7. The record row is locked (`FOR UPDATE`), the previous signature on the record is found, and the **Electronic Signature** is inserted (the only code path that may; `insert(ignore_permissions=True)` with the reason in a comment). The service marks the new record in its flags (`recorded_by_signing`), which a request cannot set (Frappe drops `flags` sent with a record); the record refuses an insert without the mark (section 2.5).

Recorded fields (labels on screen): **Document Type**, **Document**, **Document Name at Signing**, **Action**, **Meaning**, **Signed By** (user ID), **Signer Name** (full name at signing), **Signed At** (server time, site zone), **Reason**, and under *Integrity*: **Record Fingerprint**, **Previous Signature**, **Signature Fingerprint**. Record name `ESIG-YYYY-NNNNN`.

### 2.3 Binding to the record and to the previous signature (URS-CMP-004)

- **Record Fingerprint** = SHA-256 of the content the signature binds, as a canonical JSON (sorted keys). Since the independent review of 2026-10-15 every record type with a signed act in `SIGNING_ACTIONS` declares that content in `esign.SIGNED_CONTENT`: either the module's own `signed_content(doc)` (Item, BOM, Asset, Customer, Operation/Workstation/Routing/Warehouse, Quality Inspection Template, Batch, Regulatory Licence, Device Master Record) or an `esign.SignedFields` list of the fields the signers approve and the columns of each child table (`compliance/signed_records.py`: Document Revision, Controlled Document, Change Request, Lot Release, Supplier Approval, Customer Licence, Work Task, Calibration, Maintenance, Equipment Qualification, Training Matrix and Record, the four risk-management records). The declarations list what version 1 bound for each type, less the workflow `status` (the signature's act and meaning record the decision); never the record's name, who made it and when, `docstatus`, or what the system maintains after signing (e.g. Lot Release `stock_entry`, `supplier_return`; Supplier Approval `certificate_alerted`; Document Revision `superseded_by`). A field added to a record type is therefore bound by no signature until a declaration names it, and a field added to a declaration is bound only once it holds a value, so a signature that verified keeps verifying. `test_signature_integrity.TestSignedContentIsDeclared` fails when a signed record type has no declaration, or when adding a field to any of them changes a fingerprint. A record type with no signed act (only code and tests sign one) binds its whole record.
- **Signature Fingerprint**, version 2 (every signature recorded since 2026-10-15) = HMAC-SHA256 of `[2, previous signature's fingerprint or "", Document Type, Document Name at Signing, Action, Meaning, Reason, Signed By, Signer Name, Signed At, Record Fingerprint]`, under a key derived from the site's `encryption_key` in `site_config.json` (`fingerprint.trail_key`). The key is outside the database, so someone who can only write the database cannot rewrite a signature and compute the chain again. **Fingerprint Version** (2) is recorded on the signature by the service and is as unchangeable as the rest.
- **Version 1** (signatures recorded before 2026-10-15; Fingerprint Version empty): unkeyed SHA-256 of the same list without the version, and a Record Fingerprint of the whole record less `esign.SYSTEM_MAINTAINED`, `ACT_MAINTAINED` and the unset `BOUND_WHEN_SET` fields, except the record types that already declared their content (`esign.VERSION_1_DECLARED`). Nothing recorded is rewritten: version-1 signatures are verified exactly as they were made. A version-1 signature that follows a version-2 one in the chain, or that is dated after the moment this site began version 2 (`meddevice_suite_keyed_fingerprints_since` in `site_config.json`, written once by the migrate that brings version 2), is reported as rewritten.
- **Previous Signature** links the newest signature of the record at signing time, found by the links (the signature no other signature names), not by time, under a row lock, so two signings of one record serialise.
- `signature_problems(doctype, name)` checks the chain by the rule the audit trail uses too (`fingerprint.chain_problems`), following the links, so a server clock set back between two signings is not tampering. It reports a signature whose Previous Signature is missing, two signatures following the same one, a chain that starts more than once, a fingerprint that does not recompute, a version-1 signature where only version 2 can be, and a signature recorded for a name the record has never had (its Document Name at Signing is neither the record's name nor a name its *Renamed* audit events record): a signature moved to another record. `verify_signatures` is True when there is none.
- `signed_content_report(doctype, name)` says, for each signature in the order signed, whether the record still holds what it binds: **Unchanged**; **Changed and signed again** (a later signature binds the record as it now is: a revision reviewed after its author signed, a master approved again; not a finding); **Changed since signing** (no signature covers the record as it now is, and undoing the audit events recorded since brings back what was signed, so the change went through the system; the message lists those events with who, when and the reason); **Changed outside the audit trail** (undoing the audit trail does not bring it back: a data-integrity incident). Only what the signature binds counts, so a status move, a later stock entry or an alert's bookkeeping is never a change. `is_unchanged_since(signature)` answers the same question for one signature.

On screen, verification is the record's **View › Verify Integrity** button (module 50, `public/js/audit_trail.js`): green *Integrity verified — The audit trail and the signatures of <name> are intact.*, followed by *Signatures:* and one line per signature (*Approve (Approved), signed by … on …: the record still holds what was signed.*); or red *Integrity problem found* listing the audit-chain and signature-chain problems and each signature whose record changed since signing, worded as such.

### 2.4 Signatures shown with the record and on printouts (URS-CMP-003)

On every regulated record's form (`signing.onload`, hooked on `*` onload), anyone who may open the record sees the dashboard section **Electronic signatures** with columns **Meaning**, **Signed by**, **Date and time**, **Act** (act, then ` · reason` when given), oldest first. Non-regulated record types and records without signatures show nothing.

For printouts the Jinja method `signatures_for_print(doc)` is registered in `hooks.py`; the print formats **Controlled Copy** (`qms/print_format/controlled_copy/`) and, since 2026-10-10, **Lot Release (Signed)**, **Supplier Approval (Signed)** and **Change Request (Signed)** (each the default print format of its record type) use it. Other print formats show signatures only if they call it.

### 2.5 Permanence (URS-CMP-005)

`ElectronicSignature.validate` refuses any save after the first (*An electronic signature cannot be changed.*) and `on_trash` refuses deletion (*An electronic signature cannot be deleted.*), both as permission errors, for every user including Administrator and `ignore_permissions=True`. The DocType is `in_create` (no New button); no role has create/write/delete.

Since 2026-10-12 (foundation review) a signature cannot be inserted past the signing service either: `before_insert` refuses one without the service's mark (*An electronic signature is recorded only by signing, with the signer's password.*), even with permissions and validation skipped, and `validate` refuses one whose owner is not its signer (*A signature is recorded only under the signer's own account.*). Before, a system manager could grant create on the Electronic Signature in Role Permissions Manager and post a signature in another person's name with a fingerprint that verified. That grant is itself refused now (`setup/roles.refuse_hand_added_permission`, module 47: no permission row on the evidence records may grant more than select, read, report, export or print, even on migrate).

A user ID is permanent: renaming a User is refused (`audit.keep_user_id`: *User IDs are permanent in the audit trail. Create a new account instead of renaming <user>.*), and since 2026-10-12 so is deleting one who has signed, appears in the audit trail or owns a regulated record (`audit.keep_user_with_history`, 21 CFR 11.300(b): *<user> has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.*); a disabled account cannot sign (section 2.2). The Electronic Signature is in the 10-year "Compliance evidence" retention class (module 54) and is not a log Frappe clears.

### 2.6 The register of signed acts

`signing.SIGNING_ACTIONS` is the system's complete list of signable acts on 2026-10-10:

| Record type | Act | Meaning |
| --- | --- | --- |
| Document Revision | Author / Review / Return / Approve | Authored / Reviewed / Rejected / Approved |
| Controlled Document | Withdraw | Withdrawn (was Approved until 2026-10-12) |
| Change Request | Assess / Approve / Return / Reject / Verify Effective / Verify Not Effective | Reviewed / Approved / Rejected / Rejected / Verified / Verified |
| Lot Release | Release / Reject / Hold / Return to Stock / Reject Return | Released / Rejected / Held / Released / Rejected |
| Supplier Approval | Approve / Approve Conditionally / Not Approve | Approved / Approved / Rejected |
| Customer | Exempt From Licence / Require Licence | Approved / Rejected (was Approved until 2026-10-12: ending an exemption revokes QA's approval of it, signed as a revoked competence is) |

The table lists the acts of 2026-10-10 and the two meanings changed on 2026-10-12; the code is the full register. The meaning is written in the register, in the act's `esign.sign` call and in the form's script: `esign.sign` refuses a declared act signed with another meaning, `sign_action` refuses a meaning the dialog showed that is not the declared one, and `test_immutable_records.TestDeclaredMeaningIsTheSignedMeaning` checks every act's literal `sign(...)` call and meaning tables against the register. The Lot Release **Hold** meaning is under review on another branch.

### 2.7 What is intended but not implemented

- **Printouts (URS-CMP-003):** fixed 2026-10-10 — Lot Release, Supplier Approval and Change Request now print by default with their signatures (generic signed print format: fields, child tables, the **Electronic signatures** table). Only print formats that call `signatures_for_print` show signatures; Frappe's *Standard* format stays selectable and shows none.
- **Guest** is listed as a shared account and refused by the same check as Administrator, but no test signs as Guest (Guest cannot open regulated records anyway).
- A lockout started by signing failures also blocks login, and vice versa (one counter): by design, not configurable.
- Signatures are shown per record; there is no signer-centred report beyond the Electronic Signature list filtered by **Signed By**.
- Email/notification of a signature event: none (not required).

## 3. Requirements and acceptance criteria

| # | Requirement / criterion | Implementation | Automated verification |
| --- | --- | --- | --- |
| C1 | URS-CMP-001: password re-entered at signing; wrong password records nothing and counts to the login lockout; locked account cannot sign | `esign.authenticate`, `SigningRefused`, `record_failed_signing` | `test_esign.TestReauthentication`: `test_correct_password_records_a_signature`, `test_wrong_password_records_nothing_and_counts_as_a_failed_login`, `test_a_wrong_password_is_kept_in_the_login_history`, `test_the_lockout_counts_signing_failures_up_to_its_limit`, `test_the_lock_lifts_after_the_lockout_period`, `test_locked_account_cannot_sign_even_with_the_correct_password`, `test_a_disabled_account_cannot_sign`; `test_signing.TestSigningDialog.test_a_wrong_password_records_nothing` |
| C2 | URS-CMP-002: user ID, full name, date/time, act, meaning (six values), optional reason | `esign.sign`, `MEANINGS`, DocType fields | `TestSignatureContent`: `test_records_who_when_what_and_meaning`, `test_unknown_meaning_is_refused`, `test_action_is_required`, `test_the_reason_is_stored_exactly_as_signed`, `test_a_reason_frappe_would_rewrite_is_refused_rather_than_altered` |
| C3 | URS-CMP-003: shown with the record on screen and on printouts, with name, date/time, meaning | `signing.onload`, `esign.js` form-refresh, `signatures_for_print`, Controlled Copy | `test_signing.TestSignaturesShownWithTheRecord` (4 tests); `qms/test_document_control.TestControlledCopy.test_an_effective_revision_prints_as_a_controlled_copy_with_its_signatures`; Cypress `document_control.js` "[URS-QMS-008] [URS-CMP-003] the effective revision prints as a controlled copy with its signatures" |
| C4 | URS-CMP-004: bound to the declared signed content, to the record's type and name and to the previous signature, keyed outside the database; alteration, a signature moved to another record, and the record changed since signing detectable on Verify Integrity; a field added to a record type changes no signature | `SIGNED_CONTENT`, `SignedFields`, `signed_records.py`, `record_fingerprint`, `signature_fingerprint`, `signature_problems`, `signed_content_report`, `fingerprint.chain_problems`, `verify_signatures`, `is_unchanged_since`, `chain_head` | `compliance/test_signature_integrity.py`: `TestEachSignatureIsCheckedAgainstTheRecord` (6), `TestSignedContentIsDeclared` (2), `TestChainsAreKeyed` (6) — **not yet run**; `TestBinding` (4 tests), `TestFingerprint` (4 unit tests), `TestSignatureLookupsStayFastAtScale`, `test_audit.TestSignaturesSurviveRename`, `test_signing…test_a_renamed_record_keeps_verifiable_signatures`; Cypress `audit_trail.js` "[URS-CMP-010] verifies the record's audit trail and signatures on request" |
| C5 | URS-CMP-005: never edited or deleted, not even by system managers | controller `ElectronicSignature`, `in_create`, permissions | `TestSignaturesAreImmutable` (4 tests); `test_audit_review.TestQAReviewsTheEvidence.test_qa_managers_review_audit_events_and_signatures_but_never_change_them` |
| C6 | URS-CMP-006: shared accounts refused; signer needs the act's permission on the record | `SHARED_ACCOUNTS`, `doc.check_permission(permission)` | `TestSignerIdentity`: `test_administrator_cannot_sign`, `test_signer_needs_permission_on_the_record`; `test_signing…test_someone_who_cannot_open_the_record_cannot_sign_it` |
| C7 | URS-CMP-030: sign from the record in a dialog naming act and meaning, asking the password; only declared acts; only by someone who may open the record | `signing.sign_action` (POST), `signing_actions`, `esign.js` dialog | `TestSigningDialog` (7 tests incl. `test_signing_is_accepted_only_as_a_post_request`, `test_the_dialog_is_offered_only_the_declared_acts`); `qms/test_document_control.TestReviewAndApproval`; Cypress `document_control.js` "[URS-CMP-030] [URS-QMS-002] the author signs from the form; only the author is offered it" and "[URS-CMP-030] [URS-QMS-002] the reviewer signs, then QA approves; nobody can just submit" |

| C8 | Foundation review 2026-10-12: no signature past signing or under another account (URS-CMP-005); no hand-made permission on it (URS-CMP-015); a user who signed is disabled, never deleted (URS-CMP-006); each act's meaning is its own and is the one shown (URS-CMP-002, URS-CMP-030); verification by links (URS-CMP-004) | `ElectronicSignature.before_insert`/`validate`, `roles.refuse_hand_added_permission`, `audit.keep_user_with_history`, `esign.sign` + `signing.declared_meaning`, `verify_signatures` | `compliance/test_immutable_records.py`: `TestSignaturesComeOnlyFromSigning`, `TestPermissionsOnTheAppsRecordTypes`, `TestUserIdsInTheTrailAreNeverDeleted`, `TestMeanings`, `TestDeclaredMeaningIsTheSignedMeaning`, `TestDeclaredMeaningIsEnforced`, `TestChainFollowedByLinks`; meaning asserts in `qms/test_document_control.TestWithdrawal` and `master_data/test_customer_licence.TestBuyingWithoutALicence` — **not yet run** |

**Resolved 2026-10-10:** C3 on printouts (signed print formats for Lot Release, Supplier Approval and Change Request; tests `test_the_printout_shows_*`).
**Ambiguous:** URS-CMP-002 says "optional reason" while several acts make it required in the dialog — the service accepts both, the act decides; not a conflict but worth confirming with the client. URS-CMP-001 "locked account cannot sign": the lockout is Frappe's login lockout (System Settings), so a site with *Allow Consecutive Login Attempts* = 0 (unlimited) never locks; the pilot's setting was not checked (section 7).
**Not covered by this module:** which acts need a signature (each module's requirement); who may take each act (module 47 roles).

## 4. Step-by-step browser testing guide

Site: the pilot site (`pilot.localhost`, `scripts/create_pilot_site.sh`) or a test site after `prepare_test_site`. Use the demo supplier *Silicone Compounds (Demo)* and a throw-away Supplier Approval as the signed record (QA Manager signs it); any other declared act (Lot Release, Document Revision) works the same way. Record actual results and screenshots (dialog, Electronic signatures table, Verify Integrity message).

### ES-01 Sign with the correct password from the record (C2, C3, C7)
Role: QA Manager. Preconditions: a saved draft **Supplier Approval** with one item and a certificate (see `approved-suppliers.md` SA-01 step 3).
1. Open the draft (`/desk/supplier-approval/<name>`). Expected: buttons **Approve**, **Approve Conditionally**, **Not Approve**.
2. Click **Approve**. Expected: dialog **Sign: Approve**, statement **Approved**: *I approve this.*, line *Supplier Approval <name>, signed by <your name>. Your signature is recorded with the date and time and is legally binding.*, fields **Reason** and **Your password**, button **Sign**.
3. Reason `Audited on site`, correct password, **Sign**. Expected: dialog closes; the form reloads submitted; section **Electronic signatures** shows one row: Meaning *Approved*, Signed by <your full name>, Date and time = now, Act *Approve · Audited on site*.
4. Open **Compliance › Electronic Signature** (`/desk/electronic-signature`), open the newest `ESIG-…`. Expected: Document Type *Supplier Approval*, Document = the record, Document Name at Signing = same, Action *Approve*, Meaning *Approved*, Signed By = your user ID, Signer Name, Signed At, Reason; Integrity: Record Fingerprint and Signature Fingerprint (64 hex chars), Previous Signature empty (first signature).
Actual result: ____  Pass/Fail: ____  Evidence: screenshots of steps 2, 3, 4.

### ES-02 Wrong password: nothing recorded, no sign-out, counted as a failed login (C1)
Role: QA Manager. Preconditions: another draft Supplier Approval; for step 5, *Wrong Passwords Before Lockout* = 3 in Quality Settings (`quality-settings.md`, QS-02); System Settings › *Allow Consecutive Login Attempts* then shows 2 and cannot be edited by hand.
1. Click **Approve**, reason `x`, a wrong password, **Sign**. Expected: red message *Incorrect password. The signature was not recorded.*; dialog stays open with the password cleared; you are still signed in; record still a draft.
2. Close the dialog. Expected: no Electronic Signature for the record (check the list filtered by Document).
3. As System Administrator open **Users › Activity Log** (`/desk/activity-log`). Expected: an entry for the QA user, status *Failed*, subject *Electronic signature refused, wrong password: Approve on Supplier Approval <name>*.
4. As QA Manager sign again with the correct password. Expected: signed (the counter resets).
5. On a new draft, enter a wrong password 3 times. Expected: each attempt is refused with *Incorrect password. The signature was not recorded.*; after the third the account is locked, so the **correct** password is now refused with Frappe's lockout message (a security error, wording from Frappe) until *Allow Login After Fail* seconds pass; logging in from another browser fails likewise; no signature exists for the record.
Actual result: ____  Pass/Fail: ____  Cleanup: restore *Wrong Passwords Before Lockout* under a change request; wait out or reset the lockout.

### ES-03 Required fields and refused reasons (C2)
Role: QA Manager.
1. **Approve** › leave **Reason** empty, enter the password, **Sign**. Expected: Frappe's *Missing Values Required* on Reason; nothing sent.
2. Reason `from <L-001> to <L-002>`, password, **Sign**. Expected: *Write the reason without the characters < and >.*; nothing recorded.
3. Reason `Lot 7 & 8 checked; seal "OK" ✓`, **Sign**. Expected: signed; the reason appears exactly as typed in the Electronic signatures table and on the ESIG record.
Actual result: ____  Pass/Fail: ____

### ES-04 Shared and unentitled accounts cannot sign (C6, C7)
1. As **Administrator**: open a draft Supplier Approval, **Approve**, password, **Sign**. Expected: *Sign in with your own account to sign; shared accounts cannot sign.*; nothing recorded.
2. As **Purchase** (`purchase@demo.example.com`): open the same draft. Expected: no Approve buttons (read-only form).
3. As Purchase, from the browser console: `frappe.xcall("meddevice_suite.compliance.signing.sign_action", {doctype:"Supplier Approval", name:"<name>", action:"Approve", password:"<your password>", reason:"x"})`. Expected: permission error (Purchase may read but not take the act; the act's own check refuses); nothing recorded.
4. Same call with `action:"Release"`. Expected: *Release cannot be signed on Supplier Approval.*
5. As **Sales** (`sales@demo.example.com`, cannot open Supplier Approval) run the call of step 3. Expected: permission error on reading the record.
Actual result: ____  Pass/Fail: ____

### ES-05 Chain of two signatures and verification (C4)
Role: QA Manager. Preconditions: a Document Revision in progress (author and reviewer are other demo users; see `document_control.js` for the journey) or any record with two signed acts.
1. After the second signature, open **Compliance › Electronic Signature** filtered by Document. Expected: two rows; the second's **Previous Signature** = the first's name.
2. Open the record › **View › Verify Integrity**. Expected: green *Integrity verified*, *The audit trail and the signatures of <name> are intact.*
Actual result: ____  Pass/Fail: ____

### ES-06 Tampering is detected (C4, C5) — test site only, System Administrator
1. Try to change a signature from its form (it is read-only) or through the API: `frappe.client.set_value("Electronic Signature", "<ESIG>", "reason", "edited")`. Expected: *An electronic signature cannot be changed.*
2. Try **Delete** from the list. Expected: *An electronic signature cannot be deleted.* (also for Administrator).
3. Only on a disposable test site, with database access, change `meaning` of one row in `tabElectronic Signature` directly, then open the record › **Verify Integrity**. Expected: red *Integrity problem found* with *<ESIG>: content does not match its fingerprint.*
4. Same site: change a field the signature binds directly in the record's table (e.g. a Supplier Approval's `certificate_number`), then **Verify Integrity**. Expected: red, *<act> (<meaning>), signed by … on …: the record has changed since it was signed, and the audit trail does not account for the change: it was made outside the system.*
5. Same site: point a signature's `reference_name` at another record of the same type and verify that record. Expected: red, *<ESIG>: it was recorded for <first record>, a name this record has never had; it belongs to another record.*
Actual result: ____  Pass/Fail: ____  Cleanup: discard the test site or restore the row.

### ES-07 Signatures on the printout (C3)
Role: QA Manager. Preconditions: an effective Document Revision.
1. Open the revision › **Print**, format **Controlled Copy**. Expected: the printout carries *CONTROLLED COPY* and the signatures (meaning, name, date/time) of author, reviewer and approver.
2. Open a submitted **Supplier Approval** › **Print** (default format *Supplier Approval (Signed)*). Expected: the printout carries the **Electronic signatures** table (meaning, act, signer, date and time, reason); a draft prints *DRAFT – not signed*.
Actual result: ____  Pass/Fail: ____

### ES-08 Lists, filters and permissions of the Electronic Signature list
1. As QA Manager: **Compliance › Electronic Signature**; filter by Document Type, Signed By, Meaning; sort by Signed At; **Menu › Export**. Expected: filters and export work; no **New** button; opening a row shows every field read-only.
2. As Stores (`stores@demo.example.com`): visit `/desk/electronic-signature`. Expected: refused plainly (no Compliance area; no read permission).
Actual result: ____  Pass/Fail: ____

### ES-09 User ID permanence (C5)
Role: System Administrator. 1. Open a demo user › **Menu › Rename**, new ID. Expected: *User IDs are permanent in the audit trail. Create a new account instead of renaming <user>.*
Actual result: ____  Pass/Fail: ____

### ES-10 Cross-module consistency: signature, audit trail and system-maintained fields (C4)
Role: QA Manager. Preconditions: a lot in Quarantine with a verified certificate (module 16).
1. **Stock › Lot Release** for the lot › **Release**, reason `IQC passed`, password, **Sign**. Expected: submitted; **Electronic signatures** shows *Released*; the release's **Stock Entry** link is filled by the system after signing.
2. **View › Verify Integrity**. Expected: green, and under *Signatures:* *Release (Released), signed by …: the record still holds what was signed.* — the Stock Entry link is outside what the signature binds (`signed_records.LOT_RELEASE`), so the later system write does not read as a change.
3. **View › Audit Trail**. Expected: the record's events (*Created*, *Submitted* …) sit beside the signature; the submit event's time equals the signature's Signed At to the second.
4. **Compliance › Electronic Signature**, filter Document Type = *Lot Release*. Expected: the new row with Previous Signature empty (first signature on that record).
Actual result: ____  Pass/Fail: ____

### ES-11 Signatures come only from signing; meanings fit the act (C8) — test site, 2026-10-12
1. As System Administrator, **Role Permissions Manager** › Document Type *Electronic Signature* › **Add a New Rule** (any role) or tick **Create** on a row. Expected: *The permissions on Electronic Signature are part of the validated configuration and change only with a release of this app.* (or, for a grant that writes, *Electronic Signature records are written only by the system; no role may be given create on them.*); nothing changes.
2. From the browser console as System Administrator: `frappe.xcall("frappe.client.insert", {doc: {doctype: "Electronic Signature", reference_doctype: "Supplier Approval", reference_name: "<name>", action: "Approve", meaning: "Approved", signer: "qa.manager@demo.example.com"}})`. Expected: *An electronic signature is recorded only by signing, with the signer's password.*
3. As QA Manager withdraw a throw-away Controlled Document. Expected: dialog statement **Withdrawn**: *I withdraw this from use.*; after signing, the Electronic signatures table shows Meaning *Withdrawn*, Act *Withdraw*.
4. As QA Manager on a customer that buys without a licence, **Require Licence**. Expected: statement **Rejected**: *I reject this.*; recorded Meaning *Rejected*.
5. As System Administrator open **Users** › a demo user who has signed › **Delete**. Expected: *<user> has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.*; untick **Enabled** and save. Expected: saved; that user can no longer sign.
Actual result: ____  Pass/Fail: ____

### Refusal messages at a glance (for the tester)
| Situation | Exact message |
| --- | --- |
| Wrong password | *Incorrect password. The signature was not recorded.* |
| Administrator / Guest | *Sign in with your own account to sign; shared accounts cannot sign.* |
| Disabled account | *Your account is disabled; it cannot sign.* |
| Act not declared for the record type | *<act> cannot be signed on <record type>.* |
| Reason with `<` or `>` | *Write the reason without the characters < and >.* |
| Edit / delete a signature | *An electronic signature cannot be changed.* / *An electronic signature cannot be deleted.* |
| Rename a user | *User IDs are permanent in the audit trail. Create a new account instead of renaming <user>.* |
| Delete a user with history | *<user> has acted on regulated records, so the user ID stays in the audit trail and cannot be deleted. Disable the account instead.* |
| Insert a signature past signing | *An electronic signature is recorded only by signing, with the signer's password.* |
| Signature under another account | *A signature is recorded only under the signer's own account.* |
| Declared act with another meaning | *<act> on <record type> is signed as <declared>, not as <given>.* |
| Dialog loaded before a meaning changed | *<act> is now signed as <declared>, not as <shown>. Reload the page and sign again.* |

### Regression
- The signed journeys still work: `document_control.js` (author → reviewer → QA), `standard_flow.js` (QA approves the supplier, releases lots) — automated, section 8.

## 5. End-to-end business workflow

1. **QA** prepares a Supplier Approval (module 4) and clicks **Approve** → signing dialog → password → `sign_action` → `supplier_approval.approve` → `esign.sign` (meaning Approved) → record submitted in the same transaction. Data: Electronic Signature `ESIG-…` (record fingerprint of the decision), Audit Events *Created* and *Submitted* (module 50).
2. **Production Supervisor** authors a Document Revision (**Sign as Author**, Authored) → **QC Inspector** reviews (**Sign as Reviewer**, Reviewed) → **QA** approves (**Approve**, Approved); each signature's Previous Signature points to the one before; the revision becomes Effective; **Controlled Copy** prints the three signatures.
3. **QA** releases a received lot (**Lot Release › Release**, Released, reason required) → stock moves to the approved store (modules 16/17); the move's Stock Entry is outside the fingerprint (`SYSTEM_MAINTAINED`).
4. Any time later an auditor (QA) opens the record › **Verify Integrity**, or module 54's archive copies the signatures with the record.

Cross-module data: Electronic Signature (new), Activity Log (failed signings), the signed record's own fields set by the act, Audit Event per action.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| DocType | `Electronic Signature` (`ESIG-{YYYY}-{#####}`, `in_create`, sort `creation desc`); permissions: System Manager and Quality Manager read/report/export/print only; indexes `(reference_doctype, reference_name, signed_at)`, `(signer, signed_at)`, `(previous_signature)` (`on_doctype_update`) |
| Service | `compliance/esign.py`: `sign(doc, meaning, action, password, reason=None, permission="write")`, `authenticate`, `record_failed_signing`, `last_signature`, `record_fingerprint(doc, action, version)`, `signed_content`, `version_1_content`, `signature_fingerprint`, `signature_problems`, `verify_signatures`, `signed_content_report`, `changes_since`, `is_unchanged_since`; `SignedFields`; constants `MEANINGS`, `SHARED_ACCOUNTS`, `SIGNED_CONTENT`, `VERSION_1_DECLARED`, `SYSTEM_MAINTAINED` (version 1), `UNCHANGED`/`SIGNED_AGAIN`/`CHANGED`/`UNACCOUNTED`; `compliance/signed_records.py` (declared content of sixteen record types); field `fingerprint_version` on the Electronic Signature; exception `SigningRefused(frappe.ValidationError)` |
| Register / desk entry | `compliance/signing.py`: `SignedAct(meaning, perform)`, `SIGNING_ACTIONS`, `sign_action` (`@frappe.whitelist(methods=["POST"])`), `signing_actions`, `signatures_of`, `onload`, `signatures_for_print` |
| Fingerprints | `compliance/fingerprint.py`: `record_content`, `without_volatile_fields`, `VOLATILE_FIELDS`, `canonical`, `sha256` (canonical JSON, sorted keys, UTF-8), `keyed_sha256`/`trail_key` (HMAC-SHA256, key derived from `encryption_key`), `version_of`, `keyed_since` (`meddevice_suite_keyed_fingerprints_since` in site_config.json, also written by `setup.install.apply_configuration`), `Trail`, `chain_problems`, `in_chain_order`, `plain_text`, `chain_head` (left-join on the link field, `FOR UPDATE`) |
| Hooks (`hooks.py`) | `doc_events["*"]["onload"]` → `signing.onload`; `jinja.methods` → `signing.signatures_for_print`; `User.before_rename` → `audit.keep_user_id`; `User.on_trash` → `audit.keep_user_with_history`; `Custom DocPerm.before_validate` → `roles.refuse_hand_added_permission` (2026-10-12) |
| API routes | `POST /api/method/meddevice_suite.compliance.signing.sign_action` (`doctype`, `name`, `action`, `password`, `reason`, `meaning` — the meaning shown, checked against the register); `GET /api/method/meddevice_suite.compliance.signing.signing_actions?doctype=…`; `/api/resource/Electronic Signature` read-only under the DocType permissions |
| Desk | `public/js/esign.js` (`meddevice_suite.esign.sign(frm, action, meaning, {ask_reason})`, *Electronic signatures* dashboard section); `public/js/audit_trail.js` **Verify Integrity** → `audit_timeline.verify_record` (`signature_chain`, `signatures`, `signatures_intact`); list `/desk/electronic-signature` in the **Compliance** area (`workspace_sidebar/compliance.json`; area visible to Quality Manager and System Manager, `setup/desk.py`) |
| Lockout | Frappe `get_login_attempt_tracker` / `check_password(delete_tracker_cache=False)`; System Settings `allow_consecutive_login_attempts`, `allow_login_after_fail`; failed signing → Activity Log via `frappe.enqueue(queue="short")` |
| Retention / archive | `compliance/retention.py` "Compliance evidence" 10 years; `compliance/archive.py` copies `electronic_signatures` into each record's file |
| Data flow | Button → dialog → `sign_action` → act's `perform(doc, password, reason)` → checks → `esign.sign` → lock record row → `chain_head` → insert ESIG → act completes → commit; refusal anywhere rolls back everything except the queued Activity Log |

## 7. Defects, edge cases and risks

**Confirmed and fixed (all with tests, from the git history of `compliance/`):** a wrong password signed the user out (Frappe's `AuthenticationError`; now `SigningRefused`); failed signings were lost with the rolled-back request (now queued to Activity Log); renaming a record broke its chain (now `document_name_at_signing` is hashed, not `reference_name`); a reason with `<`/`>` was stored altered after fingerprinting (now refused); a disabled account with a live session could still sign (now refused); signatures were slow at scale (indexes added, `b1ab3c75`); a second signing of the same record could chain to a stale head (now `FOR UPDATE` and link-based head, `15970df7`). Foundation review 2026-10-12: a signature could be inserted past the service by anyone given create (refused in `before_insert`); a signature could name another person (owner must be the signer); a user who signed could be deleted and the ID reused (refused); Controlled Document Withdraw and Customer Require Licence were signed *Approved*; verification ordered by time and read a clock set back as tampering (now by links). Independent review 2026-10-15 (`test_signature_integrity.py`): Verify Integrity checked only the chain, never whether the record still held what each signature bound (now `signed_content_report`, per signature); a signature pointed at another record verified there (now its name at signing must be one the record has had); sixteen signed record types bound their whole record, so a field added to one broke every earlier signature (now each declares its signed content); the chain was unkeyed SHA-256, so database access alone could rewrite an older signature and recompute the chain (now HMAC-SHA256 under a key outside the database, version 2, with version 1 kept verifiable).

**Potential (not confirmed as defects):**
- Fixed 2026-10-10: a site that allows unlimited attempts, or has no lockout time, is given the standard limit (5 attempts, 15 minutes) by `apply_lockout`, run on install and every migrate (since 2026-10-11 written with the rest of the access policy, `compliance/access_policy.py`); the value is Quality Settings' (URS-PLT-013) (`test_the_site_locks_an_account_after_wrong_passwords`).
- The signed-content declarations are hand-kept: a field the signers approve that is added to a record type is not bound until a declaration names it (it is then bound only once set). Reviewers check the declaration when a signed DocType gains fields. `SYSTEM_MAINTAINED`, `ACT_MAINTAINED` and `BOUND_WHEN_SET` now serve version-1 signatures only and must never change.
- The site's `encryption_key` (site_config.json) keys every version-2 fingerprint: it must be kept with the site's backups, as Frappe already requires for stored passwords; a site restored with another key reports every version-2 signature and audit event as not matching its fingerprint. Someone with both the database and site_config.json can still recompute the chains; only the anchor file and the archives (module 50 section 2.8, module 54) show that.
- Version-1 signatures (recorded before 2026-10-15) stay as protected as they were: a chain recorded wholly before the site moved to version 2 can still be rewritten by someone who can write the database, back-dated before that moment.
- The upgrade that brings version 2 must stop the site's workers before migrating: a worker still running the old code after the moment is recorded would write version-1 entries, which verification reports as rewritten.
- Telling *Changed since signing* from *Changed outside the audit trail* undoes the recorded audit events (`audit.undo`); a value the trail recorded in another form than the record holds it (a number recorded as `5` against `5.0` on a record type with no declared content) would read as *outside the audit trail*. Declared content compares numbers and dates as values.
- Guest is refused by the same `SHARED_ACCOUNTS` check but has no dedicated test.
- The dialog's `__(meaning)` and act labels are translatable; the fingerprint uses the untranslated values, so language settings cannot break verification (by design, no test for a translated session).

**Unverified in this review:** behaviour under a real lockout period in the browser (ES-02 step 5); the exact Frappe code path that locks an account (relied on through Frappe's tracker and covered by tests).

## 8. Test results and verification summary

Executed by the lead on 2026-10-10 on the current branch (local test site; CI = GitHub Actions on a fresh site):

| Check | Scope | Result |
| --- | --- | --- |
| Full server suite | `bench --site <test-site> run-tests --app meddevice_suite` | 46 unit + 588 integration passed |
| Module tests inside it | `compliance/test_esign.py` (TestReauthentication 7, TestSignatureContent 5, TestBinding 4, TestSignerIdentity 2, TestFingerprint 4, TestSignaturesAreImmutable 4, TestSignatureLookupsStayFastAtScale 1), `compliance/test_signing.py` (TestSigningDialog 7, TestSignaturesShownWithTheRecord 4), `test_audit.TestSignaturesSurviveRename`, `qms/test_document_control` TestReviewAndApproval and TestControlledCopy | passed (part of the suite above) |
| Browser suite, retries off | `run-ui-tests … --config retries=0`; module 49 titles: `document_control.js` [URS-CMP-030] ×2, [URS-CMP-003] ×1; `audit_trail.js` [URS-CMP-010] (Verify Integrity incl. signatures) | 59 of 59 passed |
| Semgrep (Frappe rules) | app | 0 findings |
| pre-commit, traceability | app | pass |
| GitHub CI | current branch | in progress |
| Manual browser tests ES-01…ES-09 | — | **not executed** (no manual tester session); the automated journeys cover ES-01 steps 1–3 and ES-05 step 2 (document_control.js, audit_trail.js) |

2026-10-12 changes (section 1): `compliance/test_immutable_records.py` and the edited meaning asserts were written without a site and are **not yet run**; ES-11 **not executed**.

Untested: ES-02 step 5 (real lockout in the browser), ES-06 step 3 (database-level tampering; only the server test `test_an_altered_signature_is_detected` covers it), ES-07 step 2 (documented gap), translated sessions.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Password re-entry; wrong password records nothing, keeps the session, counts to lockout; locked/disabled account cannot sign | Verified (automated) |
| Signature records user ID, full name, time, act, meaning, reason; six meanings only | Verified (automated) |
| Shared accounts refused; permission on the record required; only declared acts; POST only | Verified (automated) |
| Record and chain fingerprints; altered/removed signature and changed record detected; rename safe | Verified (automated + browser Verify Integrity) |
| Never edited or deleted, not even by Administrator; no role may create | Verified (automated) |
| Never inserted past signing or under another account; no hand-made permission opens it; a user who signed is disabled, not deleted; meanings fit the act and match the dialog; chain verified by links (2026-10-12) | **Not tested** (tests written, not yet run) |
| Shown on the form for anyone who may open the record | Verified (automated + browser journey) |
| Shown on printouts | Verified for Controlled Copy only; other print formats **Failed/gap** (section 2.7) |
| Manual ES-01…ES-09 | **Not tested** |
| CI green for the final commit | **Blocked** (in progress) |
| Client confirmation: reason optional vs required per act; lockout setting on the pilot | **Open** |
