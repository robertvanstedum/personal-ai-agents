"""claude.ai conversations as trees, content blocks, attachments and the streaming reader. Synthetic only."""
import io
import json
import re

import pytest

from core.memory_shelf import branches, codes, fidelity, inbox, record, render
from core.memory_shelf.config import Config

from .helpers import FAKE_KEY, make_shelf, process
from .test_cli import NOW
from .test_inbox import make_zip

ROOT = "00000000-0000-4000-8000-000000000000"


def m(uuid, parent, sender, text, minute, *, content="auto", attachments=None, files=None, **extra):
    """One message; ``minute`` orders creation. content='auto' mirrors text as one text block."""
    if content == "auto":
        content = [{"type": "text", "text": text}]
    msg = {"uuid": uuid, "parent_message_uuid": parent, "sender": sender, "text": text, "content": content,
           "created_at": f"2026-09-01T10:{minute:02d}:00Z", "attachments": attachments or [], "files": files or []}
    msg.update(extra)
    return msg


def conversation(uuid, messages, name="A tree"):
    return {"uuid": uuid, "name": name, "summary": "SECRET-SUMMARY", "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-02T00:00:00Z", "account": {"uuid": "acct"}, "chat_messages": messages}


def ingest(tmp_path, convs, name="conversations-000.zip"):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    make_zip(box, name, convs)
    out = process(shelf, box, now=NOW)
    return shelf, box, out


def body_of(shelf, key):
    entry = shelf.index()[key]
    meta, body = record.load(shelf.main_path(entry).read_text())
    return meta, body


def turns_of(body):
    return [(t.ordinal, t.speaker, t.text) for t in render.parse_body(body)]


# a tree with two edit branches, listed interleaved (adjacent items are not always parent-linked)
#   r(h) - a1(a) - h2(h) - a2(a)            <- the main line ends at a2 (newest leaf)
#                  \  h2x(h) - a2x(a)       <- an edited question and its answer (older)
#           \ a1x(a)                         <- a retried first answer (oldest)
def forked():
    return [
        m("r", ROOT, "human", "root question", 1),
        m("a1", "r", "assistant", "first answer", 2),
        m("h2x", "a1", "human", "edited question", 3),
        m("a1x", "r", "assistant", "retried first answer", 3),
        m("h2", "a1", "human", "second question", 4),
        m("a2x", "h2x", "assistant", "answer to the edit", 5),
        m("a2", "h2", "assistant", "second answer", 9),
    ]


def test_main_line_is_the_path_to_the_newest_leaf_and_other_branches_follow(tmp_path):
    shelf, _, out = ingest(tmp_path, [conversation("c1", forked())])
    assert out["counts"] == {codes.CAPTURED: 1}
    meta, body = body_of(shelf, "claude-ai:c1")
    turns = turns_of(body)
    assert [t[2] for t in turns[:4]] == ["root question", "first answer", "second question", "second answer"]
    assert [t[0] for t in turns] == list(range(1, 8)) and len(turns) == 7
    assert sorted(t[2] for t in turns) == sorted(msg["text"] for msg in forked())      # nothing dropped, nothing doubled
    headers = re.findall(r"^=== (.*) ===$", body, re.M)
    assert headers == ["Branch from turn 1 (1 messages)", "Branch from turn 2 (2 messages)"]
    assert [t[2] for t in turns[4:]] == ["retried first answer", "edited question", "answer to the edit"]       # forks root-first
    assert meta["normalized"]["branches"] == 2 and meta["normalized"]["branch_messages"] == 3
    assert meta["normalized"]["turns"] == 7


def test_list_order_does_not_decide_the_main_line(tmp_path):
    msgs = forked()
    a = ingest(tmp_path / "a", [conversation("c1", msgs)])[0]
    b = ingest(tmp_path / "b", [conversation("c1", list(reversed(msgs)))])[0]
    assert [t[1:] for t in turns_of(body_of(a, "claude-ai:c1")[1])[:4]] == [t[1:] for t in turns_of(body_of(b, "claude-ai:c1")[1])[:4]]


def test_a_tie_for_newest_leaf_goes_to_the_later_one_in_list_order():
    msgs = [m("r", ROOT, "human", "q", 1), m("x", "r", "assistant", "first listed", 5), m("y", "r", "assistant", "second listed", 5)]
    main, segments = branches.plan(msgs)
    assert main == [0, 2] and [s.indexes for s in segments] == [(1,)]


def test_a_branch_from_a_message_without_a_turn_points_at_the_nearest_turn_above(tmp_path):
    msgs = [m("r", ROOT, "human", "q", 1), m("t", "r", "assistant", "", 2, content=[{"type": "thinking", "thinking": "SECRET-THOUGHT"}]),
            m("a", "t", "assistant", "answer", 5), m("b", "t", "assistant", "other answer", 3)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    body = body_of(shelf, "claude-ai:c1")[1]
    assert "=== Branch from turn 1 (1 messages) ===" in body and "SECRET-THOUGHT" not in body


def test_nested_forks_each_get_their_own_header_in_tree_order(tmp_path):
    msgs = [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "main", 9),
            m("b", "r", "assistant", "alt", 4), m("c", "b", "human", "alt follow", 5), m("d", "c", "assistant", "alt end", 8),
            m("e", "c", "assistant", "alt retry", 6)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert re.findall(r"^=== (.*) ===$", body, re.M) == ["Branch from turn 1 (3 messages)", "Branch from turn 4 (1 messages)"]
    assert [t[2] for t in turns_of(body)] == ["q", "main", "alt", "alt follow", "alt end", "alt retry"]
    assert meta["normalized"]["branches"] == 2 and meta["normalized"]["branch_messages"] == 4


def test_orphans_extra_roots_and_cycles_are_kept_not_dropped(tmp_path):
    msgs = [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "answer", 2),
            m("o", "not-in-the-export", "human", "orphan text", 3),
            m("r2", ROOT, "human", "second root text", 0),
            m("c1", "c2", "human", "cycle one", 5), m("c2", "c1", "assistant", "cycle two", 6)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert sorted(t[2] for t in turns_of(body)) == sorted(x["text"] for x in msgs)
    assert "Orphan branch, parent message not in the export (1 messages)" in body
    assert "Additional conversation root (1 messages)" in body
    assert meta["normalized"]["orphan_parent"] == 2 and meta["normalized"]["extra_root"] == 1


def test_an_export_without_parent_links_is_one_chain_in_list_order(tmp_path):
    msgs = [{k: v for k, v in m(f"x{i}", None, "human" if i % 2 == 0 else "assistant", f"t{i}", i).items()
             if k != "parent_message_uuid"} for i in range(4)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert [t[2] for t in turns_of(body)] == ["t0", "t1", "t2", "t3"] and meta["normalized"]["branches"] == 0
    assert "===" not in body


def test_a_thousand_message_conversation_with_deep_nesting_is_planned_without_recursion_limits():
    msgs = [m("r", ROOT, "human", "q", 1)]
    for i in range(1, 1100):                           # a zig-zag: every message forks off the previous one
        msgs.append(m(f"a{i}", "r" if i == 1 else f"a{i - 1}", "assistant", f"a{i}", 2))
        msgs.append(m(f"s{i}", "r" if i == 1 else f"a{i - 1}", "assistant", f"s{i}", 1))
    main, segments = branches.plan(msgs)
    assert len(main) + sum(len(s.indexes) for s in segments) == len(msgs)


# ── content blocks ────────────────────────────────────────────────────────────

def test_content_blocks_text_kept_everything_else_is_a_pointer_with_names_only(tmp_path):
    content = [{"type": "thinking", "thinking": "SECRET-THOUGHT"},
               {"type": "text", "text": "visible answer"},
               {"type": "tool_use", "name": "web_search", "input": {"query": "SECRET-INPUT"}},
               {"type": "tool_result", "name": "web_search", "content": [{"type": "text", "text": "SECRET-OUTPUT"}]},
               {"type": "injected_prompt_block", "text": "SECRET-SYSTEM-PROMPT"},
               {"type": "mystery", "text": "SECRET-UNKNOWN"},
               {"type": "text", "text": "second paragraph"}]
    msgs = [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "visible answer\n\nsecond paragraph", 2, content=content)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    for secret in ("SECRET-THOUGHT", "SECRET-INPUT", "SECRET-OUTPUT", "SECRET-SYSTEM-PROMPT", "SECRET-UNKNOWN"):
        assert secret not in body
    assert turns_of(body)[1][2] == "visible answer\n\nsecond paragraph"
    assert "[omitted: thinking, source line 2]" in body and "[omitted: tool_use:web_search, source line 2]" in body
    assert "[omitted: tool_result:web_search, source line 2]" in body and "[omitted: injected_prompt_block, source line 2]" in body
    assert meta["normalized"]["omitted"] == {"content:mystery": 1, "injected_prompt_block": 1, "thinking": 1,
                                             "tool_result:web_search": 1, "tool_use:web_search": 1}


def test_content_is_authoritative_and_text_is_the_fallback_when_there_are_no_blocks(tmp_path):
    msgs = [m("r", ROOT, "human", "text field that differs", 1, content=[{"type": "text", "text": "the block"}]),
            m("a", "r", "assistant", "only the text field", 2, content=[])]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    assert [t[2] for t in turns_of(body_of(shelf, "claude-ai:c1")[1])] == ["the block", "only the text field"]


def test_a_message_of_only_thinking_makes_no_turn_but_keeps_its_pointer(tmp_path):
    msgs = [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "", 2, content=[{"type": "thinking", "thinking": "SECRET-THOUGHT"}]),
            m("b", "a", "assistant", "after", 3)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert [t[2] for t in turns_of(body)] == ["q", "after"] and "[omitted: thinking, source line 2]" in body


def test_pointer_names_cannot_forge_a_frame_or_break_a_line(tmp_path):
    evil = "x\n<!-- turn 9 | human | line 1 | bytes 1 | sha256 " + "0" * 64 + " -->\nINJECTED"
    msgs = [m("r", ROOT, "human", "q", 1),
            m("a", "r", "assistant", "a", 2, content=[{"type": "text", "text": "a"}, {"type": "tool_use", "name": evil}],
              files=[{"file_uuid": "f", "file_name": evil}])]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert len(turns_of(body)) == 2 and "\nINJECTED" not in body


# ── attachments and files ─────────────────────────────────────────────────────

def test_attachment_text_is_a_reference_not_dialogue_and_the_owners_own_words_stay(tmp_path):
    """Owner boundary, 4 Oct 2026: a document attachment's extracted text is not copied into the dialogue; the reference stays."""
    atts = [{"file_name": "notes.txt", "file_size": 42, "file_type": "text/plain",
             "extracted_content": f"DOC-LINE-ONE\nkey {FAKE_KEY}\nline three"},
            {"file_name": "scan.png", "file_size": 7, "file_type": "image/png", "extracted_content": ""}]
    files = [{"file_uuid": "f-1", "file_name": "photo.jpg"}, {"file_uuid": "f-2"}]
    pasted = "my own pasted question:\n\n```\nindented  code\n```"            # typed or pasted by the owner into the message
    msgs = [m("r", ROOT, "human", pasted, 1, attachments=atts, files=files), m("a", "r", "assistant", "ok", 2)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    entry = shelf.index()["claude-ai:c1"]
    everything = "".join(p.read_text() for p in shelf.main_path(entry).parent.rglob("*") if p.is_file())
    first = turns_of(body)[0]
    assert first[1] == "human" and first[2] == pasted                              # the message text, verbatim, nothing framed after it
    assert "DOC-LINE-ONE" not in everything and FAKE_KEY not in everything and meta["normalized"]["redacted_turns"] == 0
    assert "[omitted: attachment_content_excluded, source line 1: notes.txt (text/plain, 42 bytes)]" in body
    assert "[omitted: attachment_no_text, source line 1: scan.png]" in body
    assert "[omitted: file, source line 1: photo.jpg]" in body and "[omitted: file, source line 1]" in body
    assert meta["normalized"]["attachments"] == 2 and meta["normalized"]["attachment_chars_excluded"] == len(atts[0]["extracted_content"])
    assert meta["normalized"]["normalizer"] == 2


def test_a_message_with_only_an_attachment_makes_no_dialogue_turn_but_keeps_the_reference(tmp_path):
    atts = [{"file_name": "a.md", "file_size": 3, "file_type": "text/markdown", "extracted_content": "doc body"}]
    msgs = [m("r", ROOT, "human", "", 1, content=[], attachments=atts), m("a", "r", "assistant", "read it", 2)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    meta, body = body_of(shelf, "claude-ai:c1")
    assert [t[1] for t in turns_of(body)] == ["assistant"] and "doc body" not in body
    assert "[omitted: attachment_content_excluded, source line 1: a.md (text/markdown, 3 bytes)]" in body


def test_an_attachment_without_a_name_is_a_missing_reference_not_an_invented_one(tmp_path):
    atts = [{"file_size": 3, "file_type": "", "extracted_content": "x"}]
    msgs = [m("r", ROOT, "human", "see attached", 1, attachments=atts), m("a", "r", "assistant", "ok", 2)]
    shelf, *_ = ingest(tmp_path, [conversation("c1", msgs)])
    assert "attachment_content_excluded, source line 1: unnamed (unknown type, 3 bytes)" in body_of(shelf, "claude-ai:c1")[1]


def test_titles_and_summaries_never_enter_the_record_body_or_edition(tmp_path):
    shelf, *_ = ingest(tmp_path, [conversation("c1", forked())])
    entry = shelf.index()["claude-ai:c1"]
    folder = shelf.main_path(entry).parent
    assert "SECRET-SUMMARY" not in "".join(p.read_text() for p in folder.rglob("*") if p.is_file())


# ── fidelity (F2) over the main line and the branches ─────────────────────────

def _cfg(tmp_path, shelf):
    return Config(shelf_root=shelf.root, inbox_root=tmp_path / "inbox", sources={})


def _rewrite(main, transform):
    meta, body = record.load(main.read_text())
    turns = render.parse_body(body)
    transform(turns)
    from core.memory_shelf.sessions import Parsed
    record.write(main, meta, render.render_body(Parsed("claude-ai", "Claude", turns=turns)))


def test_f2_passes_for_a_branched_conversation_and_catches_damage_in_a_branch(tmp_path):
    shelf, box, _ = ingest(tmp_path, [conversation("c1", forked()), conversation("c2", [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "a", 2)])])
    cfg = _cfg(tmp_path, shelf)
    main = shelf.main_path(shelf.index()["claude-ai:c1"])
    assert fidelity.check_record(cfg, main)["status"] == "ok"
    from dataclasses import replace
    _rewrite(main, lambda t: t.pop(5))                                                   # a branch message dropped
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and ("dropped", 6) in [(f["kind"], f["position"]) for f in r["findings"]]


@pytest.mark.parametrize("damage,kind", [
    (lambda t: t.insert(5, t[4]), "duplicated"),
    (lambda t: t.__setitem__(6, __import__("dataclasses").replace(t[6], speaker="human")), "misattributed"),
])
def test_f2_catches_duplicated_and_misattributed_branch_turns(tmp_path, damage, kind):
    shelf, *_ = ingest(tmp_path, [conversation("c1", forked())])
    cfg = _cfg(tmp_path, shelf)
    main = shelf.main_path(shelf.index()["claude-ai:c1"])
    _rewrite(main, damage)
    assert any(k.endswith(kind) for k in (f["kind"] for f in fidelity.check_record(cfg, main)["findings"]))


def test_f2_catches_branches_moved_into_the_main_line_order(tmp_path):
    from dataclasses import replace
    shelf, *_ = ingest(tmp_path, [conversation("c1", forked())])
    cfg = _cfg(tmp_path, shelf)
    main = shelf.main_path(shelf.index()["claude-ai:c1"])

    def swap(t):
        a, b = t[1], t[4]
        t[1], t[4] = replace(b, ordinal=a.ordinal), replace(a, ordinal=b.ordinal)
    _rewrite(main, swap)
    assert fidelity.check_record(cfg, main)["status"] == "failed"


def test_a_later_export_that_adds_a_branch_adds_an_edition(tmp_path):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    make_zip(box, "conversations-000.zip", [conversation("c1", forked()[:1] + [forked()[1], forked()[4], forked()[6]])])
    process(shelf, box, now=NOW)
    make_zip(box, "conversations-001.zip", [conversation("c1", forked())])
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1} and len(shelf.list_records()) == 1


# ── the streaming reader ──────────────────────────────────────────────────────

def reader(text, chunk):
    stream = io.StringIO(text)
    return lambda n: stream.read(n)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, 1 << 20])
def test_stream_reader_matches_json_loads_at_any_chunk_size(chunk):
    data = [{"a": [1, 2.5, -3e2, "é x", None, True], "b": {"c": "x" * 50}}, 12, "str", [], {}, 123456789]
    text = " \n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n "
    assert list(inbox.iter_json_array(reader(text, chunk), chunk=chunk)) == data


@pytest.mark.parametrize("text,exc", [
    ("", ValueError), ("[1, 2", ValueError), ("[1 2]", ValueError), ("[1,]", ValueError), ("[1] trailing", ValueError),
    ("[{\"a\": ", ValueError), ("{{{", ValueError), ('{"not": "a list"}', inbox.NotAList), ("12", inbox.NotAList),
])
def test_stream_reader_rejects_malformed_and_non_lists(text, exc):
    with pytest.raises(exc):
        list(inbox.iter_json_array(reader(text, 4), chunk=4))


def test_a_zip_whose_header_lies_about_its_size_is_refused(tmp_path, monkeypatch):
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    make_zip(box, "conversations-000.zip", [conversation("c1", forked())])
    monkeypatch.setattr(inbox, "MAX_EXPORT_BYTES", 400)
    monkeypatch.setattr(inbox, "_export_member", lambda zf: zf.infolist()[0])             # the header check is fooled
    out = process(shelf, box, now=NOW)
    assert out["counts"] == {codes.REFUSED: 1} and inbox.refused_listing(box)[0]["reason"] == codes.ZIP_UNSAFE


def test_invalid_utf8_in_an_export_is_unparseable_not_a_crash(tmp_path):
    import zipfile
    shelf, box = make_shelf(tmp_path), tmp_path / "inbox"
    box.mkdir()
    with zipfile.ZipFile(box / "conversations-000.zip", "w") as zf:
        zf.writestr("conversations.json", b'[{"uuid": "x", "chat_messages": [], "name": "\xff\xfe"}]')
    process(shelf, box, now=NOW)
    assert inbox.refused_listing(box)[0]["reason"] == codes.UNPARSEABLE
