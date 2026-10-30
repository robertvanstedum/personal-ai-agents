#!/bin/bash
# up.sh — start (or update to the pinned release) the staging stack.
#
# Usage: up.sh [--allow-holder PORT[,PORT...]] [service ...]
#
# Runs `compose up -d --no-build --remove-orphans` with the local images from
# build.sh. With a focus set (focus.sh; state/focus.stopped), only the kept
# services start, with --no-deps, and the stopped ones stay stopped. The two Telegram bots (profile "bots") start only when
# $STAGING_ROOT/state/bots.on exists, their native launchd pollers are gone
# (one poller per token), and env.sh wrote their Keychain test tokens;
# otherwise any running bot container is stopped.
#
# Before compose runs, every staging host port ($STAGING_HOST_PORTS) must be
# free or held only through a staging container. Anything else (a native
# process, another project's container, a stale forward) refuses, naming the
# holder: Colima's forwarder skips a busy port and never retries, so the stack
# would come up without serving it. --allow-holder accepts a named holder on
# the listed ports only (cutover step 5: 5001, 8767, 8770 until steps 6/7c/7d).

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ALLOW=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-holder) ALLOW="$ALLOW,${2:?--allow-holder needs PORT[,PORT...]}"; shift 2 ;;
    --allow-holder=*) ALLOW="$ALLOW,${1#--allow-holder=}"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    --) shift; break ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
check_allow_list "$ALLOW"

require_absolute_root
require_release
require_env
S="$STAGING_ROOT"

# A missing single-file bind source would make Docker create a DIRECTORY in
# its place, so every one must exist before compose runs.
for f in auth/users.json auth/guests.json auth/pending.json auth/reset_requests.json \
         cos_memory.md data/model_gateway_receipts.jsonl data/guild/cos_context.json \
         data/guild/build_queue.json config/litellm.staging.yaml \
         config/usage/usage_record.py config/usage/litellm_recorder.py; do
  [[ -f "$S/$f" ]] || die "missing $S/$f; run seed.sh (and build.sh for config/)"
done
for d in data/german \
         data/portuguese data/guild data/usage data/usage/portal data/workshops docs/design docs/specs agent_logs; do
  [[ -d "$S/$d" ]] || die "missing folder $S/$d; run seed.sh"
done

# Curator's own bind sources: the bind rows of scripts/operations/curator_state.map,
# mounted by no other service (tests/test_staging_environment.py checks both).
# They are checked only when curator will start. A focus set that keeps curator
# stopped starts the kept services with --no-deps, so nothing mounts them, and a
# missing Curator source must not block German, Portuguese or the portal (#305).
# Without a focus set, compose may start curator directly or as the portal's
# dependency, so they are always checked.
CURATOR_BIND_FILES="data/curator_history.json data/curator_costs.json data/research-config/config.json data/curator-config/curator_sources.json"
CURATOR_BIND_DIRS="data/curator data/curator_archive data/interests data/research-intelligence data/annotations data/research-library data/curator_cache"
curator_will_start() {
  [[ -n "$(focus_stopped)" ]] || return 0
  if [[ $# -gt 0 ]]; then
    local service
    for service in "$@"; do [[ "$service" != curator ]] || return 0; done
    return 1
  fi
  ! is_focus_stopped curator
}
if curator_will_start "$@"; then
  for f in $CURATOR_BIND_FILES; do
    [[ -f "$S/$f" ]] || die "missing $S/$f; run seed.sh"
  done
  for d in $CURATOR_BIND_DIRS; do
    [[ -d "$S/$d" ]] || die "missing folder $S/$d; run seed.sh"
  done
else
  note "curator stays stopped; its bind sources are not checked (they are checked whenever curator starts)"
fi
for v in "${STAGING_VOLUMES[@]}"; do
  docker volume inspect "$v" >/dev/null 2>&1 || die "missing volume $v; run seed.sh --postgres (README step 1b)"
done
# Each root writer's own usage folder (build.sh makes them).
for w in cos-bot cos-scheduler; do
  [[ -d "$S/data/usage/$w" ]] || die "missing folder $S/data/usage/$w; run build.sh"
done

# Staging keeps production's container names; an old dev container of the
# same name must be renamed first (README step 1).
names=("${STAGING_CORE_CONTAINERS[@]}")
bots_enabled && names+=("${STAGING_BOT_CONTAINERS[@]}")
for name in "${names[@]}"; do
  project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$name" 2>/dev/null || true)
  if docker inspect "$name" >/dev/null 2>&1 && [[ "$project" != "$STAGING_PROJECT" ]]; then
    die "container $name belongs to project '${project:-none}'; rename it to $name-pre-staging first (README step 1)"
  fi
done

if bots_enabled; then
  # Capture first, then match: `launchctl list | grep -q` under pipefail
  # returns 141 (SIGPIPE) whenever grep matches before launchctl finishes,
  # which made this guard pass while a native poller was loaded.
  if ! jobs=$(launchctl list 2>/dev/null); then
    die "bots.on is set but 'launchctl list' failed; refusing to start the bots"
  fi
  if grep -Eq 'com\.vanstedum\.(cos-bot|system-bot)$' <<< "$jobs"; then
    die "bots.on is set but a native test-bot poller is still loaded; boot it out first (README step 7a/7b)"
  fi
  problems=$(bot_token_problems)
  [[ -z "$problems" ]] || die "bots.on is set but the bot tokens are not ready: $problems"
  note "bots profile ON"
else
  note "bots profile off (touch $STAGING_BOTS_FLAG and rerun env.sh --force to enable, after the native pollers are retired)"
fi

refused=""
for port in $STAGING_HOST_PORTS; do
  holder=$(foreign_port_holder "$port")
  [[ -n "$holder" ]] || continue
  if port_allowed "$port" "$ALLOW"; then
    note "port $port is held by $holder (allowed by --allow-holder; it will not forward until that holder is retired and the staging container restarted)"
  else
    refused="$refused
  $port: $holder"
  fi
done
if [[ -n "$refused" ]]; then
  die "these staging host ports are held by something other than a staging container:$refused
retire the holder, or pass --allow-holder PORT for a native holder the runbook retires later"
fi

# Focus (focus.sh): start only the kept services, without their dependencies
# (the portal's depends_on would otherwise start curator, German, Portuguese
# and cos-scheduler), and keep the stopped ones stopped.
if [[ -n "$(focus_stopped)" ]]; then
  for service in "$@"; do
    ! is_focus_stopped "$service" || die "$service is kept stopped by focus '$(focus_name)'; run scripts/staging/focus.sh all first"
  done
  kept=()
  for service in $STAGING_CORE_SERVICES; do
    is_focus_stopped "$service" || kept+=("$service")
  done
  [[ $# -gt 0 ]] && kept=("$@")
  note "focus '$(focus_name)': starting ${kept[*]} only; kept stopped: $(focus_stopped)"
  staging_compose up -d --no-build --no-deps "${kept[@]}"
  # shellcheck disable=SC2046
  staging_compose --profile bots stop $(focus_stopped) >/dev/null 2>&1 || true
elif bots_enabled; then
  staging_compose --profile bots up -d --no-build --remove-orphans "$@"
else
  staging_compose up -d --no-build --remove-orphans "$@"
  staging_compose --profile bots stop system-bot cos-bot >/dev/null 2>&1 || true
fi
note "up. Next: scripts/staging/status.sh and scripts/staging/verify.sh"
