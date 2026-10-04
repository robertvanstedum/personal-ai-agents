"""Capture attribution fixes (owner-approved 4 Oct 2026, after the Codex selection audit). Synthetic lines only.

Claude Code ``origin.kind``; Codex approval-review and subagent sessions; agent handoffs; attachment boundary;
duplicate matching and its whitespace rule; coverage and the version bump."""
import json
from datetime import datetime, timezone

import pytest

from core.memory_shelf import approvals, codes, coverage, editions, events as ev, ledger, record, render, sessions, watchers
from core.memory_shelf.config import SourceCfg

from .helpers import FAKE_KEY, make_shelf, write_tree

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
OWNER = ev.OwnerAuthority("moi-approve")


def jl(*rows):
    return [json.dumps(r) for r in rows]


# ── Claude Code ─────────────────────────────────────────────────────────────

def cc_user(text, origin=None, **extra):
    row = {"type": "user", "sessionId": "s", "timestamp": "2026-10-04T10:00:00Z", "message": {"role": "user", "content": text}}
    if origin is not None:
        row["origin"] = origin
    row.update(extra)
    return row


def cc(*rows):
    return sessions.parse_claude_code(jl(*rows))


def test_an_explicit_human_origin_is_human_not_system():
    p = cc(cc_user("are you there?", {"kind": "human"}))
    assert [(t.speaker, t.basis) for t in p.turns] == [("human", "origin:human")]
    assert not p.manifest.get("unknown_origin")


def test_no_origin_is_still_human_and_meta_and_compaction_are_still_system():
    p = cc(cc_user("plain"), cc_user("meta line", isMeta=True), cc_user("summary", isCompactSummary=True))
    assert [t.speaker for t in p.turns] == ["human", "system", "system"]


def test_peer_is_coordination_with_who_it_came_from_and_never_the_owner():
    p = cc(cc_user("please review", {"kind": "peer", "from": "other-session", "senderTaskId": "t-1", "body": "x"}))
    (turn,) = p.turns
    assert turn.speaker == "coordination" and turn.basis == "origin:peer"
    assert turn.attrs == {"origin": "peer", "from": "other-session", "senderTaskId": "t-1"}


def test_task_notification_is_system():
    p = cc(cc_user("task done", {"kind": "task-notification", "producer": "bg"}))
    assert [(t.speaker, t.attrs) for t in p.turns] == [("system", {"origin": "task-notification"})]


@pytest.mark.parametrize("origin", [{"kind": "mystery"}, {"kind": "Human"}, {"nokind": 1}, "human", ["human"], 7])
def test_an_unknown_origin_is_never_guessed_human_and_is_visible(origin):
    parser = coverage.parse_with_coverage("claude-code", jl(cc_user("hello", origin)), watchers.PARSERS)
    (turn,) = parser.turns
    assert turn.speaker == "system" and turn.attrs["origin"] == "unknown"
    assert parser.manifest["unknown_origin"] == 1
    assert parser.coverage["flags"] == ["unknown_kind"] and any(k.startswith("origin:") for k in parser.coverage["unknown"])


def test_known_origins_raise_no_coverage_flag():
    rows = [cc_user("a", {"kind": "human"}), cc_user("b", {"kind": "peer", "from": "x"}), cc_user("c", {"kind": "task-notification"}),
            {"type": "assistant", "sessionId": "s", "message": {"content": [{"type": "text", "text": "d"}]}}]
    p = coverage.parse_with_coverage("claude-code", jl(*rows), watchers.PARSERS)
    assert p.coverage["flags"] == [] and p.coverage["seen"] == p.coverage["taken"]


def test_only_a_human_turn_can_designate_a_peer_saying_file_this_cannot():
    assert sessions.designation(cc(cc_user("file this", {"kind": "peer", "from": "x"}))) is None
    assert sessions.designation(cc(cc_user("file this", {"kind": "task-notification"}))) is None
    assert sessions.designation(cc(cc_user("file this", {"kind": "human"}))) == {"ordinal": 1, "mode": "auto"}


def test_images_and_documents_in_a_claude_code_message_are_references_the_text_stays():
    row = {"type": "user", "sessionId": "s", "message": {"role": "user", "content": [
        {"type": "text", "text": "see the picture"}, {"type": "image", "source": "BIG-BASE64"},
        {"type": "document", "source": "DOC-BYTES"}]}}
    p = cc(row)
    assert [t.text for t in p.turns] == ["see the picture"]
    assert p.omitted["image"] == 1 and p.omitted["document"] == 1
    assert "BIG-BASE64" not in json.dumps([t.text for t in p.turns])


# ── Codex session classes ───────────────────────────────────────────────────

def meta(thread_source=None, sid="0199-test", source=None):
    payload = {"id": sid}
    if thread_source is not None:
        payload["thread_source"] = thread_source
    if source is not None:
        payload["source"] = source
    return {"type": "session_meta", "timestamp": "2026-10-04T10:00:00Z", "payload": payload}


def started():
    return {"type": "event_msg", "payload": {"type": "task_started"}}


def item(kind, text, phase=None):
    row = {"type": "item_completed", "item": {"type": kind, "content": [{"type": "text", "text": text}]}}
    if phase:
        row["item"]["phase"] = phase
    return {"type": "event_msg", "payload": row}


def copy(role, text):
    return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                 "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}


def handoff(author, recipient, *parts, ts="2026-10-04T10:05:00Z"):
    return {"type": "response_item", "timestamp": ts,
            "payload": {"type": "agent_message", "author": author, "recipient": recipient, "id": "x", "content": list(parts)}}


def codex(*rows):
    return sessions.parse_codex(jl(*rows))


APPROVAL_PROMPT = "The following is the agent history added since your last approval assessment."
APPROVAL_JSON = '{"risk_level": "low", "user_authorization": "unknown", "outcome": "allow", "rationale": "ok"}'


def test_a_guardian_review_session_is_coordination_never_the_owners_dialogue():
    p = codex(meta("guardian_review", source={"subagent": "x"}), started(), item("UserMessage", APPROVAL_PROMPT),
              item("AgentMessage", APPROVAL_JSON, "final_answer"))
    assert [t.speaker for t in p.turns] == ["coordination", "coordination"]
    assert [t.attrs["kind"] for t in p.turns] == ["approval_prompt", "approval_decision"]
    assert all(t.attrs["class"] == "approval_review" and t.basis.endswith(":approval_review") for t in p.turns)
    assert p.session_class == "approval_review" and p.manifest["session_class"] == "approval_review"
    assert sessions.designation(codex(meta("guardian_review"), started(), item("UserMessage", "file this"))) is None


def test_the_owner_quoting_approval_machinery_in_an_ordinary_session_stays_the_owner():
    """No content heuristics: the same words in a normal session are dialogue, because the session is not a review."""
    p = codex(meta("user"), started(), item("UserMessage", APPROVAL_PROMPT + "\n" + APPROVAL_JSON),
              item("AgentMessage", APPROVAL_JSON, "final_answer"))
    assert [t.speaker for t in p.turns] == ["human", "assistant"] and p.session_class == "dialogue"
    assert "session_class" not in p.manifest


@pytest.mark.parametrize("thread_source", [None, "vscode", "cli", "user", ""])
def test_ordinary_and_unlabelled_sessions_are_dialogue(thread_source):
    assert codex(meta(thread_source), started(), item("UserMessage", "hi")).turns[0].speaker == "human"


@pytest.mark.parametrize("thread_source", ["subagent", "agent_created_thread"])
def test_a_subagent_threads_task_prompt_is_coordination_and_its_replies_are_marked(thread_source):
    p = codex(meta(thread_source), started(), item("UserMessage", "Do the thing"), item("AgentMessage", "Done", "final_answer"))
    assert [t.speaker for t in p.turns] == ["coordination", "assistant"]
    assert p.turns[0].attrs == {"class": "subagent_task", "kind": "subagent_task"} and p.turns[1].attrs == {"class": "subagent"}
    assert p.session_class == "subagent"


def test_a_guardian_sessions_copies_and_duplicates_are_handled_like_any_other():
    p = codex(meta("guardian_review"), started(), item("UserMessage", APPROVAL_PROMPT), copy("user", APPROVAL_PROMPT),
              copy("assistant", APPROVAL_JSON))
    assert [t.speaker for t in p.turns] == ["coordination", "coordination"] and p.omitted["response_item:message"] == 1


def test_a_heartbeat_stays_system_even_in_a_review_session():
    p = codex(meta("guardian_review"), started(), copy("assistant", "<heartbeat>tick</heartbeat>"))
    assert p.turns[0].speaker == "system"


# ── Codex handoffs ──────────────────────────────────────────────────────────

def test_a_handoff_is_kept_as_typed_coordination_with_sender_recipient_and_time():
    p = codex(meta(), started(), item("UserMessage", "run the audit"),
              handoff("/root/agent_a", "/root/agent_b", {"type": "input_text", "text": "NEW_TASK: check the shelf"}))
    turn = p.turns[1]
    assert turn.speaker == "coordination" and turn.basis == "response_item:agent_message" and turn.text == "NEW_TASK: check the shelf"
    assert turn.attrs["sender"] == "/root/agent_a" and turn.attrs["recipient"] == "/root/agent_b"
    assert turn.attrs["who"] == "root_agent_a" and turn.attrs["to"] == "root_agent_b" and turn.attrs["ts"] == "2026-10-04T10:05:00Z"
    assert turn.attrs["class"] == "handoff" and "unavailable" not in turn.attrs
    assert p.manifest["handoffs_retained"] == 1
    body = render.render_body(p)
    head = [l for l in body.splitlines() if l.startswith("<!-- turn 2")][0]
    assert "who=root_agent_a" in head and "to=root_agent_b" in head and "class=handoff" in head and "/root" not in head


def test_an_encrypted_part_is_marked_unavailable_never_decrypted_or_invented():
    p = codex(meta(), started(), handoff("/a", "/b", {"type": "input_text", "text": "visible part"},
                                         {"type": "encrypted_content", "encrypted_content": "gAAAA-cipher"}))
    assert p.turns[0].text == "visible part" and p.turns[0].attrs["unavailable"] == "encrypted_content"
    assert p.manifest["handoffs_unavailable"] == 1 and "cipher" not in json.dumps([t.text for t in p.turns])
    only = codex(meta(), started(), handoff("/a", "/b", {"type": "encrypted_content", "encrypted_content": "gAAAA"}))
    assert only.turns == [] and only.omitted["handoff:unavailable"] == 1 and only.manifest["handoffs_unavailable"] == 1


def test_a_handoff_that_repeats_a_retained_turn_is_a_counted_duplicate():
    p = codex(meta(), started(), item("UserMessage", "NEW_TASK:  do it\n  now"),
              handoff("/a", "/b", {"type": "input_text", "text": "NEW_TASK: do it now"}))
    assert [t.speaker for t in p.turns] == ["human"] and p.omitted["handoff:duplicate"] == 1 and p.manifest["handoffs_duplicate"] == 1


def test_handoff_timestamps_that_are_missing_stay_unknown():
    row = handoff("/a", "/b", {"type": "input_text", "text": "x"})
    del row["timestamp"]
    assert "ts" not in codex(meta(), started(), row).turns[0].attrs


def test_a_handoff_is_not_counted_as_a_role_message_by_the_coverage_check():
    p = coverage.parse_with_coverage("codex", jl(meta(), started(), item("UserMessage", "q"), item("AgentMessage", "a"),
                                                  handoff("/a", "/b", {"type": "input_text", "text": "task"})), watchers.PARSERS)
    assert p.coverage["flags"] == [] and p.coverage["taken"] == {"user": 1, "assistant": 1}


# ── duplicates and the whitespace rule ──────────────────────────────────────

def test_duplicate_counts_are_recorded_and_whitespace_is_collapsed_when_matching():
    p = codex(meta(), started(), item("UserMessage", "line one\r\n\r\nline two"), copy("user", "line one\n\nline two  "),
              item("AgentMessage", "a"), copy("assistant", "a"), copy("assistant", "only a copy has this"))
    assert [t.text for t in p.turns] == ["line one\r\n\r\nline two", "a", "only a copy has this"]
    assert p.manifest["copies_matched"] == 2 and p.manifest["copies_added"] == 1


def test_a_copy_that_differs_only_in_code_indentation_matches_the_item_whose_exact_text_is_the_one_kept():
    exact = "```python\ndef f():\n    return 1\n```"
    flattened = "```python\ndef f():\nreturn 1\n```"
    p = codex(meta(), started(), item("UserMessage", exact), copy("user", flattened))
    assert [t.text for t in p.turns] == [exact]                          # the item's bytes are kept; the variant copy is a duplicate


def test_a_second_message_that_differs_from_a_taken_one_only_in_indentation_is_not_lost():
    """The rule consumes each counterpart once: with no free item left, a same-words copy is its own turn."""
    exact = "```python\n    return 1\n```"
    p = codex(meta(), started(), item("UserMessage", exact), copy("user", exact), copy("user", exact.replace("    ", "")))
    assert len(p.turns) == 2 and p.turns[1].text == exact.replace("    ", "")


# ── through the pipeline: editions, tags, versions, the preview of what changes ──

def codex_root(tmp_path, name, rows, sid):
    root = tmp_path / name
    write_tree(root, {f"2026/10/04/rollout-2026-10-04T00-00-00-{sid}.jsonl": "\n".join(jl(*rows)) + "\n"})
    return root


def setup(tmp_path, rows, sid="0199aaaa-0000-0000-0000-000000000001"):
    shelf = make_shelf(tmp_path)
    cfg = SourceCfg("codex", "codex", codex_root(tmp_path, "codex", rows, sid), ())
    watchers.run_source(shelf, cfg, now=NOW)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    return shelf, cfg


def load(shelf):
    path = shelf.main_path(shelf.list_records()[0])
    return path, record.load(record.read(path))


def test_an_approval_review_record_carries_its_class_in_the_tags_and_the_manifest_and_keeps_provenance(tmp_path):
    shelf, cfg = setup(tmp_path, [meta("guardian_review"), started(), item("UserMessage", APPROVAL_PROMPT),
                                  item("AgentMessage", APPROVAL_JSON, "final_answer")])
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.CAPTURED: 1}
    path, (m, body) = load(shelf)
    assert "class:approval_review" in m["tags"] and m["normalized"]["session_class"] == "approval_review"
    turns = render.parse_body(body)
    assert [t.speaker for t in turns] == ["coordination", "coordination"] and turns[0].attrs["kind"] == "approval_prompt"
    assert render.dialogue_turns(turns) == []                           # excluded from owner dialogue and retrieval


def test_dialogue_helper_keeps_owner_and_assistant_turns_only():
    p = codex(meta("subagent"), started(), item("UserMessage", "task"), item("AgentMessage", "reply", "final_answer"),
              handoff("/a", "/b", {"type": "input_text", "text": "h"}))
    assert render.dialogue_turns(p.turns) == []                         # a subagent thread is agent work, not the owner's dialogue
    q = codex(meta("user"), started(), item("UserMessage", "q"), item("AgentMessage", "a", "final_answer"),
              copy("assistant", "<heartbeat>tick</heartbeat>"))
    assert [t.speaker for t in render.dialogue_turns(q.turns)] == ["human", "assistant"]


def test_a_version_bump_adds_an_edition_with_the_corrected_roles_and_keeps_the_old_one(tmp_path, monkeypatch):
    rows = [meta("guardian_review"), started(), item("UserMessage", APPROVAL_PROMPT), item("AgentMessage", APPROVAL_JSON, "final_answer")]
    shelf, cfg = setup(tmp_path, rows)
    old_parser = watchers.PARSERS["codex"]

    def legacy(lines):                                                  # what the shelf held before: roles straight from the item type
        out = sessions.Parsed("codex", "Codex", normalizer=2)
        for number, raw in enumerate(lines, 1):
            r = json.loads(raw)
            payload = r.get("payload") or {}
            if r.get("type") == "session_meta":
                out.source_id = payload.get("id")
            if r.get("type") == "event_msg" and payload.get("type") == "item_completed":
                it = payload["item"]
                user = it["type"] == "UserMessage"
                out.add("human" if user else "assistant", it["content"][0]["text"], number,
                        "item:UserMessage" if user else "item:AgentMessage:final_answer")
        return out
    monkeypatch.setitem(watchers.PARSERS, "codex", legacy)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 2)
    watchers.run_source(shelf, cfg, now=NOW)
    path, (m, body) = load(shelf)
    assert [t.speaker for t in render.parse_body(body)] == ["human", "assistant"] and m["normalized"]["normalizer"] == 2
    first_edition = [e.path.read_bytes() for e in editions.list_editions(path.parent)]
    monkeypatch.setitem(watchers.PARSERS, "codex", old_parser)
    monkeypatch.setitem(sessions.NORMALIZER_VERSION, "codex", 3)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.EDITION_ADDED: 1}
    path, (m, body) = load(shelf)
    assert [t.speaker for t in render.parse_body(body)] == ["coordination", "coordination"] and m["edition"] == 2
    assert m["events"][-1]["note"] == "normalizer:2->3"
    assert [e.path.read_bytes() for e in editions.list_editions(path.parent)][0] == first_edition[0]    # history untouched
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {"skipped_unchanged": 1}               # settled at version 3


def test_reprocessing_is_deterministic_the_same_source_and_version_give_the_same_edition_bytes():
    rows = [meta("guardian_review"), started(), item("UserMessage", APPROVAL_PROMPT), handoff("/a", "/b", {"type": "input_text", "text": "t"})]
    one = render.edition_bytes(codex(*rows), "0" * 64, 10, 0)
    two = render.edition_bytes(codex(*rows), "0" * 64, 10, 0)
    assert one == two
