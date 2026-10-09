"""The topic inbox (Workshop W1b): contributions from local agents are UNTRUSTED. Imported only when asked, bounded, idempotent;
they can add to the record and change nothing else. Codex's W1 spec review, items 1 and 2."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from minimoi_portal.guild_ui import topics as T
from minimoi_portal.guild_ui.payment_scrub import scrub

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
REPO = Path(__file__).resolve().parents[3]
CARD = "5555 5555 5555 4444"


@pytest.fixture
def store(tmp_path):
    return T.TopicStore(str(tmp_path))


def _topic(store):
    return store.create_topic("robert", "Guild chat")["id"]


def _drop(store, tid, agent, body, name="20261007T210000-aaaa1111.json"):
    p = Path(store.dir) / tid / "inbox" / agent
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_text(body if isinstance(body, str) else json.dumps(body))
    return p / name


def test_put_writes_one_small_file_and_changes_nothing_else(store):
    tid = _topic(store)
    before = store.get_topic(tid, "robert")
    name = store.put_inbox(tid, "codex", kind="finding", text="Ask MC belongs in the card menu.", refs=[{"type": "file", "ref": "x.md"}], model="gpt-5.5")
    assert T.INBOX_FILE_RE.fullmatch(name) and store.inbox_waiting(tid, "robert") == {"codex": 1}
    assert store.get_topic(tid, "robert") == before and store.journal(tid, "robert") == []                   # waiting is not imported
    for bad in ("Codex", "../x", "", "a" * 40):
        with pytest.raises(T.Refused):
            store.put_inbox(tid, bad, kind="note", text="x")
    with pytest.raises(T.Refused):
        store.put_inbox(tid, "codex", kind="note", text="x" * 20_000)
    with pytest.raises(T.TopicNotFound):
        store.put_inbox("t-000000000000", "codex", kind="note", text="x")


def test_import_adds_attributed_unverified_entries_and_only_that(store):
    tid = _topic(store)
    item = store.add_item(tid, "robert", "document", "Spec", text="one")
    other = store.create_topic("robert", "Another")["id"]
    foreign = store.add_item(other, "robert", "note", "n", text="x")
    store.put_inbox(tid, "codex", kind="disagreement", text=f"I disagree. Card {CARD}.", model="gpt-5.5", session_ref="desktop-1",
                    refs=[{"type": "item", "ref": item["id"]}, {"type": "item", "ref": foreign["id"]}, {"type": "file", "ref": "review.md", "quote": "the line"}])
    res = store.import_inbox(tid, "robert", scrub=scrub)
    assert res["imported"] == 1 and res["rejected"] == [] and res.get("refs_dropped") == 1
    (e,) = store.journal(tid, "robert")
    assert e["author"] == {"kind": "agent", "name": "codex", "model": "gpt-5.5", "session_ref": "desktop-1"} and e["via"] == "inbox" and e["verified"] is False
    assert CARD not in e["text"] and e["kind"] == "disagreement"
    assert [r["ref"] for r in e["refs"]] == [item["id"], "review.md"]                                          # the other topic's item is not linked
    assert store.inbox_waiting(tid, "robert") == {}
    assert (Path(store.dir) / tid / "inbox" / "codex" / "done").is_dir()                                       # kept, not deleted


@pytest.mark.parametrize("claimed", [{"verified": True}, {"author": {"kind": "owner", "name": "Robert"}}, {"stage": "returned"}, {"disposition": "accepted"},
                                     {"owner": "robert"}, {"topic": "t-000000000000"}, {"approved": True}])
def test_claims_of_identity_authority_or_another_topic_are_ignored(store, claimed):
    tid = _topic(store)
    r = store.add_item(tid, "robert", "request", "Review", text="please", request={"to": "Codex"})
    d = store.add_item(tid, "robert", "document", "Spec", text="one")
    c = store.add_comment(tid, d["id"], "robert", "A question")
    before = (store.get_item(tid, r["id"], "robert"), store.get_item(tid, d["id"], "robert"), store.get_layout(tid, "robert"))
    _drop(store, tid, "grok", {"v": 1, "kind": "finding", "text": "Looks fine.", **claimed})
    store.import_inbox(tid, "robert")
    (e,) = store.journal(tid, "robert")
    assert e["verified"] is False and e["author"]["kind"] == "agent" and e["author"]["name"] == "grok" and e["via"] == "inbox"
    assert (store.get_item(tid, r["id"], "robert"), store.get_item(tid, d["id"], "robert"), store.get_layout(tid, "robert")) == before   # nothing else moved
    assert store.comments(tid, d["id"], "robert")[0]["disposition"] is None and c["id"]


@pytest.mark.parametrize("kind", ["decision", "decision_change"])
def test_an_agents_decision_is_only_ever_a_proposal(store, kind):
    tid = _topic(store)
    _drop(store, tid, "claude-code", {"v": 1, "kind": kind, "text": "Use F."})
    store.import_inbox(tid, "robert")
    (e,) = store.journal(tid, "robert")
    assert e["kind"] == "proposal" and e["text"].startswith("[proposed as a ") and "only the owner decides" in e["text"] and "Use F." in e["text"]
    assert all(x["kind"] != "decision" for x in store.journal(tid, "robert"))


def test_importing_twice_or_after_a_crash_adds_nothing_twice(store):
    tid = _topic(store)
    p = _drop(store, tid, "codex", {"v": 1, "kind": "note", "text": "Once."})
    assert store.import_inbox(tid, "robert")["imported"] == 1
    # the crash case: the entry was appended, but the file was not moved yet (it is put back)
    (Path(store.dir) / tid / "inbox" / "codex").mkdir(exist_ok=True)
    p.write_text(json.dumps({"v": 1, "kind": "note", "text": "Once."}))
    second = store.import_inbox(tid, "robert")
    assert second["imported"] == 0 and second["already_imported"] == 1 and len(store.journal(tid, "robert")) == 1
    assert store.inbox_waiting(tid, "robert") == {}


def test_a_file_that_is_not_a_small_plain_json_file_is_rejected_and_set_aside(store, tmp_path):
    tid = _topic(store)
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("TOP SECRET")
    adir = Path(store.dir) / tid / "inbox" / "evil"
    adir.mkdir(parents=True)
    os.symlink(secret, adir / "20261007T210000-link0001.json")                                            # a link is never followed
    (adir / "20261007T210000-dir00001.json").mkdir()                                                      # nor is a folder taken for a file
    (adir / "20261007T210000-big00001.json").write_text(json.dumps({"v": 1, "kind": "note", "text": "x" * 20_000}))
    (adir / "20261007T210000-bad00001.json").write_text("{not json")
    (adir / "20261007T210000-kind0001.json").write_text(json.dumps({"v": 1, "kind": "approve", "text": "yes"}))
    (adir / "20261007T210000-text0001.json").write_text(json.dumps({"v": 1, "kind": "note", "text": "   "}))
    (adir / "not a pattern.json").write_text("{}")
    res = store.import_inbox(tid, "robert")
    reasons = {r["file"]: r["reason"] for r in res["rejected"]}
    assert res["imported"] == 0 and len(res["rejected"]) == 7
    assert "plain file" in reasons["20261007T210000-link0001.json"] and "plain file" in reasons["20261007T210000-dir00001.json"]
    assert "larger than" in reasons["20261007T210000-big00001.json"] and "JSON" in reasons["20261007T210000-bad00001.json"]
    assert "unknown kind" in reasons["20261007T210000-kind0001.json"] and reasons["20261007T210000-text0001.json"] == "no text"
    assert secret.read_text() == "TOP SECRET" and "TOP SECRET" not in json.dumps(store.journal(tid, "robert"))   # nothing outside was read into the record
    assert any((adir / "rejected").iterdir()) and store.journal(tid, "robert") == []


def test_a_batch_is_bounded_and_the_rest_waits(store, monkeypatch):
    tid = _topic(store)
    monkeypatch.setattr(T, "INBOX_BATCH_FILES", 3)
    for i in range(5):
        _drop(store, tid, "codex", {"v": 1, "kind": "note", "text": f"n{i}"}, name=f"20261007T21000{i}-aaaa111{i}.json")
    res = store.import_inbox(tid, "robert")
    assert res["imported"] == 3 and res["left_waiting"] == 2 and store.inbox_waiting(tid, "robert") == {"codex": 2}
    assert store.import_inbox(tid, "robert")["imported"] == 2


def test_nothing_imports_by_looking_only_by_asking(store):
    tid = _topic(store)
    store.put_inbox(tid, "codex", kind="note", text="Waiting.")
    store.get_topic(tid, "robert"); store.journal(tid, "robert"); store.inbox_waiting(tid, "robert"); store.verify("robert"); store.list_topics("robert")
    assert store.journal(tid, "robert") == [] and store.inbox_waiting(tid, "robert") == {"codex": 1}


def test_an_unknown_topic_folder_or_other_owner_gets_nothing(store):
    tid = _topic(store)
    store.put_inbox(tid, "codex", kind="note", text="x")
    with pytest.raises(T.TopicNotFound):
        store.import_inbox(tid, "someone-else")
    with pytest.raises(T.TopicNotFound):
        store.import_inbox("../../x", "robert")


# ── the HTTP side and the command line ──────────────────────────────────────
@pytest.fixture
def ws(floored):
    client = floored.owner(); token = floored.csrf(client)
    r = client.post(f"{API}/topics/create", json={"idempotency_key": "tc-inbox-0001", "title": "Inbox topic"}, headers=write_headers(token))
    return floored, client, token, r.get_json()["topic"]["id"]


def test_the_api_looks_without_importing_and_imports_only_on_a_guarded_post(ws):
    portal, client, token, tid = ws
    from minimoi_portal.guild_ui.topics import topics_of
    store = topics_of(portal.app.extensions["guild_ui_next"]["services"])
    store.put_inbox(tid, "codex", kind="finding", text="From the inbox.")
    assert client.get(f"{API}/topics/{tid}/inbox").get_json() == {**client.get(f"{API}/topics/{tid}/inbox").get_json(), "waiting": {"codex": 1}, "total": 1}
    assert client.get(f"{API}/topics/{tid}/journal").get_json()["entries"] == []                              # reading the record did not import
    assert client.get(f"{API}/topics/{tid}/inbox/import").status_code in (404, 405)                           # importing is never a GET
    assert client.get(f"{API}/topics/{tid}/journal").get_json()["entries"] == []
    assert client.post(f"{API}/topics/{tid}/inbox/import", json={"idempotency_key": "imp-key-0001"}).status_code == 403          # CSRF
    off = client.post(f"{API}/topics/{tid}/inbox/import", json={"idempotency_key": "imp-key-0002"}, headers=write_headers(token, mode="off_record"))
    assert off.status_code == 409                                                                             # Private never imports
    ok = client.post(f"{API}/topics/{tid}/inbox/import", json={"idempotency_key": "imp-key-0003"}, headers=write_headers(token)).get_json()
    assert ok["imported"] == 1
    (e,) = client.get(f"{API}/topics/{tid}/journal").get_json()["entries"]
    assert e["author"]["name"] == "codex" and e["verified"] is False and e["via"] == "inbox"


def test_the_command_line_leaves_a_file_and_imports_only_when_told(tmp_path):
    data = tmp_path / "guild"
    store = T.TopicStore(str(data))
    tid = store.create_topic("robert", "CLI topic")["id"]
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "tools" / "workshop" / "topic_inbox.py"), "--data", str(data), *a], capture_output=True, text=True, timeout=60)
    put = run("put", "--topic", tid, "--agent", "grok", "--kind", "decision", "--text", "Use F.", "--ref", "file:note.md", "--model", "grok-4")
    assert put.returncode == 0 and "nothing else was changed" in put.stdout
    assert store.journal(tid, "robert") == []
    assert json.loads(run("waiting", "--topic", tid).stdout) == {"grok": 1}
    assert json.loads(run("import", "--topic", tid).stdout)["imported"] == 1
    (e,) = store.journal(tid, "robert")
    assert e["kind"] == "proposal" and e["author"]["name"] == "grok" and e["refs"] == [{"type": "file", "ref": "note.md"}]
    assert run("verify").returncode == 0
    bad = run("put", "--topic", tid, "--agent", "Not Valid", "--kind", "note", "--text", "x")
    assert bad.returncode == 2 and "not done" in bad.stderr


# ── Codex's Revision 17 review: links in the folder chain, scrub on every path, one honest budget, GET never writes ──
def _journal_text(store, tid):
    return json.dumps(store.journal(tid, "robert"))


def _outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    (out / "sample-123.json").write_text(json.dumps({"v": 1, "kind": "note", "text": "from outside the topic"}))
    return out


def test_a_linked_agent_folder_is_refused_and_the_outside_folder_is_untouched(store, tmp_path):
    tid = _topic(store)
    out = _outside(tmp_path)
    inbox = Path(store.dir) / tid / "inbox"
    inbox.mkdir(parents=True)
    os.symlink(out, inbox / "claude")
    res = store.import_inbox(tid, "robert")
    assert res["imported"] == 0 and any("link" in r["reason"] for r in res["rejected"])
    assert sorted(p.name for p in out.iterdir()) == ["sample-123.json"] and store.journal(tid, "robert") == []     # not read, not moved, no done/ made
    assert store.inbox_waiting(tid, "robert") == {}                                                                  # not even counted
    with pytest.raises(T.Refused):
        store.put_inbox(tid, "claude", kind="note", text="x")                                                        # and nothing is written through it
    assert sorted(p.name for p in out.iterdir()) == ["sample-123.json"]


def test_a_linked_inbox_folder_is_refused(store, tmp_path):
    tid = _topic(store)
    out = tmp_path / "elsewhere"
    (out / "claude").mkdir(parents=True)
    (out / "claude" / "20261007T210000-aaaa1111.json").write_text(json.dumps({"v": 1, "kind": "note", "text": "x"}))
    os.symlink(out, Path(store.dir) / tid / "inbox")
    res = store.import_inbox(tid, "robert")
    assert res["imported"] == 0 and res["rejected"] and "link" in res["rejected"][0]["reason"]
    assert [p.name for p in (out / "claude").iterdir()] == ["20261007T210000-aaaa1111.json"] and store.inbox_waiting(tid, "robert") == {}
    with pytest.raises(T.Refused):
        store.put_inbox(tid, "claude", kind="note", text="x")


@pytest.mark.parametrize("which", ["done", "rejected"])
def test_a_linked_done_or_rejected_folder_is_never_moved_through(store, tmp_path, which):
    tid = _topic(store)
    out = tmp_path / "outside-dest"
    out.mkdir()
    adir = Path(store.dir) / tid / "inbox" / "grok"
    adir.mkdir(parents=True)
    os.symlink(out, adir / which)
    good = "20261007T210000-good0001.json" if which == "done" else "20261007T210000-bad00001.json"
    (adir / good).write_text(json.dumps({"v": 1, "kind": "note", "text": "ok"}) if which == "done" else "{nope")
    res = store.import_inbox(tid, "robert")
    assert list(out.iterdir()) == []                                                                                 # nothing landed outside
    assert (adir / good).exists() and any(which in r["file"] for r in res["rejected"])                                # left in place, and said so
    store.import_inbox(tid, "robert")                                                                                # again: still nothing outside, nothing duplicated
    assert list(out.iterdir()) == [] and len(store.journal(tid, "robert")) == (1 if which == "done" else 0)


def test_a_folder_swapped_for_a_link_while_importing_cannot_redirect_the_move(store, tmp_path, monkeypatch):
    tid = _topic(store)
    out = _outside(tmp_path)
    adir = Path(store.dir) / tid / "inbox" / "codex"
    adir.mkdir(parents=True)
    (adir / "20261007T210000-race0001.json").write_text(json.dumps({"v": 1, "kind": "note", "text": "raced"}))
    swapped = []
    real = T.TopicStore._count_lines

    def swap_then_count(self_, path):                                       # runs after the file was read, before it is moved
        if not swapped:
            swapped.append(1)
            os.rename(adir, str(adir) + ".moved")
            os.symlink(out, adir)
        return real(self_, path)
    monkeypatch.setattr(T.TopicStore, "_count_lines", swap_then_count)
    store.import_inbox(tid, "robert")
    assert swapped and sorted(p.name for p in out.iterdir()) == ["sample-123.json"]                                    # the link target saw nothing
    assert (Path(str(adir) + ".moved") / "done" / "20261007T210000-race0001.json").exists()                           # it moved within the folder we had checked


def test_a_named_pipe_in_the_inbox_is_rejected_without_blocking(store):
    tid = _topic(store)
    adir = Path(store.dir) / tid / "inbox" / "codex"
    adir.mkdir(parents=True)
    os.mkfifo(adir / "20261007T210000-pipe0001.json")
    res = store.import_inbox(tid, "robert")
    assert res["imported"] == 0 and "plain file" in res["rejected"][0]["reason"]


def test_payment_details_are_removed_on_every_import_path_even_without_a_caller_scrub(store):
    tid = _topic(store)
    _drop(store, tid, "codex", {"v": 1, "kind": "evidence", "text": f"Card {CARD} was pasted.", "model": f"m {CARD}", "session_ref": f"s {CARD}",
                                "refs": [{"type": "file", "ref": f"a {CARD}.md", "quote": f"q {CARD}"}]})
    assert store.import_inbox(tid, "robert")["imported"] == 1                                                         # no scrub argument: what the CLI does
    assert CARD not in _journal_text(store, tid) and "4444" not in _journal_text(store, tid)


def test_the_store_removes_payment_details_from_every_text_it_keeps(store):
    tid = store.create_topic("robert", f"Plan {CARD}", f"summary {CARD}")["id"]
    doc = store.add_item(tid, "robert", "document", f"Doc {CARD}", text=f"first paragraph\n\nsecond {CARD}", note=f"n {CARD}")
    store.add_comment(tid, doc["id"], "robert", f"c {CARD}", anchor={"type": "block", "index": 1, "quote": f"second {CARD}"})
    store.append_journal(tid, "robert", author={"kind": "owner", "name": f"Robert {CARD}"}, via="ui", kind="note", text="x", refs=[{"type": "file", "ref": "a", "quote": CARD}])
    dump = json.dumps([store.get_topic(tid, "robert"), store.get_item(tid, doc["id"], "robert"), store.journal(tid, "robert")], default=str)
    assert CARD not in dump and "5555 5555" not in dump


def test_nothing_with_a_card_number_goes_out_to_the_model_even_in_an_anchor_quote_or_title(store):
    from minimoi_portal.guild_ui.topic_context import build
    tid = _topic(store)
    doc = store.add_item(tid, "robert", "document", "Plain title", text="first\n\nsecond")
    store.add_comment(tid, doc["id"], "robert", "A question", anchor={"type": "block", "index": 1, "quote": "second"})
    path = Path(store.dir) / tid / "items" / doc["id"] / "comments.jsonl"                                            # simulate data kept before the store scrubbed
    path.write_text(path.read_text().replace('"quote": "second"', f'"quote": "second {CARD}"').replace('"quote":"second"', f'"quote":"second {CARD}"'))
    item = Path(store.dir) / tid / "items" / doc["id"] / "item.json"
    item.write_text(item.read_text().replace("Plain title", f"Title {CARD}"))
    block, report = build([{"topic_id": tid, "item_id": doc["id"], "rev": None}], store=store, principal="robert", scrub=scrub)
    assert CARD not in block and "5555 5555" not in block and CARD not in json.dumps(report)


def test_the_turn_budget_counts_text_comments_and_the_request_line_and_the_report_says_what_was_left_out(store):
    from minimoi_portal.guild_ui import topic_context as C
    tid = _topic(store)
    refs = []
    for n in range(5):
        d = store.add_item(tid, "robert", "document", f"Doc {n}", text=("word " * 5000).strip()[:20_000])
        for k in range(12):
            store.add_comment(tid, d["id"], "robert", (f"comment {n}-{k} " + "lorem ").ljust(480, "x"))
        refs.append({"topic_id": tid, "item_id": d["id"], "rev": None})
    block, report = C.build(refs, store=store, principal="robert", scrub=scrub)
    sent = sum(r["chars_sent"] + r["extra_chars_sent"] for r in report)
    assert sent <= C.TURN_CHARS                                                                                      # the chosen content obeys the one budget
    assert len(block) - sent <= C.framing_limit() and len(block) <= C.TURN_CHARS + C.framing_limit()                  # the rest is bounded framing, and counted as such
    assert all(r["status"] == "partly_read" and r["reason"] for r in report)                                          # nothing is cut silently
    assert any(r.get("comments_omitted") for r in report) or all(r["comments_sent"] == 12 for r in report)
    for r in report:
        assert r["comments_sent"] + r.get("comments_omitted", 0) == r["comments_total"] == 12


def test_a_single_populated_item_reports_comments_it_could_not_fit(store):
    from minimoi_portal.guild_ui import topic_context as C
    tid = _topic(store)
    d = store.add_item(tid, "robert", "document", "One", text="short")
    for k in range(12):
        store.add_comment(tid, d["id"], "robert", f"c{k} ".ljust(500, "y"))
    block, report = C.build([{"topic_id": tid, "item_id": d["id"], "rev": None}], store=store, principal="robert", scrub=scrub)
    r = report[0]
    assert r["comments_sent"] < 12 and r["comments_omitted"] == 12 - r["comments_sent"] and "left out" in r["reason"] and r["status"] == "partly_read"
    assert f"{r['comments_omitted']} of 12 open comments were left out" in block


def test_looking_at_a_topic_never_writes_and_linking_the_conversation_is_a_guarded_post(ws):
    portal, client, token, _ = ws
    from minimoi_portal.guild_ui.topics import topics_of
    from minimoi_portal.guild_ui.conversations import conversations_of
    services = portal.app.extensions["guild_ui_next"]["services"]
    store, convs = topics_of(services), conversations_of(services)
    tid = client.post(f"{API}/topics/create", json={"idempotency_key": "tc-nolink-0001", "title": "No link yet"}, headers=write_headers(token)).get_json()["topic"]["id"]
    topic_file = Path(store.dir) / tid / "topic.json"
    doc = json.loads(topic_file.read_text())
    doc.pop("conversation_id", None)                                                                                  # a topic whose link is missing
    topic_file.write_text(json.dumps(doc))
    before = (topic_file.read_bytes(), sorted(c["id"] for c in convs.list(doc["owner"])))
    for headers in ({}, {"X-Record-Mode": "off_record"}):                                                              # normal and Private
        got = client.get(f"{API}/topics/{tid}", headers=headers)
        assert got.status_code == 200 and got.get_json()["conversation"] is None and got.get_json()["conversation_missing"] is True
        assert (topic_file.read_bytes(), sorted(c["id"] for c in convs.list(doc["owner"]))) == before                  # nothing created, nothing renamed, nothing linked
    assert client.post(f"{API}/topics/{tid}/conversation", json={"idempotency_key": "lnk-key-0001"}, headers=write_headers(token, mode="off_record")).status_code == 409
    assert (topic_file.read_bytes(), sorted(c["id"] for c in convs.list(doc["owner"]))) == before                      # Private cannot link either
    assert client.post(f"{API}/topics/{tid}/conversation", json={"idempotency_key": "lnk-key-0002"}).status_code == 403   # CSRF
    ok = client.post(f"{API}/topics/{tid}/conversation", json={"idempotency_key": "lnk-key-0003"}, headers=write_headers(token))
    assert ok.status_code == 200 and ok.get_json()["topic"]["conversation_id"]
    again = client.post(f"{API}/topics/{tid}/conversation", json={"idempotency_key": "lnk-key-0004"}, headers=write_headers(token)).get_json()
    assert again["topic"]["conversation_id"] == ok.get_json()["topic"]["conversation_id"]                              # an existing link is kept
    assert client.get(f"{API}/topics/{tid}").get_json()["conversation_missing"] is False


# ── Codex's Revision 18 re-review: the budget covers every kind of content, and every cut is disclosed ──
def _img():
    from types import SimpleNamespace
    return SimpleNamespace(data=b"\x89PNG-sample", thumb=b"RIFFthumb", mime="image/png", ext="png", width=390, height=780, sha256="a" * 64)


def test_a_comment_cut_to_500_characters_is_reported_as_cut_with_its_lengths(store):
    from minimoi_portal.guild_ui import topic_context as C
    tid = _topic(store)
    d = store.add_item(tid, "robert", "document", "Tiny", text="x")
    store.add_comment(tid, d["id"], "robert", "L" * 3_900)
    block, report = C.build([{"topic_id": tid, "item_id": d["id"], "rev": None}], store=store, principal="robert", scrub=scrub)
    r = report[0]
    assert r["status"] == "partly_read" and r["comments_sent"] == 1 and r["comments_total"] == 1
    assert r["comments_cut"] == [{"sent": 500, "of": 3_900}] and "cut to 500 of 3,900 characters" in r["reason"]
    assert "[…cut: 500 of 3,900 characters]" in block and "L" * 501 not in block                         # the model is told too


def test_requests_and_design_descriptions_spend_the_same_budget_and_every_cut_is_disclosed(store):
    from minimoi_portal.guild_ui import topic_context as C
    tid = _topic(store)
    refs = []
    for n in range(2):
        refs.append(store.add_item(tid, "robert", "document", f"Big {n}", text="ab " * 9000)["id"])
    for n in range(3):
        refs.append(store.add_item(tid, "robert", "request", f"Request {n}", text="Please review the attached revision.", request={"to": "Codex", "included": "revision A"})["id"])
    refs.append(store.add_item(tid, "robert", "design", "Mock", image=_img())["id"])
    block, report = C.build([{"topic_id": tid, "item_id": i, "rev": None} for i in refs], store=store, principal="robert", scrub=scrub)
    sent = sum(r["chars_sent"] + r["extra_chars_sent"] for r in report)
    assert sent <= C.TURN_CHARS and len(block) <= C.TURN_CHARS + C.framing_limit()
    assert [r["chars_sent"] for r in report[:2]] == [20_000, 20_000] and sent == C.TURN_CHARS                  # the two documents used it all
    assert all(r["chars_sent"] == 0 and r["extra_chars_sent"] == 0 for r in report[2:])                        # nothing else crept in after it
    late = report[2:]
    assert all(r["status"] == "partly_read" and r["reason"] for r in late)                               # everything after the budget ran out says so
    assert all(r.get("request_omitted") for r in late[:3]) and "request details were left out" in late[0]["reason"]
    assert "only the first 0 of" in late[3]["reason"]                                                          # the design description is cut and says so


def test_the_budget_holds_for_many_mixed_selections(store):
    import random
    from minimoi_portal.guild_ui import topic_context as C
    rnd = random.Random(7)
    tid = _topic(store)
    pool = []
    for n in range(8):
        kind = ["document", "note", "request", "design"][n % 4]
        kw = {"text": "w " * rnd.randint(1, 9000)} if kind in ("document", "note", "request") else {"image": _img()}
        if kind == "note":
            kw["text"] = kw["text"][:7_900]
        if kind == "request":
            kw["text"] = kw["text"][:7_900]; kw["request"] = {"to": "Codex", "included": "x"}
        it = store.add_item(tid, "robert", kind, f"Item {n}", **kw)
        for k in range(rnd.randint(0, 12)):
            store.add_comment(tid, it["id"], "robert", ("c" * rnd.randint(1, 3_900)))
        pool.append(it["id"])
    for _ in range(40):
        pick = rnd.sample(pool, rnd.randint(1, 5))
        block, report = C.build([{"topic_id": tid, "item_id": i, "rev": None} for i in pick], store=store, principal="robert", scrub=scrub)
        sent = sum(r["chars_sent"] + r["extra_chars_sent"] for r in report)
        assert sent <= C.TURN_CHARS and len(block) <= C.TURN_CHARS + C.framing_limit()
        for r in report:
            cut_somewhere = r["chars_sent"] < r["chars_total"] or r.get("comments_omitted") or r.get("comments_cut") or r.get("request_omitted")
            assert (r["status"] == "partly_read") == bool(cut_somewhere) and (bool(r.get("reason")) == bool(cut_somewhere))
