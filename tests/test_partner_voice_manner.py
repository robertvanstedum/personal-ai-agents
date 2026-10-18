"""MC and CoS speak with one shared manner: a concise Viennese colleague, natural German, short turns that yield the floor.

Owner direction 18 Oct 2026 (MASTER_CRAFTSMAN_PERSONA.md) and the reviewed 17 Oct pacing change. The manner lives once, in
core/realtime_voice/controlled_config.VOICE_MANNER, which configuration() appends to every Guild MC and CoS managed-voice session.
These tests read the FINAL session instructions, so a contradicting older rule anywhere in them fails. Accent fidelity is a
listening check, not something a prompt can prove.
"""
import pytest

from core.realtime_voice import controlled_config as config


def _session(instructions):
    return config.configuration(instructions)["instructions"]


def _consistent(text):
    assert "Viennese partner, friend and colleague" in text
    assert "how to say something in German" in text and "return to English naturally" in text
    assert "one or two short sentences, roughly 15 to 40 words" in text and "yield the floor" in text
    assert "never caricature" in text and "never invent shared memories or a human biography" in text
    for stale in ("one to three short sentences", "switching when he does", "Speak natural English or clear Austrian"):
        assert stale not in text, stale                                    # no older, contradicting rule survives
    assert text.count("Voice manner:") == 1


def test_mc_managed_voice_session_has_one_consistent_manner():
    from minimoi_portal.guild_ui import mc_managed_realtime as R
    text = _session(R.INSTRUCTIONS + "\n\nsynthetic startup context")
    _consistent(text)
    assert "Never claim an operation succeeded without its receipt." in text


def test_cos_managed_voice_session_has_one_consistent_manner(monkeypatch, tmp_path):
    import sys
    from unittest.mock import MagicMock
    from core.realtime_voice import budget as B
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background", "apscheduler.triggers",
                 "apscheduler.triggers.cron", "apscheduler.triggers.interval"):
        monkeypatch.setitem(sys.modules, name, MagicMock())          # as tests/cos/test_voice_budget_cos_routes.py: no scheduler
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv(B.ENV_DSN, f"sqlite:///{tmp_path / 'vb.db'}")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    monkeypatch.setattr(B, "_default", None)
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as C
    monkeypatch.setattr(C, "_build_system_prompt", lambda: "SYSTEM")
    text = _session(C._build_cos_voice_instructions("robert"))
    _consistent(text)
    assert "default to one or two short sentences" in text                 # the reviewed CoS pacing patch agrees with the manner
    assert "abandon the unfinished explanation" in text
    assert "Never claim that a note was saved unless that tool returned a successful platform receipt." in text


def test_the_manner_still_fits_under_the_prompt_cap_with_room_for_the_cos_system_prompt():
    overhead = len(("\n\n" + config.VOICE_MANNER).encode())
    assert overhead < 6000                                                  # leaves the CoS system prompt most of the 24000 B cap
