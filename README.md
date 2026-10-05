# MedDevice Suite

ERP and quality management system for a Foley catheter and urine bag
manufacturer, built on ERPNext and aligned with ISO 13485, 21 CFR Part 11 and
CDSCO MDR 2017.

This repository is the project's Frappe bench. It tracks only the project's own
files; Frappe, ERPNext and India Compliance are installed at pinned versions.

## Layout

| Path | What it is |
| --- | --- |
| `apps/meddevice_suite/` | The project app: all customisation, QMS modules, tests. See its [README](apps/meddevice_suite/README.md). |
| `pinned-apps.json` | Exact versions of Frappe, ERPNext and India Compliance in use. Changing one is a change-control decision. |
| `scripts/bootstrap.sh` | Installs the pinned apps and registers the project app on a bench. Used by developers and CI alike. |
| `.github/` | CI: traceability, server tests, coverage, UI tests, linters. |
| `.pre-commit-config.yaml` | Checks run before every commit. |

## Setting up a machine

Requires Python 3.14, Node 24+, MariaDB 11.8, Redis and
[bench](https://github.com/frappe/bench).

```bash
bench init frappe-bench --frappe-branch v16.36.1 --python python3.14
cd frappe-bench
git init -b main
git remote add origin https://github.com/aryanpatel-ctrl/MedDevice-Suite.git
git pull origin main
scripts/bootstrap.sh
bench setup requirements --dev
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
bench --site <site> migrate                   # after pulling changes
bench --site <test-site> run-tests --app meddevice_suite
```

Engineering rules, the requirement register and the quality gates are
described in the [app README](apps/meddevice_suite/README.md).
