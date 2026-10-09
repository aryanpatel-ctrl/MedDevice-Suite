#!/usr/bin/env bash
# Start what the CI job's service containers provide: MariaDB on 3306 with root
# password "root", and Redis on 13000 (cache) and 11000 (queue).
set -euo pipefail

if ! sudo mariadb-admin ping --silent &> /dev/null; then
	sudo install -d -o mysql -g mysql /run/mysqld
	sudo -u mysql nohup mariadbd --bind-address=127.0.0.1 --port=3306 &> /tmp/mariadb.log &
	timeout 60 bash -c 'until sudo mariadb-admin ping --silent &> /dev/null; do sleep 1; done'
	sudo mariadb -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('root') OR unix_socket; FLUSH PRIVILEGES;"
fi
for port in 13000 11000; do
	redis-cli -p "$port" ping &> /dev/null || redis-server --port "$port" --daemonize yes --save "" > /dev/null
done
echo "MariaDB and Redis are up"
