#!/bin/sh
# Start Master Craftsman's own OpenClaw container (MC spec v0.8 §2.1, v0.9 §5).
# Nothing here knows about CoS Agent A: MC's failures stay inside MC.
#
# Order, on every start:
#   0. a sticky .mc-selfcheck-failed on the state volume: never start OpenClaw;
#      stay up and unhealthy with no restart loop until Robert runs
#      `scripts/staging/mc.sh clear-selfcheck`;
#   1. optional start delay (MC_START_DELAY_S; staggers MC behind other
#      runtimes on a small VM);
#   2. key check (MC's key set, never its own OpenClaw token), apply the
#      image's config and workspace, static check of the config;
#   3. CHECK phase: OpenClaw on --bind loopback only, so nothing on the network
#      reaches an unchecked MC; the runtime check (effective tools, the
#      plugins actually loaded, scheduler, pairing); the config must not have
#      been rewritten;
#   4. SERVE: the checked config on the LAN bind; /tmp/minimoi-mc/serving marks
#      this start as checked (the healthcheck requires it).
# A VERDICT (a wrong value, or the gateway refusing a request) writes the
# sticky marker and stays down (step 0 on the next start too). INCONCLUSIVE (a
# timeout, a gateway that exits or is not ready while starting, e.g. under CPU
# contention) exits 1, and `restart: on-failure:3` bounds the retries.
set -eu

IMAGE_DIR="${MINIMOI_MC_IMAGE_DIR:-/opt/minimoi/mc-agent}"
STATE_DIR="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"
CONFIG_DEST="${OPENCLAW_CONFIG_PATH:-$STATE_DIR/openclaw.json}"
CONFIG_SRC="$IMAGE_DIR/openclaw.json"
WORKSPACE_SRC="$IMAGE_DIR/workspace"
WORKSPACE="$STATE_DIR/workspace-mc"
SELFCHECK="$IMAGE_DIR/selfcheck.mjs"
KEY_CHECK="$IMAGE_DIR/mc-key-check.sh"
MC_FAILED="$STATE_DIR/.mc-selfcheck-failed"
RUN_DIR="${MINIMOI_MC_RUN_DIR:-/tmp/minimoi-mc}"
PORT="${MINIMOI_MC_PORT:-18789}"
READY_WAIT_S="${MINIMOI_MC_READY_WAIT_S:-300}"
START_DELAY_S="${MC_START_DELAY_S:-0}"
START_ATTEMPTS="${MINIMOI_MC_START_ATTEMPTS:-10}"
RETRY_PAUSE_S="${MINIMOI_MC_RETRY_PAUSE_S:-30}"
OPENCLAW="${MINIMOI_MC_OPENCLAW:-node /app/openclaw.mjs}"
RELEASE="${MINIMOI_RELEASE_SHA:-unknown}"

log() { echo "mc-agent[start-mc]: $*" >&2; }
loud() { log "********** $* **********"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
state() { echo "$1" > "$RUN_DIR/state"; }
printf 'started_at=%s\nrelease=%s\n' "$(now)" "$RELEASE" > "$RUN_DIR/start"

stay_down() {
  state selfcheck-failed
  loud "Master Craftsman self-check FAILED ($MC_FAILED). OpenClaw is not started; MC is unavailable. Fix the cause, then: scripts/staging/mc.sh clear-selfcheck"
  sed 's/^/  /' "$MC_FAILED" >&2 || true
  while :; do sleep 3600; done
}
[ -f "$MC_FAILED" ] && stay_down

verdict() {  # verdict REASON-FILE-OR-TEXT
  {
    echo "failed_at=$(now)"
    echo "release=$RELEASE"
    if [ -f "$1" ]; then echo "result=$(tr -d '\n' < "$1")"; else echo "result=$1"; fi
  } > "$MC_FAILED"
  stay_down
}

inconclusive() {
  state check-inconclusive
  loud "the self-check could not complete ($1); nothing is served; exiting (restart policy on-failure:3 retries)"
  exit 1
}

GW_PID=""
GW_STATUS=0
stop_gateway() {
  [ -n "$GW_PID" ] || return 0
  kill -TERM "$GW_PID" 2>/dev/null || true
  i=0
  while kill -0 "$GW_PID" 2>/dev/null; do
    i=$((i + 1))
    [ "$i" -le 60 ] || kill -KILL "$GW_PID" 2>/dev/null || true
    sleep 1
  done
  GW_STATUS=0
  wait "$GW_PID" 2>/dev/null || GW_STATUS=$?
  GW_PID=""
}
on_signal() { log "signal received; stopping OpenClaw"; stop_gateway; exit "$GW_STATUS"; }
trap on_signal TERM INT

# 0 ready; 1 the gateway exited; 2 not ready within READY_WAIT_S.
wait_ready() {
  i=0
  while [ "$i" -lt "$READY_WAIT_S" ]; do
    kill -0 "$GW_PID" 2>/dev/null || return 1
    if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/readyz" 2>/dev/null; then return 0; fi
    i=$((i + 2))
    sleep 2
  done
  return 2
}

apply_file() {
  src="$1"; dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then cp -p "$dest" "$dest.replaced-by-image"; fi
  tmp="$dest.image-apply.$$"
  cp "$src" "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$dest"
}

# check PHASE [CONFIG]: 0 pass; a verdict or inconclusive never returns.
check() {
  code=0
  node "$SELFCHECK" "$@" > "$RUN_DIR/selfcheck-$1.json" || code=$?
  cat "$RUN_DIR/selfcheck-$1.json" >&2 || true
  case "$code" in
    0) return 0 ;;
    4) stop_gateway; inconclusive "$1 check" ;;
    *) stop_gateway; verdict "$RUN_DIR/selfcheck-$1.json" ;;
  esac
}

if [ "$START_DELAY_S" -gt 0 ] 2>/dev/null; then
  state delaying
  log "start delay ${START_DELAY_S}s (staggered behind other runtimes)"
  sleep "$START_DELAY_S"
fi

state checking
if ! out=$(sh "$KEY_CHECK" OPENCLAW_GATEWAY_TOKEN 2>&1); then verdict "key check: $out"; fi
apply_file "$CONFIG_SRC" "$CONFIG_DEST"
mkdir -p "$WORKSPACE"
for f in "$WORKSPACE_SRC"/*.md; do apply_file "$f" "$WORKSPACE/$(basename "$f")"; done
check static "$CONFIG_DEST"

# A gateway that exits while starting is retried here, a bounded number of
# times: after a hard kill OpenClaw's owner lease on the state directory stays
# active for a few minutes ("Another Gateway owner lease is still active"), and
# on the 2-CPU VM a start beside another OpenClaw start can miss its start-up
# lease deadline. Both clear on their own. A gateway killed by a signal (an
# OOM kill) is not retried here. Still failing after the retries:
# inconclusive (exit 1, and on-failure:3 retries the whole start).
attempt=1
while :; do
  log "CHECK phase (attempt $attempt of $START_ATTEMPTS): OpenClaw on loopback only; nothing on the network reaches MC until the check passes"
  $OPENCLAW gateway --bind loopback &
  GW_PID=$!
  ready=0
  wait_ready || ready=$?
  [ "$ready" -ne 0 ] || break
  stop_gateway
  if [ "$ready" -eq 2 ]; then inconclusive "the loopback gateway was not ready in ${READY_WAIT_S}s"; fi
  # Killed by a signal (for example the OOM killer): no in-start retry; the
  # restart policy (on-failure:3) bounds it.
  if [ "$GW_STATUS" -ge 128 ]; then inconclusive "the loopback gateway was killed while starting (status $GW_STATUS)"; fi
  if [ "$attempt" -ge "$START_ATTEMPTS" ]; then inconclusive "the loopback gateway exited while starting, $attempt times"; fi
  log "the loopback gateway exited while starting; retrying in ${RETRY_PAUSE_S}s"
  sleep "$RETRY_PAUSE_S"
  attempt=$((attempt + 1))
done
check runtime "$CONFIG_DEST"
cmp -s "$CONFIG_SRC" "$CONFIG_DEST" || { stop_gateway; verdict "OpenClaw rewrote the config file at start"; }
stop_gateway

printf 'checked_at=%s\n' "$(now)" > "$RUN_DIR/checked"
state starting
$OPENCLAW gateway &
GW_PID=$!
ready=0
wait_ready || ready=$?
if [ "$ready" -ne 0 ] || ! cmp -s "$CONFIG_SRC" "$CONFIG_DEST"; then
  stop_gateway
  inconclusive "the checked config did not come up cleanly on the LAN bind"
fi
printf 'serving_since=%s\nrelease=%s\n' "$(now)" "$RELEASE" > "$RUN_DIR/serving"
state serving
log "serving Master Craftsman (checked on this start)"
status=0
wait "$GW_PID" || status=$?
GW_PID=""
exit "$status"
