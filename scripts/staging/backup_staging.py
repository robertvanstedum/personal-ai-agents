#!/usr/bin/env python3
"""Daily backup of the AWS staging host (Dev) to S3, job ``staging-backup`` (Robert, 13 Nov 2026: staging had no backups).

What it keeps, each piece streamed straight to S3 (the host has little free disk) with its SHA-256 taken on the way:

* ``files/opt-minimoi-staging.tar.gz``: all of /opt/minimoi-staging (settings, operator records, Dev's own keys);
* ``volumes/<name>.tar.gz``: every Docker volume, read-only, through a container with no network;
* ``databases/<container>.pg_dumpall.sql.gz``: a dump of each running Postgres container;
* ``meta/running-containers.txt``: which container runs which image;
* ``images/<image>.tar.gz``: the images the running containers use, only with ``--with-images`` (before a risky change:
  a restore then needs no rebuild; daily they would cost several GB a day);
* ``MANIFEST.sha256``: one line per piece: checksum, name.

Destination ``s3://minimoi-backups/staging/<UTC stamp>-<label>/``. The host's role may only write under ``staging/``
(inline policy ``staging-backup-write``): it can neither read production's backups nor read its own back. Nothing is
deleted. The run writes its status document for the Dev watchdog (core.jobs.status), so a missed or failed night shows
in MC's job health. It never prints a secret, and it never logs file contents.

Run as root on the staging host, from the jobs tools: ``python3 /opt/minimoi-staging/jobs-tools/scripts/staging/backup_staging.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
import sys
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

TOOLS = Path(__file__).resolve().parents[2]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from core.jobs import status  # noqa: E402

JOB = SimpleNamespace(id="staging-backup", status_file="staging-backup.json", host="dev")
ROOT = "/opt/minimoi-staging"
JOBS_ROOT = ROOT + "/data/jobs"
BUCKET_PREFIX = "s3://minimoi-backups/staging"
CHUNK = 1 << 20


def utc() -> datetime:
    return datetime.now(timezone.utc)


def upload_command(dest: str) -> list[str]:
    # --expected-size lets the CLI pick a part size for a stream it cannot measure (up to 20 GB).
    return ["aws", "s3", "cp", "-", dest, "--only-show-errors", "--expected-size", "20000000000"]


def stream(producer: list[str], dest: str, *, compress: bool = False, uploader=upload_command) -> str:
    """Run ``producer``, gzip its output if asked, forward every byte to ``uploader(dest)``, and return the SHA-256 of
    what was uploaded. Raises RuntimeError when the producer or the upload fails; nothing is half-reported as kept."""
    up = subprocess.Popen(uploader(dest), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    src = subprocess.Popen(producer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    digest = hashlib.sha256()
    packer = zlib.compressobj(6, zlib.DEFLATED, 31) if compress else None   # 31: a gzip stream
    try:
        for chunk in iter(lambda: src.stdout.read(CHUNK), b""):
            out = packer.compress(chunk) if packer else chunk
            if out:
                digest.update(out)
                up.stdin.write(out)
        if packer:
            tail = packer.flush()
            digest.update(tail)
            up.stdin.write(tail)
    finally:
        src.stdout.close()
        up.stdin.close()
    produced, uploaded = src.wait(), up.wait()
    if produced != 0:
        raise RuntimeError(f"producer exited {produced}")
    if uploaded != 0:
        raise RuntimeError(f"upload exited {uploaded}: {(up.stderr.read() or b'').decode(errors='replace')[-200:]}")
    return digest.hexdigest()


def lines(cmd: list[str]) -> list[str]:
    return [l for l in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines() if l.strip()]


def pieces(with_images: bool, root: str = ROOT) -> list[tuple[str, list[str], bool]]:
    """(name, producer command, gzip it here) for every piece of tonight's backup, read from the running host."""
    out = [("files/opt-minimoi-staging.tar.gz", ["tar", "-C", str(Path(root).parent), "-czf", "-", Path(root).name], False)]
    running = lines(["docker", "ps", "--format", "{{.Names}} {{.Image}}"])
    tool = next((img for _, img in (r.split(" ", 1) for r in running) if "portal" in img), None)
    if tool:                                           # any running image with tar; the portal's always has it
        for volume in lines(["docker", "volume", "ls", "-q"]):
            out.append((f"volumes/{volume}.tar.gz", ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/v:ro",
                                                     "--entrypoint", "tar", tool, "-czf", "-", "-C", "/v", "."], False))
    for name, _ in (r.split(" ", 1) for r in running):
        if "postgres" in name:
            out.append((f"databases/{name}.pg_dumpall.sql.gz", ["docker", "exec", name, "pg_dumpall", "-U", "postgres"], True))
    out.append(("meta/running-containers.txt", ["docker", "ps", "--format", "{{.Names}} {{.Image}}"], False))
    if with_images:
        for image in sorted({img for _, img in (r.split(" ", 1) for r in running)}):
            safe = image.replace("/", "_").replace(":", "_")
            out.append((f"images/{safe}.tar.gz", ["docker", "save", image], True))
    return out


def run(*, label: str, with_images: bool, jobs_root: str = JOBS_ROOT, prefix: str = BUCKET_PREFIX,
        uploader=upload_command, plan=pieces) -> int:
    started = utc()
    dest = f"{prefix}/{started.strftime('%Y-%m-%dT%H%MZ')}-{label}"
    running = status.start_run(jobs_root, JOB, secrets.token_hex(12), started)
    manifest, failed = [], []
    print(f"staging backup to {dest}", flush=True)
    try:
        todo = plan(with_images)
    except Exception as exc:                           # the host could not even be read: say so, keep nothing half-done
        todo, failed = [], [f"plan: {type(exc).__name__}"]
    for name, producer, compress in todo:
        try:
            digest = stream(producer, f"{dest}/{name}", compress=compress, uploader=uploader)
            manifest.append(f"{digest}  {name}")
            print(f"kept {name} {digest[:16]}", flush=True)
        except Exception as exc:
            failed.append(name)
            print(f"FAILED {name}: {type(exc).__name__}: {exc}", flush=True)
    if manifest:
        try:
            stream(["printf", "%s\n", *manifest], f"{dest}/MANIFEST.sha256", uploader=uploader)
        except Exception as exc:
            failed.append("MANIFEST.sha256")
            print(f"FAILED MANIFEST.sha256: {type(exc).__name__}", flush=True)
    state = "failed" if failed or not manifest else "ok"
    night = dest.rsplit("/", 1)[1].lower()
    # The status contract allows plain words only (core.jobs.status: no slashes, short codes): names say ':' for '/'.
    named = ", ".join(n.replace("/", ":") for n in failed)
    summary = (f"{len(manifest)} pieces kept, night {night}" if state == "ok"
               else f"{len(failed)} of {len(todo) + (1 if manifest else 0)} pieces failed: {named}")[:status.SUMMARY_MAX]
    status.finish_run(jobs_root, JOB, running, state=state, now=utc(), exit_code=0 if state == "ok" else 1,
                      summary=summary, results={"night": night[:40], "kept": str(len(manifest)),
                                                "failed": str(len(failed)), "images": "yes" if with_images else "no"},
                      next_due_by=started + timedelta(hours=26))
    print(summary, flush=True)
    return 0 if state == "ok" else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--with-images", action="store_true", help="also save the running images (before a risky change)")
    p.add_argument("--label", default="daily", help="the folder's suffix, e.g. pre-catchup")
    p.add_argument("--jobs-root", default=os.environ.get("MINIMOI_JOBS_ROOT", JOBS_ROOT))
    a = p.parse_args(argv)
    return run(label=a.label, with_images=a.with_images, jobs_root=a.jobs_root)


if __name__ == "__main__":
    sys.exit(main())
