## Requirement

<!-- Ids from requirements.toml and their module numbers, e.g. URS-QMS-004 (module 33).
     Tooling or docs with no requirement: say "none" and why. -->

## What changes for the user

<!-- Observable behaviour, in plain language. -->

## Why

<!-- Client diagram, client decision (with date) or regulation clause. -->

## Impact and risk

<!-- Validated behaviour, records or roles affected; data migration (patch);
     e-signature or audit-trail relevance; anything a reviewer or auditor must know. -->

## Evidence

<!-- Tests added or changed, coverage, UI tests, screenshots for screen changes. -->

## Checklist (CONTRIBUTING.md section 11)

- [ ] Requirement in `requirements.toml`, traced to a module and a source
- [ ] Code only in `apps/meddevice_suite/`; official apps untouched
- [ ] Configuration applied by `apply_configuration`, idempotent
- [ ] Tests cite the requirement and cover refusal paths; coverage ≥ 90%
- [ ] Requirement status `implemented`
- [ ] All quality gates pass locally and in CI
- [ ] Documentation updated where behaviour or setup changed
- [ ] Change control (section 8) followed, if it applies
