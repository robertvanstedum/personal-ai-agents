#!/bin/bash
# lib.sh — shared settings for the Mac Docker staging stack (dev.minimoi.ai).
#
# Sourced by every scripts/staging/*.sh. It is the ONLY place that builds the
# compose command line. Staging runs the production compose file plus
# docker-compose.staging.yml, both taken from the pinned release worktree,
# under project "minimoi-staging", with all state under $STAGING_ROOT.
#
# Never prints secret values. See scripts/staging/README.md.

set -euo pipefail

STAGING_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The data root, laid out exactly like /opt/minimoi. Compose does not expand
# "~", so it must be absolute. Colima shares $HOME into its VM, so keep it
# under $HOME or the containers would see empty folders.
STAGING_ROOT="${STAGING_ROOT:-$HOME/minimoi-staging}"
# The pinned, detached release worktree: build context and compose source.
# Nothing is ever bind-mounted from it.
RELEASE_DIR="${STAGING_RELEASE_DIR:-$HOME/.worktrees/staging-release}"
STAGING_PROJECT="minimoi-staging"
# The repository whose object store the release worktree and the seed read.
# Any worktree of it works; default to the one these scripts came from.
STAGING_REPO="${STAGING_REPO:-$(git -C "$STAGING_SCRIPTS_DIR" rev-parse --show-toplevel 2>/dev/null || true)}"

STAGING_ENV_FILE="$STAGING_ROOT/.env"
STAGING_RELEASE_ENV="$STAGING_ROOT/release.env"
STAGING_RELEASE_FILE="$STAGING_ROOT/RELEASE"
STAGING_BOTS_FLAG="$STAGING_ROOT/state/bots.on"
# Where env.sh got each name in .env (names and source labels, never values).
STAGING_ENV_SOURCES="$STAGING_ROOT/env.sources"
# Written by build.sh when a build fails after it moved the release worktree.
STAGING_BUILD_FAILED="$STAGING_ROOT/state/build.failed"
# Marker file build.sh puts in the release worktree's private git folder, so a
# mis-set STAGING_RELEASE_DIR can never reset somebody's own worktree.
STAGING_RELEASE_MARKER="minimoi-staging-release"

# Every host port the staging stack publishes (docker-compose.prod.yml plus
# docker-compose.staging.yml, all on 127.0.0.1). tests/test_staging_environment.py
# keeps this list equal to the compose files.
STAGING_HOST_PORTS="5001 5432 8766 8767 8769 8770 14000 18790"
# lsof command names (truncated to 9 characters) of the Docker/Colima port forwarder.
STAGING_FORWARDER_RE='^(ssh|limactl|colima|com\.docke|vpnkit|docker|gvproxy)'

# The staging bots' Telegram tokens. They come ONLY from these Keychain TEST
# accounts (env.sh), never from the root .env, and only while bots.on exists.
STAGING_TELEGRAM_TOKEN_NAMES="TELEGRAM_COS_BOT_TOKEN TELEGRAM_SYSTEM_BOT_TOKEN"
telegram_test_source() {
  case "$1" in
    TELEGRAM_COS_BOT_TOKEN) echo "keychain:telegram/cos_test_bot_token" ;;
    TELEGRAM_SYSTEM_BOT_TOKEN) echo "keychain:telegram/system_test_bot_token" ;;
  esac
}

# Container names match production (CoS health checks address them by name).
STAGING_CORE_CONTAINERS=(
  postgres-ai-agents minimoi-model-gateway minimoi-cos-agent-a minimoi-curator
  minimoi-german minimoi-portuguese minimoi-portal minimoi-cos-scheduler
)
STAGING_BOT_CONTAINERS=(minimoi-system-bot minimoi-cos-bot)

# Compose service -> container name for the main staging project.
container_of() {
  case "$1" in
    postgres) echo postgres-ai-agents ;;
    model-gateway) echo minimoi-model-gateway ;;
    cos-agent-a) echo minimoi-cos-agent-a ;;
    curator) echo minimoi-curator ;;
    german) echo minimoi-german ;;
    portuguese) echo minimoi-portuguese ;;
    portal) echo minimoi-portal ;;
    cos-scheduler) echo minimoi-cos-scheduler ;;
    system-bot) echo minimoi-system-bot ;;
    cos-bot) echo minimoi-cos-bot ;;
  esac
}
STAGING_CORE_SERVICES="postgres model-gateway cos-agent-a curator german portuguese portal cos-scheduler"
STAGING_BOT_SERVICES="system-bot cos-bot"

# Focus (Robert, 2026-09-28): on the 8 GB Mac, run only the containers under
# test. scripts/staging/focus.sh writes the set's name to state/focus and the
# services it keeps stopped to state/focus.stopped; up.sh and verify.sh read
# them, so a later up.sh never silently restarts a stopped service.
STAGING_FOCUS_FILE="$STAGING_ROOT/state/focus"
STAGING_FOCUS_STOPPED="$STAGING_ROOT/state/focus.stopped"
focus_name() { [[ -f "$STAGING_FOCUS_FILE" ]] && tr -d '[:space:]' < "$STAGING_FOCUS_FILE" || echo all; }
focus_stopped() { [[ -f "$STAGING_FOCUS_STOPPED" ]] && tr -s '[:space:]' ' ' < "$STAGING_FOCUS_STOPPED" | sed 's/^ //; s/ $//' || true; }
# is_focus_stopped SERVICE: true when the focus set keeps it stopped.
is_focus_stopped() { [[ " $(focus_stopped) " == *" $1 "* ]]; }
# Container name -> service (for verify.sh).
service_of() {
  local s
  for s in $STAGING_CORE_SERVICES $STAGING_BOT_SERVICES; do
    [[ "$(container_of "$s")" != "$1" ]] || { echo "$s"; return 0; }
  done
}

# Master Craftsman (MC spec v0.9 §3): its OWN Compose project, never in the
# main one. scripts/staging/mc.sh is the only place that runs it.
STAGING_MC_PROJECT="minimoi-staging-mc"
STAGING_MC_FILE="docker-compose.mc.yml"
STAGING_MC_ENABLED="$STAGING_ROOT/state/mc.enabled"
STAGING_MC_ENV="$STAGING_ROOT/mc.env"
STAGING_MC_NET="minimoi-staging-mc-net"
STAGING_MC_CONTAINER="minimoi-mc-agent"
STAGING_MC_VOLUMES=(minimoi-staging-mc-agent-state minimoi-staging-mc-agent-auth)
mc_enabled() { [[ -f "$STAGING_MC_ENABLED" ]]; }

# A network's IPAM gateways, one per line, without Docker's renderings of
# "none": an isolated bridge (gateway_mode_ipv4=isolated) has no gateway, and
# Docker 29 prints "invalid IP" (older versions "<no value>" or nothing).
network_gateways() {
  local line
  docker network inspect -f '{{range .IPAM.Config}}{{printf "%s\n" .Gateway}}{{end}}' "$1" 2>/dev/null \
    | while IFS= read -r line; do
        case "$line" in ""|"invalid IP"|"<no value>") ;; *) echo "$line" ;; esac
      done
}

# IPv4 addresses on a network's bridge interface inside the Docker VM, one per
# line (empty = the bridge has no host address). Needs colima; prints
# "unknown" when the VM cannot be asked.
bridge_ipv4() {
  local id name
  id=$(docker network inspect -f '{{.Id}}' "$1" 2>/dev/null) || { echo unknown; return 0; }
  name=$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}' "$1" 2>/dev/null || true)
  case "$name" in ""|"<no value>") name="br-${id:0:12}" ;; esac
  command -v colima >/dev/null 2>&1 || { echo unknown; return 0; }
  colima ssh -- ip -4 -o addr show dev "$name" 2>/dev/null | awk '{print $4}' || echo unknown
}

# External volumes (docker-compose.staging.yml). `compose down -v` cannot
# remove external volumes, and down.sh refuses -v anyway.
STAGING_VOLUMES=(minimoi-staging-postgres-data minimoi-staging-cos-agent-a-state minimoi-staging-cos-agent-a-auth)

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
die() { echo "staging: $*" >&2; exit 1; }
note() { echo "staging: $*"; }

require_absolute_root() {
  case "$STAGING_ROOT" in
    /*) ;;
    *) die "STAGING_ROOT must be an absolute path (got '$STAGING_ROOT')" ;;
  esac
  case "$STAGING_ROOT" in
    /opt/minimoi|/opt/minimoi/*) die "STAGING_ROOT may never be the production root /opt/minimoi" ;;
  esac
}

require_release_files() {
  [[ -f "$RELEASE_DIR/docker-compose.prod.yml" ]] \
    || die "no release worktree at $RELEASE_DIR; run scripts/staging/build.sh <ref> first"
  [[ -f "$RELEASE_DIR/docker-compose.staging.yml" ]] \
    || die "$RELEASE_DIR has no docker-compose.staging.yml; the pinned release predates staging support"
  [[ -f "$STAGING_RELEASE_ENV" ]] \
    || die "missing $STAGING_RELEASE_ENV (image tag); run scripts/staging/build.sh <ref>"
  [[ -f "$STAGING_RELEASE_FILE" ]] \
    || die "missing $STAGING_RELEASE_FILE; run scripts/staging/build.sh <ref>"
}

release_sha() {
  sed -n 's/^sha=//p' "$STAGING_RELEASE_FILE" | tail -n 1
}

# Prints why the release worktree does not match RELEASE (empty when it does).
# A failed build.sh can leave compose files from one commit next to images of
# another; up/down/verify must never pair them.
release_mismatch() {
  local want head
  want=$(release_sha)
  [[ -n "$want" ]] || { echo "$STAGING_RELEASE_FILE has no sha= line"; return 0; }
  head=$(git -C "$RELEASE_DIR" rev-parse HEAD 2>/dev/null || true)
  if [[ "$head" != "$want" ]]; then
    echo "release worktree $RELEASE_DIR is at ${head:-an unknown commit}, but RELEASE pins $want (a build.sh run failed or was interrupted); rerun build.sh until it succeeds"
  fi
}

require_release() {
  require_release_files
  local problem
  problem=$(release_mismatch)
  [[ -z "$problem" ]] || die "$problem"
}

require_env() {
  [[ -f "$STAGING_ENV_FILE" ]] || die "missing $STAGING_ENV_FILE; run scripts/staging/env.sh"
  local mode
  mode=$(file_mode "$STAGING_ENV_FILE")
  [[ "$mode" == "600" ]] || die "$STAGING_ENV_FILE must be mode 600 (is $mode)"
}

release_tag() {
  sed -n 's/^MINIMOI_IMAGE_TAG=//p' "$STAGING_RELEASE_ENV" | tail -n 1
}

# The single compose invocation. Shell variables win over --env-file values,
# so MINIMOI_ROOT always equals this script's STAGING_ROOT.
staging_compose() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -v|--volumes|--volumes=*) die "refusing 'compose $*': staging volumes are never removed by a script" ;;
    esac
  done
  require_absolute_root
  require_release
  require_env
  MINIMOI_ROOT="$STAGING_ROOT" docker compose \
    -p "$STAGING_PROJECT" \
    --env-file "$STAGING_ENV_FILE" \
    --env-file "$STAGING_RELEASE_ENV" \
    -f "$RELEASE_DIR/docker-compose.prod.yml" \
    -f "$RELEASE_DIR/docker-compose.staging.yml" \
    "$@"
}

bots_enabled() { [[ -f "$STAGING_BOTS_FLAG" ]]; }

# The NAMES in the staging .env, one per line (values are never printed).
env_names() {
  sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$STAGING_ENV_FILE"
}

# One line per problem with the staging bots' Telegram token names, empty when
# none: with bots.on both must be in .env and recorded by env.sh as coming from
# their Keychain test account; with bots off neither may be in .env.
bot_token_problems() {
  local names name want got
  names=$(env_names)
  for name in $STAGING_TELEGRAM_TOKEN_NAMES; do
    want=$(telegram_test_source "$name")
    got=""
    if [[ -f "$STAGING_ENV_SOURCES" ]]; then
      got=$(sed -n "s/^$name //p" "$STAGING_ENV_SOURCES" | tail -n 1)
    fi
    if bots_enabled; then
      if ! grep -qx "$name" <<< "$names"; then
        echo "$name is not in $STAGING_ENV_FILE while bots.on is set (rerun env.sh --force)"
      elif [[ "$got" != "$want" ]]; then
        echo "$name source is '${got:-unrecorded}' in $STAGING_ENV_SOURCES, expected $want (rerun env.sh --force)"
      fi
    elif grep -qx "$name" <<< "$names"; then
      echo "$name is in $STAGING_ENV_FILE while the bots are off (rerun env.sh --force)"
    fi
  done
}

# Commands listening on 127.0.0.1:PORT (or any address), as name(pid) lines.
port_listeners() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1"("$2")"}' | sort -u || true
}

# Describes whatever holds PORT other than a staging container (empty when
# the port is free or held only through a staging container's forward).
foreign_port_holder() {
  local port="$1" holders native containers name project others="" staging_held=0 desc=""
  holders=$(port_listeners "$port")
  [[ -n "$holders" ]] || return 0
  native=$(grep -Ev "$STAGING_FORWARDER_RE" <<< "$holders" | tr '\n' ' ' || true)
  containers=$(docker ps --filter "publish=$port" \
    --format '{{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null || true)
  while read -r name project; do
    [[ -n "$name" ]] || continue
    if [[ "$project" == "$STAGING_PROJECT" ]]; then
      staging_held=1
    else
      others="${others}container $name (project ${project:-none}) "
    fi
  done <<< "$containers"
  [[ -z "$native" ]] || desc="native process ${native}"
  desc="$desc$others"
  if [[ -z "$desc" && "$staging_held" == 0 ]]; then
    desc="forwarder $(tr '\n' ' ' <<< "$holders")with no running container publishing it"
  fi
  [[ -z "$desc" ]] || echo "${desc% }"
}

# port_allowed PORT "5001,8767": is PORT in the comma list?
port_allowed() { [[ ",$2," == *",$1,"* ]]; }

# Validates a comma list of ports against STAGING_HOST_PORTS.
check_allow_list() {
  local port
  for port in ${1//,/ }; do
    [[ " $STAGING_HOST_PORTS " == *" $port "* ]] || die "--allow-holder $port is not a staging host port ($STAGING_HOST_PORTS)"
  done
}


