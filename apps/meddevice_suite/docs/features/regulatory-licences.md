# Regulatory licences and registrations (module 41) — feature document and browser test guide

Requirements: **URS-REG-001 to URS-REG-006** (`meddevice_suite/qa/requirements.toml`; area code REG). Builds on the important files repository (module 61), e-signature (49), reminders (60), change control (36).
Code: `meddevice_suite/qms/regulatory_licence.py`; records *Regulatory Licence* (with *Regulatory Licence Product*, *Regulatory Licence Obligation*, *Regulatory Submission*) and *Regulatory Licence Type*; alert *Regulatory Licence Expiring*; reminder category *Regulatory Licence*; change control in `qms/change_control.py`.
DFD trace: 9E Regulatory Compliance; 9C alerts. Decisions: plan section 16, D-12 (90-day lead time) and D-14 (QA owns licences), 2026-10-11.

## 1. Feature overview

**Purpose.** The plant's own licences, registrations and certificates are on one register, each verified by QA against its copy. Renewals and obligations are never missed, and a change that needs telling the authority names the licence it affects.

**Why it matters.** Under CDSCO MDR 2017 the plant manufactures and sells only under licences in force. ISO 13485 4.1.1 and 4.2.5 require the regulatory requirements to be documented and their records kept. A licence lapsing unnoticed, or a change not notified, can stop production.

| Job profile | Here |
| --- | --- |
| QA Manager | Records licences; verifies, rejects or withdraws them (signed); records obligations done and submissions |
| Management | Read |
| Others | No access |

## 2. Detailed functional explanation

- **Licence types** are a list QA keeps. Each says whether the type expires. A type kept in force by a fee or an audit has no end date, and its fee or audit is recorded as an obligation.
- **A licence** holds its type, number, issuing authority, site, valid from, valid until (required for a type that expires), the products it covers, its obligations (with due dates) and its copy. The copy is an *Important File* (module 61), which keeps every version, its SHA-256, and who may read it.
- **Verify** (QA, signed) checks:
  - the type, number and authority are filled in;
  - the licence has a start date;
  - a licence that expires has not expired;
  - the copy is active and readable by QA.

  It then pins the copy's **version and fingerprint**. A newer version uploaded later shows on the form as not the one verified.
- **Reject** a draft, or **Withdraw** a verified licence (the authority suspended or cancelled it), with QA's signed reason.
- **Renewal:** a new licence names the one it **renews**, which must be a verified licence of the same type. Verifying the renewal supersedes the old one in the same transaction. A rejected renewal leaves the old one as it was.
- **Never edited, cancelled or deleted.** After verification, only QA's acts change a licence: withdrawal, supersession, an obligation done, a submission recorded. QA's signature binds the licence as verified and its copy, not these later records, so the signature stays intact.
- **Obligations:** QA records one as done with its evidence as an Important File. This is an unsigned record, since a payment or an audit is not an approval.
- **Submissions** to the authority are recorded on the licence with their date, the authority's reference and the copy: device or plant master file, change notification, renewal application.
- **Alerts and reminders:** QA is alerted once when a verified licence ends, or an obligation falls due, within *Licence Renewal Alert* (90 days, Quality Settings) or is overdue. A *Regulatory Licence* reminder stays open until the renewal is verified or the obligation is done.
- **Change control:** a change request whose regulatory impact is "Yes" is assessed only when its affected records include the Regulatory Licence it affects.

## 3. Requirements and acceptance criteria

| # | Requirement | Verification |
| --- | --- | --- |
| L1 | REG-001 recorded and verified by QA with a signature; only QA; permissions | `TestVerification.*`, `TestPermissions.*` |
| L2 | REG-002 never edited, cancelled or deleted; reject and withdraw with a reason; renewal supersedes; rejected renewal keeps the original; same type | `TestNeverChangedAndRenewal.*` |
| L3 | REG-003 copy pinned; newer copy is not the verified one; archived copy refused | `TestVerification.*` |
| L4 | REG-004 alerted once within the lead time or overdue; reminder until renewed; obligation done only with evidence, by QA | `TestAlertsAndObligations.*` |
| L5 | REG-005 submissions with reference and copy | `TestSubmissions.*` |
| L6 | REG-006 regulatory change names the licence | `qms/test_change_control.py::test_a_change_needing_regulatory_notification_names_the_licence` |
| UI | Verify on the form with a signature | `cypress/integration/regulatory_licence.js` |

## 4. Step-by-step browser testing guide

### RL-01 Record and verify (QA)
1. **Quality › Regulatory Licence Type › New**: *Manufacturing licence (MD-5)*, *Expires* ticked.
2. **Important File › New**: upload the licence scan into a licence category.
3. **Quality › Regulatory Licence › New**: type, number, authority, dates, products, the copy. Save, then **Verify** and sign with a reason.

Expected: the licence is verified, and *Version Verified* and *Fingerprint* are filled in.

### RL-02 A newer copy
Upload a new version of the Important File. Expected: the licence form says the copy has a newer version than the one verified.

### RL-03 Renewal
Record a new licence that *Renews* the first, then verify it. Expected: the first one shows *Superseded* by the new one.

### RL-04 Obligation and alert
On a verified licence with an obligation due within 90 days, run the daily alerts (or wait a day). Expected: QA receives one alert and a reminder. Then **Obligation Done** with evidence closes the reminder.

### RL-05 Change control
Raise a change request with regulatory impact "Yes" that lists no licence. Expected: QA's assessment is refused until the licence is added.

## 5. End-to-end business workflow
Licence granted → QA records it with its copy and verifies it (signed) → obligations and submissions recorded → alert and reminder before expiry → renewal verified, which supersedes the old licence. A change needing notification names the licence; the notification is recorded as a submission.

## 6. Technical reference

| Layer | Item |
| --- | --- |
| Signed acts | Verify (Verified), Reject (Rejected), Withdraw (Rejected) |
| Unsigned QA records | `complete_obligation`, `add_submission` (whitelisted, QA only) |
| Signed content | `regulatory_licence.signed_content` (`esign.SIGNED_CONTENT`) |
| Daily | `send_expiry_alerts` (one savepoint per alert), reminder category *Regulatory Licence* |
| Setting | `regulatory_licence_warning_days` 90 (1 to 730) |
| Change control | "Regulatory Licence" in `AFFECTED_TYPES`; `require_licence_for_regulatory_change` in `assess` |

## 7. Defects, edge cases and risks
- An expired manufacturing licence does not yet block batch release or dispatch. That check belongs to modules 26 and 8 and is recorded for them (plan decision D-22).
- Which licences expire, and which are kept by a retention fee, is client data entered as licence types and obligations (plan D-06).
- The alert goes to QA only (D-14). Management sees the register.

## 8. Test results and verification summary
See the pull request.

## 9. Final QA checklist

| Item | Status |
| --- | --- |
| Verification signed, copy pinned | Verified (automated, and on screen by Cypress) |
| Renewal, rejection, withdrawal | Verified (automated) |
| Alerts, reminders, obligations, submissions | Verified (automated) |
| Change control link | Verified (automated) |
| Manual RL-01…RL-05 | **Not tested** |
