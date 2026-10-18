"""Per-turn times in every normalizer that has them (Vault recall gap, 17 Oct 2026). Synthetic fixtures only.

A conversation begun in April and continued in May can only be found for May if each turn keeps its own time: the
frame attribute ``ts`` (``YYYY-MM-DDTHH:MM:SSZ``), the convention Rooms already uses. Text and speakers are unchanged.
"""
import hashlib
import json

import pytest

from core.memory_shelf import bundle as bundles, codes, editions, inbox, mc, record, render, sessions
from core.memory_shelf.shelf import _same_turns

from .helpers import make_shelf
from .test_chatgpt_export import cconv, node
from .test_grok_export import gconv, gr


def jl(*rows):
    return [json.dumps(r) for r in rows]


def cc(kind, text, stamp=None, sid="sess-apr"):
    row = {"type": kind, "sessionId": sid,
           "message": {"role": kind, "content": text if kind == "user" else [{"type": "text", "text": text}]}}
    if stamp:
        row["timestamp"] = stamp
    return row


# Begun on 28 April, continued on 3 and 4 May.
APRIL_TO_MAY = jl(
    cc("user", "Plan the telescope mount", "2026-04-28T21:00:00.000Z"),
    cc("assistant", "Start with the tripod", "2026-04-28T21:00:05.120Z"),
    cc("user", "Back again: the counterweight wobbles", "2026-05-03T09:30:00.000Z"),
    cc("assistant", "Tighten the clutch", "2026-05-04T08:00:00Z"),
)


def times(parsed):
    return [(t.speaker, (t.attrs or {}).get("ts")) for t in parsed.turns]


@pytest.mark.parametrize("value, want", [
    ("2026-05-03T09:30:00.000Z", "2026-05-03T09:30:00Z"),
    ("2026-05-03T11:30:00+02:00", "2026-05-03T09:30:00Z"),
    ("2026-05-03 09:30:00.123456+00:00", "2026-05-03T09:30:00Z"),     # Postgres str(): a space, not a token
    ("2026-05-03T09:30:00", "2026-05-03T09:30:00Z"),                   # no zone: UTC
    ("2026-05-03", "2026-05-03T00:00:00Z"),
    (1777800600, "2026-05-03T09:30:00Z"), (1777800600.75, "2026-05-03T09:30:00Z"),
    ({"$date": {"$numberLong": "1777800600123"}}, "2026-05-03T09:30:00Z"),   # Grok's real response shape (epoch ms)
    ({"$date": "2026-05-03T09:30:00.000Z"}, "2026-05-03T09:30:00Z"),          # relaxed extended JSON
    ({"$date": 1777800600123}, "2026-05-03T09:30:00Z"),                       # legacy extended JSON: ms as a number
    ({"$date": True}, None), ({"$date": {"$numberLong": "x"}}, None), ({"$date": {"$numberLong": "1", "y": 2}}, None),
    (True, None), (None, None), ("", None), ("yesterday", None),
    (1e15, None), ("0001-01-01T00:00:00Z", None),                      # not a time this reader trusts
])
def test_turn_time_reads_iso_and_unix_seconds_and_never_guesses(value, want):
    assert sessions.turn_time(value) == want


def test_claude_code_keeps_each_lines_own_time():
    p = sessions.parse_claude_code(APRIL_TO_MAY)
    assert times(p) == [("human", "2026-04-28T21:00:00Z"), ("assistant", "2026-04-28T21:00:05Z"),
                        ("human", "2026-05-03T09:30:00Z"), ("assistant", "2026-05-04T08:00:00Z")]
    assert p.started == "2026-04-28T21:00:00.000Z" and p.normalizer == 3
    body = render.render_body(p)
    assert "| ts=2026-05-03T09:30:00Z -->" in body
    assert [(t.attrs or {}).get("ts") for t in render.parse_body(body)] == [ts for _, ts in times(p)]


def test_a_line_without_a_time_keeps_its_frame_byte_for_byte():
    p = sessions.parse_claude_code(jl(cc("user", "no clock here"), cc("assistant", "none here either")))
    assert [t.attrs for t in p.turns] == [None, None]
    assert " | ts=" not in render.render_body(p)


def test_claude_code_origin_attributes_and_time_travel_together():
    p = sessions.parse_claude_code(jl({**cc("user", "peer says hi", "2026-05-01T00:00:00Z"),
                                       "origin": {"kind": "peer", "from": "agent-a"}}))
    assert p.turns[0].attrs == {"origin": "peer", "from": "agent-a", "ts": "2026-05-01T00:00:00Z"}


def codex_item(kind, text, stamp):
    return {"type": "event_msg", "timestamp": stamp,
            "payload": {"type": "item_completed", "item": {"type": kind, "content": [{"type": "text", "text": text}]}}}


def test_codex_keeps_each_items_time_and_a_copy_never_adds_a_second_turn():
    rows = jl({"type": "session_meta", "timestamp": "2026-04-28T21:00:00Z", "payload": {"id": "0199-apr"}},
              codex_item("UserMessage", "Refactor the exporter", "2026-04-28T21:00:01Z"),
              {"type": "response_item", "timestamp": "2026-04-28T21:00:02Z",
               "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Refactor the exporter"}]}},
              codex_item("AgentMessage", "Done", "2026-04-28T21:05:00Z"),
              codex_item("UserMessage", "One more change", "2026-05-02T10:00:00Z"),
              {"type": "response_item", "timestamp": "2026-05-02T10:01:00Z",       # a copy with no item: a turn of its own
               "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Copy-only reply"}]}})
    p = sessions.parse_codex(rows)
    assert times(p) == [("human", "2026-04-28T21:00:01Z"), ("assistant", "2026-04-28T21:05:00Z"),
                        ("human", "2026-05-02T10:00:00Z"), ("assistant", "2026-05-02T10:01:00Z")]
    assert p.normalizer == 5


def test_claude_ai_keeps_each_messages_created_at():
    conv = {"uuid": "u-1", "name": "n", "created_at": "2026-04-28T21:00:00Z", "updated_at": "2026-05-03T10:00:00Z",
            "chat_messages": [
                {"uuid": "m1", "sender": "human", "text": "April question", "content": [{"type": "text", "text": "April question"}],
                 "created_at": "2026-04-28T21:00:00.123456Z", "attachments": [], "files": []},
                {"uuid": "m2", "sender": "assistant", "text": "May answer", "content": [{"type": "text", "text": "May answer"}],
                 "created_at": "2026-05-03T10:00:00Z", "attachments": [], "files": []}]}
    item = inbox.parse_conversation(inbox.check_conversation(conv))
    assert times(item.parsed) == [("human", "2026-04-28T21:00:00Z"), ("assistant", "2026-05-03T10:00:00Z")]
    assert item.parsed.normalizer == 3


def test_grok_keeps_each_responses_create_time_and_names_its_parser_version():
    conv = gconv("g-1", [gr("r1", None, "human", "April question", 1), gr("r2", "r1", "assistant", "Answer", 2,
                                                                          create_time="2026-05-03T10:00:00Z")])
    item = inbox.parse_grok_conversation(conv)
    assert times(item.parsed) == [("human", "2026-09-01T10:01:00Z"), ("assistant", "2026-05-03T10:00:00Z")]
    assert item.parsed.normalizer == 2                     # it was left at the default 1 before


def test_grok_reads_the_real_exports_extended_json_response_time():
    conv = gconv("g-2", [gr("r1", None, "human", "May question", 1, create_time={"$date": {"$numberLong": "1777800600000"}})])
    assert times(inbox.parse_grok_conversation(conv).parsed) == [("human", "2026-05-03T09:30:00Z")]


def test_chatgpt_keeps_each_messages_create_time():
    nodes = [node("root", None, None), node("m1", "root", "user", parts=["April question"], minute=0),
             node("m2", "m1", "assistant", parts=["May answer"], minute=0, create_time=1777800600.5)]
    item = inbox.parse_chatgpt_conversation(cconv("c-1", nodes))
    got = times(item.parsed)
    assert got[0][1] == sessions.turn_time(nodes[1][1]["message"]["create_time"]) and got[1] == ("assistant", "2026-05-03T09:30:00Z")
    assert item.parsed.normalizer == 2


def mc_doc(created_at):
    return json.dumps({"schema": mc.SCHEMA, "instance": "dev", "conversation_id": "c-0123456789ab", "source_id": "dev:c-0123456789ab",
                       "archived": False, "legacy": False, "created_at": "2026-04-28 21:00:00+00:00", "updated_at": "x",
                       "scope": None, "turns": [{"note_id": 1, "created_at": created_at, "author_kind": "owner", "text": "hi",
                                                 "who": "owner-1", "label": "Robert", "voice": None}]})


def test_mc_normalizes_the_notes_postgres_time_which_version_1_dropped():
    p = mc.parse([mc_doc("2026-05-03 09:30:00.123+00:00")])
    assert p.turns[0].attrs["ts"] == "2026-05-03T09:30:00Z" and p.normalizer == 2 == mc.NORMALIZER


def bundle_of(parsed, raw: bytes):
    return bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"claude-code:{parsed.source_id}",
                               title="t", origin="watcher:claude-code", created=bundles.utc(parsed.started),
                               ledger_key=bundles.ledger_key_for("claude-code", parsed.source_id))


def test_an_unchanged_source_read_by_the_new_parser_gets_a_new_edition_with_times(tmp_path):
    shelf = make_shelf(tmp_path)
    raw = "\n".join(APRIL_TO_MAY).encode()
    old = sessions.parse_claude_code(APRIL_TO_MAY)
    old.normalizer = 2
    old.turns = [t.__class__(t.ordinal, t.speaker, t.text, t.line, t.basis, None) for t in old.turns]   # what version 2 stored
    first = shelf.ingest(bundle_of(old, raw))
    assert first.outcome == codes.CAPTURED
    again = shelf.ingest(bundle_of(sessions.parse_claude_code(APRIL_TO_MAY), raw))
    assert again.outcome == codes.EDITION_ADDED                             # was UNCHANGED: only text and speakers compared
    main = shelf.find_record(first.record_id)
    meta, body = record.load(record.read(main))
    assert meta["edition"] == 2 and meta["normalized"]["normalizer"] == 3
    assert meta["events"][-1]["note"] == "normalizer:2->3"
    assert [(t.attrs or {}).get("ts") for t in render.parse_body(body)][2] == "2026-05-03T09:30:00Z"
    assert len(editions.list_editions(main.parent)) == 2                    # the old edition stays
    assert shelf.ingest(bundle_of(sessions.parse_claude_code(APRIL_TO_MAY), raw)).outcome == codes.UNCHANGED


def test_same_turns_compares_times_but_bodies_without_times_are_still_the_same():
    with_times = render.render_body(sessions.parse_claude_code(APRIL_TO_MAY))
    plain = sessions.parse_claude_code(APRIL_TO_MAY)
    plain.turns = [t.__class__(t.ordinal, t.speaker, t.text, t.line, t.basis, None) for t in plain.turns]
    assert not _same_turns(render.render_body(plain), with_times)
    assert _same_turns(with_times, with_times) and _same_turns(render.render_body(plain), render.render_body(plain))


def test_retrieval_minimums_are_not_raised_so_records_whose_source_is_gone_stay_retrievable():
    assert sessions.RETRIEVAL_MIN_NORMALIZER["claude-code"] == 2 and sessions.RETRIEVAL_MIN_NORMALIZER["codex"] == 3
    assert sessions.RETRIEVAL_MIN_NORMALIZER["claude-ai"] == 2 and sessions.RETRIEVAL_MIN_NORMALIZER["minimoi-mc"] == 1
    assert sessions.NORMALIZER_VERSION["rooms"] == 1 and sessions.NORMALIZER_VERSION["paste"] == 1      # nothing changed there
