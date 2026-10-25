#!/usr/bin/env python3
"""Set native OpenClaw's auth order for one provider to a single profile: no silent fallback to a metered key.

    openclaw_auth_order.py plan     --provider anthropic --profile anthropic:manual
    openclaw_auth_order.py apply    --provider anthropic --profile anthropic:manual --expect-config SHA --backup-dir DIR
    openclaw_auth_order.py rollback --backup-dir DIR

Changes exactly one value, ``auth.order.<provider>``, in ``~/.openclaw/openclaw.json``. Every other byte of meaning is preserved
(checked by comparing the whole parsed config with only that key restored). The profile must already exist in ``auth.profiles``.
OpenClaw hot-reloads ``auth.order`` (observed in its log on 24 Oct: "config hot reload applied (auth.order.anthropic)"), so no
gateway restart is needed. Never prints or reads a secret value: profiles hold modes, not keys. Standard library only."""
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "apply", "rollback"])
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--provider")
    ap.add_argument("--profile")
    ap.add_argument("--expect-config")
    ap.add_argument("--backup-dir", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "rollback":
        meta = json.loads((a.backup_dir / "AUTH_ORDER.json").read_text())
        cur = a.config.read_bytes()
        if sha(cur) != meta["after_sha256"]:
            raise SystemExit("refused: config changed since this backup was made; restore by hand from the preimage")
        pre = (a.backup_dir / "openclaw.json.preimage").read_bytes()
        write(a.config, pre, os.stat(a.config).st_mode & 0o777)
        print(json.dumps({"rolled_back_to": sha(pre)}))
        return 0

    raw = a.config.read_bytes()
    cfg = json.loads(raw)
    profiles = (cfg.get("auth") or {}).get("profiles") or {}
    prof = profiles.get(a.profile)
    if not prof or prof.get("provider") != a.provider:
        raise SystemExit(f"refused: {a.profile} is not a configured {a.provider} profile")
    before = ((cfg.get("auth") or {}).get("order") or {}).get(a.provider)
    new = copy.deepcopy(cfg)
    new.setdefault("auth", {}).setdefault("order", {})[a.provider] = [a.profile]
    check = copy.deepcopy(new)
    if before is None:
        del check["auth"]["order"][a.provider]
    else:
        check["auth"]["order"][a.provider] = before
    if check != cfg:
        raise SystemExit("refused: the change would touch more than auth.order")
    out = (json.dumps(new, indent=2, ensure_ascii=False) + "\n").encode()
    info = {"provider": a.provider, "order_before": before, "order_after": [a.profile], "mode": prof.get("mode"),
            "before_sha256": sha(raw), "after_sha256": sha(out)}
    if a.cmd == "plan":
        print(json.dumps(info, indent=1))
        return 0
    if not (a.expect_config and a.backup_dir):
        raise SystemExit("refused: apply needs --expect-config and --backup-dir")
    if sha(raw) != a.expect_config:
        raise SystemExit(f"refused: config {sha(raw)[:12]} != pinned {a.expect_config[:12]}")
    a.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    write(a.backup_dir / "openclaw.json.preimage", raw, 0o600)
    write(a.backup_dir / "AUTH_ORDER.json", (json.dumps(info, indent=1) + "\n").encode(), 0o600)
    if sha(a.config.read_bytes()) != info["before_sha256"]:
        raise SystemExit("refused: config changed during apply; nothing written")
    write(a.config, out, os.stat(a.config).st_mode & 0o777)
    print(json.dumps({**info, "verified": sha(a.config.read_bytes()) == info["after_sha256"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
