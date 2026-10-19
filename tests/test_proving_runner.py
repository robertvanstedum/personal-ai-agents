"""Proving runner (scripts/staging/mc_probe/proving_runner.py): the real driver, journal, ledger and
collector against a scripted portal. No network, model, Docker or store access."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/staging/mc_probe"))
import proving_driver as d  # noqa: E402
from proving_budget import Budget, BudgetRefused  # noqa: E402
import proving_runner as r  # noqa: E402

PRINCIPAL = "mc-proving-dev-1"
INSTANCE = "a" * 32
API = d.GuildClient.API


def platform(message="Saved Board post-it #12", op=None):
    return {"status": "answered", "backend_kind": "platform", "message": message,
            "reply_note": {"text": message}, "operation": op or {"type": "board_postit", "saved": True, "id": 12}}


def mc(text="Here is my review.", turn_id="5d1f0a8e-1111-4222-8333-944455556666"):
    return {"status": "answered", "backend_kind": "cos", "reply_note": {"text": text}, "backend_turn_id": turn_id}


class Portal:
    """Scripted portal: answers /conversations and /notes, and pops one /mc/turns answer per turn."""
    def __init__(self, answers):
        self.answers, self.calls, self.conversations = list(answers), [], 0

    def __call__(self, path, data=None, form=False):
        self.calls.append(path)
        if path == API + "/conversations":
            self.conversations += 1
            return 200, {"conversation": {"id": f"c-{self.conversations}"}}
        if path == API + "/notes":
            return 200, {"note": {"request_id": data["idempotency_key"]}}
        if path == API + "/mc/turns":
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        raise AssertionError(f"unexpected {path}")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    journal = d.Journal(tmp_path / "journal")
    client = d.GuildClient("http://127.0.0.1:15001", PRINCIPAL, journal, None, instance_id=INSTANCE)
    client.csrf, client.environment = "synthetic", {"environment": "dev", "instance_id": INSTANCE}
    budget = Budget(tmp_path / "ledger" / "budget.sqlite")
    return client, budget


def runner(client, budget, answers, *, turns=5, seconds=3600, estimate="2", clock=None):
    portal = Portal([(200, a) if isinstance(a, dict) else a for a in answers])
    client._request = portal
    return r.Runner(client, budget, r.Limits(turns, seconds, estimate), clock=clock or Clock()), portal


META = {"suite_version": "journeys-v1-2026-10-19", "journey": "J1", "commit": "cdf7efcb",
        "mc_policy_sha256": "0" * 64, "model_route": "minimoi-gateway/minimoi-cos-agent"}


def test_platform_answer_releases_and_mc_answer_holds_the_estimate(env):
    client, budget = env
    run, _ = runner(client, budget, [platform(), mc()])
    summary = run.run(["Add to the Board: check the receipts", "Review this spec"])
    assert summary["stop_reason"] == "completed"
    first, second = summary["turns"]
    assert first["settlement"]["usd"] == 0.0 and first["settlement"]["source"] == "platform_answer"
    assert second["settlement"] == {"usd": None, "source": "estimate_held", "receipt_ids": []}
    assert second["backend_turn_id"] == "5d1f0a8e-1111-4222-8333-944455556666"
    # Only the MC turn's $2 estimate stays charged: $23 is still admissible today, $23.01 is not.
    budget.reserve("probe-ok-0001", "23")
    with pytest.raises(BudgetRefused):
        budget.reserve("probe-over-01", "0.01")


def test_run_record_from_the_journal(env):
    client, budget = env
    run, _ = runner(client, budget, [platform(), mc()])
    summary = run.run(["Add to the Board: check the receipts", "Review this spec"])
    record = r.run_record(client, summary, META, {"postit_rows": [], "gateway_receipts": []})
    assert record["contract"] == 2 and record["env_marker"] == {"environment": "dev", "instance_id": INSTANCE}
    assert record["identity"] == {"principal": PRINCIPAL, "test": True}
    assert [t["role"] for t in record["turns"]] == ["owner", "platform", "owner", "mc"]
    assert record["spend_admission"]["admitted"] is True and len(record["spend_admission"]["reservations"]) == 2
    assert record["cost"]["usd"] is None and record["errors"] == []
    assert record["run"]["stop_reason"] == "completed"


def test_record_written_once_and_private(env, tmp_path):
    client, budget = env
    run, _ = runner(client, budget, [mc()])
    record = r.run_record(client, run.run(["Review this spec"]), META, {})
    path = tmp_path / "run_1.json"
    r.write_record(path, record)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        r.write_record(path, record)


def test_journey_longer_than_turn_limit_refused_before_any_request(env):
    client, budget = env
    run, portal = runner(client, budget, [], turns=2)
    with pytest.raises(r.RunRefused, match="journey_exceeds_turn_limit"):
        run.run(["one", "two", "three"])
    assert portal.calls == [] and not list(client.journal.path.glob("*.json"))


def test_time_limit_never_starts_a_step_that_could_overrun(env):
    client, budget = env
    clock = Clock()
    run, portal = runner(client, budget, [mc(), mc()], seconds=900, clock=clock)
    original = portal.__call__

    def slow(path, data=None, form=False):
        clock.t += 200
        return original(path, data, form)
    client._request = slow
    summary = run.run(["first", "second"])
    # The first turn (new conversation, note, turn) took 600 s; a second needs up to 360 s more.
    assert summary["stop_reason"] == "time_limit" and len(summary["turns"]) == 1
    record = r.run_record(client, summary, META, {})
    assert "run stopped: time_limit" in record["errors"]


def test_cap_reached_stops_before_the_turn_is_sent(env):
    client, budget = env
    run, portal = runner(client, budget, [mc(), mc(), mc()], estimate="10")
    summary = run.run(["one", "two", "three"])
    assert summary["stop_reason"] == "refused:daily_cap_reached" and len(summary["turns"]) == 2
    assert portal.calls.count(API + "/mc/turns") == 2
    assert len(list(client.journal.path.glob("*-turn-intent.json"))) == 2


def test_uncertain_turn_stops_and_keeps_its_reservation(env):
    client, budget = env
    run, portal = runner(client, budget, [TimeoutError()])
    summary = run.run(["one"])
    assert summary["stop_reason"] == "uncertain:TimeoutError" and summary["turns"] == []
    assert len(list(client.journal.path.glob("*-turn-failure.json"))) == 1
    with pytest.raises(BudgetRefused):
        budget.reserve("probe-over-01", "23.01")
    record = r.run_record(client, summary, META, {})
    assert any("run stopped: uncertain:TimeoutError" == e for e in record["errors"])


def test_refused_turn_stops_without_retry(env):
    client, budget = env
    run, portal = runner(client, budget, [(409, {"error": "busy"}), mc()])
    summary = run.run(["one", "two"])
    assert summary["stop_reason"] == "refused:write_failed_or_uncertain_no_retry"
    assert portal.calls.count(API + "/mc/turns") == 1


@pytest.mark.parametrize("answer", [mc(), {"status": "answered", "backend_kind": "openclaw", "reply_note": {"text": "x"}},
                                    {"status": "answered", "backend_kind": "grok", "reply_note": {"text": "x"}}])
def test_only_a_platform_answer_is_released(env, answer):
    client, budget = env
    run, _ = runner(client, budget, [answer], estimate="25")
    summary = run.run(["one"])
    assert summary["turns"][0]["settlement"]["source"] == "estimate_held"
    with pytest.raises(BudgetRefused):
        budget.reserve("probe-over-01", "0.01")


def test_fresh_conversation_per_turn(env):
    client, budget = env
    run, _ = runner(client, budget, [mc(), mc()])
    summary = run.run(["one", "two"], fresh_conversation_per_turn=True)
    assert [t["conversation_id"] for t in summary["turns"]] == ["c-1", "c-2"]
    assert summary["turns"][0]["request_id"] != summary["turns"][1]["request_id"]


def test_one_conversation_by_default(env):
    client, budget = env
    run, _ = runner(client, budget, [mc(), mc()])
    assert {t["conversation_id"] for t in run.run(["one", "two"])["turns"]} == {"c-1"}


def test_one_run_per_journal(env):
    client, budget = env
    run, portal = runner(client, budget, [mc()])
    with r.run_lock(client.journal.path):
        with pytest.raises(r.RunRefused, match="another_proving_run_is_active"):
            run.run(["one"])
    assert portal.calls == []


def test_fresh_journal_required(env):
    client, budget = env
    run, _ = runner(client, budget, [mc(), mc()])
    run.run(["one"])
    with pytest.raises(r.RunRefused, match="fresh_journal_required"):
        run.run(["two"])


def test_turn_outside_the_runner_is_not_admitted(env):
    client, budget = env
    run, portal = runner(client, budget, [mc()])
    with pytest.raises(r.RunRefused, match="mc_turn_outside_the_runner"):
        client.write("/mc/turns", {"note_request_id": "proving-x", "request_id": "proving-x",
                                   "conversation_id": "c-1"}, "proving-x-turn")
    assert portal.calls == []


def test_client_must_be_logged_in_with_the_marker(tmp_path):
    client = d.GuildClient("http://127.0.0.1:15001", PRINCIPAL, d.Journal(tmp_path / "j"), None, instance_id=INSTANCE)
    with pytest.raises(r.RunRefused, match="log_in_and_verify"):
        r.Runner(client, Budget(tmp_path / "l" / "b.sqlite"), r.Limits(1, 600, "1"))


@pytest.mark.parametrize("args", [(0, 600, "1"), (51, 600, "1"), (True, 600, "1"), (1, 359, "1"), (1, 4 * 3600 + 1, "1"),
                                  (1, 600, "0"), (1, 600, 1), (1, 600, "-1"), (1, True, "1")])
def test_limits_are_bounded(args):
    with pytest.raises((r.RunRefused, BudgetRefused)):
        r.Limits(*args)


def test_uncertain_admission_day_survives_midnight(env):
    """An unresolved turn from 23:58 Chicago still counts at 00:05; one from yesterday afternoon does not."""
    client, budget = env
    budget.reserve("late-turn-0001", "20", at=datetime(2026, 10, 19, 4, 58, tzinfo=timezone.utc))
    with pytest.raises(BudgetRefused):
        budget.reserve("next-day-0001", "6", at=datetime(2026, 10, 19, 5, 5, tzinfo=timezone.utc))
    later = datetime(2026, 10, 19, 5, 5, tzinfo=timezone.utc) + timedelta(minutes=15)
    assert budget.reserve("next-day-0002", "6", at=later)["admitted"]


def test_runner_record_passes_the_grader_when_available(env):
    """J1b end to end: runner, journal, collector, then the private grader if it is pointed at."""
    import importlib.util
    import json
    import os
    client, budget = env
    text = "Master Craftsman post-it test"
    run, _ = runner(client, budget, [platform(f"Saved Board post-it #12: {text}")])
    summary = run.run([f"Create a post-it saying {text}"])
    at = datetime.fromisoformat(summary["started_utc"]).isoformat()
    row = {"id": 12, "text": text, "author": PRINCIPAL, "author_kind": "owner", "created_at": at, "binned_at": None}
    record = r.run_record(client, summary, {**META, "journey": "J1b"}, {"postit_rows": [row], "gateway_receipts": []})
    assert record["receipts"]["board_rows"][0]["id"] == 12 and record["unbound"] == [] and record["errors"] == []
    json.dumps(record)
    loop = Path(os.environ.get("MINIMOI_PROVING_LOOP_DIR", "/nonexistent"))
    if not (loop / "grade.py").exists():
        pytest.skip("MINIMOI_PROVING_LOOP_DIR not set to the proving-loop grader folder")
    spec = importlib.util.spec_from_file_location("loop_grade", loop / "grade.py")
    grade = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grade)
    journey = next(j for j in json.loads((loop / "journeys_v1.json").read_text())["journeys"] if j["id"] == "J1b")
    out = grade.grade_run(journey, record, None)
    assert out["grade"] == "functional_pass_pending_review" and out["unproven"] == [], out


@pytest.mark.parametrize("answer", [{"status": "not_kept", "backend_kind": "cos", "reply_note": None},
                                    {"status": "error", "failure_class": "backend_error", "reply_note": None},
                                    {"status": "unavailable", "backend_kind": None, "reply_note": None},
                                    {"status": "job", "job": {"id": "j-1"}, "message": "Started"},
                                    {"backend_kind": "cos", "reply_note": {"text": "no status"}}])
def test_http_200_that_is_not_an_answer_stops_before_the_next_text(env, answer):
    client, budget = env
    run, portal = runner(client, budget, [answer, mc()])
    summary = run.run(["one", "two"])
    assert summary["stop_reason"] == f"not_answered:{answer.get('status')}"
    assert len(summary["turns"]) == 1 and summary["turns"][0]["settlement"]["source"] == "estimate_held"
    # The second text was never sent: one note, one turn, one turn intent.
    assert portal.calls.count(API + "/notes") == 1 and portal.calls.count(API + "/mc/turns") == 1
    assert len(list(client.journal.path.glob("*-turn-intent.json"))) == 1
    with pytest.raises(BudgetRefused):                      # the $2 estimate stays charged
        budget.reserve("probe-over-01", "23.01")
    record = r.run_record(client, summary, META, {})
    assert f"run stopped: not_answered:{answer.get('status')}" in record["errors"]


@pytest.mark.parametrize("kind", ["stub", None, "mystery"])
def test_stub_or_unknown_backend_stops_and_keeps_the_reservation(env, kind):
    client, budget = env
    answer = {"status": "answered", "backend_kind": kind, "reply_note": {"text": "scripted"}}
    run, portal = runner(client, budget, [answer, mc()])
    summary = run.run(["one", "two"])
    assert summary["stop_reason"] == f"unrecognized_backend:{kind}"
    assert portal.calls.count(API + "/notes") == 1 and portal.calls.count(API + "/mc/turns") == 1
    with pytest.raises(BudgetRefused):
        budget.reserve("probe-over-01", "23.01")


def test_unsaved_platform_operation_is_a_failed_capability_never_a_success(env):
    client, budget = env
    refusal = platform("I can't save that post-it: it is empty.", {"type": "board_postit", "saved": False})
    run, _ = runner(client, budget, [refusal, mc()])
    summary = run.run(["Create an empty post-it", "Review this spec"])
    assert summary["stop_reason"] == "completed"
    first = summary["turns"][0]
    assert first["capability_failed"] is True and first["settlement"]["usd"] == 0.0   # no model was asked
    record = r.run_record(client, summary, META, {"postit_rows": [], "gateway_receipts": []})
    assert record["run"]["capability_failures"] == [first["request_id"]]
    assert record["receipts"]["board_rows"] == []


def test_platform_answer_without_a_saved_flag_is_not_a_failure(env):
    client, budget = env
    run, _ = runner(client, budget, [platform("Library: 3 results", {"type": "library_search"})])
    summary = run.run(["Search the library for receipts"])
    assert summary["turns"][0]["capability_failed"] is False and summary["stop_reason"] == "completed"
