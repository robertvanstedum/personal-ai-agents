#!/usr/bin/env python3
"""
leanings.py — Leaning object CRUD for Research Intelligence Agent

A Leaning is a belief-in-progress that spans one or more Topics.
States: question → leaning → hold

Schema (leanings.json):
  {
    "schema_version": "1.0",
    "leanings": [
      {
        "id": "a1b2c3d4",          # 8-char hex
        "title": "...",            # The statement being leaned toward
        "state": "question",       # question | leaning | hold
        "created": "2026-05-31",
        "updated": "2026-05-31",
        "topics": ["quad-..."],    # linked Topic slugs
        "evidence": [
          {
            "id": "ev-a1b2",
            "title": "...",
            "url": "...",
            "source": "...",       # publication / author
            "stance": "supports",  # supports | complicates | neutral
            "added": "2026-05-31",
            "note": ""
          }
        ],
        "teammate_read": null,     # null | {text, generated_at, evidence_count_at_read}
        "notes": ""
      }
    ]
  }
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT          = Path(__file__).resolve().parent.parent   # research-intelligence/
LEANINGS_FILE = ROOT / "data" / "leanings" / "leanings.json"

VALID_STATES  = {"question", "leaning", "hold"}
VALID_STANCES = {"supports", "complicates", "neutral"}

# ── I/O ───────────────────────────────────────────────────────────────────────

def _load() -> dict:
    LEANINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LEANINGS_FILE.exists():
        return {"schema_version": "1.0", "leanings": []}
    return json.loads(LEANINGS_FILE.read_text())


def _save(data: dict) -> None:
    LEANINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEANINGS_FILE.write_text(json.dumps(data, indent=2))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _new_id(prefix: str = "") -> str:
    return (prefix + secrets.token_hex(4))[:8]


# ── CRUD ──────────────────────────────────────────────────────────────────────

def list_leanings() -> list[dict]:
    return _load().get("leanings", [])


def get_leaning(leaning_id: str) -> Optional[dict]:
    for l in _load().get("leanings", []):
        if l["id"] == leaning_id:
            return l
    return None


def create_leaning(title: str, state: str = "question",
                   topics: Optional[list] = None, notes: str = "") -> dict:
    if state not in VALID_STATES:
        raise ValueError(f"state must be one of {VALID_STATES}")
    data = _load()
    leaning = {
        "id":      _new_id(),
        "title":   title.strip(),
        "state":   state,
        "created": _today(),
        "updated": _today(),
        "topics":  topics or [],
        "evidence": [],
        "teammate_read": None,
        "notes": notes.strip(),
    }
    data["leanings"].append(leaning)
    _save(data)
    return leaning


def update_leaning(leaning_id: str, **kwargs) -> Optional[dict]:
    """Update title, state, notes, or topics on an existing leaning."""
    data  = _load()
    found = None
    for l in data["leanings"]:
        if l["id"] == leaning_id:
            found = l
            break
    if found is None:
        return None
    allowed = {"title", "state", "notes", "topics"}
    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "state" and v not in VALID_STATES:
            raise ValueError(f"state must be one of {VALID_STATES}")
        found[k] = v
    found["updated"] = _today()
    _save(data)
    return found


def delete_leaning(leaning_id: str) -> bool:
    data = _load()
    before = len(data["leanings"])
    data["leanings"] = [l for l in data["leanings"] if l["id"] != leaning_id]
    if len(data["leanings"]) == before:
        return False
    _save(data)
    return True


# ── Evidence ──────────────────────────────────────────────────────────────────

def add_evidence(leaning_id: str, title: str, url: str, source: str = "",
                 stance: str = "neutral", note: str = "") -> Optional[dict]:
    if stance not in VALID_STANCES:
        raise ValueError(f"stance must be one of {VALID_STANCES}")
    data  = _load()
    found = None
    for l in data["leanings"]:
        if l["id"] == leaning_id:
            found = l
            break
    if found is None:
        return None
    ev = {
        "id":     "ev-" + secrets.token_hex(3),
        "title":  title.strip(),
        "url":    url.strip(),
        "source": source.strip(),
        "stance": stance,
        "added":  _today(),
        "note":   note.strip(),
    }
    found["evidence"].append(ev)
    found["updated"] = _today()
    _save(data)
    return ev


def remove_evidence(leaning_id: str, evidence_id: str) -> bool:
    data  = _load()
    found = None
    for l in data["leanings"]:
        if l["id"] == leaning_id:
            found = l
            break
    if found is None:
        return False
    before = len(found["evidence"])
    found["evidence"] = [e for e in found["evidence"] if e["id"] != evidence_id]
    if len(found["evidence"]) == before:
        return False
    found["updated"] = _today()
    _save(data)
    return True


# ── Teammate read ─────────────────────────────────────────────────────────────

def needs_badge(leaning: dict) -> bool:
    """True if new evidence has been added since the last teammate read."""
    tr = leaning.get("teammate_read")
    if not tr:
        return bool(leaning.get("evidence"))
    return len(leaning.get("evidence", [])) > tr.get("evidence_count_at_read", 0)


def store_teammate_read(leaning_id: str, text: str) -> Optional[dict]:
    data  = _load()
    found = None
    for l in data["leanings"]:
        if l["id"] == leaning_id:
            found = l
            break
    if found is None:
        return None
    found["teammate_read"] = {
        "text":                   text,
        "generated_at":           datetime.now(timezone.utc).isoformat(),
        "evidence_count_at_read": len(found.get("evidence", [])),
    }
    found["updated"] = _today()
    _save(data)
    return found["teammate_read"]


def build_teammate_prompt(leaning: dict, thread_summaries: list[str]) -> tuple[str, str]:
    """Build (system, user) prompt for teammate read."""
    state_label = {"question": "open question", "leaning": "working lean",
                   "hold": "held position"}.get(leaning["state"], leaning["state"])

    evidence_lines = []
    for e in leaning.get("evidence", []):
        stance_sym = {"supports": "▲", "complicates": "▽", "neutral": "○"}.get(e["stance"], "○")
        line = f"  {stance_sym} [{e['stance']}] {e['title']}"
        if e.get("source"):
            line += f" — {e['source']}"
        if e.get("note"):
            line += f"\n     Note: {e['note']}"
        evidence_lines.append(line)

    evidence_block = "\n".join(evidence_lines) if evidence_lines else "  (no evidence attached yet)"

    thread_block = ""
    if thread_summaries:
        thread_block = "\n\n## Related research thread excerpts\n" + "\n---\n".join(thread_summaries)

    system = (
        "You are a research teammate giving a direct, honest read on a belief-in-progress. "
        "Be concise (150–250 words). No hedging. Lead with your assessment, then implication. "
        "If the evidence is thin, say so. If the belief looks solid, say that too."
    )

    user = f"""Leaning: {leaning['title']}
Status: {state_label}

Evidence ({len(leaning.get('evidence', []))} items):
{evidence_block}{thread_block}

Give me:
1. Assessment — does the evidence support, complicate, or leave this lean unresolved?
2. Implication — what follows if this lean is right? What's the risk if it's wrong?
3. What's missing — one key piece of evidence that would most change your read.

Keep it tight. This is a thinking note, not a report."""

    return system, user
