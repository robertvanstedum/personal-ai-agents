#!/bin/bash
# build.sh — pin a release and build the staging images from it (native arch).
#
# Usage: build.sh <ref> [--reviewed-branch] [--no-fetch] [--allow-emulated]
#
#   <ref> must be on origin/main (for example origin/main or a main SHA),
#   unless --reviewed-branch is given for a reviewed PR branch (issue #234:
#   "a Docker build from a reviewed branch").
#
# Steps:
#   1. git fetch origin (skip with --no-fetch), resolve <ref> to a SHA.
#   2. Create or reset the detached release worktree $RELEASE_DIR at the SHA.
#      Refuses when that worktree has local changes.
#   3. Build the 9 images with the SAME service map as .github/workflows/
#      deploy.yml (tests/test_staging_environment.py enforces parity), tagged
#      minimoi-staging/<repository>:<tag>. No --platform: native arm64. Any
#      image that is not the host architecture fails the build unless
#      --allow-emulated.
#   4. Copy services/model_gateway/litellm.staging.yaml to
#      $STAGING_ROOT/config/ and write $STAGING_ROOT/RELEASE (SHA, time, image
#      IDs and architectures) and $STAGING_ROOT/release.env (MINIMOI_IMAGE_TAG).
#
# Building does not touch running containers; up.sh starts the new images.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The release worktree is reset below. When this script runs from inside it,
# bash would read a changing file, so re-execute from a private copy first.
if [[ -z "${STAGING_BUILD_REEXEC:-}" ]]; then
  COPY_DIR=$(mktemp -d "${TMPDIR:-/tmp}/staging-build.XXXXXX")
  cp "$STAGING_SCRIPTS_DIR/lib.sh" "$STAGING_SCRIPTS_DIR/build.sh" "$COPY_DIR/"
  STAGING_BUILD_REEXEC=1 STAGING_REPO="$STAGING_REPO" exec bash "$COPY_DIR/build.sh" "$@"
fi

REF=""
REVIEWED=0
FETCH=1
ALLOW_EMULATED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reviewed-branch) REVIEWED=1 ;;
    --no-fetch) FETCH=0 ;;
    --allow-emulated) ALLOW_EMULATED=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$REF" ]] || die "only one <ref> may be given"; REF="$1" ;;
  esac
  shift
done
[[ -n "$REF" ]] || die "usage: build.sh <ref> [--reviewed-branch] [--no-fetch] [--allow-emulated]"
[[ -n "$STAGING_REPO" ]] || die "cannot locate the repository; set STAGING_REPO"
require_absolute_root

if [[ "$FETCH" == "1" ]]; then
  note "fetching origin"
  git -C "$STAGING_REPO" fetch --quiet origin
fi
FULL_SHA=$(git -C "$STAGING_REPO" rev-parse --verify "$REF^{commit}") || die "unknown ref: $REF"
if ! git -C "$STAGING_REPO" merge-base --is-ancestor "$FULL_SHA" origin/main; then
  [[ "$REVIEWED" == "1" ]] || die "$REF ($FULL_SHA) is not on origin/main; pass --reviewed-branch for a reviewed PR branch"
  note "building a reviewed branch that is not on origin/main: $REF"
fi
# Same meaning as deploy.yml's SHA: the 7-character image tag.
SHA="${FULL_SHA:0:7}"

# ── the release worktree ─────────────────────────────────────────────────────
if [[ -e "$RELEASE_DIR" ]]; then
  git -C "$RELEASE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "$RELEASE_DIR exists but is not a git worktree; move it aside"
  if [[ -n "$(git -C "$RELEASE_DIR" status --porcelain)" ]]; then
    die "release worktree $RELEASE_DIR has local changes; refusing to reset it"
  fi
  git -C "$RELEASE_DIR" checkout --quiet --detach "$FULL_SHA"
else
  mkdir -p "$(dirname "$RELEASE_DIR")"
  git -C "$STAGING_REPO" worktree add --detach "$RELEASE_DIR" "$FULL_SHA"
fi
[[ "$(git -C "$RELEASE_DIR" rev-parse HEAD)" == "$FULL_SHA" ]] || die "release worktree is not at $FULL_SHA"
[[ -f "$RELEASE_DIR/docker-compose.staging.yml" ]] \
  || die "$REF has no docker-compose.staging.yml; staging needs a release that includes it"

# ── images: the same map as .github/workflows/deploy.yml ─────────────────────
SERVICES="portal curator german portuguese system-bot cos-bot cos-scheduler cos-agent-a model-gateway"
HOST_ARCH=$(docker version --format '{{.Server.Arch}}')
IMAGE_LINES=()
BAD_ARCH=()
for service in $SERVICES; do
  case "$service" in
    portal) dockerfile="docker/Dockerfile.portal"; repository="portal"; tag="$SHA" ;;
    curator) dockerfile="docker/Dockerfile.curator"; repository="curator"; tag="$SHA" ;;
    german) dockerfile="docker/Dockerfile.german"; repository="mein-deutsch"; tag="$SHA" ;;
    portuguese) dockerfile="docker/Dockerfile.portuguese"; repository="portuguese"; tag="$SHA" ;;
    system-bot) dockerfile="docker/Dockerfile.telegram"; repository="system-bot"; tag="$SHA" ;;
    cos-bot) dockerfile="docker/Dockerfile.cos-bot"; repository="cos-bot"; tag="$SHA" ;;
    cos-scheduler) dockerfile="docker/Dockerfile.cos-scheduler"; repository="cos-scheduler"; tag="$SHA" ;;
    cos-agent-a) dockerfile="docker/Dockerfile.cos-agent-a"; repository="cos-scheduler"; tag="agent-a-$SHA" ;;
    model-gateway) dockerfile="docker/Dockerfile.model-gateway"; repository="cos-scheduler"; tag="model-gateway-$SHA" ;;
    *) echo "Unknown service $service"; exit 1 ;;
  esac
  image="minimoi-staging/$repository:$tag"
  note "building $service from $dockerfile as $image"
  docker build -f "$RELEASE_DIR/$dockerfile" -t "$image" \
    --label "minimoi.staging.release=$FULL_SHA" "$RELEASE_DIR"
  info=$(docker image inspect --format '{{.Id}} {{.Architecture}}' "$image")
  IMAGE_LINES+=("$service $image $info")
  [[ "${info##* }" == "$HOST_ARCH" ]] || BAD_ARCH+=("$image (${info##* })")
done
if [[ "${#BAD_ARCH[@]}" -gt 0 ]]; then
  echo "staging: these images are not $HOST_ARCH and would run emulated:" >&2
  printf '  %s\n' "${BAD_ARCH[@]}" >&2
  [[ "$ALLOW_EMULATED" == "1" ]] || die "refusing; pin a multi-arch base or rerun with --allow-emulated"
fi
docker image inspect postgres:latest >/dev/null 2>&1 \
  || die "local postgres:latest is missing (staging never pulls it; pull the same major on purpose)"

# ── release record ───────────────────────────────────────────────────────────
umask 077
mkdir -p "$STAGING_ROOT/config" "$STAGING_ROOT/state" "$STAGING_ROOT/logs"
chmod 700 "$STAGING_ROOT"
# The gateway image runs as a non-root user with every capability dropped:
# the mounted config file must be world-readable (it holds no secrets).
cp "$RELEASE_DIR/services/model_gateway/litellm.staging.yaml" "$STAGING_ROOT/config/litellm.staging.yaml"
chmod 644 "$STAGING_ROOT/config/litellm.staging.yaml"
{
  echo "sha=$FULL_SHA"
  echo "tag=$SHA"
  echo "ref=$REF"
  echo "reviewed_branch=$REVIEWED"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "release_dir=$RELEASE_DIR"
  echo "host_arch=$HOST_ARCH"
  echo "postgres_image=$(docker image inspect --format '{{.Id}}' postgres:latest)"
  printf 'image %s\n' "${IMAGE_LINES[@]}"
} > "$STAGING_RELEASE_FILE"
printf 'MINIMOI_IMAGE_TAG=%s\n' "$SHA" > "$STAGING_RELEASE_ENV"
note "release $SHA pinned; images built. Next: scripts/staging/up.sh (see README.md)"
