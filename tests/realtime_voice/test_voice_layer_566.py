"""#566 (Robert, 13 Nov 2026, Craft on the phone): the page's half of faults 1 and 3 (test_voice_layer_566.mjs), and the
core's words for a turn waiting in line. The server's half of fault 1 is in tests/guild/shop_floor/test_mc_voice_direct.py."""
import shutil
import subprocess
from pathlib import Path

import pytest

from core.realtime_voice import direct_host

ROOT = Path(__file__).resolve().parents[2]


def test_the_page_says_a_waiting_turn_as_next_and_each_answer_once():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "tests/realtime_voice/test_voice_layer_566.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_page_and_the_core_use_the_same_words_for_a_turn_waiting_in_line():
    page = (ROOT / "core/realtime_voice/static/direct-voice-session.js").read_text()
    assert f'export const QUEUED = "{direct_host.QUEUED}";' in page
    assert "did not go through" not in direct_host.QUEUED.lower()


def test_a_host_that_keeps_no_line_says_the_usual_pending_words():
    assert direct_host.DirectVoiceAdapter().in_line("sid", "t1") is False
