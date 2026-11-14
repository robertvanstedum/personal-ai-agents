"""Spec v1.1 walk: the server writes, the model only reads, unset stays chained."""
from __future__ import annotations

from core.realtime_voice.confer import _duration_guard
from minimoi_portal.guild_ui.mc.voice_calls import call_record_folder
from minimoi_portal.guild_ui.mc_voice import MC_MAX_MINUTES, MC_WARNING_MINUTES, session_guard
from core.realtime_voice.direct_host import _BARE_NO
from minimoi_portal.guild_ui.mc.voice_walk import (
    READ_TOOLS, confirm, drop, hold, instructions, open_session, read_tools, run_read,
    talking_reservation, save_call, call_text,
)
from minimoi_portal.guild_ui.mc_voice_direct import PATH_ENV, may_start, voice_path


class _Store:
    def __init__(self, folder, path="x"):
        self.store = type("S", (), {"folder": folder, "path": path})()


def test_a_fifteen_minute_call_is_inside_the_cap_and_a_short_one_is_not_held_open(monkeypatch):
    monkeypatch.delenv("VOICE_MC_WARNING_MINUTES", raising=False)
    monkeypatch.delenv("VOICE_MC_MAX_MINUTES", raising=False)
    guard = session_guard()
    assert (guard.warning_minutes, guard.max_minutes) == (MC_WARNING_MINUTES, MC_MAX_MINUTES)
    assert guard.status(15 * 60) == "ok"
    assert guard.status(1 * 60) == "ok"
    assert guard.status(45 * 60) == "warning"
    assert guard.status(60 * 60) == "stop"
    monkeypatch.delenv("VOICE_CONFER_WARNING_MINUTES", raising=False)
    monkeypatch.delenv("VOICE_CONFER_MAX_MINUTES", raising=False)
    confer = _duration_guard()
    assert (confer.warning_minutes, confer.max_minutes) == (45, 60)


def test_mc_turns_dir_wins_when_it_is_a_directory(tmp_path, monkeypatch):
    turns = tmp_path / "mc-turns"
    turns.mkdir()
    monkeypatch.setenv("MC_TURNS_DIR", str(turns))
    assert call_record_folder(_Store(str(tmp_path / "guild"))) == str(turns)


def test_a_missing_turns_dir_falls_back_to_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MC_TURNS_DIR", str(tmp_path / "missing"))
    guild = tmp_path / "guild"
    guild.mkdir()
    assert call_record_folder(_Store(str(guild))) == str(guild)


def test_no_folder_writes_nothing(monkeypatch):
    monkeypatch.delenv("MC_TURNS_DIR", raising=False)
    assert call_record_folder(_Store(None, path=None)) is None


def test_realtime_does_not_admit_the_direct_path():
    assert voice_path({PATH_ENV: "realtime"}) == "realtime"
    assert may_start({PATH_ENV: "realtime"}) is False
    assert may_start({}) is False


def test_read_tools_are_only_the_three_reads():
    names = [tool["name"] for tool in read_tools()]
    assert names == list(READ_TOOLS)
    assert "save_note" not in names and "consult" not in names


def test_instructions_come_from_the_master_persona():
    text = instructions(release="0.1.34")
    assert "Master Craftsman" in text or "MC" in text
    assert "Decision:" in text and "Task:" in text
    assert "Do not say that you noted it" in text


def _yes(session, sentence, utterance, add, principal="robert", hold_id=None):
    open_session(session, principal)
    held = hold(session, sentence, principal)
    return confirm(session, utterance, hold_id=hold_id if hold_id is not None else held.get("hold", ""), principal=principal, notes_add=add)


def test_a_bare_yes_writes_mcs_sentence_and_a_task_is_an_act_note():
    saved = {}

    def add(**kwargs):
        saved.update(kwargs)
        return {"id": "n-test"}

    out = _yes("s1", "Decision: ship the call record first.", "yes", add)
    assert out["receipt"] == "n-test" and out["say"] == "Noted."
    assert saved["by"] == "mc" and saved["said_by"] == "mc"
    assert saved["what_for"] == "remember"
    assert "confirmed by Robert, by voice" in saved["text"]
    assert saved["text"].startswith("Decision:")

    out = _yes("s2", "Task: fix the call record folder.", "go ahead", add)
    assert out["for"] == "act" and saved["what_for"] == "act" and saved["topic"] == "voice-task"


def test_extra_words_and_a_stop_do_not_write():
    def add(**kwargs):
        raise AssertionError("wrote")

    assert _yes("s3", "Decision: no", "yes, and also deploy", add)["confirmed"] is False
    assert _yes("s3b", "Decision: no", "stop", add)["dropped"] is True
    open_session("s3c", "robert")
    assert confirm("s3c", "yes", hold_id="missing", principal="robert", notes_add=add)["stray"] is True


def test_every_stop_word_refuses_and_a_yes_for_another_sentence_does_not_write():
    def add(**kwargs):
        raise AssertionError("wrote")

    for word in sorted(_BARE_NO):
        out = _yes(f"stop-{word}", "Decision: keep going.", word, add)
        assert out["confirmed"] is False and out.get("dropped") is True
    open_session("s-other", "robert")
    held = hold("s-other", "Decision: the one he answered.", "robert")
    hold("s-other", "Decision: a later sentence.", "robert")
    out = confirm("s-other", "yes", hold_id=held["hold"], principal="robert", notes_add=add)
    assert out["confirmed"] is False


def test_an_ordinary_reply_drops_the_hold_and_an_unknown_call_is_refused():
    def add(**kwargs):
        raise AssertionError("wrote")

    open_session("s-chat", "robert")
    first = hold("s-chat", "Decision: keep this.", "robert")
    assert hold("s-chat", "The weather is fine.", "robert")["dropped"] is True
    assert confirm("s-chat", "yes", hold_id=first["hold"], principal="robert", notes_add=add)["stray"] is True
    assert hold("no-such", "Decision: no.", "robert")["error"] == "unknown_session"
    assert confirm("no-such", "yes", hold_id="x", principal="other", notes_add=add)["error"] == "unknown_session"


def test_the_talking_call_reserves_the_realtime_rate():
    kind, micro = talking_reservation(60)
    assert kind == "realtime_direct_session"
    assert micro == 60 * 100_000


def test_noted_is_not_said_without_a_receipt():
    def add(**kwargs):
        return {}

    out = _yes("s4", "Decision: keep the chained path.", "yes", add)
    assert out["ok"] is False and "say" not in out


def test_a_partial_tool_name_is_refused_and_a_read_can_miss():
    assert run_read("save_note", "", readers={})["ok"] is False
    assert run_read("issues", "570", readers={})["found"] is False


def test_the_whole_call_is_one_file_with_no_cap_and_its_owner_can_download_it(tmp_path):
    from datetime import datetime, timezone
    from minimoi_portal.guild_ui.mc.voice_walk import SESSIONS_DIR
    open_session("ab12cd34ef", "robert")
    long = "word " * 900                                      # far past the old 2,000-character note
    turns = [{"role": "user", "text": "Good morning."}, {"role": "mc", "text": long}, {"role": "user", "text": "and I'd like you to review it"}]
    out = save_call("ab12cd34ef", turns, principal="robert", folder=str(tmp_path),
                    now=datetime(2026, 11, 14, 14, 57, tzinfo=timezone.utc))
    assert out == {"ok": True, "written": True, "turns": 3, "file": "2026-11-14_ab12cd34ef.json"}
    saved = tmp_path / SESSIONS_DIR / "2026" / "2026-11-14_ab12cd34ef.json"
    import json
    record = json.loads(saved.read_text())
    assert record["raw_transcript"][1]["text"] == long.strip() and record["turn_count"] == 3
    assert record["principal"] == "robert" and "not decisions" in record["label"]
    text = call_text("ab12cd34ef", principal="robert", folder=str(tmp_path))
    assert text.endswith("Robert: and I'd like you to review it\n") and long.strip() in text
    assert call_text("ab12cd34ef", principal="someone-else", folder=str(tmp_path)) is None
    assert call_text("../../etc", principal="robert", folder=str(tmp_path)) is None


def test_a_call_is_not_saved_for_an_unknown_session_or_without_a_folder(tmp_path):
    turns = [{"role": "user", "text": "hello"}]
    assert save_call("never-issued", turns, principal="robert", folder=str(tmp_path))["error"] == "unknown_session"
    open_session("feed01", "robert")
    assert save_call("feed01", turns, principal="robert", folder=None)["error"] == "no_folder"
    assert save_call("feed01", [], principal="robert", folder=str(tmp_path)) == {"ok": True, "written": False}
    assert not (tmp_path / "voice-sessions").exists()


def test_drop_clears_a_replaced_sentence():
    open_session("s6", "robert")
    hold("s6", "Decision: one", "robert")
    later = hold("s6", "Decision: two", "robert")
    saved = {}

    def add(**kwargs):
        saved.update(kwargs)
        return {"id": "n-2"}

    confirm("s6", "yes please", hold_id=later["hold"], principal="robert", notes_add=add)
    assert saved["text"].startswith("Decision: two")
    drop("s6")
