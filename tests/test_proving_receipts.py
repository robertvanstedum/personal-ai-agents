"""Contract-v2 receipt collector (scripts/staging/mc_probe/proving_receipts.py): bound receipts only,
read from store snapshots, unknown cost stays None. No portal, model, gateway or Telegram access."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("proving_receipts", ROOT / "scripts/staging/mc_probe/proving_receipts.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

P = "mc-proving-dev-1"
T0, T1 = "2026-10-19T17:00:00Z", "2026-10-19T17:02:00Z"
ROUTE = "minimoi-gateway/minimoi-cos-agent"


def ex(text, response, *, conv="c-1", note="n-1", sent="2026-10-19T17:00:05Z", answered="2026-10-19T17:00:20Z", code=200):
    return {"owner_text": text, "conversation_id": conv, "note_id": note, "sent_utc": sent, "answered_utc": answered,
            "status_code": code, "response": response}


def platform(op, message="ok"):
    return {"status": "answered", "backend_kind": "platform", "message": message, "operation": op}


def mc(text, board_outcome=None):
    body = {"status": "answered", "backend_kind": "cos", "reply_note": {"text": text}}
    if board_outcome:
        body["board_outcome"] = board_outcome
    return body


def meta(**over):
    base = {"identity": {"principal": P, "test": True}, "env": "dev", "env_marker": {"environment": "dev", "instance_id": "dev-instance-7"},
            "expected_instance_id": "dev-instance-7", "commit": "b884af11",
            "mc_policy_sha256": "00ef0216", "model_route": ROUTE, "suite_version": "journeys-v1-2026-10-19", "journey": "J1b",
            "spend_admission": {"admitted": True, "cap_usd": 25}, "started_utc": T0, "finished_utc": T1}
    base.update(over)
    return base


def row(pid, author=P, at="2026-10-19T17:00:10Z", text="Master Craftsman post-it test"):
    return {"id": pid, "text": text, "author": author, "author_kind": "owner", "created_at": at, "binned_at": None}


# ── roles come from backend_kind, never from text ─────────────────────────────────

def test_platform_mc_and_refused_replies_map_to_roles_and_operations():
    turns, errors = pr.turns_from_exchanges([
        ex("Create a post-it saying x", platform({"type": "board_postit", "saved": True, "id": 12})),
        ex("Record the outcome on the board", mc("Done.", {"state": "saved", "postit_id": 13, "source_id": 4}), note="n-2"),
        ex("hello", {"error": "busy", "message": "One turn at a time."}, note="n-3", code=409),
    ], P)
    assert [t["role"] for t in turns] == ["owner", "platform", "owner", "mc", "owner", "platform"]
    assert turns[1]["operation"]["expected_author"] == P
    assert turns[3]["operation"] == {"type": "board_postit", "saved": True, "id": 13, "via": "board_outcome",
                                     "source_id": 4, "expected_author": pr.MC_AUTHOR}
    assert turns[5]["backend_kind"] == "portal_refusal" and errors == ["n-3: 409 busy"]


# ── Board rows: bound by id, expected author and time ─────────────────────────────

def test_board_rows_bind_by_operation_author_and_window():
    turns, _ = pr.turns_from_exchanges([ex("x", platform({"type": "board_postit", "saved": True, "id": 12})),
                                        ex("y", mc("ok", {"state": "saved", "postit_id": 13, "source_id": 4}), note="n-2")], P)
    rows = [row(12), row(13, author=pr.MC_AUTHOR), row(99)]
    bound, unbound = pr.board_receipts(rows, turns, T0, T1)
    assert [r["id"] for r in bound] == [12, 13] and unbound == ["board_row 99"]


@pytest.mark.parametrize("bad", [row(12, author="robert"), row(12, author=pr.MC_AUTHOR), row(12, at="2026-10-18T09:00:00Z")])
def test_board_row_with_wrong_author_or_time_is_unbound(bad):
    turns, _ = pr.turns_from_exchanges([ex("x", platform({"type": "board_postit", "saved": True, "id": 12}))], P)
    bound, unbound = pr.board_receipts([bad], turns, T0, T1)
    assert bound == [] and unbound == ["board_row 12"]


def test_an_operation_without_a_stored_row_is_reported():
    turns, _ = pr.turns_from_exchanges([ex("x", platform({"type": "board_postit", "saved": True, "id": 12}))], P)
    assert pr.board_receipts([], turns, T0, T1) == ([], ["board_row 12 (operation without a stored row)"])


# ── notes: operation marker plus the run's dates ──────────────────────────────────

MEMORY = ("# COS memory\n\n<!-- cos-operation:op-1 -->\n[note] 2026-10-19: the spare key is in the blue box\n"
          "\n<!-- cos-operation:op-old -->\n[note] 2026-10-01: something older\n")


def test_note_binds_by_operation_and_date():
    turns, _ = pr.turns_from_exchanges([ex("Make a note that …", platform({"type": "cos_note", "saved": True, "operation_id": "op-1"}))], P)
    bound, unbound = pr.note_receipts(MEMORY, turns, T0, T1)
    assert bound == [{"operation_id": "op-1", "text": "the spare key is in the blue box", "created_date": "2026-10-19",
                      "source": "file:cos_memory.md#cos-operation"}] and unbound == []


def test_note_from_another_day_or_operation_does_not_bind():
    turns, _ = pr.turns_from_exchanges([ex("…", platform({"type": "cos_note", "saved": True, "operation_id": "op-old"}))], P)
    assert pr.note_receipts(MEMORY, turns, T0, T1) == ([], ["note op-old (no stored entry in the run's dates)"])


# ── reminders and deliveries ──────────────────────────────────────────────────────

def rem(state="delivered", created="2026-10-19T17:00:06Z", rid="rem-aaaaaaaaaaaa", op="op-r", mid=4242):
    return {"id": rid, "operation_id": op, "text": "stretch", "due_utc": "2026-10-19T17:03:00Z", "created_utc": created,
            "state": state, "delivered_utc": "2026-10-19T17:03:20Z" if state == "delivered" else None, "telegram_message_id": mid}


def reminder_turns():
    return pr.turns_from_exchanges([ex("Remind me in 3 minutes to stretch", platform(
        {"type": "reminder_set", "saved": True, "reminder_id": "rem-aaaaaaaaaaaa", "operation_id": "op-r"}))], P)[0]


def test_reminder_and_its_delivery_bind():
    reminders, deliveries, unbound = pr.reminder_receipts({"reminders": [rem(), rem(rid="rem-bbbbbbbbbbbb", op="op-x")]},
                                                         reminder_turns(), T0, T1)
    assert [r["reminder_id"] for r in reminders] == ["rem-aaaaaaaaaaaa"] and unbound == []
    assert deliveries == [{"reminder_id": "rem-aaaaaaaaaaaa", "message_id": 4242, "delivered_utc": "2026-10-19T17:03:20Z",
                           "source": "file:cos_reminders/reminders.json"}]


def test_pending_or_uncertain_reminder_has_no_delivery_and_old_records_do_not_bind():
    assert pr.reminder_receipts({"reminders": [rem(state="pending")]}, reminder_turns(), T0, T1)[1] == []
    assert pr.reminder_receipts({"reminders": [rem(state="uncertain", mid=None)]}, reminder_turns(), T0, T1)[1] == []
    reminders, _, unbound = pr.reminder_receipts({"reminders": [rem(created="2026-10-18T09:00:00Z")]}, reminder_turns(), T0, T1)
    assert reminders == [] and unbound == ["reminder rem-aaaaaaaaaaaa (created outside the run)"]


# ── cost: settlement stays None; the window sum is only an estimate ─────────────

def receipt(at, usd=0.01, model=ROUTE, rid="00000000-0000-0000-0000-000000000001"):
    return {"receipt_id": rid, "occurred_at": at, "logical_model": model, "cost_usd": usd}


def cost_case(receipts, response=None):
    exchanges = [ex("q", response or mc("answer"))]
    turns, _ = pr.turns_from_exchanges(exchanges, P)
    return pr.cost(receipts, exchanges, turns, ROUTE)


def test_window_sum_is_an_estimate_never_a_settled_cost():
    out = cost_case([receipt("2026-10-19T17:00:10Z", 0.012), receipt("2026-10-19T17:00:15Z", 0.003, rid="r2")])
    assert out["usd"] is None and out["window_estimate_usd"] == 0.015 and "not settled" in out["reason"]


@pytest.mark.parametrize("bad", [-1, True, False, float("nan"), float("inf"), "0.01", None])
def test_invalid_cost_values_make_no_estimate(bad):
    out = cost_case([receipt("2026-10-19T17:00:10Z", bad)])
    assert out["usd"] is None and out["window_estimate_usd"] is None
    assert any("valid cost" in p for p in out["estimate_problems"])


def test_repeated_or_missing_receipt_ids_make_no_estimate():
    dup = cost_case([receipt("2026-10-19T17:00:10Z"), receipt("2026-10-19T17:00:12Z")])          # same id twice
    blank = cost_case([receipt("2026-10-19T17:00:10Z", rid="")])
    for out in (dup, blank):
        assert out["usd"] is None and out["window_estimate_usd"] is None
        assert any("receipt id" in p for p in out["estimate_problems"])


@pytest.mark.parametrize("receipts,response", [
    ([receipt("2026-10-19T17:00:10Z", model="other-model")], None),                    # foreign traffic
    ([], None),                                                                         # model turn, no receipt
    ([], platform({"type": "cos_note", "saved": True, "operation_id": "o"})),          # platform turn, no receipt
])
def test_other_traffic_or_missing_receipts_make_no_estimate(receipts, response):
    out = cost_case(receipts, response)
    assert out["usd"] is None and out["window_estimate_usd"] is None and out["estimate_problems"]


# ── the whole record ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("over", [{"identity": {"principal": "robert", "test": True}}, {"identity": {"principal": P, "test": False}},
                                  {"identity": {"principal": "mc-proving-", "test": True}}, {"identity": {"principal": "owner", "test": True}},
                                  {"env": "prod"}, {"env_marker": None}, {"env_marker": "dev-instance-7"},
                                  {"env_marker": {"environment": "prod", "instance_id": "dev-instance-7"}},
                                  {"env_marker": {"environment": "dev", "instance_id": "other-instance"}},
                                  {"env_marker": {"environment": "dev", "instance_id": ""}, "expected_instance_id": ""},
                                  {"expected_instance_id": None}])
def test_refuses_owner_identity_non_dev_or_missing_environment_marker(over):
    with pytest.raises(pr.Refused):
        pr.build_run_record(meta(**over), [], {})


def test_record_is_contract_two_and_passes_the_grader_when_available():
    exchanges = [ex("Create a post-it saying Master Craftsman post-it test",
                    platform({"type": "board_postit", "saved": True, "id": 12}, "Saved Board post-it #12: Master Craftsman post-it test"))]
    record = pr.build_run_record(meta(), exchanges, {"postit_rows": [row(12)], "gateway_receipts": []})
    assert record["contract"] == 2 and record["receipts"]["board_rows"][0]["id"] == 12
    assert record["cost"]["usd"] is None and record["unbound"] == [] and record["errors"] == []
    json.dumps(record)                                                                # serializable as written
    # The proving-loop grader lives in the private planning folder; point at it to check the pair end to end.
    loop = Path(os.environ.get("MINIMOI_PROVING_LOOP_DIR", "/nonexistent"))
    if not (loop / "grade.py").exists():
        pytest.skip("MINIMOI_PROVING_LOOP_DIR not set to the proving-loop grader folder")
    spec = importlib.util.spec_from_file_location("loop_grade", loop / "grade.py")
    grade = importlib.util.module_from_spec(spec); spec.loader.exec_module(grade)
    journey = next(j for j in json.loads((loop / "journeys_v1.json").read_text())["journeys"] if j["id"] == "J1b")
    out = grade.grade_run(journey, record, None)
    assert out["grade"] == "functional_pass_pending_review" and out["unproven"] == [], out
