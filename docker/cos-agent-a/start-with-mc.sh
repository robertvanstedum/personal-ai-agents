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
# N11, split by agent. CoS never goes down because of MC:
#   * the combined config fails ANY check (MC's set, CoS's set under the
#     combined config, a gateway request error on either agent, a scheduled
#     job, a config OpenClaw cannot start or rewrites) -> .mc-selfcheck-failed
#     (release, config hash, failing agent and names), then CoS starts alone on
#     the CoS-only config (#244's, production's), after its own CHECK phase.
#     Sticky for this release and config: a new release or config tries the
#     combined config again.
#   * a combined check that cannot complete (a timeout on a busy host) -> CoS
#     alone for THIS start (checked), nothing sticky; the next start retries.
#   * only when CoS fails under the CoS-only config too -> .cos-selfcheck-failed;
#     OpenClaw is NOT started; the container stays up and unhealthy, with no
#     restart loop, until Robert deletes the file.
#   * a CoS-only check that cannot complete exits for a Docker restart at most
#     MINIMOI_MC_MAX_RETRIES (3) times in a row, then stays down the same way.
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
RETRIES_FILE="$STATE_DIR/.selfcheck-retries"
MAX_RETRIES="${MINIMOI_MC_MAX_RETRIES:-3}"
COS_ONLY_REASON=""

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

# 0 ready; 1 the gateway process exited (the config did not start);
# 2 still not ready after READY_WAIT_S (a slow host, not a verdict).
wait_ready() {
  i=0
  while [ "$i" -lt "$READY_WAIT_S" ]; do
    if ! kill -0 "$GW_PID" 2>/dev/null; then return 1; fi
    if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/readyz" 2>/dev/null; then return 0; fi
    i=$((i + 2))
    sleep 2
  done
  return 2
}

# run_selfcheck static|runtime cos-only|combined -> sets CHECK_CODE
run_selfcheck() {
  CHECK_CODE=0
  node "$SELFCHECK" "$1" "$CONFIG_DEST" "$2" > "$RUN_DIR/selfcheck-$1-$2.json" || CHECK_CODE=$?
  cat "$RUN_DIR/selfcheck-$1-$2.json" >&2 || true
}

write_marker() {
  marker="$1"; mode="$2"; json="$3"; agent="${4:-}"
  {
    echo "failed_at=$(now)"
    [ -z "$agent" ] || echo "failing_agent=$agent"
    echo "release=$RELEASE"
    echo "combined_sha256=$HASH"
    echo "mode=$mode"
    echo "result=$(tr -d '\n' < "$json" 2>/dev/null || echo unknown)"
  } > "$marker"
}

# CHECK phase for one config. Returns selfcheck.mjs's codes: 0 pass, 2 CoS
# failure, 3 MC failure, 4 a CoS call could not complete, 5 only MC calls
# could not complete. A gateway that exits before it is ready counts against
# the config's owner (combined: MC; CoS-only: CoS); one never ready in time is
# inconclusive.
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
  ready=0
  wait_ready || ready=$?
  if [ "$ready" -ne 0 ]; then
    stop_gateway
    if [ "$ready" -eq 2 ]; then
      echo "{\"phase\":\"runtime\",\"mode\":\"$mode\",\"inconclusive\":[\"gateway not ready within ${READY_WAIT_S}s\"]}" > "$RUN_DIR/selfcheck-runtime-$mode.json"
      return 4
    fi
    echo "{\"phase\":\"runtime\",\"mode\":\"$mode\",\"error\":\"the gateway exited before it was ready\"}" > "$RUN_DIR/selfcheck-runtime-$mode.json"
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
    [ -z "$COS_ONLY_REASON" ] || printf 'mc_off_reason=%s\n' "$COS_ONLY_REASON" >> "$RUN_DIR/serving"
    rm -f "$RETRIES_FILE"
    state "serving-$mode"
    if [ "$mode" = cos-only ]; then
      loud "Master Craftsman is OFF ($COS_ONLY_REASON). CoS runs alone, checked on this start."
    else
      log "serving CoS and Master Craftsman (checked on this start)"
    fi
  else
    stop_gateway
    if [ "$mode" = combined ]; then
      echo "{\"phase\":\"serve\",\"mode\":\"combined\",\"error\":\"the checked combined config did not come up cleanly on the LAN bind\"}" > "$RUN_DIR/selfcheck-runtime-combined.json"
      mc_failed mc
    fi
    retry_or_stay_down "the checked CoS-only config did not come up cleanly on the LAN bind"
  fi
  status=0
  wait "$GW_PID" || status=$?
  GW_PID=""
  exit "$status"
}

# CoS alone on the CoS-only config (#244's), after its own loopback check.
cos_only() {
  code=0
  check_config cos-only "$COS_ONLY_CONFIG" || code=$?
  case "$code" in
    0) serve cos-only "$COS_ONLY_CONFIG" ;;
    4|5) retry_or_stay_down "the CoS-only self-check could not complete" ;;
    *)
      write_marker "$COS_FAILED" cos-only "$(last_result cos-only)" cos
      stay_down
      ;;
  esac
}

# The file with the last check result for a mode (runtime if it ran).
last_result() {
  if [ -s "$RUN_DIR/selfcheck-runtime-$1.json" ]; then echo "$RUN_DIR/selfcheck-runtime-$1.json"
  else echo "$RUN_DIR/selfcheck-static-$1.json"; fi
}

# CoS could not be checked (a slow or overloaded host). Never serve unchecked:
# exit for Docker's restart policy, at most MAX_RETRIES times in a row (counted
# on the state volume, cleared when anything is served), then stay down with a
# marker, so there is never an endless restart loop.
retry_or_stay_down() {
  count=$(cat "$RETRIES_FILE" 2>/dev/null || echo 0)
  case "$count" in ''|*[!0-9]*) count=0 ;; esac
  count=$((count + 1))
  if [ "$count" -ge "$MAX_RETRIES" ]; then
    rm -f "$RETRIES_FILE"
    {
      echo "failed_at=$(now)"
      echo "failing_agent=cos"
      echo "release=$RELEASE"
      echo "reason=$1 ($count starts in a row)"
      echo "result=$(tr -d '\n' < "$(last_result cos-only)" 2>/dev/null || echo unknown)"
    } > "$COS_FAILED"
    stay_down
  fi
  echo "$count" > "$RETRIES_FILE"
  state "check-inconclusive-cos-only"
  loud "$1; nothing is served; exiting so Docker restarts the container (attempt $count of $MAX_RETRIES)"
  exit 1
}

# The combined config failed a check: MC off (sticky for this release and
# config), CoS alone after its own check. $1 names the failing agent.
mc_failed() {
  write_marker "$MC_FAILED" combined "$(last_result combined)" "$1"
  COS_ONLY_REASON="the combined config failed its self-check ($1); see $MC_FAILED"
  loud "Master Craftsman self-check FAILED under the combined config (failing: $1); starting CoS alone on the CoS-only config. Details: $MC_FAILED"
  cos_only
}

# A sticky MC failure for this exact release and combined config: CoS alone.
if [ -f "$MC_FAILED" ]; then
  if grep -qx "release=$RELEASE" "$MC_FAILED" && grep -qx "combined_sha256=$HASH" "$MC_FAILED"; then
    COS_ONLY_REASON="$MC_FAILED names this release and config; delete it to retry"
    loud "Master Craftsman stays OFF: $MC_FAILED names this release and config. Delete it to retry."
    cos_only
  fi
  log "an older MC self-check failure is for another release or config; trying the combined config again"
  mv -f "$MC_FAILED" "$MC_FAILED.previous"
fi

for f in "$COMBINED_CONFIG" "$SELFCHECK" "$MC_WORKSPACE_SRC/AGENTS.md"; do
  [ -f "$f" ] || { echo "{\"error\":\"missing $f\"}" > "$RUN_DIR/selfcheck-static-combined.json"; mc_failed mc; }
done
apply_mc_workspace

code=0
check_config combined "$COMBINED_CONFIG" || code=$?
case "$code" in
  0) serve combined "$COMBINED_CONFIG" ;;
  2) mc_failed cos ;;   # CoS's set is wrong under the COMBINED config: the combined config is at fault
  4|5)
    COS_ONLY_REASON="the combined self-check could not complete on this start (not sticky; the next start retries)"
    loud "the combined self-check could not complete ($(last_result combined)); CoS starts alone for this start, nothing sticky"
    cos_only
    ;;
  *) mc_failed mc ;;
esac
