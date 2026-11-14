"""#592: what is attached to each of Robert's messages to MC, trimmed. Synthetic stores; no network, no model.

Pins (a) the attached size for a two-word message, (b) that each removed block's facts are still reachable through a
tool MC has, and (c) that "how are things?" still carries the job health, the daily note included.
"""
import functools
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import minimoi_portal.guild_ui as guild
from minimoi_portal.guild_ui import (api, github_awareness, jobs_health, mc_context, mc_regression, mc_registers,
                                     mc_voice_transcripts, release_context, supervision)
from tests.jobs.test_health import NOW as HEALTH_NOW, daily_setup
from core.jobs import registry

REPO = Path(__file__).resolve().parents[3]
CONV = {"id": "c-592", "principal": "robert", "notes_floor": "floor-592", "record_mode": "on_record"}
CHECKIN_AT = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
AWARENESS = "[MiniMoi repository awareness: synthetic]\n" + "x" * 7600            # Step 0: 7.3 to 8.1k, every turn
LESSONS = "[Owner lessons: synthetic]\n" + "L1: keep answers short.\n" * 50         # Step 0: about 1.4k
ENV = {"MINIMOI_MC_OPERATING_RULES": "1", "MINIMOI_BOARD_ACTIONS": "1", "MINIMOI_MC_GITHUB_AWARENESS": "1",
       "MINIMOI_MC_GITHUB_AWARENESS_OWNER": "robert", "MINIMOI_SUPERVISION": "1", "MINIMOI_SUPERVISION_OWNER": "robert",
       "MINIMOI_GITHUB_CONTEXT": "1"}


class Supervision:
    def __init__(self, pending=()):
        self._pending = list(pending)

    def pending(self):
        return self._pending

    def latest_checkin(self):
        return {"at": CHECKIN_AT, "summary": {
            "prs": [1, 2, 3], "runners": ["mac"],
            "running": [{"agent": "claude-code", "state": "working", "minutes": 12, "brief": "Trim what MC carries " * 6}],
            "sessions": [{"agent": "codex", "session": "s-1", "state": "waiting", "minutes": 40, "pr": 590,
                          "needs": "a review of the depth change " * 4}],
            "open_items": [{"kind": "stale_pr", "text": "PR 571 has waited three days " * 3}]}}

    def recent_exchanges(self):
        return [{"at": CHECKIN_AT, "actor": "claude-code", "recipient": "grok", "kind": "result",
                 "request_id": f"r-{n:032x}", "text": "Reviewed and accepted, with two notes on the tests. " * 4}
                for n in range(5)]


class Floor:
    def __init__(self, notes):
        self.notes = notes

    def list_notes(self, limit=200):
        return SimpleNamespace(ok=True, data={"notes": self.notes[-limit:], "more": False})


def voice(n, session="6f1c8a52-3f7e-4d3c-9a49-0d6a8b3c1e01"):
    return {"id": n, "who": mc_voice_transcripts.AUTHOR_PREFIX + session, "author_kind": "platform",
            "context": {"page": mc_voice_transcripts.PAGE}, "created_at": f"2026-11-14T09:{n:02d}:00Z",
            "text": "[Voice transcript: owner ASR; partial=false; part=1/1]\n" + f"Spoken words number {n}. " * 20}


def reply(n):
    return {"id": n, "who": "master_craftsman", "author_kind": "agent", "context": {"page": None},
            "created_at": f"2026-11-14T09:{n:02d}:30Z", "text": "An answer."}


@pytest.fixture
def turn(monkeypatch, tmp_path):
    """(say, state): say(owner_words, trim=True) returns the attached text only, Robert's words taken off."""
    state = {"kind": "openclaw", "floor": Floor([voice(1), voice(2), reply(3)]), "pending": []}
    daily_setup(tmp_path)
    (tmp_path / "health" / "helper-note.json").write_text(json.dumps({
        "written_at": HEALTH_NOW.isoformat(), "date": "2026-10-26", "accepted_by": "mc",
        "note": "Daily note: the nightly backup ran; one job needs action."}))
    monkeypatch.setattr(jobs_health, "block", functools.partial(
        jobs_health.block, now=HEALTH_NOW, root=str(tmp_path / "health"),
        registry_loader=lambda: registry.load(tmp_path / "reg.json")))
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic")            # supervision.enabled needs one; never used
    monkeypatch.setattr(supervision, "context_block", functools.partial(
        supervision.context_block, store_factory=lambda: Supervision(state["pending"])))
    monkeypatch.setattr(github_awareness, "block", lambda *a, **kw: AWARENESS)
    monkeypatch.setattr(mc_registers, "host_context", lambda *a, **kw: "")
    monkeypatch.setattr(mc_regression, "block", lambda *a, **kw: "")
    monkeypatch.setattr(mc_context, "board_block", lambda *a, **kw: "")
    issue = {"number": 592, "state": "open", "title": "Trim what MC carries", "user": {"login": "someone"},
             "labels": [], "created_at": "2026-11-14T08:00:00Z", "html_url": "https://github.com/x/y/issues/592",
             "body": "A long description of the trim. " * 80}
    fake_get = lambda url, **kw: SimpleNamespace(status_code=200, content=b"{}", json=lambda: issue)
    monkeypatch.setattr(mc_context, "github_block", functools.partial(mc_context.github_block, http_get=fake_get,
                                                                      environ={mc_context.VAR: "1"}))
    mc_context._cache.clear()
    monkeypatch.setattr(api, "_lesson_ledger", lambda *a: object())
    import core.mc_lessons.commands as lessons
    monkeypatch.setattr(lessons, "block", lambda ledger: LESSONS)
    monkeypatch.setattr(api, "_conversations", lambda: SimpleNamespace(get=lambda *a: dict(CONV)))
    monkeypatch.setattr(api, "_conv_floor", lambda fresh: state["floor"])
    monkeypatch.setattr(api, "_floor", lambda: None)
    monkeypatch.setattr(api, "_tools_own_actions", lambda principal: True)
    app = Flask(__name__)

    def say(words, trim=True, **kw):
        env = dict(ENV, **({} if trim else {"MINIMOI_MC_CONTEXT_TRIM": "0"}))
        monkeypatch.setattr(guild, "feature_environ", lambda: env)
        monkeypatch.setattr(api, "cfg", lambda: {"base_url": "http://localhost",
                                                 "services": SimpleNamespace(mc=SimpleNamespace(kind=state["kind"]))})
        with app.app_context():
            out = api._with_context(words, words, CONV, "robert", **kw)
        assert out.startswith(words)
        return out[len(words):]
    return say, state


def test_a_two_word_message_carries_about_a_tenth_of_what_it_did(turn):
    say, _ = turn
    before, after = say("hi there", trim=False), say("hi there")
    # Before: awareness, the static rules, the full check-in and the voice notes again. After: job health, lessons and
    # one supervision count line.
    assert len(before) > 12000
    assert len(after) < 2600, len(after)
    assert "[MiniMoi job health" in after and "[Owner lessons" in after and "[Build supervision" in after
    for gone in ("repository awareness", "[MiniMoi operating rules", "[Board conversational partnership",
                 "CURRENT INPUT MODALITY", "Retained voice notes", "Agents' exchanges", "Daily note:"):
        assert gone not in after, gone
    assert "3 open PRs, 1 running, 1 sessions not done, 1 open items; 5 recent agent exchanges" in after


def test_the_trim_needs_mc_tools_and_cos_keeps_its_static_lines(turn, monkeypatch):
    say, state = turn
    monkeypatch.setattr(api, "_tools_own_actions", lambda principal: False)
    assert "repository awareness" in say("hi there")                 # no tools: nothing to reach the facts with
    monkeypatch.setattr(api, "_tools_own_actions", lambda principal: True)
    state["kind"] = "cos"
    cos = say("hi there")
    assert "repository awareness" not in cos                          # live blocks: trimmed the same way
    assert "[MiniMoi operating rules" in cos and "[Board conversational partnership" in cos   # CoS's persona lacks them
    assert "CURRENT INPUT MODALITY: text" in cos


def test_how_are_things_still_answers_from_job_health(turn):
    say, _ = turn
    after = say("how are things?")
    assert "[MiniMoi job health" in after and "[needs action] Job J (ec2)" in after
    assert "Daily note: the nightly backup ran" in after               # the daily note comes with the question
    assert "Agents' exchanges in the request ledger" in after and "running: Claude Code" in after


def test_decisions_waiting_stay_word_for_word(turn):
    say, state = turn
    state["pending"] = [{"code": "D-7", "text": "Approve the trim for production?", "id": 7}]
    assert "- D-7: Approve the trim for production?" in say("hi there")


def test_voice_notes_are_carried_once(turn):
    say, state = turn
    assert "Retained voice notes" in say("hi there", trim=False)      # before: again on every turn
    assert "Retained voice notes" not in say("hi there")              # the turn MC answered already carried 1 and 2
    state["floor"].notes.append(voice(4))
    after = say("hi there")
    assert "Spoken words number 4" in after and "Spoken words number 1" not in after
    assert "Spoken words number 1" in say("print the voice transcript")   # asked for: all of them


def test_removed_facts_are_still_reachable_through_mc_tools(turn):
    """(b) Each dropped or shortened block names, or is covered by, a read tool MC has."""
    say, _ = turn
    tools = {t["name"] if isinstance(t, dict) else t for t in _tool_names()}
    from core.mc_tools.policy import HANDLERS
    granted = {h.tool for h in HANDLERS.values()}
    for name in ("minimoi_github_get", "minimoi_github_issues", "minimoi_host_read"):
        assert name in tools and name in granted, name
    # GitHub items named in his words: the title stays, the description is one tool call away.
    named = say("what about #592?")
    assert "#592 · issue · open · Trim what MC carries" in named and "minimoi_github_issues" in named
    assert "A long description of the trim" not in named
    assert "A long description of the trim" in say("what about #592?", trim=False)
    # Deploy questions: one recorded line, and the delivery record and live GitHub named.
    line = release_context.brief("what is deployed?", loader=lambda: (_ for _ in ()).throw(OSError()))
    assert "unavailable" in line and "mc-delivery" in line and "minimoi_github_get" in line
    # The persona's delivery card already reads the delivery record and live GitHub before any cache.
    card = (REPO / "services/mc_operations/personas/mc-chat/releases/0.1.34/skills/mc-delivery/SKILL.md").read_text()
    assert "minimoi_github_get" in card and "PRODUCTION.md" in card


def _tool_names():
    doc = json.loads((REPO / "core/mc_tools/tools.json").read_text())
    tools = doc if isinstance(doc, list) else doc.get("tools", doc)
    return list(tools) if isinstance(tools, dict) else tools
