"""Phase 0 observer: timings only, and it does not edit the shared controller."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
OBSERVER = ROOT / "core" / "realtime_voice" / "bench" / "p0_observer.js"
PAGE = ROOT / "core" / "realtime_voice" / "bench" / "p0_observer.html"
CONTROLLER = ROOT / "core" / "realtime_voice" / "static" / "realtime-voice-controller.js"


def test_observer_records_times_and_keeps_no_transcript_text():
    text = OBSERVER.read_text()
    assert "speechEndToFirstTranscriptMs" in text
    assert "committedUserTurns" in text
    assert "interrupted" in text
    assert "notMeasured" in text
    assert "evt.text" not in text
    assert "session.turns" not in text


def test_page_loads_the_shared_controller_and_names_the_english_return():
    page = PAGE.read_text()
    assert "/static/realtime-voice/realtime-voice-controller.js" in page
    assert "reply comes back in English" in page
    assert "navigator.clipboard" in page


def test_shared_voice_files_are_unchanged_from_main():
    changed = subprocess.run(
        ["git", "diff", "--name-only", "origin/main", "--",
         "domains/german", "core/realtime_voice/static", "core/realtime_voice/bootstrap.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert changed == ""
    assert "export class RealtimeVoiceController" in CONTROLLER.read_text()
