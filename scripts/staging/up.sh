#!/bin/bash
# up.sh — start (or update to the pinned release) the staging stack.
#
# Usage: up.sh [--allow-holder PORT[,PORT...]] [service ...]
#
# Runs `compose up -d --no-build --remove-orphans` with the local images from
# build.sh. The two Telegram bots (profile "bots") start only when
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
for f in data/curator_history.json data/curator_costs.json auth/users.json auth/guests.json \
         cos_memory.md data/model_gateway_receipts.jsonl data/guild/cos_context.json \
         data/guild/build_queue.json config/litellm.staging.yaml; do
  [[ -f "$S/$f" ]] || die "missing $S/$f; run seed.sh (and build.sh for config/)"
done
for d in data/curator data/curator_archive data/interests data/research-intelligence data/german \
         data/portuguese data/guild docs/design docs/specs agent_logs; do
  [[ -d "$S/$d" ]] || die "missing folder $S/$d; run seed.sh"
done
for v in "${STAGING_VOLUMES[@]}"; do
  docker volume inspect "$v" >/dev/null 2>&1 || die "missing volume $v; run seed.sh --postgres (README step 1b)"
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

if bots_enabled; then
  staging_compose --profile bots up -d --no-build --remove-orphans "$@"
else
  staging_compose up -d --no-build --remove-orphans "$@"
  staging_compose --profile bots stop system-bot cos-bot >/dev/null 2>&1 || true
fi
note "up. Next: scripts/staging/status.sh and scripts/staging/verify.sh"
