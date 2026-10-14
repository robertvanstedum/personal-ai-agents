"""Synthetic fixtures for the index tests. No real conversation is ever read.

``FakeEmbedder`` is deterministic: each word maps to a *concept* (a small synonym table makes "loaf" and "bread"
the same concept), each concept to a fixed dimension, so a paraphrase with no shared word still lands near its
source. That is enough to tell the keyword and meaning lists apart in tests.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from core.memory_index.embedder import DIMS, PIN_DIGEST, PIN_NAME, Embedder, EmbedderUnavailable, Identity
from core.memory_shelf import bundle as bundles
from core.memory_shelf import events as ev
from core.memory_shelf import record
from core.memory_shelf.sessions import Parsed
from core.memory_shelf.shelf import Shelf

T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
FULL_DIGEST = PIN_DIGEST + "f" * 52

CONCEPTS = {
    "bread": "bread", "loaf": "bread", "sourdough": "bread", "dough": "bread", "rise": "bread", "yeast": "bread",
    "telescope": "sky", "planets": "sky", "scope": "sky", "stars": "sky", "jupiter": "sky",
    "invoice": "money", "billing": "money", "payment": "money", "bill": "money",
    "garden": "plant", "tomatoes": "plant", "seedlings": "plant",
}


class FakeEmbedder(Embedder):
    def __init__(self, digest: str = FULL_DIGEST, name: str = f"{PIN_NAME}:latest", down: bool = False):
        self.digest, self.name, self.down, self.calls, self.texts, self.releases = digest, name, down, 0, 0, 0

    def release(self):
        self.releases += 1

    def identity(self) -> Identity:
        if self.down:
            raise EmbedderUnavailable("down")
        return Identity(self.name, self.digest, DIMS)

    def _embed(self, texts):
        if self.down:
            raise EmbedderUnavailable("down")
        self.calls += 1
        self.texts += len(texts)
        out = []
        for text in texts:
            vec = [0.0] * DIMS
            vec[0] = 0.05                                     # never a zero vector
            for word in text.lower().replace(":", " ").split():
                word = word.strip(".,?!'\"")
                if word in ("search_document", "search_query"):
                    continue
                concept = CONCEPTS.get(word, word)
                vec[1 + int(hashlib.sha256(concept.encode()).hexdigest(), 16) % (DIMS - 1)] += 1.0
            out.append(vec)
        return out


def make_shelf(tmp_path: Path) -> Shelf:
    shelf = Shelf(tmp_path / "shelf", min_free_bytes=0, clock=lambda: T0)
    shelf.init_layout()
    return shelf


def add_record(shelf: Shelf, provider: str, sid: str, turns: list[tuple[str, str]], *, normalizer: int = 1,
               kind: str = "session", chair: str | None = None, created: str = "2026-10-01T10:00:00Z") -> str:
    """Ingest a synthetic conversation through the shelf's own writer; returns the record id."""
    chairs = {"grok": "Grok", "codex": "Codex", "claude-code": "Claude Code", "claude-ai": "Claude", "canary": "Claude Code"}
    parsed = Parsed(provider, chair or chairs.get(provider, "Grok"), source_id=sid, identity="source",
                    normalizer=normalizer)
    for i, turn in enumerate(turns, 1):
        if len(turn) == 3:                                   # (speaker, text, attrs): needs the typed-turn contract
            parsed.add(turn[0], turn[1], i, "test", turn[2])
        else:
            parsed.add(turn[0], turn[1], i, "test")
    raw = repr(turns).encode()
    b = bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"{provider}:{sid}",
                            title=f"{provider} {sid}", origin=f"test:{provider}", created=created, kind=kind,
                            ledger_key=bundles.ledger_key_for(provider, sid))
    result = shelf.ingest(b)
    assert result.outcome in ("captured", "edition-added"), result
    return result.record_id


def main_path(shelf: Shelf, record_id: str) -> Path:
    return shelf.find_record(record_id)


def designate(shelf: Shelf, record_id: str) -> None:
    """Test-only: the owner designates a record (appends an event; every line number below it moves)."""
    path = main_path(shelf, record_id)
    record.append_event(path, ev.make_event("designated-curated", "robert", now=T0, via="moi-designate"))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def tree_digest(root: Path) -> str:
    """One hash over every file's path and bytes under ``root`` (index folder excluded): proves nothing was written."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "_index" not in p.parts):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def world(tmp_path: Path) -> tuple[Shelf, dict[str, str]]:
    """A small mixed shelf: retrievable Grok records, a held Codex v2 record, a canary."""
    shelf = make_shelf(tmp_path)
    ids = {
        "bread": add_record(shelf, "grok", "g-bread", [
            ("human", "How do I make my sourdough rise better?"),
            ("assistant", "Feed the starter twice a day and proof the dough somewhere warm.\nAn overnight cold "
                          "proof also helps the loaf.")]),
        "sky": add_record(shelf, "grok", "g-sky", [
            ("human", "Which telescope should I buy for looking at planets?"),
            ("assistant", "A 6 inch Dobsonian shows Jupiter's moons and Saturn's rings.")]),
        "money": add_record(shelf, "grok", "g-money", [
            ("human", "Draft the invoice reminder for the October billing run."),
            ("system", "SYSTEM NOTE invoice reminder internal"),
            ("assistant", "Here is a short reminder about the October invoice.")]),
        "held": add_record(shelf, "codex", "c-old", [
            ("human", "APPROVAL REVIEW prompt that must never be retrieved telescope invoice"),
            ("assistant", "decision json")], normalizer=2),
        "canary": add_record(shelf, "canary", "canary-2026-10-04", [
            ("human", "canary marker phrase zebra quartz"), ("assistant", "canary reply zebra quartz")], kind="canary"),
    }
    return shelf, ids
