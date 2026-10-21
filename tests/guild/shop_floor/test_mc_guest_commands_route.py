"""The guest owner commands on the real MC turn route (journey link 4 wiring): gated by flag,
answered by the platform with no model call, and an ordinary 'Confirm' still reaches MC."""
import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turn_ledger_routes import turned, owner, _keep, _ask  # noqa: F401
from core.mc_domain_actions import guest_commands as gc


class FakeCommands:
    calls = []
    pending = False

    def __init__(self, connect, *, principal, environment):
        FakeCommands.env = environment

    def has_pending(self, conversation):
        return FakeCommands.pending

    def handle(self, command, *, conversation, turn_key):
        FakeCommands.calls.append(command.kind)
        return {"reply": f"platform:{command.kind}", "operation": {"type": "guest_action", "state": "proposed"}}


@pytest.fixture
def guests(turned, monkeypatch):
    FakeCommands.calls, FakeCommands.pending = [], False
    monkeypatch.setattr(gc, "GuestCommands", FakeCommands)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    ext = turned.app.extensions["guild_ui_next"]                       # a copy, restored after the test
    monkeypatch.setitem(ext, "feature_environ", {**(ext.get("feature_environ") or {}),
                                                 "MINIMOI_MC_GUEST_ACTIONS": "1", "MINIMOI_MC_GUEST_ACTIONS_ENV": "dev"})
    return turned


def say(app, text):
    c, t = owner(app)
    n = _keep(c, t, text)["request_id"]
    return _ask(c, t, n).get_json()


def test_reject_is_answered_by_the_platform_without_a_model_call(guests):
    sent = len(guests.extra["runtime"].sent)
    body = say(guests, "Reject the guest request from Bruno")
    assert body["backend_kind"] == "platform" and body["message"] == "platform:reject"
    assert body["operation"]["type"] == "guest_action"
    assert len(guests.extra["runtime"].sent) == sent and FakeCommands.env == "dev"


def test_an_ordinary_confirm_still_goes_to_mc_when_nothing_is_pending(guests):
    sent = len(guests.extra["runtime"].sent)
    body = say(guests, "Confirm")
    assert body["backend_kind"] != "platform" and FakeCommands.calls == []
    assert len(guests.extra["runtime"].sent) == sent + 1


def test_confirm_is_claimed_while_a_proposal_is_pending(guests):
    FakeCommands.pending = True
    body = say(guests, "Confirm")
    assert body["message"] == "platform:confirm" and FakeCommands.calls == ["confirm"]


@pytest.mark.parametrize("flag,envname", [("0", "dev"), ("1", "staging"), ("1", None)])
def test_off_unless_flag_and_environment_are_set(guests, flag, envname):
    env = guests.app.extensions["guild_ui_next"]["feature_environ"]
    env["MINIMOI_MC_GUEST_ACTIONS"] = flag
    if envname is None:
        env.pop("MINIMOI_MC_GUEST_ACTIONS_ENV", None)
    else:
        env["MINIMOI_MC_GUEST_ACTIONS_ENV"] = envname
    body = say(guests, "Reject the guest request from Bruno")
    assert body["backend_kind"] != "platform" and FakeCommands.calls == []


def test_a_failure_is_reported_as_unknown_never_as_done(guests, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("database went away")
    monkeypatch.setattr(FakeCommands, "handle", boom)
    body = say(guests, "Reject the guest request from Bruno")
    assert body["operation"]["state"] == "unknown" and "cannot confirm" in body["message"]
