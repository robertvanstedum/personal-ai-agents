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

# Container names match production (CoS health checks address them by name).
STAGING_CORE_CONTAINERS=(
  postgres-ai-agents minimoi-model-gateway minimoi-cos-agent-a minimoi-curator
  minimoi-german minimoi-portuguese minimoi-portal minimoi-cos-scheduler
)
STAGING_BOT_CONTAINERS=(minimoi-system-bot minimoi-cos-bot)

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

require_release() {
  [[ -f "$RELEASE_DIR/docker-compose.prod.yml" ]] \
    || die "no release worktree at $RELEASE_DIR; run scripts/staging/build.sh <ref> first"
  [[ -f "$RELEASE_DIR/docker-compose.staging.yml" ]] \
    || die "$RELEASE_DIR has no docker-compose.staging.yml; the pinned release predates staging support"
  [[ -f "$STAGING_RELEASE_ENV" ]] \
    || die "missing $STAGING_RELEASE_ENV (image tag); run scripts/staging/build.sh <ref>"
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


