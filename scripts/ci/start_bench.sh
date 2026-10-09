#!/usr/bin/env bash
# Start the bench for the UI tests, as CI does, and wait until the site answers.
# Run by the CI workflow and by scripts/ci-local.sh. Linux only (GNU sed, sudo).
#
# The file watcher and the scheduler stay off: the assets are built once, and
# scheduled jobs would change records while a test reads them. Safe to run
# again: a running bench is stopped first.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
: "${SITE:?set SITE to the test site name}"

grep -q " $SITE\$" /etc/hosts || echo "127.0.0.1 $SITE" | sudo tee -a /etc/hosts > /dev/null
sed -i -E 's/^(watch|schedule):/# \1:/' Procfile

if pkill -f "honcho start" 2> /dev/null; then
	timeout 30 bash -c 'while curl -s -o /dev/null http://127.0.0.1:8000; do sleep 1; done' || true
fi
nohup bench start &> bench_start.log &
timeout 120 bash -c "until curl -sf http://127.0.0.1:8000/api/method/ping -H 'Host: $SITE' > /dev/null; do sleep 2; done"
echo "bench is up for $SITE"
