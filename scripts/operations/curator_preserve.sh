#!/bin/bash
# curator_preserve.sh — keep Curator's container-only state before the
# container is replaced (#305).
#
# Some Curator state lives only in the container's writable layer (no bind
# mount): general comments, the signal event log, the X pull cursor, the
# research library, and runtime-edited config. Replacing the container throws
# that layer away. This script copies it out first.
#
#   check     Read only. Report the container, its image, every known
#             container-only path, and how many other files the writable
#             layer holds outside the mounts. Writes nothing.
#   snapshot  Stop the container (so nothing writes mid-copy), copy every
#             known container-only path plus every other file the writable
#             layer added or changed outside the mounts, then write a
#             SHA-256 manifest. Start the container again afterwards, unless
#             --keep-stopped is given (the deploy gate replaces it next).
#
# The snapshot is root-only, written under a .partial name and renamed only
# when complete, and never pruned by this script. It refuses to stop the
# container while a Curator job is running inside it; retry when it has
# finished. If anything fails after the stop, the EXIT trap starts the old
# container again, so a failed snapshot never leaves Curator down.
#
# Usage: curator_preserve.sh check|snapshot [--keep-stopped]
# Env:   CURATOR_CONTAINER (default minimoi-curator)
#        CURATOR_PRESERVE_ROOT (default /opt/minimoi/backups/curator-preserve)
#        CURATOR_COMPOSE_FILE (default /opt/minimoi/docker-compose.prod.yml)

set -euo pipefail

MODE="${1:-}"
KEEP_STOPPED=0
[[ "${2:-}" == "--keep-stopped" ]] && KEEP_STOPPED=1
case "$MODE" in
  check|snapshot) ;;
  *) echo "usage: $0 check|snapshot [--keep-stopped]" >&2; exit 2 ;;
esac

CONTAINER="${CURATOR_CONTAINER:-minimoi-curator}"
PRESERVE_ROOT="${CURATOR_PRESERVE_ROOT:-/opt/minimoi/backups/curator-preserve}"
COMPOSE_FILE="${CURATOR_COMPOSE_FILE:-/opt/minimoi/docker-compose.prod.yml}"

# Known durable state with no mount in the current production definition
# (read from the deployed code, 15 Oct 2026). `topics` is listed so that it is
# kept if it ever appears; its absence is reported, never created.
KNOWN_PATHS=(
  /app/data/annotations
  /app/signal_store.jsonl
  /app/x_pull_state.json
  /app/_NewDomains/research-intelligence/library
  /app/_NewDomains/research-intelligence/agent/config.json
  /app/domains/curator/curator_sources.json
  /app/curator_cache
  /app/curator_output.txt
  /app/_NewDomains/research-intelligence/topics
)

# A Curator job running inside the container must not be cut off mid-write.
BUSY_PATTERN='curator_rss_v2|curator_intelligence|curator_priority_feed|x_pull_incremental|research\.py|run\.py|generate_dive|thread_expiry'

# Exact-name lookup in a successful container listing. A Docker or daemon
# error stops the script: it is never read as "no container" (#305 review).
container_exists() {
  local names
  names="$(docker ps -a --format '{{.Names}}')" || { echo "REFUSED: could not list Docker containers" >&2; exit 1; }
  grep -qxF -- "$1" <<< "$names"
}

if ! container_exists "$CONTAINER"; then
  echo "No $CONTAINER container; nothing to preserve."
  exit 0
fi

inspect() { docker inspect --format="$1" "$CONTAINER"; }

# Mount destinations, one per line. Files under them are durable already.
MOUNT_DESTS="$(docker inspect --format '{{range .Mounts}}{{println .Destination}}{{end}}' "$CONTAINER" | sed '/^$/d')"

under_mount() {
  local path="$1" dest
  while IFS= read -r dest; do
    [[ -n "$dest" ]] || continue
    [[ "$path" == "$dest" || "$path" == "$dest"/* ]] && return 0
  done <<< "$MOUNT_DESTS"
  return 1
}

is_known() {
  local path="$1" known
  for known in "${KNOWN_PATHS[@]}"; do
    [[ "$path" == "$known" || "$path" == "$known"/* ]] && return 0
  done
  return 1
}

# Writable-layer entries (added/changed) outside the mounts and outside the
# known list. A directory reported only because something beneath it changed
# (/app, /app/data, a mount point's parent) is skipped: its changed children
# are listed on their own, and copying it would copy the whole image tree.
# Compiled Python is code, not state, and is left out (recorded).
# Reads `docker diff` output on stdin. Each line is "<A|C|D> <path>"; the path
# is everything after the status and its space, so names with spaces survive.
layer_entries() { sed -n 's/^[AC] //p'; }
extra_layer_paths() {
  local all
  all="$(layer_entries | LC_ALL=C sort)"
  printf '%s\n' "$all" | awk '{ p[NR] = $0 }
    END { for (i = 1; i <= NR; i++) { parent = 0
            for (j = 1; j <= NR; j++) if (j != i && index(p[j], p[i] "/") == 1) { parent = 1; break }
            if (!parent) print p[i] } }' |
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in *.pyc|*/__pycache__|*/__pycache__/*) continue ;; esac
    under_mount "$path" && continue
    is_known "$path" && continue
    echo "$path"
  done
}

busy_jobs() {
  [[ "$(inspect '{{.State.Running}}')" == "true" ]] || return 0
  docker top "$CONTAINER" -o pid,args 2>/dev/null | tail -n +2 | grep -E "$BUSY_PATTERN" || true
}

echo "container=$CONTAINER id=$(inspect '{{.Id}}')"
echo "image=$(inspect '{{.Config.Image}}') image_id=$(inspect '{{.Image}}')"
echo "created=$(inspect '{{.Created}}') running=$(inspect '{{.State.Running}}')"

if [[ "$MODE" == "check" ]]; then
  for path in "${KNOWN_PATHS[@]}"; do
    if under_mount "$path"; then
      echo "mounted  $path"
    elif err="$(docker cp "$CONTAINER:$path" - 2>&1 >/dev/null)"; then
      echo "UNMOUNTED $path"
    elif [[ "$err" == *"Could not find the file"* ]]; then
      echo "absent   $path"
    else
      echo "ERROR    $path: ${err:0:200}"
    fi
  done
  extras="$(docker diff "$CONTAINER" | extra_layer_paths)"
  echo "other writable-layer entries outside mounts: $(printf '%s' "$extras" | grep -c . || true)"
  printf '%s\n' "$extras" | sed '/^$/d; s/^/  /' | head -50
  jobs="$(busy_jobs)"
  [[ -z "$jobs" ]] && echo "busy: no Curator job running" || { echo "busy: a Curator job is running:"; echo "$jobs" | sed 's/^/  /'; }
  exit 0
fi

# ---- snapshot ----
jobs="$(busy_jobs)"
if [[ -n "$jobs" ]]; then
  echo "REFUSED: a Curator job is running inside $CONTAINER; retry when it finishes:" >&2
  echo "$jobs" | sed 's/^/  /' >&2
  exit 3
fi

STOPPED=0
DEST=""
on_exit() {
  local status=$?
  if [[ "$STOPPED" == "1" && ( "$status" -ne 0 || "$KEEP_STOPPED" == "0" ) ]]; then
    docker start "$CONTAINER" >/dev/null 2>&1 ||
      echo "WARNING: could not start $CONTAINER again; start it by hand" >&2
  fi
  if [[ "$status" -ne 0 && -n "$DEST" && -d "$DEST.partial" ]]; then
    echo "Snapshot incomplete; left for inspection at $DEST.partial" >&2
  fi
  exit "$status"
}
trap on_exit EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
( umask 077 && mkdir -p "$PRESERVE_ROOT" )
chmod 700 "$PRESERVE_ROOT"
DEST="$PRESERVE_ROOT/$stamp"
[[ ! -e "$DEST" && ! -e "$DEST.partial" ]] || { echo "REFUSED: $DEST already exists" >&2; exit 1; }
WORK="$DEST.partial"
( umask 077 && mkdir -p "$WORK/archives" "$WORK/tree" )

was_running="$(inspect '{{.State.Running}}')"
if [[ "$was_running" == "true" ]]; then
  echo "Stopping $CONTAINER for a coherent copy"
  STOPPED=1
  docker stop --time 30 "$CONTAINER" >/dev/null
fi

# Read the layer after the stop: nothing can change it now. Written to a file
# first so a failing `docker diff` stops the snapshot (set -e) instead of
# yielding an empty list.
docker diff "$CONTAINER" > "$WORK/docker-diff.txt"
mapfile -t EXTRAS < <(extra_layer_paths < "$WORK/docker-diff.txt")

# The writable layer added or changed this path, or something beneath it.
in_layer() {
  local path="$1" entry
  while IFS= read -r entry; do
    [[ "$entry" == "$path" || "$entry" == "$path"/* ]] && return 0
  done < <(layer_entries < "$WORK/docker-diff.txt")
  return 1
}

# Fail closed. A path counts as absent only when Docker answers "Could not find
# the file" AND the writable layer does not show it. Any other failure (disk
# full, I/O, daemon, permissions, an unreadable archive) stops the snapshot:
# the partial copy and Docker's message stay in the .partial folder as
# evidence, nothing is published, and the EXIT trap starts the old container.
copied=0
copy_path() {
  local path="$1" name archive err
  name="$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
  archive="$WORK/archives/$name.tar"
  err="$WORK/archives/$name.cp-error"
  if docker cp "$CONTAINER:$path" - > "$archive.partial" 2> "$err"; then
    if ! tar -tf "$archive.partial" >/dev/null 2>&1; then
      echo "REFUSED: the copy of $path is not a readable archive; kept at $archive.partial" >&2
      exit 1
    fi
    mv "$archive.partial" "$archive"
    rm -f "$err"
  elif grep -q "Could not find the file" "$err" && ! in_layer "$path"; then
    rm -f "$archive.partial" "$err"
    echo "absent $path" >> "$WORK/PATHS"
    return 0
  else
    echo "REFUSED: could not copy $path: $(head -c 300 "$err" | tr '\n' ' ')" >&2
    echo "  partial copy and error kept in $WORK/archives" >&2
    exit 1
  fi
  mkdir -p "$WORK/tree$(dirname "$path")"
  tar -xf "$archive" -C "$WORK/tree$(dirname "$path")"
  echo "copied $path" >> "$WORK/PATHS"
  copied=$((copied + 1))
}

: > "$WORK/PATHS"
for path in "${KNOWN_PATHS[@]}"; do
  if under_mount "$path"; then
    echo "mounted $path" >> "$WORK/PATHS"
  else
    copy_path "$path"
  fi
done
for path in "${EXTRAS[@]}"; do
  [[ -n "$path" ]] && copy_path "$path"
done

(
  cd "$WORK/tree"
  find . -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$WORK/FILES.sha256"
(
  cd "$WORK/archives"
  find . -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$WORK/ARCHIVES.sha256"

compose_sha="absent"
[[ -f "$COMPOSE_FILE" ]] && compose_sha="$(sha256sum "$COMPOSE_FILE" | cut -d' ' -f1)"
{
  echo "taken_at=$stamp"
  echo "container=$CONTAINER"
  echo "container_id=$(inspect '{{.Id}}')"
  echo "image=$(inspect '{{.Config.Image}}')"
  echo "image_id=$(inspect '{{.Image}}')"
  echo "container_created=$(inspect '{{.Created}}')"
  echo "was_running=$was_running"
  echo "compose_file=$COMPOSE_FILE"
  echo "compose_sha256=$compose_sha"
  echo "paths_copied=$copied"
  echo "excluded=compiled Python (*.pyc, __pycache__)"
  echo "mounts:"
  docker inspect --format '{{range .Mounts}}  {{.Type}} {{.Source}} -> {{.Destination}} rw={{.RW}}{{println}}{{end}}' "$CONTAINER" | sed '/^$/d'
} > "$WORK/SNAPSHOT"
chmod -R go-rwx "$WORK"
mv "$WORK" "$DEST"

echo "Curator state preserved: $DEST ($copied paths, $(wc -l < "$DEST/FILES.sha256") files, $(du -sh "$DEST" | cut -f1))"
echo "snapshot_dir=$DEST"
if [[ "$STOPPED" == "1" && "$KEEP_STOPPED" == "1" ]]; then
  echo "$CONTAINER left stopped for replacement."
  STOPPED=0
fi
