"""§10 enhanced mediation: complete Codex reading, the coverage check, re-mediation. Synthetic fixtures only."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, coverage, editions, events as ev, fidelity, ledger, record, review, sessions, watchers
from core.memory_shelf import cli
from core.memory_shelf.config import Config, SourceCfg

from .helpers import make_shelf, write_tree

OWNER = ev.OwnerAuthority("moi-approve")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def jl(*rows):
    return [json.dumps(r) for r in rows]


def meta(sid="0199-test"):
    return {"type": "session_meta", "timestamp": "2026-10-03T11:00:00Z", "payload": {"id": sid}}


def started():
    return {"type": "event_msg", "payload": {"type": "task_started"}}


def item(kind, text, phase=None):
    row = {"type": "item_completed", "item": {"type": kind, "content": [{"type": "text", "text": text}]}}
    if phase:
        row["item"]["phase"] = phase
    return {"type": "event_msg", "payload": row}


def copy(role, *texts, phase=None):
    kind = "input_text" if role in ("user", "developer") else "output_text"
    payload = {"type": "message", "role": role, "content": [{"type": kind, "text": t} for t in texts]}
    if phase:
        payload["phase"] = phase
    return {"type": "response_item", "payload": payload}


def parse(*rows):
    return sessions.parse_codex(jl(meta(), *rows))


def turns(parsed):
    return [(t.speaker, t.text) for t in parsed.turns]


# ── complete reading ────────────────────────────────────────────────────────

def test_a_session_written_only_as_response_items_is_still_captured_in_order():
    p = parse(started(), copy("user", "exec prompt"), copy("assistant", "reply one", phase="commentary"),
              copy("assistant", "reply two", phase="final_answer"))
    assert turns(p) == [("human", "exec prompt"), ("assistant", "reply one"), ("assistant", "reply two")]
    assert [t.basis for t in p.turns] == ["response_item:user", "response_item:assistant:commentary",
                                          "response_item:assistant:final_answer"]


def test_items_with_their_copies_are_not_counted_twice():
    p = parse(started(), item("UserMessage", "question"), copy("user", "question"),
              item("AgentMessage", "answer", "final_answer"), copy("assistant", "answer", phase="final_answer"))
    assert turns(p) == [("human", "question"), ("assistant", "answer")]
    assert p.omitted["response_item:message"] == 2


def test_a_copy_that_only_differs_in_whitespace_is_still_the_same_turn():
    p = parse(started(), item("UserMessage", "line one\r\n\r\nline two"), copy("user", "line one\n\nline two  "))
    assert [t.speaker for t in p.turns] == ["human"]


def test_a_copy_with_no_matching_item_is_added_where_it_sits():
    p = parse(started(), item("UserMessage", "first"), item("AgentMessage", "second", "commentary"),
              copy("assistant", "only a copy has this"), item("UserMessage", "third"))
    assert turns(p) == [("human", "first"), ("assistant", "second"), ("assistant", "only a copy has this"),
                        ("human", "third")]


def test_the_same_words_said_twice_are_two_turns():
    p = parse(started(), item("UserMessage", "ok"), copy("user", "ok"), started(), item("UserMessage", "ok"),
              copy("user", "ok"))
    assert turns(p) == [("human", "ok"), ("human", "ok")]


def test_a_copy_before_its_task_started_line_still_matches_its_item():
    p = parse(copy("user", "question"), started(), item("UserMessage", "question"))
    assert turns(p) == [("human", "question")]


def test_each_injected_context_marker_is_a_pointer_never_a_human_turn():
    markers = ["<environment_context>cwd</environment_context>", "<guardian_tool_descriptions>x</guardian_tool_descriptions>",
               "<recommended_plugins>x</recommended_plugins>", "<external_codex_apps_open_page>x</external_codex_apps_open_page>",
               "<codex_delegation>x</codex_delegation>", "# AGENTS.md instructions for /repo\n\nrules"]
    for marker in markers:
        p = parse(started(), copy("user", marker))
        assert p.turns == [], marker
        assert any(k.startswith("injected:") for k in p.omitted), marker


def test_injected_context_next_to_a_human_message_leaves_the_human_turn_alone():
    p = parse(started(), copy("user", "<environment_context>cwd</environment_context>", "# AGENTS.md instructions for x",
                              "please do the thing"))
    assert turns(p) == [("human", "please do the thing")]
    assert p.omitted["injected:environment_context"] == 1 and p.omitted["injected:AGENTS.md"] == 1


def test_injected_context_that_an_item_carries_is_a_pointer_too():
    p = parse(started(), item("UserMessage", "<environment_context>cwd</environment_context>"))
    assert p.turns == [] and p.omitted["injected:environment_context"] == 1


def test_developer_role_copies_are_pointers():
    p = parse(started(), copy("developer", "<permissions>x</permissions>"))
    assert p.turns == [] and p.omitted["injected:developer"] == 1


def test_a_heartbeat_is_a_system_turn_kept_verbatim_and_never_designates():
    beat = "<heartbeat>file this</heartbeat>"
    p = parse(started(), copy("assistant", beat), item("UserMessage", "<heartbeat>save this conversation</heartbeat>"))
    assert turns(p) == [("system", beat), ("system", "<heartbeat>save this conversation</heartbeat>")]
    assert sessions.designation(p) is None


def test_a_question_reply_is_a_human_turn_and_an_unknown_tag_on_a_copy_fails_closed_to_system():
    p = parse(started(), copy("user", "<send_user_message_question_reply>yes</send_user_message_question_reply>"),
              copy("user", "<brand_new_thing>x</brand_new_thing>"))
    assert [t.speaker for t in p.turns] == ["human", "system"]
    assert p.turns[1].basis == "response_item:user:system:brand_new_thing"


def test_an_item_that_starts_with_an_unknown_tag_stays_human_because_the_structure_says_so():
    p = parse(started(), item("UserMessage", "<div>look at this html</div>"))
    assert turns(p) == [("human", "<div>look at this html</div>")]


def test_a_human_marker_in_a_copy_only_session_designates():
    p = parse(started(), copy("user", "file this"), copy("assistant", "ok"))
    assert sessions.designation(p) == {"ordinal": 1, "mode": "auto"}


def test_commentary_messages_are_assistant_turns_with_the_phase_noted():
    p = parse(started(), item("AgentMessage", "thinking aloud", "commentary"))
    assert p.turns[0].basis == "item:AgentMessage:commentary" and p.turns[0].speaker == "assistant"


def test_an_old_style_file_reads_exactly_as_before():
    p = parse(item("UserMessage", "q"), item("Reasoning", ""), item("AgentMessage", "a", "final_answer"))
    assert turns(p) == [("human", "q"), ("assistant", "a")] and p.normalizer == sessions.NORMALIZER_VERSION["codex"]


# ── the coverage check ──────────────────────────────────────────────────────

def covered(*rows, kind="codex"):
    return coverage.parse_with_coverage(kind, jl(meta(), *rows), watchers.PARSERS)


def test_a_complete_reading_has_no_flags_and_the_counts_say_so():
    p = covered(started(), item("UserMessage", "q"), copy("user", "q"), item("AgentMessage", "a"), copy("assistant", "a"),
                copy("user", "<environment_context>x</environment_context>"))
    assert p.coverage["flags"] == [] and p.coverage["gap"] == {"user": 0, "assistant": 0}
    assert p.coverage["seen"] == {"user": 1, "assistant": 1} and p.coverage["taken"] == {"user": 1, "assistant": 1}


def test_a_reader_that_misses_copies_is_flagged_with_counts_only(monkeypatch):
    """Mutation: the old reader (items only). The check must not stay silent on the gapped file."""
    rows = (started(), copy("user", "SECRET-ONE"), copy("assistant", "SECRET-TWO"), item("UserMessage", "q"))
    p = coverage.parse_with_coverage("codex", jl(meta(), *rows), {"codex": _items_only})
    assert p.coverage["flags"] == ["possible_gap"] and p.coverage["gap"] == {"user": 1, "assistant": 1}
    assert "SECRET" not in json.dumps(p.coverage)


def test_a_line_type_nobody_has_seen_is_unknown_kind_by_name_only():
    p = covered(started(), item("UserMessage", "q"), {"type": "brand_new_line", "payload": {"type": "x"}},
                {"type": "event_msg", "payload": {"type": "new/event kind"}},
                {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "NewItem"}}})
    assert p.coverage["flags"] == ["unknown_kind"]
    assert p.coverage["unknown"] == {"event:new/event_kind": 1, "item:NewItem": 1, "line:brand_new_line": 1}


def test_unknown_kind_names_are_short_safe_identifiers():
    p = covered({"type": "x" * 500 + "\nsecret text", "payload": {}})
    assert all(len(k) <= 50 and "\n" not in k for k in p.coverage["unknown"])


def test_claude_code_coverage_counts_text_parts_and_knows_its_line_types():
    rows = [{"type": "user", "sessionId": "s", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "sessionId": "s", "message": {"content": [{"type": "text", "text": "yo"},
                                                                            {"type": "thinking", "thinking": "x"}]}},
            {"type": "last-prompt"}]
    p = coverage.parse_with_coverage("claude-code", jl(*rows), watchers.PARSERS)
    assert p.coverage["flags"] == [] and p.coverage["seen"] == {"user": 1, "assistant": 1}
    p = coverage.parse_with_coverage("claude-code", jl(*rows, {"type": "mystery-line"}), watchers.PARSERS)
    assert p.coverage["flags"] == ["unknown_kind"]


# ── watcher, ledger, review, record ─────────────────────────────────────────

def codex_root(tmp_path, files):
    root = tmp_path / "codex"
    write_tree(root, {f"2026/10/03/rollout-2026-10-03T00-00-00-{name}.jsonl": "\n".join(jl(*rows)) + "\n"
                      for name, rows in files.items()})
    return root


UUID_A = "0199aaaa-0000-0000-0000-000000000001"
UUID_B = "0199bbbb-0000-0000-0000-000000000002"


def setup(tmp_path, files):
    shelf = make_shelf(tmp_path)
    cfg = SourceCfg("codex", "codex", codex_root(tmp_path, files), ())
    watchers.run_source(shelf, cfg, now=NOW)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    return shelf, cfg


def clean_rows(sid):
    return [meta(sid), started(), item("UserMessage", "q"), item("AgentMessage", "a")]


def test_a_flagged_file_shows_in_the_run_counts_ledger_review_and_record(tmp_path, monkeypatch):
    shelf, cfg = setup(tmp_path, {UUID_A: [meta(UUID_A), started(), copy("user", "PRIVATE-TEXT"), item("UserMessage", "q")],
                                  UUID_B: clean_rows(UUID_B)})
    monkeypatch.setitem(watchers.PARSERS, "codex", lambda lines: _items_only(lines))
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"]["possible_gap"] == 1 and out["counts"][codes.CAPTURED] == 2
    rep = ledger.report(shelf)
    assert rep["coverage"] == {"codex": {"possible_gap": 1, "gap_messages": 1}} and rep["ok"]
    items = [i for i in review.items(shelf) if i["type"] == "coverage-flag"]
    assert len(items) == 1 and set(items[0]["detail"]) == {"source", "date", "bytes", "flags", "gap", "unknown"}
    assert "PRIVATE" not in json.dumps(items)
    entry = next(e for e in shelf.list_records())
    flagged = [e for e in shelf.list_records() if (record.load(shelf.main_path(e).read_text())[0]["normalized"]
                                                   .get("coverage") or {}).get("flags")]
    assert len(flagged) == 1


def _items_only(lines):
    """Mutation seam: the pre-§10 reader, which ignores response_item messages."""
    out = sessions.Parsed("codex", "Codex", normalizer=sessions.NORMALIZER_VERSION["codex"])
    for number, raw in enumerate(lines, 1):
        row = json.loads(raw)
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            out.source_id = payload.get("id")
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            it = payload["item"]
            out.add("human" if it["type"] == "UserMessage" else "assistant", it["content"][0]["text"], number, "item:UserMessage"
                    if it["type"] == "UserMessage" else "item:AgentMessage:?")
    return out


def test_the_flag_is_resolved_and_the_warning_goes_once_a_complete_edition_is_captured(tmp_path, monkeypatch):
    shelf, cfg = setup(tmp_path, {UUID_A: [meta(UUID_A), started(), copy("user", "gapped"), item("UserMessage", "q")]})
    monkeypatch.setitem(watchers.PARSERS, "codex", _items_only)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    first = watchers.run_source(shelf, cfg, now=NOW)
    assert first["counts"]["possible_gap"] == 1
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    second = watchers.run_source(shelf, cfg, now=NOW)            # same bytes, same version: nothing re-read, the flag stays
    assert second["counts"].get("possible_gap") == 1 and second["counts"]["skipped_unchanged"] == 1
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)  # the fixed reader ships
    third = watchers.run_source(shelf, cfg, now=NOW)
    assert "possible_gap" not in third["counts"] and third["counts"][codes.EDITION_ADDED] == 1
    assert [i for i in review.items(shelf) if i["type"] == "coverage-flag"] == []
    assert ledger.report(shelf)["coverage"] == {}


# ── re-mediation ────────────────────────────────────────────────────────────

def test_a_version_bump_with_changed_turns_is_a_new_edition_and_the_old_one_stays(tmp_path, monkeypatch):
    rows = [meta(UUID_A), started(), copy("user", "from a copy"), item("UserMessage", "q"), item("AgentMessage", "a")]
    shelf, cfg = setup(tmp_path, {UUID_A: rows})
    monkeypatch.setitem(watchers.PARSERS, "codex", _items_only)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    watchers.run_source(shelf, cfg, now=NOW)
    entry = shelf.list_records()[0]
    folder = shelf.main_path(entry).parent
    before = [e.path.read_bytes() for e in editions.list_editions(folder)]
    assert len(before) == 1
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1}
    after = editions.list_editions(folder)
    assert len(after) == 2 and after[0].path.read_bytes() == before[0]               # the old edition is untouched
    meta_, body = record.load(shelf.main_path(entry).read_text())
    assert meta_["edition"] == 2 and meta_["normalized"]["normalizer"] == 2
    last = meta_["events"][-1]
    assert last["kind"] == "edition-added" and last["note"] == "normalizer:1->2"
    assert "from a copy" in body
    again = watchers.run_source(shelf, cfg, now=NOW)
    assert again["counts"] == {"skipped_unchanged": 1}                                # settled at version 2


def test_a_version_bump_that_reads_the_same_turns_adds_no_edition(tmp_path, monkeypatch):
    shelf, cfg = setup(tmp_path, {UUID_A: clean_rows(UUID_A)})
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    monkeypatch.setattr(sessions, "parse_codex", sessions.parse_codex)
    watchers.run_source(shelf, cfg, now=NOW)
    entry = shelf.list_records()[0]
    folder = shelf.main_path(entry).parent
    editions_before = [e.path.name for e in editions.list_editions(folder)]
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.UNCHANGED: 1}
    assert [e.path.name for e in editions.list_editions(folder)] == editions_before
    meta_, _ = record.load(shelf.main_path(entry).read_text())
    assert meta_["edition"] == 1 and meta_["normalized"]["normalizer"] == 2 and not any(
        e["kind"] == "edition-added" for e in meta_["events"])


def test_claude_code_records_do_not_churn_when_only_codex_moves(tmp_path):
    from .helpers import cc_lines
    shelf = make_shelf(tmp_path)
    root = tmp_path / "cc"
    write_tree(root, {"projA/s-1.jsonl": cc_lines("s-1", ("human", "hi"), ("assistant", "yo"))})
    cfg = SourceCfg("claude-code", "claude-code", root, ())
    watchers.run_source(shelf, cfg, now=NOW)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.CAPTURED: 1}
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {"skipped_unchanged": 1}
    assert sessions.NORMALIZER_VERSION["claude-code"] >= 1


def test_a_record_from_an_older_reader_has_no_normalizer_and_counts_as_version_one(tmp_path, monkeypatch):
    shelf, cfg = setup(tmp_path, {UUID_A: [meta(UUID_A), started(), copy("user", "from a copy"), item("UserMessage", "q")]})
    monkeypatch.setitem(watchers.PARSERS, "codex", _items_only)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    watchers.run_source(shelf, cfg, now=NOW)
    entry = shelf.list_records()[0]
    path = shelf.main_path(entry)
    meta_, body = record.load(path.read_text())
    meta_["normalized"].pop("normalizer")
    meta_["normalized"].pop("coverage", None)
    record.write(path, meta_, body)                                      # a record written before versions existed
    state_path = Path(shelf.status_dir) / "watch-state" / "codex.json"
    state = json.loads(state_path.read_text())
    for e in state["files"].values():
        e.pop("normalizer", None)
    state_path.write_text(json.dumps(state))
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.EDITION_ADDED: 1}


def test_fidelity_skips_a_record_whose_reader_is_older_instead_of_calling_it_damaged(tmp_path, monkeypatch):
    shelf, cfg = setup(tmp_path, {UUID_A: [meta(UUID_A), started(), copy("user", "from a copy"), item("UserMessage", "q")]})
    monkeypatch.setitem(watchers.PARSERS, "codex", _items_only)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 1)
    watchers.run_source(shelf, cfg, now=NOW)
    monkeypatch.setitem(watchers.PARSERS, "codex", sessions.parse_codex)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    path = shelf.main_path(shelf.list_records()[0])
    config = Config(shelf_root=shelf.root, sources={"codex": cfg})
    result = fidelity.check_record(config, path)
    assert result["status"] == "skipped" and result["reason"] == fidelity.NORMALIZER_CHANGED
    watchers.run_source(shelf, cfg, now=NOW)
    assert fidelity.check_record(config, path)["status"] == "ok"


def test_the_cli_prints_the_coverage_line_and_the_flagged_files_without_text(tmp_path, monkeypatch):
    import io
    shelf, cfg = setup(tmp_path, {UUID_A: [meta(UUID_A), started(), copy("user", "PRIVATE-TEXT"), item("UserMessage", "q")]})
    monkeypatch.setitem(watchers.PARSERS, "codex", _items_only)
    watchers.run_source(shelf, cfg, now=NOW)
    out = io.StringIO()
    cli.cmd_ledger(shelf, out, False)
    assert "coverage\t" in out.getvalue() and "possible_gap" in out.getvalue() and "PRIVATE" not in out.getvalue()
    out = io.StringIO()
    config = Config(shelf_root=shelf.root, inbox_root=tmp_path / "inbox", sources={"codex": cfg})
    cli.cmd_review(config, shelf, out)
    text = out.getvalue()
    assert "coverage-flag\tcodex\t" in text and "PRIVATE" not in text
    clear = make_shelf(tmp_path, "clear")
    out = io.StringIO()
    cli.cmd_ledger(clear, out, False)
    assert "coverage\tclear" in out.getvalue()
