"""A spoken lookup's CoS turn may only read (Robert, 8 Nov 2026: "I don't need you to ask me for permission to read
things, maybe just to write things"). Its binding is kept read-only, and the bound listener refuses every operation
that changes anything before it is admitted."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domains.cos import tool_listener  # noqa: E402
from domains.cos.binding import BindingStore, TurnBinder  # noqa: E402
from test_cos_tool_listener import RUN, USER, rig, tool  # noqa: E402,F401  (fixture)


def lookup(bindings, turn="t1", run=1, read_only=True):
    binder = TurnBinder(bindings)
    binding = {"origin": "owner", "principal": "robert", "owner_request_id": "req-1",
               **({"read_only": True} if read_only else {})}
    assert binder.begin(turn_id=turn, session_user=USER, agent_entry="cos-agent-a", conversation_id="owner",
                        meeting_scope=None, binding=binding) == turn
    assert bindings.attach_run(turn, RUN.format(run))


def test_a_lookup_turn_reads_but_every_change_is_refused_before_admission(rig):
    client, bindings, writer = rig
    lookup(bindings)
    assert tool(client, {"operation": "list"}, call="c-list")["status"] == "done"
    refused = tool(client, {"operation": "open", "title": "Prepare deck"}, call="c-open")
    assert refused["status"] == "refused" and refused["code"] == "read_only", refused
    assert writer.docs("cos.in_flight") == []
    assert [c["tool"] for c in bindings.trace("t1")["calls"]] == ["cos_actions"]   # the refused call was never admitted


def test_a_plain_owner_turn_still_writes(rig):
    client, bindings, writer = rig
    lookup(bindings, read_only=False)
    assert tool(client, {"operation": "open", "title": "Prepare deck"})["status"] == "done"


def test_a_meeting_binding_never_carries_the_flag(rig):
    _client, bindings, _writer = rig
    binder = TurnBinder(bindings)
    assert binder.begin(turn_id="m1", session_user=USER, agent_entry="cos-meeting", conversation_id="room",
                        meeting_scope="owner_private",
                        binding={"origin": "meeting", "principal": "robert", "audience": "room", "claim_id": "c",
                                 "room_generation": "g", "read_only": True}) == "m1"
    row = bindings._db().execute("SELECT read_only FROM bindings WHERE turn_id='m1'").fetchone()
    assert row["read_only"] == 0


def test_an_older_bindings_file_gains_the_column(tmp_path):
    path = tmp_path / "b" / "bindings.db"
    path.parent.mkdir()
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE bindings(
      turn_id TEXT PRIMARY KEY, environment TEXT NOT NULL, principal TEXT NOT NULL, origin TEXT NOT NULL,
      seat TEXT NOT NULL, actor TEXT NOT NULL, agent_entry TEXT NOT NULL, session_user TEXT NOT NULL,
      scope TEXT NOT NULL, audience TEXT NOT NULL, owner_request_id TEXT, supersession_key TEXT,
      claim_id TEXT, room_generation TEXT, trigger_seq INTEGER, approval_generation INTEGER,
      source_ref TEXT, runtime_instance TEXT NOT NULL, epoch TEXT NOT NULL, run_id TEXT UNIQUE, expires REAL NOT NULL,
      created REAL NOT NULL, revoked REAL, revoked_reason TEXT);""")
    db.close()
    store = BindingStore(path, "dev")
    assert "read_only" in {row["name"] for row in store._db().execute("PRAGMA table_info(bindings)")}


def test_only_reading_operations_pass_on_a_lookup():
    # cos_tasks reads joined on 10 Nov (0690fcf3, work modes): status, helper results and work lookups change nothing.
    assert tool_listener.READ_OPERATIONS == {"cos_tasks": {"status", "helper_result", "work_find", "work_read"},
                                             "cos_actions": {"list", "get"}, "cos_board": {"read", "list", "result"}}
