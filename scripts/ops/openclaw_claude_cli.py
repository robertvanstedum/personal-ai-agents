#!/usr/bin/env python3
"""Run native OpenClaw's Anthropic models through the Claude CLI runtime (the signed-in Claude Code = Robert's plan).

    openclaw_claude_cli.py plan     [--models anthropic/claude-opus-5,...] [--config PATH]
    openclaw_claude_cli.py stage    --out FILE                       write the candidate config (0600) for `openclaw config validate`
    openclaw_claude_cli.py apply    --expect-config SHA --backup-dir DIR
    openclaw_claude_cli.py rollback --backup-dir DIR

Sets ``agents.defaults.models["<model>"].agentRuntime = {"id": "claude-cli"}`` for each Anthropic model the config can
select (the documented per-model runtime switch, docs/gateway/cli-backends.md "Configuration"), and narrows
``auth.order.anthropic`` to the token profile so no Anthropic turn can silently fall back to the metered API key.
Nothing else changes (checked by restoring those keys and comparing the parsed config). Never reads or prints a secret.
Standard library only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

CONFIG = Path.home() / ".openclaw" / "openclaw.json"
RUNTIME = {"id": "claude-cli"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write(path: Path, raw: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def anthropic_models(cfg: dict) -> list[str]:
    a = cfg.get("agents") or {}
    refs = set((a.get("defaults") or {}).get("models") or {})
    refs |= set(((a.get("defaults") or {}).get("modelPolicy") or {}).get("allow") or [])
    for spec in [(a.get("defaults") or {}).get("model")] + [e.get("model") for e in (a.get("entries") or {}).values()]:
        if isinstance(spec, dict):
            refs |= {spec.get("primary")} | set(spec.get("fallbacks") or [])
        elif isinstance(spec, str):
            refs.add(spec)
    return sorted(r for r in refs if isinstance(r, str) and r.startswith("anthropic/"))


def candidate(cfg: dict, models: list[str]) -> dict:
    new = copy.deepcopy(cfg)
    m = new["agents"].setdefault("defaults", {}).setdefault("models", {})
    for ref in models:
        m.setdefault(ref, {})["agentRuntime"] = dict(RUNTIME)
    profiles = (new.get("auth") or {}).get("profiles") or {}
    tokens = [p for p, v in profiles.items() if v.get("provider") == "anthropic" and v.get("mode") == "token"]
    if tokens:
        new["auth"].setdefault("order", {})["anthropic"] = tokens[:1]
    return new


def only_expected_changes(old: dict, new: dict, models: list[str]) -> bool:
    check = copy.deepcopy(new)
    om = ((old.get("agents") or {}).get("defaults") or {}).get("models") or {}
    cm = check["agents"]["defaults"]["models"]
    for ref in models:
        if ref in om:
            cm[ref] = copy.deepcopy(om[ref])
        else:
            del cm[ref]
    if "models" not in ((old.get("agents") or {}).get("defaults") or {}):
        del check["agents"]["defaults"]["models"]
    old_order = ((old.get("auth") or {}).get("order") or {}).get("anthropic")
    if old_order is None:
        check.get("auth", {}).get("order", {}).pop("anthropic", None)
    elif "auth" in check:
        check["auth"]["order"]["anthropic"] = old_order
    return check == old


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "stage", "apply", "rollback"])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--models")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--expect-config")
    ap.add_argument("--backup-dir", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "rollback":
        meta = json.loads((a.backup_dir / "CLAUDE_CLI.json").read_text())
        if sha(a.config.read_bytes()) != meta["after_sha256"]:
            raise SystemExit("refused: config changed since this backup; restore by hand from the preimage")
        pre = (a.backup_dir / "openclaw.json.preimage").read_bytes()
        write(a.config, pre, os.stat(a.config).st_mode & 0o777)
        print(json.dumps({"rolled_back_to": sha(pre)}))
        return 0
    raw = a.config.read_bytes()
    cfg = json.loads(raw)
    models = a.models.split(",") if a.models else anthropic_models(cfg)
    new = candidate(cfg, models)
    if not only_expected_changes(cfg, new, models):
        raise SystemExit("refused: the change would touch more than model runtimes and auth.order.anthropic")
    out = (json.dumps(new, indent=2, ensure_ascii=False) + "\n").encode()
    info = {"models": models, "runtime": RUNTIME, "auth_order_anthropic": ((new.get("auth") or {}).get("order") or {}).get("anthropic"),
            "before_sha256": sha(raw), "after_sha256": sha(out)}
    if a.cmd == "plan":
        print(json.dumps(info, indent=1))
        return 0
    if a.cmd == "stage":
        write(a.out, out, 0o600)
        print(json.dumps({**info, "staged": str(a.out)}, indent=1))
        return 0
    if not (a.expect_config and a.backup_dir):
        raise SystemExit("refused: apply needs --expect-config and --backup-dir")
    if sha(raw) != a.expect_config:
        raise SystemExit(f"refused: config {sha(raw)[:12]} != pinned {a.expect_config[:12]}")
    a.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    write(a.backup_dir / "openclaw.json.preimage", raw, 0o600)
    write(a.backup_dir / "CLAUDE_CLI.json", (json.dumps(info, indent=1) + "\n").encode(), 0o600)
    if sha(a.config.read_bytes()) != info["before_sha256"]:
        raise SystemExit("refused: config changed during apply; nothing written")
    write(a.config, out, os.stat(a.config).st_mode & 0o777)
    print(json.dumps({**info, "verified": sha(a.config.read_bytes()) == info["after_sha256"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
