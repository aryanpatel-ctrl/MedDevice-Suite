# MedDevice Suite

ERP and quality management system for a Foley catheter and urine bag
manufacturer, built on ERPNext and aligned with ISO 13485, 21 CFR Part 11 and
CDSCO MDR 2017.

This repository is the project's complete Frappe bench: our app and the exact
releases of Frappe, ERPNext and India Compliance it runs on. Only secrets
(`sites/`) and files each machine generates for itself are kept out of git.

## Layout

| Path | What it is |
| --- | --- |
| `apps/meddevice_suite/` | The project app: all customisation, QMS modules and tests. See its [README](apps/meddevice_suite/README.md). |
| `apps/frappe/`, `apps/erpnext/`, `apps/india_compliance/` | Official releases, kept exactly as released. Never edit these; all changes go into the project app. |
| `pinned-apps.json` | Which release of each official app is in `apps/`, with its upstream commit. |
| `scripts/bootstrap.sh` | Turns a clone into a working bench. Used by developers and CI alike. |
| `scripts/verify_vendored.sh` | Proves each official app is byte-for-byte its pinned release. Runs in CI. |
| `scripts/create_pilot_site.sh` | Creates the pilot site with fictional demo data for the client walk-through. Never on production. |
| `scripts/ci/` | The CI job's steps (test site, bench start), shared by CI and `scripts/ci-local.sh`. |
| `scripts/ci-local.sh` | Runs the UI tests on this machine as CI runs them, to reproduce and fix a CI failure locally. |
| `.github/` | CI: release integrity, traceability, server tests, coverage, UI tests, linters. |
| `.pre-commit-config.yaml` | Checks run before every commit. |

## Setting up a machine

Requires Python 3.14, Node 24+, yarn, MariaDB 11.8, Redis and
[bench](https://github.com/frappe/bench) (`pip install frappe-bench`).

```bash
git clone https://github.com/aryanpatel-ctrl/MedDevice-Suite.git frappe-bench
cd frappe-bench
scripts/bootstrap.sh --dev
pre-commit install
```

Then create a working site and a separate test site:

```bash
bench new-site <site> --install-app erpnext
bench --site <site> install-app india_compliance
bench --site <site> install-app meddevice_suite
```

## The pilot site

The client walks through the standard flow on a separate pilot site with
fictional demo data (Phase 2). The demo loader refuses every site that is not
marked as a pilot site, so demo data can never reach a working or production
site.

```bash
MARIADB_ROOT_PASSWORD=… ADMIN_PASSWORD=… PILOT_USER_PASSWORD=… scripts/create_pilot_site.sh
```

Each job profile has a demo user, such as `stores@demo.example.com`, signing in
with `PILOT_USER_PASSWORD`.

## Daily use

```bash
bench start                                   # run the system
scripts/bootstrap.sh --dev                    # after pulling changes
bench --site <site> migrate                   # after pulling changes
bench --site <test-site> run-tests --app meddevice_suite
```

## Reproducing a CI failure

The UI tests run in CI on a GitHub runner: 2 slow CPUs shared by the database,
Redis, the bench and the browser, so a page takes about three times as long as
on a developer's Mac, and a test that is too quick to act fails only there.
`scripts/ci-local.sh` runs them the same way here, in Docker: Linux, 2 CPUs at
the runner's pace, a fresh bench and test site built by CI's own scripts, and
the same Electron browser.

```bash
scripts/ci-local.sh up      # once: the runner and its test site (about 5 minutes)
scripts/ci-local.sh test --spec cypress/integration/standard_flow.js --config retries=0
scripts/ci-local.sh down    # when done
```

`test` copies in the app as it is in the checkout, committed or not, so a fix
is tried before it is pushed. Screenshots of failures land in
`ci-local-results/`. Needs Docker Desktop; on a Mac that may sleep, run it under
`caffeinate -i`.

## Upgrading Frappe, ERPNext or India Compliance

An upgrade is a change-control decision. Replace the app folder with the new
release, update its `ref` and `commit` in `pinned-apps.json`, run
`scripts/verify_vendored.sh`, and commit both together with the change record.

Engineering rules, the requirement register and the quality gates are
described in the [app README](apps/meddevice_suite/README.md).
