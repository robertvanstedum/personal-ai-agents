"""The citation rule (v0.5 D6): a synthesis sentence with no file-and-line citation is invalid and marked unsupported.

A direct ``ask`` returns the two lists and nothing else. An agent (CoS or MC) may then read the cited files and write a
synthesis; this module is how that synthesis is judged against what the query actually returned, so Robert can tell a
retrieval failure from a reasoning failure. A sentence is:

* ``supported``: it cites ``file:line`` or ``file:start-end`` and the cited lines lie inside a passage this very query
  returned (either list or the adjacent slot) for that file;
* ``invalid_citation``: it cites something, but not a passage this query returned (a made-up or wrong citation);
* ``unsupported``: it makes a claim and cites nothing.

Sentences of fewer than four words (headings, connectors) are not claims and are ``exempt``.
"""
from __future__ import annotations

import re

SUPPORTED, UNSUPPORTED, INVALID, EXEMPT = "supported", "unsupported", "invalid_citation", "exempt"
_CITE = re.compile(r"([\w./@+-]+\.md):(\d+)(?:\s*[-–]\s*(\d+))?")
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
MARK = " [unsupported: no file:line citation from this query]"
MARK_INVALID = " [unsupported: citation is not a passage this query returned]"


def _passages(result: dict) -> list[tuple[str, int, int]]:
    hits = list(result.get("keyword", [])) + list(result.get("meaning", []))
    slot = result.get("adjacent") or {}
    if slot.get("file"):
        hits.append(slot)
    return [(h["file"], h["lines"][0], h["lines"][1]) for h in hits]


def _inside(cite: tuple[str, int, int], passage: tuple[str, int, int]) -> bool:
    """A real line range lying wholly inside a returned passage of the same file: ``1 <= lo <= a <= b <= hi``.
    A reversed range (19-12), a zero or out-of-range line is not a citation of anything the query returned."""
    (name, a, b), (file, lo, hi) = cite, passage
    return name == file and 1 <= lo <= a <= b <= hi


def _cited(sentence: str) -> list[tuple[str, int, int]]:
    out = []
    for name, a, b in _CITE.findall(sentence):
        a = int(a)
        out.append((name, a, int(b) if b else a))
    return out


def check(text: str, result: dict) -> list[dict]:
    """One entry per sentence: ``{"index", "state"}`` (never the sentence itself)."""
    passages = _passages(result)
    out = []
    for i, sentence in enumerate(s for s in _SPLIT.split(text or "") if s.strip()):
        cites = _cited(sentence)
        if len(re.findall(r"\w+", _CITE.sub("", sentence))) < 4 and not cites:
            state = EXEMPT
        elif not cites:
            state = UNSUPPORTED
        elif all(any(_inside(c, p) for p in passages) for c in cites):
            state = SUPPORTED
        else:
            state = INVALID
        out.append({"index": i, "state": state})
    return out


def mark_unsupported(text: str, result: dict) -> str:
    """The synthesis with each unsupported or invalidly cited sentence visibly marked (the sentence is kept)."""
    sentences = [s for s in _SPLIT.split(text or "") if s.strip()]
    verdict = {v["index"]: v["state"] for v in check(text, result)}
    return "\n".join(s + (MARK if verdict[i] == UNSUPPORTED else MARK_INVALID if verdict[i] == INVALID else "")
                     for i, s in enumerate(sentences))


__all__ = ["check", "mark_unsupported", "SUPPORTED", "UNSUPPORTED", "INVALID", "EXEMPT"]
