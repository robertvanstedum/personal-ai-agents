"""Vault correctness checks and the canary contract (WS3 C1.4). Read-only; standard library only; never logs content.

Two notions are kept apart on purpose:

* **mode match**: is each ask service's index in the mode that is *approved now* (``config/desired_state.json``,
  ``desired_mode``)? Keyword-only while promotion is pending is a match, not a fault. It is computed only when the
  index file is structurally a served index (below); a metadata stamp alone is a *declared* mode, nothing more.
* **objective** ``usable_meaning_search``: keyword serving is ``pending``. Meaning serving is ``met`` only when every
  prerequisite holds (policy valid, metadata ``ok`` and fresh, structure ``ok``, privacy acceptable) **and** the
  service's canary passed; a passing canary with any prerequisite failing is ``blocked``; meaning serving with the
  prerequisites but no passing canary is ``unverified``. Freshness is a prerequisite: a stale index is not "usable".

Every check reports its own fixed code; anything that cannot be read or is malformed is ``unknown`` or ``invalid``,
never fine (Codex C1.4 review, 28 Nov 2026).

    load_desired(path)                     -> document or None
    validate_service(raw) / validate_canary(doc, service) -> policy / spec, or the code "invalid"
    inspect_index(path)                    -> (meta_state, meta, structure)
    check_service(policy, meta_state, meta, structure, now, canary=None) -> {policy, meta, structure, mode,
                                              mode_match, privacy, objective}
    run_canary(spec, ask)                  -> not_configured | pass | miss | fallback | error

**Privacy (MC):** ``required`` means the exact MC profile (``mc``) and the supported filter version (``1``) with a
digest present: ``stamp_ok``. Only when the policy pins an expected digest and it matches is it ``verified``. A stamp
is conformance of the index's metadata, not proof that every career record was excluded; that narrower behaviour is
the indexer's own (``core/memory_index/career_filter``) and its tests.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.jobs import status

MODES = ("keyword", "meaning")
OBJECTIVES = ("usable_meaning_search",)
CAREER_RULES = ("required", "none")
MC_PROFILE, MC_FILTER_VERSION = "mc", "1"        # core/memory_index/career_filter.PROFILE, FILTER_VERSION (test-pinned)
INDEX_SCHEMA_VERSION = "2"                        # core/memory_index/store.SCHEMA_VERSION (test-pinned)
BASE_TABLES = ("meta", "records", "chunks", "fts")
MAX_AGE_LIMIT_S = 30 * 86400
_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_DIGEST = re.compile(r"^[A-Za-z0-9:_-]{4,128}$")


def load_desired(path) -> dict | None:
    try:
        doc = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("services"), dict):
        return None
    return doc


def validate_service(raw) -> dict | str:
    """A service's policy with every field checked, or ``"invalid"``. No raw value ever reaches a message."""
    if not isinstance(raw, dict):
        return "invalid"
    mode, objective, rule = raw.get("desired_mode"), raw.get("objective"), raw.get("career_filter")
    path, age, digest = raw.get("index_path"), raw.get("max_age_s"), raw.get("career_filter_digest")
    if mode not in MODES or objective not in OBJECTIVES or rule not in CAREER_RULES:
        return "invalid"
    if not isinstance(path, str) or not path.startswith("/"):
        return "invalid"
    if isinstance(age, bool) or not isinstance(age, int) or not 60 <= age <= MAX_AGE_LIMIT_S:
        return "invalid"
    if digest is not None and not (isinstance(digest, str) and _DIGEST.match(digest)):
        return "invalid"
    return {"desired_mode": mode, "objective": objective, "career_filter": rule, "index_path": path,
            "max_age_s": age, "career_filter_digest": digest}


def inspect_index(index_path) -> tuple[str, dict, str]:
    """(meta_state, meta, structure). meta_state: ok | missing | corrupt. structure: ok | missing_tables |
    incompatible | empty | unknown. Read-only (``mode=ro``); only ``sqlite_master``, ``meta`` and two counts."""
    path = Path(index_path)
    if not path.is_file():
        return "missing", {}, "unknown"
    try:
        con = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            names = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "meta" not in names:
                return "corrupt", {}, "missing_tables"
            meta = {str(k): (None if v is None else str(v)) for k, v in con.execute("SELECT key, value FROM meta")}
            if not set(BASE_TABLES) <= names:
                return "ok", meta, "missing_tables"
            if meta.get("embedder_digest") and "vec" not in names:
                return "ok", meta, "missing_tables"
            if meta.get("schema_version") != INDEX_SCHEMA_VERSION:
                return "ok", meta, "incompatible"
            records = con.execute("SELECT count(*) FROM records").fetchone()[0]
            chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return "corrupt", {}, "unknown"
    return "ok", meta, ("ok" if records and chunks else "empty")


def _mode(meta: dict) -> tuple[str, bool]:
    keyword, digest = meta.get("keyword_only") == "1", bool(meta.get("embedder_digest"))
    if keyword and not digest:
        return "keyword", True
    if digest and not keyword:
        return "meaning", True
    return "unknown", False


def check_service(policy, meta_state: str, meta: dict, structure: str, now: datetime, canary: str | None = None) -> dict:
    out = {"policy": "ok", "meta": meta_state, "structure": structure, "mode": "unknown", "mode_match": "unknown",
           "privacy": "unknown", "objective": "unknown"}
    if not isinstance(policy, dict):
        out["policy"] = "invalid"
        return out
    if meta_state != "ok":
        return out
    mode, consistent = _mode(meta)
    if not consistent:
        out["meta"] = "inconsistent"
    else:
        out["mode"] = mode                                   # the declared mode
        stamp = status.parse_time(meta.get("updated_at") or meta.get("built_at"))
        if stamp is None or stamp > now:
            out["meta"] = "inconsistent"
        elif (now - stamp).total_seconds() > policy["max_age_s"]:
            out["meta"] = "stale"
    out["privacy"] = _privacy(policy, meta)
    if out["meta"] in ("ok", "stale") and structure == "ok":
        out["mode_match"] = "match" if mode == policy["desired_mode"] else "mismatch"
    if out["mode"] == "keyword":
        out["objective"] = "pending"
    elif out["mode"] == "meaning":
        ready = (out["meta"] == "ok" and structure == "ok" and out["privacy"] in ("stamp_ok", "verified", "not_required"))
        out["objective"] = ("blocked" if not ready else "met" if canary == "pass" else "unverified")
    return out


def _privacy(policy: dict, meta: dict) -> str:
    if policy["career_filter"] == "none":
        return "not_required"
    if meta.get("profile") != MC_PROFILE or meta.get("career_filter_version") != MC_FILTER_VERSION:
        return "violated"
    digest = meta.get("career_filter_digest")
    if not digest:
        return "violated"
    want = policy.get("career_filter_digest")
    if want:
        return "verified" if digest == want else "violated"
    return "stamp_ok"


@dataclass(frozen=True)
class CanarySpec:
    service: str
    record_ref: str
    question_ref: str


def validate_canary(doc: dict | None, service: str):
    """The service's canary spec, None when not configured (the owner has not chosen), or ``"invalid"``."""
    section = (doc or {}).get("canary", {})
    if section is None:
        return None
    if not isinstance(section, dict):
        return "invalid"
    raw = section.get(service)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return "invalid"
    record, question = raw.get("record_ref"), raw.get("question_ref")
    if not (isinstance(record, str) and _REF.match(record) and isinstance(question, str) and _REF.match(question)):
        return "invalid"
    return CanarySpec(service, record, question)


def canary_spec(doc: dict | None, service: str) -> CanarySpec | None:
    spec = validate_canary(doc, service)
    return spec if isinstance(spec, CanarySpec) else None


def run_canary(spec: CanarySpec | None, ask: Callable[[CanarySpec], dict]) -> str:
    """One canary through an injected ``ask``; a code only, never the question, answer or records. A hit served by
    keyword fallback is ``fallback``, never ``pass``."""
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
