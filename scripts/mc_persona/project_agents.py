#!/usr/bin/env python3
"""Project one persona release's AGENTS.md into the native mc-chat workspace. Workspace AGENTS.md only.

    project_agents.py plan     --release DIR --base-release DIR --workspace DIR --config FILE
    project_agents.py apply    ... --expect-agents SHA --expect-config SHA --backup-dir DIR
    project_agents.py rollback --workspace DIR --backup-dir DIR

The live projection is <base AGENTS> + "\\n" + <runtime binding> + <private context> + "\\n". The new projection keeps
every byte after the base AGENTS (runtime binding and private context included) and replaces only the base AGENTS
prefix with the release's AGENTS. So private context is preserved exactly, and nothing is rebuilt from a stale packet.

Refuses when: the live AGENTS or config hash differs from what the operator pinned; the live file does not start with
the base release's AGENTS; the release's own manifest hashes do not match its files; the projection exceeds the
release's budget; or AGENTS changes between read and write. It never writes openclaw.json, never restarts the gateway,
and never reads secrets (the config is hashed, not parsed). OpenClaw loads AGENTS.md at the start of each session, so
the next new conversation picks it up. Standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path: Path, raw: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Refused(SystemExit):
    def __init__(self, reason: str):
        super().__init__(f"refused: {reason}")


def load_release(release: Path) -> dict:
    manifest = json.loads((release / "manifest.json").read_text())
    for name, want in manifest["files"].items():
        got = sha((release / name).read_bytes())
        if got != want:
            raise Refused(f"{release.name}/{name} hash {got[:12]} != manifest {want[:12]}")
    return manifest


def build(release: Path, base: Path, workspace: Path) -> tuple[bytes, bytes, dict]:
    manifest = load_release(release)
    live = (workspace / "AGENTS.md").read_bytes()
    base_agents = (base / "AGENTS.md").read_bytes()
    if not live.startswith(base_agents):
        raise Refused(f"live AGENTS.md does not start with {base.name}/AGENTS.md (live projection is not the base release)")
    new = (release / "AGENTS.md").read_bytes() + live[len(base_agents):]
    budget = int(manifest.get("projection_budget_chars", 14000))
    chars = len(new.decode("utf-8"))
    if chars > budget:
        raise Refused(f"projection {chars} chars exceeds budget {budget}")
    return live, new, {"release": manifest["version"], "base": base.name, "chars": chars, "budget": budget,
                       "before_sha256": sha(live), "after_sha256": sha(new)}


def others(workspace: Path) -> dict:
    return {p.name: sha(p.read_bytes()) for p in sorted(workspace.iterdir()) if p.is_file() and p.name != "AGENTS.md"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "apply", "rollback"])
    ap.add_argument("--release", type=Path)
    ap.add_argument("--base-release", type=Path)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--expect-agents")
    ap.add_argument("--expect-config")
    ap.add_argument("--backup-dir", type=Path)
    a = ap.parse_args(argv)
    agents = a.workspace / "AGENTS.md"

    if a.cmd == "rollback":
        meta = json.loads((a.backup_dir / "PROJECTION.json").read_text())
        current = sha(agents.read_bytes())
        if current != meta["after_sha256"]:
            raise Refused(f"live AGENTS {current[:12]} is not the projection this backup made ({meta['after_sha256'][:12]})")
        pre = (a.backup_dir / "AGENTS.md.preimage").read_bytes()
        if sha(pre) != meta["before_sha256"]:
            raise Refused("preimage backup hash mismatch")
        atomic_write(agents, pre, 0o600)
        print(json.dumps({"rolled_back_to": meta["before_sha256"], "verified": sha(agents.read_bytes()) == meta["before_sha256"]}))
        return 0

    if not (a.release and a.base_release):
        raise Refused("plan/apply need --release and --base-release")
    live, new, info = build(a.release, a.base_release, a.workspace)
    info["config_sha256"] = sha(a.config.read_bytes()) if a.config else None
    if a.cmd == "plan":
        print(json.dumps(info, indent=1))
        return 0

    if not (a.expect_agents and a.expect_config and a.backup_dir and a.config):
        raise Refused("apply needs --expect-agents, --expect-config, --config and --backup-dir")
    if info["before_sha256"] != a.expect_agents:
        raise Refused(f"live AGENTS {info['before_sha256'][:12]} != pinned {a.expect_agents[:12]}")
    if info["config_sha256"] != a.expect_config:
        raise Refused(f"live config {info['config_sha256'][:12]} != pinned {a.expect_config[:12]}")
    before_others = others(a.workspace)
    a.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(a.backup_dir / "AGENTS.md.preimage", live, 0o600)
    atomic_write(a.backup_dir / "PROJECTION.json", (json.dumps(info, indent=1) + "\n").encode(), 0o600)
    if sha(agents.read_bytes()) != info["before_sha256"]:
        raise Refused("live AGENTS changed during apply; nothing written")
    atomic_write(agents, new, 0o600)
    checks = {"agents": sha(agents.read_bytes()) == info["after_sha256"],
              "config_unchanged": sha(a.config.read_bytes()) == a.expect_config,
              "other_workspace_files_unchanged": others(a.workspace) == before_others}
    print(json.dumps({**info, "checks": checks}, indent=1))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
