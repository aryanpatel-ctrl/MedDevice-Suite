#!/usr/bin/env bash
# Prove that each official app in apps/ is exactly the release pinned in
# pinned-apps.json.
#
# For every pin, the release tag is fetched from its upstream repository and
# must still point at the pinned commit, and the git tree of apps/<name> in the
# given revision (default HEAD) must equal that commit's tree. Equal trees mean
# identical file names, contents and modes: not one byte has been changed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

revision="${1:-HEAD}"
upstream="$(mktemp -d)"
trap 'rm -rf "$upstream"' EXIT
git -C "$upstream" init --quiet

pins() {
	python3 -c 'import json; [print(p["name"], p["url"], p["ref"], p["commit"]) for p in json.load(open("pinned-apps.json"))]'
}

failures=0
while read -r name url ref commit; do
	git -C "$upstream" fetch --quiet --depth 1 "$url" "refs/tags/$ref"
	tag_commit="$(git -C "$upstream" rev-parse 'FETCH_HEAD^{commit}')"
	release_tree="$(git -C "$upstream" rev-parse 'FETCH_HEAD^{tree}')"
	bundled_tree="$(git rev-parse "$revision:apps/$name" 2>/dev/null || echo missing)"

	if [[ "$tag_commit" != "$commit" ]]; then
		echo "FAIL $name: tag $ref now points at $tag_commit, pinned commit is $commit" >&2
		failures=$((failures + 1))
	elif [[ "$bundled_tree" != "$release_tree" ]]; then
		echo "FAIL $name: apps/$name differs from release $ref" >&2
		failures=$((failures + 1))
	else
		echo "ok   $name $ref ($commit)"
	fi
done < <(pins)

exit $((failures > 0))
