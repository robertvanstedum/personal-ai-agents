#!/bin/bash
# focus.sh — run only the staging containers under test (8 GB Mac; Robert,
# 2026-09-28). The set is persisted, so up.sh and verify.sh respect it.
#
# Usage: focus.sh mc | mc+cos | all | show
#
#   mc       keep postgres, model-gateway, portal (plus Master Craftsman's own
#            project, which mc.sh runs); stop everything else
#   mc+cos   the same plus cos-agent-a and cos-scheduler (CoS regression checks)
#   all      clear the focus and bring the whole stack back through up.sh
#            (the bots follow state/bots.on as usual)
#   show     print the current set and the services it keeps stopped
#
# Stopping uses down.sh <service> (containers stop; nothing is removed; volumes
# are never touched). Only the staging project is ever addressed (lib.sh).
# Writes state/focus (the set's name) and state/focus.stopped (the services
# kept stopped). Master Craftsman's own project is not touched: use mc.sh.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

keep_for() {
  case "$1" in
    mc) echo "postgres model-gateway portal" ;;
    mc+cos) echo "postgres model-gateway portal cos-agent-a cos-scheduler" ;;
    *) return 1 ;;
  esac
}

set_name="${1:-}"
case "$set_name" in
  show)
    echo "focus: $(focus_name)"
    echo "kept stopped: $(focus_stopped)"
    exit 0 ;;
  all)
    require_absolute_root
    rm -f "$STAGING_FOCUS_FILE" "$STAGING_FOCUS_STOPPED"
    note "focus cleared; bringing the whole stack back (up.sh)"
    exec "$STAGING_SCRIPTS_DIR/up.sh" "${@:2}" ;;
  mc|mc+cos) ;;
  *) die "usage: focus.sh mc | mc+cos | all | show" ;;
esac

require_absolute_root
require_release
require_env
keep=$(keep_for "$set_name")
stop=""
for service in $STAGING_CORE_SERVICES $STAGING_BOT_SERVICES; do
  [[ " $keep " == *" $service "* ]] || stop="$stop $service"
done
stop="${stop# }"

# Refuse to act on containers of another project that happen to use a staging name.
for service in $stop; do
  name=$(container_of "$service")
  project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null || true)
  if docker inspect "$name" >/dev/null 2>&1 && [[ "$project" != "$STAGING_PROJECT" ]]; then
    die "container $name belongs to project '${project:-none}', not $STAGING_PROJECT; refusing"
  fi
done

mkdir -p "$STAGING_ROOT/state"
printf '%s\n' "$set_name" > "$STAGING_FOCUS_FILE"
printf '%s\n' "$stop" > "$STAGING_FOCUS_STOPPED"
note "focus $set_name: stopping $stop (volumes and files untouched)"
"$STAGING_SCRIPTS_DIR/down.sh" $stop
note "focus $set_name: running $keep. Back to everything: scripts/staging/focus.sh all"
