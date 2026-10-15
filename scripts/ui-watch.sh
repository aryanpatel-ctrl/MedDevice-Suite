#!/usr/bin/env bash
# Watch the browser tests: they run in a visible Chrome window, one job after another, and each
# spec is also recorded as a video to replay later (apps/meddevice_suite/cypress/videos/<spec>.mp4).
#
#   scripts/ui-watch.sh                         every spec
#   scripts/ui-watch.sh item_master             one spec (name without .js)
#   scripts/ui-watch.sh item_master,routing     several specs
#   scripts/ui-watch.sh --open                  Cypress's own window: pick a spec, watch it, re-run it
#
# SITE=<site> picks the site (default test.localhost). The site must be served (`bench start`) and
# its name must resolve (`bench --site <site> add-to-hosts`). CI keeps running headless, unchanged.
set -euo pipefail

cd "$(dirname "$0")/.."
site="${SITE:-test.localhost}"

# Terminals inside VS Code or Cursor set ELECTRON_RUN_AS_NODE, which stops Cypress from opening a
# window; macOS resolves *.localhost to IPv6 first, which the web server does not listen on.
run() { env -u ELECTRON_RUN_AS_NODE NODE_OPTIONS=--dns-result-order=ipv4first "$@"; }

if [[ "${1:-}" == "--open" ]]; then
	exec run bench --site "$site" run-ui-tests meddevice_suite --browser chrome
fi

spec_args=()
if [[ -n "${1:-}" ]]; then
	specs=""
	IFS=',' read -ra names <<<"$1"
	for name in "${names[@]}"; do
		specs+="${specs:+,}cypress/integration/${name%.js}.js"
	done
	spec_args=(--spec "$specs")
fi

# `--headless` selects Cypress's run mode (each spec once, then a summary); `--headed` after `--`
# shows its browser all the same; the videos are switched on for this run only.
CYPRESS_video=true run bench --site "$site" run-ui-tests meddevice_suite --headless --browser chrome \
	"${spec_args[@]}" -- --headed
echo "Videos: apps/meddevice_suite/cypress/videos/"
