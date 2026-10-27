"""Streamed MC replies update only the blocks that changed (#402): runs tests/mc_stream_render.mjs in normal pytest/CI,
and checks the stream applies render events through it."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_stream_render_fixtures():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, str(ROOT / "tests/mc_stream_render.mjs")], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "5 mc stream render fixtures passed" in done.stdout


def test_render_events_never_replace_the_whole_message():
    js = (ROOT / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    stream = js[js.index("async function streamMasterCraftsman("):js.index("function appendOffRecord(")]
    assert "applyRendered(rendered, ev.html);" in stream
    assert "rendered.innerHTML" not in stream
