#!/usr/bin/env python3
"""The UI test specs one CI shard runs, as a comma-separated list for `run-ui-tests --spec`.

The UI tests ran as one queue of nearly three hours, most of it the browser test sheets. CI runs
them in parallel shards instead (.github/workflows/ci.yml): each long spec has a shard of its own,
and the last shard runs every spec no other shard names, so a new spec is run without being added
here. Nothing is left out; `--check` proves it (run by the CI job and by qa/test_ci_shards.py).

        scripts/ci/ui_shard.py <shard>    the specs of shard 1 … SHARDS
        scripts/ci/ui_shard.py --check    every spec in exactly one shard, every named spec existing
"""

import sys
from pathlib import Path

SPECS = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "meddevice_suite"
    / "cypress"
    / "integration"
)

# The longest specs, by their CI time (2026-10-19): D4 63 min, D10 23, D9a 23, D9b 15, the screen
# sweep 15, D2 13, D1 and D3 5 each; every other spec together about 7 min.
NAMED: dict[int, tuple[str, ...]] = {
    1: ("sheet_d4.js",),
    2: ("sheet_d10.js",),
    3: ("sheet_d9a.js",),
    4: ("sheet_d9b.js", "sheet_d1.js"),
    5: ("screen_sweep.js", "sheet_d2.js", "sheet_d3.js"),
}
SHARDS = 6  # the last one: every spec not named above


def all_specs() -> list[str]:
    return sorted(path.name for path in SPECS.glob("*.js"))


def specs_of(shard: int) -> list[str]:
    if shard in NAMED:
        return list(NAMED[shard])
    if shard == SHARDS:
        named = {spec for specs in NAMED.values() for spec in specs}
        return [spec for spec in all_specs() if spec not in named]
    raise SystemExit(f"no shard {shard}: shards are 1 to {SHARDS}")


def problems() -> list[str]:
    found = set(all_specs())
    named = [spec for specs in NAMED.values() for spec in specs]
    issues = [
        f"{spec} is named but does not exist" for spec in named if spec not in found
    ]
    issues += [
        f"{spec} is named in two shards"
        for spec in sorted(set(named))
        if named.count(spec) > 1
    ]
    run = [spec for shard in range(1, SHARDS + 1) for spec in specs_of(shard)]
    issues += [f"{spec} is in no shard" for spec in sorted(found - set(run))]
    issues += [
        f"shard {shard} runs nothing"
        for shard in range(1, SHARDS + 1)
        if not specs_of(shard)
    ]
    return issues


def main() -> None:
    if sys.argv[1:] == ["--check"]:
        issues = problems()
        print(
            "\n".join(issues)
            or f"{len(all_specs())} specs in {SHARDS} shards, each once"
        )
        sys.exit(1 if issues else 0)
    print(
        ",".join(f"cypress/integration/{spec}" for spec in specs_of(int(sys.argv[1])))
    )


if __name__ == "__main__":
    main()
