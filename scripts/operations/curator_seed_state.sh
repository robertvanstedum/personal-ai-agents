#!/bin/bash
# curator_seed_state.sh — fill Curator's new durable host paths from a
# production snapshot taken by curator_preserve.sh (#305).
#
# For each seeded entry in curator_state.map:
#   snapshot has it, host path missing   -> copy it in (staged, then renamed)
#   snapshot has it, host path identical -> keep (already seeded)
#   snapshot has it, host path differs   -> REFUSE: never overwrite, never merge
#   (a path the snapshot did not record as copied, absent or mounted, or
#    recorded as copied but missing from its tree -> REFUSE)
#   snapshot lacks it, host path present -> keep (already durable)
#   snapshot lacks it, host path missing -> moved: fine (created on first
#                                           write); appdir recorded
#                                           "absent": create it empty (the
#                                           container had none, so nothing is
#                                           hidden); dir/file: REFUSE
#   wrong type or a symlink on either side -> REFUSE
#
# Content comes only from the snapshot: never from dev, the image or the repo.
# The snapshot is re-verified against its own SHA-256 manifest first, and must
# come from the container that is about to be replaced.
#
# Usage: curator_seed_state.sh SNAPSHOT_DIR
# Env:   MINIMOI_ROOT (default /opt/minimoi), CURATOR_CONTAINER (default
#        minimoi-curator), CURATOR_STATE_MAP (default: beside this script)

set -euo pipefail

SNAP="${1:?usage: curator_seed_state.sh SNAPSHOT_DIR}"
ROOT="${MINIMOI_ROOT:-/opt/minimoi}"
CONTAINER="${CURATOR_CONTAINER:-minimoi-curator}"
MAP="${CURATOR_STATE_MAP:-$(dirname "$0")/curator_state.map}"

fail() { echo "seed REFUSED: $*" >&2; exit 1; }

[[ -f "$MAP" ]] || fail "state map $MAP missing"
[[ -f "$SNAP/SNAPSHOT" && -f "$SNAP/FILES.sha256" && -f "$SNAP/PATHS" && -d "$SNAP/tree" ]] ||
  fail "$SNAP is not a complete curator_preserve.sh snapshot"
[[ ! -L "$SNAP/tree" && ! -L "$SNAP/FILES.sha256" ]] ||
  fail "snapshot tree or manifest is a symlink"

# sha256sum -c rejects a legitimate zero-file manifest. Such a manifest is
# valid only for a tree holding directories alone; never ignore unlisted files,
# symlinks or special entries. Nonempty manifests retain strict checksum checks.
if [[ -s "$SNAP/FILES.sha256" ]]; then
  ( cd "$SNAP/tree" && sha256sum -c --quiet ../FILES.sha256 ) ||
    fail "$SNAP does not match its own manifest"
else
  unexpected="$(find "$SNAP/tree" -mindepth 1 ! -type d -print -quit)" ||
    fail "could not inspect empty snapshot tree"
  [[ -z "$unexpected" ]] || fail "empty manifest has unlisted snapshot entries"
fi

# Exact-name lookup in a successful container listing. A Docker or daemon
# error stops the script: it is never read as "no container" (#305 review).
container_exists() {
  local names
  names="$(docker ps -a --format '{{.Names}}')" || { echo "REFUSED: could not list Docker containers" >&2; exit 1; }
  grep -qxF -- "$1" <<< "$names"
}

snap_container="$(sed -n 's/^container_id=//p' "$SNAP/SNAPSHOT")"
if container_exists "$CONTAINER"; then
  live_container="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
  [[ "$snap_container" == "$live_container" ]] ||
    fail "snapshot is from container ${snap_container:0:12}, not the current ${live_container:0:12}"
fi

kind_of() {
  if [[ -L "$1" ]]; then echo symlink
  elif [[ -d "$1" ]]; then echo dir
  elif [[ -f "$1" ]]; then echo file
  elif [[ -e "$1" ]]; then echo other
  else echo missing
  fi
}

same_content() {
  if [[ -d "$1" ]]; then diff -rq "$1" "$2" >/dev/null 2>&1
  else cmp -s "$1" "$2"
  fi
}

# research-intelligence/topics stays unmounted on purpose: if the folder
# exists, research sessions are written there instead of the durable
# data/threads. A snapshot keeps it as evidence, but recreation would drop the
# live folder, so a release cannot proceed while it exists (#305 review).
for TOPICS in /app/_NewDomains/research-intelligence/topics /app/research-state/topics; do
  if grep -F -x -q "copied $TOPICS" "$SNAP/PATHS"; then
    fail "$TOPICS exists in the running container; its sessions would not stay live after recreation. Preserve it live (a reviewed change) before releasing Curator"
  fi
done

# What the snapshot itself recorded for a path: copied, absent or mounted.
# No line at all prints nothing (and must not trip set -e/pipefail).
recorded() { { grep -F -x -e "copied $1" -e "absent $1" -e "mounted $1" "$SNAP/PATHS" || true; } | head -1 | cut -d' ' -f1; }

# Check every entry before changing anything, so a refusal leaves the host as it was.
plan=()
while read -r kind old host _target; do
  [[ -z "$kind" || "$kind" == \#* || "$old" == "-" ]] && continue
  src="$SNAP/tree$old"
  dst="$ROOT/$host"
  want="$kind"; [[ "$kind" == moved ]] && want=file; [[ "$kind" == appdir ]] && want=dir
  src_kind="$(kind_of "$src")"
  dst_kind="$(kind_of "$dst")"
  record="$(recorded "$old")"
  [[ -n "$record" ]] || fail "the snapshot does not account for $old"
  if [[ "$record" == copied && "$src_kind" == missing ]]; then
    fail "the snapshot says it copied $old but its tree does not hold it"
  fi
  if [[ "$record" != copied && "$src_kind" != missing ]]; then
    fail "the snapshot records $old as $record but its tree holds it"
  fi
  [[ "$src_kind" == missing || "$src_kind" == "$want" ]] ||
    fail "$old in the snapshot is a $src_kind, expected a $want"
  [[ "$dst_kind" == missing || "$dst_kind" == "$want" ]] ||
    fail "$dst is a $dst_kind, expected a $want"
  if [[ "$src_kind" == missing ]]; then
    if [[ "$dst_kind" == missing && "$kind" == appdir && "$record" == absent ]]; then
      plan+=("-|$dst")
      echo "create  $dst (empty; the container had no $old)"
      continue
    fi
    if [[ "$dst_kind" == missing && "$kind" != moved ]]; then
      fail "$old is not in the snapshot and $dst does not exist; nothing to mount"
    fi
    echo "keep    $dst (not in snapshot)"
  elif [[ "$dst_kind" == missing ]]; then
    plan+=("$src|$dst")
    echo "seed    $dst <- snapshot $old"
  elif same_content "$src" "$dst"; then
    echo "same    $dst"
  else
    fail "$dst already exists and differs from the snapshot's $old; resolve by hand, nothing was changed"
  fi
done < "$MAP"

# Bash 3.2 with nounset treats an empty array expansion as unset.
# A verified empty plan is an idempotent no-op, not a reason to seed anything.
if (( ${#plan[@]} )); then
  for item in "${plan[@]}"; do
    src="${item%%|*}"
    dst="${item#*|}"
    mkdir -p "$(dirname "$dst")"
    if [[ "$src" == "-" ]]; then
      mkdir "$dst"
      continue
    fi
    stage="$dst.seeding.$$"
    rm -rf -- "$stage"
    cp -a "$src" "$stage"
    same_content "$src" "$stage" || fail "copy of $src into $stage did not verify"
    [[ ! -e "$dst" ]] || fail "$dst appeared while seeding; nothing overwritten (staged copy left at $stage)"
    mv "$stage" "$dst"
  done
fi

echo "Curator state seeded from $SNAP (${#plan[@]} new, nothing overwritten)"
