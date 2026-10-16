"""Bounded Board outcome proposals through the already authenticated MC turn.

The owner turn supplies authority; model output supplies DATA only. This module
never initiates inference, accesses another floor, or marks the source complete.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from ..mc_context import board_ids
from ..payment_scrub import scrub
from ..stores import POSTIT_MAX

# Explicit direct-owner requests only. Model output and referenced Board DATA
# never enter prepare. These small grammars intentionally do not interpret prose.
_OUTCOME = r"(?:the\s+)?(?:outcome|results?)(?:\s+and\s+any\s+unavailable\s+services)?"
_WRITE = r"(?:leave|record|post|save)\s+" + _OUTCOME + r"\s+(?:on|to)\s+(?:the\s+)?board(?=[.!?](?:\s|$)|$)"
_REQUEST = re.compile(r"(?:^|[.!?]\s+)(?:please\s+)?" + _WRITE, re.I)
# A direct task can coordinate its explicit Board outcome in the same sentence.
_CHAIN_REQUEST = re.compile(
    r"(?:^|[.!?]\s+|:\s+)(?:please\s+)?(?:pick\s+up|check|review|work\s+on)\s+"
    r"(?:the\s+)?board\s+(?:(?:note|item|post[- ]?it)\s+)?#?\d{1,5}\b"
    r"[^.!?\n]{0,400}?\band\s+(?:please\s+)?" + _WRITE, re.I)
_DENIAL = re.compile(r"\b(?:do\s+not|don['’]t|never|not\s+to)\s+(?:leave|record|post|save)\b", re.I)
_REPORTED = re.compile(
    r"(?:^|\n)\s*(?:copied|quoted|pasted|forwarded|document|transcript|model\s+(?:output|response)|assistant\s+(?:output|response))\s*(?:text|instructions|excerpt)?\s*:"
    r"|\b(?:he|she|they|someone|MC|CoS|Codex|Claude|(?:the\s+)?(?:agent|model|assistant|document|transcript))\s+(?:said|says|wrote|writes|asked|asks|suggested|suggests|recommended|recommends)\b", re.I)

@dataclass(frozen=True)
class Plan:
    sources: tuple[tuple[int, int], ...]
    turn_key: str

    def contract(self):
        bindings = ", ".join(f"#{n} version {v}" for n, v in self.sources)
        return ("[The owner explicitly asked to record a Board outcome. You may propose ONE outcome for "
                + bindings + ". After actually checking the task, make your ENTIRE response exactly one JSON object, with no prose, Markdown fences or save announcement: "
                '{"action":"report_board_outcome","source_id":14,"version":1,"answer":"your full answer",'
                '"outcome":"brief factual outcome"}. Use the actual bound ID and version, not the example. '
                "The platform validates and saves the proposal after keeping your answer. Do not say it is already saved, "
                "and do not claim task success without evidence. If more information is needed, answer normally; no Board write occurs.]")


def prepare(owner_text, floor, turn_key):
    if not isinstance(owner_text, str) or any(c in owner_text for c in ('"', '“', '”', '`', '>')):
        return None
    if _DENIAL.search(owner_text) or _REPORTED.search(owner_text):
        return None
    if not (_REQUEST.search(owner_text) or _CHAIN_REQUEST.search(owner_text)):
        return None
    ids = board_ids(owner_text)
    if not ids:
        return None
    result = floor.referenced_postits(ids)
    if not result.ok:
        return None
    rows = result.data.get("postits", [])
    sources = tuple((r["id"], r["version"]) for r in rows if r["id"] in ids and r["state"] != "binned")
    return Plan(sources, turn_key) if sources else None


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate field")
        out[key] = value
    return out


def parse(plan, text):
    """None for an ordinary answer; ValueError for a malformed structured proposal."""
    if plan is None or not isinstance(text, str):
        return None
    # Formatting tolerance is not authority: only the complete response may be
    # one JSON fence, and all schema/binding/commit gates below still apply.
    if len(text.encode("utf-8")) > 16000:
        raise ValueError("proposal too large")
    fenced = re.fullmatch(r"\s*```json[ \t]*\r?\n(\{[\s\S]*\})\r?\n```\s*", text)
    if fenced is not None:
        text = fenced.group(1)
    if not text.lstrip().startswith("{"):
        # A proposal wrapped in prose or Markdown is not an ordinary answer.
        # Refuse it rather than leaking the command or extracting a writable
        # object from arbitrary generated text.
        if re.search(r'"action"\s*:\s*"report_board_outcome"', text):
            raise ValueError("proposal must be the whole response")
        return None
    if len(text.encode("utf-8")) > 16000:
        raise ValueError("proposal too large")
    obj = json.loads(text, object_pairs_hook=_unique)
    if not isinstance(obj, dict) or set(obj) != {"action", "source_id", "version", "answer", "outcome"}:
        raise ValueError("proposal schema")
    if (obj["action"] != "report_board_outcome" or type(obj["source_id"]) is not int
            or type(obj["version"]) is not int or (obj["source_id"], obj["version"]) not in plan.sources):
        raise ValueError("proposal binding")
    if not isinstance(obj["answer"], str) or not obj["answer"].strip() or len(obj["answer"]) > 8000:
        raise ValueError("answer invalid")
    if not isinstance(obj["outcome"], str) or not obj["outcome"].strip():
        raise ValueError("outcome invalid")
    answer, outcome = scrub(obj["answer"].strip()), scrub(obj["outcome"].strip())
    outcome = f"Re Board note #{obj['source_id']}: {outcome}"
    if len(outcome) > POSTIT_MAX or not answer.strip():
        raise ValueError("outcome too long")
    return {"source_id":obj["source_id"], "version":obj["version"], "answer":answer, "text":outcome}


def commit(plan, proposal, floor, *, clean, answer_kept):
    if plan is None or proposal is None or not clean or not answer_kept:
        return {"state":"not_saved"}
    if (proposal.get("source_id"), proposal.get("version")) not in plan.sources:
        return {"state":"refused"}
    result = floor.add_outcome(proposal["source_id"], proposal["version"], proposal["text"], turn_key=plan.turn_key)
    if result.outcome == "added" and result.value:
        return {"state":"saved", "source_id":proposal["source_id"], "postit_id":result.value["id"], "repeated":result.repeated}
    return {"state":"not_saved", "reason":result.outcome}
