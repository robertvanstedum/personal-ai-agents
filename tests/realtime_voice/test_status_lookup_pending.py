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
    assert "Say once, in a word or two, that you're checking (for example \"Checking.\"), then stay quiet" in rules
    assert "never say what you think it is, not from your briefing, the platform status" in rules
    assert "a real check can take a minute" in rules
    assert "say it once, and never say it again when the same result is shown later" in rules
    # The old rule had it fall back on the platform status while the answer was still on its way.
    assert "use it too when a lookup's answer did not reach you" not in rules
    assert "Never answer a specific one from the platform status while it is being checked" in rules
    # Production, 13 Nov 00:38: "I couldn't get the deploy answer just now", seconds before MC's correct answer arrived.
    assert "say plainly that you couldn't get it just now" not in rules
    assert "Never say you couldn't get an answer: the app tells you" in rules


def test_the_voice_never_claims_it_cannot_see_github():
    rules = direct_host.direct_rules(MC, "Robert")
    assert "can't see it from here" not in rules
    assert "never say you can't see GitHub or the deploys, since the app checks them for you" in rules
    assert "never that all is well" in rules and "say you can check something specific" in rules     # #542 kept


def test_the_digest_header_keeps_specific_questions_for_the_lookup():
    assert "a specific one is checked, never guessed from it" in status_digest.HEAD
    text = status_digest.compose([("Deploys", lambda: "Production's last deployment, release e6017b5, finished.")])
    assert len(text) <= status_digest.MAX_CHARS and text.endswith("finished.")


# ── the wait: a lookup is a real MC turn with GitHub reads ──────────────────────────────────────────────────────────────

class _Adapter:
    person = {"domain": "t", "short": "MC", "name": "Master Craftsman"}

    def __init__(self, wait=None):
        if wait is not None:
            self.lookup_wait_s = wait


def test_a_lookup_waits_for_the_hosts_own_bound_and_everything_else_as_before():
    from minimoi_portal.guild_ui import mc_voice_direct
    assert direct_host.consult_wait(_Adapter(75), True) == 75
    assert direct_host.consult_wait(_Adapter(75), False) == direct_host.CONSULT_WAIT_S        # a confirmed request
    assert direct_host.consult_wait(_Adapter(), True) == direct_host.CONSULT_WAIT_S           # CoS: her proxy bound holds
    assert direct_host.consult_wait(_Adapter(500), True) == direct_host.LOOKUP_WAIT_MAX_S
    assert direct_host.consult_wait(_Adapter(True), True) == direct_host.CONSULT_WAIT_S
    # MC's measured turns (12-13 Nov: p90 45 s, max 56 s) fit; under the page's 90 s and nginx's 120 s.
    assert 56 < mc_voice_direct._MasterCraftsman.lookup_wait_s <= direct_host.LOOKUP_WAIT_MAX_S < 90
    page = (ROOT / "core/realtime_voice/static/direct-voice-session.js").read_text()
    assert "const LOOKUP_POST_MS = 90000;" in page and "const LOOKUP_HOLDING = [];" in page


def test_run_answers_a_slow_lookup_instead_of_pending(monkeypatch):
    import time as _time
    monkeypatch.setattr(direct_host, "CONSULT_WAIT_S", 0.05)
    monkeypatch.setattr(direct_host, "dispatch", lambda *a, **k: _time.sleep(0.3) or {"ok": True, "outcome": "done",
                                                                                         "spoken": "Yes."})

    class Sessions:
        announced = []

        def told_when_done(self, sid, item):
            return False

        def announce(self, sid, row):
            self.announced.append(row)

    args = (Sessions(), "p", None, "sid", "call", "consult", "Did the deploy finish?", "t0", 1, 0.0)
    assert direct_host.run(_Adapter(2), *args, read_only=True)["outcome"] == "done"
    assert direct_host.run(_Adapter(2), *args, read_only=False)["outcome"] == "pending"


# ── what is spoken: voice length, no caveat chrome, spoken times, single full stops ─────────────────────────────────────

def test_mc_is_asked_for_a_voice_length_answer():
    from minimoi_portal.guild_ui.mc_voice_transcripts import SPOKEN_ANSWER, input_context
    text = input_context(direct_voice=True)
    assert SPOKEN_ANSWER in text and "one or two short sentences" in SPOKEN_ANSWER
    assert '"as of the last check"' in SPOKEN_ANSWER and "about eleven twenty-five last night" in SPOKEN_ANSWER
    assert SPOKEN_ANSWER not in input_context() and SPOKEN_ANSWER not in input_context(current_voice=True)


def test_the_production_answer_is_spoken_short_plain_and_once_stopped():
    from datetime import datetime, timezone
    from core.realtime_voice.spoken import for_speech
    now = datetime(2026, 11, 13, 6, 40, tzinfo=timezone.utc)              # 00:40 in Chicago
    said = ("Yes. The last deploy finished.. The main pipeline succeeded at 05:25 UTC; the MC container release finished "
            "later, at 2026-11-13T05:51:07Z.\n\nThat is not a live refresh, and it is not whole-platform acceptance.")
    spoken = direct_host.speakable(for_speech(said, now))
    assert spoken == ("Yes. The last deploy finished. The main pipeline succeeded about eleven twenty-five last night; the MC "
                      "container release finished later, about eleven fifty-one last night.")
    assert ".." not in spoken and "UTC" not in spoken and "acceptance" not in spoken


def test_spoken_times_and_stops():
    from datetime import datetime, timezone
    from core.realtime_voice import spoken
    now = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)              # 14:00 in Chicago
    at = lambda h, m: datetime(2026, 11, 13, h, m, tzinfo=timezone.utc)   # noqa: E731
    assert spoken.time_words(at(15, 0), now) == "about nine o'clock this morning"
    assert spoken.time_words(at(19, 5), now) == "about one oh five this afternoon"
    assert spoken.time_words(at(9, 30), now) == "about three thirty early this morning"
    assert spoken.time_words(datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc), now) == "about twelve o'clock on Tuesday"
    assert spoken.single_stops("Done.. Next. Wait... ok?. Fine .") == "Done. Next. Wait... ok? Fine."
    assert direct_host.speakable("It finished.\n\nNo errors") == "It finished. No errors"
    assert direct_host.speakable("Two paragraphs\n\nhere.") == "Two paragraphs. here."
    # Only disclaimers go: an answer made only of one keeps its first sentence.
    assert spoken.for_speech("That is not a live refresh.", now) == "That is not a live refresh."
