#!/usr/bin/env python3
"""
feedback.py — Signal storage for Research Intelligence Agent

CLI:
  python agent/feedback.py save --url URL --title TITLE --session SESSION [--note TEXT]
  python agent/feedback.py drop --domain DOMAIN --session SESSION [--note TEXT]
  python agent/feedback.py redirect --topic TOPIC --session SESSION [--note TEXT]
  python agent/feedback.py good --session SESSION [--note TEXT]
  python agent/feedback.py weak --session SESSION [--note TEXT]
  python agent/feedback.py note --session SESSION --text TEXT
  python agent/feedback.py weights

Weight range: 0.1 (floor — no query ever fully suppressed) to 3.0 (ceiling —
no query dominates). Starts at 1.0. good/save push up, weak pushes down.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from pydantic import BaseModel, field_validator
except ImportError:
    print("Missing dependency: pip install pydantic")
    raise

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "feedback"
ARTICLE_SIGNALS = DATA / "article_signals.json"
SESSION_SIGNALS = DATA / "session_signals.json"
QUERY_PERF      = DATA / "query_performance.json"

# ── Schemas ───────────────────────────────────────────────────────────────────

class ArticleSignal(BaseModel):
    url: str
    title: str
    session_id: str
    signal: str           # save | drop | redirect
    domain: Optional[str] = None   # populated for drop
    topic: Optional[str] = None    # populated for redirect
    note: Optional[str] = None
    timestamp: str

    @field_validator("signal")
    @classmethod
    def valid_signal(cls, v):
        if v not in {"save", "drop", "redirect"}:
            raise ValueError(f"signal must be one of: save, drop, redirect — got '{v}'")
        return v


class SessionSignal(BaseModel):
    session_id: str
    signal: str           # good | weak | note
    note: Optional[str] = None
    timestamp: str

    @field_validator("signal")
    @classmethod
    def valid_signal(cls, v):
        if v not in {"good", "weak", "note"}:
            raise ValueError(f"signal must be one of: good, weak, note — got '{v}'")
        return v


class QueryPerformance(BaseModel):
    """
    Keyed by query_id (first 8 chars of SHA-256 of query text).
    Using a stable hash means a minor query reword gets a new key and fresh
    history, while an exact-match reuse accumulates correctly. Better than
    using the raw string as key — avoids orphaned history on small text edits.

    Weight range: 0.1–3.0. Floor ensures no query is permanently suppressed.
    Ceiling prevents any single strong query from crowding out exploration.
    """
    query_id: str
    query: str
    sessions_used: List[str] = []
    avg_score: float = 0.0
    high_value_hits: int = 0
    weight: float = 1.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def query_id(query_text: str) -> str:
    """Stable 8-char hash of query text. Survives minor rewording only if exact match."""
    return hashlib.sha256(query_text.encode()).hexdigest()[:8]


def _load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def _save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ── Writers ───────────────────────────────────────────────────────────────────

def record_article_signal(
    signal: str,
    session_id: str,
    url: str = "",
    title: str = "",
    domain: str | None = None,
    topic: str | None = None,
    note: str | None = None,
) -> ArticleSignal:
    rec = ArticleSignal(
        url=url, title=title, session_id=session_id, signal=signal,
        domain=domain, topic=topic, note=note, timestamp=_now(),
    )
    data = _load(ARTICLE_SIGNALS, [])
    # Dedup: skip if a save signal for this URL already exists
    if signal == "save" and url and any(
        r.get("signal") == "save" and r.get("url") == url for r in data
    ):
        print(f"⚠️  Duplicate save skipped: {url[:80]}")
        return None
    data.append(rec.model_dump(exclude_none=True))
    _save(ARTICLE_SIGNALS, data)
    return rec


def record_session_signal(
    signal: str,
    session_id: str,
    note: str | None = None,
) -> SessionSignal:
    rec = SessionSignal(session_id=session_id, signal=signal,
                        note=note, timestamp=_now())
    data = _load(SESSION_SIGNALS, [])
    data.append(rec.model_dump(exclude_none=True))
    _save(SESSION_SIGNALS, data)
    return rec


# ── Query weights ─────────────────────────────────────────────────────────────

def load_query_perf() -> dict[str, QueryPerformance]:
    return {k: QueryPerformance(**v) for k, v in _load(QUERY_PERF, {}).items()}


def save_query_perf(perf: dict[str, QueryPerformance]):
    _save(QUERY_PERF, {k: v.model_dump() for k, v in perf.items()})


def register_query(query_text: str, session_id: str) -> QueryPerformance:
    """Ensure a query exists in the performance table and mark session use."""
    perf = load_query_perf()
    qid  = query_id(query_text)
    if qid not in perf:
        perf[qid] = QueryPerformance(query_id=qid, query=query_text)
    qp = perf[qid]
    if session_id not in qp.sessions_used:
        qp.sessions_used.append(session_id)
    save_query_perf(perf)
    return qp


def adjust_weights(session_id: str, signal: str):
    """
    good  → +0.1 on all queries used in this session
    weak  → -0.1 on all queries used in this session
    save  → +0.2 on all queries used in this session (article-level save)
    Weight clamped to [0.1, 3.0].
    """
    delta = {"good": +0.1, "weak": -0.1, "save": +0.2}.get(signal, 0.0)
    if delta == 0.0:
        return
    perf = load_query_perf()
    for qp in perf.values():
        if session_id in qp.sessions_used:
            qp.weight = round(max(0.1, min(3.0, qp.weight + delta)), 2)
    save_query_perf(perf)


def print_weights():
    perf = load_query_perf()
    if not perf:
        print("No query performance data yet.")
        return
    print(f"\n{'ID':<10} {'Weight':<8} {'Sessions':<14} {'Query'}")
    print("-" * 90)
    for qp in sorted(perf.values(), key=lambda q: q.weight, reverse=True):
        q = qp.query[:55] + "..." if len(qp.query) > 58 else qp.query
        print(f"{qp.query_id:<10} {qp.weight:<8.2f} {','.join(qp.sessions_used):<14} {q}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Research Intelligence — feedback recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    sv = sub.add_parser("save",     help="Mark article as high value")
    sv.add_argument("--url",     required=True)
    sv.add_argument("--title",   default="")
    sv.add_argument("--session", required=True)
    sv.add_argument("--note",    default=None)

    dr = sub.add_parser("drop",     help="Blacklist a domain")
    dr.add_argument("--domain",  required=True)
    dr.add_argument("--session", required=True)
    dr.add_argument("--note",    default=None)

    rd = sub.add_parser("redirect", help="Flag a query thread for replacement")
    rd.add_argument("--topic",   required=True)
    rd.add_argument("--session", required=True)
    rd.add_argument("--note",    default=None)

    for sig in ("good", "weak"):
        x = sub.add_parser(sig, help=f"Mark session quality as {sig}")
        x.add_argument("--session", required=True)
        x.add_argument("--note",    default=None)

    nt = sub.add_parser("note",     help="Freeform annotation on a session")
    nt.add_argument("--session", required=True)
    nt.add_argument("--text",    required=True)

    sub.add_parser("weights",       help="Print query weight table")

    args = p.parse_args()

    if args.cmd == "save":
        rec = record_article_signal("save", args.session,
                                    url=args.url, title=args.title, note=args.note)
        if rec is not None:
            adjust_weights(args.session, "save")
            print(f"✅ Saved: {rec.url[:80]}")

    elif args.cmd == "drop":
        rec = record_article_signal("drop", args.session,
                                    domain=args.domain, note=args.note)
        print(f"✅ Dropped domain: {rec.domain}")

    elif args.cmd == "redirect":
        rec = record_article_signal("redirect", args.session,
                                    topic=args.topic, note=args.note)
        print(f"✅ Redirect flagged: {rec.topic}")

    elif args.cmd in ("good", "weak"):
        record_session_signal(args.cmd, args.session, note=args.note)
        adjust_weights(args.session, args.cmd)
        print(f"✅ Session signal recorded: {args.cmd}")

    elif args.cmd == "note":
        record_session_signal("note", args.session, note=args.text)
        print(f"✅ Note recorded for session {args.session}")

    elif args.cmd == "weights":
        print_weights()

    else:
        p.print_help()


if __name__ == "__main__":
    main()
