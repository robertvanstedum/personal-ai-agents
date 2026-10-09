"""Builds the synthetic shelf snapshot in tests/vault_t1/snapshot/ with the REAL shelf writer.

Run once, inside a merged tree (the memory branch's core/ and utils/ plus this branch's core/workshop_journal), as
    python tests/vault_t1/make_snapshot.py <output-dir>
Everything is invented. The result is committed so the Vault tests need no shelf code, only the on-disk format."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_shelf import bundle as bundle_mod, record, sessions, shelf as shelf_mod
from core.memory_shelf.sessions import ASSISTANT, COORDINATION, HUMAN, Parsed
from core.workshop_journal import vault_export as vx
from core.workshop_journal.journal import Journal

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
shelf = shelf_mod.Shelf(out, min_free_bytes=0)
shelf.init_layout()
record.CHAIRS = record.CHAIRS | frozenset({vx.CHAIR})
sessions.NORMALIZER_VERSION[vx.PROVIDER] = vx.NORMALIZER


def ingest(parsed: Parsed, key: str, title: str, created: str, sha: str, scope: str | None = None):
    b = bundle_mod.from_parsed(parsed, sha, 1000, key=key, title=title, origin="synthetic", created=created)
    if scope:
        b.meta["scope"] = scope
    shelf.stage(b)
    results = shelf.drain()
    assert results and results[0].outcome in ("captured", "edition-added"), results
    return b


p1 = Parsed("claude-code", "Claude Code", source_id="synthetic-garden", started="2026-10-01T14:00:00Z", normalizer=sessions.NORMALIZER_VERSION["claude-code"])
p1.add(HUMAN, "Plan the garden beds for spring. Tomatoes need the sunny corner.", 1, "origin:human")
p1.add(ASSISTANT, "Put the tomatoes in the south-east bed and the basil beside them.", 2, "role:assistant")
p1.add(HUMAN, "Decision: we will build raised beds from cedar, not pine.", 3, "origin:human")
p1.add(ASSISTANT, "Noted: raised cedar beds. I will list the lumber needed.", 4, "role:assistant")
ingest(p1, "claude-code:synthetic-garden", "Garden planning", "2026-10-01T14:00:00Z", "a" * 64)

p1b = Parsed("claude-code", "Claude Code", source_id="synthetic-garden", started="2026-10-01T14:00:00Z", normalizer=sessions.NORMALIZER_VERSION["claude-code"])
for turn in p1.turns:
    p1b.add(turn.speaker, turn.text, turn.line, turn.basis)
p1b.add(HUMAN, "Add the lumber list to the shopping note: eight cedar boards.", 5, "origin:human")
p1b.add(ASSISTANT, "Shopping note updated with eight cedar boards.", 6, "role:assistant")
ingest(p1b, "claude-code:synthetic-garden", "Garden planning", "2026-10-01T14:00:00Z", "e" * 64)     # a grown source: edition 2

p2 = Parsed("codex", "Codex", source_id="synthetic-review", started="2026-10-02T09:00:00Z", normalizer=sessions.NORMALIZER_VERSION["codex"])
p2.add(HUMAN, "Review the irrigation schedule for the north bed.", 1, "event_msg")
p2.add(ASSISTANT, "The schedule waters twice daily; once at dawn is enough in October.", 2, "event_msg")
p2.add(COORDINATION, "handoff to claude-code: apply the dawn-only schedule", 3, "handoff", {"class": "handoff"})
ingest(p2, "codex:synthetic-review", "Irrigation review", "2026-10-02T09:00:00Z", "b" * 64)

p3 = Parsed("claude-ai", "Claude", source_id="synthetic-export-1", started="2026-09-20T18:00:00Z", normalizer=sessions.NORMALIZER_VERSION["claude-ai"])
p3.add(HUMAN, "What should I name the compost heap? The word zymurgy keeps coming to mind.", 1, "export")
p3.add(ASSISTANT, "Call it the Heap of Many Thanks.", 2, "export")
ingest(p3, "claude-ai:synthetic-export-1", "Naming the compost heap", "2026-09-20T18:00:00Z", "c" * 64)

p4 = Parsed("grok", "Grok", source_id="synthetic-private-mandate", started="2026-09-25T10:00:00Z", normalizer=sessions.NORMALIZER_VERSION["grok"])
p4.add(HUMAN, "Mandate-scoped note: the orchard budget is forty dollars.", 1, "export")
p4.add(ASSISTANT, "Understood; the orchard budget stays inside the mandate.", 2, "export")
ingest(p4, "grok:synthetic-private-mandate", "Orchard budget (mandate)", "2026-09-25T10:00:00Z", "d" * 64, scope="mandate:01ARZ3NDEKTSV4RRFFQ69G5FAV")

# a Workshop day with a real decision trail, built through the export seam
work = Path(sys.argv[1] + ".journal")
work.mkdir(mode=0o700, exist_ok=True)
clock = {"now": datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)}
j = Journal(str(work), "workshop-neubau", clock=lambda: clock["now"])


def say(kind, actor, text, payload, **extra):
    clock["now"] += timedelta(seconds=45)
    r = j.append({"actor": actor, "kind": kind, "item": "topic:garden-build", "topic": "garden-build", "text": text, "payload": payload, **extra})
    assert r.ok, r.to_json()
    return r


req = say("request", "claude-code", "Review the raised-bed plan.", {"action": "review", "expected_result": "Numbered findings."}, recipients=["codex"])
prop = say("decision", "claude-code", "Proposal: cedar beds.", {"record_event_kind": "proposed", "resolves": [], "reason": "cedar lasts longer"})
say("decision", "robert", "Cedar it is; pine is rejected.", {"record_event_kind": "approved-direct", "resolves": [prop.event_id], "reason": "owner chose cedar"},
    authority_ref={"type": "owner-control", "ref": "synthetic:cedar"}, supersedes=prop.event_id)
say("receipt", "codex", "picked up", {"request_id": req.event_id, "recipient": "codex"})
say("result", "codex", "Findings returned.", {"request_id": req.event_id, "recipient": "codex", "outcome": "completed", "limitations": ["did not measure the beds"]})
export = vx.day_export(j.read().events, "2026-10-08")
b = vx.build_bundle("workshop-neubau", j.stream, export)
shelf.stage(b)
assert shelf.drain()[0].outcome == "captured"
print("snapshot written:", out)
