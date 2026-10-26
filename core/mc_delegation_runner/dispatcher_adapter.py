"""Runner-side adapter to the dispatcher store: the runner's ``dispatcher`` protocol, plus the
bounded output text returned with each result so the owner sees what the delegate produced."""
from __future__ import annotations

import json
import math
from pathlib import Path

OUTPUT_MAX = 64 * 1024


class StoreDispatcher:
    def __init__(self, store, artifact_root: Path):
        self.store, self.artifact_root = store, Path(artifact_root)

    def heartbeat(self, runner_id, state, capabilities):
        return self.store.heartbeat(runner_id, state, capabilities)

    def claim(self, runner_id, capabilities):
        return self.store.claim(runner_id, capabilities)

    def progress(self, request_id, generation, phase):
        return self.store.progress(request_id, generation, phase)

    def cancel_requested(self, request_id, generation):
        return self.store.cancel_requested(request_id, generation)

    def renew(self, request_id, generation):
        return self.store.renew(request_id, generation)

    def result(self, request_id, generation, *, event_id, status, artifacts, detail):
        text, cost = output_of(self.artifact_root, artifacts)
        if cost is not None:
            detail = {**(detail or {}), "cost_usd": cost}           # what the delegate reports it spent
        return self.store.result(request_id, generation, event_id=event_id, status=status, artifacts=artifacts,
                                 detail=detail, output_text=text)


def output_of(artifact_root: Path, artifacts):
    """(text the owner reads, cost the delegate reports) from the job's output artifact, or (None, None)."""
    text, cost = None, None
    for a in artifacts or []:
        if isinstance(a, dict) and a.get("kind") == "output" and "/" not in str(a.get("ref", "")):
            path = Path(artifact_root) / a["ref"]
            try:
                if path.is_file() and not path.is_symlink():
                    raw = path.read_bytes()
                    text, cost = readable(raw), cost_usd(raw)
            except OSError:
                text = None
    return text, cost


_STOPPED = {"error_max_budget_usd": "Claude Code stopped at its cost cap before finishing.",
            "error_max_turns": "Claude Code stopped at its turn limit before finishing.",
            "error_during_execution": "Claude Code hit an error while working."}


def _envelope(raw: bytes):
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace").strip())
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and doc.get("type") == "result" else None


def cost_usd(raw: bytes):
    """The cost Claude Code's result envelope reports, or None."""
    doc = _envelope(raw)
    value = doc.get("total_cost_usd") if doc else None
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    return round(float(value), 4) if ok else None


def codex_answer(text: str):
    """Codex ``exec --json`` events: the last agent message, or a plain failure line; None when not Codex events."""
    answer, failed, events = None, None, 0
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue                                       # stderr lines interleaved in the log
        if not isinstance(ev, dict) or not isinstance(ev.get("type"), str):
            continue
        events += 1
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        if ev["type"] == "item.completed" and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            answer = item["text"].strip()
        elif ev["type"] == "turn.failed":
            failed = "Codex stopped with an error before finishing."
    if not events or not any(k in text for k in ('"thread.started"', '"turn.started"')):
        return None
    return answer or failed or "Codex finished but returned no answer text."


def readable(raw: bytes) -> str:
    """The answer the owner reads. Claude Code's ``--output-format json`` wraps it in a result envelope; the raw
    artifact keeps the envelope, the owner sees its ``result`` text. Codex's ``--json`` events give its last message.
    Anything else is returned as written."""
    text = raw.decode("utf-8", errors="replace").strip()
    codex = codex_answer(text)
    if codex is not None:
        return codex.encode("utf-8")[:OUTPUT_MAX].decode("utf-8", errors="ignore")
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    if isinstance(doc, dict) and doc.get("type") == "result" and isinstance(doc.get("result"), str):
        text = doc["result"].strip() or "Claude Code finished but returned no answer text."
        if doc.get("is_error"):
            text = "Claude Code reported an error: " + text
    elif isinstance(doc, dict) and doc.get("type") == "result":       # an error envelope: never shown as raw JSON
        text = _STOPPED.get(str(doc.get("subtype")), "Claude Code stopped without an answer.")
    return text.encode("utf-8")[:OUTPUT_MAX].decode("utf-8", errors="ignore")
