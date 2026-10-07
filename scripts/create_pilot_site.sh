#!/usr/bin/env bash
# Create the pilot site of Phase 2 and load its demo data, or add what is missing
# to an existing one. For a local or staging bench only: never run on production.
#
#   MARIADB_ROOT_PASSWORD=… ADMIN_PASSWORD=… PILOT_USER_PASSWORD=… \
#     scripts/create_pilot_site.sh [site]          (default site: pilot.localhost)
#
# The passwords are read from the environment and kept in the site's own
# site_config.json (sites/ is not in git). PILOT_USER_PASSWORD is the password of
# the demo users, one per job profile, e.g. stores@demo.example.com.
set -euo pipefail

SITE="${1:-pilot.localhost}"
: "${MARIADB_ROOT_PASSWORD:?set MARIADB_ROOT_PASSWORD}"
: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD}"
: "${PILOT_USER_PASSWORD:?set PILOT_USER_PASSWORD}"

cd "$(dirname "$0")/.."

if [[ -d "sites/$SITE" ]]; then
	echo "$SITE exists: adding only what is missing"
else
	bench new-site "$SITE" \
		--db-root-password "$MARIADB_ROOT_PASSWORD" \
		--admin-password "$ADMIN_PASSWORD" \
		--install-app erpnext \
		--install-app india_compliance \
		--install-app meddevice_suite
fi

# The mark the demo loader insists on: it refuses every site without it.
bench --site "$SITE" set-config --parse pilot_site 1
bench --site "$SITE" set-config pilot_user_password "$PILOT_USER_PASSWORD"

bench --site "$SITE" execute meddevice_suite.pilot.site.prepare_pilot_site
bench --site "$SITE" execute meddevice_suite.pilot.demo.load_demo_data

echo "Pilot site ready: http://$SITE:8000 (run 'bench --site $SITE add-to-hosts' once if the name does not resolve)"
