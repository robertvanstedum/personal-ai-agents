"""A record body must read back exactly as written, whatever the turns contain (CR, CRLF, trailing blanks).

Regression for the 4 Oct write_failed run: ``Path.read_text`` turned a CR or CRLF inside a turn into LF, so 35 real
Codex records no longer matched their own frames and the re-mediation compare raised. Synthetic text only."""
import json
from pathlib import Path

import pytest

from core.memory_shelf import codes, editions, record, render, sessions, shelf as shelf_mod, watchers
from core.memory_shelf.config import SourceCfg

from .helpers import make_shelf, write_tree
from .test_codex_mediation import NOW, UUID_A, OWNER, approvals, copy, item, jl, meta, started

TEXTS = ["line one\r\nline two", "old mac\rbreak", "ends with blanks  ", "ends with newlines\n\n", "ends crlf\r\n"]


def record_text(last: str):
    p = sessions.Parsed("codex", "Codex")
    p.add("human", "first\r\nturn", 1, "x")
    p.add("assistant", last, 2, "x")
    m, body, _ = render.to_shelf(p, "0" * 64, 1, created="2026-10-04T00:00:00Z")
    return m, body, record.dump(m, body)


@pytest.mark.parametrize("last", TEXTS)
def test_a_record_reads_back_with_every_turn_byte_for_byte(last, tmp_path):
    m, body, text = record_text(last)
    path = tmp_path / "r.md"
    record.write(path, m, body)
    _, back = record.load(record.read(path))
    got = render.parse_body(back)
    assert [t.text for t in got] == ["first\r\nturn", last]


def test_read_text_would_have_broken_it(tmp_path):
    """The mutation: the translating reader. It must fail the same check the fix passes."""
    m, body, _ = record_text("x")
    path = tmp_path / "r.md"
    record.write(path, m, body)
    _, back = record.load(path.read_text("utf-8"))
    with pytest.raises(record.InvalidRecord):
        render.parse_body(back)


def test_appending_an_event_keeps_every_turn_untouched(tmp_path):
    from core.memory_shelf import events as ev
    m, body, _ = record_text("last\r\n\r\n")
    path = tmp_path / "r.md"
    record.write(path, m, body)
    record.append_event(path, ev.make_event("designated-curated", "robert", via="transcript-marker"))
    _, back = record.load(record.read(path))
    assert [t.text for t in render.parse_body(back)] == ["first\r\nturn", "last\r\n\r\n"]


def cr_rows():
    return [meta(UUID_A), started(), item("UserMessage", "a\r\nb\rc"), copy("user", "from a copy\r\nwith cr"),
            item("AgentMessage", "done  \n\n")]


def capture_old_then_remediate(tmp_path, monkeypatch):
    root = tmp_path / "codex"
    write_tree(root, {f"2026/10/04/rollout-2026-10-04T00-00-00-{UUID_A}.jsonl": "\n".join(jl(*cr_rows())) + "\n"})
    shelf, cfg = make_shelf(tmp_path), SourceCfg("codex", "codex", root, ())
    watchers.run_source(shelf, cfg, now=NOW)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    return shelf, cfg


def old_reader(lines):
    out = sessions.Parsed("codex", "Codex", normalizer=sessions.NORMALIZER_VERSION["codex"])
    for number, raw in enumerate(lines, 1):
        row = json.loads(raw)
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            out.source_id = payload.get("id")
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            it = payload["item"]
            user = it["type"] == "UserMessage"
            out.add("human" if user else "assistant", it["content"][0]["text"], number,
                    "item:UserMessage" if user else "item:AgentMessage:?")
    return out


def test_remediating_a_record_with_cr_and_trailing_blanks_is_an_edition_not_a_failed_write(tmp_path, monkeypatch):
    shelf, cfg = capture_old_then_remediate(tmp_path, monkeypatch)
    monkeypatch.setitem(watchers.PARSERS, "codex", old_reader)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"][codes.CAPTURED] == 1
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1}, out
    entry = shelf.list_records()[0]
    _, body = record.load(record.read(shelf.main_path(entry)))
    assert [t.text for t in render.parse_body(body)][0] == "a\r\nb\rc"
    assert not (Path(shelf.ledger_dir) / "index.dirty").exists()


def test_a_body_that_does_not_read_back_falls_back_to_a_new_edition_never_a_failed_ingest(tmp_path, monkeypatch):
    """A record written by an older build whose body no longer parses (the 4 Oct shape, read the old way)."""
    shelf, cfg = capture_old_then_remediate(tmp_path, monkeypatch)
    monkeypatch.setitem(watchers.PARSERS, "codex", old_reader)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    watchers.run_source(shelf, cfg, now=NOW)
    path = shelf.main_path(shelf.list_records()[0])
    m, body = record.load(record.read(path))
    record.write(path, m, body.replace("a\r\nb", "a\nb"))                 # damage one turn so its frame no longer matches
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1}
    assert len(editions.list_editions(path.parent)) == 2
    _, fixed = record.load(record.read(path))
    assert [t.text for t in render.parse_body(fixed)][0] == "a\r\nb\rc"      # the current body is whole again


def test_same_turns_helper_never_raises_on_a_bad_body():
    good = render.render_body(sessions.parse_codex(jl(meta(), started(), item("UserMessage", "q"))))
    assert shelf_mod._same_turns(good, good) is True
    assert shelf_mod._same_turns("<!-- turn 1 | human | line 1 | bytes 9 | sha256 " + "0" * 64 + " -->\nshort\n", good) is False


def test_a_version_bump_that_reads_the_same_turns_leaves_a_cr_record_whole_and_adds_no_edition(tmp_path, monkeypatch):
    shelf, cfg = capture_old_then_remediate(tmp_path, monkeypatch)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"][codes.CAPTURED] == 1
    path = shelf.main_path(shelf.list_records()[0])
    before = record.read(path)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.UNCHANGED: 1}
    assert len(editions.list_editions(path.parent)) == 1
    _, body = record.load(record.read(path))
    assert [t.text for t in render.parse_body(body)] == [t.text for t in render.parse_body(record.load(before)[1])]
    assert "\r" in record.read(path)
    assert record.load(record.read(path))[0]["normalized"]["normalizer"] == 2          # the manifest moved on
