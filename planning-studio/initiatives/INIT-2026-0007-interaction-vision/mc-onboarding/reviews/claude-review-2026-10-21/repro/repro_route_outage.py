"""TEMPORARY review reproduction (not to be committed)."""
from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turn_ledger_routes import turned, owner, _keep, _ask, FakeLedger, _ledger_on  # noqa: F401
from test_mc_guest_commands_route import FakeCommands, guests, say  # noqa: F401


def test_db_outage_turns_an_ordinary_dont_into_unknown_guest_action_and_pauses(guests, monkeypatch):
    def down(self, conversation):
        raise OSError("could not connect to server")
    monkeypatch.setattr(FakeCommands, "has_pending", down)
    ledger = FakeLedger()
    _ledger_on(guests, ledger)
    sent = len(guests.extra["runtime"].sent)
    body = say(guests, "Don't.")                       # an ordinary reply; no guest proposal exists
    print("\nBODY", body.get("message"), body.get("operation"), "| ledger:", ledger.finished)
    assert len(guests.extra["runtime"].sent) == sent   # MC never saw the message
    assert ledger.finished[-1][0] == "unknown"          # and the turn ledger now pauses on 'unknown'
