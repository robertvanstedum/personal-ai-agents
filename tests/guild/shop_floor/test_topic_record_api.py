"""The topic Workshop reads the shared Workshop record (the journal) through a read-only API: linking, honest states, no writes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)

from core.workshop_journal.journal import Journal

API = "/guild-next/api/v1"
WORKSHOP = "workshop-neubau"


def _k(n=[0]):
    n[0] += 1
    return f"key-{n[0]:08d}-record"


def _post(client, token, path, body=None, mode="on_record"):
    return client.post(f"{API}{path}", json={"idempotency_key": _k(), **(body or {})}, headers=write_headers(token, mode=mode))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(minutes=5)
        return self.now


def build_journal(root: Path) -> Journal:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    j = Journal(str(root), WORKSHOP, clock=Clock())

    def send(actor, kind, text, payload, topic="garden-build", **extra):
        r = j.append({"actor": actor, "kind": kind, "item": f"topic:{topic}", "topic": topic, "text": text, "payload": payload, **extra})
        assert r.ok, r.to_json()
        return r
    req = send("claude-code", "request", "Review the bed plan.", {"action": "review", "expected_result": "Findings."}, recipients=["codex"])
    send("codex", "receipt", "Codex picked it up.", {"request_id": req.event_id, "recipient": "codex"})
    send("codex", "result", "Two findings.", {"request_id": req.event_id, "recipient": "codex", "outcome": "completed", "limitations": ["site not visited"]})
    send("claude-code", "needs_you", "Cedar or pine?", {"reason_code": "owner_choice", "requested_action": "Pick.", "incident_id": "11111111-2222-4333-8444-555555555551"},
         recipients=["robert"])
    send("robert", "decision", "Pine.", {"record_event_kind": "approved-direct", "resolves": [], "reason": "claimed"},
         authority_ref={"type": "owner-control", "ref": "not-recognised"})
    send("claude-code", "progress", "Ordered the lumber.", {"action": "ordering"})
    send("codex", "progress", "An unrelated topic.", {"action": "x"}, topic="other-topic")
    return j


@pytest.fixture
def rec(floored, tmp_path, monkeypatch):
    source = tmp_path / "source"
    build_journal(source)
    replica = tmp_path / "synced"
    (replica / WORKSHOP).mkdir(parents=True, mode=0o700)
    os.chmod(replica, 0o700)
    shutil.copy(source / WORKSHOP / "events.jsonl", replica / WORKSHOP / "events.jsonl")
    os.chmod(replica / WORKSHOP / "events.jsonl", 0o600)
    monkeypatch.setenv("MINIMOI_WORKSHOPS_DIR", str(replica))
    client = floored.owner()
    token = floored.csrf(client)
    tid = _post(client, token, "/topics/create", {"title": "Garden build", "summary": "Raised beds"}).get_json()["topic"]["id"]
    floored.extra["replica"] = replica
    return floored, client, token, tid, replica


def link(client, token, tid, topic="garden-build", workshop=WORKSHOP):
    return _post(client, token, f"/topics/{tid}/record-link", {"workshop": workshop, "topic": topic})


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        digest.update(p.relative_to(root).as_posix().encode())
        if p.is_file():
            digest.update(p.read_bytes())
    return digest.hexdigest()


def test_an_unlinked_topic_says_so_and_an_unconfigured_server_says_so(rec, monkeypatch):
    portal, client, token, tid, replica = rec
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["status"] == "unlinked" and got["rows"] == [] and "not linked" in got["notice"]
    monkeypatch.delenv("MINIMOI_WORKSHOPS_DIR")
    assert link(client, token, tid).status_code == 200
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["status"] == "unconfigured" and "No shared record is configured" in got["notice"] and got["rows"] == []


def test_linking_is_a_guarded_idempotent_owner_write_and_refuses_bad_names(rec):
    portal, client, token, tid, replica = rec
    body = {"idempotency_key": _k(), "workshop": WORKSHOP, "topic": "garden-build"}
    assert client.post(f"{API}/topics/{tid}/record-link", json=body).status_code == 403
    assert client.post(f"{API}/topics/{tid}/record-link", json=body, headers=write_headers(token, mode="off_record")).status_code == 409
    assert portal.guest().post(f"{API}/topics/{tid}/record-link", json=body, headers=write_headers(token)).status_code in (401, 403)
    for bad in ({"workshop": "../x", "topic": "garden-build"}, {"workshop": WORKSHOP, "topic": "Bad Topic"}, {"workshop": "", "topic": ""}):
        assert _post(client, token, f"/topics/{tid}/record-link", bad).status_code == 422
    first = _post(client, token, f"/topics/{tid}/record-link", {"workshop": WORKSHOP, "topic": "garden-build"}, mode="on_record")
    assert first.status_code == 200 and first.get_json()["link"]["topic"] == "garden-build"
    repeat = client.post(f"{API}/topics/{tid}/record-link", json=body, headers=write_headers(token))
    assert repeat.status_code == 200
    unlinked = _post(client, token, f"/topics/{tid}/record-link", {"clear": True})
    assert unlinked.get_json()["result"] == "unlinked"
    assert client.get(f"{API}/topics/{tid}/record").get_json()["status"] == "unlinked"
    folder = Path(portal.extra["replica"]).parent
    kept = list(folder.rglob("record_link.json.unlinked-*"))
    assert kept and "garden-build" in kept[0].read_text()                                                     # the old link is set aside, not deleted


def test_a_linked_topic_shows_its_own_entries_with_honest_labels(rec):
    portal, client, token, tid, replica = rec
    assert link(client, token, tid).status_code == 200
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["status"] == "ok" and got["complete"] is True and got["link"]["topic"] == "garden-build"
    assert [r["kind"] for r in got["rows"]] == ["request", "receipt", "result", "needs_you", "decision", "progress"]      # the other topic is not here
    assert "An unrelated topic." not in json.dumps(got)
    by = {r["kind"]: r for r in got["rows"]}
    assert [b["text"] for b in by["decision"]["badges"]] == ["claimed approval, not confirmed"]
    assert by["decision"]["author"] == {"name": "robert", "kind": "owner"}                                   # the label is shown, the badge says it is only a claim
    assert by["result"]["badges"][0]["text"].startswith("worker report · 3 newer entries since")
    assert [w["text"] for w in got["waiting_for_you"]] == ["Cedar or pine?"]
    assert any(g["code"] == "unconfirmed_owner_claim" for g in got["gaps"])
    assert "not connected yet" in got["answer_note"]
    assert got["requests"][0]["state"] == "closed" and got["requests"][0]["recipients"]["codex"]["status"] == "returned"


def test_a_damaged_record_is_reported_as_damaged_with_no_rows_and_no_error(rec):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    path = replica / WORKSHOP / "events.jsonl"
    path.write_bytes(path.read_bytes() + b"garbage line\n")
    r = client.get(f"{API}/topics/{tid}/record")
    got = r.get_json()
    assert r.status_code == 200 and got["status"] == "damaged" and got["rows"] == [] and got["waiting_for_you"] == []
    assert "damaged" in got["notice"] and "partial or wrong picture" in got["notice"]


def test_a_cut_off_record_is_incomplete_and_a_missing_one_is_missing(rec):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    path = replica / WORKSHOP / "events.jsonl"
    path.write_bytes(path.read_bytes() + b'{"v":2,"cut')
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["status"] == "incomplete" and got["complete"] is False and len(got["rows"]) == 6 and "cut off" in got["notice"]
    shutil.rmtree(replica / WORKSHOP)
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["status"] == "missing" and got["rows"] == [] and "not on this server yet" in got["notice"]


def test_reading_never_writes_anything_into_the_synced_copy(rec):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    before = tree_hash(replica)
    for _ in range(3):
        assert client.get(f"{API}/topics/{tid}/record").status_code == 200
        assert client.get(f"{API}/topics/{tid}/record?since_seq=2").status_code == 200
        assert client.get(f"{API}/topics/{tid}/record?candidates={WORKSHOP}").status_code == 200
    assert tree_hash(replica) == before


def test_since_seq_lists_what_changed_and_candidates_list_the_journals_topics(rec):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    got = client.get(f"{API}/topics/{tid}/record?since_seq=4").get_json()
    assert got["changed_since"]["since_seq"] == 4 and got["changed_since"]["entries"] == 2
    cands = client.get(f"{API}/topics/{tid}/record?candidates={WORKSHOP}").get_json()["candidates"]
    assert {c["topic"]: c["entries"] for c in cands} == {"garden-build": 6, "other-topic": 1}
    assert client.get(f"{API}/topics/{tid}/record?candidates=../etc").get_json()["candidates"] == []


def test_only_the_owner_can_read_a_topics_record_and_another_topic_is_not_found(rec):
    portal, client, token, tid, replica = rec
    assert portal.guest().get(f"{API}/topics/{tid}/record").status_code in (401, 403)
    assert portal.client().get(f"{API}/topics/{tid}/record").status_code in (401, 403)
    assert client.get(f"{API}/topics/t-000000000000/record").status_code == 404


# ── R18: one response is built from ONE read of the journal ──────────────────────────────────────────────────────────
def _after_read(monkeypatch, action):
    """Wrap Journal.read so that `action` runs right after each read, like the synced copy changing under the page."""
    calls = []
    original = Journal.read

    def wrapped(self, *a, **kw):
        got = original(self, *a, **kw)
        calls.append(1)
        action()
        return got
    monkeypatch.setattr(Journal, "read", wrapped)
    return calls, lambda: monkeypatch.setattr(Journal, "read", original)


def test_the_response_reads_the_journal_once_so_a_change_between_reads_cannot_mix_two_pictures(rec, monkeypatch):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    path = replica / WORKSHOP / "events.jsonl"
    calls, restore = _after_read(monkeypatch, lambda: path.write_bytes(path.read_bytes() + b"garbage line\n"))
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert len(calls) == 1                                                                                   # one read, not one per view
    assert got["status"] == "ok" and got["complete"] is True and len(got["rows"]) == 6                       # the picture is the one that was read, whole
    assert [b["text"] for r in got["rows"] if r["kind"] == "decision" for b in r["badges"]] == ["claimed approval, not confirmed"]
    restore()
    assert client.get(f"{API}/topics/{tid}/record").get_json()["status"] == "damaged"                        # the next look reports the damage, never healthy empty rows


def test_an_entry_that_arrives_between_would_be_reads_is_in_neither_the_rows_nor_the_badges(rec, monkeypatch):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    source = replica.parent / "source"
    j = Journal(str(source), WORKSHOP, clock=Clock())
    path = replica / WORKSHOP / "events.jsonl"

    def newest_claim():
        r = j.append({"actor": "robert", "kind": "decision", "item": "topic:garden-build", "topic": "garden-build", "text": "Skip the timer.",
                      "payload": {"record_event_kind": "approved-direct", "resolves": [], "reason": "claimed"}, "authority_ref": {"type": "owner-control", "ref": "not-recognised"}})
        assert r.ok, r.to_json()
        shutil.copy(source / WORKSHOP / "events.jsonl", path)
    _calls, restore = _after_read(monkeypatch, newest_claim)
    first = client.get(f"{API}/topics/{tid}/record").get_json()
    assert len(first["rows"]) == 6 and len(first["decisions"]["unconfirmed"]) == 1 and "Skip the timer." not in json.dumps(first)
    restore()
    second = client.get(f"{API}/topics/{tid}/record").get_json()
    assert len(second["rows"]) == 7 and len(second["decisions"]["unconfirmed"]) == 2
    assert sum(1 for r in second["rows"] for b in r["badges"] if b["text"] == "claimed approval, not confirmed") == 2


def test_a_long_record_discloses_that_only_the_latest_rows_are_sent(rec, monkeypatch):
    portal, client, token, tid, replica = rec
    link(client, token, tid)
    from minimoi_portal.guild_ui import topic_record
    monkeypatch.setattr(topic_record, "ROW_LIMIT", 4)
    got = client.get(f"{API}/topics/{tid}/record").get_json()
    assert got["rows_total"] == 6 and got["rows_shown"] == 4 and len(got["rows"]) == 4
    assert [r["kind"] for r in got["rows"]] == ["result", "needs_you", "decision", "progress"]                # the latest, oldest first
