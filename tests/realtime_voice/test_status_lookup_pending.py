"""#561 (Robert, 13 Nov 2026): by voice, "did the deploy finish?" went to MC as a lookup, but the voice guessed "finished" from
the session's platform status while the lookup ran, read out MC's opposite answer, and later said "I can't see GitHub
directly from here". The rules the realtime voice is given, and the page's half (test_status_lookup_pending.mjs)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from core.realtime_voice import direct_host, status_digest

ROOT = Path(__file__).resolve().parents[2]
MC = {"name": "Master Craftsman", "short": "MC"}


def test_the_page_says_checking_once_and_each_answer_once():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "tests/realtime_voice/test_status_lookup_pending.mjs"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_pending_lookup_says_once_it_is_checking_and_never_guesses():
    rules = direct_host.direct_rules(MC, "Robert")
    assert "Say once, in a few words, that you're checking" in rules
    assert "nothing about the answer: never say what you think it is, not from your briefing, the platform status" in rules
    assert "say it once, and never say it again when the same result is shown later" in rules
    # The old rule had it fall back on the platform status while the answer was still on its way.
    assert "use it too when a lookup's answer did not reach you" not in rules
    assert "Never answer a specific one from the platform status while it is being checked" in rules


def test_the_voice_never_claims_it_cannot_see_github():
    rules = direct_host.direct_rules(MC, "Robert")
    assert "can't see it from here" not in rules
    assert "never say you can't see GitHub or the deploys, since the app checks them for you" in rules
    assert "never that all is well" in rules and "say you can check something specific" in rules     # #542 kept


def test_the_digest_header_keeps_specific_questions_for_the_lookup():
    assert "a specific one is checked, never guessed from it" in status_digest.HEAD
    text = status_digest.compose([("Deploys", lambda: "Production's last deployment, release e6017b5, finished.")])
    assert len(text) <= status_digest.MAX_CHARS and text.endswith("finished.")
