import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import cos
from core.memory_index import search, policy
from scripts.memory.dev_feed import run

CID = "cosc-0123456789abcdef01234567"
TID = "t-0123456789abcdef"
AT = "2026-11-05T06:00:00Z"


def source(tmp_path, private=False):
    root = tmp_path / "history"
    (root / "conversations").mkdir(parents=True)
    (root / "turns").mkdir()
    meta = {"schema": "minimoi.cos.conversation/1", "conversation_id": CID, "private": private,
            "environment": "dev", "data_scope": "personal", "created_at": AT}
    (root / "conversations" / (CID + ".json")).write_text(json.dumps(meta))
    row = {"schema": "minimoi.cos.turn/1", "conversation_id": CID, "turn_id": TID,
           "seq": 1, "status": "pending", "user_text": "Remember the amber otter rendezvous",
           "reply_text": None, "created_at": AT}
    turns = root / "turns" / (CID + ".jsonl")
    turns.write_text(json.dumps(row) + "\n")
    return root, turns, row


def mc_export(root):
    root.mkdir()
    doc = {"schema": "minimoi.mc-conversation/1", "instance": "dev", "conversation_id": "c-0123456789ab",
           "source_id": "dev:c-0123456789ab", "archived": False, "legacy": False, "created_at": AT,
           "updated_at": AT, "turns": [{"note_id": 1, "created_at": AT, "author_kind": "owner",
                                       "text": "Please arrange the blue heron meeting", "who": "robert", "label": "Robert"}]}
    (root / "dev--c-0123456789ab.json").write_text(json.dumps(doc))
    manifest(root)


def manifest(root):
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("dev--*.json")}
    (root / "feed-export.json").write_text(json.dumps({"schema": "minimoi.dev-mc-export/1", "status": "ok",
        "completed_at": datetime.now(timezone.utc).isoformat(), "files": files}))


class NoModel:
    def release(self):
        pass
    def check_pin(self):
        raise AssertionError("no model calls permitted")


def test_chat_to_retrieval_and_new_edition(tmp_path):
    root, turns, row = source(tmp_path)
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    args = dict(cos_root=root, mc_root=mc_root, shelf_root=tmp_path / "shelf",
                index_path=tmp_path / "index" / "memory.sqlite", jobs_root=tmp_path / "jobs", min_free=0)
    first = run(**args)
    assert first["state"] == "ok", first
    again = run(**args)
    assert {r["outcome"] for r in again["receipts"]} == {"unchanged"}
    answered = dict(row, reply_text="The amber otter rendezvous is on Friday", status="answered", answered_at=AT)
    with turns.open("a") as f:
        f.write(json.dumps(answered) + "\n")
    after = run(**args)
    assert after["state"] == "ok", after
    assert "edition-added" in {r["outcome"] for r in after["receipts"]}
    result = search.search("amber otter", shelf_root=args["shelf_root"], index_path=args["index_path"],
                           embedder=NoModel(), caller=policy.Caller(tier="all", scopes=("robert",)))
    assert result["keyword"]
    assert not result["unresolved"]
    denied = search.search("amber otter", shelf_root=args["shelf_root"], index_path=args["index_path"],
                           embedder=NoModel(), caller=policy.Caller(tier="all", scopes=("mandate:other",)))
    assert not denied["keyword"]
    # The deployed API's authentication + freshness gate uses the same index.
    from scripts.memory.dev_ask import app_for
    from core.memory_ask.identity import add_agent
    token = add_agent(tmp_path / "memory-ask/agents.json", "cos", tiers=("curated", "all"))
    # app_for expects the standard deployment folder names.
    (tmp_path / "memory").symlink_to(args["shelf_root"], target_is_directory=True)
    (tmp_path / "memory-index").symlink_to(args["index_path"].parent, target_is_directory=True)
    client = app_for(tmp_path).test_client()
    response = client.post("/v1/ask", json={"query": "amber otter", "include_raw": True},
                           headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200, response.json
    assert response.json["keyword"]


def test_private_not_read(tmp_path):
    root, turns, _ = source(tmp_path, private=True)
    turns.unlink()
    with pytest.raises(cos.Refused, match="private"):
        cos.read(root, CID)


def test_partial_journal_and_link_refused(tmp_path):
    root, turns, _ = source(tmp_path)
    turns.write_bytes(turns.read_bytes() + b'{"unfinished":')
    with pytest.raises(cos.Refused, match="partial_journal"):
        cos.read(root, CID)
    turns.unlink()
    turns.symlink_to(tmp_path / "other")
    with pytest.raises(OSError):
        cos.read(root, CID)


def test_missing_sources_cannot_be_healthy(tmp_path):
    report = run(cos_root=tmp_path / "missing", mc_root=tmp_path / "absent", shelf_root=tmp_path / "shelf",
                 index_path=tmp_path / "index" / "memory.sqlite", jobs_root=tmp_path / "jobs", min_free=0)
    assert report["state"] == "failed"
    assert report["errors"]["cos_missing"]
    assert report["errors"]["mc_missing"]


def test_withdrawn_and_stale_sources_leave_retrieval(tmp_path):
    root, turns, row = source(tmp_path)
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    args = dict(cos_root=root, mc_root=mc_root, shelf_root=tmp_path / "shelf",
                index_path=tmp_path / "index" / "memory.sqlite", jobs_root=tmp_path / "jobs", min_free=0)
    assert run(**args)["state"] == "ok"
    meta_path = root / "conversations" / (CID + ".json")
    meta = json.loads(meta_path.read_text())
    meta["private"] = True
    meta_path.write_text(json.dumps(meta))
    assert run(**args)["state"] == "ok"
    result = search.search("amber otter", shelf_root=args["shelf_root"], index_path=args["index_path"],
                           embedder=NoModel(), caller=policy.Caller(tier="all", scopes=("robert",)))
    assert not result["keyword"]
    receipt_path = mc_root / "feed-export.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["completed_at"] = "2020-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps(receipt))
    assert run(**args)["state"] == "failed"
    result = search.search("blue heron", shelf_root=args["shelf_root"], index_path=args["index_path"],
                           embedder=NoModel(), caller=policy.Caller(tier="all", scopes=("robert",)))
    assert not result["keyword"]


def test_source_links_and_journal_rollback(tmp_path):
    root, turns, row = source(tmp_path)
    row.update(context_ref={"task_id": "task-123"}, commitment_receipts=[{"receipt_id": "r-456"}],
               picture_revision="p-789", channel="portal")
    turns.write_text(json.dumps(row) + "\n")
    parsed, _ = cos.read(root, CID)
    evidence = json.loads(parsed.notes[0]["text"])
    assert evidence["context_ref"] == row["context_ref"]
    assert evidence["commitment_receipts"] == row["commitment_receipts"]
    turns.write_text("")
    with pytest.raises(cos.Refused, match="journal_rewritten"):
        cos.read(root, CID, checkpoint=parsed.manifest["cos_journal_checkpoint"])


def test_new_empty_conversation(tmp_path):
    root, turns, _ = source(tmp_path)
    turns.unlink()
    parsed, _ = cos.read(root, CID)
    assert not parsed.turns


def test_overlapping_run_cannot_replace_active_status(tmp_path):
    import fcntl
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    active = jobs / "dev-memory-feed.json"
    active.write_bytes(b'{"active_run":"unchanged"}')
    with (jobs / ".dev-memory-feed.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = run(cos_root=tmp_path / "cos", mc_root=tmp_path / "mc", shelf_root=tmp_path / "shelf",
                     index_path=tmp_path / "index/memory.sqlite", jobs_root=jobs, min_free=0)
    assert report == {"state": "busy"}
    assert active.read_bytes() == b'{"active_run":"unchanged"}'
    assert not (tmp_path / "shelf").exists()


def test_good_old_records_cannot_mask_missing_new_index_row(tmp_path, monkeypatch):
    from scripts.memory import dev_feed
    from core.memory_index import store
    root, _, _ = source(tmp_path)
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    original = dev_feed.indexer.update
    def lose_cos(*args, **kwargs):
        result = original(*args, **kwargs)
        con = store.connect(kwargs["index_path"], keyword_only=True)
        try:
            rid = con.execute("SELECT record_id FROM records WHERE provider='minimoi-cos'").fetchone()[0]
            store.delete_record_chunks(con, rid, vectors=False)
            con.execute("DELETE FROM records WHERE record_id=?", (rid,))
            con.commit()
        finally:
            con.close()
        return result
    monkeypatch.setattr(dev_feed.indexer, "update", lose_cos)
    report = run(cos_root=root, mc_root=mc_root, shelf_root=tmp_path / "shelf",
                 index_path=tmp_path / "index/memory.sqlite", jobs_root=tmp_path / "jobs", min_free=0)
    assert report["verification"]["chunks"] > 0  # MC still looks fine in isolation
    assert report["state"] == "failed"
    assert report["errors"]["receipt_not_indexed"] == 1


def test_platform_only_evidence_is_saved_but_not_owner_dialogue(tmp_path):
    root, _, _ = source(tmp_path)
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    path = mc_root / "dev--c-0123456789ab.json"
    doc = json.loads(path.read_text())
    doc["turns"][0].update(author_kind="platform", who="platform", text="Synthetic platform receipt only")
    path.write_text(json.dumps(doc))
    manifest(mc_root)
    report = run(cos_root=root, mc_root=mc_root, shelf_root=tmp_path / "shelf",
                 index_path=tmp_path / "index/memory.sqlite", jobs_root=tmp_path / "jobs", min_free=0)
    assert report["state"] == "ok", report
    assert report["counts"]["mc_evidence_only"] == 1
    receipt = next(r for r in report["receipts"] if r["source"].startswith("minimoi-mc:"))
    assert receipt["record_id"] and receipt["dialogue_expected"] is False
    result = search.search("Synthetic platform receipt only", shelf_root=tmp_path / "shelf",
                           index_path=tmp_path / "index/memory.sqlite", embedder=NoModel(),
                           caller=policy.Caller(tier="all", scopes=("robert",)))
    assert not result["keyword"]


def test_mc_index_is_published_with_career_turns_filtered_and_served_by_the_mc_profile(tmp_path):
    """#594: the feed also publishes MC's career-filtered index; dev_ask's MC profile serves only that index."""
    root, turns, row = source(tmp_path)
    career = dict(row, seq=2, turn_id="t-0123456789abcdee", user_text="Rewrite my resume for the recruiter call",
                  reply_text="Here is a tighter resume summary", status="answered", answered_at=AT)
    with turns.open("a") as f:
        f.write(json.dumps(career) + "\n")
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    export = mc_root / "dev--c-0123456789ab.json"
    doc = json.loads(export.read_text())
    doc["turns"] += [{"note_id": 2, "created_at": AT, "author_kind": "owner", "who": "robert", "label": "Robert",
                      "text": "Neighbour turn, dropped with the next one"},
                     {"note_id": 3, "created_at": AT, "author_kind": "owner", "who": "robert", "label": "Robert",
                      "text": "Also, the cover letter for the hiring manager needs a pass"},
                     {"note_id": 4, "created_at": AT, "author_kind": "owner", "who": "robert", "label": "Robert",
                      "text": "Unrelated aside about sourdough"},
                     {"note_id": 5, "created_at": AT, "author_kind": "owner", "who": "robert", "label": "Robert",
                      "text": "Then wire the green kestrel gauge into health"}]
    export.write_text(json.dumps(doc))
    manifest(mc_root)
    data = tmp_path / "vault"
    args = dict(cos_root=root, mc_root=mc_root, shelf_root=data / "memory", index_path=data / "memory-index" / "memory.sqlite",
                jobs_root=data / "jobs", min_free=0, mc_index_path=data / "memory-index" / "memory-mc.sqlite")
    report = run(**args)
    assert report["state"] == "ok" and report["mc_index"]["code"] == "ok", report
    caller = policy.Caller(tier="all", scopes=("robert",))
    def hits(index, q):
        return search.search(q, shelf_root=args["shelf_root"], index_path=index, embedder=NoModel(), caller=caller)["keyword"]
    assert hits(args["index_path"], "recruiter") and hits(args["index_path"], "amber otter")   # CoS's index: unchanged
    assert hits(args["mc_index_path"], "recruiter") == []                          # MC's: never
    assert hits(args["mc_index_path"], "amber otter") == []                        # CoS's conversations: excluded by default
    assert hits(args["index_path"], "cover letter")
    assert hits(args["mc_index_path"], "cover letter") == []                       # a career turn in an MC conversation
    assert hits(args["mc_index_path"], "blue heron meeting")                       # MC's: everything else
    assert hits(args["mc_index_path"], "green kestrel gauge")
    from scripts.memory.dev_ask import app_for
    from core.memory_ask.identity import add_agent
    token = add_agent(data / "memory-ask" / "agents-mc.json", "mc", tiers=("curated", "all"))
    client = app_for(data, "mc").test_client()
    answer = client.post("/v1/ask", json={"query": "blue heron", "include_raw": True},
                         headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 200 and answer.get_json()["keyword"]


def test_ask_keeps_answering_from_the_published_index_while_a_feed_run_is_in_progress(tmp_path):
    """Dev, 14 Nov: a /v1/ask during the feed's rebuild got 503. The indexes are swapped in whole, so the last good run's
    files stay servable while the next run works; a stale last success still refuses."""
    from datetime import timedelta
    from core.jobs import status
    from scripts.memory import dev_feed
    from scripts.memory.dev_ask import app_for
    from core.memory_ask.identity import add_agent
    root, _, _ = source(tmp_path)
    mc_root = tmp_path / "mc"
    mc_export(mc_root)
    data = tmp_path / "vault"
    assert run(cos_root=root, mc_root=mc_root, shelf_root=data / "memory", index_path=data / "memory-index" / "memory.sqlite",
               jobs_root=data / "jobs", min_free=0)["state"] == "ok"
    token = add_agent(data / "memory-ask" / "agents.json", "cos", tiers=("curated", "all"))
    client = app_for(data).test_client()
    ask = lambda: client.post("/v1/ask", json={"query": "blue heron", "include_raw": True},
                              headers={"Authorization": f"Bearer {token}"})
    assert ask().status_code == 200
    now = datetime.now(timezone.utc)
    status.start_run(data / "jobs", dev_feed.JOB, "r" * 24, now)            # the next run starts: state "running"
    answer = ask()
    assert answer.status_code == 200 and answer.get_json()["keyword"]
    doc = status.read_status(data / "jobs", "dev-memory-feed.json", "dev-memory-feed")
    stale = status.build(job_id=doc["job_id"], host=doc["host"], run_id=doc["run_id"], state="running",
                         started_at=now, summary="run in progress", last_success_at=now - timedelta(hours=1))
    status.write_status(data / "jobs", "dev-memory-feed.json", stale)
    refused = ask()
    assert refused.status_code == 503 and refused.get_json() == {"error": "feed_not_ready"}
