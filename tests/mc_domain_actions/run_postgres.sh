#!/bin/bash
# Disposable socket-only PostgreSQL for tests/mc_domain_actions (no TCP, synthetic DB, removed after).
# Usage: tests/mc_domain_actions/run_postgres.sh /path/to/postgres/bin   (run under heavy_job.py)
set -euo pipefail
B="$1"; ROOT=$(mktemp -d /private/tmp/mcda.XXXX); D=$ROOT/data; S=$ROOT/s; mkdir -m 700 $S
trap '"$B/pg_ctl" -D "$D" stop -m fast >/dev/null 2>&1 || true; rm -rf "$ROOT"' EXIT
"$B/initdb" -D "$D" -U mc_fixture --auth-local=trust --auth-host=reject --no-locale --encoding=UTF8 >/dev/null
"$B/pg_ctl" -D "$D" -l "$ROOT/log" -o "-k $S -c listen_addresses='' -c max_connections=20" -w start >/dev/null
"$B/createdb" -h "$S" -U mc_fixture mc12_test_guest_actions
cd "$(dirname "$0")/../.."
MC_BINDING_TEST_DSN="host=$S user=mc_fixture dbname=mc12_test_guest_actions" PYTHONDONTWRITEBYTECODE=1 \
  "${PYTHON:-python3}" -m unittest discover -s tests/mc_domain_actions -v
