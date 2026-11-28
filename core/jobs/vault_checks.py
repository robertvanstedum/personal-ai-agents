"""Vault correctness checks and the canary contract (WS3 C1.4). Read-only; standard library only; never logs content.

Two notions are kept apart on purpose (Codex acceptance of C1.3, instructions for C1.4):

* **mode match**: is each ask service serving the mode that is *approved now* (``config/desired_state.json``,
  ``desired_mode``)? Keyword-only while promotion is pending is a match, not a fault.
* **objective**: the business goal, ``usable_meaning_search``. It is ``met`` only when the served index is in meaning
  mode **and** the service's canary has passed. A mode match alone never certifies it; keyword serving is ``pending``;
  meaning serving without a passing canary is ``unverified``.

Every input that cannot be read stays ``unknown``; nothing unreadable reads as fine.

    read_meta(index_path)                  -> (state, meta)   state: ok | missing | corrupt
    check_service(desired, state, meta, now, canary=None) -> {meta, mode, mode_match, objective, privacy}
    canary_spec(desired_doc, service)      -> CanarySpec | None   (None: not configured, the owner has not chosen)
    run_canary(spec, ask)                  -> result code     (not_configured | pass | miss | fallback | error)

The metadata keys are the index's own ``meta`` table (``core/memory_index``): ``keyword_only``, ``embedder_digest``,
``built_at``, ``updated_at``, ``profile``, ``career_filter_digest``, ``career_filter_version``.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.jobs import status

MODES = ("keyword", "meaning")
CAREER_KEYS = ("profile", "career_filter_digest", "career_filter_version")


def load_desired(path) -> dict | None:
    """The desired-state document, or None when it is missing or not this schema (callers report ``unknown``)."""
    try:
        doc = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("services"), dict):
        return None
    return doc


def read_meta(index_path) -> tuple[str, dict]:
    path = Path(index_path)
    if not path.is_file():
        return "missing", {}
    try:
        con = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT key, value FROM meta").fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return "corrupt", {}
    return "ok", {str(k): (None if v is None else str(v)) for k, v in rows}


def _mode(meta: dict) -> tuple[str, bool]:
    """(mode, consistent). keyword_only=1 with an embedder digest, or neither marker, is inconsistent."""
    keyword, digest = meta.get("keyword_only") == "1", bool(meta.get("embedder_digest"))
    if keyword and not digest:
        return "keyword", True
    if digest and not keyword:
        return "meaning", True
    return "unknown", False


def check_service(desired: dict | None, meta_state: str, meta: dict, now: datetime, canary: str | None = None) -> dict:
    out = {"meta": meta_state, "mode": "unknown", "mode_match": "unknown", "objective": "unknown", "privacy": "unknown"}
    if desired is None or desired.get("desired_mode") not in MODES:
        return out                                           # no approved state to compare with: unknown
    if meta_state != "ok":
        return out
    mode, consistent = _mode(meta)
    if not consistent:
        out["meta"] = "inconsistent"
        return out
    out["mode"] = mode
    stamp = status.parse_time(meta.get("updated_at") or meta.get("built_at"))
    if stamp is None or stamp > now:
        out["meta"] = "inconsistent"                         # no usable build stamp, or one in the future
    elif desired.get("max_age_s") and (now - stamp).total_seconds() > int(desired["max_age_s"]):
        out["meta"] = "stale"                                # still judged below; staleness is its own signal
    out["mode_match"] = "match" if mode == desired["desired_mode"] else "mismatch"
    if desired.get("objective") == "usable_meaning_search":
        out["objective"] = ("pending" if mode == "keyword"
                            else "met" if canary == "pass" else "unverified")
    out["privacy"] = _privacy(desired, meta)
    return out


def _privacy(desired: dict, meta: dict) -> str:
    rule = desired.get("career_filter")
    have = {k: meta.get(k) for k in CAREER_KEYS}
    if rule == "required":
        if not all(have.values()):
            return "violated"                                # MC's index without its filter stamp: treat as exposed
        want = desired.get("career_filter_digest")
        return "violated" if (want and have["career_filter_digest"] != want) else "ok"
    if rule == "none":
        return "not_required"
    return "unknown"


@dataclass(frozen=True)
class CanarySpec:
    service: str
    record_ref: str          # an opaque reference to the expected record, never its text
    question_ref: str        # an opaque reference the caller resolves privately to the question; never logged


def canary_spec(desired_doc: dict | None, service: str) -> CanarySpec | None:
    raw = ((desired_doc or {}).get("canary") or {}).get(service)
    if not isinstance(raw, dict):
        return None
    record, question = raw.get("record_ref"), raw.get("question_ref")
    if not (isinstance(record, str) and record and isinstance(question, str) and question):
        return None
    return CanarySpec(service, record, question)


def run_canary(spec: CanarySpec | None, ask: Callable[[CanarySpec], dict]) -> str:
    """One canary: ``ask(spec)`` resolves the question privately and returns ``{"mode": keyword|meaning,
    "record_refs": [...]}``. The result is a code only: the question, the answer and the records are never returned or
    logged. A hit served by keyword fallback is ``fallback``, never ``pass``: it proves no meaning search."""
    if spec is None:
        return "not_configured"
    try:
        reply = ask(spec)
    except Exception:                                        # noqa: BLE001
        return "error"
    if not isinstance(reply, dict) or not isinstance(reply.get("record_refs"), list):
        return "error"
    hit = spec.record_ref in reply["record_refs"]
    if reply.get("mode") != "meaning":
        return "fallback" if hit else "miss"
    return "pass" if hit else "miss"
