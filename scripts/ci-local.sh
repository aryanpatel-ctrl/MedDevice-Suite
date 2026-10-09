#!/usr/bin/env bash
# Run the UI tests on this machine as the CI job runs them: Linux, 2 CPUs at a
# GitHub runner's pace and 7 GB, shared by the database, Redis, the bench and
# the browser, a fresh bench and site built by the same scripts as CI's, and the
# same Electron browser.
# A failure seen in CI is reproduced and fixed here, instead of by pushing.
#
#   scripts/ci-local.sh up             build the runner and the test site (once)
#   scripts/ci-local.sh test [args]    copy this checkout's meddevice_suite in,
#                                      migrate, rebuild its assets and run the
#                                      UI tests; args go to Cypress, e.g.
#                                      --spec cypress/integration/standard_flow.js
#                                      --config retries=0
#   scripts/ci-local.sh run [args]     up, test, then down: a clean full run
#   scripts/ci-local.sh shell          a shell in the runner
#   scripts/ci-local.sh down           remove the runner and its site
#
# The bench is this checkout as it is, committed or not (the official apps as
# committed); `test` copies the app's latest changes in again. Screenshots of failures and the bench log are copied to
# ci-local-results/. Needs Docker (Docker Desktop on macOS).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

IMAGE=meddevice-ci-runner
CONTAINER=meddevice-ci
SITE=test.localhost
CPUS="${CI_LOCAL_CPUS:-2}"
# CPU time allowed across those CPUs. A GitHub runner's core is slower than an Apple
# silicon one, and most of the work runs on one thread at a time (a request, the page's
# script), so the cap is below one core: at 0.7 the journey's steps took CI's times
# (run 37901087062); on 2 full cores they ran 1.5 to 1.8 times faster.
CPU_TIME="${CI_LOCAL_CPU_TIME:-0.7}"
MEMORY="${CI_LOCAL_MEMORY:-7g}"
WORK=/home/runner/work
RESULTS=ci-local-results

in_runner() {
	docker exec -i -e SITE="$SITE" -e CI=Yes -w "$WORK" "$CONTAINER" bash -euo pipefail -c "$1"
}

running() {
	[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2> /dev/null)" == true ]]
}

up() {
	if running; then
		echo "runner already up; 'down' first for a fresh one"
		return
	fi
	docker build --quiet -t "$IMAGE" scripts/ci-local > /dev/null
	docker rm -f "$CONTAINER" &> /dev/null || true
	# Caches survive between runners, as the CI job's pip cache does.
	# Pinned to $CPUS CPUs, not only limited to their time: the browser and the build tools
	# size their thread pools by the CPUs they see, as on the runner.
	docker run -d --name "$CONTAINER" --cpuset-cpus "0-$((CPUS - 1))" --cpus "$CPU_TIME" --memory "$MEMORY" --memory-swap "$MEMORY" \
		--shm-size 2g -v meddevice-ci-cache:/home/runner/.cache \
		-v "$PWD/.git:/source.git:ro" "$IMAGE" sleep infinity > /dev/null
	docker exec "$CONTAINER" sudo chown runner:runner /home/runner/.cache
	echo "runner up: $CPUS CPUs at $CPU_TIME CPUs' time, $MEMORY"

	local commit
	commit="$(git rev-parse HEAD)"
	docker exec "$CONTAINER" git clone --quiet --no-checkout /source.git "$WORK"
	in_runner "git -c advice.detachedHead=false checkout --quiet $commit"
	copy_checkout
	in_runner "ci-services"
	echo "bootstrapping the bench (as CI's setup-bench, with assets)"
	in_runner "scripts/bootstrap.sh --dev --skip-redis > /tmp/bootstrap.log 2>&1 || { tail -40 /tmp/bootstrap.log; exit 1; }
		sed -i '/^redis_/d' Procfile"
	echo "creating the test site"
	in_runner "scripts/ci/create_test_site.sh > /tmp/site.log 2>&1 || { tail -40 /tmp/site.log; exit 1; }"
	in_runner "scripts/ci/start_bench.sh"
}

# A tar of the listed files without macOS's metadata (._ files, extended
# attributes), which the Linux side would otherwise read as source files.
pack() {
	COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs -cf - "$@"
}

copy_checkout() {
	# This checkout's own files as they are now, committed or not, over the
	# runner's clone. The official apps are left as cloned: they never change.
	git ls-files -co --exclude-standard -- . ':!apps/frappe' ':!apps/erpnext' ':!apps/india_compliance' \
		| pack -T - | docker exec -i "$CONTAINER" tar -xf - -C "$WORK"
}

copy_app() {
	# Tracked and new files of the app, as they are in this checkout.
	git ls-files -co --exclude-standard apps/meddevice_suite | pack -T - \
		| docker exec -i "$CONTAINER" bash -c "rm -rf /tmp/app && mkdir /tmp/app && tar -xf - -C /tmp/app"
	in_runner "rsync -a --delete --exclude node_modules --exclude __pycache__ --exclude '*.egg-info' \
		--exclude cypress/screenshots --exclude reports /tmp/app/apps/meddevice_suite/ apps/meddevice_suite/"
}

test_ui() {
	running || up
	copy_app
	in_runner "ci-services > /dev/null
		bench --site $SITE migrate > /tmp/migrate.log 2>&1 || { tail -40 /tmp/migrate.log; exit 1; }
		bench build --app meddevice_suite > /tmp/build.log 2>&1 || { tail -40 /tmp/build.log; exit 1; }
		scripts/ci/start_bench.sh"
	local cypress_args="" status=0
	(($#)) && cypress_args="--$(printf ' %q' "$@")"
	in_runner "rm -rf apps/meddevice_suite/cypress/screenshots
		bench --site $SITE run-ui-tests meddevice_suite --headless --browser electron $cypress_args" || status=$?
	rm -rf "$RESULTS" && mkdir -p "$RESULTS"
	docker cp "$CONTAINER:$WORK/apps/meddevice_suite/cypress/screenshots" "$RESULTS/" &> /dev/null || true
	docker cp "$CONTAINER:$WORK/bench_start.log" "$RESULTS/" &> /dev/null || true
	echo "UI tests exit $status; screenshots and bench log in $RESULTS/"
	return "$status"
}

down() {
	docker rm -f "$CONTAINER" &> /dev/null || true
	echo "runner removed"
}

command="${1:-}"
shift || true
case "$command" in
up) up ;;
test) test_ui "$@" ;;
run)
	up
	status=0
	test_ui "$@" || status=$?
	down
	exit "$status"
	;;
shell) docker exec -it -e SITE="$SITE" -w "$WORK" "$CONTAINER" bash ;;
down) down ;;
*)
	sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
	exit 2
	;;
esac
