#!/bin/bash
# Bring the production database up to Master Craftsman's schema (the Guild, actions, domain-action, MC tools and
# delegation migrations dev already has). Runbook: docs/runbooks/mc_container_prod.md.
#
#   mc_prod_db_migrate.sh rehearse   copy prod's database into a throwaway Postgres (no network, same host), apply every
#                                    migration there in one transaction, list the result, delete the copy. Prod untouched.
#   mc_prod_db_migrate.sh apply      back up prod's database (pg_dump, root 0600 under /opt/minimoi/backups), then apply
#                                    every migration in ONE transaction: all or nothing.
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
  core/mc_delegation_dispatcher/sql/001_delegations.sql
)

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
  docker exec "$c" psql "${args[@]}"
  docker exec "$c" rm -rf /mc-migrations
  log "applied ${#MIGRATIONS[@]} migrations in one transaction as role $role"
}

tables() { docker exec "$1" psql -U postgres -d "$DB" -Atc "select count(*) from pg_tables where schemaname='guild'"; }

case "${1:-}" in
  rehearse)
    R=mc-migrate-rehearsal
    docker rm -f "$R" >/dev/null 2>&1 || true
    image=$(docker inspect --format '{{.Config.Image}}' "$PG")
    docker run -d --name "$R" --network none -e POSTGRES_HOST_AUTH_METHOD=trust "$image" >/dev/null
    trap 'docker rm -f "$R" >/dev/null 2>&1 || true' EXIT
    for _ in $(seq 1 30); do docker exec "$R" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 2; done
    role=$(owner_of "$PG")
    docker exec "$R" psql -U postgres -qc "CREATE ROLE $role LOGIN" >/dev/null
    docker exec "$R" createdb -U postgres -O "$role" "$DB"
    docker exec "$PG" pg_dump -U postgres -Fc "$DB" | docker exec -i "$R" pg_restore -U postgres -d "$DB" --no-owner --role="$role" 2>/dev/null || true
    log "copy restored: $(tables "$R") guild tables (prod has $(tables "$PG"))"
    apply_all "$R"
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
    apply_all "$PG"
    log "prod now has $(tables "$PG") guild tables"
    ;;
  *) echo "usage: $0 rehearse|apply" >&2; exit 2;;
esac
