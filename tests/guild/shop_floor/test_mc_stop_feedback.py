"""MC's Stop answers a click at once, keeps an early click until the turn is acknowledged, and says a confirmed Stop once
(Dev, 11 Nov 2026: an early Stop did nothing visible, was pressed twice, and the notice showed twice)."""
from pathlib import Path

JS = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()


def test_an_early_click_shows_stopping_and_is_sent_on_acknowledgement():
    click = JS[JS.index("stop.addEventListener('click'"):JS.index("const handle = (ev) =>")]
    assert click.index("setSlot(li, 'state', 'Stopping…')") < click.index("if (!li.turnId) { li.stopPending = true; return; }")
    ack = JS[JS.index("if (ev.t === 'ack'"):JS.index("ev.t === 'progress'")]
    assert "if (li.stopPending)" in ack and "apiStop(" in ack


def test_a_confirmed_stop_is_said_once():
    assert JS.count("stoppedNotice(") >= 3                       # defined, and used on both the stream and buffered paths
    assert "platformLine('Guild platform', body.message || 'Stopped. Your message is kept.')" not in JS
