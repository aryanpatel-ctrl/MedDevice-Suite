# MedDevice Suite app

Medical-device manufacturing QMS and compliance layer for ERPNext, built for a
Foley catheter and urine bag manufacturer (ISO 13485, 21 CFR Part 11,
CDSCO MDR 2017).

ERPNext covers standard ERP (buying, stock, manufacturing, selling, accounts);
India Compliance covers GST, e-invoice and e-way bill. This app adds what a
regulated device manufacturer needs on top: lot status control, batch records
and release, sterilization, QMS (document control, NCR, deviation, CAPA,
change control, training, risk), post-market (complaints, vigilance, recall),
e-signatures and an immutable audit trail.

Machine setup is in the [repository README](../../README.md). The exact
Frappe, ERPNext and India Compliance versions are pinned in
[`pinned-apps.json`](../../pinned-apps.json); `pyproject.toml` declares the
range this app supports. Changing either is a change-control decision.

## Engineering rules

1. **Never modify frappe, erpnext or india_compliance.** All behaviour lives in
   this app: hooks, doc events, overrides, custom fields.
2. **Configuration is code.** Anything this app sets on a site is applied by
   `meddevice_suite.setup.install.apply_configuration`, which runs on install
   and after every `bench migrate`. Every step in it must be idempotent.
   No configuration is done by hand in the UI.
3. **Scope is the client requirement.** Standard features outside it are
   hidden (see `meddevice_suite/setup/desk.py`), not deleted.
4. **Every change ships with tests traced to a requirement** (see below).

## Testing and validation evidence

Tests run on a dedicated test site, never on a working site.

**Requirements.** `meddevice_suite/qa/requirements.toml` is the scope baseline
(the 57 agreed modules) and the register of user requirements (`URS-…`). Ids
are permanent; a dropped requirement is marked `withdrawn`, never deleted.

**Tracing tests.** A Python test cites what it verifies with
`@verifies("URS-…")` from `meddevice_suite.qa` (on a method or a whole class).
A UI test puts the id in its title: `it("[URS-PLT-001] …")`.

**Gates.** A change is accepted only when all of these pass. Commands run from
the bench root unless marked *(app)*, which means `apps/meddevice_suite`.

| Gate | Command | Rule |
| --- | --- | --- |
| Lint and format | `pre-commit run --all-files` | ruff, eslint, prettier, traceability |
| Server tests | `bench --site <test-site> run-tests --app meddevice_suite --coverage --junit-xml-output apps/meddevice_suite/reports/junit.xml` | all pass |
| Coverage | `env/bin/coverage report --data-file=sites/.coverage --rcfile=apps/meddevice_suite/pyproject.toml` | at least 90% |
| Traceability *(app)* | `python3 -m meddevice_suite.qa.traceability --check --junit reports/junit.xml --output reports/traceability.md` | every implemented requirement verified by a passing test |
| UI tests | `bench --site <test-site> run-ui-tests meddevice_suite --headless --browser electron` | all pass |

Before running UI tests locally, make the test site's name resolve once with
`bench --site <test-site> add-to-hosts` (macOS does not resolve `*.localhost`
for Node). Terminals inside VS Code or Cursor set `ELECTRON_RUN_AS_NODE`, which
stops Cypress from starting; prefix the command with
`env -u ELECTRON_RUN_AS_NODE` there.

## CI

- **CI** builds a fresh bench at the pinned versions, installs this app and
  runs the traceability, server-test, coverage and UI-test gates on every push
  to `main` and every pull request. The traceability matrix, JUnit report and
  coverage report are published as the `server-test-evidence` artifact and on
  the run summary.
- **Linters** run pre-commit, Frappe Semgrep rules and pip-audit.

## License

GPL-3.0
