"""Runs inside a merged tree (the memory branch's shelf code plus this branch's Workshop code) and prints one JSON object.

It registers the Workshop provider **in this process only** (a monkeypatch of three tables), never in the shelf's files, and
traverses the real consumer: Bundle -> Shelf.stage -> drain -> record validation -> exact edition lookup -> index policy."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.workshop_journal import vault_export as vx
from core.workshop_journal.journal import Journal

out: dict = {}
work = Path(tempfile.mkdtemp())
(work / "ws").mkdir(mode=0o700)
clock = {"now": datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)}
j = Journal(str(work / "ws"), "workshop-neubau", clock=lambda: clock["now"])


def say(kind, text, actor="claude-code", **extra):
    payload = {"progress": {"action": "working"}, "request": {"action": "review", "expected_result": "Findings."}}[kind]
    clock["now"] += timedelta(seconds=30)
    env = {"actor": actor, "kind": kind, "item": "topic:scenario", "topic": "scenario", "text": text, "payload": payload,
           "recipients": ["codex"] if kind == "request" else [], **extra}
    r = j.append(env)
    assert r.ok, r.to_json()
    return r


say("request", "Review the spec.")
say("progress", "Reading it.", actor="codex")
j.append({"actor": "host", "kind": "health", "item": "host:mac", "text": "ok", "payload": {"component": "disk", "status": "ok",
          "observed_at": "2026-10-08T15:01:00Z"}}, software=True)
events = j.read().events
exp = vx.day_export(events, "2026-10-08")
out["listing"] = exp.listing()

from core.memory_shelf import editions, record, render, sessions, shelf as shelf_mod
from core.memory_index import policy

# 1. Without registration the consumer refuses: unknown chair
try:
    vx.build_bundle("workshop-neubau", j.stream, exp)
    out["unregistered_bundle"] = "built"
except record.InvalidRecord as exc:
    out["unregistered_bundle"] = "refused:" + str(exc)

# 2. Register in this process only
record.CHAIRS = record.CHAIRS | frozenset({vx.CHAIR})
sessions.NORMALIZER_VERSION[vx.PROVIDER] = vx.NORMALIZER
out["gate_before_floor"] = None
bundle = vx.build_bundle("workshop-neubau", j.stream, exp)
out["gate_before_floor"] = policy.record_gate(bundle.meta)
sessions.RETRIEVAL_MIN_NORMALIZER[vx.PROVIDER] = vx.NORMALIZER
out["gate_after_floor"] = policy.record_gate(bundle.meta)

# 3. Stage, drain, validate, look up the exact edition
shelf = shelf_mod.Shelf(work / "shelf", min_free_bytes=0)
shelf.init_layout()
path, err = shelf.stage(bundle)
out["stage"] = [bool(path), err]
results = shelf.drain()
out["drain"] = [[r.outcome, r.reason] for r in results]
rec_path = shelf.find_record(bundle.meta["id"])
meta, body = record.load(record.read(rec_path))
out["record"] = {"chair": meta["chair"], "source": meta["source"], "kind": meta["kind"], "tier": meta["tier"], "scope": meta["scope"],
                 "edition": meta["edition"], "source_hash": meta["source_hash"], "tags": meta["tags"]}
found = editions.find(rec_path.parent, bundle.edition)
out["edition_lookup"] = {"found": bool(found), "number": found.number if found else None,
                         "hash_matches_record": editions.digest(bundle.edition) == meta["edition_hash"]}
edition_file = editions.list_editions(rec_path.parent)[0].path
restored = vx.restore_prefix(edition_file.read_bytes())            # the journal is not consulted
out["restore"] = {"sha_matches_source_hash": restored.sha256 == meta["source_hash"], "same_as_exported_prefix": restored.prefix == exp.prefix,
                  "payload_only_value": restored.rows[0]["payload"]["expected_result"], "rows": len(restored.rows),
                  "authority_and_item_kept": restored.rows[0]["item"] == "topic:scenario" and "authority_ref" in restored.rows[0],
                  "retained_kind": meta.get("retained", {}).get("kind")}
turns = render.parse_body(body)
out["turns"] = {"speakers": sorted({t.speaker for t in turns}), "who": [t.who for t in turns], "count": len(turns),
                "seqs": [(t.attrs or {}).get("seq") for t in turns]}
out["retrievable_workshop_turns"] = len(policy.retrievable_turns(turns))

# 4. A permitted dialogue fixture is still indexed
dialogue = sessions.Parsed("claude-code", "Claude Code", source_id="fixture-1", normalizer=sessions.NORMALIZER_VERSION["claude-code"])
dialogue.add(sessions.HUMAN, "Hello there", 1, "role:user")
dialogue.add(sessions.ASSISTANT, "General Kenobi", 2, "role:assistant")
out["retrievable_dialogue_turns"] = len(policy.retrievable_turns(dialogue.turns))

# 5. A later prefix adds an immutable edition; replaying the older prefix changes nothing
for n in range(3):
    say("progress", f"More work {n}.", actor="codex")
events2 = j.read().events
exp2 = vx.day_export(events2, "2026-10-08")
bundle2 = vx.build_bundle("workshop-neubau", j.stream, exp2)
shelf.stage(bundle2)
r2 = shelf.drain()
out["second_drain"] = [[r.outcome, r.reason] for r in r2]
shelf.stage(bundle2)                                   # exact repeat
out["repeat_drain"] = [[r.outcome, r.reason] for r in shelf.drain()]
older = vx.build_bundle("workshop-neubau", j.stream, vx.day_export(events2, "2026-10-08", through_seq=exp.through_seq))
shelf.stage(older)
out["older_drain"] = [[r.outcome, r.reason] for r in shelf.drain()]
meta2, _ = record.load(record.read(shelf.find_record(bundle.meta["id"])))
out["after"] = {"edition": meta2["edition"], "revision": meta2["normalized"].get("source_revision"), "events": [e["kind"] for e in meta2["events"]],
                "editions_on_disk": len(editions.list_editions(rec_path.parent))}
old_edition = editions.find(rec_path.parent, bundle.edition)
out["first_edition_still_exact"] = bool(old_edition) and editions.digest(old_edition.path.read_bytes()) == meta["edition_hash"]
print(json.dumps(out, sort_keys=True))
