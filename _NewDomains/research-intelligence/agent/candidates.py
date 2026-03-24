#!/usr/bin/env python3
"""
candidates.py — Review and promote query candidates to config.json

Candidates are extracted by observe.py after each synthesis run.
This script is the human review gate before any query reaches the session runner.

CLI:
  python agent/candidates.py list                   # all candidates (default)
  python agent/candidates.py list --topic X         # filter by topic
  python agent/candidates.py list --status promoted # show history
  python agent/candidates.py promote --id X         # write to config session_searches
  python agent/candidates.py retire  --id X         # mark retired, keep record
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent   # research-intelligence/
CONFIG     = ROOT / "agent" / "config.json"
CANDIDATES = ROOT / "data" / "feedback" / "query_candidates.json"


# ── Storage ───────────────────────────────────────────────────────────────────

def load() -> list[dict]:
    if not CANDIDATES.exists():
        return []
    try:
        return json.loads(CANDIDATES.read_text())
    except json.JSONDecodeError:
        return []


def _save(data: list[dict]):
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(topic: str | None, status: str | None):
    data = load()
    # Default view is the actionable queue — candidates only
    effective_status = status or "candidate"
    filtered = [
        r for r in data
        if (not topic or r.get("topic") == topic)
        and r.get("status") == effective_status
    ]

    if not filtered:
        label = f"topic={topic}, " if topic else ""
        print(f"No records found ({label}status={effective_status}).")
        return

    print(f"\n{'ID':<10} {'Topic':<22} {'Status':<12} {'Query'}")
    print("-" * 95)
    for r in filtered:
        q = r["query"][:57] + "..." if len(r["query"]) > 60 else r["query"]
        print(f"{r['id']:<10} {r.get('topic',''):<22} {r.get('status',''):<12} {q}")
    print(f"\n{len(filtered)} record{'s' if len(filtered) != 1 else ''}  "
          f"(use --status promoted|retired to see history)")


def cmd_promote(id: str):
    data = load()
    rec  = next((r for r in data if r["id"] == id), None)

    if not rec:
        print(f"ID '{id}' not found. Run 'list' to see available IDs.")
        return {"ok": False, "error": f"ID '{id}' not found"}
    if rec["status"] == "promoted":
        print(f"Already promoted: {rec['query']}")
        return {"ok": False, "error": f"Already promoted: {rec['query']}"}
    if rec["status"] == "retired":
        print(f"Cannot promote a retired candidate. Retire status is permanent.")
        return {"ok": False, "error": "Cannot promote a retired candidate. Retire status is permanent."}

    # Write to config.json session_searches
    if not CONFIG.exists():
        print(f"config.json not found at {CONFIG}")
        return {"ok": False, "error": f"config.json not found at {CONFIG}"}

    cfg     = json.loads(CONFIG.read_text())
    topic   = rec["topic"]
    searches = cfg.setdefault("session_searches", {}).setdefault(topic, [])

    if rec["query"] not in searches:
        searches.append(rec["query"])
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        print(f"✅ Promoted → config session_searches[{topic}]:")
        print(f"   {rec['query']}")
    else:
        print(f"Already in config [{topic}]: {rec['query']}")

    rec["status"] = "promoted"
    _save(data)
    return {"ok": True, "id": id, "status": "promoted", "query": rec["query"], "topic": topic}


def cmd_retire(id: str):
    data = load()
    rec  = next((r for r in data if r["id"] == id), None)

    if not rec:
        print(f"ID '{id}' not found. Run 'list' to see available IDs.")
        return {"ok": False, "error": f"ID '{id}' not found"}
    if rec["status"] == "retired":
        print(f"Already retired: {rec['query']}")
        return {"ok": False, "error": f"Already retired: {rec['query']}"}

    rec["status"] = "retired"
    _save(data)
    print(f"✅ Retired: {rec['query']}")
    return {"ok": True, "id": id, "status": "retired", "query": rec["query"]}


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Research Intelligence — query candidate review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    ls = sub.add_parser("list", help="List candidates (default: status=candidate)")
    ls.add_argument("--topic",  default=None, help="Filter by topic slug")
    ls.add_argument("--status", default=None,
                    choices=["candidate", "promoted", "retired"],
                    help="Filter by status (default: candidate)")

    pr = sub.add_parser("promote", help="Promote candidate to config session_searches")
    pr.add_argument("--id", required=True, help="8-char candidate ID from list")

    rt = sub.add_parser("retire", help="Mark candidate retired (kept for audit trail)")
    rt.add_argument("--id", required=True, help="8-char candidate ID from list")

    args = p.parse_args()

    if args.cmd == "list":
        cmd_list(args.topic, args.status)
    elif args.cmd == "promote":
        cmd_promote(args.id)
    elif args.cmd == "retire":
        cmd_retire(args.id)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
