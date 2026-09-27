#!/bin/bash
# seed_build_queue.sh — the host-side writer for the live Guild Build Queue.
#
# The production portal writes /opt/minimoi/data/guild/build_queue.json when
# Robert saves (domains/guild/queue_store.py). Every host-side write here takes
# the SAME lock as the portal: flock(2) on the sidecar file
# <queue folder>/.build_queue.json.lock. The portal container mounts the host
# folder (/opt/minimoi/data/guild -> /app/runtime/guild), and on native Linux a
# flock taken on the host and one taken in the container exclude each other.
# Each replace is atomic (same-folder temp, fsync, rename), keeps the live
# file's mode and owner, is read back, and is journalled as a `replaced` line
# in <queue folder>/queue_journal.jsonl so it is never mistaken for an
# unexplained change and never hides an unfinished Save.
#
# Modes:
#   seed_build_queue.sh SOURCE_URL [DEST] [BACKUP_DIR]
#       Deploy seed. Missing queue: seed it from SOURCE_URL. Nonempty queue:
#       keep it untouched and save a timestamped backup. An EXISTING EMPTY
#       queue fails loudly: an empty live queue means something went wrong,
#       and the deploy must stop rather than paper over it.
#   seed_build_queue.sh --status [DEST]
#       Print the live queue's SHA-256 and the journal's recent writes.
#   seed_build_queue.sh --publish --expect-live-sha256 HEX SOURCE_URL [DEST] [BACKUP_DIR]
#       Deliberately replace the live queue with SOURCE_URL. Refused unless the
#       live file's SHA-256 still equals HEX (the digest the operator looked at
#       with --status), and unless the running portal container mounts the
#       queue FOLDER (an atomic replace would detach a single-file mount).
#
# SOURCE_URL is anything curl accepts (a GitHub raw URL pinned to a commit, or
# file:// in tests). Requires python3 (for flock, JSON and the journal).
# Environment: QUEUE_LOCK_TIMEOUT_S (default 30), QUEUE_PORTAL_CONTAINER
# (default minimoi-portal), QUEUE_PORTAL_MOUNT (default /app/runtime/guild).

set -euo pipefail

usage() {
  echo "usage: seed_build_queue.sh SOURCE_URL [DEST] [BACKUP_DIR]" >&2
  echo "       seed_build_queue.sh --status [DEST]" >&2
  echo "       seed_build_queue.sh --publish --expect-live-sha256 HEX SOURCE_URL [DEST] [BACKUP_DIR]" >&2
  exit 2
}

MODE=seed
EXPECT=""
case "${1:-}" in
  --status) MODE=status; shift ;;
  --publish)
    MODE=publish; shift
    [ "${1:-}" = "--expect-live-sha256" ] || { echo "build_queue: --publish requires --expect-live-sha256 HEX (run --status first)" >&2; exit 2; }
    EXPECT="${2:-}"; shift 2 || usage
    [[ "$EXPECT" =~ ^[0-9a-f]{64}$ ]] || { echo "build_queue: --expect-live-sha256 needs a 64-character lowercase hex digest" >&2; exit 2; }
    ;;
  "") usage ;;
esac

command -v python3 >/dev/null 2>&1 || { echo "build_queue: python3 is required (lock, JSON check, journal); nothing written" >&2; exit 1; }

if [ "$MODE" = status ]; then
  SRC_URL=""
  DEST="${1:-/opt/minimoi/data/guild/build_queue.json}"
  BACKUP_DIR=""
else
  SRC_URL="${1:-}"; [ -n "$SRC_URL" ] || usage
  DEST="${2:-/opt/minimoi/data/guild/build_queue.json}"
  BACKUP_DIR="${3:-$(dirname "$DEST")/backups}"
fi

if [ "$MODE" = seed ]; then
  mkdir -p "$(dirname "$DEST")"
  if [ -e "$DEST" ] && [ ! -s "$DEST" ]; then
    echo "build_queue: FAILED — $DEST exists but is EMPTY. Refusing to seed over it." >&2
    echo "build_queue: an empty live queue means a write went wrong. Restore it from $BACKUP_DIR (newest backup), or remove it deliberately, then redeploy." >&2
    exit 1
  fi
fi

TMP=""
cleanup() { [ -n "$TMP" ] && rm -f "$TMP"; return 0; }
trap cleanup EXIT
if [ "$MODE" = publish ] || { [ "$MODE" = seed ] && [ ! -e "$DEST" ]; }; then
  # Download outside the lock (no network while holding it), into the queue's
  # own folder so the final rename is atomic.
  TMP="$(dirname "$DEST")/.$(basename "$DEST").seed-$$"
  curl -fsSL "$SRC_URL" -o "$TMP"
fi

python3 - "$MODE" "$DEST" "$BACKUP_DIR" "$TMP" "$EXPECT" "$SRC_URL" <<'PY'
import errno, fcntl, hashlib, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone

mode, dest, backup_dir, tmp, expect, src_url = sys.argv[1:7]
folder = os.path.dirname(os.path.abspath(dest))
name = os.path.basename(dest)
lock_path = os.path.join(folder, "." + name + ".lock")
journal = os.path.join(folder, "queue_journal.jsonl")
timeout = float(os.environ.get("QUEUE_LOCK_TIMEOUT_S", "30"))


def now():
    return datetime.now(timezone.utc)


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def die(message, code=1):
    print("build_queue: " + message, file=sys.stderr)
    sys.exit(code)


def append(record):
    data = (json.dumps(record, sort_keys=True) + "\n").encode()
    fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size:
            with open(journal, "rb") as stream:
                stream.seek(size - 1)
                if stream.read(1) != b"\n":
                    data = b"\n" + data
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def valid_queue(path):
    try:
        with open(path, "rb") as stream:
            items = json.loads(stream.read().decode("utf-8"))
    except Exception:
        return False
    return isinstance(items, list)


def replace_verified(src, mode_bits, uid, gid):
    os.chmod(src, mode_bits)
    try:
        os.chown(src, uid, gid)
    except PermissionError:
        pass
    fd = os.open(src, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    after = digest(src)
    os.replace(src, dest)
    dfd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    if digest(dest) != after:
        die("read-back after replace did not match; check the queue now")
    return after


def portal_mount_problem():
    container = os.environ.get("QUEUE_PORTAL_CONTAINER", "minimoi-portal")
    target = os.environ.get("QUEUE_PORTAL_MOUNT", "/app/runtime/guild")
    try:
        out = subprocess.run(["docker", "inspect", "--format", "{{json .Mounts}}", container],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "cannot run docker inspect (%s)" % exc
    if out.returncode != 0:
        return "cannot inspect container %s" % container
    try:
        mounts = json.loads(out.stdout or "[]") or []
    except ValueError:
        return "cannot read the mounts of %s" % container
    for mount in mounts:
        if mount.get("Destination") == target and \
                os.path.realpath(mount.get("Source", "")) == os.path.realpath(folder):
            return None
    return "%s does not mount %s at %s (an atomic replace would detach it)" % (container, folder, target)


if mode == "status":
    if not os.path.exists(dest):
        die("no live queue at %s" % dest)
    with open(dest, "rb") as stream:
        raw = stream.read()
    print("build_queue: live %s sha256=%s bytes=%d valid=%s" % (
        dest, hashlib.sha256(raw).hexdigest(), len(raw), valid_queue(dest)))
    lines = []
    if os.path.exists(journal):
        for chunk in open(journal, "rb").read().split(b"\n"):
            try:
                lines.append(json.loads(chunk))
            except ValueError:
                pass
    last_replace = max((i for i, r in enumerate(lines) if r.get("kind") == "replaced"), default=-1)
    saves = [r for r in lines[last_replace + 1:] if r.get("kind") == "completed"]
    print("build_queue: %d verified Save(s) journalled since the last seed/publish" % len(saves))
    for record in lines[-5:]:
        print("build_queue: journal %s %s %s" % (record.get("at", "?"), record.get("kind"),
                                                 record.get("receipt_id") or record.get("writer") or record.get("outcome") or ""))
    print("build_queue: to publish the repository copy over THIS file, rerun with --expect-live-sha256=%s" % hashlib.sha256(raw).hexdigest())
    sys.exit(0)

fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
deadline = time.monotonic() + timeout
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except OSError as exc:
        if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise
        if time.monotonic() >= deadline:
            die("the queue lock %s is held by another writer; nothing written" % lock_path, 75)
        time.sleep(0.05)

try:
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    if mode == "seed":
        if os.path.exists(dest):
            if os.path.getsize(dest) == 0:
                die("%s became EMPTY; refusing to seed over it" % dest)
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, "build_queue.%s.json" % stamp)
            shutil.copy2(dest, backup)
            print("build_queue: live copy kept, not overwritten by deploy (backup: %s)" % backup)
            sys.exit(0)
        if not tmp or not valid_queue(tmp):
            die("seed source is not a valid JSON list; nothing written")
        after = replace_verified(tmp, 0o644, os.getuid(), os.getgid())
        append({"kind": "replaced", "writer": "seed", "before_digest": None,
                "after_digest": after, "source": src_url, "at": now().isoformat()})
        print("build_queue: no live copy found; seeded from %s" % src_url)
        sys.exit(0)

    # publish
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        die("no nonempty live queue at %s; use the deploy seed instead" % dest)
    live = digest(dest)
    if live != expect:
        die("the live queue changed since you looked (now sha256=%s, expected %s). "
            "Nothing published. Run --status again and check the new Saves are in "
            "the repository copy first." % (live, expect))
    problem = portal_mount_problem()
    if problem:
        die("refusing to publish: %s. Nothing published." % problem)
    if not tmp or not valid_queue(tmp):
        die("published copy is not a valid JSON list; nothing written")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "build_queue.%s.before-publish.json" % stamp)
    shutil.copy2(dest, backup)
    st = os.stat(dest)
    after = replace_verified(tmp, st.st_mode & 0o7777, st.st_uid, st.st_gid)
    append({"kind": "replaced", "writer": "publish", "before_digest": live,
            "after_digest": after, "source": src_url, "backup": backup,
            "at": now().isoformat()})
    print("OK: build_queue.json published (verified sha256=%s; live copy backed up to %s)" % (after, backup))
finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
PY
