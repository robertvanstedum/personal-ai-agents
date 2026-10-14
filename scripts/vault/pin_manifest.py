#!/usr/bin/env python3
"""pin_manifest.py: write the Vault's exact pin (V1): the base commit, the content hash of every file the Vault owns, the tool versions it runs on, and what launchd runs
today versus what this pin would run. Read-only on the repo, the shelf and launchd; it writes only the manifest it is asked to. Nothing here repoints a job.

    pin_manifest.py [--out docs/vault/PIN_MANIFEST.json]

The tree hash covers the files themselves (not git history), so an uncommitted candidate is still pinned exactly; commit it and the same hash holds."""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OWNED = ["core/memory_shelf", "core/memory_index", "core/memory_ask", "core/memory_backup", "core/vault_t1", "core/jobs",
         "scripts/memory", "scripts/vault", "tests/memory_shelf", "tests/memory_index", "tests/memory_ask", "tests/memory_backup", "tests/vault_t1", "tests/jobs",
         "config/scheduled_jobs.json", "config/memory_shelf.example.json", "scripts/moi", "scripts/jobs/watchdog.py", "scripts/jobs/launchd.sh",
         "requirements-vault.txt", "requirements.test.txt", "docs/memory_capture_report_samples", "core/agent_turns/writer.py",
         "utils/credential_scrub.py", "utils/payment_scrub.py", "infrastructure/launchd/com.vanstedum.minimoi-memory-watch.plist",
         "infrastructure/launchd/com.vanstedum.minimoi-jobs-watchdog.plist"]
LAUNCHD = Path.home() / "Library" / "LaunchAgents"
JOBS = {"memory-watch": "com.vanstedum.minimoi-memory-watch.plist", "jobs-watchdog": "com.vanstedum.minimoi-jobs-watchdog.plist"}


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60).stdout.strip()


def files() -> list[Path]:
    out = []
    for item in OWNED:
        p = REPO / item
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"]
    return sorted(set(out))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launchd_view() -> dict:
    view = {}
    for job, name in JOBS.items():
        try:
            doc = plistlib.loads((LAUNCHD / name).read_bytes())
        except (OSError, ValueError):
            view[job] = {"plist": name, "found": False}
            continue
        view[job] = {"plist": name, "found": True, "program_arguments": doc.get("ProgramArguments"), "working_directory": doc.get("WorkingDirectory"),
                     "start_calendar_interval": doc.get("StartCalendarInterval"),
                     "runs_pin": (doc.get("WorkingDirectory") or "").replace(str(Path.home()), "~")}
    return view


def versions() -> dict:
    out = {"python": sys.version.split()[0]}
    for mod in ("yaml", "sqlite_vec"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "present")
        except ImportError:
            out[mod] = "missing"
    import sqlite3
    out["sqlite"] = sqlite3.sqlite_version
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "docs" / "vault" / "PIN_MANIFEST.json"))
    a = ap.parse_args(argv)
    entries = {str(f.relative_to(REPO)): sha(f) for f in files()}
    tree = hashlib.sha256("".join(f"{k}\0{v}\n" for k, v in sorted(entries.items())).encode()).hexdigest()
    doc = {
        "runtime_dependencies": ["core.agent_turns.writer", "utils.credential_scrub", "utils.payment_scrub"],
        "format": "vault-pin/1", "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "base_commit": git("rev-parse", "HEAD"),
        "base_subject": git("log", "-1", "--format=%s")[:100], "dirty_paths": len([l for l in git("status", "--short").splitlines() if l]),
        "owned_roots": OWNED, "files": len(entries), "tree_sha256": tree, "file_sha256": entries, "tool_versions": versions(), "launchd_today": launchd_view(),
        "not_done": ["no job repointed", "no plist changed", "no shelf data touched", "no capture widened"],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: doc[k] for k in ("branch", "base_commit", "files", "tree_sha256", "dirty_paths")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
