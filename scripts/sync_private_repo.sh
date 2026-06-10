#!/bin/bash
# sync_private_repo.sh
# Syncs data/memory/config files from personal-ai-agents to mini-moi-private.
# mini-moi-private never contains code — only data and memory files.
#
# Run manually or wired to a launchd schedule.
# Usage: ./scripts/sync_private_repo.sh [--dry-run]

set -e

MAIN_REPO="/Users/vanstedum/Projects/personal-ai-agents"
PRIVATE_REPO="/Users/vanstedum/Projects/mini-moi-private"
DRY_RUN=false
[[ "$1" == "--dry-run" ]] && DRY_RUN=true

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── Files / directories to sync ───────────────────────────────────────────────
# Format: "src_path_relative_to_main_repo"
# Directory paths sync recursively into same structure in private repo.

SYNC_PATHS=(
    "data/guild/memory/cos_memory.md"
    "data/guild/memory/ops_memory.md"
    "data/guild/memory/devagent_memory.md"
    "data/guild/cos_agenda.json"
    "domains/guild/config/cos_context.json"
    "_working/archive/"
)

# ── Validate private repo ─────────────────────────────────────────────────────
if [[ ! -d "$PRIVATE_REPO/.git" ]]; then
    log "ERROR: Private repo not found at $PRIVATE_REPO"
    log "Run: cd ~/Projects && git clone git@github.com:robertvanstedum/mini-moi-private.git"
    exit 1
fi

cd "$PRIVATE_REPO"
git pull --rebase origin main --quiet 2>/dev/null || true

# ── Copy files ────────────────────────────────────────────────────────────────
CHANGED=0

for src_rel in "${SYNC_PATHS[@]}"; do
    src="$MAIN_REPO/$src_rel"

    if [[ ! -e "$src" ]]; then
        log "SKIP (not found): $src_rel"
        continue
    fi

    dest="$PRIVATE_REPO/$src_rel"
    dest_dir=$(dirname "$dest")

    # For directory paths, rsync the whole tree
    if [[ -d "$src" ]]; then
        mkdir -p "$dest"
        if $DRY_RUN; then
            log "DRY-RUN: rsync $src_rel/"
        else
            rsync -a --delete "$src" "$(dirname "$dest")/" 2>/dev/null
            log "Synced dir: $src_rel"
        fi
        CHANGED=$((CHANGED + 1))
        continue
    fi

    # For individual files, copy only if changed
    mkdir -p "$dest_dir"
    if ! diff -q "$src" "$dest" > /dev/null 2>&1; then
        if $DRY_RUN; then
            log "DRY-RUN: would copy $src_rel"
        else
            cp "$src" "$dest"
            log "Copied: $src_rel"
        fi
        CHANGED=$((CHANGED + 1))
    else
        log "Unchanged: $src_rel"
    fi
done

# ── Commit and push ───────────────────────────────────────────────────────────
if [[ "$CHANGED" -eq 0 ]]; then
    log "Nothing changed — no commit needed"
    exit 0
fi

if $DRY_RUN; then
    log "DRY-RUN: $CHANGED path(s) would be synced — no commit"
    exit 0
fi

cd "$PRIVATE_REPO"
git add -A

if git diff --cached --quiet; then
    log "Git sees no staged changes — nothing to commit"
    exit 0
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "sync: memory + config + archive — $TIMESTAMP"
git push origin main
log "Pushed to mini-moi-private ✅"
