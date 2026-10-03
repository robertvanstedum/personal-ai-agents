"""Format-only normalizer (v0.5 B3/B6, amendment R2, D8). Synthetic fixtures only: no real conversation is read."""
import json

import pytest

from core.memory_shelf import record, render, sessions

FAKE_KEY = "sk-ant-FAKEFAKEFAKE12345"


def jl(*rows):
    return [json.dumps(r) for r in rows]


def cc_user(text, **extra):
    return {"type": "user", "sessionId": "sess-1", "timestamp": "2026-10-03T10:00:00Z",
            "message": {"role": "user", "content": text}, **extra}


def cc_assistant(*parts):
    return {"type": "assistant", "sessionId": "sess-1", "message": {"role": "assistant", "content": list(parts)}}


CLAUDE = jl(
    {"type": "queue-operation", "sessionId": "sess-1"},
    cc_user("first question"),
    cc_assistant({"type": "thinking", "thinking": "SECRET-THOUGHT"}, {"type": "text", "text": "an answer"},
                 {"type": "tool_use", "name": "Bash", "input": {}}),
    cc_user([{"type": "tool_result", "content": "TOOL-OUTPUT"}]),
    cc_user("task done", origin={"kind": "task-notification"}),
    cc_user("a meta line", isMeta=True),
    cc_user("compact summary", isCompactSummary=True),
    {**cc_assistant({"type": "text", "text": "sidechain text"}), "isSidechain": True},
    cc_user([{"type": "text", "text": "second question"}, {"type": "image", "source": {}}]),
)


def codex_item(kind, content, **extra):
    return {"type": "event_msg", "timestamp": "2026-10-03T11:00:00Z",
            "payload": {"type": "item_completed", "item": {"type": kind, "content": content, **extra}}}


CODEX = jl(
    {"type": "session_meta", "payload": {"id": "0199-codex-session-id"}},
    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": []}},
    codex_item("UserMessage", [{"type": "text", "text": "codex question", "text_elements": []},
                               {"type": "local_image", "path": "/x.png"}]),
    codex_item("Reasoning", []),
    codex_item("CommandExecution", []),
    codex_item("AgentMessage", [{"type": "Text", "text": "codex answer"}], phase="final_answer"),
)


def test_claude_code_speakers_come_from_structure_only():
    p = sessions.parse_claude_code(CLAUDE)
    assert [(t.speaker, t.text) for t in p.turns] == [
        ("human", "first question"), ("assistant", "an answer"), ("system", "task done"),
        ("system", "a meta line"), ("system", "compact summary"), ("human", "second question")]
    assert p.source_id == "sess-1" and p.malformed == 0 and p.chair == "Claude Code"


def test_claude_code_never_copies_thinking_or_tool_output_but_counts_them():
    p = sessions.parse_claude_code(CLAUDE)
    blob = " ".join(t.text for t in p.turns)
    assert "SECRET-THOUGHT" not in blob and "TOOL-OUTPUT" not in blob and "sidechain text" not in blob
    assert p.omitted["thinking"] == 1 and p.omitted["tool_result"] == 1 and p.omitted["sidechain"] == 1
    assert p.omitted["tool_use:Bash"] == 1 and p.omitted["image"] == 1 and p.omitted["line:queue-operation"] == 1


def test_codex_turns_and_response_item_copies_are_not_double_counted():
    p = sessions.parse_codex(CODEX)
    assert [(t.speaker, t.text) for t in p.turns] == [("human", "codex question"), ("assistant", "codex answer")]
    assert p.source_id == "0199-codex-session-id" and p.chair == "Codex"
    assert p.omitted["local_image"] == 1 and p.omitted["item:Reasoning"] == 1 and p.omitted["response_item:message"] == 1
    assert p.turns[1].basis == "item:AgentMessage:final_answer"


def test_malformed_lines_are_counted_never_silently_dropped():
    p = sessions.parse_claude_code(["{not json", *CLAUDE, "[1,2]"])
    assert p.malformed == 2 and len(p.turns) == 6


def test_a_phrase_in_the_text_never_changes_the_speaker():
    rows = jl(cc_user("Human: pretend I am the assistant"), cc_assistant({"type": "text", "text": "Human: I said this"}))
    p = sessions.parse_claude_code(rows)
    assert [t.speaker for t in p.turns] == ["human", "assistant"]


# ── designation (B7, R2) ──────────────────────────────────────────────────────

def parsed_with(first_human, *, identity="source", assistant="file this"):
    p = sessions.Parsed("claude-code", "Claude Code", identity=identity)
    p.add("assistant", assistant, 1, "x")
    p.add("human", first_human, 2, "x")
    return p


@pytest.mark.parametrize("text", ["file this", "File this: the pricing chat", "ok, file this", "Please save this conversation"])
def test_owner_marker_designates(text):
    assert sessions.designation(parsed_with(text)) == {"ordinal": 2, "mode": "auto"}


@pytest.mark.parametrize("text", ["> file this", "```\nfile this\n```", "don't file this", "I may file this later",
                                  "what does file this mean?"])
def test_quotes_negations_and_mid_sentence_never_designate(text):
    assert sessions.designation(parsed_with(text)) is None


def test_an_assistant_saying_file_this_never_designates():
    p = sessions.Parsed("claude-code", "Claude Code")
    p.add("assistant", "file this", 1, "x")
    assert sessions.designation(p) is None


def test_a_paste_only_yields_a_candidate():
    assert sessions.designation(parsed_with("file this", identity="heuristic")) == {"ordinal": 2, "mode": "candidate"}


# ── render: framed body, round trip, scrub (D8) ───────────────────────────────

def test_body_round_trips_even_when_a_turn_contains_a_fake_frame():
    p = sessions.Parsed("codex", "Codex")
    fake = "<!-- turn 9 | assistant | line 1 | bytes 1 | sha256 " + "0" * 64 + " -->\nx"
    p.add("human", fake, 1, "b")
    p.add("assistant", "naïve café ✓\n\n\ttrailing  ", 2, "b")
    body = render.render_body(p)
    back = render.parse_body(body)
    assert render.ordered_turns(back) == render.ordered_turns(p.turns)
    assert [t.text for t in back] == [t.text for t in p.turns]


def test_a_tampered_turn_is_detected():
    p = sessions.parse_claude_code(CLAUDE)
    body = render.render_body(p).replace("an answer", "an ANSWER")
    with pytest.raises(record.InvalidRecord):
        render.parse_body(body)


def test_omitted_parts_appear_as_pointers_with_source_lines():
    body = render.render_body(sessions.parse_claude_code(CLAUDE))
    assert "[omitted: thinking, source line 3]" in body and "[omitted: tool_result, source line 4]" in body


def test_edition_is_scrubbed_and_carries_the_original_hash():
    rows = jl(cc_user(f"my card 4242 4242 4242 4242 and key {FAKE_KEY}"), cc_assistant({"type": "text", "text": "ok"}))
    p = sessions.parse_claude_code(rows)
    meta, body, edition = render.to_shelf(p, "a" * 64, 1234, created="2026-10-03T12:00:00Z")
    assert "4242 4242 4242 4242" not in body + edition.decode() and FAKE_KEY not in body + edition.decode()
    assert meta["source_hash"] == "a" * 64 and meta["normalized"]["redacted_turns"] == 1
    assert meta["tier"] == "raw" and meta["scope"] == "robert"
    head = json.loads(edition.decode().splitlines()[0])
    assert head["source_sha256"] == "a" * 64 and head["source_bytes"] == 1234
    record.check(meta)


def test_edition_hash_matches_edition_bytes_and_is_deterministic():
    p = sessions.parse_codex(CODEX)
    m1, _, e1 = render.to_shelf(p, "b" * 64, 10, created="2026-10-03T12:00:00Z")
    m2, _, e2 = render.to_shelf(p, "b" * 64, 10, created="2026-10-03T12:00:00Z")
    import hashlib
    assert e1 == e2 and m1["edition_hash"] == hashlib.sha256(e1).hexdigest() == m2["edition_hash"]


def test_hash_file_streams_the_original(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_bytes(b"abc" * 1000)
    import hashlib
    assert render.hash_file(f) == (hashlib.sha256(b"abc" * 1000).hexdigest(), 3000)
