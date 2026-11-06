#!/bin/bash
# MiniMoi 1.2's backup before production's first 1.2 change, and a proof that it restores. Nothing here changes production's
# data. Prints names, sizes and sha256 only, never a value (`set -x` stays off: the archives hold .env and mc.env).
#
#   minimoi_1_2_backup.sh backup <folder>
#       Both databases (pg_dump -Fc), the host files, and MC's four volumes AT REST.
#       WARNING: MC is stopped while its volumes are read (up to 10 minutes): MC chat is down. It is started again even
#       when a copy fails. Everything else keeps running.
#   minimoi_1_2_backup.sh restore-proof <folder>
#       Restores personal_agents into a throwaway Postgres with no network, on the exact image the live database runs,
#       and compares row counts with the live database. Unpacks files.tgz in the folder and diffs auth/ with the live
#       one. Removes both. Live data is only read.
#
# <folder> must be new or empty for backup; it is made 0700, and every file in it 0600. receipt.txt lists sizes and sha256.
# Runs on the host alone: this one file. Copy off the host is a separate step: aws s3 cp <folder> s3://minimoi-backups/...
set -euo pipefail
umask 077

ROOT="${MINIMOI_ROOT:-/opt/minimoi}"
POSTGRES="${MINIMOI_BACKUP_POSTGRES:-postgres-ai-agents}"
MC="${MINIMOI_BACKUP_MC:-minimoi-mc-agent}"
RESTORE="${MINIMOI_BACKUP_RESTORE_NAME:-minimoi-1-2-restore-proof}"
DATABASES=(personal_agents litellm_keys)
# docker-compose.mc.prod.yml's named volumes.
MC_VOLUMES=(minimoi-mc-state minimoi-mc-auth minimoi-mc-claude-home minimoi-mc-relay-state)
# Host paths as docker-compose.prod.yml binds them; cos_memory.md is a file beside cos/. Missing ones are named in the receipt.
FILES=(data auth cos cos_memory.md cos_reminders cos_voice mc/portal mc/mc.env .env gateway-keys.env secrets/1-2 state/1-2)
# The tables a rollback most needs. A table the old code hasn't got yet reads "absent" on both sides.
PROOF_TABLES=(guild.mc_delegations guild.floor_messages guild.floor_postits guild.floor_board_meta)

die() { echo "minimoi-backup: REFUSED: $*" >&2; exit 2; }
log() { echo "minimoi-backup: $*"; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }

receipt() { # receipt <name>: one line with its size and hash.
  printf '%s size=%s sha256=%s\n' "$1" "$(wc -c < "$OUT/$1" | tr -d ' ')" "$(sha "$OUT/$1")" | tee -a "$OUT/receipt.txt"
}

folder() {
  [[ -n "${1:-}" && "$1" == /* ]] || die "give an absolute folder"
  [[ ! -L "$1" ]] || die "$1 is a symlink"
  OUT="$1"
}

mc_started=1
restart_mc() {
  if [[ "$mc_started" == 0 ]]; then
    docker start "$MC" >/dev/null || echo "minimoi-backup: MC DID NOT START AGAIN: docker start $MC" >&2
    mc_started=1
  fi
}

backup() {
  folder "${1:-}"
  if [[ -d "$OUT" && -n "$(ls -A "$OUT")" ]]; then die "$OUT is not empty: a backup is never written over another"; fi
  install -d -m 0700 "$OUT"
  [[ "$(docker inspect --format '{{.State.Running}}' "$POSTGRES" 2>/dev/null)" == true ]] || die "$POSTGRES is not running"
  docker inspect --format '{{.State.Status}}' "$MC" >/dev/null 2>&1 || die "$MC is not there"
  local helper; helper=$(docker inspect --format '{{.Image}}' "$POSTGRES")      # has tar; nothing is pulled
  : > "$OUT/receipt.txt"
  echo "postgres_image=$helper" >> "$OUT/receipt.txt"

  # Counted just before the dump: the restore must hold at least these rows, and no more than live has by then.
  local table
  for table in "${PROOF_TABLES[@]}"; do
    echo "rows_at_dump $table $(count "$POSTGRES" personal_agents "$table")" >> "$OUT/receipt.txt"
  done
  local db
  for db in "${DATABASES[@]}"; do
    docker exec "$POSTGRES" pg_dump -U postgres -Fc "$db" > "$OUT/$db.dump"
    [[ -s "$OUT/$db.dump" ]] || die "the dump of $db is empty"
    receipt "$db.dump"
  done

  local present=() item
  for item in "${FILES[@]}"; do
    if [[ -e "$ROOT/$item" ]]; then present+=("$item"); else echo "not_on_this_host=$item" >> "$OUT/receipt.txt"; fi
  done
  tar -czf "$OUT/files.tgz" -C "$ROOT" "${present[@]}"
  receipt files.tgz

  log "stopping $MC to read its volumes at rest: MC chat is down until this finishes (up to 10 minutes)"
  trap restart_mc EXIT
  if [[ "$(docker inspect --format '{{.State.Running}}' "$MC")" == true ]]; then   # a stopped MC stays stopped afterwards
    docker stop --time 120 "$MC" >/dev/null
    mc_started=0
  fi
  local volume
  for volume in "${MC_VOLUMES[@]}"; do
    docker volume inspect "$volume" >/dev/null 2>&1 || die "volume $volume is not there"
    docker run --rm --network none -v "$volume:/v:ro" --entrypoint tar "$helper" -czf - -C /v . > "$OUT/$volume.tgz"
    receipt "$volume.tgz"
  done
  restart_mc
  trap - EXIT
  chmod 0600 "$OUT"/*
  log "done. Receipt: $OUT/receipt.txt. Now run: $0 restore-proof $OUT"
}

count() { # count <container> <database> <table>: rows, or absent.
  docker exec "$1" psql -U postgres -d "$2" -Atc \
    "SELECT CASE WHEN to_regclass('$3') IS NULL THEN 'absent' END" | grep -qx absent && { echo absent; return; }
  docker exec "$1" psql -U postgres -d "$2" -Atc "SELECT count(*) FROM $3"
}

remove_restore() { docker rm -f "$RESTORE" >/dev/null 2>&1 || true; }

restore_proof() {
  folder "${1:-}"
  [[ -f "$OUT/personal_agents.dump" && -f "$OUT/files.tgz" && -f "$OUT/receipt.txt" ]] || die "$OUT holds no finished backup"
  local proof="$OUT/restore-proof.txt" image failed=0 item
  : > "$proof"
  image=$(docker inspect --format '{{.Image}}' "$POSTGRES")
  [[ "$(sed -n 's/^postgres_image=//p' "$OUT/receipt.txt")" == "$image" ]] \
    || echo "note=the live database's image changed since this backup" | tee -a "$proof"
  # Any recorded hash that no longer matches stops here, before the dump is trusted.
  local name recorded
  while read -r name _ recorded; do
    [[ "$recorded" == sha256=* ]] || continue
    [[ "$(sha "$OUT/$name")" == "${recorded#sha256=}" ]] || die "$name no longer matches its receipt"
  done < "$OUT/receipt.txt"

  remove_restore
  trap remove_restore EXIT
  # No network, trust inside this one container only: nothing can reach it, and it exists for this comparison.
  docker run -d --name "$RESTORE" --network none -e POSTGRES_HOST_AUTH_METHOD=trust "$image" >/dev/null
  local i
  for i in $(seq 1 60); do
    docker exec "$RESTORE" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
    [[ "$i" != 60 ]] || die "the throwaway Postgres did not start"
  done
  docker exec "$RESTORE" createdb -U postgres r
  # pg_restore reports ownership of roles this empty server lacks; --no-owner stops those, and a real fault shows in the counts.
  docker exec -i "$RESTORE" pg_restore -U postgres --no-owner --no-privileges -d r < "$OUT/personal_agents.dump" >/dev/null 2>&1 || true
  local table live restored dumped
  for table in "${PROOF_TABLES[@]}"; do
    live=$(count "$POSTGRES" personal_agents "$table")
    restored=$(count "$RESTORE" r "$table")
    dumped=$(sed -n "s/^rows_at_dump $table //p" "$OUT/receipt.txt")
    echo "rows $table at_dump=$dumped restored=$restored live=$live" | tee -a "$proof"
    if [[ "$dumped" == absent || "$restored" == absent || "$live" == absent ]]; then
      [[ "$dumped" == "$restored" ]] || { echo "differs=$table" | tee -a "$proof"; failed=1; }
    elif (( restored < dumped || restored > live )); then
      # Live may have grown since the dump, which is fine. Fewer than the dump held, or more than live has, is not.
      echo "differs=$table" | tee -a "$proof"; failed=1
    fi
  done
  remove_restore
  trap - EXIT

  local unpacked="$OUT/.restore-files"
  [[ ! -e "$unpacked" ]] || die "$unpacked is left from an earlier run; remove it first"
  install -d -m 0700 "$unpacked"
  tar -xzf "$OUT/files.tgz" -C "$unpacked"
  # Names only: -q never prints a file's contents.
  if diff -rq "$unpacked/auth" "$ROOT/auth" > "$OUT/.auth-diff"; then
    echo "files auth=same" | tee -a "$proof"
  else
    echo "files auth=differs names=$(wc -l < "$OUT/.auth-diff" | tr -d ' ')" | tee -a "$proof"; failed=1
  fi
  rm -rf "$unpacked" "$OUT/.auth-diff"
  chmod 0600 "$proof"
  [[ "$failed" == 0 ]] || { log "restore proof: DIFFERENCES above. Look before relying on this backup."; exit 1; }
  log "restore proof: every comparison matched. Receipt: $proof"
}

case "${1:-}" in
  backup) backup "${2:-}" ;;
  restore-proof) restore_proof "${2:-}" ;;
  *) die "usage: minimoi_1_2_backup.sh backup <folder> | restore-proof <folder>" ;;
esac
