#!/usr/bin/env bash
# Create the test site the CI jobs run against, with the production app set.
# Run by the CI workflow and by scripts/ci-local.sh, so both build the same site.
#
# Needs a bootstrapped bench, MariaDB on 3306, env SITE and, if the MariaDB root
# password is not "root" (the CI service container's), DB_ROOT_PASSWORD.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
: "${SITE:?set SITE to the test site name}"

bench new-site --db-root-password "${DB_ROOT_PASSWORD:-root}" --admin-password admin \
	--install-app erpnext "$SITE"
bench --site "$SITE" install-app india_compliance
bench --site "$SITE" install-app meddevice_suite
bench --site "$SITE" set-config admin_password admin
bench --site "$SITE" set-config allow_tests true
bench --site "$SITE" execute meddevice_suite.qa.site_setup.prepare_test_site
