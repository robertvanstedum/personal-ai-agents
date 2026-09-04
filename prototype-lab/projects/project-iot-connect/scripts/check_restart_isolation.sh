#!/bin/sh
# App-only restart with isolation proof.
# Records the app and integrations container IDs and start times, performs the
# documented restart sequence (`docker compose restart app` then
# `docker compose up -d --wait`), then proves: the app container restarted
# (same ID, new StartedAt) while the integrations container ID and StartedAt are
# unchanged and nothing was recreated. Exit 1 on any deviation.
set -u
COMPOSE="${COMPOSE:-docker compose}"

started_at() { docker inspect -f '{{.State.StartedAt}}' "$1"; }

app_before=$($COMPOSE ps -q app); int_before=$($COMPOSE ps -q integrations); pg_before=$($COMPOSE ps -q postgres)
[ -n "$app_before" ] && [ -n "$int_before" ] || { echo "FAIL  stack is not running"; exit 1; }
app_started_before=$(started_at "$app_before"); int_started_before=$(started_at "$int_before"); pg_started_before=$(started_at "$pg_before")
echo "before: app=$app_before started=$app_started_before"
echo "        integrations=$int_before started=$int_started_before"

$COMPOSE restart app || { echo "FAIL  restart app"; exit 1; }
$COMPOSE up -d --wait || { echo "FAIL  up --wait after restart"; exit 1; }

app_after=$($COMPOSE ps -q app); int_after=$($COMPOSE ps -q integrations); pg_after=$($COMPOSE ps -q postgres)
app_started_after=$(started_at "$app_after"); int_started_after=$(started_at "$int_after"); pg_started_after=$(started_at "$pg_after")
echo "after:  app=$app_after started=$app_started_after"
echo "        integrations=$int_after started=$int_started_after"

status=0
check() { if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 ($2 vs $3)"; status=1; fi; }
check "app container ID unchanged (restarted in place, not recreated)" "$app_before" "$app_after"
if [ "$app_started_before" != "$app_started_after" ]; then echo "PASS  app StartedAt changed (restart happened)"; else echo "FAIL  app StartedAt unchanged (no restart happened)"; status=1; fi
check "integrations container ID unchanged" "$int_before" "$int_after"
check "integrations StartedAt unchanged" "$int_started_before" "$int_started_after"
check "postgres container ID unchanged" "$pg_before" "$pg_after"
check "postgres StartedAt unchanged" "$pg_started_before" "$pg_started_after"
[ $status -eq 0 ] && echo "RESTART ISOLATION: PASS" || echo "RESTART ISOLATION: FAIL"
exit $status
