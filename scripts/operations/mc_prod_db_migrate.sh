#!/bin/bash
# Bring the production database up to Master Craftsman's schema (the Guild, actions, domain-action, MC tools and
# delegation migrations dev already has). Runbook: docs/runbooks/mc_container_prod.md.
#
#   mc_prod_db_migrate.sh rehearse   copy prod's database into a throwaway Postgres (no network, same host), apply every
#                                    migration there in one transaction, list the result, delete the copy. Prod untouched.
#   mc_prod_db_migrate.sh apply      back up prod's database (pg_dump, root 0600 under /opt/minimoi/backups), then apply
#                                    every migration in ONE transaction: all or nothing.
#
#   ... rehearse|apply --host-runtime   the same, with the host runtime's two migrations added LAST, in this order:
#                                    dispatcher 002 (protocol version 2), then supervision 003 (runtime decisions, contacts,
#                                    watch, outbox). Only by this explicit opt-in (host runtime P1-a, on Robert's word):
#                                    a plain run, which operations makes for other teams' migrations, never applies them.
#                                    Lock waits: with --host-runtime the one transaction opens with SET LOCAL
#                                    lock_timeout = '5s' (right after SET ROLE), so every file's lock wait is bounded, the
#                                    38 earlier ones' too (they re-run ALTER TABLE ... IF NOT EXISTS, which takes its lock
#                                    even with nothing to do); 002 and 003 also say it themselves. On LockNotAvailable the
#                                    whole transaction rolls back: nothing changed, run it again (a fresh backup is taken
#                                    each time). A plain run's transaction is unchanged. A timeout bounds the wait, not
#                                    002's rewrite of guild.mc_delegations (queue_seq): the rehearsal prints how long the
#                                    whole apply took on the production-shaped copy. After an --host-runtime run the
#                                    script checks that every 002 and 003 object exists.
#   mc_prod_db_migrate.sh version     prints this script's version; a copy older than P1-a has no `version` and prints
#                                    its usage instead (it would ignore --host-runtime: never run that copy with it). The
#                                    script and the SQL reach /opt/minimoi only through an MC image release (mc-agent-v*,
#                                    deploy-mc-agent.yml installs /opt/minimoi/scripts/ and /opt/minimoi/mc-migrations).
#
# The migration files are shipped beside this script in $DIR (MIGRATIONS lists them in dependency order; each is written
# to be safe to re-run). They run as the role that owns the existing Guild tables, so new tables have the same owner.
set -euo pipefail

DIR="${MC_MIGRATIONS_DIR:-/opt/minimoi/mc-migrations}"
DB=personal_agents
PG=postgres-ai-agents
MIGRATIONS=(
  minimoi_portal/guild_ui/sql/002_board.sql
  minimoi_portal/guild_ui/sql/003_media.sql
  minimoi_portal/guild_ui/sql/004_board_place.sql
  minimoi_portal/guild_ui/sql/005_board_size.sql
  minimoi_portal/guild_ui/sql/006_agent_reply_size.sql
  minimoi_portal/guild_ui/sql/007_mc_turn_ledger.sql
  minimoi_portal/guild_ui/sql/008_board_operations.sql
  core/minimoi_actions/sql/001_runtime_bindings.sql
  core/minimoi_actions/sql/002_standing_ingress.sql
  core/minimoi_actions/sql/003_standing_control.sql
  core/minimoi_actions/sql/004_action_receipts.sql
  core/minimoi_actions/sql/005_action_grants.sql
  core/minimoi_actions/sql/006_owner_annotation_intents.sql
  core/mc_domain_actions/sql/001_guest_action_evidence.sql
  core/mc_domain_actions/sql/002_guest_proposals.sql
  core/mc_tools/sql/001_tool_turns.sql
  core/mc_tools/sql/002_tool_run_binding.sql
  core/mc_tools/sql/003_offer_display_and_clocks.sql
  core/mc_tools/sql/004_tool_turn_revocation.sql
  core/mc_tools/sql/005_tool_runtime_instance.sql
  core/mc_tools/sql/006_tool_policy.sql
  core/mc_tools/sql/007_admission_outcome.sql
  core/mc_tools/sql/008_handoffs.sql
  core/mc_tools/sql/009_turn_revocations.sql
  core/mc_tools/sql/010_runtime_instance_history.sql
  core/mc_tools/sql/011_portal_epoch.sql
  core/mc_tools/sql/012_board_conversation.sql
  core/mc_tools/sql/013_host_reads.sql
  core/mc_tools/sql/014_host_artifacts.sql
  core/mc_tools/sql/015_agent_contact.sql
  core/mc_tools/sql/016_task_watch.sql
  core/mc_tools/sql/017_approved_agent_handoff.sql
  core/mc_tools/sql/018_status_reply_protocol.sql
  core/mc_tools/sql/019_delegate_build.sql
  core/mc_tools/sql/020_issue_create_update.sql
  core/mc_delegation_dispatcher/sql/001_delegations.sql
  core/supervision/sql/001_supervision.sql
  core/supervision/sql/002_grok_and_exchanges.sql
)
# The host runtime's migrations (P1-a): never in the list above; added, in this order and last, only by --host-runtime.
# 003 must stay last (S1-G closure C-N7): its SET LOCAL lock_timeout then bounds nothing but its own and 002's waits.
HOST_RUNTIME_MIGRATIONS=(
  core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql
  core/supervision/sql/003_runtime_decisions_and_watch.sql
)
VERSION="mc-migrate host-runtime-1"
if [[ "${1:-}" == version && $# -eq 1 ]]; then echo "$VERSION"; exit 0; fi
HOST_RUNTIME=0
case "${2:-}" in
  "") ;;
  --host-runtime) HOST_RUNTIME=1; MIGRATIONS+=("${HOST_RUNTIME_MIGRATIONS[@]}") ;;
  *) echo "usage: $0 rehearse|apply [--host-runtime]" >&2; exit 2 ;;
esac
[[ $# -le 2 ]] || { echo "usage: $0 rehearse|apply [--host-runtime]" >&2; exit 2; }

log() { echo "mc-migrate: $*"; }
die() { echo "mc-migrate: REFUSED: $*" >&2; exit 2; }

for f in "${MIGRATIONS[@]}"; do test -f "$DIR/$f" || die "missing $DIR/$f"; done

owner_of() {   # the role that owns the existing Guild tables in container $1
  docker exec "$1" psql -U postgres -d "$DB" -Atc "select tableowner from pg_tables where schemaname='guild' and tablename='floor_messages'"
}

apply_all() {  # container $1: every migration, one transaction, as the Guild owner role
  local c="$1" role; role=$(owner_of "$c")
  [[ "$role" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "cannot read the Guild owner role"
  local args=(-U postgres -d "$DB" -v ON_ERROR_STOP=1 --single-transaction -q -c "SET ROLE $role")
  # --host-runtime: every lock wait in the one transaction is bounded, not only 002's and 003's (P1-a review SF1).
  if [[ "$HOST_RUNTIME" == "1" ]]; then args+=(-c "SET LOCAL lock_timeout = '5s'"); fi
  for f in "${MIGRATIONS[@]}"; do args+=(-f "/mc-migrations/$f"); done
  docker exec "$c" rm -rf /mc-migrations
  docker cp "$DIR" "$c:/mc-migrations"
  # Most migrations carry their own BEGIN;/COMMIT;. Inside --single-transaction a COMMIT would end the outer
  # transaction partway (rehearsal, 26 Oct), so the copies lose those standalone lines: all or nothing, for real.
  docker exec "$c" find /mc-migrations -name '*.sql' -exec sed -i -E '/^[[:space:]]*(BEGIN|COMMIT)[[:space:]]*;[[:space:]]*$/d' {} +
  # (A function body's bare BEGIN ... END is not a transaction statement and is left alone.)
  if docker exec "$c" grep -rliE '^[[:space:]]*(BEGIN|COMMIT|ROLLBACK|END)[[:space:]]*(TRANSACTION|WORK)?[[:space:]]*;|START[[:space:]]+TRANSACTION' /mc-migrations >/dev/null; then
    die "a migration still controls its own transaction"
  fi
  # CREATE SCHEMA IF NOT EXISTS needs the database's CREATE right even when the schema exists, and the Guild owner role
  # does not hold it in prod (first apply, 26 Oct: refused, rolled back). For a schema that already exists the
  # statement is a no-op, so the copies drop it; a schema that does not exist yet is left in and fails loudly.
  local s
  for s in $(docker exec "$c" sh -c "grep -rhoiE '^[[:space:]]*CREATE SCHEMA IF NOT EXISTS [a-z_]+;' /mc-migrations" | awk '{print $6}' | tr -d ';' | sort -u); do
    if [[ "$(docker exec "$c" psql -U postgres -d "$DB" -Atc "select 1 from pg_namespace where nspname='$s'")" == "1" ]]; then
      docker exec "$c" find /mc-migrations -name '*.sql' -exec sed -i -E "/^[[:space:]]*CREATE SCHEMA IF NOT EXISTS $s;[[:space:]]*$/Id" {} +
    fi
  done
  docker exec "$c" psql "${args[@]}"
  docker exec "$c" rm -rf /mc-migrations
  log "applied ${#MIGRATIONS[@]} migrations in one transaction as role $role"
}

tables() { docker exec "$1" psql -U postgres -d "$DB" -Atc "select count(*) from pg_tables where schemaname='guild'"; }

verify_host_runtime() {  # container $1: after --host-runtime, every 002 and 003 object is there, or the run is refused
  local c="$1" q
  q="select (select count(*) from information_schema.columns where table_schema='guild' and table_name='mc_delegations'
       and column_name='queue_seq') = 1
     and to_regclass('guild.mc_delegation_events') is not null and to_regclass('guild.mc_delegation_reservations') is not null
     and to_regclass('guild.supervision_decision_messages') is not null and to_regclass('guild.supervision_contacts') is not null
     and to_regclass('guild.supervision_watch') is not null and to_regclass('guild.supervision_outbox') is not null"
  [[ "$(docker exec "$c" psql -U postgres -d "$DB" -Atc "$q")" == "t" ]] || die "host runtime objects missing after apply"
  log "host runtime: 002 and 003 objects present (mc_delegations has $(docker exec "$c" psql -U postgres -d "$DB" -Atc \
    "select count(*) from guild.mc_delegations") rows)"
}

timed_apply() {  # container $1: apply_all, timed (the rehearsal's measure of 002's rewrite on a production-shaped copy)
  local start=$SECONDS
  if [[ "$HOST_RUNTIME" == "1" ]]; then
    log "host runtime opt-in: ${#MIGRATIONS[@]} migrations, the last two dispatcher 002 then supervision 003"
  fi
  apply_all "$1"
  log "the one transaction took $((SECONDS - start)) s"
  if [[ "$HOST_RUNTIME" == "1" ]]; then verify_host_runtime "$1"; fi
}

case "${1:-}" in
  rehearse)
    R=mc-migrate-rehearsal
    docker rm -f "$R" >/dev/null 2>&1 || true
    image=$(docker inspect --format '{{.Config.Image}}' "$PG")
    docker run -d --name "$R" --network none -e POSTGRES_HOST_AUTH_METHOD=trust "$image" >/dev/null
    trap 'docker rm -f "$R" >/dev/null 2>&1 || true' EXIT
    for _ in $(seq 1 30); do docker exec "$R" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 2; done
    # Mirror prod's ownership exactly (database owned by postgres, objects by their real owners, grants restored): a
    # copy owned by the Guild role hid a missing database privilege on the first apply.
    for r in $(docker exec "$PG" psql -U postgres -Atc "select rolname from pg_roles where rolname !~ '^pg_' and rolname <> 'postgres'"); do
      docker exec "$R" psql -U postgres -qc "CREATE ROLE \"$r\"" >/dev/null 2>&1 || true
    done
    docker exec "$R" createdb -U postgres "$DB"
    docker exec "$PG" pg_dump -U postgres -Fc "$DB" | docker exec -i "$R" pg_restore -U postgres -d "$DB" 2>/dev/null || true
    log "copy owners: database $(docker exec "$R" psql -U postgres -Atc "select pg_get_userbyid(datdba) from pg_database where datname='$DB'"), CREATE for $(owner_of "$PG"): $(docker exec "$R" psql -U postgres -Atc "select has_database_privilege('$(owner_of "$PG")','$DB','CREATE')")"
    log "copy restored: $(tables "$R") guild tables (prod has $(tables "$PG"))"
    timed_apply "$R"
    log "after: $(tables "$R") guild tables"
    docker exec "$R" psql -U postgres -d "$DB" -Atc "select tablename from pg_tables where schemaname='guild' order by 1" | tr '\n' ' '; echo
    log "rehearsal copy deleted; prod untouched"
    ;;
  apply)
    install -d -m 0700 /opt/minimoi/backups
    b=/opt/minimoi/backups/mc-migrate-$(date -u +%Y%m%dT%H%M%SZ).dump
    ( umask 077; docker exec "$PG" pg_dump -U postgres -Fc "$DB" > "$b" )
    [[ -s "$b" ]] || die "backup is empty"
    log "backup $b ($(du -h "$b" | cut -f1))"
    timed_apply "$PG"
    log "prod now has $(tables "$PG") guild tables"
    ;;
  *) echo "usage: $0 rehearse|apply [--host-runtime] | version" >&2; exit 2;;
esac
