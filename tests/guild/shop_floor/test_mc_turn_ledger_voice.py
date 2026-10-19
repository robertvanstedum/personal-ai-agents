"""The turn ledger on the managed voice path (issue #346 P1): one claim covers the spoken Board,
owner-action, transcript-copy, job and consult branches, and a CoS note or reminder whose outcome
was not confirmed after sending is unknown (a pause), not answered. Reuses the synthetic managed
host from test_mc_managed_realtime.py: no provider, model, network or database."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from core.realtime_voice.controlled_session import SessionRefused

from minimoi_portal.guild_ui.mc import ledger_gate

from test_mc_managed_realtime import ready, start  # noqa: F401  (pytest fixture and helper)
from test_mc_turn_ledger_routes import FakeLedger


def _on(r, ledger):
    r.env[ledger_gate.FLAG] = "1"
    r.config["services"].turn_ledger = ledger
    return ledger


def _say(session, name, text, op):
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(session.dispatch, name, text, "c-123456abcdef", op).result()


@pytest.mark.parametrize("receipt,state", [({"state": "uncertain"}, "unknown"), ({"state": "refused", "reply": "No."}, "failed")])
def test_spoken_note_not_confirmed_after_sending_pauses(ready, receipt, state):
    ledger = _on(ready, FakeLedger())
    ready.config["services"].mc.operation = lambda text, **kw: receipt
    s, _ = start(ready)
    out = _say(s, "save_cos_note", "Save a note: exact owner note", "voice-op-1")
    assert out["operation"]["saved"] is False and out["operation"]["state"] == receipt["state"]
    assert [e[0] for e in ledger.events][0] == "admit" and ledger.events[0][1]["kind"] == "voice"
    assert ledger.finished[0][0] == state and not ready.calls            # no model consult


def test_spoken_board_command_runs_under_the_voice_claim(ready):
    ledger = _on(ready, FakeLedger())
    s, _ = start(ready)
    out = _say(s, "consult_cos_agent", "Make a post-it: Retest tomorrow", "voice-op-2")
    assert out["operation"]["saved"] is True and ledger.finished[0][0] == "answered" and not ready.calls


def test_spoken_turn_is_refused_while_another_turn_holds_the_claim(ready):
    _on(ready, FakeLedger(result="busy"))
    s, _ = start(ready)
    with pytest.raises(SessionRefused):
        _say(s, "consult_cos_agent", "Actual owner words", "voice-op-3")
    assert not ready.calls and not ready.notes.rows                       # nothing kept, nothing sent


def test_spoken_consult_passes_its_claim_to_the_model_turn(ready):
    ledger = _on(ready, FakeLedger())
    s, _ = start(ready)
    out = _say(s, "consult_cos_agent", "Actual owner words", "voice-op-4")
    assert out["reply"] == "Synthetic native reply"
    assert ready.calls[0]["ledger_turn"] is not None and ledger.finished[0][0] == "answered"


def test_ledger_off_voice_is_unchanged(ready):
    ledger = FakeLedger()
    ready.config["services"].turn_ledger = ledger
    s, _ = start(ready)
    assert _say(s, "consult_cos_agent", "Actual owner words", "voice-op-5")["reply"] == "Synthetic native reply"
    assert ledger.events == [] and ready.calls[0].get("ledger_turn") is None
