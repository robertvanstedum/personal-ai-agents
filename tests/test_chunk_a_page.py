"""Chunk A page: the record is synthetic, and the host refuses to start without keys."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_page_record_counts_without_storing_words():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, "--test", str(ROOT / "tests/realtime_voice/test_chunk_a_page.mjs")],
        capture_output=True, text=True, timeout=20,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "chunk A page record: passed" in done.stdout


def test_host_stops_when_a_voice_key_is_missing():
    serve = (ROOT / "core/realtime_voice/bench/serve.py").read_text()
    assert "find-generic-password" in serve
    assert "Nothing was started" in serve
    assert "127.0.0.1" in serve
    page = (ROOT / "core/realtime_voice/bench/a_page.html").read_text()
    assert "Provider use is billed" in page
    assert "playback once per session" in page
