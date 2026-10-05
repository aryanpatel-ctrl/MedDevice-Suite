# Engineering rules

These rules apply to every change in this repository: features, fixes,
configuration, upgrades and documentation, whether written by a person or with
AI assistance. They exist because this is a regulated medical-device system
(ISO 13485, 21 CFR Part 11, CDSCO MDR 2017): an untraced change is a validation
gap, and an unreviewed change to released behaviour is a compliance finding.

"Must" is mandatory. "Should" may be departed from only with the reason
written in the pull request.

## 1. Non-negotiables

1. **Official apps are read-only.** Nothing under `apps/frappe/`,
   `apps/erpnext/` or `apps/india_compliance/` is ever edited. All behaviour is
   added in `apps/meddevice_suite/`. CI rejects any change to these folders
   (`scripts/verify_vendored.sh`).
2. **Scope is the client requirement.** Every change traces to a requirement
   in `apps/meddevice_suite/meddevice_suite/qa/requirements.toml`, which traces
   to one of the 57 agreed modules. Work outside them is a change request to
   the client, not a feature.
3. **Configuration is code.** Nothing is configured by hand on a site. Custom
   fields, property setters, roles, permissions, workflows, desk visibility and
   settings are applied by `meddevice_suite.setup.install.apply_configuration`
   (runs on install and after every `bench migrate`) and must be idempotent.
4. **Every change is verified by a test that cites its requirement.**
5. **Secrets never enter git.** Credentials live in `sites/*/site_config.json`
   or the environment. `sites/` is ignored for this reason.
6. **Regulated records are never destroyed.** Records that need approval are
   submittable (draft → submitted → cancelled/amended); nothing submitted is
   hard-deleted or edited in place.

## 2. Workflow for a feature or change

### 2.1 Requirement first

Before writing code, add or update the requirement in
`requirements.toml`:

```toml
[[requirement]]
id = "URS-QMS-004"          # URS-<AREA>-<nnn>; next free number in the area
module = 33                 # module number from the [[module]] list
title = "A non-conformance cannot be closed until its disposition is approved by QA with an electronic signature."
source = "D1 NCR / Rework-Scrap-Use-as-is; ISO 13485 8.3"
status = "planned"
```

- **id** is permanent. Never renumber, reuse or delete one; a dropped
  requirement becomes `status = "withdrawn"`.
- **title** is one testable sentence: observable behaviour, not
  implementation. "The system shall be user-friendly" is not testable.
- **source** names the client diagram (D1, D2, D3), the client decision with
  its date, or the regulation clause.
- **status** becomes `implemented` in the same pull request that adds the
  passing tests. The traceability gate fails if it is `implemented` without
  them.

Area codes in use: `PLT` platform, `MST` master data, `SAL` sales, `PLN`
planning, `PUR` purchasing, `STK` stores and lots, `PRD` production, `QC`
inspection, `STR` sterilization, `REL` batch release, `DSP` dispatch,
`FIN` finance, `QMS` quality system, `TRN` training, `EQP` equipment,
`PMS` post-market, `CMP` compliance (e-signature, audit trail, retention),
`RPT` reports. Add a new code here before using it.

### 2.2 Branch

Branch from `main`:

| Prefix | For |
| --- | --- |
| `feature/<module>-<slug>` | new behaviour, e.g. `feature/33-ncr-disposition` |
| `fix/<slug>` | defect in released behaviour |
| `chore/<slug>` | tooling, CI, dependencies, docs |
| `upgrade/<app>-<version>` | official app upgrade (section 8) |

`main` is always releasable: every commit on it passes every gate.

### 2.3 Build

Follow sections 3 to 6.

### 2.4 Verify locally

All gates in section 7 pass before the pull request is opened.

### 2.5 Pull request

Fill in the pull request template completely. One pull request delivers one
requirement or one coherent set of requirements. Review checks this document,
not personal taste.

## 3. Where code goes

Everything lives in `apps/meddevice_suite/meddevice_suite/`:

| Path | Contents |
| --- | --- |
| `<module>/doctype/<doctype>/` | DocTypes of a Frappe module, exported by developer mode |
| `<module>/report/`, `<module>/page/` | reports and pages of that module |
| `setup/` | `apply_configuration` and everything it applies |
| `patches/` + `patches.txt` | one-off data migrations |
| `qa/` | requirement register and traceability tooling |
| `public/` | JavaScript and CSS for the desk and shop-floor screens |
| `cypress/integration/` (app root) | UI tests |

**Frappe modules.** Group DocTypes by functional area, one Frappe module per
area (for example QMS, Batch Records, Sterilization, Post Market,
Compliance). Add the module to `modules.txt` before creating its first
DocType. A DocType belongs to exactly one module.

**Extending ERPNext, in order of preference:**

1. Hooks and `doc_events` in `hooks.py` (validate, on_submit, on_cancel …).
2. Custom fields and property setters applied in `setup/` with
   `frappe.custom.doctype.custom_field.custom_field.create_custom_fields`.
3. `extend_doctype_class` / `override_whitelisted_methods`, only when 1 and 2
   cannot do it; the pull request explains why.
4. `override_doctype_class` is not used: it replaces ERPNext's class and
   breaks on upgrade.

## 4. Code standards

**Naming**

- DocTypes: Title Case, singular, never the name of an existing Frappe,
  ERPNext or India Compliance DocType. ERPNext already has, among others,
  `Non Conformance` and `Quality Action`; check with
  `frappe.db.exists("DocType", name)` before choosing a name.
- Custom fields on standard DocTypes: fieldname prefixed `mds_`
  (`mds_lot_status`), so they never collide with a future upstream field.
- Python: modules and functions `snake_case`, classes `PascalCase`, constants
  `UPPER_SNAKE_CASE`. Names say what something is or does; no abbreviations a
  QA reviewer would not know.

**Python**

- Formatting and linting are enforced by pre-commit (ruff, tabs, line length
  110). Do not disable a rule inline without a comment saying why.
- Type hints on every function signature.
- Comments explain why, not what. A docstring is required where the reason
  for a design is not obvious from the code (see `setup/desk.py`).
- Use the query builder or parameterised `frappe.db` calls. Never build SQL
  by string formatting.
- No queries inside loops over records; fetch in one query.
- `ignore_permissions=True` and `frappe.flags.ignore_*` are allowed only in
  setup code and patches, each with a comment saying why.
- Every `@frappe.whitelist()` method checks permission explicitly
  (`frappe.has_permission` / `doc.check_permission`) before acting.
- Errors shown to users use `frappe.throw(_("…"))`; unexpected failures are
  logged with `frappe.log_error`. No `print`.
- Dates and times come from `frappe.utils` (`today`, `now_datetime`,
  `getdate`), which use the site's time zone (Asia/Kolkata). Never
  `datetime.now()`.
- Quantities and money use the field's precision (`flt(value, precision)`);
  no bare floats compared for equality.
- Every user-facing string is translatable: `_("…")` in Python,
  `__("…")` in JavaScript.

**JavaScript**

- Formatting and linting are enforced by pre-commit (prettier, eslint).
- Client scripts only improve the form; every rule that matters is enforced
  on the server too.

**DocType design**

- Records that require approval or signature are submittable.
- Every link field that must exist is mandatory; every status is a `Select`
  with a fixed list, changed only by code paths that validate the transition.
- Permissions are set per role in the DocType, least privilege first. A role
  that does not need a record does not get read access to it.
- Indexes on fields used in list filters and reports.

## 5. Compliance requirements in code

- **Electronic signatures (21 CFR 11.50, 11.70, 11.200).** Every action the
  requirement says is signed (approve, release, close, disposition) goes
  through the e-signature service of module 49 once it ships: re-entry of
  the password, the signature meaning, and a permanent link to the record.
  Until module 49 ships, such features are not marked `implemented`.
- **Audit trail (21 CFR 11.10(e)).** Changes to regulated records are
  recorded by the module 50 audit trail with who, when, old value, new value
  and reason. Code never writes around it (no direct `frappe.db.set_value`
  on regulated records outside documented system processes).
- **Data integrity (ALCOA+).** Records are attributable to a user, made at
  the time of the event, and corrected by amendment, never by overwrite.
- **Lot status.** No code path issues, consumes or dispatches stock from a
  lot that is not `Approved`.

## 6. Tests

- Server tests live next to the code as `test_*.py` and run on the dedicated
  test site, never on a working site.
- `UnitTestCase` for logic without the database; `IntegrationTestCase` for
  anything that reads or writes records. Tests create their own data and do
  not rely on the demo company or on other tests.
- Each test, or its class, carries `@verifies("URS-…")` from
  `meddevice_suite.qa`. A UI test puts the id in its title:
  `it("[URS-PLT-001] …")`.
- Every requirement is verified by at least one test of the behaviour it
  describes, including the refusal paths ("cannot be closed until …" needs a
  test that the close is refused).
- User-visible journeys (receipt → inspection → release → production →
  batch release → dispatch, complaint → recall) get a UI test.
- Coverage stays at or above 90%; new code should be fully covered.
- A bug fix starts with a failing test that reproduces it.

## 7. Quality gates

A change merges only when all of these pass, locally and in CI. Commands run
from the bench root unless marked *(app)* (`apps/meddevice_suite`).

| Gate | Command |
| --- | --- |
| Official apps unchanged | `scripts/verify_vendored.sh` |
| Lint and format | `pre-commit run --all-files` |
| Server tests | `bench --site <test-site> run-tests --app meddevice_suite --coverage --junit-xml-output apps/meddevice_suite/reports/junit.xml` |
| Coverage ≥ 90% | `env/bin/coverage report --data-file=sites/.coverage --rcfile=apps/meddevice_suite/pyproject.toml` |
| Traceability *(app)* | `python3 -m meddevice_suite.qa.traceability --check --junit reports/junit.xml --output reports/traceability.md` |
| UI tests | `bench --site <test-site> run-ui-tests meddevice_suite --headless --browser electron` |
| Migrates cleanly | `bench --site <test-site> migrate` |

Before running UI tests locally, make the test site's name resolve once with
`bench --site <test-site> add-to-hosts` (macOS does not resolve `*.localhost`
for Node). Terminals inside VS Code or Cursor set `ELECTRON_RUN_AS_NODE`,
which stops Cypress from starting; prefix the command with
`env -u ELECTRON_RUN_AS_NODE` there.

## 8. Changes that need change control

These change validated behaviour or the validated platform. The pull request
states the impact, the regression tests run and who approved it.

- **Upgrading Frappe, ERPNext or India Compliance.** On an
  `upgrade/<app>-<version>` branch: replace the app folder with the new
  release, update `ref` and `commit` in `pinned-apps.json`, raise the version
  range in `apps/meddevice_suite/pyproject.toml` if the major changes, run
  `scripts/verify_vendored.sh` and every gate, and run ERPNext's own tests for
  the DocTypes we extend.
- **Changing an `implemented` requirement.** Update its title in place (the
  id stays), update the tests in the same pull request, and record the
  client decision in `source`.
- **Schema changes to existing DocTypes.** Renamed or removed fields need a
  patch that migrates existing data. Fields are never repurposed.
- **Patches.** Listed in `patches.txt` under `[pre_model_sync]` or
  `[post_model_sync]`, idempotent, and never edited once released; a mistake
  is fixed by a new patch.

## 9. Commits

- One logical change per commit; every commit passes the gates.
- Subject: what the change does for the user or the project, in plain
  English, imperative mood, at most 72 characters, no trailing full stop.
  `Block issue of quarantined lots to work orders`, not `fix bug` or
  `update stock_entry.py`.
- Body (required unless the subject says it all): why the change is needed,
  what it affects, and anything a reviewer or auditor should know. Wrap at
  72 characters.
- Reference the requirement where there is one: `Implements URS-STK-002.`
- No work-in-progress, merge-noise or "fix typo in previous commit" commits
  on `main`; tidy the branch before merging.

## 10. Releases

- `apps/meddevice_suite/meddevice_suite/__init__.py` holds the app version,
  semantic versioning: major for changes needing client re-validation, minor
  for new requirements, patch for fixes.
- A release is a tag `vX.Y.Z` on `main` with its traceability matrix, JUnit
  report and coverage report (the CI `server-test-evidence` artifact)
  archived as the validation record.

## 11. Definition of done

- [ ] Requirement in `requirements.toml`, traced to a module and a source
- [ ] Code only in `apps/meddevice_suite/`, following sections 3 to 5
- [ ] Configuration applied by `apply_configuration`, idempotent
- [ ] Tests cite the requirement, cover refusal paths, coverage ≥ 90%
- [ ] Requirement status `implemented`
- [ ] All gates in section 7 pass locally and in CI
- [ ] Documentation updated where behaviour or setup changed
- [ ] Pull request template complete
