#!/bin/bash
# mc.sh — Master Craftsman's own Compose project on staging (MC spec v0.9 §3,
# stage A: no spend). The ONLY script that starts, stops or builds MC. It never
# touches the main project's containers (CoS Agent A, the gateway, the portal).
#
# Usage:
#   mc.sh token            write a random MC_OPENCLAW_GATEWAY_TOKEN into
#                          $STAGING_ROOT/mc.env (mode 600) if it has none; the
#                          value is never printed
#   mc.sh build [--allow-running]
#                          build minimoi-staging/mc-agent:<release tag> from the
#                          pinned release worktree (build.sh's RELEASE). Build
#                          BEFORE stopping anything, with MC stopped (BuildKit
#                          shares the VM's memory); --allow-running overrides
#   mc.sh up               start MC; needs state/mc.enabled, the image, mc.env
#                          with MC's own token, and the main stack's mc-net.
#                          Waits until CoS Agent A (if running) and the gateway
#                          are healthy first, so MC never starts beside another
#                          OpenClaw start on the 2-CPU VM
#   mc.sh down             stop and remove MC's container (volumes kept)
#   mc.sh status           MC's state, health, restarts and self-check state
#   mc.sh clear-selfcheck  remove a sticky .mc-selfcheck-failed from MC's state
#                          volume and start MC again (CoS is never involved)
#
# Stage C (Robert runs these two; never an agent):
#   mc.sh gateway-keys     the shared gateway's key database: a new database
#                          litellm_keys with its own role in the existing Postgres
#                          (random password, in gateway.env only, never printed);
#                          asks for MC's own Anthropic key (hidden input) for
#                          mc.env; turns state/gateway.keys on and recreates ONLY
#                          the model gateway (a CoS-touching step: CoS's calls
#                          fail for about 30 s; ask the CoS question after)
#   mc.sh key              MC's capped virtual key: asks for the monthly cap in
#                          dollars (default 15); makes the key inside the gateway
#                          (MC's route only, chat completions only, monthly
#                          budget, rpm 10); writes it to mc.env unprinted;
#                          recreates only mc-agent. Prints the cap, routes,
#                          models and the last 4 characters of the key's id.
#                          A re-run asks before rotating.
#
# MC's secrets live only in mc.env (mode 600), passed as an interpolation-only
# --env-file to MC's project; never in .env, which the main services load
# whole. Stage A: no MC_MODEL_GATEWAY_KEY, so MC gets a placeholder the gateway
# refuses (401). The script compares MC's key and token with CoS's values
# without printing any of them.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/mc_keys.sh"

MC_WAIT_S="${MC_WAIT_S:-420}"

mc_compose() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -v|--volumes|--volumes=*) die "refusing 'compose $*': MC's volumes are never removed by a script" ;;
    esac
  done
  require_absolute_root
  require_release
  [[ -f "$RELEASE_DIR/$STAGING_MC_FILE" ]] || die "the pinned release has no $STAGING_MC_FILE (it predates MC stage A)"
  local extra=()
  if [[ -f "$STAGING_MC_ENV" ]]; then
    [[ "$(file_mode "$STAGING_MC_ENV")" == 600 ]] || die "$STAGING_MC_ENV must be mode 600"
    extra=(--env-file "$STAGING_MC_ENV")
  fi
  docker compose -p "$STAGING_MC_PROJECT" --env-file "$STAGING_RELEASE_ENV" ${extra[@]+"${extra[@]}"} \
    -f "$RELEASE_DIR/$STAGING_MC_FILE" "$@"
}

# env_value FILE NAME: a variable's value from an env file, for comparison only.
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | sed "s/^'\\(.*\\)'\$/\\1/; s/^\"\\(.*\\)\"\$/\\1/"; }

# Refuses when MC's key or token equals one of CoS's (values never printed).
key_problems() {
  local mc_key mc_token relay_token cos_key cos_token problems=""
  mc_key=$(env_value "$STAGING_MC_ENV" MC_MODEL_GATEWAY_KEY)
  mc_token=$(env_value "$STAGING_MC_ENV" MC_OPENCLAW_GATEWAY_TOKEN)
  relay_token=$(env_value "$STAGING_MC_ENV" MC_RELAY_TOKEN)
  cos_key=$(env_value "$STAGING_ENV_FILE" MINIMOI_MODEL_GATEWAY_KEY)
  cos_token=$(env_value "$STAGING_ENV_FILE" COS_AGENT_A_GATEWAY_TOKEN)
  [[ -n "$mc_token" ]] || problems="$problems MC_OPENCLAW_GATEWAY_TOKEN is missing from mc.env (run: mc.sh token);"
  [[ -n "$relay_token" ]] || problems="$problems MC_RELAY_TOKEN is missing from mc.env (run: mc.sh token);"
  if [[ -n "$relay_token" && ( "$relay_token" == "$mc_token" || "$relay_token" == "$cos_token" || "$relay_token" == "$cos_key" ) ]]; then
    problems="$problems MC_RELAY_TOKEN equals another credential (MC's own token or one of CoS's);"
  fi
  if [[ -n "$mc_key" && ( "$mc_key" == "$cos_key" || "$mc_key" == "$cos_token" ) ]]; then
    problems="$problems MC_MODEL_GATEWAY_KEY equals one of CoS's credentials;"
  fi
  if [[ -n "$mc_token" && ( "$mc_token" == "$cos_token" || "$mc_token" == "$cos_key" ) ]]; then
    problems="$problems MC_OPENCLAW_GATEWAY_TOKEN equals one of CoS's credentials;"
  fi
  if grep -Eq '^(MC_MODEL_GATEWAY_KEY|MC_OPENCLAW_GATEWAY_TOKEN|MC_RELAY_TOKEN|MC_ANTHROPIC_API_KEY)=' "$STAGING_ENV_FILE" 2>/dev/null; then
    problems="$problems an MC secret name is in .env (the main services load it whole; use mc.env);"
  fi
  echo "${problems# }"
}

health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo absent; }

# Wait until NAME is healthy, or is not running at all (then nothing to wait for).
wait_healthy_or_absent() {
  local name="$1" i=0 h
  while [[ "$i" -lt "$MC_WAIT_S" ]]; do
    h=$(health_of "$name")
    case "$h" in healthy|absent|exited|created) return 0 ;; esac
    [[ "$i" -gt 0 ]] || note "waiting for $name ($h) before starting MC"
    sleep 5; i=$((i + 5))
  done
  die "$name is still '$h' after ${MC_WAIT_S}s; not starting MC beside it"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  token)
    require_absolute_root
    umask 077
    touch "$STAGING_MC_ENV"; chmod 600 "$STAGING_MC_ENV"
    for name in MC_OPENCLAW_GATEWAY_TOKEN MC_RELAY_TOKEN; do
      if grep -q "^$name=." "$STAGING_MC_ENV"; then
        note "mc.env already has $name (unchanged)"
      else
        printf '%s=%s\n' "$name" "$(openssl rand -hex 32)" >> "$STAGING_MC_ENV"
        note "wrote a new $name to $STAGING_MC_ENV (not printed)"
      fi
    done ;;
  build)
    require_absolute_root
    require_release
    if [[ "${1:-}" != --allow-running ]] \
       && [[ "$(docker inspect -f '{{.State.Running}}' "$STAGING_MC_CONTAINER" 2>/dev/null || echo false)" == true ]]; then
      die "MC is running; build with MC stopped (mc.sh down) or pass --allow-running"
    fi
    sha=$(release_sha); tag=$(release_tag)
    image="minimoi-staging/mc-agent:$tag"
    note "building $image from $RELEASE_DIR at ${sha:0:7}"
    docker build -f "$RELEASE_DIR/docker/Dockerfile.mc-agent" -t "$image" \
      --build-arg "MINIMOI_RELEASE_SHA=$sha" --label "minimoi.staging.release=$sha" "$RELEASE_DIR"
    note "built $image" ;;
  up)
    require_absolute_root
    require_release
    require_env
    mc_enabled || die "MC is not enabled on staging: touch $STAGING_MC_ENABLED first (stage A needs Robert's go-ahead)"
    docker image inspect "minimoi-staging/mc-agent:$(release_tag)" >/dev/null 2>&1 || die "no MC image for this release; run mc.sh build"
    for net in "$STAGING_MC_NET" "$STAGING_MC_FRONT"; do
      docker network inspect "$net" >/dev/null 2>&1 || die "network $net is missing; up.sh with a release that has it creates it"
    done
    problems=$(key_problems)
    [[ -z "$problems" ]] || die "$problems"
    for v in "${STAGING_MC_VOLUMES[@]}"; do
      docker volume inspect "$v" >/dev/null 2>&1 || { docker volume create "$v" >/dev/null; note "created volume $v"; }
    done
    wait_healthy_or_absent minimoi-model-gateway
    wait_healthy_or_absent minimoi-cos-agent-a
    mc_compose up -d --no-build
    note "MC starting (its self-check runs on loopback first; about 30-60 s, up to 10 min on a busy VM or after a hard kill). Then: mc.sh status" ;;
  down)
    mc_compose down
    note "MC down; volumes ${STAGING_MC_VOLUMES[*]} kept" ;;
  status)
    h=$(health_of "$STAGING_MC_CONTAINER")
    echo "mc-agent: $h, restarts $(docker inspect -f '{{.RestartCount}}' "$STAGING_MC_CONTAINER" 2>/dev/null || echo -)"
    echo "self-check: $(docker exec "$STAGING_MC_CONTAINER" cat /tmp/minimoi-mc/state 2>/dev/null || echo unknown)"
    echo "mc-relay: $(health_of minimoi-mc-relay)"
    echo "portal: MINIMOI_GUILD_MC=$(mc_mode), MINIMOI_GUILD_MC_TURNS=$(mc_turns) (state/mc.mode, state/mc.turns; up.sh applies them)"
    echo "enabled: $(mc_enabled && echo yes || echo no)" ;;
  clear-selfcheck)
    require_absolute_root
    require_release
    mc_compose stop >/dev/null 2>&1 || true
    docker run --rm --network none --user 0:0 -v "${STAGING_MC_VOLUMES[0]}:/state" --entrypoint rm \
      "minimoi-staging/mc-agent:$(release_tag)" -f /state/.mc-selfcheck-failed
    note "cleared MC's self-check marker; starting MC again"
    exec "$0" up ;;
  gateway-keys)
    require_absolute_root
    require_release
    require_env
    [[ -f "$RELEASE_DIR/$STAGING_KEYS_OVERLAY" ]] || die "the pinned release has no $STAGING_KEYS_OVERLAY (it predates stage C)"
    for c in postgres-ai-agents minimoi-model-gateway; do
      [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == true ]] || die "$c is not running"
    done
    umask 077
    touch "$STAGING_GATEWAY_ENV" "$STAGING_MC_ENV"
    chmod 600 "$STAGING_GATEWAY_ENV" "$STAGING_MC_ENV"
    if grep -q '^LITELLM_DATABASE_URL=.' "$STAGING_GATEWAY_ENV"; then
      note "gateway.env already has the key database URL (unchanged)"
    else
      pw=$(openssl rand -hex 24)
      keydb_sql "$pw" | docker exec -i postgres-ai-agents psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q >/dev/null \
        || die "could not create the key database (nothing written)"
      printf 'LITELLM_DATABASE_URL=%s\n' "$(keydb_url "$pw" postgres)" >> "$STAGING_GATEWAY_ENV"
      pw=""
      note "created role and database $KEYDB_NAME (random password in $STAGING_GATEWAY_ENV, not printed)"
    fi
    if grep -q '^MC_ANTHROPIC_API_KEY=.' "$STAGING_MC_ENV"; then
      note "mc.env already has MC's own Anthropic key (unchanged)"
    else
      printf "Paste Master Craftsman's OWN Anthropic API key (input hidden; Enter to skip for now): " >&2
      IFS= read -r -s akey || akey=""
      echo >&2
      if [[ -n "$akey" ]]; then
        [[ "$akey" != "$(env_value "$STAGING_ENV_FILE" ANTHROPIC_API_KEY)" ]] \
          || die "that is CoS's Anthropic key (ANTHROPIC_API_KEY); MC needs its own key with its own console limit"
        printf 'MC_ANTHROPIC_API_KEY=%s\n' "$akey" >> "$STAGING_MC_ENV"
        akey=""
        note "MC's Anthropic key written to mc.env (not printed)"
      else
        note "no Anthropic key entered: MC's route keeps its placeholder, so MC's turns stay refused"
      fi
    fi
    mkdir -p "$STAGING_ROOT/state"
    printf 'on\n' > "$STAGING_KEYS_FILE"
    note "recreating ONLY the model gateway with its key database (CoS's calls fail for about 30 s)"
    staging_compose up -d --no-build --no-deps model-gateway
    i=0
    until [[ "$(health_of minimoi-model-gateway)" == healthy ]]; do
      i=$((i + 5)); [[ "$i" -lt 240 ]] || die "the gateway is not healthy after 240 s (docker logs minimoi-model-gateway)"
      sleep 5
    done
    tables=$(docker exec postgres-ai-agents psql -U postgres -d "$KEYDB_NAME" -tAc \
      "select count(*) from information_schema.tables where table_name = 'LiteLLM_VerificationToken'" 2>/dev/null || echo 0)
    [[ "$tables" == 1 ]] && note "gateway healthy; its key tables exist. Next: the CoS question, then mc.sh key" \
      || die "the gateway is healthy but its key tables are missing"
    ;;
  key)
    require_absolute_root
    require_release
    require_env
    mc_enabled || die "MC is not enabled (state/mc.enabled)"
    gateway_keys_on || die "the gateway has no key database yet: run mc.sh gateway-keys first"
    [[ "$(health_of minimoi-model-gateway)" == healthy ]] || die "the model gateway is not healthy"
    touch "$STAGING_MC_ENV"; chmod 600 "$STAGING_MC_ENV"
    old=$(env_value "$STAGING_MC_ENV" MC_MODEL_GATEWAY_KEY)
    if [[ -n "$old" ]]; then
      printf 'MC already has a key. Rotate it (the old key stops working)? [y/N] ' >&2
      IFS= read -r answer || answer=""
      [[ "$answer" == y || "$answer" == Y ]] || { note "kept the existing key"; exit 0; }
    fi
    printf "Master Craftsman's monthly cap in dollars [15]: " >&2
    IFS= read -r cap || cap=""
    cap=${cap:-15}
    valid_cap "$cap" || die "'$cap' is not a dollar amount between 0 and 500"
    out=$(mc_keygen_py | MC_OLD_KEY="$old" MC_CAP="$cap" docker exec -i -e MC_OLD_KEY -e MC_CAP minimoi-model-gateway python -) \
      || die "the gateway did not make the key (nothing written)"
    key=$(sed -n 1p <<< "$out")
    last4=$(sed -n 2p <<< "$out")
    out=""
    [[ "$key" == sk-* ]] || die "the gateway's answer was not a key (nothing written)"
    tmp="$STAGING_MC_ENV.tmp.$$"
    { grep -v '^MC_MODEL_GATEWAY_KEY=' "$STAGING_MC_ENV" || true; printf 'MC_MODEL_GATEWAY_KEY=%s\n' "$key"; } > "$tmp"
    chmod 600 "$tmp"; mv -f "$tmp" "$STAGING_MC_ENV"
    key=""
    problems=$(key_problems)
    [[ -z "$problems" ]] || die "$problems"
    note "recreating only mc-agent with its key (the relay and CoS are untouched)"
    mc_compose up -d --no-build
    echo "MC's key: monthly cap \$$cap ($MC_KEY_BUDGET_DURATION), models $MC_KEY_MODELS, routes $MC_KEY_ROUTES, rpm $MC_KEY_RPM, key id …$last4"
    ;;
  *) die "usage: mc.sh token | build [--allow-running] | up | down | status | clear-selfcheck | gateway-keys | key" ;;
esac
