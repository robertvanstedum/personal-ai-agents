"""The Journeyman's check-in (v0.7 Unit 7; acceptance rows O02 and the check-in scenario). No model is called by the base report."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import REPO, WORKSHOP, new_id, write_config
from core.workshop_journal import checkin, profiles
from core.workshop_journal.journal import Journal
from fakes import SyntheticOwnerResolver, Teammate, make_synthetic_root
from test_reducer_cli import cli, write_env


@pytest.fixture
def world(root):
    make_synthetic_root(root)
    j = Journal(root, WORKSHOP, lock_timeout=0.3, resolver=SyntheticOwnerResolver(root))
    return j, Teammate("claude-code", j), Teammate("codex", j), Teammate("journeyman", j)


NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def test_the_report_names_the_last_entry_waiting_questions_and_missing_updates_in_plain_words(world):
    j, code, codex, journeyman = world
    code.progress("Unit 4 under way.")
    code.needs_you("Which backup destination?")
    codex.request(["claude-code"], "review", "Look at the diff.")
    text = checkin.report(j.brief(now=NOW, teammates=("claude-code", "codex", "grok-cli")))
    assert "- claude-code: last entry was a needs_you at " in text and "nothing newer has been reported since" in text
    assert "- grok-cli: no entry in this scope; nothing has been reported." in text
    assert "Waiting for Robert:" in text and "Which backup destination?" in text
    assert "No receipt from claude-code for request" in text
    assert "not observed" in text and "idle" not in text.lower() and "all clear" not in text.lower()


def test_a_quiet_clean_workshop_says_exactly_that_and_nothing_more(world):
    j, code, codex, _ = world
    req = code.request(["codex"], "review", "x")
    codex.receive(req.event_id)
    codex.result(req.event_id)
    text = checkin.report(j.brief(now=NOW))
    assert "Nothing is waiting for Robert in this scope." in text and "No missing receipts, results or overdue requests are recorded." in text


def test_a_damaged_journal_gets_no_report_not_a_stale_one(root):
    j = Journal(root, WORKSHOP)
    assert "cannot report" in checkin.report(j.brief()) and "missing" in checkin.report(j.brief())


def test_the_refresh_is_an_ordinary_request_and_its_answer_is_correlated(world):
    j, code, codex, journeyman = world
    ask = j.append(checkin.refresh_request(["codex"]))
    assert ask.committed
    assert j.brief(topic="checkin")["requests"][0]["recipients"]["codex"]["status"] == "pending"
    j.append({"actor": "codex", "kind": "receipt", "item": "topic:checkin", "topic": "checkin", "text": "x",
              "payload": {"request_id": ask.event_id, "recipient": "codex"}})
    answer = j.append({"actor": "codex", "kind": "result", "item": "topic:checkin", "topic": "checkin", "text": "In progress.",
                       "payload": {"request_id": ask.event_id, "recipient": "codex", "outcome": "completed", "limitations": []}})
    got = j.brief(topic="checkin")["requests"][0]
    assert got["state"] == "closed" and got["recipients"]["codex"]["result_seq"] == answer.seq


# ── O02: a missing model profile is a hard error ────────────────────────────────────────────────────────────────────
def test_O02_a_missing_profile_is_a_hard_error_and_the_environment_is_never_consulted(world, monkeypatch):
    j, *_ = world
    for var in ("ANTHROPIC_MODEL", "OPENAI_MODEL", "WORKSHOP_MODEL", "MODEL", "XAI_MODEL"):
        monkeypatch.setenv(var, "something-in-the-environment")
    called = []
    with pytest.raises(profiles.ProfileMissing) as caught:
        checkin.summarise(j.brief(), {}, lambda profile, brief: called.append(1) or {"text": "x", "cites": []})
    assert "no_routine_profile_configured" in str(caught.value) and called == []                   # the caller was never reached
    with pytest.raises(profiles.ProfileMissing):
        profiles.require({"routine": {"provider": "p", "model": "m"}}, "judgment")                  # one purpose configured does not cover another


def test_O02_profiles_come_only_from_config_and_the_requested_model_is_recorded_as_provider_and_requested(tmp_path):
    write_config(tmp_path, json.dumps({"v": 1, "model_profiles": {"routine": {"provider": "local", "model": "small-1"},
                                                                                   "judgment": {"provider": "cloud", "model": "large-1"}}}))
    table = profiles.load(str(tmp_path))
    assert profiles.require(table, "routine") == {"provider": "local", "requested": "small-1"}
    assert profiles.require(table, "judgment") == {"provider": "cloud", "requested": "large-1"}
    assert profiles.load(str(tmp_path / "nowhere")) == {}


@pytest.mark.parametrize("bad", [{"routine": {"provider": "p"}}, {"routine": {"provider": "p", "model": "m", "key": "k"}},
                                 {"cheap": {"provider": "p", "model": "m"}}, {"routine": {"provider": "p q", "model": "m"}}, [], {"routine": "m"}])
def test_O02_a_bad_profile_table_is_refused(bad):
    with pytest.raises(profiles.BadProfiles):
        profiles.validate(bad)


def test_O02_no_model_name_is_hardcoded_anywhere_in_the_workshop_code():
    pattern = re.compile(r"(?i)\b(?:claude-(?:opus|sonnet|haiku|fable)[\w.-]*|gpt-[\w.-]+|grok-[0-9][\w.-]*|gemini-[\w.-]+|llama-?[\w.-]+|o[134]-(?:mini|preview))")
    for path in sorted((REPO / "core" / "workshop_journal").glob("*.py")) + [REPO / "scripts/workshop/workshop.py"]:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            hit = pattern.search(line)
            assert not hit or "grok-cli" in hit.group(0) or "grok-chat" in hit.group(0), (path.name, lineno, hit.group(0))


# ── a model summary may help reading, never change the facts ─────────────────────────────────────────────────────────
def configured():
    return {"routine": {"provider": "local", "model": "small-1"}}


def test_a_checked_summary_is_appended_below_the_base_report_and_cannot_replace_it(world):
    j, code, codex, _ = world
    req = codex.request(["claude-code"], "review", "Look.")
    brief = j.brief(now=NOW)
    seen = {}

    def fake_model(profile, brief_):
        seen["profile"] = profile
        return {"text": "Codex is waiting on a receipt from claude-code.", "cites": [req.event_id]}
    text, recorded = checkin.summarise(brief, configured(), fake_model)
    assert text.startswith(checkin.report(brief)) and "Summary (written by a model, cites " + req.event_id in text
    assert "No receipt from claude-code" in text                                                        # the gap is still there
    assert recorded == {"provider": "local", "requested": "small-1"} == {"provider": seen["profile"]["provider"], "requested": seen["profile"]["requested"]}


@pytest.mark.parametrize("summary,reason", [
    ({"text": "fine", "cites": [new_id()]}, "cites_unknown_id"), ({"text": "fine", "cites": []}, "cites_required"),
    ({"text": "Everything is fine, no gaps remain.", "cites": ["__REQ__"]}, "claims_what_the_brief_does_not"),
    ({"text": "Robert approved the plan.", "cites": ["__REQ__"]}, "claims_what_the_brief_does_not"),
    ({"text": "fine", "cites": ["__REQ__"], "extra": 1}, "shape"), ({"text": "", "cites": ["__REQ__"]}, "text"), ("text", "shape"),
])
def test_a_summary_that_cites_nothing_real_or_claims_more_than_the_brief_is_refused(world, summary, reason):
    j, code, codex, _ = world
    req = codex.request(["claude-code"], "review", "Look.")
    if isinstance(summary, dict):
        summary = {k: ([req.event_id if c == "__REQ__" else c for c in v] if k == "cites" else v) for k, v in summary.items()}
    with pytest.raises(checkin.BadSummary) as caught:
        checkin.summarise(j.brief(now=NOW), configured(), lambda p, b: summary)
    assert str(caught.value) == reason


def test_the_check_in_command_prints_the_report_and_can_post_refresh_requests(root, tmp_path):
    seed = {"actor": "claude-code", "kind": "progress", "item": "topic:x", "topic": "x", "text": "working", "payload": {"action": "w"}}
    assert cli(root, "append", "--file", write_env(tmp_path, seed)).returncode == 0
    out = cli(root, "checkin", "--teammate", "codex", "--now", "2026-10-09T09:00:00Z")
    assert out.returncode == 0 and "- codex: no entry in this scope" in out.stdout and "claude-code: last entry was a progress" in out.stdout
    dry = cli(root, "checkin", "--ask-refresh", "codex", "--dry-run")
    assert '"status": "would_commit"' in dry.stdout and len(Journal(root, WORKSHOP).read().events) == 1
    posted = cli(root, "checkin", "--ask-refresh", "codex")
    assert '"status": "committed"' in posted.stdout and len(Journal(root, WORKSHOP).read().events) == 2
    pending = json.loads(cli(root, "pending", "--for", "codex", "--json").stdout)["requests"]
    assert pending[0]["from"] == "journeyman" and pending[0]["action"] == "refresh"
