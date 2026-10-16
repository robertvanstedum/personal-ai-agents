#!/bin/bash
# curator_bind_preflight.sh — refuse to create the Curator container unless
# every bind source in curator_state.map exists with the right type (#305,
# the Curator part of #179).
#
# A missing bind source would make Docker create an empty directory in its
# place: the app would start on nothing and later writes would land beside,
# not in, the real data. A file source that became a directory is the same
# failure. Run after seeding, before `up`.
#
# Env: MINIMOI_ROOT (default /opt/minimoi), CURATOR_STATE_MAP (default: beside
#      this script)

set -euo pipefail

ROOT="${MINIMOI_ROOT:-/opt/minimoi}"
MAP="${CURATOR_STATE_MAP:-$(dirname "$0")/curator_state.map}"
[[ -f "$MAP" ]] || { echo "preflight FAILED: state map $MAP missing" >&2; exit 1; }

problems=0
while read -r kind _old host _target; do
  [[ -z "$kind" || "$kind" == \#* || "$kind" == moved ]] && continue
  path="$ROOT/$host"
  if [[ -L "$path" ]]; then
    echo "preflight: $path is a symlink, expected a real $kind" >&2; problems=$((problems + 1))
  elif [[ ! -e "$path" ]]; then
    echo "preflight: $path is missing (Docker would mount an empty directory)" >&2; problems=$((problems + 1))
  elif [[ ( "$kind" == dir || "$kind" == appdir ) && ! -d "$path" ]]; then
    echo "preflight: $path should be a directory" >&2; problems=$((problems + 1))
  elif [[ "$kind" == file && ! -f "$path" ]]; then
    echo "preflight: $path should be a regular file" >&2; problems=$((problems + 1))
  fi
done < "$MAP"

if (( problems > 0 )); then
  echo "Curator bind preflight FAILED ($problems problem(s)); container not created." >&2
  exit 1
fi
echo "Curator bind preflight passed."
