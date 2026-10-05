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

Machine setup is in the [repository README](../../README.md). Every change
follows the engineering rules in [CONTRIBUTING.md](../../CONTRIBUTING.md):
workflow, code standards, tests, quality gates and change control.

## Requirement traceability

`meddevice_suite/qa/requirements.toml` is the scope baseline (the 57 agreed
modules) and the register of user requirements (`URS-…`). Tests cite what
they verify with `@verifies("URS-…")` from `meddevice_suite.qa`; UI tests
carry the id in their title. `python3 -m meddevice_suite.qa.traceability`
prints the traceability matrix; with `--check` it fails when an implemented
requirement has no passing test.

## Layout

| Path | Contents |
| --- | --- |
| `meddevice_suite/setup/` | Site configuration applied on install and every migrate |
| `meddevice_suite/qa/` | Requirement register and traceability tooling |
| `meddevice_suite/patches/` | Data migrations |
| `cypress/` | UI tests |

## License

GPL-3.0
