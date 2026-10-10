#!/bin/bash
# Portal-only switches for #292. Never rebuilds, starts dependencies or touches production.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
case "${1:-}" in
  jobs) flag="$STAGING_JOBS_FILE"; overlay=docker-compose.staging-jobs.yml; folder=jobs ;;
  mc-capture) flag="$STAGING_MC_CAPTURE_FILE"; overlay=docker-compose.staging-mc-turns.yml; folder=mc-turns ;;
  *) die "usage: portal-memory.sh jobs|mc-capture on|off|status" ;;
esac
case "${2:-}" in
  status) if [[ -f "$flag" && "$(tr -d '[:space:]' < "$flag")" == on ]]; then echo "$1: on (configured)"; else echo "$1: off"; fi; exit 0 ;;
  on|off) ;;
  *) die "usage: portal-memory.sh jobs|mc-capture on|off|status" ;;
esac
require_absolute_root
require_release
if [[ "$2" == on ]]; then
  [[ -f "$STAGING_PORTAL_RELEASE_DIR/$overlay" ]] || die "pinned release lacks $overlay; integrate it before activation"
  mkdir -p "$STAGING_ROOT/data/$folder"
  chmod 700 "$STAGING_ROOT/data/$folder"
fi
# An active portal override must be honored; refuse rather than silently downgrade it.
if [[ -f "$STAGING_ROOT/state/GUILD_DEV_RELEASE" ]]; then
  [[ -x "$STAGING_ROOT/state/guild-portal-up.sh" ]] || die "portal override has no recreate wrapper"
fi
mkdir -p "$STAGING_ROOT/state"
previous=off
[[ ! -f "$flag" ]] || previous=$(cat "$flag")
printf '%s\n' "$2" > "$flag"
recreate() {
  if [[ -f "$STAGING_ROOT/state/GUILD_DEV_RELEASE" ]]; then
    "$STAGING_ROOT/state/guild-portal-up.sh"
  else
    staging_compose up -d --no-deps --no-build --pull never portal
  fi
}
if ! ( recreate ); then
  printf '%s\n' "$previous" > "$flag"
  echo "Portal recreate failed; configuration restored. Verify portal state before retrying." >&2
  exit 1
fi
echo "$1: $2 (portal recreated; no dependencies changed)"
