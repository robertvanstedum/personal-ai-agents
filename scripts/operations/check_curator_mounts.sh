#!/bin/bash
# check_curator_mounts.sh — post-deploy proof that the running Curator reads
# and writes the host's durable state (#305). Follows check_queue_mount.sh.
#
# Fails the release unless, for every entry in curator_state.map:
#   * dir/file: the container has a read-write bind from the host path to the
#     container path, and the content seen inside equals the host's (SHA-256
#     of the file, or of the sorted per-file hashes of the folder);
#   * moved: the env var names the mounted path, the host file (if any) equals
#     what the container sees there, and the old unmounted path is absent.
# A green /health alone would not show any of this.
#
# Env: MINIMOI_ROOT (default /opt/minimoi), CURATOR_CONTAINER (default
#      minimoi-curator), CURATOR_STATE_MAP (default: beside this script)

set -euo pipefail

ROOT="${MINIMOI_ROOT:-/opt/minimoi}"
CONTAINER="${CURATOR_CONTAINER:-minimoi-curator}"
MAP="${CURATOR_STATE_MAP:-$(dirname "$0")/curator_state.map}"

fail() { echo "curator mount check FAILED: $*" >&2; exit 1; }

[[ -f "$MAP" ]] || fail "state map $MAP missing"
[[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == "true" ]] ||
  fail "$CONTAINER is not running"

MOUNTS="$(docker inspect --format '{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}|{{.RW}};{{end}}' "$CONTAINER")"

DIGEST='cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum | sha256sum | cut -d" " -f1'
host_digest() {
  if [[ -d "$1" ]]; then bash -c "$DIGEST" _ "$1"; else sha256sum "$1" | cut -d' ' -f1; fi
}
container_digest() {
  if [[ "$2" == dir ]]; then docker exec "$CONTAINER" sh -c "$DIGEST" _ "$1"
  else docker exec "$CONTAINER" sha256sum "$1" | cut -d' ' -f1
  fi
}

# One retry: a Curator job may write between the two reads.
same_both_sides() {
  local host="$1" target="$2" kind="$3" attempt
  for attempt in 1 2; do
    [[ "$(host_digest "$host")" == "$(container_digest "$target" "$kind")" ]] && return 0
    [[ "$attempt" == 1 ]] && sleep 5
  done
  return 1
}

checked=0
while read -r kind old host target; do
  [[ -z "$kind" || "$kind" == \#* ]] && continue
  src="$ROOT/$host"
  case "$kind" in
    dir|appdir|file)
      case ";$MOUNTS" in
        *";bind|$src|$target|true;"*) ;;
        *) fail "$CONTAINER has no read-write bind $src -> $target" ;;
      esac
      same_both_sides "$src" "$target" "${kind/appdir/dir}" || fail "$target inside $CONTAINER differs from $src"
      ;;
    moved)
      case "$target" in
        */signal_store.jsonl) var=CURATOR_SIGNAL_STORE_PATH ;;
        */x_pull_state.json) var=CURATOR_X_PULL_STATE_PATH ;;
        */curator_output.txt) var=CURATOR_OUTPUT_PATH ;;
        *) fail "no env var known for $target" ;;
      esac
      value="$(docker exec "$CONTAINER" printenv "$var" || true)"
      [[ "$value" == "$target" ]] || fail "$var in $CONTAINER is '$value', expected $target"
      if docker exec "$CONTAINER" test -e "$old"; then
        fail "$old exists inside $CONTAINER; something still writes the unmounted path"
      fi
      if [[ -e "$src" ]]; then
        same_both_sides "$src" "$target" file || fail "$target inside $CONTAINER differs from $src"
      fi
      ;;
    *) fail "unknown kind '$kind' in $MAP" ;;
  esac
  checked=$((checked + 1))
done < "$MAP"

echo "Curator mount check passed ($checked entries)."
