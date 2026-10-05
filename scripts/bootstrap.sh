#!/usr/bin/env bash
# Turn a clone of this repository into a working bench, or bring an existing
# bench up to date. Safe to run again.
#
#   git clone https://github.com/aryanpatel-ctrl/MedDevice-Suite.git frappe-bench
#   cd frappe-bench
#   scripts/bootstrap.sh --dev
#
# Options: --dev           also install test and coverage tools
#          --skip-assets   do not build the desk's JavaScript and CSS
#          --skip-redis    Redis is provided elsewhere (CI service containers);
#                          do not generate local Redis config
#
# Needs Python 3.14 (or PYTHON=<interpreter>), Node 24+, yarn, Redis,
# MariaDB 11.8 and bench (pip install frappe-bench).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON="${PYTHON:-python3.14}"
PROJECT_APP=meddevice_suite
dev=""
build_assets=yes
local_redis=yes
for option in "$@"; do
	case "$option" in
	--dev) dev="--dev" ;;
	--skip-assets) build_assets="" ;;
	--skip-redis) local_redis="" ;;
	*) echo "unknown option: $option" >&2 && exit 2 ;;
	esac
done

pinned_names() {
	python3 -c 'import json; [print(p["name"]) for p in json.load(open("pinned-apps.json"))]'
}

# mysqlclient compiles against MariaDB's client library, which Homebrew keeps
# outside the default search paths.
if command -v brew > /dev/null && prefix="$(brew --prefix mariadb@11.8 2> /dev/null)"; then
	export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
fi

mkdir -p sites logs config/pids
{
	pinned_names
	echo "$PROJECT_APP"
} > sites/apps.txt

[[ -f sites/common_site_config.json ]] || bench setup config
[[ -x env/bin/python ]] || bench setup env --python "$PYTHON"
# Python and Node separately: the combined `bench setup requirements` expects
# every app to be its own git repository, and here the whole bench is one.
# With --dev, bench installs only the development tools, hence a third call.
bench setup requirements --python
bench setup requirements --node
[[ -z "$dev" ]] || bench setup requirements --dev
[[ -z "$local_redis" || -f config/redis_cache.conf ]] || bench setup redis
[[ -f Procfile ]] || bench setup procfile
[[ -z "$build_assets" ]] || bench build

# Installers must not change the official apps. Some rewrite files on install
# (ERPNext's banking yarn.lock, for example); put those back as released.
official_apps=$(pinned_names | sed 's#^#apps/#')
if ! git diff --quiet -- $official_apps; then
	echo "restoring official app files changed during install:"
	git diff --name-only -- $official_apps | sed 's/^/  /'
	git checkout -- $official_apps
fi

# Every app must import from the environment; a failure here fails the setup.
while read -r name; do
	version="$(env/bin/python -c "import $name; print($name.__version__)")"
	echo "$name $version"
done < <(
	pinned_names
	echo "$PROJECT_APP"
)
