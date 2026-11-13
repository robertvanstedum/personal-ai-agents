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

Every stack, not only the main one (#550): ``guildhall.json`` beside them is guildhall_prod.sh's own observation of its last
refresh (Records' and the workers' digests, and the result), and ``mc-agent.json`` the tag MC's own container runs and
whether this release left MC's image behind. deploy.yml re-runs this after the Guildhall refresh with the MC flags, and
deploy-mc-agent.yml after an MC release (--mc-agent-released). A re-run for the same release replaces that release's
history line rather than adding a second one.

  python3 -I -B write_production_delivery_record.py [--status-root DIR] [--docs-root DIR]
      [--mc-agent-live TAG] [--mc-agent-needed yes|no|unknown] [--mc-agent-changes N] [--mc-agent-released TAG]
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


MC_TAG = re.compile(r"mc-agent-v[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.-]{0,40}")
GUILDHALL_WORDS = {
    "refreshed": "refreshed by this release and verified",
    "current": "already on this release's images; verified",
    "skipped": "not switched on on this host",
    "refused": "REFUSED by a gate at the last release; still on its previous images",
    "rolled_back": "the last refresh FAILED and was put back; on its previous images, verified",
    "rollback_failed": "the last refresh FAILED and is NOT verified",
}


def _digest(value) -> str:
    text = _text(value, 80)
    return text[:19] if re.fullmatch(r"sha256:[a-f0-9]{64}", text) else ""


def _guildhall(observation) -> dict | None:
    if not isinstance(observation, dict) or observation.get("format") != "minimoi.guildhall-observation/1":
        return None
    result = _text(observation.get("result"), 40)
    return {"result": result if result in GUILDHALL_WORDS else "unknown", "release_tag": _text(observation.get("release_tag"), 40),
            "records_image": _digest(observation.get("records_image")), "worker_image": _digest(observation.get("worker_image")),
            "changed": [_text(c, 20) for c in observation.get("changed") or [] if isinstance(c, str)][:4],
            "observed_at": _text(observation.get("observed_at"), 40)}


def _mc_agent(observation) -> dict | None:
    if not isinstance(observation, dict) or observation.get("format") != "minimoi.mc-agent-observation/1":
        return None
    live = _text(observation.get("live_tag"), 60)
    needed = observation.get("needed")
    changes = observation.get("changes")
    return {"live_tag": live if MC_TAG.fullmatch(live) else "", "released_at": _text(observation.get("released_at"), 40),
            "needed": needed if needed in ("yes", "no", "unknown") else "unknown",
            "changes": changes if isinstance(changes, int) and changes >= 0 else None,
            "checked_at": _text(observation.get("checked_at"), 40)}


def _stack_lines(tag: str, guildhall: dict | None, mc: dict | None) -> list[str]:
    lines = ["", "## Every stack", "", f"- **Main:** release `{tag}` (the services above)."]
    if guildhall is None:
        lines.append("- **Guildhall (Records and its two workers):** no refresh recorded yet.")
    else:
        words = GUILDHALL_WORDS.get(guildhall["result"], "result unknown")
        line = f"- **Guildhall (Records and its two workers):** {words}"
        if guildhall["records_image"]:
            line += f"; Records on `{guildhall['records_image']}`"
        if guildhall["worker_image"]:
            line += f", workers on `{guildhall['worker_image']}`"
        if guildhall["release_tag"] and guildhall["release_tag"] != tag:
            line += f" (observed at release `{guildhall['release_tag']}`)"
        lines.append(line + (f", {guildhall['observed_at']}." if guildhall["observed_at"] else "."))
    if mc is None or not mc["live_tag"]:
        lines.append("- **MC agent:** its tag is not recorded yet (the next deploy or MC release records it).")
    else:
        line = f"- **MC agent:** `{mc['live_tag']}`" + (f", released {mc['released_at']}" if mc["released_at"] else "") + "."
        lines.append(line)
    if mc is not None and mc["needed"] == "yes":
        count = f"{mc['changes']} file(s)" if mc["changes"] is not None else "Files"
        lines.append(f"- **MC agent release needed:** {count} MC's image holds changed since `{mc['live_tag'] or 'its tag'}`; "
                     "those changes are merged but MC does not run them until an `mc-agent-v*` release.")
    elif mc is not None and mc["needed"] == "unknown":
        lines.append("- **MC agent release needed:** unknown (the last deploy could not compare MC's tag with the release).")
    return lines


def record(status: dict, manifest: dict | None, at: float, guildhall=None, mc=None) -> tuple[str, dict]:
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
           "services": {name: _tag((row or {}).get("image")) for name, row in sorted(current.items())},
           "guildhall": _guildhall(guildhall), "mc_agent": _mc_agent(mc)}
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(at))
    lines = ["# What is in production (minimoi.ai)", "",
             f"Written by production's deploy at {when}, after it confirmed the released images. A platform record: read it, never edit it.", "",
             f"- **Release:** `{tag}`" + (f", version {doc['version']}" if doc["version"] else "") + ".",
             (f"- **Commit:** `{commit[:8]}` on `main`: {doc['subject']}" if commit else
              "- **Commit:** not recorded for this release (no matching release manifest); the tag is the commit's short hash."),
             f"- **Deployed this release:** {', '.join(selected) if selected else 'none listed'}."]
    lines += _stack_lines(tag, doc["guildhall"], doc["mc_agent"])
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


def note_mc_agent(status_root: Path, *, live=None, needed=None, changes=None, released=None, at: float | None = None) -> None:
    """Update mc-agent.json beside the release observation. Tags are validated; nothing else is accepted."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if at is None else at))
    current = _read_json(status_root / "mc-agent.json")
    doc = current if isinstance(current, dict) and current.get("format") == "minimoi.mc-agent-observation/1" else \
        {"format": "minimoi.mc-agent-observation/1"}
    for tag in (live, released):
        if tag is not None and not MC_TAG.fullmatch(tag):
            raise Refused("not an mc-agent release tag")
    if needed is not None and needed not in ("yes", "no", "unknown"):
        raise Refused("needed is yes, no or unknown")
    if released is not None:             # A release just ran: the next deploy compares again.
        doc.update(live_tag=released, released_at=now, needed="no", changes=0)
    if live is not None:
        if live != doc.get("live_tag"):
            doc["released_at"] = ""
        doc["live_tag"] = live
    if needed is not None:
        doc["needed"] = needed
        doc["changes"] = changes if isinstance(changes, int) and changes >= 0 else None
    doc["checked_at"] = now
    _atomic(status_root, "mc-agent.json", (json.dumps(doc, sort_keys=True) + "\n").encode())


def write(status_root: Path, docs_root: Path, at: float | None = None) -> Path:
    status = _read_json(status_root / "status.json")
    if status is None:
        raise Refused("no release observation yet")
    text, doc = record(status, _read_json(status_root / "manifest.json"), time.time() if at is None else at,
                       guildhall=_read_json(status_root / "guildhall.json"), mc=_read_json(status_root / "mc-agent.json"))
    folder = docs_root / DELIVERY_DIR
    folder.mkdir(mode=0o755, parents=True, exist_ok=True)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise Refused("delivery folder is not owned by this user or is writable by others")
    _atomic(folder, "PRODUCTION.md", text.encode())
    _atomic(folder, "PRODUCTION.json", (json.dumps(doc, sort_keys=True) + "\n").encode())
    line = json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
    history = folder / "history.jsonl"
    previous = _last_history(history)
    if previous and (previous.get("release_tag"), previous.get("observed_at")) == (doc["release_tag"], doc["observed_at"]):
        # The same release, recorded again with its other stacks: replace its line, keep one line per release.
        kept = history.read_bytes().rstrip(b"\n").rpartition(b"\n")[0]
        _atomic(folder, "history.jsonl", (kept + b"\n" if kept else b"") + line.encode())
        return folder / "PRODUCTION.md"
    hist = os.open(history, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        os.write(hist, line.encode())
    finally:
        os.close(hist)
    return folder / "PRODUCTION.md"


def _last_history(path: Path) -> dict | None:
    try:
        tail = path.read_bytes().rstrip(b"\n").rpartition(b"\n")[2]
        value = json.loads(tail) if tail else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--status-root", type=Path, default=STATUS_ROOT)
    p.add_argument("--docs-root", type=Path, default=DOCS_ROOT)
    p.add_argument("--mc-agent-live", help="the mc-agent-v* tag MC's container runs")
    p.add_argument("--mc-agent-needed", choices=("yes", "no", "unknown"), help="does this release leave MC's image behind")
    p.add_argument("--mc-agent-changes", type=int, help="how many files MC's image holds changed since its tag")
    p.add_argument("--mc-agent-released", help="an mc-agent-v* release just deployed (deploy-mc-agent.yml)")
    a = p.parse_args(argv)
    try:
        if any(v is not None for v in (a.mc_agent_live, a.mc_agent_needed, a.mc_agent_released)):
            note_mc_agent(a.status_root, live=a.mc_agent_live, needed=a.mc_agent_needed, changes=a.mc_agent_changes,
                          released=a.mc_agent_released)
        path = write(a.status_root, a.docs_root)
    except Refused as exc:
        print(f"delivery record: {exc}; nothing written", file=sys.stderr)
        return 1
    print(f"delivery record: wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
