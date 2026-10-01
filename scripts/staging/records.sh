#!/bin/bash
# records.sh — Rooms' Records service on staging (Guild 1.1 slice 4, spec §6):
# its OWN Compose project (docker-compose.records.yml), like mc.sh for Master
# Craftsman. It never touches the main project's containers; the portal joins
# Records' internal network permanently from docker-compose.staging.yml.
#
# Usage:
#   records.sh build    build minimoi-staging/records:<release tag> from the
#                       pinned release worktree (build.sh's RELEASE)
#   records.sh up       start minimoi-records (needs the image, the main
#                       stack's network minimoi-staging-records, and the data
#                       folder $STAGING_ROOT/data/records at mode 700)
#   records.sh down     stop and remove the container (the data folder is kept)
#   records.sh status   state and health
#
# Records keeps its own sign-in. On its first start it creates owner-key.txt
# (mode 600) in the data folder, unless the owner put his own one there first;
# this script never creates, reads or prints a credential.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RECORDS_PROJECT="minimoi-staging-records"
RECORDS_FILE="docker-compose.records.yml"
RECORDS_CONTAINER="minimoi-records"
RECORDS_NETWORK="minimoi-staging-records"

records_compose() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -v|--volumes|--volumes=*) die "refusing 'compose $*': Records' data is never removed by a script" ;;
    esac
  done
  require_absolute_root
  require_release
  [[ -f "$RELEASE_DIR/$RECORDS_FILE" ]] || die "the pinned release has no $RECORDS_FILE (it predates Guild 1.1 slice 4)"
  MINIMOI_ROOT="$STAGING_ROOT" docker compose -p "$RECORDS_PROJECT" --env-file "$STAGING_RELEASE_ENV" \
    -f "$RELEASE_DIR/$RECORDS_FILE" "$@"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  build)
    require_absolute_root
    require_release
    sha=$(release_sha); tag=$(release_tag)
    image="minimoi-staging/records:$tag"
    note "building $image from $RELEASE_DIR at ${sha:0:7}"
    docker build -f "$RELEASE_DIR/docker/Dockerfile.records" -t "$image" \
      --label "minimoi.staging.release=$sha" "$RELEASE_DIR" ;;
  up)
    require_absolute_root
    folder="$STAGING_ROOT/data/records"
    [[ -d "$folder" ]] || die "missing $folder; run build.sh (it creates it at mode 700)"
    [[ "$(file_mode "$folder")" == 700 ]] || die "$folder must be mode 700 (Records refuses a shared folder)"
    docker network inspect "$RECORDS_NETWORK" >/dev/null 2>&1 \
      || die "missing network $RECORDS_NETWORK; bring the main stack up first (up.sh portal)"
    records_compose up -d --no-build
    note "up. Records answers only through the portal at https://dev.minimoi.ai/app/records/ (owner sign-in, then Records' own sign-in)" ;;
  down)
    records_compose down ;;
  status)
    docker inspect -f '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$RECORDS_CONTAINER" 2>/dev/null \
      || echo "$RECORDS_CONTAINER: absent" ;;
  *)
    sed -n '2,19p' "$0"; exit 2 ;;
esac
