"""Spec v1.1 walk: the server writes, the model only reads, unset stays chained."""
from __future__ import annotations

from core.realtime_voice.confer import _duration_guard
from minimoi_portal.guild_ui.mc.voice_calls import call_record_folder
from minimoi_portal.guild_ui.mc_voice import MC_MAX_MINUTES, MC_WARNING_MINUTES, session_guard
from minimoi_portal.guild_ui.mc.voice_walk import (
    READ_TOOLS, confirm, drop, hold, instructions, read_tools, run_read, transcript_note,
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
    assert (confer.warning_minutes, confer.max_minutes) == (13, 15)


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


def test_a_bare_yes_writes_mcs_sentence_and_a_task_is_an_act_note():
    saved = {}

    def add(**kwargs):
        saved.update(kwargs)
        return {"id": "n-test"}

    hold("s1", "Decision: ship the call record first.")
    out = confirm("s1", "yes", notes_add=add)
    assert out["receipt"] == "n-test" and out["say"] == "Noted."
    assert saved["by"] == "mc" and saved["said_by"] == "mc"
    assert saved["what_for"] == "remember"
    assert "confirmed by Robert, by voice" in saved["text"]
    assert saved["text"].startswith("Decision:")

    hold("s2", "Task: fix the call record folder.")
    out = confirm("s2", "go ahead", notes_add=add)
    assert out["for"] == "act" and saved["what_for"] == "act" and saved["topic"] == "voice-task"


def test_extra_words_and_a_stop_do_not_write():
    def add(**kwargs):
        raise AssertionError("wrote")

    hold("s3", "Decision: no")
    assert confirm("s3", "yes, and also deploy", notes_add=add)["confirmed"] is False
    assert confirm("s3", "stop", notes_add=add)["dropped"] is True
    assert confirm("s3", "yes", notes_add=add)["stray"] is True


def test_noted_is_not_said_without_a_receipt():
    def add(**kwargs):
        return {}

    hold("s4", "Decision: keep the chained path.")
    out = confirm("s4", "yes", notes_add=add)
    assert out["ok"] is False and "say" not in out


def test_a_partial_tool_name_is_refused_and_a_read_can_miss():
    assert run_read("save_note", "", readers={})["ok"] is False
    assert run_read("issues", "570", readers={})["found"] is False


def test_the_transcript_is_not_a_decision():
    saved = {}

    def add(**kwargs):
        saved.update(kwargs)
        return {"id": "n-tr"}

    out = transcript_note("s5", [{"role": "user", "text": "hello"}, {"role": "mc", "text": "here"}], notes_add=add)
    assert out["written"] is True
    assert saved["topic"] == "voice-transcript"
    assert saved["text"].startswith("Transcript, not a decision.")


def test_drop_clears_a_replaced_sentence():
    hold("s6", "Decision: one")
    hold("s6", "Decision: two")
    saved = {}

    def add(**kwargs):
        saved.update(kwargs)
        return {"id": "n-2"}

    confirm("s6", "yes please", notes_add=add)
    assert saved["text"].startswith("Decision: two")
    drop("s6")
