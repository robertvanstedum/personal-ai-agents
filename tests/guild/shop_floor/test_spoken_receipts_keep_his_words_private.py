"""Verification S-6 (REVIEW_a4_fix.md), under Robert's rule of 29 Oct: "I don't want the whole world hearing what I just
said"; "definitely dont want the respoken unless I ask for it". A spoken receipt for his note, post-it or reminder never
repeats his words, on MC's direct voice (mc_voice_direct.spoken_receipt: "Saved.", "Post-it added.", "Reminder set for
<time>.") as on CoS's ("Saved."). The written receipt in the conversation keeps them. Real routes and typed-turn code on
the synthetic Guild mount (test_mc_voice_direct.build) and on her rig (tests/cos/cos_voice_rig.py). No provider, no
Keychain, no network."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from minimoi_portal.guild_ui import mc_voice_direct as direct, owner_actions as oa
from test_mc_voice_direct import ON, build, call, start

WORDS = "the spare key is under the blue pot"


class Board:
    def __init__(self):
        self.rows = {}

    def add_postit(self, text, author, *, idempotency_key):
        old = self.rows.get(idempotency_key)
        row = old or {"id": len(self.rows) + 1, "text": text}
        self.rows[idempotency_key] = row
        return SimpleNamespace(outcome="added", value=row, repeated=old is not None)


class Cos:
    """The Chief of Staff's operations: a note or a reminder receipt, as Confer gives them."""
    kind = "cos"

    def __init__(self):
        self.calls = []

    def operation(self, text, *, conversation_id, request_id):
        self.calls.append(text)
        if text.startswith("/note "):
            return {"state": "ok", "operation": {"type": "note_save", "status": "saved", "operation_id": request_id,
                                                 "storage": "cos_platform_memory"}}
        due = re.fullmatch(r"/remind (\S+) .+", text, re.S)[1]
        return {"state": "ok", "operation": {
            "type": "reminder_set", "status": "saved", "operation_id": request_id, "storage": "cos_reminders",
            "delivery": "telegram", "due_utc": due, "reminder_id": "rem-" + hashlib.sha256(request_id.encode()).hexdigest()[:12]}}


@pytest.fixture
def mc(monkeypatch, tmp_path):
    from minimoi_portal.guild_ui import api
    r = build(monkeypatch, tmp_path, dict(ON))
    r.board, r.ops = Board(), Cos()
    r.config["services"] = SimpleNamespace(mc=r.ops, mc_turns=True)
    monkeypatch.setattr(api, "_floor", lambda: r.board)
    monkeypatch.setattr(oa, "reminders_enabled", lambda: True)
    return r


def his_words_in(line, words=WORDS):
    """Whether any run of three or more of his words is in a spoken line."""
    tokens = words.casefold().split()
    return any(" ".join(tokens[i:i + 3]) in line.casefold() for i in range(len(tokens) - 2))


@pytest.mark.parametrize("name,words,spoken", [
    ("save_note", f"make a note that {WORDS}", direct.SPOKEN_SAVED),
    ("consult", f"make a note that {WORDS}", direct.SPOKEN_SAVED),
    ("save_note", f"save a note {WORDS}", direct.SPOKEN_SAVED),
    ("save_note", f"put a post-it on the board saying {WORDS}", direct.SPOKEN_POSTIT),
    ("consult", f"put a post-it on the board saying {WORDS}", direct.SPOKEN_POSTIT),
])
def test_mc_speaks_a_fixed_receipt_and_keeps_his_words_in_writing(mc, name, words, spoken):
    sid = start(mc)["voice_session_id"]
    said = call(mc, sid, call_id="c1", name=name, words=words).get_json()
    assert said.get("ok", True) is True and said["receipt"] and said["spoken"] == spoken and not his_words_in(said["spoken"])
    written = [row["text"] for row in mc.notes.rows.values() if row["author_kind"] != "owner"]
    assert any(WORDS in text for text in written)                         # the written receipt keeps them
    again = call(mc, sid, call_id="c1", name=name, words=words).get_json()
    assert again == said                                                  # a replay says the same, still not his words


def test_mc_speaks_a_reminders_time_but_not_its_words(mc):
    sid = start(mc)["voice_session_id"]
    said = call(mc, sid, call_id="r1", words=f"Remind me in 20 minutes to check that {WORDS}").get_json()
    assert said["spoken"].startswith("Reminder set for ") and said["spoken"].endswith("It will arrive by Telegram.")
    assert not his_words_in(said["spoken"]) and "Chicago time" in said["spoken"]
    assert any(WORDS in row["text"] for row in mc.notes.rows.values() if row["author_kind"] != "owner")


def test_the_spoken_receipt_line_for_each_kind():
    assert direct.spoken_receipt({"type": "cos_note", "saved": True}) == "Saved."
    assert direct.spoken_receipt({"type": "cos_note", "saved": True, "repeated": True}) == "Already saved."
    assert direct.spoken_receipt({"type": "board_postit", "saved": True, "id": 3}) == "Post-it added."
    assert direct.spoken_receipt({"type": "reminder_set", "saved": True, "due_utc": "2026-10-19T14:00:00Z"}) == (
        "Reminder set for Mon 19 Oct, 9:00 AM Chicago time. It will arrive by Telegram.")
    for operation in ({"type": "cos_note", "saved": False}, {"type": "job"}, None, {}):
        assert direct.spoken_receipt(operation) is None


def test_cos_speaks_saved_and_never_his_words(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cos"))
    import cos_voice_rig as rig
    r = rig.build(monkeypatch, tmp_path)
    sid = rig.start(r)
    by_tool = rig.ask(r, sid, "call_1", "t1", words=f"save a note {WORDS}", name="save_note").get_json()
    by_check = rig.ask(r, sid, "host-2", "t2", words=f"make a note that {WORDS}").get_json()
    for said in (by_tool, by_check):
        assert said["saved"] is True and said["spoken"] == "Saved." and not his_words_in(said["spoken"])
    assert list(r.notes.saved.values()) == [WORDS, WORDS]
