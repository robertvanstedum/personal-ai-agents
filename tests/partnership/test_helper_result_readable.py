"""An accepted helper result reads as labelled lines, keeps every field, and is the same text every time
(Dev, 11 Nov 2026: CoS's answer carried a raw JSON block)."""
from services.helper_seats.presentation import readable

OUTCOME = {"not_searched": ["conversations", "CoS history"],
           "records": [{"current": False, "item_id": 112, "status": "done", "title": "German Multiuser Isolation"}],
           "searched": {"build_log_items": 127}, "extra": {"nested": {"deep": 1}}, "note": None}


def test_reads_as_lines_and_bullets_not_json():
    text = readable(OUTCOME)
    assert "{" not in text.split("Extra:")[0]
    assert text.splitlines()[:2] == ["Records:", "- Title: German Multiuser Isolation; Item id: 112; Status: done; Current: no"]
    assert "Searched:\n- Build log items: 127" in text and "Not searched: conversations, CoS history" in text
    assert "Note: none" in text


def test_keeps_every_field_and_is_deterministic():
    text = readable(OUTCOME)
    for word in ("Records", "Searched", "Not searched", "Extra", "Note", "deep"):
        assert word in text
    shuffled = dict(reversed(list(OUTCOME.items())))
    assert readable(shuffled) == text


def test_plain_values_stay_plain():
    assert readable("Found it in build_log.html") == "Found it in build_log.html"
    assert readable({"found": [], "stopped": True}) == "Found: none\nStopped: yes"
