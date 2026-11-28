#!/usr/bin/env bash
# Starts one automatic UAT run after each staging deploy (SPEC_AUTOMATIC_UAT v0.1 §5). Staging is deployed by
# hand-run image swaps, so this watches the host itself. Cron, every 5 minutes, on the staging host:
#
#   */5 * * * * /opt/minimoi-staging/repo/scripts/uat/uat_watch.sh >> /opt/minimoi-staging/uat/watch.log 2>&1
#
# * It hashes the sorted list of running container names and image ids (the UAT's own container excluded).
# * When that hash differs from the last tested one and has stayed the same for 3 minutes, it runs
#   run_staging_uat.sh under flock (no overlapping runs).
# * If the containers changed during the run, the run is marked STALE and the hash is not recorded as tested, so the
#   next tick starts a fresh run once the set settles.
# * It keeps the last 20 runs.
# * Optional: with GH_TOKEN set and gh installed, it posts report.md as a comment on the open PR whose title starts
#   with "Release". Without them it skips quietly.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UAT_ROOT="${UAT_ROOT:-/opt/minimoi-staging/uat}"
STATE_DIR="$UAT_ROOT/.state"
LOCK="${UAT_LOCK:-$UAT_ROOT/.uat.lock}"
STABLE_S="${UAT_STABLE_SECONDS:-180}"
KEEP="${UAT_KEEP_RUNS:-20}"
GH_REPO="${UAT_GH_REPO:-robertvanstedum/personal-ai-agents}"

mkdir -p "$STATE_DIR"

container_hash() {
  # name + image id of every running container, sorted; the UAT runner's own container is left out
  docker ps -q | xargs -r docker inspect --format '{{.Name}} {{.Image}}' \
    | grep -v '^/minimoi-uat-' | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}

read_state() { cat "$STATE_DIR/$1" 2>/dev/null || true; }
write_state() { printf '%s\n' "$2" > "$STATE_DIR/$1.tmp" && mv "$STATE_DIR/$1.tmp" "$STATE_DIR/$1"; }

now=$(date -u +%s)
cur=$(container_hash)
seen=$(read_state seen_hash)
tested=$(read_state tested_hash)

if [ "$cur" != "$seen" ]; then
  write_state seen_hash "$cur"
  write_state seen_since "$now"
  exit 0                                                     # changed just now: wait for it to settle
fi
[ "$cur" = "$tested" ] && exit 0                             # this set was already tested
since=$(read_state seen_since); since=${since:-$now}
[ $((now - since)) -lt "$STABLE_S" ] && exit 0               # not stable for long enough yet

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
echo "$(date -u +%FT%TZ) containers changed and settled ($cur); starting UAT run $RUN_ID"
set +e
UAT_RUN_ID="$RUN_ID" UAT_LOCK_HELD=1 flock -n -E 75 "$LOCK" "$HERE/run_staging_uat.sh"
rc=$?
set -e
if [ "$rc" -eq 75 ]; then
  echo "another UAT run holds the lock; trying again next tick"; exit 0
fi
OUT="$UAT_ROOT/$RUN_ID"

after=$(container_hash)
if [ "$after" = "$cur" ]; then
  write_state tested_hash "$cur"
  echo "$cur" > "$OUT/containers_hash" 2>/dev/null || true
else
  echo "containers changed during run $RUN_ID: marked STALE; a new run follows once they settle"
  echo "containers changed during the run ($cur → $after)" > "$OUT/STALE" 2>/dev/null || true
  write_state seen_hash "$after"
  write_state seen_since "$(date -u +%s)"
fi

# keep the last $KEEP runs (run ids sort by time)
ls -1d "$UAT_ROOT"/20[0-9][0-9][01][0-9][0-3][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z 2>/dev/null \
  | LC_ALL=C sort | head -n -"$KEEP" | xargs -r rm -rf --

# optional: the report as a comment on the open release PR
# The repo is PUBLIC: never post agent replies or evidence. Only the pass/fail table (everything before the first
# "## " section after the table) goes out, and only when UAT_POST_PR=1 is set explicitly.
if [ "${UAT_POST_PR:-0}" = "1" ] && [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1 && [ -s "$OUT/report.md" ] && [ ! -e "$OUT/STALE" ]; then
  awk 'NR>1 && /^## /{exit} {print}' "$OUT/report.md" > "$OUT/summary_public.md"
  printf '\n_Automatic staging UAT. Evidence stays on the staging host: %s_\n' "$OUT" >> "$OUT/summary_public.md"
  pr=$(gh pr list --repo "$GH_REPO" --state open --json number,title \
        --jq '[.[] | select(.title | startswith("Release"))][0].number // empty' 2>/dev/null || true)
  if [ -n "$pr" ]; then
    gh pr comment "$pr" --repo "$GH_REPO" --body-file "$OUT/summary_public.md" >/dev/null 2>&1 \
      && echo "report posted on PR #$pr" || echo "could not post the report on PR #$pr"
  fi
fi
exit 0
