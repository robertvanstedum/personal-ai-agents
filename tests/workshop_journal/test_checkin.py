"""The Journeyman's check-in (v0.7 Unit 7; acceptance rows O02 and the check-in scenario). No model is called by the base report."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workshop_journal.conftest import REPO, WORKSHOP, new_id, write_config
from core.workshop_journal import checkin, profiles
from core.workshop_journal.journal import Journal
from workshop_journal.fakes import SyntheticOwnerResolver, Teammate, make_synthetic_root
from workshop_journal.test_reducer_cli import cli, write_env


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


# ── a model may only choose facts the brief holds; it writes no sentence (Codex R10) ────────────────────────────────
def configured():
    return {"routine": {"provider": "local", "model": "small-1"}}


def test_a_model_selection_is_rendered_from_templates_below_the_base_report(world):
    j, code, codex, _ = world
    req = codex.request(["claude-code"], "review", "Look.")
    code.needs_you("Which destination?")
    brief = j.brief(now=NOW)
    qid = brief["needs_you"][0]["event_id"]
    seen = {}

    def fake_model(profile, brief_):
        seen["profile"] = profile
        return {"facts": [{"kind": "request_open", "id": req.event_id}, {"kind": "no_receipt", "id": req.event_id, "recipient": "claude-code"},
                          {"kind": "owner_question_open", "id": qid}, {"kind": "request_open", "id": req.event_id}]}
    text, recorded = checkin.summarise(brief, configured(), fake_model)
    assert text.startswith(checkin.report(brief)) and "Highlights (chosen by a model;" in text
    tail = text.split("Highlights")[1]
    assert f"Request {req.event_id} from codex to claude-code (review) is still open." in tail
    assert f"claude-code has not picked up request {req.event_id}." in tail and "A question for Robert is open" in tail
    assert tail.count("is still open") == 1                                                                 # a repeated choice is shown once
    assert "No receipt from claude-code" in text                                                           # the gap is still in the base report
    assert recorded == {"provider": "local", "requested": "small-1"} == {"provider": seen["profile"]["provider"], "requested": seen["profile"]["requested"]}


def test_R10_invented_authority_or_completion_cannot_be_expressed_at_all(world):
    """Codex's case: an unanswered request, and a model that says Robert authorized a release and the review finished."""
    j, code, codex, _ = world
    req = codex.request(["claude-code"], "review", "Look.")
    brief = j.brief(now=NOW)
    liar = {"facts": [{"kind": "request_closed", "id": req.event_id}]}                                      # it is open
    with pytest.raises(checkin.BadSummary) as caught:
        checkin.summarise(brief, configured(), lambda p, b: liar)
    assert str(caught.value) == "fact_not_in_the_brief"
    for prose in ({"text": "Robert authorized production release. The review finished successfully; every finding is resolved.",
                   "cites": [req.event_id]}, {"facts": [{"kind": "authorized", "id": req.event_id}]},
                  {"facts": [{"kind": "request_open", "id": req.event_id, "text": "Robert authorized production release."}]}):
        with pytest.raises(checkin.BadSummary):
            checkin.summarise(brief, configured(), lambda p, b, prose=prose: prose)


@pytest.mark.parametrize("summary,reason", [
    ({"facts": []}, "fact_count"), ({"facts": "text"}, "shape"), ({"facts": [{"kind": "request_open", "id": "x"}]}, "fact_not_in_the_brief"),
    ({"facts": [{"kind": "last_entry", "actor": "codex", "id": "x"}]}, "fact_not_in_the_brief"), ("text", "shape"), ({"facts": [{}]}, "bad_fact"),
    ({"facts": [{"kind": "gap", "code": "overdue", "id": "x"}]}, "fact_not_in_the_brief"), ({"facts": [1]}, "bad_fact"),
    ({"facts": [{"kind": "request_open", "id": "x"}] * 13}, "fact_count"),
])
def test_a_selection_naming_anything_the_brief_does_not_hold_is_refused(world, summary, reason):
    j, code, codex, _ = world
    codex.request(["claude-code"], "review", "Look.")
    with pytest.raises(checkin.BadSummary) as caught:
        checkin.summarise(j.brief(now=NOW), configured(), lambda p, b: summary)
    assert str(caught.value) == reason


def test_every_fact_the_brief_holds_can_be_selected_and_reads_correctly(world):
    j, code, codex, _ = world
    req = codex.request(["claude-code"], "build", "Build.", due_at="2026-10-08T12:00:00Z")
    claim = Teammate("claude-code", j).claim(req.event_id, "checkout-a", 1)
    prop = code.propose("A plan")
    code.needs_you("Q?")
    brief = j.brief(now=NOW)
    picks = [{"kind": "claim_active", "id": claim.event_id}, {"kind": "proposal_open", "id": prop.event_id},
             {"kind": "last_entry", "actor": "claude-code", "id": brief["teammates"][0]["last"]["event_id"]},
             {"kind": "gap", "code": "overdue", "id": req.event_id}, {"kind": "no_result", "id": req.event_id, "recipient": "claude-code"}]
    lines = checkin.check_summary({"facts": picks[:4]}, brief)
    assert lines[0] == "claude-code holds checkout-a (generation 1)." and "has not been settled" in lines[1] and "was due 2026-10-08T12:00:00Z" in lines[3]
    codex_receipt = Teammate("claude-code", j).receive(req.event_id)
    assert codex_receipt.committed
    assert checkin.check_summary({"facts": picks[4:]}, j.brief(now=NOW))[0].endswith("but has returned no result.")


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
