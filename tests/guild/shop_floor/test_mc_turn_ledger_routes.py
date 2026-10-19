"""The durable turn ledger wired into the MC routes (issue #346 P1; mc/ledger_gate.py), with a fake
ledger: the real portal, owner guard, CSRF and SQLite floor store, a fake runtime behind the real
OpenClaw adapter. No network, no model call, no database ledger (the real-Postgres store tests are
test_mc_turn_ledger_postgres.py)."""
from __future__ import annotations

import pytest

from minimoi_portal.guild_ui.mc import ledger_gate
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.turn_ledger import Admission, Claim, LedgerUnavailable

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import FakeRuntime, RELAY_TOKEN, _keep, _turn_on

API = "/guild-next/api/v1"


class FakeLedger:
    """Records every call; answers admit() with a scripted result."""

    def __init__(self, result="claimed", barrier=None, earlier=None, down=False):
        self.result, self.barrier, self.earlier, self.down = result, barrier, earlier, down
        self.events, self.finished, self.beats = [], [], 0
        self.next_id = 1

    def admit(self, **kw):
        self.events.append(("admit", kw))
        if self.down:
            raise LedgerUnavailable("unavailable")
        if self.result == "claimed":
            lid = self.next_id
            self.next_id += 1
            return Admission("claimed", entry={"id": lid, **kw}, claim=Claim(lid, "tok", 1))
        if self.result == "paused":
            return Admission("paused", barrier=self.barrier or {"ledger_id": 41, "reason": "unknown"})
        return Admission(self.result, entry=self.earlier or {"id": 40, "state": "answered", "outcome": {"turn_id": "t0"}})

    def held(self, claim, interval_s=30):
        ledger = self

        class Hold:
            def __enter__(self):
                ledger.events.append(("hold", claim.ledger_id))

            def __exit__(self, *a):
                ledger.events.append(("release", claim.ledger_id))
        return Hold()

    def finish(self, claim, state, outcome=None):
        from minimoi_portal.guild_ui.mc.turn_ledger import _clean_outcome
        clean = _clean_outcome(outcome)                    # the real allowlist: anything else raises
        self.events.append(("finish", state))
        self.finished.append((state, clean))
        return True

    def status(self, principal, recent=10):
        return {"running": None, "pause": {"principal": principal, "ledger_id": 41, "reason": "unknown",
                                           "created_at": "2026-10-19T21:00:00+00:00"},
                "recent": [{"id": 41, "conversation_id": "c-1", "note_request_id": "n", "turn_request_id": "t",
                            "kind": "typed", "state": "unknown", "attachment_ids": ["d-0123456789abcdef"],
                            "created_at": "x", "updated_at": "y", "outcome": {"reason": "lease_expired"}}],
                "released": []}

    def continue_after(self, principal, ledger_id):
        self.events.append(("continue", ledger_id))
        if ledger_id != 41:
            return {"result": "not_paused"}
        return {"result": "continued", "ledger_id": 41, "reason": "unknown",
                "message": "Continuing. The earlier message's outcome is still unknown: it may have run, or may still be running."}


def _ledger_on(portal, ledger, on=True):
    ext = portal.app.extensions["guild_ui_next"]
    services = ext["services"]
    services.turn_ledger = ledger
    env = dict(ext.get("feature_environ") or {})
    if on:
        env[ledger_gate.FLAG] = "1"
    else:
        env.pop(ledger_gate.FLAG, None)
    ext["feature_environ"] = env
    return services


def _openclaw(runtime, post=None):
    return OpenClawMasterCraftsman("http://mc-relay:8790/v1", RELAY_TOKEN, http_get=runtime.get,
                                   http_post=post or runtime.post)


def _ask(client, token, note_id, request_id=None):
    import uuid
    request_id = request_id or "req-" + uuid.uuid4().hex     # the in-process duplicate check outlives a test
    return client.post(f"{API}/mc/turns", json={"note_request_id": note_id, "request_id": request_id,
                                                "record_mode": "on_record"}, headers=write_headers(token))


@pytest.fixture
def turned(floored):
    runtime = FakeRuntime()
    _turn_on(floored, _openclaw(runtime))
    floored.extra["runtime"] = runtime
    return floored


def owner(portal):
    client = portal.owner()
    return client, portal.csrf(client)


# ── flag off: nothing changes and the ledger is never touched ─────────────────

def test_flag_off_never_touches_the_ledger(turned):
    ledger = FakeLedger()
    _ledger_on(turned, ledger, on=False)
    client, token = owner(turned)
    body = _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()
    assert body["status"] == "answered" and ledger.events == [] and len(turned.extra["runtime"].sent) == 1


# ── flag on: claimed before dispatch, finished from what is proven ────────────

def test_model_turn_is_claimed_before_dispatch_and_finished_answered(turned):
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    note = _keep(client, token, "q")
    r = _ask(client, token, note["request_id"])
    body = r.get_json()
    assert body.get("status") == "answered" and len(turned.extra["runtime"].sent) == 1, (r.status_code, body, ledger.events)
    kinds = [e[0] for e in ledger.events]
    assert kinds == ["admit", "hold", "finish", "release"]
    admit = ledger.events[0][1]
    assert admit["note_request_id"] == note["request_id"] and admit["turn_request_id"].startswith("req-")
    assert admit["kind"] == "typed" and admit["attachment_ids"] == [] and admit["topic_refs"] == []
    state, outcome = ledger.finished[0]
    assert state == "answered" and outcome["backend_kind"] == "openclaw" and type(outcome["reply_note_id"]) is int
    assert "reply" not in outcome and "message" not in outcome


@pytest.mark.parametrize("result,code,status", [("busy", "busy", 409), ("paused", "paused", 409),
                                                ("running", "already_dispatched", 409), ("done", "already_done", 200)])
def test_refusals_dispatch_nothing(turned, result, code, status):
    ledger = FakeLedger(result=result)
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    r = _ask(client, token, _keep(client, token, "q")["request_id"])
    assert r.status_code == status and r.get_json()["error"] == code
    assert turned.extra["runtime"].sent == [] and ledger.finished == []
    if result == "paused":
        assert r.get_json()["ledger_id"] == 41 and "may still be running" in r.get_json()["message"]
    if result == "done":
        assert r.get_json()["outcome"] == {"turn_id": "t0"}


def test_ledger_down_refuses_and_dispatches_nothing(turned):
    ledger = FakeLedger(down=True)
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    r = _ask(client, token, _keep(client, token, "q")["request_id"])
    assert r.status_code == 503 and r.get_json()["error"] == "ledger_unavailable"
    assert turned.extra["runtime"].sent == []


def test_missing_ledger_service_refuses(turned):
    _ledger_on(turned, None)
    client, token = owner(turned)
    assert _ask(client, token, _keep(client, token, "q")["request_id"]).status_code == 503
    assert turned.extra["runtime"].sent == []


def test_platform_board_command_runs_under_the_claim(turned):
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    r = _ask(client, token, _keep(client, token, 'put "buy milk" on the board')["request_id"])
    assert r.get_json()["backend_kind"] == "platform" and turned.extra["runtime"].sent == []
    state, outcome = ledger.finished[0]
    assert state == "answered" and outcome["operation"]["type"] == "board_postit" and outcome["operation"]["saved"] is True


def test_timeout_after_dispatch_is_unknown(turned):
    class ReadTimeout(Exception):
        pass

    def boom(*_a, **_k):
        raise ReadTimeout()
    _turn_on(turned, _openclaw(turned.extra["runtime"], post=boom))
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    assert _ask(client, token, _keep(client, token, "q")["request_id"]).get_json()["status"] == "timeout_uncertain"
    assert ledger.finished[0][0] == "unknown"


def test_an_error_answer_after_dispatch_is_failed(turned):
    from test_mc_turns import Resp
    _turn_on(turned, _openclaw(FakeRuntime(lambda body, headers: Resp(500, "boom"))))
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    _ask(client, token, _keep(client, token, "q")["request_id"])
    assert ledger.finished[0][0] == "failed"


def test_refusal_before_dispatch_is_not_sent(turned):
    from test_mc_turns import Resp

    class NotReady(FakeRuntime):
        def get(self, url, **kw):
            return Resp(503, '{"ready":false}')
    runtime = NotReady()
    _turn_on(turned, _openclaw(runtime))
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    r = _ask(client, token, _keep(client, token, "q")["request_id"])
    assert r.status_code == 503 and r.get_json()["error"] == "mc_unavailable" and runtime.sent == []
    assert ledger.finished[0][0] == "not_sent"


def test_an_exception_after_dispatch_is_unknown(turned):
    def explode(*_a, **_k):
        raise KeyError("synthetic")
    _turn_on(turned, _openclaw(turned.extra["runtime"], post=explode))
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    _ask(client, token, _keep(client, token, "q")["request_id"])
    assert ledger.finished[0][0] in ("unknown", "failed")       # the adapter maps it; never not_sent


# ── no bypass: Private, direct calls and the probes ───────────────────────────

def test_private_is_refused_while_the_ledger_is_on(turned):
    _ledger_on(turned, FakeLedger())
    client, token = owner(turned)
    r = client.post(f"{API}/mc/private", json={"text": "hi", "session": "s" * 20},
                    headers=write_headers(token, mode="off_record"))
    assert r.status_code in (403, 409) and turned.extra["runtime"].sent == []
    if r.status_code == 409:
        assert r.get_json()["error"] in ("private_unavailable", "private_unsupported")


def test_run_mc_turn_without_a_claim_is_refused_while_the_ledger_is_on(turned):
    from minimoi_portal.guild_ui import api
    services = _ledger_on(turned, FakeLedger())
    with turned.app.test_request_context(f"{API}/mc/turns"):
        from flask import request
        request.blueprint                                       # noqa: B018
    with turned.app.app_context():
        import minimoi_portal.guild_ui as gui
        real = gui.feature_environ
        gui.feature_environ = lambda: {ledger_gate.FLAG: "1"}
        try:
            body, status = api.run_mc_turn(services, conversations=None, floor=None, conv={"id": "c"},
                                           principal="p", note={"text": "x"}, note_id="n")
        finally:
            gui.feature_environ = real
    assert status == 409 and body["error"] == "ledger_required" and turned.extra["runtime"].sent == []


# ── the owner's view and Continue ─────────────────────────────────────────────

def test_ledger_view_is_content_free_and_off_when_disabled(turned):
    ledger = FakeLedger()
    client, token = owner(turned)
    _ledger_on(turned, ledger, on=False)
    assert client.get(f"{API}/mc/turns/ledger").status_code == 404
    _ledger_on(turned, ledger)
    body = client.get(f"{API}/mc/turns/ledger").get_json()
    assert body["pause"]["ledger_id"] == 41 and body["recent"][0]["state"] == "unknown"
    assert "attachment_ids" not in body["recent"][0] and "turn_request_id" not in body["recent"][0]


def test_continue_needs_the_write_guard_and_the_matching_entry(turned):
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    assert client.post(f"{API}/mc/turns/continue", json={"ledger_id": 41}).status_code in (400, 403)   # no CSRF
    assert client.post(f"{API}/mc/turns/continue", json={"ledger_id": "41"}, headers=write_headers(token)).status_code == 422
    assert client.post(f"{API}/mc/turns/continue", json={"ledger_id": 7}, headers=write_headers(token)).status_code == 409
    r = client.post(f"{API}/mc/turns/continue", json={"ledger_id": 41}, headers=write_headers(token))
    assert r.status_code == 200 and "may still be running" in r.get_json()["message"]


# ── Turn's mapping, including the stream end ──────────────────────────────────

def _turn():
    ledger = FakeLedger()
    return ledger_gate.Turn(ledger, ledger.admit(principal="p", conversation_id="c", note_request_id="n",
                                                 turn_request_id="t", kind="typed")), ledger


@pytest.mark.parametrize("args,state", [
    (("not_started", None, "openclaw", False), "not_sent"),
    (("answered", None, "openclaw", True), "answered"),
    (("timeout_uncertain", "local_timeout", "openclaw", True), "unknown"),
    (("interrupted", "truncated", "openclaw", True), "unknown"),
    (("error", "upstream", "openclaw", True), "unknown"),
    (("error", "runtime_error", "openclaw", True), "failed"),
    (("error", "not_kept", "openclaw", True), "failed"),
])
def test_stream_end_mapping(args, state):
    turn, ledger = _turn()
    if args[3]:
        turn.mark_started()
    turn.finish_stream(*args)
    assert ledger.finished[0][0] == state and ledger.events[-1][0] == "release"


def test_stopped_stream_is_failed_only_when_the_runtime_acknowledged():
    turn, ledger = _turn()
    turn.mark_started()
    turn.finish_stream("stopped", "stopped", "openclaw", True, True)
    other, ledger2 = _turn()
    other.mark_started()
    other.finish_stream("stopped", "stopped", "openclaw", True, None)
    assert ledger.finished[0][0] == "failed" and ledger2.finished[0][0] == "unknown"


def test_not_sent_cannot_be_claimed_after_work_started():
    turn, _ = _turn()
    turn.mark_started()
    with pytest.raises(RuntimeError):
        turn.finish_not_sent()


def test_finish_happens_once():
    turn, ledger = _turn()
    turn.finish_platform({"status": "answered", "backend_kind": "platform"})
    turn.finish_unknown()
    assert [s for s, _ in ledger.finished] == ["answered"]


def test_outcome_mapping_drops_text_and_keeps_ids():
    body = {"turn_id": "f375654ea2ae485dbcff5de50eb3b89a", "status": "answered", "backend_kind": "cos",
            "message": "Saved Board post-it #4: buy milk", "reply_note": {"id": 7, "text": "secret words"},
            "operation": {"type": "board_postit", "saved": True, "id": 4, "text": "buy milk", "operation_id": "x"},
            "failure_class": "Not A Code"}
    out = ledger_gate.outcome_of(body)
    assert out == {"turn_id": "f375654ea2ae485dbcff5de50eb3b89a", "backend_kind": "cos", "status": "answered",
                   "reply_note_id": 7, "operation": {"type": "board_postit", "saved": True, "id": 4}}


def test_stream_run_calls_on_end_once_when_abandoned_before_start():
    from minimoi_portal.guild_ui.mc.streaming import StreamContext, StreamRun
    calls = []
    ctx = StreamContext(turn_id="t" * 32, principal="p", backend=type("B", (), {"kind": "openclaw"})(), events=iter(()),
                        floor=None, reply_key="r", context={}, release=lambda: calls.append("release"),
                        on_end=lambda *a: calls.append(("end",) + a))
    run = StreamRun(ctx, record_usage=lambda **k: None)
    run.abandon_if_not_started()
    run.abandon_if_not_started()
    assert calls == [("end", "not_started", None, "openclaw", False, None), "release"]


# ── platform actions: classified by what their receipt proves (typed, stream, voice) ──

class FakeCos:
    """A Chief of Staff backend whose note/reminder operation answers with a scripted receipt."""
    kind, display_name, accepts_files, supports_streaming = "cos", "Chief of Staff", True, True

    def __init__(self, receipt):
        self.receipt, self.calls = receipt, []

    def operation(self, text, *, conversation_id, request_id):
        self.calls.append(text)
        return self.receipt


def _cos(portal, receipt):
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc = FakeCos(receipt)
    return services.mc


@pytest.mark.parametrize("path", ["/mc/turns", "/mc/turns/stream"])
@pytest.mark.parametrize("receipt,state", [({"state": "uncertain"}, "unknown"),
                                           ({"state": "refused", "reply": "No."}, "failed"),
                                           ({"state": "failed"}, "failed")])
def test_unconfirmed_or_failed_note_after_sending_pauses(turned, path, receipt, state):
    cos = _cos(turned, receipt)
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    note = _keep(client, token, "Make a note that the spare key is in the blue box")
    r = client.post(f"{API}{path}", json={"note_request_id": note["request_id"], "request_id": "req-" + note["request_id"][-12:],
                                          "record_mode": "on_record"}, headers=write_headers(token))
    body = r.get_json()
    assert body["backend_kind"] == "platform" and cos.calls and turned.extra["runtime"].sent == []
    assert ledger.finished[0][0] == state
    assert ledger.finished[0][1]["operation"]["state"] == receipt["state"]


def test_reminder_without_a_time_is_a_safe_clarification(turned):
    cos = _cos(turned, {"state": "ok"})
    ledger = FakeLedger()
    _ledger_on(turned, ledger)
    client, token = owner(turned)
    r = _ask(client, token, _keep(client, token, "Remind me to stretch")["request_id"])
    assert r.get_json()["backend_kind"] == "platform" and cos.calls == []
    assert ledger.finished[0][0] == "answered"


@pytest.mark.parametrize("op,state", [({"type": "cos_note", "saved": False, "state": "uncertain"}, "unknown"),
                                      ({"type": "library_intake", "saved": False, "state": "uncertain"}, "unknown"),
                                      ({"type": "reminder_set", "saved": False, "state": "refused"}, "failed"),
                                      ({"type": "library_intake", "saved": False, "state": "not_sent"}, "answered"),
                                      ({"type": "board_postit", "saved": False, "reason": "needs_text"}, "answered"),
                                      ({"type": "board_postit", "saved": True, "id": 3}, "answered")])
def test_platform_receipt_classification(op, state):
    turn, ledger = _turn()
    turn.finish_platform({"status": "answered", "backend_kind": "platform", "operation": op})
    assert ledger.finished[0][0] == state


# ── the Library seam says whether an unconfirmed filing had been sent ─────────

def test_library_failure_before_sending_is_not_sent():
    from minimoi_portal.guild_ui import mc_library
    out = mc_library.host_command("save this to library: https://example.com/a", environ={}, principal="p",
                                  origin="http://localhost", conversation={}, note_id="n",
                                  sender=lambda *a: pytest.fail("not configured: nothing may be sent"))
    assert out["status"] == "unconfirmed" and out["operation"]["state"] == "not_sent"


def test_library_failure_after_sending_is_uncertain(monkeypatch):
    from minimoi_portal.guild_ui import mc_library
    profile = type("P", (), {"owner": "p", "origin": "http://localhost"})()
    monkeypatch.setattr(mc_library.transport, "configured", lambda environ: profile)
    monkeypatch.setattr(mc_library.transport, "payload", lambda *a: {"synthetic": True})

    def timeout(*a):
        raise TimeoutError()
    conv = {"id": "c-1", "principal": "p", "record_mode": "on_record", "archived": False}
    out = mc_library.host_command("save this to library: https://example.com/a", environ={}, principal="p",
                                  origin="http://localhost", conversation=conv, note_id="n", sender=timeout)
    assert out["operation"]["state"] == "uncertain"
    out = mc_library.host_command("save this to library: https://example.com/a", environ={}, principal="p",
                                  origin="http://localhost", conversation=conv, note_id="n", sender=lambda *a: {"result": "?"})
    assert out["operation"]["state"] == "uncertain"


# ── the operator probe's real entry refuses from the mount's own settings ─────

@pytest.mark.parametrize("argv", [["--yes-spend"], ["--yes-spend", "--stream"]])
def test_probe_entry_refuses_while_the_mount_has_the_ledger_on(monkeypatch, argv, capsys):
    import sys
    import types
    from minimoi_portal import mc_cost_probe
    created = []
    services = types.SimpleNamespace(mc_turns=True, mc=types.SimpleNamespace(kind="openclaw"), mc_stream=True)
    fake_app = types.SimpleNamespace(app=types.SimpleNamespace(extensions={"guild_ui_next": {
        "services": services, "feature_environ": {ledger_gate.FLAG: "1"}}}))
    monkeypatch.setitem(sys.modules, "minimoi_portal.app", fake_app)
    import minimoi_portal
    monkeypatch.setattr(minimoi_portal, "app", fake_app, raising=False)
    from minimoi_portal.guild_ui.mc import cost_probe
    monkeypatch.setattr(cost_probe, "run", lambda *a, **k: created.append("run"))
    assert mc_cost_probe.main(argv) == 5 and created == []
    assert "turn ledger is on" in capsys.readouterr().out


def test_probe_entry_unchanged_when_the_ledger_is_off(monkeypatch):
    import sys
    import types
    from minimoi_portal import mc_cost_probe
    services = types.SimpleNamespace(mc_turns=False, mc=types.SimpleNamespace(kind="openclaw"))
    fake_app = types.SimpleNamespace(app=types.SimpleNamespace(extensions={"guild_ui_next": {
        "services": services, "feature_environ": {}}}))
    monkeypatch.setitem(sys.modules, "minimoi_portal.app", fake_app)
    import minimoi_portal
    monkeypatch.setattr(minimoi_portal, "app", fake_app, raising=False)
    assert mc_cost_probe.main(["--yes-spend"]) == 3          # the probe's own next refusal: turns off
