#!/usr/bin/env python3
"""Production's delivery record: what MC and CoS read for "what is in production?" (#536).

Dev's deploy writes ``/minimoi/docs/delivery/DEV.md`` on the Dev host (scripts/staging/beta_dev_deploy.py). Production had
no record at all, so production MC's read of its delivery document failed. This writes the production counterpart beside
where Dev's would be: ``/opt/minimoi/docs/delivery/PRODUCTION.md`` (+ ``PRODUCTION.json`` and one ``history.jsonl`` line
per release). MC's container mounts ``/opt/minimoi/docs`` read-only at ``/minimoi/docs``; nothing here widens that.

Inputs are the deploy's own nonsecret observations in ``/opt/minimoi/release-status`` (scripts/operations/release_status.py):
``status.json`` (the finish observation: image per service) and ``manifest.json`` (commit, subject, release notes). It
writes only after a finish that confirmed the selected images, so the record never claims what production does not run.
Root owns the folder (0755) and the files (0644): agents read them, never write them. Standard library only.

  python3 -I -B write_production_delivery_record.py [--status-root DIR] [--docs-root DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

STATUS_ROOT = Path("/opt/minimoi/release-status")
DOCS_ROOT = Path("/opt/minimoi/docs")
DELIVERY_DIR = "delivery"
MAX_INPUT = 16384


class Refused(RuntimeError):
    """Nothing written."""


def _read_json(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o022 or st.st_size > MAX_INPUT:
            raise Refused(f"{path.name}: not a private regular file")
        return json.loads(os.read(fd, MAX_INPUT + 1))
    finally:
        os.close(fd)


def _text(value, n=240) -> str:
    return re.sub(r"[\x00-\x1f\x7f`]", " ", str(value or ""))[:n].strip()


def _tag(image) -> str:
    return _text(str(image).rsplit(":", 1)[-1], 80) if image and ":" in str(image) else "not running"


def record(status: dict, manifest: dict | None, at: float) -> tuple[str, dict]:
    """(PRODUCTION.md text, PRODUCTION.json doc) from a confirmed finish observation."""
    if not isinstance(status, dict) or status.get("status") != "selected_images_observed":
        raise Refused("the last release observation did not confirm its images")
    if status.get("environment", "production") != "production":
        raise Refused("not a production observation")
    current = status.get("current") or {}
    manifest = manifest if isinstance(manifest, dict) else None
    commit = (manifest or {}).get("source_commit") or ""
    tag = _text(status.get("requested_tag"), 40)
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or not commit.startswith(tag):
        manifest, commit = None, ""          # the manifest describes another release; claim only the tag
    selected = [s for s in status.get("selected") or [] if isinstance(s, str)]
    doc = {"format": "minimoi.production-delivery/1", "at": at, "observed_at": _text(status.get("observed_at"), 40),
           "release_tag": tag, "commit": commit, "subject": _text((manifest or {}).get("subject"), 160),
           "version": _text((manifest or {}).get("version"), 20),
           "capabilities": [_text(c.get("summary")) for c in (manifest or {}).get("capabilities") or [] if isinstance(c, dict)][:8],
           "selected": selected,
           "services": {name: _tag((row or {}).get("image")) for name, row in sorted(current.items())}}
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(at))
    lines = ["# What is in production (minimoi.ai)", "",
             f"Written by production's deploy at {when}, after it confirmed the released images. A platform record: read it, never edit it.", "",
             f"- **Release:** `{tag}`" + (f", version {doc['version']}" if doc["version"] else "") + ".",
             (f"- **Commit:** `{commit[:8]}` on `main`: {doc['subject']}" if commit else
              "- **Commit:** not recorded for this release (no matching release manifest); the tag is the commit's short hash."),
             f"- **Deployed this release:** {', '.join(selected) if selected else 'none listed'}."]
    if doc["capabilities"]:
        lines += ["", "## In this release", ""] + [f"- {c}" for c in doc["capabilities"]]
    lines += ["", "## Running now (image tag per service)", ""]
    lines += [f"- {name}: `{t}`" for name, t in doc["services"].items()] or ["- none observed"]
    lines += ["", "## Answering \"what was delivered?\"", "",
              "- **In production:** what is merged to `main` up to the commit above, and running here. Newer merges to `main` deploy with the next release.",
              "- **On Dev only:** Dev's own record (`DEV.md`) lives on the Dev host, not here. Check GitHub's branches or ask Robert.",
              "- **Still open:** not in either record. Say so, and use the planning record or ask Robert.",
              "- Each production deploy rewrites this file; `history.jsonl` beside it keeps one line per release.", ""]
    return "\n".join(lines), doc


def _atomic(folder: Path, name: str, raw: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix="." + name + ".", dir=folder)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, folder / name)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def write(status_root: Path, docs_root: Path, at: float | None = None) -> Path:
    status = _read_json(status_root / "status.json")
    if status is None:
        raise Refused("no release observation yet")
    text, doc = record(status, _read_json(status_root / "manifest.json"), time.time() if at is None else at)
    folder = docs_root / DELIVERY_DIR
    folder.mkdir(mode=0o755, parents=True, exist_ok=True)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise Refused("delivery folder is not owned by this user or is writable by others")
    _atomic(folder, "PRODUCTION.md", text.encode())
    _atomic(folder, "PRODUCTION.json", (json.dumps(doc, sort_keys=True) + "\n").encode())
    hist = os.open(folder / "history.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        os.write(hist, (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(hist)
    return folder / "PRODUCTION.md"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--status-root", type=Path, default=STATUS_ROOT)
    p.add_argument("--docs-root", type=Path, default=DOCS_ROOT)
    a = p.parse_args(argv)
    try:
        path = write(a.status_root, a.docs_root)
    except Refused as exc:
        print(f"delivery record: {exc}; nothing written", file=sys.stderr)
        return 1
    print(f"delivery record: wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
