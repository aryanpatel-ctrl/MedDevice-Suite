# The GitHub runner of the UI tests job, for scripts/ci-local.sh: Ubuntu 24.04,
# a "runner" user with sudo, MariaDB 11.8 and Redis (the job's service
# containers, here in the same container so they share its 2 CPUs as they share
# the runner's), Python 3.14, Node 24 and what Cypress's Electron needs.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=Etc/UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
		build-essential ca-certificates curl git gnupg pkg-config rsync sudo tzdata xz-utils \
		libffi-dev libssl-dev \
		redis-server \
		xvfb libgtk-3-0t64 libgbm1 libnss3 libasound2t64 libxss1 libxtst6 libnotify4 \
		libxkbcommon0 libdrm2 fonts-liberation \
	&& rm -rf /var/lib/apt/lists/*

# MariaDB 11.8, the version of the CI service container, from MariaDB's own repository.
RUN curl -fsSL https://r.mariadb.com/downloads/mariadb_repo_setup \
		| bash -s -- --mariadb-server-version=mariadb-11.8 --skip-maxscale \
	&& apt-get install -y --no-install-recommends mariadb-server mariadb-client libmariadb-dev \
	&& rm -rf /var/lib/apt/lists/*

# Node 24 and yarn, as actions/setup-node with node-version 24.
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& npm install -g yarn \
	&& rm -rf /var/lib/apt/lists/*

# Python 3.14, as actions/setup-python, and bench.
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.14 \
	&& ln -s "$(uv python find 3.14)" /usr/local/bin/python3.14 \
	&& uv venv --python 3.14 /opt/bench \
	&& uv pip install --python /opt/bench/bin/python frappe-bench pip \
	&& ln -s /opt/bench/bin/bench /usr/local/bin/bench
ENV PYTHON=/usr/local/bin/python3.14

RUN useradd --create-home --shell /bin/bash runner \
	&& echo "runner ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/runner

COPY services.sh /usr/local/bin/ci-services
USER runner
WORKDIR /home/runner
