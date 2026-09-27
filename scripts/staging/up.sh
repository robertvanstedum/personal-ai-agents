#!/bin/bash
# up.sh — start (or update to the pinned release) the staging stack.
#
# Usage: up.sh [service ...]
#
# Runs `compose up -d --no-build --remove-orphans` with the local images from
# build.sh. The two Telegram bots (profile "bots") start only when
# $STAGING_ROOT/state/bots.on exists and their native launchd pollers are
# gone (one poller per token); otherwise any running bot container is stopped.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
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
  if launchctl list 2>/dev/null | grep -Eq 'com\.vanstedum\.(cos-bot|system-bot)$'; then
    die "bots.on is set but a native test-bot poller is still loaded; boot it out first (README step 7a/7b)"
  fi
  note "bots profile ON"
else
  note "bots profile off (touch $STAGING_BOTS_FLAG to enable after the native pollers are retired)"
fi

if bots_enabled; then
  staging_compose --profile bots up -d --no-build --remove-orphans "$@"
else
  staging_compose up -d --no-build --remove-orphans "$@"
  staging_compose --profile bots stop system-bot cos-bot >/dev/null 2>&1 || true
fi
note "up. Next: scripts/staging/status.sh and scripts/staging/verify.sh"
