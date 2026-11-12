"""FF-04 (#526), one voice at a time with CoS and MC side by side: the frame relay, the CoS bridge and the Confer page's
side of the hand-off, run under node on fakes (no browser, microphone, provider or network)."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

HERE = Path(__file__).parent


def _run(script: str) -> str:
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js unavailable for the voice fixtures')
    result = subprocess.run([node, str(HERE / script)], capture_output=True, text=True,
                            timeout=60, env=dict(os.environ), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_mic_claim_crosses_the_frame_both_ways_with_origin_checks():
    assert '11 mic-claim frame scenarios passed' in _run('mic_claim_across_frame.mjs')


def test_confer_page_claims_the_pane_and_yields_to_it():
    out = _run('confer_voice_direct.mjs')
    assert 'Confer direct-voice scenarios passed' in out
