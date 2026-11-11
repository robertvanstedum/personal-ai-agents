"""F2 step 1: the CoS binding store and resolver. F06 negative cases: no binding; expired, revoked or superseded;
another turn's run; replaced runtime; restarted scheduler; Stop before the binding; quota; revoke versus admit."""
import os
import stat
import threading
import json
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import pytest

from domains.cos.binding import BindingStore, NotBound, dumps_trace
from domains.cos.store import actions_v2
from domains.cos.store.writer import StoreWriter

RUN = "chatcmpl_{:08x}-0000-0000-0000-000000000000"
INST = "a" * 32
INST2 = "b" * 32
USER = "cos-confer:" + "0" * 32
SIGNER = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(SIGNER.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = BindingStore(tmp_path / "private" / "bindings.sqlite3", "dev", clock=clock)
    s.start_epoch("epoch-1")
    s.register_instance(INST, public_key=PUBLIC)
    return s


def owner_turn(store, turn="t1", run=1, key="dev:owner", **kw):
    assert store.record(turn, principal="robert", origin="owner", scope="owner", audience="owner",
                        agent_entry="cos-agent-a", session_user=USER, lifetime_s=600, owner_request_id="req-" + turn,
                        supersession_key=key, **kw)
    assert store.attach_run(turn, RUN.format(run))


def resolve(store, run=1, call="call-1", instance=INST, agent="cos-agent-a", user=USER, wait_s=0):
    return store.resolve(run_id=RUN.format(run), tool_call_id=call, instance_id=instance, agent_id=agent,
                         session_user=user, wait_s=wait_s)


def admit(store, binding, call="call-1", tool="cos_actions", limit=5):
    return store.admit(binding, tool=tool, tool_call_id=call, instance_id=INST, session_user=USER, limit=limit)


def code(fn):
    with pytest.raises(NotBound) as err:
        fn()
    return err.value.code


def test_file_is_private(tmp_path, store):
    path = tmp_path / "private" / "bindings.sqlite3"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def terminal_receipt(**changes):
    value = {"instance": INST, "run": RUN.format(1), "session": f"agent:cos-agent-a:openai-user:{USER}",
            "agent": "cos-agent-a", "success": True, "model_calls": 1, "tool_calls": 0, **changes}
    message = json.dumps([value[k] for k in ("instance", "run", "session", "agent", "success", "model_calls", "tool_calls")],
                         separators=(",", ":")).encode()
    value["signature"] = base64.b64encode(SIGNER.sign(message)).decode()
    return value


def test_agent_writable_receipt_cannot_forge_zero_tools(store):
    owner_turn(store)
    forged = terminal_receipt(tool_calls=1)
    forged["tool_calls"] = 0
    assert not store.record_terminal(forged)
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)


def test_runtime_key_cannot_be_replaced_on_existing_instance(store):
    attacker = Ed25519PrivateKey.generate()
    key = base64.b64encode(attacker.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
    assert code(lambda: store.register_instance(INST, public_key=key)) == "instance_key_changed"


def test_depth_retires_quick_before_deep_and_cannot_dispatch_again(store):
    owner_turn(store)
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)
    assert store.record_terminal(terminal_receipt())
    assert store.prepare_depth("t1", RUN.format(1), "c" * 32)
    assert code(lambda: resolve(store)) == "run_unattached"
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)
    assert not store.attach_depth("t1", RUN.format(1))
    assert store.attach_depth("t1", RUN.format(2))
    assert resolve(store, run=2).turn_id == "t1"
    assert code(lambda: resolve(store)) == "run_unattached"


@pytest.mark.parametrize("changes", [{"success": False}, {"tool_calls": 1}, {"model_calls": 2}])
def test_depth_refuses_incomplete_or_used_attempt(store, changes):
    owner_turn(store)
    assert store.record_terminal(terminal_receipt(**changes))
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)


def test_stop_after_preparation_prevents_deep_binding(store):
    owner_turn(store)
    store.record_terminal(terminal_receipt())
    assert store.prepare_depth("t1", RUN.format(1), "c" * 32)
    store.revoke("t1")
    assert code(lambda: store.attach_depth("t1", RUN.format(2))) == "stopped"


def test_conflicting_terminal_receipt_invalidates_permission(store):
    owner_turn(store)
    assert store.record_terminal(terminal_receipt())
    assert not store.record_terminal(terminal_receipt(tool_calls=1))
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)


def test_recorded_action_wins_over_zero_tool_receipt(store):
    owner_turn(store)
    admit(store, resolve(store))
    store.record_terminal(terminal_receipt())
    assert not store.prepare_depth("t1", RUN.format(1), "c" * 32)


def test_live_turn_resolves_and_admits_with_identity_from_the_binding(store):
    owner_turn(store)
    b = resolve(store)
    assert (b.turn_id, b.principal, b.origin, b.scope, b.owner_request_id) == ("t1", "robert", "owner", "owner", "req-t1")
    call = admit(store, b)
    assert call.scope == "owner" and call.call_id == RUN.format(1) + ":call-1" and call.environment == "dev"


def test_no_binding_or_unattached_run(store):
    assert code(lambda: resolve(store)) == "run_unattached"
    assert store.record("t1", principal="robert", origin="owner", scope="owner", audience="owner",
                        agent_entry="cos-agent-a", session_user=USER, lifetime_s=600)
    assert code(lambda: resolve(store)) == "run_unattached"
    assert code(lambda: resolve(store, call="")) == "run_missing"
    assert code(lambda: store.resolve(run_id="forged", tool_call_id="c", instance_id=INST, agent_id="cos-agent-a",
                                      session_user=USER, wait_s=0)) == "run_missing"


def test_waits_for_a_late_attach_without_blocking_writers(store):
    assert store.record("t1", principal="robert", origin="owner", scope="owner", audience="owner",
                        agent_entry="cos-agent-a", session_user=USER, lifetime_s=600)
    timer = threading.Timer(0.2, lambda: store.attach_run("t1", RUN.format(1)))
    timer.start()
    try:
        assert resolve(store, wait_s=2).turn_id == "t1"
    finally:
        timer.join()


def test_expired(store, clock):
    owner_turn(store)
    clock.t += 601
    assert code(lambda: resolve(store)) == "expired"


def test_stop_revokes_and_a_stop_before_the_binding_applies_from_the_start(store):
    owner_turn(store)
    b = resolve(store)
    assert store.revoke("t1")
    assert code(lambda: resolve(store)) == "stopped"
    assert code(lambda: admit(store, b)) == "stopped"
    assert store.revoke("t2") is False
    assert not store.record("t2", principal="robert", origin="owner", scope="owner", audience="owner",
                            agent_entry="cos-agent-a", session_user=USER, lifetime_s=600)
    store.attach_run("t2", RUN.format(2))
    assert code(lambda: resolve(store, run=2)) == "stopped"


def test_supersession_revokes_the_older_turn_only(store):
    owner_turn(store, "t1", 1, key="dev:owner")
    owner_turn(store, "t3", 3, key="dev:telegram-1")
    owner_turn(store, "t2", 2, key="dev:owner")
    assert code(lambda: resolve(store, run=1)) == "superseded"
    assert resolve(store, run=2).turn_id == "t2"
    assert resolve(store, run=3).turn_id == "t3"


def test_another_turns_run_session_or_agent_is_refused(store):
    owner_turn(store, "t1", 1)
    owner_turn(store, "t2", 2, key="dev:other")
    assert code(lambda: resolve(store, run=1, user="cos-confer:" + "1" * 32)) == "session_invalid"
    assert code(lambda: resolve(store, run=1, agent="cos-meeting")) == "session_invalid"
    assert store.attach_run("t2", RUN.format(1)) is False        # a run never moves or is shared
    assert store.attach_run("t1", RUN.format(9)) is False
    b1 = resolve(store, run=1)
    store.end("t1")
    assert code(lambda: admit(store, b1)) == "turn_ended"         # late call from an ended turn, while t2 is live
    assert resolve(store, run=2).turn_id == "t2"


def test_runtime_replacement_and_retired_instance(store):
    owner_turn(store)
    assert code(lambda: resolve(store, instance=INST2)) == "runtime_replaced"
    assert store.register_instance(INST2)
    assert code(lambda: resolve(store, instance=INST2)) == "runtime_replaced"
    assert code(lambda: store.register_instance(INST)) == "instance_retired"
    assert store.register_instance(INST2) is False


def test_scheduler_restart_revokes_old_epoch(store):
    owner_turn(store)
    store.start_epoch("epoch-2")
    assert code(lambda: resolve(store)) == "scheduler_restarted"


def test_nothing_is_live_before_an_instance_and_epoch(tmp_path, clock):
    s = BindingStore(tmp_path / "b.sqlite3", "dev", clock=clock)
    assert not s.record("t1", principal="robert", origin="owner", scope="owner", audience="owner",
                        agent_entry="cos-agent-a", session_user=USER, lifetime_s=600)


def test_quota_and_retried_call_is_one_admission_with_one_time(store, clock):
    owner_turn(store)
    b = resolve(store)
    first = admit(store, b, "c1", limit=2)
    clock.t += 5
    assert admit(store, b, "c1", limit=2).at == first.at
    admit(store, b, "c2", limit=2)
    assert code(lambda: admit(store, b, "c3", limit=2)) == "quota_exceeded"
    assert code(lambda: admit(store, b, "c1", tool="cos_board", limit=2)) == "call_reused"


def test_meeting_binding_needs_claim_and_generation(store):
    base = dict(principal="robert", origin="meeting", scope="meeting", audience="room-1", agent_entry="cos-meeting",
                session_user=USER, lifetime_s=600)
    assert code(lambda: store.record("m1", **base)) == "turn_invalid"
    assert code(lambda: store.record("m1", **dict(base, origin="owner"), claim_id="c", room_generation="g1")) == \
        "turn_invalid"
    assert store.record("m1", **base, claim_id="claim-1", room_generation="g4", trigger_seq=9)
    store.attach_run("m1", RUN.format(7))
    b = resolve(store, run=7, agent="cos-meeting")
    assert (b.scope, b.claim_id, b.room_generation, b.trigger_seq) == ("meeting", "claim-1", "g4", 9)


def test_revoke_versus_admit_is_atomic(store):
    owner_turn(store)
    b = resolve(store)
    results = []
    barrier = threading.Barrier(2)

    def admitter():
        barrier.wait()
        for n in range(50):
            try:
                admit(store, b, f"c{n}", limit=100)
                results.append(("admitted", n))
            except NotBound as err:
                results.append((err.code, n))

    def stopper():
        barrier.wait()
        store.revoke("t1")

    threads = [threading.Thread(target=admitter), threading.Thread(target=stopper)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    seen_stop = False
    for outcome, _ in results:
        if outcome == "stopped":
            seen_stop = True
        assert not (seen_stop and outcome == "admitted")      # nothing is admitted after the Stop committed
    assert code(lambda: admit(store, b, "late", limit=100)) == "stopped"


def test_native_calls_are_logged_never_refused_and_the_trace_links_receipts(store, tmp_path):
    owner_turn(store)
    assert store.log_native(run_id=RUN.format(1), tool_call_id="n1", tool="exec", target="python3 make_deck.py")
    assert store.log_native(run_id=RUN.format(99), tool_call_id="n2", tool="write", target="/x") is False
    assert store.log_native(run_id=None, tool_call_id=None, tool="", target=None) is False
    b = resolve(store)
    call = admit(store, b)
    writer = StoreWriter(tmp_path / "store", "dev")
    out = actions_v2.serve(writer, call, "open", {"title": "Deck"})
    store.record_receipt(b, "call-1", command_id="cmd-cos-v2-" + call.ref[3:],
                         receipt_id=out["body"]["result"]["receipt_id"])
    trace = store.trace("t1")
    assert trace["calls"][0]["receipt_id"] == out["body"]["result"]["receipt_id"]
    assert [n["tool"] for n in trace["native_calls"]] == ["exec"]
    assert "make_deck" in dumps_trace(trace)


def test_invalid_records_are_refused(store):
    good = dict(principal="robert", origin="owner", scope="owner", audience="owner", agent_entry="cos-agent-a",
                session_user=USER, lifetime_s=600)
    for bad in (dict(origin="robert"), dict(scope="admin"), dict(agent_entry="Bad Agent"), dict(lifetime_s=0),
                dict(lifetime_s=99999), dict(audience=""), dict(session_user="")):
        assert code(lambda: store.record("t9", **dict(good, **bad))) == "turn_invalid"
    assert code(lambda: store.record("bad id!", **good)) == "turn_invalid"


def test_starting_leaves_an_existing_folders_mode_and_tightens_only_its_own_file(tmp_path):
    """The store's root belongs to the deploy (cos_prod.sh prepare): a scheduler start must not change it."""
    import os
    import stat
    from domains.cos.binding import BindingStore
    folder = tmp_path / "store"
    folder.mkdir()
    folder.chmod(0o750)
    BindingStore(folder / "tool-bindings.sqlite3", "prod")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o750
    assert stat.S_IMODE((folder / "tool-bindings.sqlite3").stat().st_mode) == 0o600
    new = tmp_path / "made-here" / "tool-bindings.sqlite3"
    BindingStore(new, "prod")
    assert stat.S_IMODE(os.stat(new.parent).st_mode) == 0o700
