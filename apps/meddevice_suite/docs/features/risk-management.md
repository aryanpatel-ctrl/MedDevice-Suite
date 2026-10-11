# Risk management, ISO 14971:2019 (module 39): feature document and browser test guide

Requirements: **URS-QMS-017 to URS-QMS-025** (`meddevice_suite/qa/requirements.toml`). Builds on document control (module 30), change control (36), master approval and versioning (1, 2, 3, 6), e-signature (49), reminders (60) and Quality Settings.
Code: `meddevice_suite/qms/risk_management.py` and `meddevice_suite/qms/pinned_records.py`, which module 31 shares. Records: *Risk Acceptability Policy* (with *Risk Scale Level* and *Risk Matrix Cell*), *Risk Management File* (with *Risk Management File Item*), *Risk Assessment* (with *Risk Hazard* and *Risk Control Measure*), *Risk Management Report* (with *Risk Report Information Source*) and *Risk Review*. Report: *Risk Register*. Reminder: *Risk Review*.
DFD trace: D2 Risk Management flowchart (Risk Identification, Risk Analysis, Risk Evaluation, Control Measures, Risk Report, Risk Review). Decisions: plan D-13 (scales and families), D-21 (who approves the policy) and Q6 (what a design element is), all decided 2026-10-11.

## 1. Feature overview

**Purpose.** Each product family, such as "Foley catheter" or "Urine collection bag", has one risk management file. Its analysis is issued in signed revisions. Every unacceptable risk is controlled by a real record of the system: a specification, a labelling document, an item, a bill of materials, an operation or an inspection specification. The file is reviewed again whenever one of those records changes, when a change request touches the file, and once a year.

**Why it matters.** ISO 14971 and ISO 13485 7.1 require a documented risk management process. The risk acceptability criteria are top management's (ISO 14971 4.2). Risk controls must be implemented and verified, and the file must be kept up to date with production and post-production information (ISO 14971 10). A control that lives only as free text cannot be shown to be implemented, and a specification revised without looking at the risk file breaks the link an auditor follows.

| Job profile | Here |
| --- | --- |
| QA Manager (Quality Manager) | Writes the policy and signs it *Reviewed*; creates files; authors, returns and approves assessments; authors and approves reports; closes reviews (all signed) |
| Second QA person (Quality Manager) | Approves what the first QA person authored. Approval is always by a third person |
| Management (Manufacturing, Sales, Purchase or Stock Manager) | Approves the policy (signed, plan D-21); reads everything |
| Production Supervisor (Manufacturing User) | Reads; may be named reviewer of an assessment and signs *Reviewed* |
| System Administrator | Reads only |
| Every other job | May pick a risk management file on a change request; reads nothing |

## 2. Detailed functional explanation

- **Risk Acceptability Policy (URS-QMS-017).** A new policy starts with the D-13 scales. Severity runs from 1 to 5: Negligible, Minor, Serious, Critical, Catastrophic. Probability runs from 1 to 5: Improbable, Remote, Occasional, Probable, Frequent. All 25 pairs are listed, with their acceptability left blank. QA decides each pair as *Acceptable*, *Review (ALARP)* or *Unacceptable*, states how the criteria were set, and signs **Sign as Reviewed**. Review is refused while a pair is undecided, or while a pair is more acceptable than one with a lower severity or probability. Management signs **Approve**, and must be a different person from the QA reviewer (D-21). The policy is then *In Force*, and the previous policy becomes *Superseded* with a link to its successor. Either side can return a reviewed policy to draft with a reason. A policy is never cancelled or deleted.
- **Risk Management File (URS-QMS-018).** QA creates one file per product family. It records the family name (unique), the intended use, the reasonably foreseeable misuse, the risk management plan (a controlled document) and the family's finished goods. Each finished good is in one file only, and only finished goods can be listed. Before the first approval, QA edits the file directly. After it, the scope (family, intended use, misuse, plan, items) changes only through a new revision: approving a revision writes the revision's scope back to the file. The status, the assessment in force, the report in force and the next periodic review are set by the system only.
- **Risk Assessment (URS-QMS-019 to URS-QMS-022).** One revision of the file's analysis, named `<file>-R01`, `-R02` and so on. It copies the file's scope when it is started.
  - **Hazards.** Each hazard has an ID, an ISO 14971 Annex C category (energy, biological and chemical, performance related), the hazard, the sequence of events, the hazardous situation and the harm. It is rated for severity and probability before control and, as the residual risk, after control.
  - **Computed risk.** The system works out each acceptability from the policy in force and overwrites anything typed.
  - **Controls.** Each control answers a hazard. It is of an ISO 14971 7.1 kind and links the record that implements it:
    - *Inherently safe design and manufacture*: a Specification controlled document, an item or a bill of materials (Q6).
    - *Protective measures*: any controlled document, inspection specification, item, bill of materials or operation.
    - *Information for safety*: a *Packaging and Labelling* controlled document. That document type exists in document control, so no substitute type was needed.
  - **Evidence.** Each control links the record verifying its implementation and the record verifying its effectiveness: an effective document, a document revision, a submitted inspection, change request or qualification, or an active important file. A hazard that a control introduces (ISO 14971 7.5) must be one of the assessment's hazards.
  - **Rules at the author's signature and again at approval.** The plan must be effective. Every unacceptable risk needs a control. Every hazard needs a residual risk that is no higher than its risk before control. A residual risk that is not *Acceptable* needs a justification: as low as reasonably practicable, or benefit-risk.
  - **Three signers.** The author (QA) signs **Sign as Author**. The named reviewer, another person with read access, signs **Sign as Reviewer**. QA, neither the author nor the reviewer, signs **Approve**. The reviewer or QA can **Return for Changes** with a reason.
  - **What approval does.** Approval is refused while any control's record is no longer in force. It pins each control's version and the policy, makes the revision the file's assessment in force, supersedes the previous revision, takes that revision's report out of force, and sets the next periodic review.
  - **Starting a new revision.** Once the file has an approved revision, a new one is started only under an approved change request that lists the file, or to answer an open risk review. Only one revision can be in progress at a time. Nothing is cancelled or deleted.
- **Risk Management Report (URS-QMS-023).** QA writes the report on the file's approved revision in force. It confirms that the plan was implemented and how this was checked, and evaluates the overall residual risk. The highest residual risk is taken from the assessment. It also names the effective SOPs that collect production and post-production information. The author signs it, and another QA person approves it. It is then the file's report in force, until a newer report is approved or a new revision is approved.
- **Risk Review (URS-QMS-024, URS-QMS-025).** Reviews are raised by the system only:
  - when a change request listing the file is fully approved (*Change Request*);
  - when a pinned control gets a new version or is no longer in force: a document revised or withdrawn, a master's version raised or disabled, a bill no longer the active default (*Control Changed*). This happens at the save, and the daily sweep catches changes written past the document hooks;
  - when the periodic review falls due (*Periodic*);
  - through `raise_review(file, trigger, source_doctype, source_name)`, which the complaint (43) and CAPA (35) modules will call (*Complaint*, *CAPA*).

  One review is open per file and source, and the same event (the same source at the same version) raises a review only once. A review is due within *Risk Review Due Within* (30 days, Quality Settings) and stays a *Risk Review* reminder until it is closed. QA either starts a new revision under it, whose approval closes the review as *New revision*, or signs **Close: No Change** with a rationale. Closing a periodic review sets the next one to *Risk File Reviewed Every* months ahead (12, Quality Settings).
- **Change control.** A change request whose impact on risk is *Yes* cannot be assessed until it lists a Risk Management File. Its final approval raises a review of each file it lists. It cannot be closed (effectiveness check) while such a review is open.
- **Risk Register.** Each hazard of the assessments in force, with its ratings, risks, controls (with their pinned versions) and justification. It can be filtered by file and by residual risk.

## 3. Requirements and acceptance criteria

| # | Requirement | Verification (`qms/test_risk_management.py`, `qms/test_change_control.py`) |
| --- | --- | --- |
| R1 | QMS-017: one policy in force; QA reviews, Management approves as a second person; D-13 scales; complete and monotonic matrix; returned with a reason; an approved assessment keeps its policy | `TestPolicy.*`, `TestPermissionsAndRegistration.*` |
| R2 | QMS-018: each finished good in one file; finished goods only; one file per family; effective plan; scope changes only through a revision | `TestRiskManagementFile.*` |
| R3 | QMS-019: risks computed, not typed, on the right axes; ratings within the scales; situation and harm required; hazard IDs unique | `TestScoring.*` |
| R4 | QMS-020: unacceptable needs a control; no free-text control; labelling for information for safety; design element for inherent safety; out-of-force control blocks approval; versions pinned at approval; introduced hazard analysed | `TestControls.*`, `TestPinnedRecords.*` |
| R5 | QMS-021: residual risk required, not higher, justified when not acceptable; verifying records required and in force | `TestEvidenceAndResidualRisk.*` |
| R6 | QMS-022: three distinct signers; only the author edits the draft; system fields not typed; return with a reason; only by signing; never deleted or cancelled; wrong password | `TestSignatures.*`, `TestPermissionsAndRegistration.*` |
| R7 | QMS-023: report on the revision in force; plan, conclusion and SOPs required; author and approver distinct; out of date after a new revision | `TestReport.*` |
| R8 | QMS-024: revision needs a change request or open review; risk impact needs a file; approval raises a review (Major only when fully approved); open review blocks closing the change | `TestRevisionUnderChange.*`, `test_change_control.TestRiskReviews.*` |
| R9 | QMS-025: one review per changed control; withdrawal; daily sweep; periodic review and setting; no change needs a rationale; new revision closes its review; interface for modules 43 and 35; system-raised only; reminder | `TestReviews.*` |
| R10 | Browser: hazard, then control, then signed approval, then signed report | `cypress/integration/risk_management.js` `[URS-QMS-020]` |

## 4. Step-by-step browser testing guide

Use two QA accounts (QA-A and QA-B), the Production Supervisor and a Management account.

### RM-01 Policy (QA, then Management)
1. As QA-A, go to **Quality › Risk Acceptability Policy › New**. Give it a title and the basis of the criteria, then save. Expected: the D-13 scales and 25 pairs appear.
2. Set every pair's acceptability and save. Use **Sign as Reviewed**.
3. As Management, use **Approve**. Expected: the policy is *In Force*, and the previous one is *Superseded*.
4. As someone who holds both QA and Management, review a policy and then try to approve it. Expected: refused.

### RM-02 File and first assessment
1. As QA-A, create a **Risk Management File** for "Foley catheter". Choose an effective plan and add the finished goods. Then use **Start New Revision**.
2. Add hazard H-01 (severity 4, probability 4) and save. Expected: *Risk Before Control* shows *Unacceptable*, whatever you typed.
3. Add a control of kind *Information for safety* that links an SOP. Expected: refused (it must be a Packaging and Labelling document).
4. Add a control of kind *Inherently safe design and manufacture* that links an effective Specification document, with both verifying records. Set residual severity 2 and probability 1, choose the Production Supervisor as reviewer, and save.
5. Sign in turn: **Sign as Author** (QA-A), **Sign as Reviewer** (Production Supervisor), **Approve** (QA-B). Expected: the assessment is *Approved* and each control shows its pinned version. The file is *In Force*, with its next periodic review a year away.

### RM-03 Report
As QA-A, create a **Risk Management Report** on the approved assessment. Tick the plan as implemented, describe how it was checked, write the conclusion and add the post-market SOP. Use **Sign as Author**. Then, as QA-B, use **Approve**. Expected: the file shows the report in force.

### RM-04 Control changed
Issue a new revision of the Specification document. Expected: one *Risk Review* (*Control Changed*) on the file, and a *Risk Review* reminder for QA. Close it with **Close: No Change** and a rationale. Expected: the review is *Closed*, and the reminder closes on the next daily run.

### RM-05 Change request
1. Raise a change request and send it for assessment.
2. As QA, set *Impact on risk = Yes* and try to assess. Expected: refused until the file is listed.
3. Approve the change. Expected: a *Change Request* review is raised.
4. Do the actions, then try the effectiveness check. Expected: refused while the review is open.
5. Start a new revision from the file, naming the change request, and approve it. Expected: the review closes and the effectiveness check is allowed.

## 5. End-to-end business workflow
Management approves the policy → QA opens the family's file → the assessment's hazards are scored by the policy → each unacceptable risk is linked to the specification, label, item, bill or operation that controls it → the assessment is signed by author, reviewer and QA, and its controls are pinned → QA reports on it → a change, a changed control, a complaint, a CAPA or the yearly review raises a risk review → QA answers it with a new revision or a signed "no change".

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Rules | `risk_management.score` (validate), `check_controls`, `check_content`, `out_of_force`, `take_effect`, `raise_review`, `raise_periodic_reviews`, `control_pins` and `controls_changed` |
| Shared | `pinned_records.version_token`, `in_force`, `on_pinned_change`, `sweep`; `PIN_OWNERS` registry. Module 39 registers *Risk Management File*; module 31 will add the device master record |
| Signed acts | Policy: Review (Reviewed), Approve (Approved), Return (Rejected). Assessment: Author (Authored), Review (Reviewed), Return (Rejected), Approve (Approved). Report: Author, Return, Approve. Review: Close: No Change (Reviewed) |
| Not signed | `start_revision` (whitelisted, QA): a draft copied from the revision in force |
| Hooks | `on_update` of Item, Routing, Operation, Quality Inspection Template and Controlled Document; BOM `on_submit`, `on_update_after_submit` and `on_cancel`. Daily `raise_periodic_reviews` and `pinned_records.sweep`, before `reminders.run` |
| Version token | Controlled Document: current revision. Item, Routing, Operation and inspection specification: `mds_version`. BOM: `name\|is_active\|is_default\|docstatus` |
| In force | Document *Effective*; item approved and enabled; routing and operation approved (routing enabled); specification stating item and stage; BOM submitted, active and default |
| Change control | `AFFECTED_TYPES` + *Risk Management File*; `assess` (`require_file_for_risk_impact`), `approve` (`raise_change_reviews`), `verify` (`open_reviews_of`) |
| Settings | `risk_periodic_review_months` 12 (1–36), `risk_review_due_days` 30 (1–180) |
| Permissions | QA edits (no submit, cancel, amend or delete); Management, Manufacturing User and System Manager read; Desk User may select a file; Risk Review: QA reads (raised only by the system) |
| Signature content | `SYSTEM_MAINTAINED`: policy status, approval and supersession fields; assessment status and superseded_by; report status; review docstatus |
| Item master | *Risk Management File* and *Risk Assessment* are in `SETUP_TYPES`: a draft finished good may be analysed before QA approves it |
| Retention | All five records are in "Quality system records" (10 years) |
| Print format | Not built in this change. The report prints with the standard print view and its signatures |

## 7. Defects, edge cases and risks
- **A new revision supersedes the report.** Approving a new revision takes the file's report out of force until a new report is approved. This is deliberate: the report must cover the analysis in force. Module 31 pins the report in force.
- **A control changed and "no change" decided.** The approved revision keeps its old pinned version. The review records the version it was raised for, so the daily sweep does not raise it again. The next revision re-pins the control.
- **Draft finished goods.** A file may list finished goods that QA has not yet approved, but a control that links an unapproved item is not in force and blocks approval.
- **Hazard categories.** The categories follow the three groups of ISO 14971:2019 Annex C, Table C.1, as we read it. If QA's procedure uses the ISO/TR 24971 lists, the Select options change with a release.
- **Change requests and the review.** A change request implemented before its revision is started can no longer authorise one. The revision is then started from the change request's open review instead.
- **Failures in the hooks.** A failure while checking pins is logged and does not block the master's save; the daily sweep raises the review. Risk: until then, the file shows no review.
- **Complaints and CAPA.** Complaint and CAPA triggers have no caller until modules 43 and 35 exist.

**Independent review (2026-10-12).** An adversarial review of this module found the following, now fixed and tested:
- An item or a bill of materials could not be an inherently safe design control. Only a document must be a Specification.
- A revision was re-scored after its author signed. Scores are now fixed at the author's signature, and approval is refused if the policy changed since; the revision is returned and authored again.
- An unfinished revision or report could not be ended, which left its file, review and change request stuck. QA now signs **Withdraw** with a reason.
- A review could be closed "no change" while its control was out of force. That is refused, which also stops a recurring change slipping past its review.
- A risk could be lowered with no control. A reduced risk now needs the control that reduces it (ISO 14971 7).
- Two concurrent races were closed: approving two policies at once, and raising the same review twice. Both now use locking reads.

Two findings were kept as designed:
- **Approval pins the version in force at approval.** Approval refuses any control that is out of force, and every later change raises a review.
- **The author's and reviewer's signatures do not cover the approver's own fields.** This is the convention of document control (module 30): `verify_signatures` checks the chain, and the approval signature binds the approved content.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Policy reviewed by QA and approved by Management, one in force | Automated tests (results in the pull request) |
| Risks computed from the policy; controls typed and linked; versions pinned at approval | Automated tests (results in the pull request) |
| Three distinct signers; nothing deleted or cancelled | Automated tests (results in the pull request) |
| Reviews raised by change, control change, sweep, periodic due and the interface | Automated tests (results in the pull request) |
| Change request link enforced at assess, approve and close | Automated tests (results in the pull request) |
| Browser: hazard → control → approval → report | Cypress spec (results in the pull request) |
| Manual RM-01 to RM-05 | **Not tested** |
