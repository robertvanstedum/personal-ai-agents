#!/bin/sh
# Staging-only start for CoS Agent A with Master Craftsman as a second agent
# (MC spec v0.7 §1.4, Robert's N11 "split by agent", Codex v0.7 finding 1).
#
# Production never runs this file: its entrypoint stays apply-config.sh (#244).
# docker-compose.staging-mc.yml sets
#   entrypoint: ["tini", "-g", "--", "/opt/minimoi/cos-agent-a/start-with-mc.sh"]
#
# The readiness gate. No CoS or MC request is served by a configuration whose
# self-check has not passed on this start:
#   1. static check of the config file (no gateway yet);
#   2. CHECK phase: OpenClaw started with --bind loopback, so nothing outside
#      the container can reach it (CoS callers get "connection refused", the
#      same as a stopped container); the runtime check asks it, over loopback,
#      for each agent's effective tools and the scheduler; then it is stopped;
#   3. only after a pass is the checked config started on the LAN bind.
# The run markers live in /tmp/minimoi-mc (container-local, cleared on every
# start), so a marker always belongs to this start. The staging healthcheck
# requires /tmp/minimoi-mc/serving.
#
# N11, split by agent:
#   * CoS's check fails  -> .cos-selfcheck-failed on the state volume; OpenClaw
#     is NOT started; the container stays up and unhealthy with no restart loop
#     until Robert deletes the file.
#   * only MC's check fails -> .mc-selfcheck-failed (release, config hash,
#     failing names); CoS starts alone on the CoS-only config (#244's), after
#     its own CHECK phase. Sticky for this release and config: a new release
#     or config tries the combined config again.
# CoS never goes down because of MC.
#
# Signals: tini -g sends TERM to the whole process group; docker stop gives
# OpenClaw its grace period (stop_grace_period in the overlay).
set -eu

# The MINIMOI_MC_* overrides exist for tests/cos/test_start_with_mc.py; the
# image never sets them.
IMAGE_DIR="${MINIMOI_MC_IMAGE_DIR:-/opt/minimoi/cos-agent-a}"
COS_ONLY_CONFIG="$IMAGE_DIR/openclaw.json"
COMBINED_CONFIG="$IMAGE_DIR/openclaw.cos-mc.json"
MC_WORKSPACE_SRC="$IMAGE_DIR/mc-agent/workspace"
SELFCHECK="$IMAGE_DIR/selfcheck.mjs"
STATE_DIR="${OPENCLAW_STATE_DIR:-/home/node/.openclaw}"
CONFIG_DEST="${OPENCLAW_CONFIG_PATH:-$STATE_DIR/openclaw.json}"
MC_WORKSPACE="$STATE_DIR/workspace-mc"
COS_FAILED="$STATE_DIR/.cos-selfcheck-failed"
MC_FAILED="$STATE_DIR/.mc-selfcheck-failed"
RUN_DIR="${MINIMOI_MC_RUN_DIR:-/tmp/minimoi-mc}"
PORT="${MINIMOI_MC_PORT:-18789}"
READY_WAIT_S="${MINIMOI_MC_READY_WAIT_S:-300}"
OPENCLAW="${MINIMOI_MC_OPENCLAW:-node /app/openclaw.mjs}"
RELEASE="${MINIMOI_RELEASE_SHA:-unknown}"

log() { echo "cos-agent-a[start-with-mc]: $*" >&2; }
loud() { log "********** $* **********"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
state() { echo "$1" > "$RUN_DIR/state"; }

# Everything that decides MC's behaviour, hashed: the combined config, MC's
# workspace files and these two scripts. A fallback marker is keyed on it.
combined_hash() {
  cat "$COMBINED_CONFIG" "$MC_WORKSPACE_SRC"/*.md "$SELFCHECK" "$0" 2>/dev/null | sha256sum | cut -c1-64
}
HASH=$(combined_hash)
printf 'started_at=%s\nrelease=%s\ncombined_sha256=%s\n' "$(now)" "$RELEASE" "$HASH" > "$RUN_DIR/start"

stay_down() {
  state cos-selfcheck-failed
  loud "CoS self-check FAILED ($COS_FAILED). OpenClaw is NOT started. CoS and MC are both unavailable. Read the file, fix the cause, then delete it and restart the container."
  sed 's/^/  /' "$COS_FAILED" >&2 || true
  # Stay up (no restart loop) and unhealthy. tini -g ends this on docker stop.
  while :; do sleep 3600; done
}

[ -f "$COS_FAILED" ] && stay_down

# Copy an image file over the state volume's copy, keeping a differing
# previous copy beside it (as apply-config.sh does for openclaw.json).
apply_file() {
  src="$1"; dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then
    cp -p "$dest" "$dest.replaced-by-image"
  fi
  tmp="$dest.image-apply.$$"
  cp "$src" "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$dest"
}

apply_mc_workspace() {
  mkdir -p "$MC_WORKSPACE"
  for f in "$MC_WORKSPACE_SRC"/*.md; do
    apply_file "$f" "$MC_WORKSPACE/$(basename "$f")"
  done
}

GW_PID=""
stop_gateway() {
  [ -n "$GW_PID" ] || return 0
  kill -TERM "$GW_PID" 2>/dev/null || true
  i=0
  while kill -0 "$GW_PID" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then kill -KILL "$GW_PID" 2>/dev/null || true; fi
    sleep 1
  done
  GW_STATUS=0
  wait "$GW_PID" 2>/dev/null || GW_STATUS=$?
  GW_PID=""
}
# docker stop: OpenClaw drains and exits; exit with its status (0 when clean).
on_signal() {
  log "signal received; stopping OpenClaw"
  GW_STATUS=0
  stop_gateway
  exit "$GW_STATUS"
}
trap on_signal TERM INT

wait_ready() {
  i=0
  while [ "$i" -lt "$READY_WAIT_S" ]; do
    if ! kill -0 "$GW_PID" 2>/dev/null; then return 1; fi
    if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/readyz" 2>/dev/null; then return 0; fi
    i=$((i + 2))
    sleep 2
  done
  return 1
}

# run_selfcheck static|runtime cos-only|combined -> sets CHECK_CODE
run_selfcheck() {
  CHECK_CODE=0
  node "$SELFCHECK" "$1" "$CONFIG_DEST" "$2" > "$RUN_DIR/selfcheck-$1-$2.json" || CHECK_CODE=$?
  cat "$RUN_DIR/selfcheck-$1-$2.json" >&2 || true
}

write_marker() {
  marker="$1"; mode="$2"; json="$3"
  {
    echo "failed_at=$(now)"
    echo "release=$RELEASE"
    echo "combined_sha256=$HASH"
    echo "mode=$mode"
    echo "result=$(tr -d '\n' < "$json" 2>/dev/null || echo unknown)"
  } > "$marker"
}

# CHECK phase for one config. Returns 0 pass, 2 CoS failure, 3 MC failure.
check_config() {
  mode="$1"; src="$2"
  if ! apply_file "$src" "$CONFIG_DEST"; then
    echo "{\"phase\":\"static\",\"mode\":\"$mode\",\"error\":\"could not apply $src\"}" > "$RUN_DIR/selfcheck-static-$mode.json"
    [ "$mode" = combined ] && return 3 || return 2
  fi
  run_selfcheck static "$mode"
  [ "$CHECK_CODE" -eq 0 ] || return "$CHECK_CODE"
  state "checking-$mode"
  log "CHECK phase ($mode): OpenClaw on loopback only; callers are refused until the check passes"
  $OPENCLAW gateway --bind loopback &
  GW_PID=$!
  if ! wait_ready; then
    stop_gateway
    echo "{\"phase\":\"runtime\",\"mode\":\"$mode\",\"error\":\"gateway not ready within ${READY_WAIT_S}s\"}" > "$RUN_DIR/selfcheck-runtime-$mode.json"
    [ "$mode" = combined ] && return 3 || return 2
  fi
  run_selfcheck runtime "$mode"
  code=$CHECK_CODE
  if [ "$code" -eq 0 ] && ! cmp -s "$src" "$CONFIG_DEST"; then
    echo "{\"phase\":\"runtime\",\"mode\":\"$mode\",\"error\":\"OpenClaw rewrote the config file at start\"}" > "$RUN_DIR/selfcheck-runtime-$mode.json"
    [ "$mode" = combined ] && code=3 || code=2
  fi
  stop_gateway
  return "$code"
}

# SERVE phase: the checked config, LAN bind (from the config), foreground.
serve() {
  mode="$1"; src="$2"
  apply_file "$src" "$CONFIG_DEST"
  printf 'mode=%s\nchecked_at=%s\n' "$mode" "$(now)" > "$RUN_DIR/checked"
  state "starting-$mode"
  $OPENCLAW gateway &
  GW_PID=$!
  if wait_ready && cmp -s "$src" "$CONFIG_DEST"; then
    printf 'mode=%s\nserving_since=%s\nrelease=%s\ncombined_sha256=%s\n' "$mode" "$(now)" "$RELEASE" "$HASH" > "$RUN_DIR/serving"
    state "serving-$mode"
    if [ "$mode" = cos-only ]; then
      loud "Master Craftsman is OFF: its self-check failed ($MC_FAILED). CoS runs alone. Delete the file to retry."
    else
      log "serving CoS and Master Craftsman (checked on this start)"
    fi
  else
    log "the checked config did not come up cleanly on the LAN bind; exiting so Docker restarts the container"
    stop_gateway
    exit 1
  fi
  status=0
  wait "$GW_PID" || status=$?
  GW_PID=""
  exit "$status"
}

cos_only() {
  code=0
  check_config cos-only "$COS_ONLY_CONFIG" || code=$?
  if [ "$code" -ne 0 ]; then
    write_marker "$COS_FAILED" cos-only "$(last_result cos-only)"
    stay_down
  fi
  serve cos-only "$COS_ONLY_CONFIG"
}

# The file with the last check result for a mode (runtime if it ran).
last_result() {
  if [ -s "$RUN_DIR/selfcheck-runtime-$1.json" ]; then echo "$RUN_DIR/selfcheck-runtime-$1.json"
  else echo "$RUN_DIR/selfcheck-static-$1.json"; fi
}

mc_failed() {
  write_marker "$MC_FAILED" combined "$(last_result combined)"
  loud "Master Craftsman self-check FAILED; starting CoS alone. Details: $MC_FAILED"
  cos_only
}

# A sticky MC failure for this exact release and combined config: CoS alone.
if [ -f "$MC_FAILED" ]; then
  if grep -qx "release=$RELEASE" "$MC_FAILED" && grep -qx "combined_sha256=$HASH" "$MC_FAILED"; then
    loud "Master Craftsman stays OFF: $MC_FAILED names this release and config. Delete it to retry."
    cos_only
  fi
  log "an older MC self-check failure is for another release or config; trying the combined config again"
  mv -f "$MC_FAILED" "$MC_FAILED.previous"
fi

for f in "$COMBINED_CONFIG" "$SELFCHECK" "$MC_WORKSPACE_SRC/AGENTS.md"; do
  [ -f "$f" ] || { echo "{\"error\":\"missing $f\"}" > "$RUN_DIR/selfcheck-static-combined.json"; mc_failed; }
done
apply_mc_workspace

code=0
check_config combined "$COMBINED_CONFIG" || code=$?
case "$code" in
  0) serve combined "$COMBINED_CONFIG" ;;
  3) mc_failed ;;
  *)
    write_marker "$COS_FAILED" combined "$(last_result combined)"
    stay_down
    ;;
esac
