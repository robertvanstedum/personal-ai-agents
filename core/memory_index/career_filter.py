"""The career filter: what never enters Master Craftsman's index (#594; Robert's decision 4, 13 Nov 2026).

Private career material stays off MC's reach, and the filter works at **index time**: an index built with a filter
(``indexer.update/rebuild(..., career=CareerFilter)``) never receives a career record or a career turn, so no query,
caller or tier can bring one back. Search results are read from the shelf at the lines of an index row, after the
resolver re-chunks the record and checks the row is one of its chunks; a turn whose chunks were never written has no
row to point at.

Rules (``minimoi.career-filter.v1``, JSON):

    {"schema": "minimoi.career-filter.v1",
     "terms": ["recruiters?", "cover letters?", ...],    regex fragments, matched case-insensitively as whole words
     "record_threshold": 3,                              hold the whole record at this many matching turns...
     "record_fraction": 0.1,                             ...when they are also at least this share of its turns
     "turn_window": 1,                                   also drop this many retrievable turns before and after a match
     "exclude_providers": [],                            hold every record of these providers (e.g. "minimoi-cos")
     "exclude_records": []}                              hold these record ids (the owner's list)

The public repository ships only generic career words (``config/vault/career_filter.default.json``). Each host keeps a
PRIVATE rules file beside it (root-only, never in git) with the names that make career material recognisable: employers,
recruiters, people, target roles. The two are merged: terms and exclusion lists are united, the threshold and fraction
are the lower (stricter) of the two and the window the wider. ``require_private`` (production) refuses to build without it.

A record is held (``career_excluded``) when its provider or id is excluded, its title matches, or ``record_threshold`` or
more of its retrievable turns match AND they are at least ``record_fraction`` of them (a conversation that is mostly about
career). A long engineering session that mentions a job search a few times keeps its other turns. Otherwise each matching turn, with ``turn_window`` neighbours on each side, loses all
its chunks; a record left with no chunk is held too. Over-excluding is the accepted cost.

``digest`` names the merged rules (sha256 of their canonical JSON). The index stores it; a different digest means a full
rebuild, never an incremental update (``indexer.check_compatible``). Reports carry counts only, never text or terms.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "minimoi.career-filter.v1"
FILTER_VERSION = 1                     # the matching procedure below; bump when it changes (forces a rebuild)
PROFILE = "mc"                         # meta.profile of a filtered index
CAREER_EXCLUDED = "career_excluded"    # held-record reason code
DEFAULT_RULES = Path(__file__).resolve().parents[2] / "config" / "vault" / "career_filter.default.json"
MAX_TERMS, MAX_TERM_LEN, MAX_LIST = 2000, 200, 10000
_KEYS = {"schema", "terms", "record_threshold", "record_fraction", "turn_window", "exclude_providers", "exclude_records"}
_RID = re.compile(r"^[0-9A-Z]{26}$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class FilterError(ValueError):
    """The rules cannot be trusted. ``code``: ``filter_missing`` | ``filter_insecure`` | ``filter_invalid``."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _read(path: Path, *, private: bool) -> dict:
    try:
        info = path.stat()
    except FileNotFoundError:
        raise FilterError("filter_missing") from None
    except OSError:
        raise FilterError("filter_invalid") from None
    if private and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise FilterError("filter_insecure")              # the private names are readable by others: refuse to use them
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FilterError("filter_invalid") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or set(doc) - _KEYS:
        raise FilterError("filter_invalid")
    terms = doc.get("terms", [])
    if not isinstance(terms, list) or len(terms) > MAX_TERMS:
        raise FilterError("filter_invalid")
    for term in terms:
        if not isinstance(term, str) or not 1 <= len(term) <= MAX_TERM_LEN:
            raise FilterError("filter_invalid")
        try:
            re.compile(term)
        except re.error:
            raise FilterError("filter_invalid") from None
    for key, pattern in (("exclude_providers", _PROVIDER), ("exclude_records", _RID)):
        values = doc.get(key, [])
        if not isinstance(values, list) or len(values) > MAX_LIST or any(
                not isinstance(v, str) or not pattern.match(v) for v in values):
            raise FilterError("filter_invalid")
    for key, low, high in (("record_threshold", 1, 1000), ("turn_window", 0, 10)):
        value = doc.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high):
            raise FilterError("filter_invalid")
    fraction = doc.get("record_fraction")
    if fraction is not None and (isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1):
        raise FilterError("filter_invalid")
    return doc


@dataclass(frozen=True)
class CareerFilter:
    terms: tuple[str, ...]
    record_threshold: int
    record_fraction: float
    turn_window: int
    exclude_providers: frozenset[str]
    exclude_records: frozenset[str]
    has_private: bool

    @property
    def canonical(self) -> dict:
        return {"schema": SCHEMA, "version": FILTER_VERSION, "terms": sorted(set(self.terms)),
                "record_threshold": self.record_threshold, "record_fraction": self.record_fraction,
                "turn_window": self.turn_window,
                "exclude_providers": sorted(self.exclude_providers), "exclude_records": sorted(self.exclude_records)}

    @property
    def digest(self) -> str:
        raw = json.dumps(self.canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @property
    def _pattern(self) -> re.Pattern | None:
        cached = self.__dict__.get("_compiled")
        if cached is None and self.terms:
            cached = re.compile(r"(?<!\w)(?:" + "|".join(f"(?:{t})" for t in sorted(set(self.terms))) + r")(?!\w)",
                                re.IGNORECASE)
            object.__setattr__(self, "_compiled", cached)
        return cached

    def matches(self, text: str | None) -> bool:
        pattern = self._pattern
        return bool(text and pattern is not None and pattern.search(text))

    def record_reason(self, meta: dict) -> str | None:
        """``career_excluded`` when the record's own facts exclude it before any turn is read."""
        provider = str(meta.get("source", "")).split(":", 1)[0]
        if provider in self.exclude_providers or meta.get("id") in self.exclude_records:
            return CAREER_EXCLUDED
        if self.matches(str((meta.get("normalized") or {}).get("title") or "")):
            return CAREER_EXCLUDED
        return None

    def apply(self, meta: dict, chunks: list) -> tuple[str | None, list, int]:
        """``(reason, kept chunks, turns dropped)`` for one record's retrievable chunks (``chunker.Chunk``)."""
        reason = self.record_reason(meta)
        if reason:
            return reason, [], 0
        turns: list[int] = []
        text: dict[int, list[str]] = {}
        for c in chunks:
            if c.turn not in text:
                turns.append(c.turn)
                text[c.turn] = []
            text[c.turn].append(c.text)
        hits = [i for i, t in enumerate(turns) if self.matches("\n".join(text[t]))]
        if len(hits) >= self.record_threshold and len(hits) >= self.record_fraction * len(turns):
            return CAREER_EXCLUDED, [], len(turns)
        dropped = {turns[j] for i in hits for j in range(max(0, i - self.turn_window), min(len(turns), i + self.turn_window + 1))}
        kept = [c for c in chunks if c.turn not in dropped]
        if chunks and not kept:
            return CAREER_EXCLUDED, [], len(turns)
        return None, kept, len(dropped)


def load(default: str | os.PathLike | None = None, private: str | os.PathLike | None = None, *,
         require_private: bool = False) -> CareerFilter:
    """The merged rules, or ``FilterError``. ``default``: the repository's generic file unless given."""
    base = _read(Path(default) if default else DEFAULT_RULES, private=False)
    docs = [base]
    if private:
        docs.append(_read(Path(private), private=True))
    elif require_private:
        raise FilterError("filter_missing")
    thresholds = [d["record_threshold"] for d in docs if d.get("record_threshold") is not None] or [3]
    windows = [d["turn_window"] for d in docs if d.get("turn_window") is not None] or [1]
    fractions = [float(d["record_fraction"]) for d in docs if d.get("record_fraction") is not None] or [0.1]
    return CareerFilter(
        terms=tuple(t for d in docs for t in d.get("terms", [])), record_threshold=min(thresholds),
        record_fraction=min(fractions), turn_window=max(windows),
        exclude_providers=frozenset(p for d in docs for p in d.get("exclude_providers", [])),
        exclude_records=frozenset(r for d in docs for r in d.get("exclude_records", [])), has_private=len(docs) > 1)


__all__ = ["CareerFilter", "FilterError", "load", "SCHEMA", "FILTER_VERSION", "PROFILE", "CAREER_EXCLUDED", "DEFAULT_RULES"]
