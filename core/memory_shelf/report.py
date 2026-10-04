"""The memory capture report: a counts-only, versioned, read-only account of what the shelf captured, what it left out and
how well it did it. Contract: ``docs/memory_capture_report_contract.md`` (the authority for every field and rule here).

* **Read-only.** ``build`` reads the shelf (record front matter and bodies, the ledger, the watcher status files, the approval
  records, the outbox, the jobs status file) and **never captures, repairs or rewrites** anything. Only ``publish`` and
  ``record_quality`` write, and only the report files, the bounded history and the quality-result files, atomically.
* **Counts and fixed codes only.** Every string in the payload is a short safe token (source names, role names, omitted-kind
  names with unsafe characters replaced, reason codes, times, run ids). No title, file name, path, credential or text. A payload
  that breaks this is refused (``assert_safe``) and nothing is published.
* **Units are never mixed.** Raw items omitted (file objects, lines, parts) and normalized turns kept are separate numbers; there is
  no retention percentage. Records are not "unique conversations": nothing is deduplicated across sources.
* **Unknown is never healthy.** Missing, stale or unreadable state is ``unknown``. A successful job and a clean capture can
  still have selection or quality concerns; job freshness (``job``) and capture quality (``status``) are separate.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, coverage, fsio, ledger, migration, record, render, review, sessions
from core.memory_shelf.config import Config

SCHEMA = "minimoi.memory-capture-report.v1"
MATRIX_SCHEMA = "minimoi.memory-capture-matrix.v1"
POLICY_VERSION = "capture-policy.2026-10-04.1"
REPORT_FILE, MATRIX_FILE = "memory-capture-report.json", "memory-capture-matrix.json"
HISTORY_BOUND = 90                        # snapshots kept (about a quarter of daily runs); older ones are dropped
EXPECTED_EVERY_S, MISSED_AFTER_S = 86400, 180000      # the memory-watch job's registry values (config/scheduled_jobs.json)
STALE_REPORT_AFTER_S = 2 * EXPECTED_EVERY_S           # a consumer treats a report older than this as unknown
FIDELITY_STALE_AFTER_S = 14 * 86400
ROLES = ("human", "assistant", "system", "coordination")
MANUAL_PROVIDERS = frozenset({"claude-ai", "grok", "paste"})
LABELS = {"claude-code": "Claude Code", "codex": "Codex", "rooms": "Rooms", "claude-ai": "Claude.ai export",
          "grok": "Grok export", "paste": "Pastes"}
STATES = ("current", "watch", "off", "unknown")
_SAFE = re.compile(r"^[A-Za-z0-9_:.+\- ]{0,100}$")
_TOKEN = re.compile(r"[^A-Za-z0-9_:.+-]")


class ReportError(ValueError):
    """The payload would carry something other than counts and fixed codes. Nothing is published."""


# ── selection: what was left out, by a fixed disposition ────────────────────

DISPOSITIONS = ("duplicate", "machinery", "tool_activity", "reasoning_trace", "injected_context", "attachment_content_excluded",
                "attachment_reference", "handoff_unavailable", "empty", "unknown")
_EXACT = {
    "response_item:message": "duplicate", "handoff:duplicate": "duplicate", "handoff:unavailable": "handoff_unavailable",
    "attachment_content_excluded": "attachment_content_excluded", "attachment_no_text": "attachment_reference",
    "document": "attachment_content_excluded", "image": "attachment_content_excluded", "input_image": "attachment_content_excluded",
    "local_image": "attachment_content_excluded", "file_attachment": "attachment_content_excluded", "file": "attachment_reference",
    "attachments": "attachment_reference", "files": "attachment_reference", "generated_image_urls": "attachment_content_excluded",
    "card_attachments_json": "attachment_content_excluded", "line:attachment": "attachment_reference",
    "item:ImageView": "attachment_content_excluded", "thinking": "reasoning_trace", "thinking_trace": "reasoning_trace",
    "agent_thinking_traces": "reasoning_trace", "item:Reasoning": "reasoning_trace", "response_item:reasoning": "reasoning_trace",
    "sidechain": "machinery", "item:ContextCompaction": "machinery", "response_item:compaction": "machinery",
    "injected_prompt_block": "injected_context", "web_search_results": "tool_activity", "cited_web_search_results": "tool_activity",
    "query": "tool_activity", "steps": "tool_activity", "tool_result": "tool_activity", "response_item:agent_message": "unknown",
}
_PREFIX = (("tool_use:", "tool_activity"), ("tool_result:", "tool_activity"), ("event_msg:", "machinery"), ("line:", "machinery"),
           ("injected:", "injected_context"), ("empty:", "empty"), ("content:", "unknown"),
           ("response_item:custom_tool_call", "tool_activity"), ("response_item:function_call", "tool_activity"),
           ("response_item:tool_search", "tool_activity"))
_CODEX_TOOL_ITEMS = frozenset({"CommandExecution", "FileChange", "McpToolCall", "FunctionCallOutput", "WebSearch", "Extension",
                               "SubAgentActivity", "CollabAgentToolCall"})


def disposition(kind: str) -> str:
    """The fixed disposition of an omitted raw-item kind. An unrecognised kind is ``unknown`` (a Watch), never guessed benign.
    ``response_item:agent_message`` is unknown here on purpose: since the capture revision a handoff is kept, so one still being
    omitted means an older reading."""
    if kind in _EXACT:
        return _EXACT[kind]
    if kind.startswith("item:") and kind[5:] in _CODEX_TOOL_ITEMS:
        return "tool_activity"
    if kind in ("injected:image", "injected:/image"):
        return "attachment_content_excluded"
    for prefix, name in _PREFIX:
        if kind.startswith(prefix):
            return name
    return "unknown"


def token(value) -> str:
    """A fixed safe token from any source-provided name."""
    return (_TOKEN.sub("_", str(value))[:60]) or "_"


# ── reading the shelf ────────────────────────────────────────────────────────

def _now_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _role_counts(meta: dict, main: Path) -> tuple[Counter, bool]:
    """Turns by role for one record: from the manifest when it has them, else counted from the body (older records)."""
    by_role = (meta.get("normalized") or {}).get("turns_by_role")
    if isinstance(by_role, dict):
        return Counter({k: v for k, v in by_role.items() if isinstance(v, int)}), True
    try:
        _, body = record.load(record.read(main))
        return Counter(t.speaker for t in render.parse_body(body)), False
    except (OSError, record.InvalidRecord):
        return Counter(), False


def _blank_source() -> dict:
    return {"records": 0, "turns": Counter(), "redacted_turns": 0, "by_class": Counter(), "omitted": Counter(),
            "attachments": 0, "attachment_chars_excluded": 0, "handoffs_retained": 0, "handoffs_unavailable": 0,
            "handoffs_duplicate": 0, "copies_matched": 0, "copies_added": 0, "dup_records": 0, "behind": 0, "unknown_origin": 0,
            "flags": Counter(), "role_unreadable": 0}


def _scan_records(shelf) -> dict[str, dict]:
    per: dict[str, dict] = defaultdict(_blank_source)
    for entry in shelf.list_records():
        provider = token(entry.get("provider") or "unknown")
        main = shelf.main_path(entry)
        try:
            meta = record_front(main)
        except (OSError, record.InvalidRecord, ValueError):
            per[provider]["role_unreadable"] += 1
            continue
        norm = meta.get("normalized") or {}
        row = per[provider]
        row["records"] += 1
        roles, _ = _role_counts(meta, main)
        row["turns"].update(roles)
        row["redacted_turns"] += int(norm.get("redacted_turns") or 0)
        for kind, n in (norm.get("omitted") or {}).items():
            if isinstance(n, int):
                row["omitted"][token(kind)] += n
        row["attachments"] += int(norm.get("attachments") or 0)
        row["attachment_chars_excluded"] += int(norm.get("attachment_chars_excluded") or 0)
        for key in ("handoffs_retained", "handoffs_unavailable", "handoffs_duplicate", "copies_matched", "copies_added", "unknown_origin"):
            row[key] += int(norm.get(key) or 0)
        have = norm.get("normalizer", sessions.DEFAULT_NORMALIZER)
        row["behind"] += have < migration.current_version(provider)
        if provider == "codex" and have >= migration.current_version(provider):
            row["dup_records"] += 1
        for flag in (norm.get("coverage") or {}).get("flags") or []:
            row["flags"][token(flag)] += 1
        cls = norm.get("session_class")
        if cls and cls != "dialogue":
            row["by_class"][token(cls)] += int(sum(roles.values()))
    return per


def record_front(main: Path) -> dict:
    from core.memory_shelf.shelf import read_front_matter
    return read_front_matter(main)


def _watch_status(shelf, name: str) -> dict | None:
    doc = fsio.read_json(Path(shelf.status_dir) / f"watch-{name}.json")
    return doc if isinstance(doc, dict) else None


def _last_success(shelf, name: str, status: dict | None, provider: str) -> tuple[datetime | None, str, datetime | None]:
    """(last successful capture/check, ``capture`` | ``check`` | ``none``, last time something new was captured)."""
    if status:
        ok = status.get("status") == "ok" and not any((status.get("counts") or {}).get(k) for k in ("failed", "unstable", "disk_low"))
        success = _parse(status.get("last_success_at")) or (_parse(status.get("last_run_at")) if ok else None)
        new = _parse(status.get("last_new_capture_at"))
        kind = "none" if success is None else ("capture" if (new and success and new >= success) else "check")
        return success, kind, new
    # a manual source (an export) has no watcher status: its own ledger rows say when it last captured
    last = None
    for (src, _key), row in ledger.latest(shelf).items():
        if src == name and row.get("outcome") in codes.OK_OUTCOMES:
            moment = _parse(row.get("at"))
            last = moment if (last is None or (moment and moment > last)) else last
    return last, ("capture" if last else "none"), last


def _ledger_by_source(shelf) -> dict[str, dict]:
    rep = ledger.report(shelf)
    return rep.get("sources", {})


def _open_quality_items(shelf) -> Counter:
    out: Counter = Counter()
    for item in review.items(shelf):
        if item.get("type") == "coverage-flag":
            out[token((item.get("detail") or {}).get("source") or "unknown")] += 1
    return out


def _read_quality(shelf, name: str) -> dict | None:
    doc = fsio.read_json(Path(shelf.status_dir) / "quality" / f"{name}.json")
    return doc if isinstance(doc, dict) else None


def record_quality(shelf, name: str, doc: dict) -> None:
    """Write one quality-result file (fidelity sample, semantic audit). Same safe-token rule as the report."""
    assert_safe(doc)
    fsio.write_json(Path(shelf.status_dir) / "quality" / f"{name}.json", doc)


def _fidelity_block(shelf, now: datetime) -> dict:
    doc = _read_quality(shelf, "fidelity")
    if not doc:
        return {"state": "not_run"}
    at = _parse(doc.get("at"))
    state = "unknown" if at is None else ("stale" if (now - at).total_seconds() > FIDELITY_STALE_AFTER_S else
                                         ("failed" if doc.get("failed") else "ok"))
    keep = {k: doc[k] for k in ("at", "method", "sample_size", "checked", "ok", "failed", "skipped", "seed", "scope") if k in doc}
    return {"state": state, **keep}


def _semantic_block(shelf) -> dict:
    doc = _read_quality(shelf, "semantic-audit")
    if not doc:
        return {"state": "not_run"}
    keep = {k: doc[k] for k in ("at", "reviewer", "method", "sample_size", "scope", "findings", "skipped") if k in doc}
    return {"state": "recorded", **keep}


# ── building the report ─────────────────────────────────────────────────────

def build(shelf, cfg: Config, now: datetime | None = None, *, jobs_root=None, run_id: str | None = None) -> dict:
    """The full report. Read-only. Never raises for missing or malformed state: those become ``unknown``."""
    now = now or datetime.now(timezone.utc)
    per = _scan_records(shelf)
    led = _ledger_by_source(shelf)
    quality_items = _open_quality_items(shelf)
    pending = len(shelf.pending())
    configured = dict(cfg.sources)
    names = sorted(set(per) | set(configured))
    fidelity = _fidelity_block(shelf, now)
    semantic = _semantic_block(shelf)
    job = _job_block(jobs_root, now)
    sources: dict[str, dict] = {}
    for name in names:
        row = per.get(name) or _blank_source()
        scfg = configured.get(name)
        manual = name in MANUAL_PROVIDERS
        led_row = led.get("inbox" if manual else name, {})
        status_doc = None if manual else _watch_status(shelf, name)
        success, kind, new_at = _last_success(shelf, "inbox" if manual else name, status_doc, name)
        approval = None
        if scfg is not None:
            approval = approvals.source_status(shelf, name, scfg.fingerprint())
        by_disposition: Counter = Counter()
        by_kind = Counter(row["omitted"])
        for k, n in row["omitted"].items():
            by_disposition[disposition(k)] += n
        unknown_kinds = sorted(k for k in row["omitted"] if disposition(k) == "unknown")
        findings = _classification_findings(name, row, quality_items)
        dup = _duplicate_block(name, row)
        capture = {"records": row["records"],
                   "turns": {**{r: row["turns"].get(r, 0) for r in ROLES}, "total": int(sum(row["turns"].values()))},
                   "redacted_turns": row["redacted_turns"],
                   "by_class": dict(sorted(row["by_class"].items())),
                   "failures": dict(sorted((led_row.get("missing") or {}).items())),
                   "pending_outbox": pending,
                   "held": dict(sorted((led_row.get("held") or {}).items())),
                   "excluded": dict(sorted((led_row.get("excluded") or {}).items())),
                   "refused": dict(sorted((led_row.get("refused") or {}).items())),
                   "changed_or_unverifiable_sources": _changed_sources(shelf, cfg, name, scfg),
                   "unreadable_records": row["role_unreadable"]}
        selection = {"raw_items_omitted": {"total": int(sum(row["omitted"].values())),
                                           "by_disposition": {d: by_disposition.get(d, 0) for d in DISPOSITIONS},
                                           "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))[:40]),
                                           "kinds_total": len(by_kind), "unknown_kinds": unknown_kinds[:20]},
                     "normalized_turns_kept": capture["turns"]["total"],
                     "units_note": "raw_items_and_turns_are_different_units"}
        quality = {"classification": {"findings": findings, "records_on_older_parser": row["behind"],
                                      "current_parser_version": migration.current_version(name)},
                   "duplicates": dup,
                   "fidelity": fidelity,
                   "semantic_audit": semantic}
        evidence = {"attachment_references": by_disposition.get("attachment_reference", 0),
                    "attachment_content_excluded_items": by_disposition.get("attachment_content_excluded", 0),
                    "attachment_chars_excluded": row["attachment_chars_excluded"], "attachments_seen": row["attachments"],
                    "handoffs_retained": row["handoffs_retained"], "handoffs_unavailable": row["handoffs_unavailable"],
                    "handoffs_duplicate": row["handoffs_duplicate"]}
        status = _source_status(now, name=name, manual=manual, configured=scfg is not None, approval=approval, status_doc=status_doc,
                                success=success, kind=kind, new_at=new_at, row=row, findings=findings, quality_items=quality_items,
                                fidelity=fidelity, unknown_kinds=unknown_kinds, led_row=led_row, has_records=row["records"] > 0)
        sources[name] = {"label": LABELS.get(name, name), "mode": "manual" if manual else "scheduled", "capture": capture,
                         "selection": selection, "quality": quality, "evidence": evidence, "status": status}
    run = run_id or f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    totals = {"records": sum(s["capture"]["records"] for s in sources.values()),
              "turns": {**{r: sum(s["capture"]["turns"][r] for s in sources.values()) for r in ROLES},
                        "total": sum(s["capture"]["turns"]["total"] for s in sources.values())},
              "note": "records_not_deduplicated_across_sources"}
    report = {"schema": SCHEMA, "run_id": run, "generated_at": _now_iso(now),
              "source_snapshot": _snapshot(shelf, now), "versions": {"policy": POLICY_VERSION, "report_schema": 1,
                                                                        "parsers": {n: migration.current_version(n) for n in sorted(names)}},
              "cadence": {"expected_every_s": EXPECTED_EVERY_S, "missed_after_s": MISSED_AFTER_S, "stale_report_after_s": STALE_REPORT_AFTER_S},
              "job": job, "sources": sources, "totals": totals}
    report["history"] = _history_summary(shelf, report)
    report["operate"] = matrix(report)
    return report


def _snapshot(shelf, now: datetime) -> dict:
    rows = fsio.read_jsonl(Path(shelf.ledger_dir) / "ledger.jsonl")
    last = max((r.get("at") for r in rows if isinstance(r.get("at"), str)), default=None)
    return {"as_of": last or _now_iso(now), "ledger_rows": len(rows), "shelf_records": len(shelf.list_records()),
            "basis": "read_only_at_generated_at"}


def _job_block(jobs_root, now: datetime) -> dict:
    """Freshness of the daily memory job: its own judgement, separate from capture quality."""
    if not jobs_root:
        return {"state": "unknown", "reason": "no_jobs_root"}
    from core.jobs import status as jobs_status
    doc = jobs_status.read_status(Path(jobs_root), "memory-watch.json", "memory-watch")
    if not doc:
        return {"state": "unknown", "reason": "no_status_file"}
    finished = _parse(doc.get("finished_at"))
    age = int((now - finished).total_seconds()) if finished else None
    state = doc.get("state") if doc.get("state") in ("ok", "warn", "failed", "running") else "unknown"
    if finished and age is not None and age > MISSED_AFTER_S:
        return {"state": "unknown", "reason": "stale", "finished_at": doc.get("finished_at"), "age_s": age}
    return {"state": state, "finished_at": doc.get("finished_at"), "age_s": age,
            "results": {k: token(v) for k, v in (doc.get("results") or {}).items()}}


def _changed_sources(shelf, cfg: Config, name: str, scfg) -> dict:
    """How many kept sources changed since capture, or are gone: ``stat`` only (size and mtime), never opened."""
    if scfg is None or scfg.kind not in ("claude-code", "codex"):
        return {"changed": 0, "missing": 0, "checked": 0, "basis": "not_applicable"}
    state = fsio.read_json(Path(shelf.status_dir) / "watch-state" / f"{name}.json")
    files = (state or {}).get("files") if isinstance(state, dict) else None
    if not isinstance(files, dict):
        return {"changed": 0, "missing": 0, "checked": 0, "basis": "no_state"}
    changed = missing = checked = 0
    for entry in files.values():
        if not isinstance(entry, dict) or entry.get("outcome") not in codes.OK_OUTCOMES or "rel" not in entry:
            continue
        checked += 1
        try:
            st = (Path(scfg.root) / entry["rel"]).stat()
        except OSError:
            missing += 1
            continue
        changed += not (st.st_size == entry.get("size") and st.st_mtime_ns == entry.get("mtime_ns"))
    return {"changed": changed, "missing": missing, "checked": checked, "basis": "size_and_mtime_since_capture"}


def _classification_findings(name: str, row: dict, quality_items: Counter) -> list[dict]:
    """Known unresolved findings (each a Watch until it is cleared). Counts only; today's audit numbers are not hard-coded:
    these come from what the shelf holds now."""
    out = []
    if row["behind"]:
        out.append({"code": "records_on_older_parser", "records": row["behind"]})
    if row["unknown_origin"]:
        out.append({"code": "unknown_origin", "turns": row["unknown_origin"]})
    if row["flags"].get("unknown_kind"):
        out.append({"code": "unknown_kind", "records": row["flags"]["unknown_kind"]})
    if row["flags"].get("possible_gap") or quality_items.get(name):
        out.append({"code": "possible_gap", "records": max(row["flags"].get("possible_gap", 0), quality_items.get(name, 0))})
    unknown = sum(n for k, n in row["omitted"].items() if disposition(k) == "unknown")
    if unknown:
        out.append({"code": "unclassified_omitted_kind", "items": unknown})
    return out


def _duplicate_block(name: str, row: dict) -> dict:
    if name != "codex":
        return {"state": "not_applicable"}
    omitted = row["omitted"].get("response_item:message", 0)
    counted = row["dup_records"]
    return {"state": "counted" if counted else "unavailable",
            "basis": "parser_matching_recorded_per_record",
            "records_counted": counted, "records_unverifiable": row["records"] - counted,
            "copies_matched": row["copies_matched"], "copies_kept_as_turns": row["copies_added"],
            "omitted_copy_items_all_records": omitted,
            "whitespace_rule": "collapse_whitespace_runs_item_bytes_kept"}


def _source_status(now, *, name, manual, configured, approval, status_doc, success, kind, new_at, row, findings, quality_items,
                   fidelity, unknown_kinds, led_row, has_records) -> dict:
    """``current | watch | off | unknown`` plus one reason code (contract: the order below is the order of judgement)."""
    base = {"last_success_at": _now_iso(success) if success else None, "last_check_kind": kind,
            "last_new_capture_at": _now_iso(new_at) if new_at else None}

    def done(state, reason):
        return {"state": state, "reason": reason, **base}
    if not manual:
        if not configured:
            return done("off", "not_configured")
        if approval != approvals.APPROVED and approval != approvals.STALE:
            return done("off", "not_approved")                       # the owner has not switched it on: intentional
        if approval == approvals.STALE:
            return done("watch", "approval_stale")                   # it was on and its definition changed: needs the owner
        if status_doc is None:
            return done("unknown", "no_state")
        counts = status_doc.get("counts") or {}
        if status_doc.get("status") != "ok" or any(counts.get(k) for k in ("failed", "unstable", "disk_low")):
            return done("watch", "capture_failed")
        if success is None:
            return done("unknown", "no_state")
        if (now - success).total_seconds() > MISSED_AFTER_S:
            return done("watch", "overdue")
    else:
        if not has_records and not led_row:
            return done("off", "no_captures_yet")
        if (led_row.get("missing") or {}) and sum((led_row.get("missing") or {}).values()):
            return done("watch", "capture_failed")
    if unknown_kinds:
        return done("watch", "unclassified_omitted_kind")
    if findings:
        return done("watch", findings[0]["code"])
    if fidelity.get("state") in ("not_run", "stale", "unknown") and not manual:
        return done("unknown", "quality_unknown")                    # missing or stale quality data is never healthy
    if fidelity.get("state") == "failed":
        return done("watch", "fidelity_failed")
    return done("current", "ok")


# ── the matrix (the small Operate contract) ─────────────────────────────────

_WORST = {"watch": 3, "unknown": 2, "current": 1, "off": 0}


def matrix(report: dict) -> dict:
    """The compact per-source matrix Operate shows. ``captured_records`` stays visible when a source is stale, labelled by its
    status; a total alone never means capture is active."""
    rows = []
    for name, src in report["sources"].items():
        st = src["status"]
        rows.append({"source": name, "label": src["label"], "mode": src["mode"],
                     "last_success_at": st["last_success_at"], "last_check_kind": st["last_check_kind"],
                     "last_new_capture_at": st["last_new_capture_at"], "captured_records": src["capture"]["records"],
                     "status": st["state"], "reason": st["reason"]})
    worst = max((r["status"] for r in rows), key=lambda s: _WORST[s], default="unknown")
    return {"schema": MATRIX_SCHEMA, "run_id": report["run_id"], "generated_at": report["generated_at"],
            "stale_after_s": STALE_REPORT_AFTER_S, "job": {"state": report["job"].get("state"), "reason": report["job"].get("reason")},
            "overall": worst if rows else "unknown", "rows": rows}


# ── history: bounded, and only when something changed ───────────────────────

def _history_path(shelf) -> Path:
    return Path(shelf.status_dir) / "report" / "history.jsonl"


def _snapshot_row(report: dict) -> dict:
    per = {n: {"records": s["capture"]["records"], "turns": s["capture"]["turns"]["total"],
               "omitted": s["selection"]["raw_items_omitted"]["total"], "findings": len(s["quality"]["classification"]["findings"]),
               "parser": report["versions"]["parsers"].get(n), "state": s["status"]["state"]} for n, s in report["sources"].items()}
    fingerprint = hashlib.sha256(json.dumps(per, sort_keys=True).encode()).hexdigest()[:16]
    return {"run_id": report["run_id"], "at": report["generated_at"], "policy": report["versions"]["policy"], "fingerprint": fingerprint,
            "sources": per}


def _history_summary(shelf, report: dict) -> dict:
    rows = fsio.read_jsonl(_history_path(shelf))
    prev = rows[-1] if rows else None
    now_row = _snapshot_row(report)
    delta = {}
    if prev:
        for n, cur in now_row["sources"].items():
            old = (prev.get("sources") or {}).get(n)
            if old:
                delta[n] = {k: cur[k] - old[k] for k in ("records", "turns", "omitted") if isinstance(cur.get(k), int) and isinstance(old.get(k), int)}
    return {"retained_snapshots": len(rows), "bound": HISTORY_BOUND, "previous_run_id": prev.get("run_id") if prev else None,
            "previous_at": prev.get("at") if prev else None, "delta_since_previous": delta,
            "basis": "per_source_same_units_snapshot_on_change_or_daily",
            "unchanged_since_previous": bool(prev and prev.get("fingerprint") == now_row["fingerprint"])}


def _update_history(shelf, report: dict) -> bool:
    rows = fsio.read_jsonl(_history_path(shelf))
    row = _snapshot_row(report)
    if rows and rows[-1].get("fingerprint") == row["fingerprint"] and str(rows[-1].get("at", ""))[:10] == row["at"][:10]:
        return False
    rows.append(row)
    rows = rows[-HISTORY_BOUND:]
    fsio.write_atomic(_history_path(shelf), ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode("utf-8"))
    return True


# ── safety and publication ───────────────────────────────────────────────────

def assert_safe(payload) -> None:
    """Every string in the payload is a short safe token; every key is too. Raises ``ReportError`` otherwise."""
    def walk(value, depth=0):
        if depth > 12:
            raise ReportError("too deep")
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str) or not _SAFE.match(k):
                    raise ReportError("an unsafe key")
                walk(v, depth + 1)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, depth + 1)
        elif isinstance(value, str):
            if not _SAFE.match(value):
                raise ReportError("an unsafe string")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise ReportError("an unsupported value")
    walk(payload)


def publish(shelf, report: dict, jobs_root) -> dict:
    """Write the report and the matrix atomically under the jobs root (read-only mount for the Guild), and update the bounded
    history. Nothing is written if the payload is not counts-only."""
    assert_safe(report)
    root = Path(jobs_root)
    fsio.ensure_dir(root)
    fsio.write_json(root / REPORT_FILE, report)
    fsio.write_json(root / MATRIX_FILE, report["operate"])
    return {"published": [REPORT_FILE, MATRIX_FILE], "history_appended": _update_history(shelf, report)}


def summary_line(report: dict) -> str:
    states = Counter(s["status"]["state"] for s in report["sources"].values())
    return (f"report\t{report['run_id']}\toverall={report['operate']['overall']}\trecords={report['totals']['records']}"
            f"\tturns={report['totals']['turns']['total']}\tsources={json.dumps(dict(sorted(states.items())), sort_keys=True)}")


__all__ = ["build", "publish", "matrix", "disposition", "assert_safe", "record_quality", "summary_line", "SCHEMA", "MATRIX_SCHEMA",
           "ReportError", "DISPOSITIONS", "REPORT_FILE", "MATRIX_FILE"]
