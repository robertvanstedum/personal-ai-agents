#!/usr/bin/env python3
"""
threads.py — Thread management library and CLI for Research Intelligence Agent

A Thread is a named research question with a lifecycle. It is not the same as
a topic. A topic (gold-geopolitics) can have multiple threads over time. Each
thread is a discrete intellectual episode with its own motivation, evidence
base, and conclusion.

CLI:
  python agent/threads.py create   --topic gold-geopolitics
  python agent/threads.py annotate --topic gold-geopolitics --type direction_shift --note "..."
  python agent/threads.py annotate --topic gold-geopolitics --type reaction --ref-session gold-001 --note "..."
  python agent/threads.py wrap-up  --topic gold-geopolitics --note "..."
  python agent/threads.py retire   --topic gold-geopolitics --reason "..."
  python agent/threads.py status   --topic gold-geopolitics
  python agent/threads.py list

Annotation types:
  direction_shift  — change search/triage focus; injected into next session
  reaction         — what you think about what you found
  observation      — something noticed, no direction change
  wrap_up          — closing thought; triggers thread close
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from pydantic import BaseModel, field_validator
except ImportError:
    print("Missing dependency: pip install 'pydantic>=2.0'")
    raise

ROOT    = Path(__file__).resolve().parent.parent
THREADS = ROOT / "data" / "threads"
TOPICS  = ROOT / "topics"

STATIC_FILES = {"CONTEXT.md", "ORIGIN.md", "STORY_FOR_CLAUDE_AI.md", "README.md"}
SESSION_RE   = re.compile(r'^[a-z][a-z0-9-]*\.md$')


# ── Schemas ───────────────────────────────────────────────────────────────────

class ThreadRecord(BaseModel):
    id:                     str
    topic:                  str
    version:                int = 1
    status:                 str = "active"       # active | expired | closed | archived | retired
    opened:                 str
    closed:                 Optional[str] = None
    retired:                bool = False
    auto_close_days:        int = 30
    duration_days:          Optional[int] = None
    expires:                Optional[str] = None   # ISO date string YYYY-MM-DD
    deeper_dive_generated:  bool = False
    deeper_dive_path:       Optional[str] = None
    last_session_date:      Optional[str] = None
    session_count:          int = 0
    links_to:               list[str] = []
    links_from:             list[str] = []
    motivation:             str
    prior_belief:           str
    wrap_up:                Optional[str] = None
    retired_reason:         Optional[str] = None
    created_by:             str = "robert"
    schema_version:         str = "1.0"

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v not in {"active", "expired", "closed", "archived", "retired"}:
            raise ValueError(f"status must be active | expired | closed | archived | retired — got '{v}'")
        return v


class Annotation(BaseModel):
    id:                   str
    thread_id:            str
    timestamp:            str
    type:                 str      # direction_shift | reaction | observation | wrap_up
    level:                str = "thread"    # thread | session | article
    ref_session:          Optional[str] = None
    ref_article:          Optional[str] = None
    note:                 str
    affects_next_session: bool = False
    influenced_sessions:  list[str] = []

    @field_validator("type")
    @classmethod
    def valid_type(cls, v):
        allowed = {"direction_shift", "reaction", "observation", "wrap_up"}
        if v not in allowed:
            raise ValueError(f"type must be one of {allowed}")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _thread_id(topic: str) -> str:
    """Auto-generate thread ID: {topic}-{year}."""
    return f"{topic}-{datetime.now(timezone.utc).year}"


def _next_annotation_id(annotations: list[Annotation]) -> str:
    """Generate next ann-NNN id based on existing list."""
    if not annotations:
        return "ann-001"
    nums = []
    for a in annotations:
        m = re.match(r'^ann-(\d+)$', a.id)
        if m:
            nums.append(int(m.group(1)))
    nxt = max(nums) + 1 if nums else 1
    return f"ann-{nxt:03d}"


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


def _count_sessions(topic: str) -> tuple[int, Optional[str]]:
    """
    Count session files in topics/{topic}/ and return (count, last_session_date).

    Includes: .md files matching ^[a-z][a-z0-9-]*\\.md$ (lowercase first char)
    Excludes: sources-candidates-*, CONTEXT.md, ORIGIN.md, README.md, etc.
    Source of truth: directory contents, not session-log.md.
    """
    topic_dir = TOPICS / topic
    if not topic_dir.exists():
        return 0, None
    session_files = [
        p for p in topic_dir.iterdir()
        if p.is_file()
        and SESSION_RE.match(p.name)
        and p.name not in STATIC_FILES
        and not p.name.startswith("sources-candidates-")
    ]
    if not session_files:
        return 0, None
    newest = max(session_files, key=lambda p: p.stat().st_mtime)
    last_date = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d")
    return len(session_files), last_date


# ── Storage interface (importable by research.py, observe.py, research_routes.py) ──

def load_thread(topic: str) -> Optional[ThreadRecord]:
    p = THREADS / topic / "thread.json"
    if not p.exists():
        return None
    return ThreadRecord(**_load(p, {}))


def save_thread(topic: str, record: ThreadRecord):
    _save(THREADS / topic / "thread.json", record.model_dump())


def load_annotations(topic: str) -> list[Annotation]:
    data = _load(THREADS / topic / "annotations.json", [])
    return [Annotation(**a) for a in data]


def save_annotations(topic: str, annotations: list[Annotation]):
    _save(
        THREADS / topic / "annotations.json",
        [a.model_dump() for a in annotations],
    )


# ── Library functions (called by research.py and observe.py) ──────────────────

def check_direction_shifts(topic: str) -> Optional[dict]:
    """
    Return the latest unprocessed direction_shift annotation for a topic, or None.
    'Unprocessed' means affects_next_session=True and influenced_sessions is empty.
    Called by research.py at session start.
    """
    for ann in reversed(load_annotations(topic)):
        if (ann.type == "direction_shift"
                and ann.affects_next_session
                and not ann.influenced_sessions):
            return ann.model_dump()
    return None


def get_active_direction(topic: str) -> Optional[dict]:
    """
    Alias for check_direction_shifts, named for observe.py use.
    Returns latest active direction shift or None.
    """
    return check_direction_shifts(topic)


def mark_influenced(topic: str, annotation_id: str, session_id: str):
    """
    Record that session_id was influenced by annotation_id.
    Called by research.py at session end.
    Updates influenced_sessions in annotations.json.
    """
    annotations = load_annotations(topic)
    for ann in annotations:
        if ann.id == annotation_id and session_id not in ann.influenced_sessions:
            ann.influenced_sessions.append(session_id)
    save_annotations(topic, annotations)


# ── CLI commands ──────────────────────────────────────────────────────────────

def cmd_create(topic: str, motivation: str, prior_belief: str):
    """Create a new thread for a topic."""
    existing = load_thread(topic)
    if existing and existing.status == "active":
        print(f"⚠️  Active thread already exists for '{topic}': {existing.id}")
        print("   Use annotate --type wrap_up to close it before opening a new one.")
        return

    count, last_date = _count_sessions(topic)
    tid = _thread_id(topic)

    record = ThreadRecord(
        id=tid,
        topic=topic,
        opened=_now(),
        motivation=motivation,
        prior_belief=prior_belief,
        session_count=count,
        last_session_date=last_date,
    )
    save_thread(topic, record)

    # Create empty annotations file if missing
    ann_path = THREADS / topic / "annotations.json"
    if not ann_path.exists():
        _save(ann_path, [])

    print(f"✅ Thread created: {tid}")
    print(f"   Sessions found: {count}")
    print(f"   Last session:   {last_date or 'none'}")


def cmd_annotate(
    topic: str,
    ann_type: str,
    note: str,
    ref_session: Optional[str] = None,
    ref_article: Optional[str] = None,
):
    """Add an annotation to a thread."""
    thread = load_thread(topic)
    if not thread:
        print(f"✗ No thread found for '{topic}'. Run: python agent/threads.py create --topic {topic}")
        return

    annotations = load_annotations(topic)
    ann_id = _next_annotation_id(annotations)
    level  = "session" if ref_session else ("article" if ref_article else "thread")

    ann = Annotation(
        id=ann_id,
        thread_id=thread.id,
        timestamp=_now(),
        type=ann_type,
        level=level,
        ref_session=ref_session,
        ref_article=ref_article,
        note=note,
        affects_next_session=(ann_type == "direction_shift"),
    )
    annotations.append(ann)
    save_annotations(topic, annotations)

    if ann_type == "wrap_up":
        thread.status  = "closed"
        thread.closed  = _now()
        thread.wrap_up = note
        save_thread(topic, thread)
        print(f"✅ {ann_id} recorded — thread '{thread.id}' closed.")
    else:
        print(f"✅ {ann_id} recorded ({ann_type}) on thread '{thread.id}'")
        if ann_type == "direction_shift":
            print("   → Will inject into next research session and observe run.")


def cmd_wrap_up(topic: str, note: str):
    """Close a thread with a conclusion note."""
    cmd_annotate(topic, "wrap_up", note)


def cmd_retire(topic: str, reason: str):
    """Retire a thread — hidden from UI, preserved on disk."""
    thread = load_thread(topic)
    if not thread:
        print(f"✗ No thread found for '{topic}'.")
        return
    thread.status        = "retired"
    thread.retired       = True
    thread.retired_reason = reason
    save_thread(topic, thread)
    print(f"✅ Thread '{thread.id}' retired.")
    print(f"   Reason: {reason}")
    print("   Thread preserved on disk, hidden from dashboard.")


def cmd_status(topic: str):
    """Print thread status and recent annotations."""
    thread = load_thread(topic)
    if not thread:
        print(f"✗ No thread for '{topic}'.")
        return
    print(f"\n{'─'*60}")
    print(f"Thread:  {thread.id}  [{thread.status.upper()}]")
    print(f"Opened:  {thread.opened}")
    print(f"Sessions: {thread.session_count}  |  Last: {thread.last_session_date or 'none'}")
    print(f"\nMotivation:\n  {thread.motivation}")
    print(f"\nPrior belief:\n  {thread.prior_belief}")
    if thread.wrap_up:
        print(f"\nConclusion:\n  {thread.wrap_up}")

    annotations = load_annotations(topic)
    if annotations:
        print(f"\nAnnotations ({len(annotations)}):")
        for ann in annotations[-5:]:
            tag = "[→ session]" if ann.affects_next_session and not ann.influenced_sessions else ""
            ref = f" [ref: {ann.ref_session or ann.ref_article}]" if ann.ref_session or ann.ref_article else ""
            print(f"  {ann.id}  {ann.type:<18} {ann.timestamp[:10]}{ref} {tag}")
            print(f"    {ann.note[:100]}{'...' if len(ann.note) > 100 else ''}")
    else:
        print("\nNo annotations yet.")
    print(f"{'─'*60}\n")


def cmd_list():
    """List all threads across all topics."""
    if not THREADS.exists():
        print("No threads directory found.")
        return
    topics = sorted(THREADS.iterdir())
    if not topics:
        print("No threads yet.")
        return
    print(f"\n{'Topic':<22} {'Thread ID':<28} {'Status':<10} {'Sessions'}")
    print("─" * 75)
    for t in topics:
        thread = load_thread(t.name)
        if thread:
            print(f"{thread.topic:<22} {thread.id:<28} {thread.status:<10} {thread.session_count}")
    print()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Research Intelligence — thread manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    cr = sub.add_parser("create", help="Create a new thread for a topic")
    cr.add_argument("--topic",        required=True)
    cr.add_argument("--motivation",   default=None, help="Why you opened this thread")
    cr.add_argument("--prior-belief", default=None, help="What you believed before evidence")

    an = sub.add_parser("annotate", help="Add a note to a thread")
    an.add_argument("--topic",       required=True)
    an.add_argument("--type",        required=True,
                    choices=["direction_shift", "reaction", "observation", "wrap_up"])
    an.add_argument("--note",        required=True)
    an.add_argument("--ref-session", default=None)
    an.add_argument("--ref-article", default=None)

    wu = sub.add_parser("wrap-up", help="Close a thread with a conclusion")
    wu.add_argument("--topic", required=True)
    wu.add_argument("--note",  required=True)

    rt = sub.add_parser("retire", help="Retire a thread (hidden, preserved)")
    rt.add_argument("--topic",  required=True)
    rt.add_argument("--reason", required=True)

    st = sub.add_parser("status", help="Print thread status and annotations")
    st.add_argument("--topic", required=True)

    sub.add_parser("list", help="List all threads")

    args = p.parse_args()

    if args.cmd == "create":
        motivation   = args.motivation   or input("Motivation (why did you open this thread?): ").strip()
        prior_belief = args.prior_belief or input("Prior belief (what did you believe before?): ").strip()
        cmd_create(args.topic, motivation, prior_belief)

    elif args.cmd == "annotate":
        cmd_annotate(
            args.topic, args.type, args.note,
            ref_session=args.ref_session,
            ref_article=args.ref_article,
        )

    elif args.cmd == "wrap-up":
        cmd_wrap_up(args.topic, args.note)

    elif args.cmd == "retire":
        cmd_retire(args.topic, args.reason)

    elif args.cmd == "status":
        cmd_status(args.topic)

    elif args.cmd == "list":
        cmd_list()

    else:
        p.print_help()


if __name__ == "__main__":
    main()
