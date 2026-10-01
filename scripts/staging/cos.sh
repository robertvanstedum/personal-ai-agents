#!/bin/bash
# cos.sh — CoS's own capped gateway key on staging (Robert, September 28 2026:
# CoS gets its own capped key too, instead of the master key). Robert runs
# `cos.sh key` himself; an agent never does.
#
# Usage:
#   cos.sh key      CoS's capped virtual key: asks for the monthly cap in dollars
#                   (default 30); makes the key inside the gateway (CoS's four
#                   routes only; chat completions and responses only; monthly
#                   budget; rpm 60); writes it to cos.env (600) unprinted; turns
#                   state/cos.key on and recreates ONLY cos-agent-a, which then
#                   uses that key instead of the master key. Prints the cap,
#                   period, models, routes, rpm and the last 4 characters of the
#                   key's id. A re-run asks before rotating. Needs the gateway's
#                   key database (mc.sh gateway-keys) and reuses staging's
#                   existing provider keys (nothing new from a provider).
#   cos.sh off      back to the master key: state/cos.key off, recreate only
#                   cos-agent-a. The key stays in cos.env and the gateway.
#   cos.sh status   which key CoS uses (never the value), whether the CoS turn
#                   log is on, and Agent A's health
#   cos.sh turns on|off
#                   the CoS turn log (Spec 160 path (a); Confer voice
#                   transcripts): state/cos.turns on or off, then recreates
#                   ONLY cos-scheduler with or without its data/cos-turns mount.
#                   Off by default. Needs no rebuild. Off keeps the lines
#                   already written.
#
# With its own key, CoS depends on the gateway's key database: with Postgres
# down, CoS's key is refused after the gateway's 60 s key cache (MC stage C
# probe). `cos.sh off` is the quick way back.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/mc_keys.sh"

COS_CONTAINER=minimoi-cos-agent-a
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | sed "s/^'\\(.*\\)'\$/\\1/; s/^\"\\(.*\\)\"\$/\\1/"; }
health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo absent; }

cos_key_problems() {
  local key master mc_key problems=""
  key=$(env_value "$STAGING_COS_ENV" COS_MODEL_GATEWAY_KEY)
  master=$(env_value "$STAGING_ENV_FILE" MINIMOI_MODEL_GATEWAY_KEY)
  mc_key=$(env_value "$STAGING_MC_ENV" MC_MODEL_GATEWAY_KEY)
  [[ -n "$key" ]] || problems="$problems COS_MODEL_GATEWAY_KEY is missing from cos.env;"
  [[ -n "$key" && "$key" == "$master" ]] && problems="$problems COS_MODEL_GATEWAY_KEY equals the master key;"
  [[ -n "$key" && -n "$mc_key" && "$key" == "$mc_key" ]] && problems="$problems COS_MODEL_GATEWAY_KEY equals MC's key;"
  if grep -Eq '^COS_MODEL_GATEWAY_KEY=' "$STAGING_ENV_FILE" 2>/dev/null; then
    problems="$problems COS_MODEL_GATEWAY_KEY is in .env (every main service loads it; use cos.env);"
  fi
  echo "${problems# }"
}

recreate_cos() {
  note "recreating ONLY cos-agent-a (CoS's turns fail for about a minute; ask the CoS question after)"
  staging_compose up -d --no-build --no-deps cos-agent-a
  local i=0
  until [[ "$(health_of "$COS_CONTAINER")" == healthy ]]; do
    i=$((i + 5)); [[ "$i" -lt 360 ]] || die "cos-agent-a is not healthy after 360 s (docker logs $COS_CONTAINER)"
    sleep 5
  done
}

case "${1:-}" in
  key)
    require_absolute_root
    require_release
    require_env
    [[ -f "$RELEASE_DIR/$STAGING_COS_KEY_OVERLAY" ]] || die "the pinned release has no $STAGING_COS_KEY_OVERLAY"
    gateway_keys_on || die "the gateway has no key database yet: run mc.sh gateway-keys first"
    [[ "$(health_of minimoi-model-gateway)" == healthy ]] || die "the model gateway is not healthy"
    umask 077                                   # the key's temp file is never world-readable, even briefly
    touch "$STAGING_COS_ENV"; chmod 600 "$STAGING_COS_ENV"
    old=$(env_value "$STAGING_COS_ENV" COS_MODEL_GATEWAY_KEY)
    if [[ -n "$old" ]]; then
      printf 'CoS already has its own key. Rotate it (the old key stops working)? [y/N] ' >&2
      IFS= read -r answer || answer=""
      [[ "$answer" == y || "$answer" == Y ]] || { note "kept the existing key"; exit 0; }
    fi
    printf "CoS's monthly cap in dollars [30]: " >&2
    IFS= read -r cap || cap=""
    cap=${cap:-30}
    valid_cap "$cap" || die "'$cap' is not a dollar amount between 0 and 500"
    out=$(cos_keygen_py | COS_OLD_KEY="$old" COS_CAP="$cap" docker exec -i -e COS_OLD_KEY -e COS_CAP minimoi-model-gateway python -) \
      || die "the gateway did not make the key (nothing written)"
    key=$(sed -n 1p <<< "$out")
    last4=$(sed -n 2p <<< "$out")
    out=""
    [[ "$key" == sk-* ]] || die "the gateway's answer was not a key (nothing written)"
    tmp="$STAGING_COS_ENV.tmp.$$"
    { grep -v '^COS_MODEL_GATEWAY_KEY=' "$STAGING_COS_ENV" || true; printf 'COS_MODEL_GATEWAY_KEY=%s\n' "$key"; } > "$tmp"
    chmod 600 "$tmp"; mv -f "$tmp" "$STAGING_COS_ENV"
    key=""
    problems=$(cos_key_problems)
    [[ -z "$problems" ]] || die "$problems"
    mkdir -p "$STAGING_ROOT/state"
    printf 'on\n' > "$STAGING_COS_KEY_FILE"
    recreate_cos
    echo "CoS's key: monthly cap \$$cap ($COS_KEY_BUDGET_DURATION), models $COS_KEY_MODELS, routes $COS_KEY_ROUTES, rpm $COS_KEY_RPM, key id …$last4"
    note "next: verify.sh, then the CoS regression question (a cited answer and a new usage line with \"key_ref\":\"cos-agent-…\")"
    ;;
  off)
    require_absolute_root
    require_release
    require_env
    mkdir -p "$STAGING_ROOT/state"
    printf 'off\n' > "$STAGING_COS_KEY_FILE"
    recreate_cos
    note "CoS uses the master key again (its own key is kept in cos.env and the gateway)"
    ;;
  turns)
    require_absolute_root
    require_release
    require_env
    case "${2:-}" in
      on)
        [[ -f "$RELEASE_DIR/$STAGING_COS_TURNS_OVERLAY" ]] || die "the pinned release has no $STAGING_COS_TURNS_OVERLAY"
        [[ -d "$STAGING_ROOT/data/cos-turns" ]] || die "missing $STAGING_ROOT/data/cos-turns; run build.sh"
        value=on ;;
      off) value=off ;;
      *) die "usage: cos.sh turns on|off" ;;
    esac
    mkdir -p "$STAGING_ROOT/state"
    printf '%s\n' "$value" > "$STAGING_COS_TURNS_FILE"
    note "recreating ONLY cos-scheduler (Confer is unavailable for a few seconds)"
    staging_compose up -d --no-build --no-deps cos-scheduler
    if [[ "$value" == on ]]; then
      note "the CoS turn log is on: Confer voice transcripts are kept unless Private is on (Confer's Private switch)"
    else
      note "the CoS turn log is off: nothing new is kept; the lines already written stay"
    fi
    ;;
  status)
    if cos_turns_on; then echo "CoS turn log: on (state/cos.turns)"; else echo "CoS turn log: off"; fi
    if cos_key_on; then
      [[ -n "$(env_value "$STAGING_COS_ENV" COS_MODEL_GATEWAY_KEY)" ]] && echo "CoS key: its own capped key (cos.env)" \
        || echo "CoS key: state/cos.key is on but cos.env has no key (up.sh will refuse)"
    else
      echo "CoS key: the gateway's master key (state/cos.key off)"
    fi
    echo "cos-agent-a: $(health_of "$COS_CONTAINER")"
    ;;
  *) die "usage: cos.sh key | off | status | turns on|off" ;;
esac
