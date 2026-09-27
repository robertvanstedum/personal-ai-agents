#!/bin/bash
# seed.sh — fill $STAGING_ROOT from the existing dev data. COPY, NEVER MOVE.
#
# Usage:
#   seed.sh [--ref REF] [--queue-ref REF]   file data (default mode)
#   seed.sh --postgres                      copy the dev Postgres volume
#   seed.sh --docs-only                     refresh docs/specs + docs/design
#                                           from the pinned RELEASE commit
#   seed.sh --drift                         list source files changed since
#                                           the seed (a missed writer)
#
# Every step reads a source and writes only under $STAGING_ROOT (or into the
# new staging volumes). No source is moved, deleted or modified: this script
# never calls mv, rm -r or rsync --delete (tests/test_staging_environment.py).
#
# File mode refuses a non-empty target. It writes:
#   $STAGING_ROOT/SEEDED_FROM.txt      each target, its source, mtime, digest
#   $STAGING_ROOT/SHA256SUMS.sources   per-file digests of every copied source
#   $STAGING_ROOT/SHA256SUMS.seed      per-file digests of the seeded tree
#
# --ref (default origin/main) supplies the tracked German/Portuguese base data,
# the docs and cos_context.json; --queue-ref (default origin/main) supplies
# data/guild/build_queue.json. No fetch is done here (build.sh fetches).
#
# --postgres copies personal-ai-agents_postgres-data into the external volume
# minimoi-staging-postgres-data ONLY while no container uses the source, then
# proves the copy with a digest of both, and creates the two fresh Agent A
# volumes. The source volume is mounted read-only and kept as the rollback.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MODE=files
REF=origin/main
QUEUE_REF=origin/main
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift ;;
    --queue-ref) QUEUE_REF="${2:?--queue-ref needs a value}"; shift ;;
    --postgres) MODE=postgres ;;
    --docs-only) MODE=docs ;;
    --drift) MODE=drift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
require_absolute_root
S="$STAGING_ROOT"

# ── sources (override any of them through the environment) ──────────────────
MAIN_CHECKOUT=$(git -C "$STAGING_SCRIPTS_DIR" worktree list --porcelain | sed -n '1s/^worktree //p')
R="${SEED_SOURCE_REPO:-$MAIN_CHECKOUT}"
GERMAN_STATE="${SEED_GERMAN_STATE:-$HOME/minimoi-german-state}"
PORTUGUESE_STATE="${SEED_PORTUGUESE_STATE:-$HOME/minimoi-portuguese-state}"
COS_MEMORY="${SEED_COS_MEMORY:-$HOME/.worktrees/dev-portal-release-v2/data/cos_memory.md}"
RECEIPTS="${SEED_RECEIPTS:-$HOME/.codex/worktrees/2696/personal-ai-agents/data/model_gateway_receipts.jsonl}"
PG_SOURCE_VOLUME="${SEED_PG_SOURCE_VOLUME:-personal-ai-agents_postgres-data}"
PG_TARGET_VOLUME="minimoi-staging-postgres-data"
UTILITY_IMAGE="${SEED_UTILITY_IMAGE:-postgres:latest}"   # local; never pulled

SEEDED_FROM="$S/SEEDED_FROM.txt"
SOURCE_SUMS="$S/SHA256SUMS.sources"

mtime() { date -r "$1" +%Y-%m-%dT%H:%M:%S; }
sha_file() { shasum -a 256 "$1" | cut -d' ' -f1; }

# Per-file digests of a source (file or tree), absolute paths, appended.
record_source_sums() {
  local src="$1"
  if [[ -d "$src" ]]; then
    find "$src" -type f -print0 | LC_ALL=C sort -z | xargs -0 -r shasum -a 256 >> "$SOURCE_SUMS"
  else
    shasum -a 256 "$src" >> "$SOURCE_SUMS"
  fi
}

tree_digest() {
  ( cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r shasum -a 256 | shasum -a 256 | cut -d' ' -f1 )
}

record() {   # record TARGET SOURCE KIND
  local target="$1" src="$2" kind="$3" digest=""
  if [[ "$kind" == git:* ]]; then
    printf '%-44s %s  (%s)\n' "$target" "$src" "$kind" >> "$SEEDED_FROM"
    return
  fi
  if [[ -d "$src" ]]; then digest="tree:$(tree_digest "$src")"; else digest="sha256:$(sha_file "$src")"; fi
  printf '%-44s %s  (%s, mtime %s, %s)\n' "$target" "$src" "$kind" "$(mtime "$src")" "$digest" >> "$SEEDED_FROM"
  record_source_sums "$src"
}

copy_dir() {   # copy_dir SOURCE TARGET_REL [optional]
  local src="$1" rel="$2" optional="${3:-}"
  if [[ ! -d "$src" ]]; then
    [[ -n "$optional" ]] || die "required source folder missing: $src"
    note "optional source $src is absent; creating an empty $rel"
    mkdir -p "$S/$rel"
    printf '%-44s (absent: %s; created empty)\n' "$rel" "$src" >> "$SEEDED_FROM"
    return
  fi
  cp -Rp "$src" "$S/$rel"
  record "$rel" "$src" copy
}

copy_file() {  # copy_file SOURCE TARGET_REL
  local src="$1" rel="$2"
  [[ -f "$src" ]] || die "required source file missing: $src"
  cp -pL "$src" "$S/$rel"
  record "$rel" "$src" copy
}

git_tree() {   # git_tree REF:PATH TARGET_REL — extract a tracked tree
  local spec="$1" rel="$2"
  mkdir -p "$S/$rel"
  git -C "$R" archive "$spec" | tar -x -C "$S/$rel"
  record "$rel" "$spec" "git:$(git -C "$R" rev-parse "${spec%%:*}")"
}

git_file() {   # git_file REF:PATH TARGET_REL
  local spec="$1" rel="$2"
  git -C "$R" show "$spec" > "$S/$rel"
  record "$rel" "$spec" "git:$(git -C "$R" rev-parse "${spec%%:*}")"
}

overlay() {    # overlay SOURCE TARGET_REL — rsync that adds and updates, never deletes
  local src="$1" rel="$2"
  if [[ ! -d "$src" ]]; then
    note "no overlay source $src; $rel keeps the tracked base only"
    printf '%-44s (overlay absent: %s)\n' "$rel" "$src" >> "$SEEDED_FROM"
    return
  fi
  rsync -a "$src/" "$S/$rel/"
  record "$rel (overlay)" "$src" overlay
}

seed_files() {
  [[ -d "$R/.git" || -f "$R/.git" ]] || die "source repository not found: $R"
  local sha queue_sha
  sha=$(git -C "$R" rev-parse --verify "$REF^{commit}") || die "unknown ref $REF"
  queue_sha=$(git -C "$R" rev-parse --verify "$QUEUE_REF^{commit}") || die "unknown ref $QUEUE_REF"
  local part
  for part in data auth docs agent_logs cos_memory.md; do
    if [[ -e "$S/$part" ]] && [[ -n "$(ls -A "$S/$part" 2>/dev/null)" || -f "$S/$part" ]]; then
      die "target $S/$part is not empty; refusing to seed over existing staging data"
    fi
  done

  umask 077
  mkdir -p "$S"
  chmod 700 "$S"
  mkdir -p "$S"/{data/guild,data/german,data/portuguese,auth,docs/specs,docs/design,config,logs,state,rollback}
  {
    echo "# Staging seed, $(date -u +%Y-%m-%dT%H:%M:%SZ). Copy, never move."
    echo "# source repository: $R"
    echo "# base ref: $REF = $sha"
    echo "# queue ref: $QUEUE_REF = $queue_sha"
    echo "# Stale or duplicate copies left untouched (no runtime reader after cutover):"
    echo "#   $R/domains/german/data, $R/domains/portuguese/data, $R/data/guild/build_queue.json,"
    echo "#   $R/data/cos_memory.md, $R/data/model_gateway_receipts.jsonl,"
    echo "#   ~/.worktrees/dev-portal-release-v2/data/guild/build_queue.json, Codex worktree 2696 copies"
  } > "$SEEDED_FROM"
  : > "$SOURCE_SUMS"

  # Curator
  copy_dir "$R/data/curator" data/curator
  copy_dir "$R/curator_archive" data/curator_archive optional
  copy_file "$R/curator_history.json" data/curator_history.json
  copy_file "$R/curator_costs.json" data/curator_costs.json
  copy_dir "$R/interests" data/interests
  copy_dir "$R/_NewDomains/research-intelligence/data" data/research-intelligence optional

  # German and Portuguese: tracked base, then the live dev state on top
  git_tree "$sha:domains/german/data" data/german
  overlay "$GERMAN_STATE" data/german
  git_tree "$sha:domains/portuguese/data" data/portuguese
  overlay "$PORTUGUESE_STATE" data/portuguese

  # Guild: the live queue folder (portal) and the CoS context
  git_file "$queue_sha:data/guild/build_queue.json" data/guild/build_queue.json
  /usr/bin/python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d,list) and d; print(f"staging: queue seeded with {len(d)} items")' \
    "$S/data/guild/build_queue.json" || die "seeded build_queue.json is not a non-empty JSON list"
  git_file "$sha:domains/guild/config/cos_context.json" data/guild/cos_context.json

  # CoS memory and the gateway receipts ledger
  if [[ -f "$COS_MEMORY" ]]; then
    copy_file "$COS_MEMORY" cos_memory.md
  else
    note "cos_memory source $COS_MEMORY absent; using $R/data/cos_memory.md"
    copy_file "$R/data/cos_memory.md" cos_memory.md
  fi
  if [[ -f "$RECEIPTS" ]]; then
    copy_file "$RECEIPTS" data/model_gateway_receipts.jsonl
  else
    note "receipts source $RECEIPTS absent; starting an empty ledger"
    : > "$S/data/model_gateway_receipts.jsonl"
    printf '%-44s (absent: %s; created empty)\n' data/model_gateway_receipts.jsonl "$RECEIPTS" >> "$SEEDED_FROM"
  fi

  # Portal sign-in and published docs
  copy_file "$R/minimoi_portal/auth/users.json" auth/users.json
  copy_file "$R/minimoi_portal/auth/guests.json" auth/guests.json
  git_tree "$sha:docs/specs" docs/specs
  git_tree "$sha:docs/design" docs/design
  copy_dir "$R/agent_logs" agent_logs optional

  ( cd "$S" && find ./data ./auth ./docs ./agent_logs ./cos_memory.md -type f -print0 \
      | LC_ALL=C sort -z | xargs -0 -r shasum -a 256 ) > "$S/SHA256SUMS.seed"
  note "seeded $S ($(wc -l < "$S/SHA256SUMS.seed" | tr -d ' ') files); see SEEDED_FROM.txt"
  note "next: scripts/staging/seed.sh --postgres once the old dev postgres is stopped"
}

volume_digest() {
  docker run --rm --pull never --network none -v "$1:/d:ro" --entrypoint sh "$UTILITY_IMAGE" \
    -c 'cd /d && find . -type f -exec sha256sum {} + | LC_ALL=C sort -k2 | sha256sum | cut -d" " -f1'
}

seed_postgres() {
  docker image inspect "$UTILITY_IMAGE" >/dev/null 2>&1 || die "local image $UTILITY_IMAGE missing (never pulled)"
  docker volume inspect "$PG_SOURCE_VOLUME" >/dev/null 2>&1 || die "source volume $PG_SOURCE_VOLUME not found"
  local users
  users=$(docker ps -q --filter "volume=$PG_SOURCE_VOLUME")
  [[ -z "$users" ]] || die "a running container still uses $PG_SOURCE_VOLUME; stop the old dev postgres first (README step 1)"
  if docker volume inspect "$PG_TARGET_VOLUME" >/dev/null 2>&1; then
    local count
    count=$(docker run --rm --pull never --network none -v "$PG_TARGET_VOLUME:/to:ro" --entrypoint sh "$UTILITY_IMAGE" \
      -c 'ls -A /to | wc -l')
    [[ "$count" -eq 0 ]] || die "$PG_TARGET_VOLUME already holds data; refusing to copy over it"
  else
    docker volume create --label minimoi.staging=1 "$PG_TARGET_VOLUME" >/dev/null
  fi
  note "copying $PG_SOURCE_VOLUME -> $PG_TARGET_VOLUME (source mounted read-only)"
  docker run --rm --pull never --network none \
    -v "$PG_SOURCE_VOLUME:/from:ro" -v "$PG_TARGET_VOLUME:/to" \
    --entrypoint sh "$UTILITY_IMAGE" -c 'cp -a /from/. /to/'
  local src_digest dst_digest
  src_digest=$(volume_digest "$PG_SOURCE_VOLUME")
  dst_digest=$(volume_digest "$PG_TARGET_VOLUME")
  mkdir -p "$S/state"
  printf '%-44s volume %s  (copy, %s, tree:%s)\n' "volume $PG_TARGET_VOLUME" "$PG_SOURCE_VOLUME" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$src_digest" >> "$SEEDED_FROM"
  [[ "$src_digest" == "$dst_digest" ]] || die "postgres copy digest mismatch (source $src_digest, copy $dst_digest)"
  note "postgres copy verified: $src_digest"
  local v
  for v in minimoi-staging-cos-agent-a-state minimoi-staging-cos-agent-a-auth; do
    docker volume inspect "$v" >/dev/null 2>&1 || docker volume create --label minimoi.staging=1 "$v" >/dev/null
    note "volume $v ready (fresh; filled from the image on first mount)"
  done
}

seed_docs() {
  [[ -f "$STAGING_RELEASE_FILE" ]] || die "no RELEASE file; run build.sh first"
  local sha
  sha=$(sed -n 's/^sha=//p' "$STAGING_RELEASE_FILE")
  [[ -n "$sha" ]] || die "RELEASE has no sha"
  mkdir -p "$S/docs/specs" "$S/docs/design"
  # Like scripts/sync_docs.sh on EC2: add and update files, delete nothing.
  git -C "$R" archive "$sha:docs/specs" | tar -x -C "$S/docs/specs"
  git -C "$R" archive "$sha:docs/design" | tar -x -C "$S/docs/design"
  printf '%-44s refreshed from %s (%s)\n' "docs/specs docs/design" "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$SEEDED_FROM"
  note "docs refreshed from $sha"
}

seed_drift() {
  [[ -f "$SOURCE_SUMS" ]] || die "no $SOURCE_SUMS; nothing was seeded"
  /usr/bin/python3 - "$SOURCE_SUMS" "$SEEDED_FROM" <<'PY'
import hashlib, os, re, sys
sums_path, seeded_path = sys.argv[1], sys.argv[2]
seeded = {}
for line in open(sums_path, encoding="utf-8"):
    digest, path = line.rstrip("\n").split("  ", 1)
    seeded[path] = digest
roots = []
for line in open(seeded_path, encoding="utf-8"):
    m = re.match(r"^\S.*?\s(/\S+)\s+\((copy|overlay)", line)
    if m:
        roots.append(m.group(1))
def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
current = {}
for root in roots:
    if os.path.isdir(root):
        for base, _dirs, files in os.walk(root):
            for name in files:
                p = os.path.join(base, name)
                current[p] = digest(p)
    elif os.path.isfile(root):
        current[root] = digest(root)
changed = sorted(p for p in seeded if p in current and current[p] != seeded[p])
removed = sorted(p for p in seeded if p not in current)
added = sorted(p for p in current if p not in seeded)
for label, items in (("changed", changed), ("added", added), ("removed", removed)):
    for p in items:
        print(f"{label}: {p}")
total = len(changed) + len(added) + len(removed)
print(f"drift: {len(changed)} changed, {len(added)} added, {len(removed)} removed since the seed")
sys.exit(1 if total else 0)
PY
}

case "$MODE" in
  files) seed_files ;;
  postgres) seed_postgres ;;
  docs) seed_docs ;;
  drift) seed_drift ;;
esac
