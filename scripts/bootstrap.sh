#!/usr/bin/env bash
# Bring a bench to this project's pinned app set. Safe to run again.
#
#   bench init frappe-bench --frappe-branch v16.36.1 --python python3.14
#   cd frappe-bench
#   git init -b main && git remote add origin <this repository> && git pull origin main
#   scripts/bootstrap.sh
#
# Installs the third-party apps at the versions in pinned-apps.json, checks that
# every installed app is at its pinned version, and registers apps/meddevice_suite.
# A version change is a change-control decision, so a mismatch stops the script
# instead of being "fixed" automatically.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT_APP=meddevice_suite

pins() {
	python3 -c 'import json; [print(a["name"], a["url"], a["ref"]) for a in json.load(open("pinned-apps.json"))]'
}

installed_version() {
	env/bin/python -c "import $1; print($1.__version__)" 2>/dev/null || true
}

while read -r name url ref; do
	if [[ ! -d "apps/$name" ]]; then
		bench get-app --branch "$ref" "$url"
	fi

	version="$(installed_version "$name")"
	if [[ "v$version" != "$ref" ]]; then
		echo "error: $name is at ${version:-unknown}, pinned to $ref" >&2
		exit 1
	fi
	echo "$name $ref"
done < <(pins)

if ! grep -qx "$PROJECT_APP" sites/apps.txt; then
	env/bin/python -m pip install --quiet -e "apps/$PROJECT_APP"
	python3 - "$PROJECT_APP" <<'EOF'
import sys
path = "sites/apps.txt"
apps = [line.strip() for line in open(path) if line.strip()]
open(path, "w").write("\n".join([*apps, sys.argv[1]]) + "\n")
EOF
fi
echo "$PROJECT_APP registered"
