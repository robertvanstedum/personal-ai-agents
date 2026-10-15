#!/bin/bash
# curator_seed_state.sh — fill Curator's new durable host paths from a
# production snapshot taken by curator_preserve.sh (#305).
#
# For each seeded entry in curator_state.map:
#   snapshot has it, host path missing   -> copy it in (staged, then renamed)
#   snapshot has it, host path identical -> keep (already seeded)
#   snapshot has it, host path differs   -> REFUSE: never overwrite, never merge
#   snapshot lacks it, host path present -> keep (already durable)
#   snapshot lacks it, host path missing -> moved: fine (created on first
#                                           write); dir/file: REFUSE
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
[[ -f "$SNAP/SNAPSHOT" && -f "$SNAP/FILES.sha256" && -d "$SNAP/tree" ]] ||
  fail "$SNAP is not a complete curator_preserve.sh snapshot"
( cd "$SNAP/tree" && sha256sum -c --quiet ../FILES.sha256 ) ||
  fail "$SNAP does not match its own manifest"

snap_container="$(sed -n 's/^container_id=//p' "$SNAP/SNAPSHOT")"
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
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

# Check every entry before changing anything, so a refusal leaves the host as it was.
plan=()
while read -r kind old host _target; do
  [[ -z "$kind" || "$kind" == \#* || "$old" == "-" ]] && continue
  src="$SNAP/tree$old"
  dst="$ROOT/$host"
  want="$kind"; [[ "$kind" == moved ]] && want=file
  src_kind="$(kind_of "$src")"
  dst_kind="$(kind_of "$dst")"
  [[ "$src_kind" == missing || "$src_kind" == "$want" ]] ||
    fail "$old in the snapshot is a $src_kind, expected a $want"
  [[ "$dst_kind" == missing || "$dst_kind" == "$want" ]] ||
    fail "$dst is a $dst_kind, expected a $want"
  if [[ "$src_kind" == missing ]]; then
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

for item in "${plan[@]}"; do
  src="${item%%|*}"
  dst="${item#*|}"
  mkdir -p "$(dirname "$dst")"
  stage="$dst.seeding.$$"
  rm -rf -- "$stage"
  cp -a "$src" "$stage"
  same_content "$src" "$stage" || fail "copy of $src into $stage did not verify"
  [[ ! -e "$dst" ]] || fail "$dst appeared while seeding; nothing overwritten (staged copy left at $stage)"
  mv "$stage" "$dst"
done

echo "Curator state seeded from $SNAP (${#plan[@]} new, nothing overwritten)"
