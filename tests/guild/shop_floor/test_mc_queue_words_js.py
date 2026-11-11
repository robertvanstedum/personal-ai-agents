"""What the waiting-messages line and chips say (static/js/mc_queue_words.js, issue #346 P3), run in node: only what the
server's ledger view says, never that a waiting message reached or changes the current answer."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static" / "js" / "mc_queue_words.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(script: str):
    code = f"import * as W from {json.dumps(MODULE.as_uri())};\nconst out = (() => {{ {script} }})();\nconsole.log(JSON.stringify(out));"
    done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_chip_words_say_waiting_and_place_only():
    assert run("return [W.chipWords(1, false), W.chipWords(1, true), W.chipWords(2, true), W.chipWords(5, false)];") == [
        "Not sent · next to send", "Not sent · next to send · waits for MC's current answer",
        "Not sent · 2nd in line · waits for MC's current answer", "Not sent · 5th in line"]


def test_pause_words_never_claim_an_outcome():
    unknown, failed, none = run("return [W.pauseWords({reason:'unknown'}, 2), W.pauseWords({reason:'failed'}, 1), W.pauseWords({reason:'unknown'}, 0)];")
    assert "may have run, or may still be running" in unknown and "lost its connection" not in unknown and "2 waiting messages are held; Continue sends them" in unknown
    assert "failed, or could not be sent" in failed and "1 waiting message is held; Continue sends it" in failed
    assert none.endswith("Continue lets new messages go through again.")


def test_pause_words_say_when_it_paused():
    at = run("return W.pauseWords({reason:'failed', created_at:'2026-11-11T15:41:00Z'}, 0);")
    assert at.startswith("Paused at ") and "didn't get an answer" in at
    assert run("return W.pauseWords({reason:'failed', created_at:'nonsense'}, 0);").startswith("Paused: ")


def test_not_sent_words_keep_their_own_reason():
    assert run("""return [W.notSentWords({state:'expired', outcome:{reason:'queue_expired'}}),
                          W.notSentWords({state:'cancelled', outcome:{reason:'earlier_expired'}}),
                          W.notSentWords({state:'cancelled', outcome:{}}),
                          W.notSentWords({state:'answered', outcome:{}})];""") == [
        "Not sent · it waited more than 24 hours", "Not sent · cancelled because an earlier message expired",
        "Not sent · cancelled", None]


def test_bar_words():
    out = run("""const q = (c) => ({conversation_id: c});
      return [W.barWords({queued: [], running: null, pause: null}, 'c-1'),
              W.barWords({queued: [q('c-1')], running: {}, pause: null}, 'c-1'),
              W.barWords({queued: [q('c-1'), q('c-2')], running: null, pause: null}, 'c-1'),
              W.barWords({queued: [q('c-1')], running: null, pause: {reason: 'failed'}}, 'c-1')];""")
    assert out[0] == ""
    assert out[1] == "1 message waiting"
    assert out[2] == "2 messages waiting (1 in another conversation)"
    assert out[3].startswith("Paused: an earlier message didn't get an answer")
    joined = " ".join(out).lower()
    for claim in ("will see", "steer", "while it answers", "received", "added to"):
        assert claim not in joined


def test_retry_delays_back_off_and_cap():
    assert run("let d = 0, out = []; for (let i = 0; i < 6; i++) { d = W.nextDelay(d); out.push(d); } return out;") == [
        5000, 15000, 30000, 60000, 60000, 60000]


def test_confirmed_stop_never_claims_the_message_was_not_sent():
    assert run("return W.notSentWords({state:'cancelled', outcome:{status:'stopped', reason:'owner_stop_acknowledged'}});") == 'Stopped · cancellation confirmed; no answer saved'
