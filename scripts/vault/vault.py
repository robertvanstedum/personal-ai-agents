#!/usr/bin/env python3
"""vault.py: the portable, headless Thinking Store tools (T1). No model, no network, no database, no MiniMoi.

  vault.py --root SHELF list | topics | coverage | decisions
  vault.py --root SHELF open RECORD [--edition N]          the exact retained edition bytes
  vault.py --root SHELF search TERM [--ignore-case] [--all-editions] [--limit N]
  vault.py --root SHELF export --to NEWDIR [--topic T]     a topic, or everything this caller may read
  vault.py verify SHELF_OR_EXPORT
  vault.py restore EXPORT --to NEWDIR                      a clean shelf-shaped folder, verified

Every command prints one JSON object (``open`` prints the edition bytes). Exit codes: 0 fine, 2 refused, 5 damaged or failed to verify,
8 not found. Mandate-scoped records are visible only with --mandate ID. Nothing here writes into the shelf it reads.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.vault_t1 import portable                              # noqa: E402
from core.vault_t1.reader import Vault, VaultError              # noqa: E402


def out(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, sort_keys=True, ensure_ascii=False))
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="the shelf folder (required for read commands)")
    ap.add_argument("--mandate", action="append", default=[], help="a mandate ID whose records this caller may read (repeatable)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "topics", "coverage", "decisions"):
        sub.add_parser(name)
    o = sub.add_parser("open")
    o.add_argument("record")
    o.add_argument("--edition", type=int)
    s = sub.add_parser("search")
    s.add_argument("term")
    s.add_argument("--ignore-case", action="store_true")
    s.add_argument("--all-editions", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    e = sub.add_parser("export")
    e.add_argument("--to", required=True)
    e.add_argument("--topic")
    e.add_argument("--allow-incomplete", action="store_true", help="export what can be read even if some shelf entries cannot (the manifest says so)")
    v = sub.add_parser("verify")
    v.add_argument("path")
    r = sub.add_parser("restore")
    r.add_argument("export")
    r.add_argument("--to", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "verify":
            report = portable.verify(a.path)
            return out({"ok": report["ok"], **report}, 0 if report["ok"] else 5)
        if a.cmd == "restore":
            return out({"ok": True, **portable.restore(a.export, a.to)})
        if not a.root:
            return out({"ok": False, "status": "refused", "reason": "root_required"}, 2)
        vault = Vault(a.root, tuple(a.mandate))
        if a.cmd == "open":
            _, _, data = vault.open(a.record, a.edition)
            sys.stdout.buffer.write(data)
            return 0
        if a.cmd == "search":
            return out({"ok": True, **vault.search(a.term, ignore_case=a.ignore_case, all_editions=a.all_editions, limit=max(1, min(a.limit, 500)))})
        if a.cmd == "export":
            manifest = portable.export(vault, a.to, topic=a.topic, allow_incomplete=a.allow_incomplete)
            return out({"ok": True, "complete": manifest["complete"], "counts": manifest["counts"], "left_out": manifest["left_out"], "manifest": "manifest.json"})
        return out({"ok": True, **getattr(vault, a.cmd)()})
    except OSError as exc:
        return out({"ok": False, "status": "failed", "reason": "output_error", "errno": exc.errno}, 5)
    except VaultError as exc:
        return out({"ok": False, "status": type(exc).__name__.lower(), "reason": exc.code, **({"detail": exc.detail} if exc.detail else {})}, exc.exit)


if __name__ == "__main__":
    sys.exit(main())
