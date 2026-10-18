"""The ChatGPT (OpenAI) data export: detection, tree, speakers, content, identity, layout, dry run, refusals. Synthetic only."""
import json
import zipfile
from pathlib import Path

import pytest

from core.memory_shelf import codes, fidelity, inbox, ledger, record
from core.memory_shelf.config import Config

from .helpers import FAKE_KEY, make_shelf, process
from .test_cli import NOW, World
from .test_claude_ai_tree import turns_of
from .test_grok_export import grok_zip, simple as grok_simple
from .test_inbox import conv as claude_conv, make_zip as claude_zip

T0 = 1756720800.0                     # 2025-09-01T10:00:00Z
ZIP = "0f92aaaa-2026-10-04-05-49-28-c9cc.zip"


def node(nid, parent, role, parts=None, minute=0, ctype="text", hidden=False, attachments=None, author_name=None,
         content_extra=None, **msg):
    """One mapping node; ``role=None`` is the export's empty root."""
    if role is None:
        return nid, {"id": nid, "message": None, "parent": parent, "children": []}
    meta = {"model_slug": "SECRET-MODEL-ROUTE", "serialization_metadata": {"x": 1}}
    if hidden:
        meta["is_visually_hidden_from_conversation"] = True
    if attachments is not None:
        meta["attachments"] = attachments
    content = {"content_type": ctype}
    if parts is not None:
        content["parts"] = parts
    content.update(content_extra or {})
    m = {"id": nid, "author": {"role": role, "name": author_name, "metadata": {}}, "create_time": T0 + 60 * minute,
         "update_time": None, "content": content, "status": "finished_successfully", "end_turn": True, "weight": 1.0,
         "metadata": meta, "recipient": "all", "channel": None}
    m.update(msg)
    return nid, {"id": nid, "message": m, "parent": parent, "children": []}


def cconv(cid, nodes, title="SECRET-TITLE", current="last", created=T0, updated=T0 + 3600, **extra):
    mapping = dict(nodes)
    for nid, n in mapping.items():
        if n["parent"] in mapping:
            mapping[n["parent"]]["children"].append(nid)
    c = {"title": title, "create_time": created, "update_time": updated, "mapping": mapping,
         "moderation_results": [], "current_node": list(mapping)[-1] if current == "last" else current,
         "plugin_ids": None, "conversation_id": cid, "conversation_template_id": None, "gizmo_id": None,
         "is_archived": False, "safe_urls": [], "default_model_slug": "gpt-test", "id": cid, "voice": None}
    if current is None:
        del c["current_node"]
    c.update(extra)
    return c


def simple(cid="c-1", title="SECRET-TITLE", extra_turns=0):
    nodes = [node("root", None, None),
             node("sys", "root", "system", [""], 0, hidden=True),
             node("u1", "sys", "user", ["SECRET-BODY question"], 1),
             node("a1", "u1", "assistant", ["answer one"], 2)]
    for i in range(extra_turns):
        nodes.append(node(f"x{i}", nodes[-1][0], "user" if i % 2 == 0 else "assistant", [f"more {i}"], 3 + i))
    return cconv(cid, nodes, title=title)


def chatgpt_zip(box: Path, convs, name=ZIP, split=None, assets=2, extra_members=None, conversations_text=None):
    """A synthetic copy of the export layout. ``split`` = list of lists for conversations-NNN.json files instead."""
    box.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(box / name, "w") as zf:
        if split is None:
            zf.writestr("conversations.json", conversations_text if conversations_text is not None else json.dumps(list(convs)))
        else:
            for i, chunk in enumerate(split):
                zf.writestr(f"conversations-{i:03d}.json", json.dumps(chunk))
        zf.writestr("chat.html", "<html>SECRET-HTML-COPY</html>")
        zf.writestr("user.json", json.dumps({"email": "SECRET-EMAIL@example.invalid", "phone_number": "SECRET-PHONE"}))
        zf.writestr("export_manifest.json", json.dumps({"files": [{"url": "https://SECRET-LINK.example"}]}))
        zf.writestr("message_feedback.json", json.dumps([{"text": "SECRET-FEEDBACK"}]))
        for i in range(assets):
            zf.writestr(f"file-{i:04d}abc-photo.dat", b"\x89PNG SECRET-ASSET")
        for member, data in (extra_members or {}).items():
            zf.writestr(member, data)
    return box / name


def ingest(tmp_path, convs, **kw):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    chatgpt_zip(box, convs, **kw)
    return shelf, box, process(shelf, box, now=NOW)


def body_of(shelf, key):
    return record.load(shelf.main_path(shelf.index()[key]).read_text())


def shelf_text(shelf):
    return "".join(p.read_bytes().decode("utf-8", "replace") for p in shelf.root.rglob("*") if p.is_file())


# ── detection ─────────────────────────────────────────────────────────────────

def test_a_chatgpt_zip_is_its_own_kind_and_claude_ai_and_grok_detection_is_unchanged(tmp_path):
    box = tmp_path / "box"
    assert inbox.classify(ZIP, chatgpt_zip(box, [simple()])) == ("chatgpt-export", None)
    split = chatgpt_zip(box, [], name="split.zip", split=[[simple("c-1")], [simple("c-2")]])
    assert inbox.classify("split.zip", split) == ("chatgpt-export", None)
    claude = claude_zip(box, "claude.zip", [claude_conv("u-1", "x", [("human", "a"), ("assistant", "b")])])
    assert inbox.classify("claude.zip", claude) == ("claude-ai-export", "conversations")
    assert inbox.classify("empty.zip", claude_zip(box, "empty.zip", [])) == ("claude-ai-export", "conversations")
    grok = grok_zip(box / "g", [grok_simple("g-1")])
    assert inbox.classify(grok.name, grok) == ("grok-export", None)
    assert inbox.classify("conversations-000.zip", None) == ("claude-ai-export", "conversations")
    assert inbox.classify("conversations.json", None) == ("claude-ai-export", "conversations")
    bare = box / "bare" / "conversations.json"
    bare.parent.mkdir()
    bare.write_text(json.dumps([simple()]))
    assert inbox.classify("conversations.json", bare) == ("chatgpt-export", None)
    bare.write_text(json.dumps([claude_conv("u-1", "x", [("human", "a"), ("assistant", "b")])]))
    assert inbox.classify("conversations.json", bare) == ("claude-ai-export", "conversations")
    # a claude.ai conversation never becomes ChatGPT, even with a stray "mapping" key
    assert not inbox.looks_like_chatgpt({"uuid": "u", "chat_messages": [], "mapping": {}})
    (box / "notzip.zip").write_bytes(b"not a zip")
    assert inbox.classify("notzip.zip", box / "notzip.zip") == ("claude-ai-export", "conversations")


def test_a_traversal_member_is_never_read_as_the_export(tmp_path):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    box.mkdir()
    with zipfile.ZipFile(box / "slip.zip", "w") as zf:
        zf.writestr("../conversations.json", json.dumps([simple()]))
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA and shelf.list_records() == []


# ── tree ──────────────────────────────────────────────────────────────────────

def forked(current="a2", cid="c-1"):
    return cconv(cid, [
        node("root", None, None),
        node("u1", "root", "user", ["root question"], 1),
        node("a1", "u1", "assistant", ["first answer"], 2),
        node("a1x", "u1", "assistant", ["retried answer"], 10),          # the newest message, but not the visible branch
        node("u2", "a1", "user", ["second question"], 3),
        node("a2", "u2", "assistant", ["second answer"], 4),
    ], current=current)


def test_main_line_is_the_visible_branch_from_current_node_and_the_other_branch_is_kept(tmp_path):
    shelf, box, out = ingest(tmp_path, [forked()])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert [t[2] for t in turns_of(body)] == ["root question", "first answer", "second question", "second answer", "retried answer"]
    assert "=== Branch from turn 1 (1 messages) ===" in body and meta["normalized"]["branches"] == 1
    assert [t[1] for t in turns_of(body)] == ["human", "assistant", "human", "assistant", "assistant"]


def test_without_a_current_node_the_newest_message_decides_the_main_line(tmp_path):
    for current in (None, "not-a-node"):
        shelf, *_ = ingest(tmp_path / str(current), [forked(current=current)])
        assert [t[2] for t in turns_of(body_of(shelf, "chatgpt:c-1")[1])][:2] == ["root question", "retried answer"]


def test_the_null_message_root_is_structure_not_a_turn_or_an_omission(tmp_path):
    shelf, *_ = ingest(tmp_path, [forked()])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert meta["normalized"]["structural_nodes"] == 1 and meta["normalized"]["omitted"] == {}
    assert len(turns_of(body)) == 5


def test_orphan_nodes_are_kept(tmp_path):
    c = cconv("c-1", [node("root", None, None), node("u1", "root", "user", ["q"], 1), node("a1", "u1", "assistant", ["a"], 2),
                      node("o1", "gone", "user", ["orphan words"], 3)], current="a1")
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert "orphan words" in body and meta["normalized"]["orphan_parent"] == 1


# ── speakers and what is not dialogue ─────────────────────────────────────────

def test_speakers_chair_source_key_dates_and_designation(tmp_path):
    c = cconv("c-1", [node("root", None, None), node("u1", "root", "user", ["file this conversation please"], 1),
                      node("a1", "u1", "assistant", ["ok"], 2)], created=1756720800.25, updated=1759000000.5)
    shelf, box, out = ingest(tmp_path, [c])
    assert out["counts"] == {codes.CAPTURED: 1, codes.EXCLUDED: 2, codes.HELD: 2}   # account + manifest; assets + other members
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert meta["chair"] == "ChatGPT" and meta["source"] == "chatgpt:c-1" and meta["normalized"]["identity"] == "source"
    assert meta["created"].startswith("2025-09-01T10:00:00") and meta["normalized"]["source_updated"] == "2025-09-27T19:06:40Z"
    assert meta["retained"]["member"] == "c-1" and meta["retained"]["format"] == "chatgpt"
    assert [e["kind"] for e in meta["events"]] == ["designated-curated"]


def test_system_tool_hidden_and_empty_messages_are_counted_pointers_never_turns(tmp_path):
    c = cconv("c-1", [
        node("root", None, None),
        node("sys", "root", "system", ["SECRET-SYSTEM-PROMPT"], 0),
        node("ctx", "sys", "user", None, 0, ctype="user_editable_context", hidden=True,
             content_extra={"user_profile": "SECRET-PROFILE"}),
        node("u1", "ctx", "user", ["question"], 1),
        node("call", "u1", "assistant", None, 2, ctype="code", recipient="python", content_extra={"text": "SECRET-CODE"}),
        node("out", "call", "tool", None, 3, ctype="execution_output", author_name="python",
             content_extra={"text": "SECRET-TOOL-OUTPUT"}),
        node("blank", "out", "assistant", ["", "  "], 4),
        node("think", "blank", "assistant", None, 5, ctype="thoughts", content_extra={"thoughts": [{"content": "SECRET-THOUGHT"}]}),
        node("a1", "think", "assistant", ["answer"], 6),
    ])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert [(t[1], t[2]) for t in turns_of(body)] == [("human", "question"), ("assistant", "answer")]
    assert meta["normalized"]["omitted"] == {"injected:system": 1, "injected:hidden": 1, "tool_use:python": 1,
                                             "tool_result:python": 1, "empty:message": 1, "thinking": 1}
    for secret in ("SECRET-SYSTEM-PROMPT", "SECRET-PROFILE", "SECRET-CODE", "SECRET-TOOL-OUTPUT", "SECRET-THOUGHT", "SECRET-MODEL-ROUTE"):
        assert secret not in body


def test_an_unknown_role_refuses_that_conversation_only_and_says_so(tmp_path):
    bad = cconv("c-bad", [node("root", None, None), node("u1", "root", "user", ["q"], 1),
                          node("x", "u1", "critic", ["SECRET-CRITIC"], 2)])
    shelf, box, out = ingest(tmp_path, [simple("c-1"), bad])
    assert out["counts"][codes.CAPTURED] == 1 and out["counts"][codes.REFUSED] == 1
    assert list(shelf.index()) == ["chatgpt:c-1"]
    assert ledger.report(shelf)["sources"]["inbox"]["refused"] == {"unknown_sender": 1}
    assert "SECRET-CRITIC" not in shelf_text(shelf)


# ── content ───────────────────────────────────────────────────────────────────

def test_multi_part_text_is_kept_verbatim_in_order(tmp_path):
    c = cconv("c-1", [node("root", None, None), node("u1", "root", "user", ["part one", "part two\n- with a list\n"], 1),
                      node("a1", "u1", "assistant", ["Answer with `code`"], 2)])
    shelf, *_ = ingest(tmp_path, [c])
    assert turns_of(body_of(shelf, "chatgpt:c-1")[1])[0][2] == "part one\n\npart two\n- with a list\n"


def test_non_text_parts_and_attachments_are_pointers_and_voice_transcripts_are_dialogue(tmp_path):
    image = {"content_type": "image_asset_pointer", "asset_pointer": "file-service://file-img1", "size_bytes": 1234,
             "width": 10, "height": 10, "metadata": {"dalle": {"prompt": "SECRET-DALLE"}}}
    audio = {"content_type": "audio_asset_pointer", "asset_pointer": "sediment://file_aud1", "size_bytes": 99, "format": "wav",
             "expiry_datetime": "2026-01-01", "metadata": {"transcription": "SECRET-DUPLICATE-TRANSCRIPT"}}
    said = {"content_type": "audio_transcription", "text": "spoken words", "direction": "in", "decoding_id": None}
    att = [{"id": "file-att1", "name": "notes.pdf", "size": 2048, "mime_type": "application/pdf", "is_big_paste": False}]
    c = cconv("c-1", [
        node("root", None, None),
        node("u1", "root", "user", [image, "what is this?"], 1, ctype="multimodal_text", attachments=att),
        node("a1", "u1", "assistant", ["a picture"], 2),
        node("u2", "a1", "user", [said, audio], 3, ctype="multimodal_text"),
        node("a2", "u2", "assistant", [{"content_type": "mystery_part"}, "reply"], 4, ctype="multimodal_text"),
    ])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert [t[2] for t in turns_of(body)] == ["what is this?", "a picture", "spoken words", "reply"]
    for pointer in ("[omitted: image, source line 2: file-service://file-img1 (1234 bytes)]",
                    "[omitted: attachment_no_text, source line 2: notes.pdf (application/pdf, 2048 bytes)]",
                    "[omitted: file_attachment, source line 4: audio_asset_pointer: sediment://file_aud1 (99 bytes)]",
                    "[omitted: content:mystery_part, source line 5]"):
        assert pointer in body, pointer
    norm = meta["normalized"]
    assert (norm["images"], norm["attachments"], norm["audio_video"], norm["audio_transcriptions"]) == (1, 1, 1, 1)
    assert "SECRET-DALLE" not in body and "SECRET-DUPLICATE-TRANSCRIPT" not in body and "SECRET-ASSET" not in shelf_text(shelf)


def test_credentials_in_a_message_are_scrubbed_like_everything_else(tmp_path):
    c = cconv("c-1", [node("root", None, None), node("u1", "root", "user", [f"my key is {FAKE_KEY}"], 1),
                      node("a1", "u1", "assistant", ["ok"], 2)])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "chatgpt:c-1")
    assert FAKE_KEY not in body and meta["normalized"]["redacted_turns"] == 1


# ── identity and editions ─────────────────────────────────────────────────────

def test_a_re_export_adds_an_edition_for_the_changed_conversation_and_never_a_duplicate(tmp_path):
    shelf, box, out = ingest(tmp_path, [simple("c-1"), simple("c-2")])
    assert out["counts"][codes.CAPTURED] == 2
    same = simple("c-1")
    same["update_time"] = T0 + 99999                                    # export-run metadata alone is not a change
    same["is_archived"] = True
    same["mapping"]["u1"]["message"]["metadata"]["serialization_metadata"] = {"changed": True}
    longer = simple("c-2", extra_turns=2)
    chatgpt_zip(box, [same, longer], name="second-export.zip", assets=5)
    out = process(shelf, box, now=NOW)
    assert out["counts"][codes.UNCHANGED] == 1 and out["counts"][codes.EDITION_ADDED] == 1
    assert sorted(shelf.index()) == ["chatgpt:c-1", "chatgpt:c-2"] and len(shelf.list_records()) == 2
    meta, body = body_of(shelf, "chatgpt:c-2")
    assert meta["edition"] == 2 and "more 1" in body and meta["events"][-1]["kind"] == "edition-added"
    assert body_of(shelf, "chatgpt:c-1")[0]["edition"] == 1


def test_an_asset_link_expiring_differently_in_a_later_export_is_not_a_change():
    def with_audio(expiry):
        part = {"content_type": "audio_asset_pointer", "asset_pointer": "sediment://file_a", "size_bytes": 5, "expiry_datetime": expiry}
        return cconv("c-1", [node("root", None, None), node("u1", "root", "user", [part, "hi"], 1, ctype="multimodal_text"),
                             node("a1", "u1", "assistant", ["yo"], 2)])
    a = inbox.parse_chatgpt_conversation(inbox.check_chatgpt_conversation(with_audio("2026-01-01")))
    b = inbox.parse_chatgpt_conversation(inbox.check_chatgpt_conversation(with_audio("2027-05-05")))
    assert a.source_sha256 == b.source_sha256 and a.key == "chatgpt:c-1"


def test_split_conversation_files_are_all_read_in_order(tmp_path):
    shelf, box, out = ingest(tmp_path, [], split=[[simple("c-1")], [simple("c-2"), simple("c-3")]])
    assert out["counts"][codes.CAPTURED] == 3 and sorted(shelf.index()) == ["chatgpt:c-1", "chatgpt:c-2", "chatgpt:c-3"]


def test_the_older_conversation_id_field_alone_is_enough():
    c = simple("c-1")
    del c["id"]
    item = inbox.parse_chatgpt_conversation(inbox.check_chatgpt_conversation(c))
    assert item.key == "chatgpt:c-1" and item.member == "c-1"


# ── empty conversations ───────────────────────────────────────────────────────

def test_empty_conversations_are_skipped_with_a_reason_and_counted(tmp_path):
    only_system = cconv("c-sys", [node("root", None, None), node("sys", "root", "system", ["x"], 0, hidden=True)])
    blank = cconv("c-blank", [node("root", None, None), node("u1", "root", "user", ["  "], 1), node("a1", "u1", "assistant", [""], 2)])
    no_nodes = cconv("c-none", [], current=None)
    shelf, box, out = ingest(tmp_path, [simple("c-1"), only_system, blank, no_nodes])
    assert list(shelf.index()) == ["chatgpt:c-1"] and out["counts"][codes.CAPTURED] == 1
    assert ledger.report(shelf)["sources"]["inbox"]["excluded"]["empty"] == 3


# ── layout ────────────────────────────────────────────────────────────────────

def test_account_files_manifest_assets_and_other_members_are_never_read_into_the_shelf(tmp_path):
    shelf, box, out = ingest(tmp_path, [simple("c-1")], assets=4, extra_members={"dalle-generations/img.webp": b"SECRET-DALLE-IMG",
                                                                                   "user_settings.json": '{"s": "SECRET-SETTINGS"}'})
    text = shelf_text(shelf)
    for secret in ("SECRET-EMAIL", "SECRET-PHONE", "SECRET-LINK", "SECRET-FEEDBACK", "SECRET-HTML-COPY", "SECRET-ASSET",
                   "SECRET-DALLE-IMG", "SECRET-SETTINGS"):
        assert secret not in text
    rep = ledger.report(shelf)["sources"]["inbox"]
    assert rep["excluded"] == {"account_metadata": 1, "export_manifest": 1} and rep["held"] == {"unsupported_kind": 2}
    assert rep["missing"] == {}
    assert inbox.chatgpt_layout(box / "_processed" / "2026-10-03" / ZIP) == {"assets": 5, "account_files": 2, "manifests": 1,
                                                                              "other_members": 2}


def test_the_dry_run_counts_everything_and_names_no_title(tmp_path):
    w = World(tmp_path)
    empty = cconv("c-none", [], current=None)
    bad = cconv("c-bad", [node("root", None, None), node("x", "root", "critic", ["x"], 1)], created=T0 + 40 * 86400)
    chatgpt_zip(w.inbox, [forked(), empty, bad, simple("c-3")], assets=3)
    code, text = w.run("dry-run", "inbox")
    listing = json.loads((w.shelf.status_dir / "dry-runs" / "inbox.json").read_text())
    blob = json.dumps(listing) + text
    for secret in ("SECRET-TITLE", "SECRET-BODY", "SECRET-EMAIL", "SECRET-LINK", "root question", "gpt-test"):
        assert secret not in blob
    row = listing["files"][0]
    assert (row["kind"], row["decision"]) == ("chatgpt-export", "would_import")
    assert row["export"] == {"conversations": 4, "messages": 5 + 0 + 1 + 3, "turns_kept": 5 + 2, "items_omitted": 1,
                             "created_first": "2025-09-01", "created_last": "2025-10-11", "conversations_with_branches": 1,
                             "empty_conversations": 1, "unknown_sender_conversations": 1, "attachments": 0,
                             "held": {"assets": 3, "other_members": 2},
                             "excluded": {"account_metadata": 1, "export_manifest": 1}}
    assert listing["counts"]["conversations"] == 4


def test_cli_end_to_end_gate_then_list_and_fidelity(tmp_path):
    w = World(tmp_path)
    chatgpt_zip(w.inbox, [forked()])
    w.run("dry-run", "inbox")
    assert w.run("inbox")[0] == 0 and w.shelf.list_records() == []          # the gate: nothing without an approval
    assert w.run("approve-source", "inbox")[0] == 0
    code, text = w.run("inbox")
    assert '"captured": 1' in text
    code, text = w.run("list", "chatgpt")
    rows = [l.split("\t") for l in text.splitlines() if not l.startswith("#")]
    assert code == 0 and len(rows) == 1 and rows[0][1] == "secret-title" and rows[0][3] == "5"
    cfg = Config(shelf_root=w.shelf.root, inbox_root=w.inbox, sources={})
    assert fidelity.check_record(cfg, w.shelf.main_path(w.shelf.index()["chatgpt:c-1"]))["status"] == "ok"
    assert w.run("fidelity", "--sample", "5")[0] == 0


# ── refusals and limits ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mapping", [
    ["not", "a", "dict"],
    {"n1": "not a node"},
    {"n1": {"parent": 5, "message": None}},
    {"n1": {"parent": None, "message": "a string"}},
    {"n1": {"parent": None, "message": {"content": {"content_type": "text", "parts": ["x"]}}}},
    {"n1": {"parent": None, "message": {"author": {"role": "user"}, "content": {"parts": ["x"]}}}},
    {"n1": {"parent": None, "message": {"author": {"role": "user"}, "content": {"content_type": "text", "parts": "x"}}}},
])
def test_a_malformed_mapping_refuses_the_whole_file_visibly(tmp_path, mapping):
    broken = {"id": "c-bad", "title": "t", "create_time": T0, "mapping": mapping}
    shelf, box, out = ingest(tmp_path, [simple("c-1"), broken])
    assert out["counts"] == {codes.REFUSED: 1} and shelf.list_records() == [] and shelf.pending() == []
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA and (box / "_refused" / ZIP).is_file()


def test_a_conversation_without_an_id_refuses_the_whole_file(tmp_path):
    c = simple("c-1")
    del c["id"], c["conversation_id"]
    shelf, box, out = ingest(tmp_path, [simple("c-0"), c])
    assert out["counts"] == {codes.REFUSED: 1} and inbox.refused_listing(box)[0]["reason"] == codes.UNKNOWN_SCHEMA


def test_corrupt_conversations_json_is_unparseable(tmp_path):
    text = json.dumps([simple("c-1"), simple("c-2")])[:-40]
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    path = chatgpt_zip(box, [], conversations_text=text)
    assert inbox.classify(ZIP, path) == ("chatgpt-export", None)           # the first conversation is whole
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNPARSEABLE and shelf.list_records() == []


def test_oversized_exports_are_refused_including_the_split_total(tmp_path, monkeypatch):
    one = len(json.dumps([simple("c-1")]))
    monkeypatch.setattr(inbox, "MAX_EXPORT_BYTES", one + 10)                 # each split file fits, their total does not
    shelf, box, out = ingest(tmp_path, [], split=[[simple("c-1")], [simple("c-2")]])
    assert out["counts"] == {codes.REFUSED: 1} and inbox.refused_listing(box)[0]["reason"] == codes.TOO_LARGE
    monkeypatch.setattr(inbox, "MAX_EXPORT_BYTES", 50)
    shelf2, box2, _ = ingest(tmp_path / "b", [simple("c-1")])
    assert inbox.refused_listing(box2)[0]["reason"] == codes.TOO_LARGE


def test_a_zip_header_that_understates_the_size_is_stopped_by_the_reader(tmp_path):
    path = chatgpt_zip(tmp_path / "box", [simple("c-1"), simple("c-2")])

    class Tiny(inbox._Capped):
        def __init__(self, handle, limit):
            super().__init__(handle, 64)                                      # as if the header had lied
    with pytest.raises(inbox.Refusal) as exc:
        list(inbox.stream_chatgpt(path, capped=Tiny))
    assert exc.value.code == codes.ZIP_UNSAFE


def test_the_gate_applies_to_chatgpt_zips(tmp_path):
    w = World(tmp_path)
    chatgpt_zip(w.inbox, [simple("c-1")])
    assert w.run("inbox")[1].startswith("inbox\tdry_run_only") and w.shelf.list_records() == []
    assert (w.inbox / ZIP).exists()


# ── documents the assistant authored through a tool (review finding, 17 Oct) ──

def test_canvas_documents_edits_and_saved_memories_are_kept_as_assistant_turns(tmp_path):
    import json as _json
    c = cconv("c-1", [
        node("root", None, None),
        node("u1", "root", "user", ["write me a plan"], 1),
        node("doc", "u1", "assistant", [_json.dumps({"name": "Plan", "type": "document", "content": "PLAN-BODY text"})], 2,
             recipient="canmore.create_textdoc"),
        node("edit", "doc", "assistant", [_json.dumps({"updates": [{"pattern": ".*", "replacement": "PLAN-EDIT text"}]})], 3,
             recipient="canmore.update_textdoc"),
        node("mem", "edit", "assistant", ["Prefers short answers"], 4, recipient="bio"),
        node("call", "mem", "assistant", None, 5, ctype="code", recipient="python", content_extra={"text": "SECRET-CODE"}),
        node("a1", "call", "assistant", ["done"], 6),
    ])
    shelf, *_ = ingest(tmp_path, [c])
    meta, body = body_of(shelf, "chatgpt:c-1")
    texts = [t[2] for t in turns_of(body)]
    assert texts[0] == "write me a plan" and texts[-1] == "done"
    assert any("[Canvas document: Plan]" in t and "PLAN-BODY text" in t for t in texts)
    assert any("[Canvas edit]" in t and "PLAN-EDIT text" in t for t in texts)
    assert any("[Saved memory]" in t and "Prefers short answers" in t for t in texts)
    assert meta["normalized"]["omitted"].get("tool_use:python") == 1 and "SECRET-CODE" not in body
