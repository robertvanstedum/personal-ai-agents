#!/usr/bin/env python3
"""Dev host operator: current MC export, isolated feed worker, bounded scratch prune.

No production defaults; requires an immutable local image id and explicit owner.
Only mounts this Dev shelf and read-only canonical CoS history into the worker.
The portal exporter uses its existing read credentials and writes no source data.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

ROOT = Path("/opt/minimoi-staging")
MAX_ARCHIVE = 256 * 1024 * 1024
NAME = re.compile(r"dev--(?:c-[0-9a-f]{12}|legacy-[0-9a-f]{16})\.json\Z")


def failed_status():
    folder = ROOT / "data/vault-jobs"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / "dev-memory-feed.json"
    try:
        previous = json.loads(target.read_text())
    except (OSError, ValueError):
        previous = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"schema_version": 1, "job_id": "dev-memory-feed", "host": "dev", "run_id": uuid.uuid4().hex,
           "state": "failed", "started_at": now, "finished_at": now, "exit_code": 1,
           "summary": "Dev memory cycle failed", "results": {"cycle": "failed"}, "next_due_by": None,
           "last_success_at": previous.get("last_success_at"), "alert": "not_sent"}
    with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as handle:
        json.dump(doc, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, target)
    publish_status()


def publish_status():
    """Only counts/status go to shared jobs; private receipts/cursors stay out.

    The host owns publication into the existing UID-owned jobs directory. The
    cap-dropped worker never needs DAC override or access to other jobs.
    """
    shared = ROOT / "data/jobs"
    source = ROOT / "data/vault-jobs/dev-memory-feed.json"
    shared.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner = shared.stat()
    raw = source.read_bytes()
    if len(raw) > 16384:
        raise ValueError("status_limit")
    with tempfile.NamedTemporaryFile(dir=shared, delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
    os.replace(handle.name, shared / "dev-memory-feed.json")


def unpack(archive, destination):
    """Extract only validated regular exports, with a final complete receipt."""
    files, receipt, total = {}, None, 0
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            if receipt is not None or not member.isfile() or member.size > 48 * 1024 * 1024:
                raise ValueError("invalid_export")
            total += member.size
            if total > MAX_ARCHIVE or len(files) > 2000:
                raise ValueError("export_limit")
            if member.name == "EXPORT.json":
                if member.size > 65536:
                    raise ValueError("receipt_limit")
                receipt = json.load(tar.extractfile(member))
                continue
            if not NAME.fullmatch(member.name) or member.name in files:
                raise ValueError("invalid_name")
            raw = tar.extractfile(member).read()
            files[member.name] = hashlib.sha256(raw).hexdigest()
            (destination / member.name).write_bytes(raw)
        if not receipt or receipt.get("status") != "ok" or receipt.get("counts", {}).get("exported", 0) != len(files):
            raise ValueError("incomplete_export")
    manifest = {"schema": "minimoi.dev-mc-export/1", "status": "ok", "files": files,
                "completed_at": datetime.now(timezone.utc).isoformat()}
    (destination / "feed-export.json").write_text(json.dumps(manifest))


def prune(scratch, now=None):
    """Only abandoned worker-created export scratch older than one day."""
    now = time.time() if now is None else now
    count = 0
    for path in scratch.iterdir():
        if re.fullmatch(r"cycle-[a-z0-9_]{8}", path.name) and not path.is_symlink() and path.is_dir():
            if now - path.stat().st_mtime > 86400:
                shutil.rmtree(path)
                count += 1
    return count


def run_worker(command, worker):
    try:
        subprocess.run(command, check=True, timeout=300)
    finally:
        subprocess.run(["docker", "rm", "-f", worker], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
        remaining = subprocess.run(["docker", "ps", "-a", "--filter", f"name=^/{worker}$", "--format", "{{.ID}}"],
                                   capture_output=True, text=True, check=True, timeout=10)
        if remaining.stdout.strip():
            raise ValueError("worker_cleanup_failed")


def cycle(image, principal):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,128}", principal):
        raise ValueError("invalid_operator_input")
    if not ROOT.is_dir():
        raise ValueError("dev_host_required")
    data = ROOT / "data"
    scratch = ROOT / "operators/vault-dev-20261105/scratch"
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (scratch.parent / "cycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(data).free < 2 * 1024 ** 3:
            raise ValueError("disk_low")
        prune(scratch)
        for name in ("memory", "memory-index", "vault-jobs"):
            (data / name).mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cycle-", dir=scratch) as temp:
            temp = Path(temp)
            archive, export = temp / "export.tar", temp / "mc"
            export.mkdir(mode=0o700)
            with archive.open("wb") as output:
                # Alarm lives inside the exec process: killing a docker CLI alone
                # does not stop the remote Python process.
                bounded = "import signal,runpy,sys;signal.alarm(110);sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
                proc = subprocess.Popen(["docker", "exec", "minimoi-portal", "python", "-B", "-c", bounded,
                    "/app/scripts/vault/export_mc_conversations.py", "--stdout-tar", "--principal", principal,
                    "--instance", "dev", "--guild-dir", "/app/runtime/guild"], stdout=output, stderr=subprocess.DEVNULL)
                start = time.monotonic()
                try:
                    while proc.poll() is None:
                        if archive.stat().st_size > MAX_ARCHIVE or time.monotonic() - start > 120:
                            raise ValueError("export_limit")
                        time.sleep(.25)
                    if proc.returncode or archive.stat().st_size > MAX_ARCHIVE:
                        raise ValueError("export_failed")
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=10)
            unpack(archive, export)
            mounts = [(data / "cos-conversations/history", "/source/cos", "ro"), (export, "/source/mc", "ro"),
                      (data / "memory", "/vault/memory", "rw"), (data / "memory-index", "/vault/memory-index", "rw"),
                      (data / "vault-jobs", "/vault/jobs", "rw")]
            worker = "dev-memory-feed-" + uuid.uuid4().hex
            command = ["docker", "run", "--name", worker, "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--memory", "512m", "--cpus", "1", "--pids-limit", "64",
                       "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m", "--entrypoint", "python"]
            for src, dst, mode in mounts:
                command += ["-v", f"{src}:{dst}:{mode}"]
            command += [image, "-B", "/app/scripts/memory/dev_feed.py", "--cos-root", "/source/cos", "--mc-root",
                        "/source/mc", "--shelf-root", "/vault/memory", "--index-path", "/vault/memory-index/memory.sqlite",
                        "--jobs-root", "/vault/jobs"]
            run_worker(command, worker)
            publish_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--principal", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        cycle(args.image, args.principal)
    except BlockingIOError:
        print('{"state":"busy"}')
        raise SystemExit(0)
    except Exception:
        failed_status()
        print('{"state":"failed","code":"dev_cycle_failed"}')
        raise SystemExit(1)
