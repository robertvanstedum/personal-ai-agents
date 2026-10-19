"""Private while every message is recorded (issue #346): with the turn ledger on, the page says Private is off, with
its own reason, and the lock cannot be selected. The server's own refusal (/mc/private) is unchanged, and so is the
Chief of Staff's reason. No network, no model."""
from __future__ import annotations

import re

from minimoi_portal.guild_ui.mc import ledger_gate

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turn_ledger_routes import FakeLedger, _ledger_on, turned  # noqa: F401
from test_mc_cos_backend import _cos_turned

API = "/guild-next/api/v1"
WHY = "Private is off here: every message is recorded before it is sent"


def _lock(html):
    return re.search(r"<button[^>]*data-mc-record[^>]*>", html).group(0)


def test_ledger_on_the_page_says_private_is_off_with_its_own_reason(turned):
    _ledger_on(turned, FakeLedger())
    html = turned.owner().get("/guild-next/guild/build").get_data(as_text=True)
    lock = _lock(html)
    assert 'data-mc-private="false"' in html
    assert " disabled" in lock and 'data-private-off="recorded"' in lock
    assert f'title="{WHY}"' in lock and f'data-private-off-why="{WHY}"' in lock
    assert 'aria-label="Private is not available"' in lock
    assert "keeps its own record" not in lock                       # not the Chief of Staff's reason


def test_ledger_off_private_is_offered_as_before(turned):
    _ledger_on(turned, FakeLedger(), on=False)
    html = turned.owner().get("/guild-next/guild/build").get_data(as_text=True)
    lock = _lock(html)
    assert 'data-mc-private="true"' in html and "data-private-off" not in lock and " disabled" not in lock
    assert 'aria-label="Go Private"' in lock


def test_the_chief_of_staff_keeps_its_own_reason(floored):
    _cos_turned(floored)
    html = floored.owner().get("/guild-next/guild/build").get_data(as_text=True)
    lock = _lock(html)
    assert 'data-private-off="backend"' in lock and "it keeps its own record" in lock and WHY not in lock


def test_the_server_still_refuses_private_while_recorded(turned):
    _ledger_on(turned, FakeLedger())
    client = turned.owner()
    token = turned.csrf(client)
    r = client.post(f"{API}/mc/private", json={"text": "x", "session": "s" * 20},
                    headers=write_headers(token, mode="off_record"))
    assert r.status_code == 409 and r.get_json()["error"] == "private_unavailable"
    assert turned.extra["runtime"].sent == []


def test_the_page_reads_the_servers_reason_and_exit_always_works():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    assert "btn.dataset.privateOffWhy" in js and "refuse(`${privateOffWhy()}. Nothing was sent. Your draft is kept.`)" in js
    assert "if (exit) exit.addEventListener('click', () => { if (live.off) toggleRecord(); });" in js
    assert ledger_gate.FLAG == "MINIMOI_MC_TURN_LEDGER"
