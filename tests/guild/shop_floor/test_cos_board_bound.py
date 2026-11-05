"""F2 M2: CoS's Board, bound. The sealed plugin call -> bound listener (binding, admission, quota) -> the scheduler's
Board key -> the real signed Board API -> CoS's own floor. Replaces the old agent-held-key test (cos-board plugin).

What it proves:
- a live owner turn saves, lists, reads, updates, completes and undoes notes, each change with a verified receipt
  linked to the turn in the binding trace; MC's floor is untouched;
- a host retry of the same call is the same Board operation (replayed, one note); the same call id with different
  arguments is never applied twice;
- a lost or tampered reply is ``unknown`` with the operation id, and ``result`` recovers it, never a second note;
- arguments can't name another principal or Board, oversized text is refused before sending, a revoked key never
  reports saved;
- a meeting turn, a stopped turn, an unsealed call and an agent-held key (no binding) get nothing;
- the profile file must be owner-only.
"""
import hashlib
import json
import os
from dataclasses import replace
from threading import Thread

import pytest
from werkzeug.serving import make_server

from core.domain_api.auth import Authenticator, KeyRegistry
from core.domain_api.contract import KeyProfile
from domains.cos.binding import BindingStore
from domains.cos.board_tool import CosBoardTool, http_exchange, load_profile, operation_id
from domains.cos.store.writer import StoreWriter
from domains.cos.tool_listener import create_app as listener_app
from minimoi_portal.guild_ui.board_actions import BoardActions, BoardContext
from minimoi_portal.guild_ui.board_headless import create_app
from test_board_actions import board_case, CAPS  # noqa: F401  (fixture)

TOKEN = "t" * 40
RUN = "chatcmpl_{:08x}-0000-0000-0000-000000000000"
INST = "a" * 32
USER = "cos-confer:" + "0" * 32
SECRET = b"a" * 32


def _profile_file(tmp_path):
    value = dict(key_id="cos-test", principal="test-owner", actor="cos", domain="guild", environment="dev",
                 board="cos-board", secret_hex=SECRET.hex())
    path = tmp_path / "secrets" / ("cos-board-profile-%d.json" % len(list(tmp_path.glob("secrets/*"))))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value))
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def rig(board_case, tmp_path):
    _, source, _, _ = board_case
    store, mc = source.for_floor("cos-board"), source.for_floor("guild")
    key = KeyProfile("cos-test", "test-owner", "cos", "dev", "guild", CAPS | {"guild.board.capabilities.read"}, SECRET)
    registry = KeyRegistry([key])
    board = create_app(store=store, owner="test-owner", environment="dev", cursor_key=b"b" * 32, registry=registry,
                       authenticator=Authenticator(registry.lookup, domain="guild", environment="dev"))
    server = make_server("127.0.0.1", 0, board, threaded=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    real = http_exchange(f"http://127.0.0.1:{server.server_port}")
    faults = {"mode": None}

    def exchange(method, route, raw, headers):
        status, body, signature = real(method, route, raw, headers)
        if faults["mode"] == "lost":
            raise ConnectionError("reply lost after the Board committed")
        if faults["mode"] == "signature":
            signature = "0" * 64
        return status, body, signature

    profile_path = {"path": _profile_file(tmp_path)}
    tool = CosBoardTool(profile=lambda: load_profile(profile_path["path"]), exchange=exchange)
    bindings = BindingStore(tmp_path / "b" / "bindings.sqlite3", "dev")
    bindings.start_epoch("e1")
    app = listener_app(bindings=bindings, writer=StoreWriter(tmp_path / "store", "dev"),
                       token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(), wait_s=0, board_tool=tool)
    client = app.test_client()
    assert client.post("/internal/cos/v1/instance", json={"instance": INST},
                       headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
    try:
        yield dict(client=client, bindings=bindings, store=store, mc=mc, registry=registry, key=key, faults=faults,
                   profile_path=profile_path, tmp=tmp_path, tool=tool)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def live(bindings, turn="t1", run=1, scope="owner"):
    extra = {} if scope == "owner" else {"claim_id": "c1", "room_generation": "g1"}
    agent = "cos-agent-a" if scope == "owner" else "cos-meeting"
    assert bindings.record(turn, principal="robert", origin="owner" if scope == "owner" else "meeting", scope=scope,
                           audience="owner", agent_entry=agent, session_user=USER, lifetime_s=600,
                           owner_request_id="req-1", supersession_key="dev:owner:" + turn, **extra)
    assert bindings.attach_run(turn, RUN.format(run))


def board(rig, args, call, run=1, agent="cos-agent-a"):
    headers = {"Authorization": "Bearer " + TOKEN, "X-CoS-Session": f"agent:{agent}:openai-user:{USER}",
               "X-CoS-Agent": agent, "X-CoS-Run": RUN.format(run), "X-CoS-Tool-Call": call, "X-CoS-Instance": INST}
    return rig["client"].post("/internal/cos/v1/tools/cos_board", json={"arguments": args}, headers=headers).json


def test_owner_turn_save_read_update_done_undo_with_receipts_linked_to_the_turn(rig):
    live(rig["bindings"])
    first = board(rig, dict(operation="save", text="Préparer le résumé — Danke 😀", label="followup"), "c-save")
    assert first["status"] == "done" and first["data"]["state"] == "verified", first
    data = first["data"]
    note, op = data["current"]["id"], data["receipt"]["operation_id"]
    assert data["receipt"]["executor"] == "cos" and data["receipt"]["board"] == "cos-board"
    assert op == data["operation_id"]
    listed = board(rig, dict(operation="list", query="résumé"), "c-list")["data"]
    assert [x["id"] for x in listed["notes"]] == [note]
    assert board(rig, dict(operation="read", note_ref=note), "c-read")["data"]["semantic_revision"] == 1
    done = board(rig, dict(operation="update", note_ref=note, expected_revision=1, changes={"done": True}), "c-done")
    assert done["data"]["state"] == "verified" and done["data"]["current"]["state"] == "done"
    stale = board(rig, dict(operation="update", note_ref=note, expected_revision=1, changes={"text": "Stale"}), "c-st")
    assert stale["status"] == "refused" and stale["data"]["code"] == "revision_conflict"
    undo = board(rig, dict(operation="undo", operation_ref=done["data"]["receipt"]["operation_id"],
                           expected_revision=2), "c-undo")
    assert undo["data"]["state"] == "verified" and undo["data"]["current"]["state"] == "active"
    recovered = board(rig, dict(operation="result", operation_ref=op), "c-res")["data"]
    assert recovered["receipt"] == data["receipt"]
    # Every change traces to the turn: its admitted call carries the Board operation id.
    calls = {c["tool_call_id"]: c for c in rig["bindings"].trace("t1")["calls"]}
    assert calls["c-save"]["command_id"] == op and calls["c-save"]["receipt_id"] == data["receipt"]["target_ref"]
    assert calls["c-done"]["command_id"] == done["data"]["operation_id"]
    assert calls["c-list"]["command_id"] is None                        # reads change nothing and link nothing
    # MC's floor never sees CoS's notes.
    svc = BoardActions(rig["mc"], owner="test-owner", environment="dev", cursor_key=b"b" * 32)
    assert svc.query(BoardContext("test-owner", "dev", "guild", "mc", "test", CAPS))["notes"] == []


def test_host_retry_is_the_same_operation_and_a_reused_call_id_never_applies_twice(rig):
    live(rig["bindings"])
    args = dict(operation="save", text="One note, once")
    first = board(rig, args, "c1")["data"]
    again = board(rig, args, "c1")["data"]
    assert again["state"] == "verified" and again["replayed"] and again["operation_id"] == first["operation_id"]
    changed = board(rig, dict(operation="save", text="different words"), "c1")
    assert changed["data"].get("state") != "verified"
    assert len(board(rig, dict(operation="list"), "c-list")["data"]["notes"]) == 1


def test_lost_or_tampered_reply_is_unknown_and_result_recovers_without_a_second_note(rig):
    live(rig["bindings"])
    rig["faults"]["mode"] = "lost"
    lost = board(rig, dict(operation="save", text="Saved, reply lost"), "c1")
    assert lost["status"] == "unknown" and lost["data"]["state"] == "unknown" and lost["data"]["operation_id"]
    rig["faults"]["mode"] = "signature"
    bad = board(rig, dict(operation="save", text="Saved, reply tampered"), "c2")
    assert bad["status"] == "unknown"
    rig["faults"]["mode"] = None
    for out in (lost, bad):
        rec = board(rig, dict(operation="result", operation_ref=out["data"]["operation_id"]), "r-" + out["data"]["operation_id"][:8])
        assert rec["data"]["state"] == "verified"
    assert board(rig, dict(operation="save", text="Saved, reply lost"), "c1")["data"]["replayed"] is True
    assert len(board(rig, dict(operation="list"), "c-list")["data"]["notes"]) == 2


def test_arguments_cannot_name_identity_and_bad_values_are_refused_before_sending(rig):
    live(rig["bindings"])
    sent = []
    real = rig["tool"]._exchange
    rig["tool"]._exchange = lambda *a: (sent.append(a), real(*a))[1]
    for args in (dict(operation="save", text="no", principal="somebody"),
                 dict(operation="save", text="no", board="guild"),
                 dict(operation="save", text="x" * 281),
                 dict(operation="save", text="   "),
                 dict(operation="update", note_ref="1", expected_revision=0, changes={"done": True}),
                 dict(operation="read", note_ref="../etc"),
                 dict(operation="undo", operation_ref="not-a-uuid", expected_revision=1),
                 dict(operation="delete", note_ref="1")):
        out = board(rig, args, "c-" + hashlib.sha256(json.dumps(args).encode()).hexdigest()[:12])
        assert out["status"] == "refused", (args, out)
    assert sent == []


def test_revoked_key_never_reports_saved(rig):
    live(rig["bindings"])
    rig["registry"].replace([replace(rig["key"], enabled=False)])
    out = board(rig, dict(operation="save", text="no"), "c1")
    assert out["status"] != "done" and out["data"].get("state") != "verified"


def test_meeting_stopped_unsealed_and_unbound_calls_get_nothing(rig):
    live(rig["bindings"], "m1", 5, scope="meeting")
    out = board(rig, dict(operation="list"), "c1", run=5, agent="cos-meeting")
    assert out["status"] == "refused" and out["code"] == "scope_not_allowed"
    live(rig["bindings"], "t1", 1)
    rig["bindings"].revoke("t1")
    assert board(rig, dict(operation="save", text="after stop"), "c2")["code"] == "stopped"
    assert board(rig, dict(operation="save", text="no run"), "c3", run=9)["status"] == "refused"
    # A run with no binding gets nothing, reads included.
    assert board(rig, dict(operation="list"), "c4", run=9)["status"] == "refused"


def test_listener_without_a_board_tool_refuses_and_profile_must_be_private(rig, tmp_path):
    bindings = BindingStore(tmp_path / "x" / "b.sqlite3", "dev")
    bindings.start_epoch("e")
    app = listener_app(bindings=bindings, writer=StoreWriter(tmp_path / "s2", "dev"),
                       token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(), wait_s=0)
    out = app.test_client().post("/internal/cos/v1/tools/cos_board", json={"arguments": {"operation": "list"}},
                                 headers={"Authorization": "Bearer " + TOKEN}).json
    assert out["code"] == "tool_unavailable"
    path = _profile_file(tmp_path)
    os.chmod(path, 0o644)
    with pytest.raises(ValueError):
        load_profile(path)
    for bad in (dict(board="guild"), dict(actor="mc")):
        with pytest.raises(ValueError):
            load_profile(_bad(tmp_path, bad))


def _bad(tmp_path, over):
    value = dict(key_id="cos-test", principal="test-owner", actor="cos", domain="guild", environment="dev",
                 board="cos-board", secret_hex=SECRET.hex())
    value.update(over)
    path = tmp_path / ("bad-%s.json" % "-".join(over))
    path.write_text(json.dumps(value))
    os.chmod(path, 0o600)
    return path


def test_operation_id_is_stable_per_call_and_distinct_across_calls(tmp_path):
    p = load_profile(_profile_file(tmp_path))
    a, b = operation_id(p, "tc-" + "1" * 40), operation_id(p, "tc-" + "2" * 40)
    assert a == operation_id(p, "tc-" + "1" * 40) and a != b and a[14] == "8"
