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
| `.github/` | CI: release integrity, traceability, server tests, coverage, UI tests, linters. |
| `.pre-commit-config.yaml` | Checks run before every commit. |
| `CONTRIBUTING.md` | Engineering rules for every change: workflow, standards, tests, quality gates, change control. Read before contributing. |

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

## Daily use

```bash
bench start                                   # run the system
scripts/bootstrap.sh --dev                    # after pulling changes
bench --site <site> migrate                   # after pulling changes
bench --site <test-site> run-tests --app meddevice_suite
```

## Upgrading Frappe, ERPNext or India Compliance

An upgrade is a change-control decision. Replace the app folder with the new
release, update its `ref` and `commit` in `pinned-apps.json`, run
`scripts/verify_vendored.sh`, and commit both together with the change record.

Every change follows [CONTRIBUTING.md](CONTRIBUTING.md). The requirement
register and test tooling are described in the [app README](apps/meddevice_suite/README.md).
