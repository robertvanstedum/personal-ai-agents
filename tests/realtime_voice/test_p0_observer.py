"""Phase 0 observer stays a console snippet. It does not become a route."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "core" / "realtime_voice" / "bench"
SNIPPET = BENCH / "p0_snippet.js"


def test_snippet_uses_the_deployed_relative_specifier_and_refuses_a_missing_controller():
    text = SNIPPET.read_text()
    assert 'new URL("/app/german/static/realtime-voice/realtime-voice-controller.js"' in text
    assert 'VERSION = "20260809-ga1"' in text
    assert "Nothing was patched" in text
    assert "/api/realtime-voice/bootstrap" not in text
    assert not (BENCH / "p0_observer.html").exists()


def test_observer_uses_the_real_registration_order():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, "--test", str(ROOT / "tests/realtime_voice/test_p0_observer.mjs")],
        capture_output=True, text=True, timeout=20,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "p0 observer events: passed" in done.stdout


def test_snippet_counts_completed_turns_without_replacing_the_host_callback():
    text = SNIPPET.read_text()
    assert 'event?.completed === true' in text
    assert "_onUserTurn" not in text
    assert "event.text" not in text
    assert "event?.text" not in text
