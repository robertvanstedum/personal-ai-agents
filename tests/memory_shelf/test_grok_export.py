"""The Grok (xAI) account export: layout, speakers, tree, content, identity, dry run, list. Synthetic only."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from core.memory_shelf import cli, codes, fidelity, inbox, ledger, record, render
from core.memory_shelf.config import Config

from .helpers import make_shelf, process
from .test_cli import NOW, World
from .test_claude_ai_tree import turns_of

UUID = "11111111-2222-3333-4444-555555555555"
BASE = f"ttl/30d/export_data/{UUID}/"


def gr(rid, parent, sender, message, minute, **extra):
    r = {"_id": rid, "conversation_id": "c", "parent_response_id": parent, "children": [], "create_time": f"2026-09-01T10:{minute:02d}:00Z",
         "sender": sender, "message": message, "query": "", "thinking_trace": "", "steps": [], "web_search_results": [],
         "file_attachments": [], "generated_image_urls": [], "error": None, "partial": False}
    r.update(extra)
    return r


def gconv(cid, responses, title="SECRET-TITLE", leaf=None, created="2026-09-01T10:00:00Z", **head):
    h = {"id": cid, "create_time": created, "modify_time": "2026-09-09T00:00:00Z", "title": title, "summary": "SECRET-SUMMARY",
         "leaf_response_id": leaf, "temporary": False, "starred": False, "asset_ids": [], "team_id": "t", "kind": "chat"}
    h.update(head)
    return {"conversation": h, "responses": [{"response": r, "share_link": None} for r in responses]}


def grok_zip(box: Path, convs, name=f"{UUID}.zip", projects=(), tasks=(), media=(), assets=3, backend=None, extra_members=None):
    box.mkdir(parents=True, exist_ok=True)
    doc = backend if backend is not None else {"conversations": list(convs), "projects": list(projects), "tasks": list(tasks),
                                               "media_posts": list(media)}
    with zipfile.ZipFile(box / name, "w") as zf:
        zf.writestr(BASE + "prod-grok-backend.json", json.dumps(doc))
        zf.writestr(BASE + "prod-mc-auth-mgmt-api.json", json.dumps({"email": "SECRET-EMAIL@example.invalid", "sessions": ["SECRET-SESSION"]}))
        zf.writestr(BASE + "prod-mc-billing.json", json.dumps({"card": "SECRET-BILLING"}))
        for i in range(assets):
            zf.writestr(BASE + f"prod-mc-asset-server/asset-{i}/content", b"\x89PNG SECRET-ASSET")
        for member, data in (extra_members or {}).items():
            zf.writestr(member, data)
    return box / name


def simple(cid="g-1", title="SECRET-TITLE"):
    return gconv(cid, [gr("r1", None, "human", "SECRET-BODY question", 1), gr("r2", "r1", "ASSISTANT", "answer one", 2),
                       gr("r3", "r2", "human", "follow up", 3), gr("r4", "r3", "grok-3", "answer two", 4)], title=title)


def ingest(tmp_path, convs, **kw):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    grok_zip(box, convs, **kw)
    out = process(shelf, box, now=NOW)
    return shelf, box, out


def body_of(shelf, key):
    meta, body = record.load(shelf.main_path(shelf.index()[key]).read_text())
    return meta, body


# ── speakers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sender,speaker", [("human", "human"), ("HUMAN", "human"), (" Human ", "human"), ("assistant", "assistant"),
                                            ("ASSISTANT", "assistant"), ("model", "assistant"), ("Model", "assistant"),
                                            ("grok-3", "assistant"), ("grok-latest", "assistant"), ("GROK-4-fast", "assistant"),
                                            ("grok", None), ("grok-", None), ("system", None), ("tool", None), ("user", None), ("", None), (None, None), (3, None)])
def test_speaker_comes_from_the_closed_set_only(sender, speaker):
    assert inbox.grok_speaker(sender) == speaker


def test_speakers_chair_source_key_and_designation_marker(tmp_path):
    convs = [gconv("g-1", [gr("r1", None, "human", "file this conversation please", 1), gr("r2", "r1", "assistant", "ok", 2)])]
    shelf, box, out = ingest(tmp_path, convs)
    assert out["counts"] == {codes.CAPTURED: 1, codes.EXCLUDED: 1, codes.HELD: 1}      # + the account files, + the assets, each counted once
    meta, body = body_of(shelf, "grok:g-1")
    assert meta["chair"] == "Grok" and meta["source"] == "grok:g-1" and meta["normalized"]["identity"] == "source"
    assert [t[1] for t in turns_of(body)] == ["human", "assistant"]
    assert [e["kind"] for e in meta["events"]] == ["designated-curated"]                # an owner-turn marker designates; nothing more


def test_an_assistant_saying_file_this_does_not_designate(tmp_path):
    shelf, *_ = ingest(tmp_path, [gconv("g-1", [gr("r1", None, "human", "hello", 1), gr("r2", "r1", "assistant", "file this", 2)])])
    assert body_of(shelf, "grok:g-1")[0]["events"] == []


def test_an_unknown_sender_refuses_that_conversation_only_and_says_so(tmp_path):
    bad = gconv("g-bad", [gr("r1", None, "human", "q", 1), gr("r2", "r1", "tool", "SECRET-TOOL-OUTPUT", 2)])
    shelf, box, out = ingest(tmp_path, [simple("g-1"), bad])
    assert out["counts"][codes.CAPTURED] == 1 and out["counts"][codes.REFUSED] == 1
    assert list(shelf.index()) == ["grok:g-1"]
    row = ledger.report(shelf)["sources"]["inbox"]
    assert row["refused"] == {"unknown_sender": 1} and row["missing"] == {}
    assert "SECRET-TOOL-OUTPUT" not in "".join(p.read_text(errors="replace") for p in shelf.root.rglob("*") if p.is_file())


# ── tree ──────────────────────────────────────────────────────────────────────

def forked(leaf=None, cid="g-1"):
    return gconv(cid, [
        gr("r1", None, "human", "root question", 1),
        gr("r2", "r1", "assistant", "first answer", 2),
        gr("r3", "r1", "assistant", "retried answer", 6),
        gr("r4", "r2", "human", "second question", 3),
        gr("r5", "r4", "assistant", "second answer", 4),
    ], leaf=leaf)


def test_main_line_follows_the_named_leaf_when_it_resolves(tmp_path):
    shelf, *_ = ingest(tmp_path, [forked(leaf="r5")])
    meta, body = body_of(shelf, "grok:g-1")
    assert [t[2] for t in turns_of(body)] == ["root question", "first answer", "second question", "second answer", "retried answer"]
    assert "=== Branch from turn 1 (1 messages) ===" in body and meta["normalized"]["branches"] == 1


def test_main_line_is_the_newest_leaf_when_the_leaf_does_not_resolve(tmp_path):
    for leaf in (None, "not-a-response", ""):
        shelf, *_ = ingest(tmp_path / (leaf or "none"), [forked(leaf=leaf)])
        assert [t[2] for t in turns_of(body_of(shelf, "grok:g-1")[1])][:2] == ["root question", "retried answer"]


def test_a_named_leaf_in_the_middle_still_keeps_what_comes_after_it(tmp_path):
    shelf, *_ = ingest(tmp_path, [forked(leaf="r4")])
    turns = [t[2] for t in turns_of(body_of(shelf, "grok:g-1")[1])]
    assert turns[:3] == ["root question", "first answer", "second question"] and sorted(turns) == sorted(
        ["root question", "first answer", "retried answer", "second question", "second answer"])


def test_orphan_responses_are_kept(tmp_path):
    c = gconv("g-1", [gr("r1", None, "human", "q", 1), gr("r2", "r1", "assistant", "a", 2), gr("r9", "gone", "human", "orphan words", 3)])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "grok:g-1")
    assert "orphan words" in body and meta["normalized"]["orphan_parent"] == 1


def test_conversations_without_parent_links_are_one_chain(tmp_path):
    rs = [gr(f"r{i}", None, "human" if i % 2 == 0 else "assistant", f"t{i}", i) for i in range(4)]
    for r in rs:
        del r["parent_response_id"]
    shelf, *_ = ingest(tmp_path, [gconv("g-1", rs)])
    assert [t[2] for t in turns_of(body_of(shelf, "grok:g-1")[1])] == ["t0", "t1", "t2", "t3"]


def test_empty_conversations_are_skipped_with_a_reason_and_counted(tmp_path):
    blank = gconv("g-blank", [gr("r1", None, "human", "  ", 1), gr("r2", "r1", "assistant", None, 2)])
    shelf, box, out = ingest(tmp_path, [simple("g-1"), gconv("g-none", []), blank])
    assert list(shelf.index()) == ["grok:g-1"] and out["counts"][codes.CAPTURED] == 1 and out["counts"][codes.EXCLUDED] == 3
    assert ledger.report(shelf)["sources"]["inbox"]["excluded"]["empty"] == 2


# ── content ───────────────────────────────────────────────────────────────────

def test_message_is_verbatim_and_everything_else_is_a_pointer_without_content(tmp_path):
    card = '{"c": "SECRET-CARD"}'
    rich = gr("r2", "r1", "assistant", "Answer with `code`\n\n- and a list\n", 2,
              query="SECRET-MODEL-QUERY", thinking_trace="SECRET-THOUGHT", agent_thinking_traces=["SECRET-AGENT-1", "SECRET-AGENT-2"],
              steps=[{"s": "SECRET-STEP"}] * 3, web_search_results=[{"url": "https://SECRET-URL.example"}] * 4,
              cited_web_search_results=[{"url": "https://SECRET-CITED.example"}], card_attachments_json=card,
              file_attachments=["asset-aaa", "asset-bbb"], generated_image_urls=["https://SECRET-IMG.example/1.png", "https://SECRET-IMG.example/2.png"],
              error="SECRET-ERROR", partial=True)
    c = gconv("g-1", [gr("r1", None, "human", "question", 1, file_attachments=["asset-up"]), rich])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "grok:g-1")
    assert turns_of(body)[1][2] == "Answer with `code`\n\n- and a list\n"
    for secret in ("SECRET-MODEL-QUERY", "SECRET-THOUGHT", "SECRET-AGENT", "SECRET-STEP", "SECRET-URL", "SECRET-CITED", "SECRET-CARD",
                   "SECRET-IMG", "SECRET-ERROR"):
        assert secret not in body
    for pointer in ("[omitted: thinking_trace, source line 2]", "[omitted: agent_thinking_traces, source line 2: 2 items]",
                    "[omitted: steps, source line 2: 3 items]", "[omitted: web_search_results, source line 2: 4 results]",
                    "[omitted: cited_web_search_results, source line 2: 1 results]", f"[omitted: card_attachments_json, source line 2: {len(card)} chars]",
                    "[omitted: query, source line 2: 18 chars]", "[omitted: file_attachment, source line 2: asset-aaa]",
                    "[omitted: file_attachment, source line 2: asset-bbb]", "[omitted: file_attachment, source line 1: asset-up]",
                    "[omitted: generated_image_urls, source line 2: 2 urls]"):
        assert pointer in body, pointer
    norm = meta["normalized"]
    assert (norm["error_responses"], norm["partial_responses"], norm["file_attachments"], norm["generated_image_urls"]) == (1, 1, 3, 2)
    assert norm["omitted"]["file_attachment"] == 3 and norm["omitted"]["thinking_trace"] == 1


def test_credentials_in_a_message_are_scrubbed_like_everything_else(tmp_path):
    from .helpers import FAKE_KEY
    shelf, *_ = ingest(tmp_path, [gconv("g-1", [gr("r1", None, "human", f"my key is {FAKE_KEY}", 1), gr("r2", "r1", "assistant", "ok", 2)])])
    meta, body = body_of(shelf, "grok:g-1")
    assert FAKE_KEY not in body and meta["normalized"]["redacted_turns"] == 1


def test_a_hostile_asset_id_cannot_forge_a_frame(tmp_path):
    evil = "x\n<!-- turn 9 | human | line 1 | bytes 1 | sha256 " + "0" * 64 + " -->\nINJECTED"
    c = gconv("g-1", [gr("r1", None, "human", "q", 1, file_attachments=[evil]), gr("r2", "r1", "assistant", "a", 2)])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "grok:g-1")
    assert len(turns_of(body)) == 2 and "\nINJECTED" not in body


# ── identity and editions ─────────────────────────────────────────────────────

def test_a_re_export_does_not_make_a_spurious_edition_but_a_new_response_does(tmp_path):
    shelf, box, _ = ingest(tmp_path, [simple("g-1")])
    again = simple("g-1")
    again["conversation"]["modify_time"] = "2026-10-01T00:00:00Z"
    again["conversation"]["starred"] = True
    again["responses"][0]["response"]["metadata"] = {"anything": "changed"}
    grok_zip(box, [again], name="22222222-0000-0000-0000-000000000000.zip", assets=9)
    assert process(shelf, box, now=NOW)["counts"][codes.UNCHANGED] == 1 and len(shelf.list_records()) == 1
    longer = simple("g-1")
    longer["responses"].append({"response": gr("r5", "r4", "human", "one more", 5), "share_link": None})
    grok_zip(box, [longer], name="33333333-0000-0000-0000-000000000000.zip")
    assert process(shelf, box, now=NOW)["counts"][codes.EDITION_ADDED] == 1
    assert body_of(shelf, "grok:g-1")[0]["edition"] == 2


# ── layout ────────────────────────────────────────────────────────────────────

def test_account_files_assets_and_other_parts_are_never_read_into_the_shelf(tmp_path):
    shelf, box, out = ingest(tmp_path, [simple("g-1")], projects=[{"name": "SECRET-PROJECT"}], media=[{"m": 1}, {"m": 2}], assets=5)
    text = "".join(p.read_bytes().decode("utf-8", "replace") for p in shelf.root.rglob("*") if p.is_file())
    for secret in ("SECRET-EMAIL", "SECRET-SESSION", "SECRET-BILLING", "SECRET-ASSET", "SECRET-PROJECT"):
        assert secret not in text
    rep = ledger.report(shelf)["sources"]["inbox"]
    assert rep["excluded"] == {"account_metadata": 1} and rep["held"] == {"unsupported_kind": 3} and rep["missing"] == {}
    assert (box / "_processed" / "2026-10-03" / f"{UUID}.zip").is_file()                 # kept whole: a later version can import the rest


def test_the_dry_run_counts_everything_and_names_no_title(tmp_path):
    w = World(tmp_path)
    c = forked()
    c["conversation"]["title"] = "SECRET-TITLE"
    empty = gconv("g-none", [])
    bad = gconv("g-bad", [gr("r1", None, "human", "q", 1), gr("r2", "r1", "robot", "x", 2)], created="2026-10-05T00:00:00Z")
    grok_zip(w.inbox, [c, empty, bad, simple("g-3")], projects=[{"p": 1}], media=[{"m": 1}, {"m": 2}], assets=4)
    code, text = w.run("dry-run", "inbox")
    listing = json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())
    blob = json.dumps(listing) + text
    for secret in ("SECRET-TITLE", "SECRET-SUMMARY", "SECRET-BODY", "SECRET-EMAIL", "SECRET-BILLING", "root question"):
        assert secret not in blob
    row = listing["files"][0]
    assert (row["kind"], row["decision"]) == ("grok-export", "would_import")
    assert row["export"] == {"conversations": 4, "messages": 5 + 0 + 2 + 4, "created_first": "2026-09-01", "created_last": "2026-10-05",
                             "conversations_with_branches": 1, "empty_conversations": 1, "unknown_sender_conversations": 1,
                             "file_attachments": 0, "held": {"assets": 4, "projects": 1, "media_posts": 2},
                             "excluded": {"account_metadata": 2}}
    assert listing["counts"]["conversations"] == 4


def test_cli_end_to_end_then_list_grok_and_fidelity(tmp_path):
    w = World(tmp_path)
    grok_zip(w.inbox, [simple("g-1")], name=f"{UUID}.zip")
    w.run("dry-run", "inbox")
    assert w.run("inbox")[0] == 0 and w.shelf.list_records() == []
    assert w.run("approve-source", "inbox")[0] == 0
    code, text = w.run("inbox")
    assert '"captured": 1' in text
    code, text = w.run("list", "grok")
    rows = [l.split("\t") for l in text.splitlines() if not l.startswith("#")]
    assert code == 0 and len(rows) == 1 and rows[0][1] == "secret-title" and rows[0][3] == "4"
    assert "SECRET-BODY" not in text and w.run("list", "claude-ai")[1].count("\n") == 2
    assert w.run("fidelity", "--sample", "5")[0] == 0


def test_f2_catches_damage_to_a_grok_record(tmp_path):
    shelf, box, _ = ingest(tmp_path, [forked(leaf="r5")])
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={})
    main = shelf.main_path(shelf.index()["grok:g-1"])
    assert fidelity.check_record(cfg, main)["status"] == "ok"
    meta, body = record.load(main.read_text())
    turns = render.parse_body(body)
    turns.pop(4)                                                   # drop the branch turn
    from core.memory_shelf.sessions import Parsed
    record.write(main, meta, render.render_body(Parsed("grok", "Grok", turns=turns)))
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and ("dropped", 5) in [(f["kind"], f["position"]) for f in r["findings"]]


# ── refusals ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("backend", [
    ["not", "a", "dict"], {"projects": []}, {"conversations": {"not": "a list"}},
    {"conversations": [{"conversation": {"id": "x"}}]}, {"conversations": [{"conversation": {}, "responses": []}]},
    {"conversations": [{"conversation": {"id": "x"}, "responses": [{"response": {"sender": "human"}}]}]},
    {"conversations": ["a string"]},
])
def test_an_unknown_grok_shape_is_refused_whole_and_visibly(tmp_path, backend):
    shelf, box, out = ingest(tmp_path, [], backend=backend)
    assert out["counts"] == {codes.REFUSED: 1} and shelf.list_records() == []
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA and (box / "_refused" / f"{UUID}.zip").is_file()


def test_an_empty_conversation_list_is_refused_as_no_turns(tmp_path):
    shelf, box, out = ingest(tmp_path, [])
    assert out["counts"] == {codes.REFUSED: 1} and inbox.refused_listing(box)[0]["reason"] == codes.NO_TURNS


def test_a_zip_with_neither_layout_is_unknown_schema(tmp_path):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    box.mkdir()
    with zipfile.ZipFile(box / f"{UUID}.zip", "w") as zf:
        zf.writestr("something/else.json", "[]")
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA


def test_corrupt_grok_json_is_unparseable(tmp_path):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    box.mkdir()
    with zipfile.ZipFile(box / f"{UUID}.zip", "w") as zf:
        zf.writestr(BASE + "prod-grok-backend.json", '{"conversations": [{"conversation": ')
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNPARSEABLE


def test_the_gate_applies_to_grok_zips(tmp_path):
    w = World(tmp_path)
    grok_zip(w.inbox, [simple("g-1")])
    assert w.run("inbox")[1].startswith("inbox\tdry_run_only") and w.shelf.list_records() == []
    assert (w.inbox / f"{UUID}.zip").exists()


# ── the object reader ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("chunk", [1, 3, 17, 1 << 20])
def test_object_reader_streams_the_array_and_returns_the_other_keys(chunk):
    doc = {"projects": [1, 2], "conversations": [{"a": "é"}, {"b": [1, 2, 3]}, 5], "tasks": [], "media_posts": [{"x": 1}]}
    text = json.dumps(doc, indent=2)
    stream = io.StringIO(text)
    events = list(inbox.iter_json_object(stream.read, "conversations", chunk=chunk))
    assert [e for e in events if e[0] == "item"] == [("item", {"a": "é"}), ("item", {"b": [1, 2, 3]}), ("item", 5)]
    assert {e[1]: e[2] for e in events if e[0] == "value"} == {"projects": [1, 2], "tasks": [], "media_posts": [{"x": 1}]}
    assert ("start", "conversations") in events


@pytest.mark.parametrize("text,exc", [("", ValueError), ('{"a": 1', ValueError), ('{"a" 1}', ValueError), ("{} x", ValueError),
                                      ('{"a": 1,}', ValueError), ("[1]", inbox.NotAList)])
def test_object_reader_rejects_malformed_input(text, exc):
    with pytest.raises(exc):
        list(inbox.iter_json_object(io.StringIO(text).read, "conversations", chunk=3))
