"""The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §4.4, §5) in the host core, through the fake adapter of
test_direct_host.py (an in-memory store, not the Guild). Each test fails on the tree before its item's commit. No provider,
no Keychain, no microphone, no network."""
from test_direct_host import BASE, ENV, OWNER, consult, rig, start  # noqa: F401


def bare(r, sid, **fields):
    return r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "call_1", "name": "consult",
                                                                 "owner_transcript_item": "What is on my agenda?", **fields})


# ── Item 1: the owner turn is named by its capture id, owner_item_id (§5.1) ─────────────────────────────────────────────

def test_a_consult_names_its_owner_turn_by_capture_id_and_nothing_else_is_accepted(rig):
    r = rig
    sid = start(r)
    assert bare(r, sid, owner_turn="u1").status_code == 400                       # A1's turn count is not an identity
    assert bare(r, sid, owner_item_id="u1").status_code == 400                    # not a capture id
    assert bare(r, sid, owner_item_id="t").status_code == 400
    assert not r.host.turns
    answer = bare(r, sid, owner_item_id="t3")
    assert answer.status_code == 200 and len(r.host.turns) == 1


# ── Item 2: durable admission: a consult_request record first, the first write wins (§5.1, §4.4) ───────────────────────

def records_of(r, sid):
    return {rid: rec for (s, rid), rec in r.store.kept_records.items() if s == sid}


def test_the_request_is_recorded_durably_before_dispatch_and_its_outcome_after(rig):
    r = rig
    sid = start(r)
    seen = []
    turn = r.host.typed_turn
    r.host.typed_turn = lambda *a, **kw: seen.append(sorted(records_of(r, sid))) or turn(*a, **kw)
    answer = consult(r, sid, call_id="call_1", turn="t5").get_json()
    assert seen == [["consult_request:t5"]]                                       # written before the typed turn ran
    request, outcome = records_of(r, sid)["consult_request:t5"], records_of(r, sid)["consult_outcome:t5"]
    assert request["schema"] == 1 and request["kind"] == "consult_request" and request["owner_item_id"] == "t5"
    assert request["at"] and len(request["text_sha256"]) == 64 and "agenda" not in str(request)   # the words' digest only
    assert outcome["kind"] == "consult_outcome" and outcome["outcome"] == "done" and outcome["call_id"] == "call_1"
    assert outcome["tool"] == "consult" and outcome["result"]["receipt"] == answer["receipt"]


def test_a_second_dispatch_of_a_recorded_key_gets_its_stored_outcome_and_runs_nothing(rig):
    """The store answers, not this process's memory: the key's outcome was kept (here by another process)."""
    from core.realtime_voice.direct_host import digest, record
    r = rig
    sid = start(r)
    r.store.put("owner", sid, record("consult_request", "consult_request:t7", owner_item_id="t7",
                                     text_sha256=digest("What is on my agenda?")))
    r.store.put("owner", sid, record("consult_outcome", "consult_outcome:t7", owner_item_id="t7", tool="save_note",
                                     call_id="call_first", outcome="done",
                                     result={"ok": True, "saved": True, "receipt": "op-9", "spoken": "Saved."}))
    answer = consult(r, sid, call_id="host-2", turn="t7").get_json()                # Check after the model
    assert answer["saved"] is True and answer["same_turn"] is True and answer["first_tool"] == "save_note"
    assert answer["outcome"] == "done" and not r.host.turns


def test_a_recorded_key_with_no_outcome_and_nothing_running_is_unknown_and_never_rerun(rig):
    """The process that admitted it died mid-turn: the request stays unknown, and nothing re-runs by itself."""
    from core.realtime_voice.direct_host import digest, record
    r = rig
    sid = start(r)
    r.store.put("owner", sid, record("consult_request", "consult_request:t8", owner_item_id="t8",
                                     text_sha256=digest("What is on my agenda?")))
    answer = consult(r, sid, call_id="call_retry", turn="t8")
    assert answer.status_code == 200 and answer.get_json()["outcome"] == "unknown" and not r.host.turns
    assert "did not go through" not in answer.get_json()["spoken"]


def test_the_same_key_with_other_words_is_refused_and_runs_nothing(rig):
    r = rig
    sid = start(r)
    consult(r, sid, call_id="call_1", turn="t1", words="What is on my agenda?")
    other = consult(r, sid, call_id="call_2", turn="t1", words="Delete the agenda.")
    assert other.status_code == 409 and other.get_json()["error"] == "turn_mismatch" and len(r.host.turns) == 1


def test_a_request_the_store_cannot_keep_is_never_dispatched(rig):
    r = rig
    sid = start(r)
    r.store.put_ok = False
    answer = consult(r, sid, call_id="call_1", turn="t1").get_json()
    assert answer["ok"] is False and answer["error"] == "not_recorded" and not r.host.turns
    assert "nothing was done" in answer["spoken"]


def test_a_retry_check_and_a_new_call_id_on_one_key_are_one_effect_through_one_dispatcher(rig):
    r = rig
    sid = start(r)
    model = consult(r, sid, call_id="call_model", name="save_note", words="save a note: the kiln", turn="t2").get_json()
    check = consult(r, sid, call_id="host-1", words="save a note: the kiln", turn="t2").get_json()   # Check after the model
    retry = consult(r, sid, call_id="call_model", name="save_note", words="save a note: the kiln", turn="t2").get_json()
    assert len(r.host.turns) == 1 and retry == model and check["saved"] is True and check["first_tool"] == "save_note"
    assert check["same_turn"] is True and len([k for k in records_of(r, sid) if k.startswith("consult_request")]) == 1


# ── Item 3: operation ids derive from the key (§5.1) ────────────────────────────────────────────────────────────────

def test_the_core_names_one_operation_per_owner_turn_and_host_by_uuid5():
    import uuid
    from core.realtime_voice.direct_host import VOICE_NS, operation_id
    sid = "0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b"
    one = operation_id("cos", sid, "t5")
    assert uuid.UUID(one).version == 5 and one == str(uuid.uuid5(VOICE_NS, f"cos:{sid}:t5"))
    assert one == operation_id("cos", sid, "t5")                                   # a retry: the same operation
    assert len({one, operation_id("cos", sid, "t6"), operation_id("mc", sid, "t5"),
                operation_id("cos", "1b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b", "t5")}) == 4
    assert str(VOICE_NS) == str(uuid.uuid5(uuid.NAMESPACE_URL, "https://minimoi.ai/spec/165/voice-operation"))
    for bad in (("", sid, "t5"), ("a:b", sid, "t5"), ("cos", "not-a-session", "t5"), ("cos", sid, "u5")):
        try:
            operation_id(*bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_the_hosts_typed_turn_is_given_the_owner_turn_its_operation_derives_from(rig):
    r = rig
    sid = start(r)
    consult(r, sid, call_id="call_1", turn="t5")
    consult(r, sid, call_id="host-2", turn="t5")
    assert r.host.items == ["t5"]


# ── R1 P3-5, R2 S3: a refusal that runs nothing binds no owner turn (one effect per turn) ───────────────────────────────

def test_a_hosts_own_refusal_binds_nothing_and_the_other_tool_still_acts_on_the_turn(rig):
    r = rig
    sid = start(r)
    r.host.refusals = {"save_note": {"error": "not_a_note", "spoken": "Nothing was saved."}}
    note = consult(r, sid, call_id="m1", name="save_note", turn="t1").get_json()
    asked = consult(r, sid, call_id="m2", name="consult", turn="t1").get_json()
    assert note["error"] == "not_a_note" and not note.get("same_turn")
    assert asked["receipt"] == "turn-2" and not asked.get("same_turn") and len(r.host.turns) == 1   # the refusal counted (FR N6)


def test_an_unavailable_conversation_or_an_unkept_request_binds_nothing(rig):
    r = rig
    sid = start(r)
    r.host.down = 1
    assert consult(r, sid, call_id="c1", turn="t2").get_json()["error"] == "conversation"
    r.store.fail_keeps = 1
    assert consult(r, sid, call_id="c2", turn="t2").get_json()["error"] == "not_kept"
    assert not r.host.turns and "consult_request:t2" not in records_of(r, sid)
    later = consult(r, sid, call_id="c3", turn="t2").get_json()
    assert later["receipt"] == "turn-1" and len(r.host.turns) == 1


# ── Item 4 / R1 P1-1: four outcomes with their words; dispatched with no result is unknown, everywhere ──────────────────

def test_every_consult_answer_is_one_of_four_outcomes_with_its_words(rig):
    from core.realtime_voice import direct_host as core
    r = rig
    sid = start(r)
    assert consult(r, sid, call_id="c1", turn="t1").get_json()["outcome"] == "done"
    r.host.refusals = {"save_note": {"error": "not_a_note", "spoken": "Nothing was saved."}}
    refused = consult(r, sid, call_id="c2", name="save_note", turn="t2").get_json()
    assert refused["outcome"] == "not_done" and refused["ok"] is False and refused["spoken"] == "Nothing was saved."
    assert core.FAILED == "That did not go through. Nothing was done."
    assert core.PENDING == "That's taking longer; the answer will be in the conversation."
    assert core.UNKNOWN == "I didn't get the result back. Check it in writing before asking again."


def test_an_exception_after_dispatch_is_unknown_in_the_answer_the_receipt_the_record_and_every_replay(rig):
    r = rig
    sid = start(r)

    def boom(*a, **kw):
        r.host.turns.append(a)
        raise RuntimeError("after dispatch")
    r.host.typed_turn = boom
    answer = consult(r, sid, call_id="c1", turn="t1").get_json()
    assert answer["outcome"] == "unknown" and answer["ok"] is False and "did not go through" not in answer["spoken"]
    receipt = r.store.rows[-1]["text"]
    assert receipt.startswith("Outcome unknown (") and r.host.receipts.parts(receipt)["outcome"] == "result unknown"
    assert records_of(r, sid)["consult_outcome:t1"]["outcome"] == "unknown"
    assert consult(r, sid, call_id="c1", turn="t1").get_json()["outcome"] == "unknown"              # the page's retry
    assert consult(r, sid, call_id="host-2", turn="t1").get_json()["outcome"] == "unknown"          # Check after it
    assert len(r.host.turns) == 1


def test_a_host_that_reports_unknown_is_said_and_revealed_as_unknown(rig):
    r = rig
    sid = start(r)
    r.host.typed_turn = lambda *a, **kw: {"ok": False, "error": "timeout_uncertain", "outcome": "unknown", "spoken": "x"}
    answer = consult(r, sid, call_id="c1", turn="t1").get_json()
    assert answer["outcome"] == "unknown" and answer["spoken"].startswith("I didn't get the result back")
    assert r.store.rows[-1]["text"].startswith("Outcome unknown (timeout_uncertain).")


# ── Item 4: the 25-second worker, pending, the receipt at completion, the result on the next heartbeat ─────────────────

def test_a_long_turn_is_pending_then_kept_when_it_completes_and_told_once_on_the_next_heartbeat(rig, monkeypatch):
    import threading
    from core.realtime_voice import direct_host as core
    from test_direct_host import post
    r = rig
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.05)
    sid = start(r)
    release, entered = threading.Event(), threading.Event()
    turn = r.host.typed_turn

    def slow(*a, **kw):
        entered.set()
        assert release.wait(5)
        return turn(*a, **kw)
    r.host.typed_turn = slow
    answer = consult(r, sid, call_id="call_1", turn="t1")
    assert answer.status_code == 200 and answer.get_json()["outcome"] == "pending" and entered.is_set()
    assert answer.get_json()["spoken"] == core.PENDING
    assert "consult_outcome:t1" not in records_of(r, sid)                       # still running
    assert consult(r, sid, call_id="host-2", turn="t1").get_json()["outcome"] == "pending"   # Check meanwhile
    assert "completed" not in post(r, sid, []).get_json()
    release.set()
    for _ in range(200):
        if "consult_outcome:t1" in records_of(r, sid):
            break
        threading.Event().wait(0.01)
    assert records_of(r, sid)["consult_outcome:t1"]["outcome"] == "done"
    assert r.store.rows[-1]["text"].startswith("Answered. Reply turn-1.")          # the receipt, when it completed
    beat = post(r, sid, []).get_json()
    assert beat["completed"] == [{"owner_item_id": "t1", "tool": "consult", "outcome": "done", "spoken": "Two items."}]
    assert "completed" not in post(r, sid, []).get_json()                          # told once
    assert consult(r, sid, call_id="call_1", turn="t1").get_json()["receipt"] == "turn-1"   # the page's retry: the result
    assert len(r.host.turns) == 1


def test_a_turn_that_finishes_in_time_is_answered_and_not_told_again(rig):
    from test_direct_host import post
    r = rig
    sid = start(r)
    assert consult(r, sid, call_id="call_1", turn="t1").get_json()["outcome"] == "done"
    assert "completed" not in post(r, sid, []).get_json()


# ── R1 P2-3: a replay to the other tool is not that tool's own success ───────────────────────────────────────────────

def test_two_requests_in_one_utterance_the_second_tool_is_told_it_was_already_handled(rig):
    r = rig
    sid = start(r)
    words = "save a note: the kiln, and what is on my agenda?"
    note = consult(r, sid, call_id="m1", name="save_note", words=words, turn="t1").get_json()
    asked = consult(r, sid, call_id="m2", name="consult", words=words, turn="t1").get_json()
    assert note["saved"] is True and len(r.host.turns) == 1
    assert asked["same_turn"] is True and asked["first_tool"] == "save_note" and asked["outcome"] == "done"
    assert asked["spoken"] == "That was already handled: Saved. Ask the other part on its own."
    assert "receipt" not in asked and asked["saved"] is True                    # the note's receipt, not a consult's answer


def test_a_consult_then_the_models_save_note_is_not_saved_unless_the_first_saved_a_note(rig):
    r = rig
    sid = start(r)
    consult(r, sid, call_id="host-1", name="consult", turn="t2")               # Check: an answer, no note
    note = consult(r, sid, call_id="m2", name="save_note", turn="t2").get_json()
    assert note["saved"] is False and note["first_tool"] == "consult" and note["same_turn"] is True
    assert note["spoken"].startswith("That was already handled: Two items.")


def test_the_tool_texts_say_how_to_speak_same_turn_and_pending():
    from core.realtime_voice.direct_host import tools
    for tool in tools({"name": "Test Person", "notes": ("save a note:",)}):
        assert "'same_turn'" in tool["description"] and "pending" in tool["description"]


# ── Item 5: reconciliation, GET …/consult/<session>/<owner_item_id>: the stored outcome, running nothing ────────────────

def read(r, sid, item, cid="owner", client=None):
    return (client or r.client).get(BASE + f"/consult/{sid}/{item}?conversation_id={cid}", headers=OWNER)


def test_reconciliation_reads_the_stored_outcome_runs_nothing_and_works_after_end(rig):
    r = rig
    sid = start(r)
    done = consult(r, sid, call_id="call_1", turn="t1").get_json()
    got = read(r, sid, "t1")
    assert got.status_code == 200 and got.headers["Cache-Control"] == "no-store"
    assert got.get_json()["outcome"] == "done" and got.get_json()["receipt"] == done["receipt"]
    assert got.get_json()["tool"] == "consult" and got.get_json()["recorded"] is True
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER)
    assert read(r, sid, "t1").get_json()["receipt"] == done["receipt"] and len(r.host.turns) == 1
    assert "read" in r.host.kinds
    missing = read(r, sid, "t9")
    assert missing.status_code == 404 and missing.get_json()["outcome"] == "unknown" and missing.get_json()["recorded"] is False
    assert read(r, sid, "x9").status_code == 422
    assert read(r, sid, "t1", cid="elsewhere").status_code == 404                  # not this session's conversation
    other = r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers={**OWNER, "X-Owner": "guest"})
    assert other.status_code == 403


def test_after_a_restart_the_store_answers_and_a_request_with_no_outcome_stays_unknown(rig):
    from flask import Flask
    from core.realtime_voice.direct_host import digest, install_direct_voice, record
    r = rig
    sid = start(r)
    consult(r, sid, call_id="call_1", turn="t1")
    r.store.put("owner", sid, record("consult_request", "consult_request:t2", owner_item_id="t2",
                                     text_sha256=digest("What is on my agenda?")))      # admitted, then the process died
    restarted = Flask("restarted")                                                   # a new process: same store, no memory
    install_direct_voice(restarted, r.host, url_prefix="/tp/voice", environ=dict(ENV), budget=r.budget, summary_http=r.gateway)
    client = restarted.test_client()
    assert read(r, sid, "t1", client=client).get_json()["outcome"] == "done"
    lost = read(r, sid, "t2", client=client).get_json()
    assert lost["outcome"] == "unknown" and lost["recorded"] is True and len(r.host.turns) == 1


def test_a_turn_still_running_reads_pending_and_its_result_is_told_when_it_lands(rig, monkeypatch):
    import threading
    from core.realtime_voice import direct_host as core
    from test_direct_host import post
    r = rig
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 5)
    sid = start(r)
    release, entered = threading.Event(), threading.Event()
    turn = r.host.typed_turn
    r.host.typed_turn = lambda *a, **kw: (entered.set(), release.wait(5), turn(*a, **kw))[2]
    answered = []
    worker = threading.Thread(target=lambda: answered.append(consult(r, sid, call_id="call_1", turn="t1").get_json()))
    worker.start()
    assert entered.wait(5)
    assert read(r, sid, "t1").get_json()["outcome"] == "pending"                 # the page's answer was lost meanwhile
    release.set()
    worker.join(5)
    assert answered[0]["outcome"] == "done"
    assert post(r, sid, []).get_json()["completed"][0]["owner_item_id"] == "t1"   # told: the read said pending


# ── Item 6: a receipt that lands after the summary is kept, marked, and never remakes the summary ───────────────────────

def test_a_receipt_after_the_summary_is_kept_marked_and_the_summary_is_not_remade(rig, monkeypatch):
    import threading
    from core.realtime_voice import direct_host as core
    from test_direct_host import post, turn
    r = rig
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.05)
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Run the nightly job, then we're done.")])
    release = threading.Event()
    slow = r.host.typed_turn
    r.host.typed_turn = lambda *a, **kw: (release.wait(5), slow(*a, **kw))[1]
    assert consult(r, sid, call_id="call_1", turn="t0").get_json()["outcome"] == "pending"
    summary = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    assert summary["state"] == "proposed" and "still running" in summary["written"]
    release.set()
    for _ in range(200):
        if "consult_outcome:t0" in records_of(r, sid):
            break
        threading.Event().wait(0.01)
    receipt = r.store.rows[-1]["text"]
    assert receipt.startswith("[Arrived after the summary] Answered. Reply turn-1.")
    assert r.host.receipts.parts(receipt)["outcome"] == "answered · arrived after the summary"
    assert len(r.host.prompts) == 1 and r.store.summary("owner", sid) == summary["written"]   # not remade
    again = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    assert again["repeated"] is True and len(r.host.prompts) == 1


def test_a_receipt_before_the_summary_is_not_marked(rig):
    r = rig
    sid = start(r)
    consult(r, sid, call_id="call_1", turn="t0")
    assert r.store.rows[-1]["text"].startswith("Answered.")


# ── Item 7: the close's report, complete | incomplete(n) in the end marker, later posts refused and counted ────────────
# (R1 P3-6, R2 N4, N5, N6 with it)

def test_the_close_carries_its_report_and_the_marker_counts_only_what_the_server_does_not_hold(rig):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Kept."), turn(r, "assistant", "t1", "Kept too, answer lost.")])
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER,
                          json={"last_item_id": "t2", "unacknowledged": ["t1", "t2"]}).get_json()
    assert ended == {"ok": True, "ended": True, "capture": "incomplete", "missing": 1}   # t1 is held; t2 is not
    assert r.store.markers[(sid, "end")] | {} == {**r.store.markers[(sid, "end")], "capture": "incomplete", "missing": 1,
                                                  "boundary": "t2"}
    whole = start(r)
    post(r, whole, [turn(r, "user", "t0", "All of it.")])
    assert r.client.post(BASE + f"/session/{whole}/end", headers=OWNER,
                         json={"last_item_id": "t0", "unacknowledged": []}).get_json()["capture"] == "complete"
    assert r.store.markers[(whole, "end")]["capture"] == "complete"


def test_a_post_after_the_close_is_refused_and_counted(rig):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t0", "unacknowledged": []})
    assert post(r, sid, [turn(r, "assistant", "t1", "Sure, summing up.")]).status_code == 409
    assert post(r, sid, []).status_code == 409
    assert r.sessions.late_posts(sid) == 2 and r.store.markers[(sid, "end")]["capture"] == "complete"


def test_events_end_may_not_keep_off_the_record_are_counted_not_kept(rig):
    from test_direct_host import turn
    r = rig
    sid = start(r)
    r.host.keep_ok = False
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER,
                          json={"events": [turn(r, "user", "t0", "Private."), turn(r, "assistant", "t1", "Fine.")],
                                "last_item_id": "t1", "unacknowledged": []}).get_json()
    assert ended["capture"] == "incomplete" and ended["missing"] == 2 and not r.store.rows


def test_a_summary_for_a_session_this_process_does_not_hold_keeps_the_pages_count(rig):
    """R1 P3-6 / R2 N5: after a restart the summary's report still reaches the marker, the summary and every view."""
    from flask import Flask
    from core.realtime_voice.direct_host import install_direct_voice
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Kept words.")])
    restarted = Flask("restarted")
    install_direct_voice(restarted, r.host, url_prefix="/tp/voice", environ=dict(ENV), budget=r.budget, summary_http=r.gateway)
    summary = restarted.test_client().post(BASE + f"/session/{sid}/summary", headers=OWNER, json={
        "conversation_id": "owner", "last_item_id": "t1", "unacknowledged": ["t1"]}).get_json()
    assert r.store.markers[(sid, "end")]["capture"] == "incomplete" and r.store.markers[(sid, "end")]["missing"] == 1
    assert "was not kept (1 turn)" in r.host.prompts[-1] and "was not kept (1 turn)" in summary["written"]


def test_a_session_summarized_without_any_end_or_report_is_not_claimed_complete(rig):
    """R2 N6: a session that ended without End (dropped), summarized later from another process."""
    from flask import Flask
    from core.realtime_voice.direct_host import install_direct_voice
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Words.")])
    restarted = Flask("restarted")
    install_direct_voice(restarted, r.host, url_prefix="/tp/voice", environ=dict(ENV), budget=r.budget, summary_http=r.gateway)
    restarted.test_client().post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    assert r.store.markers[(sid, "end")]["capture"] == "incomplete" and "missing" not in r.store.markers[(sid, "end")]
    assert "was not kept, so it is not below" in r.host.prompts[-1]


# ── Item 10: the summary is a plain completion through the model gateway (§5.4, D22 a) ─────────────────────────────────

def summarized(r):
    from test_direct_host import post, turn
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "We looked at the agenda. We're done.")])
    return sid, r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()


def test_the_summary_request_has_no_tools_no_agent_and_no_binding_and_is_bounded(rig):
    r = rig
    _sid, answer = summarized(r)
    assert answer["state"] == "proposed"
    (sent,) = r.gateway.requests
    assert set(sent["json"]) == {"model", "stream", "max_tokens", "temperature", "messages"}       # no tools, no user
    assert "tools" not in sent["json"] and "tool_choice" not in sent["json"] and "user" not in sent["json"]
    assert sent["json"]["model"] == "minimoi-voice-summary" and sent["json"]["stream"] is False
    assert sent["url"] == "http://model-gateway:4000/v1/chat/completions" and sent["allow_redirects"] is False
    assert sent["headers"]["Authorization"] == "Bearer test-only-summary-key"
    system, user = sent["json"]["messages"]
    assert system["role"] == "system" and "Test Person" in system["content"] and "plain and brief" in system["content"]
    assert user["role"] == "user" and "BEGIN_VOICE_RECORDS" in user["content"] and "agenda" in user["content"]
    connect, read = sent["timeout"]
    assert connect + read < 45                                    # the server has stopped waiting before the page's bound


def test_a_tool_call_shaped_answer_is_unavailable():
    from types import SimpleNamespace
    from core.realtime_voice.summary_gateway import GatewaySummary
    for message, finish in (({"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}, "tool_calls"),
                            ({"role": "assistant", "content": "{}", "function_call": {"name": "x"}}, "stop"),
                            ({"role": "assistant", "content": "x"}, "tool_calls")):
        answer = {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": finish, "message": message}]}
        calls = []

        def tool_call(url, **kw):
            calls.append(kw)
            return SimpleNamespace(status_code=200, json=lambda: answer)
        client = GatewaySummary("voice-summary-tp", environ=dict(ENV), http_post=tool_call)
        assert client("system", "prompt") == (None, "tool_call") and len(calls) == 1


def test_a_missing_key_refuses_and_nothing_is_sent(rig):
    from flask import Flask
    from core.realtime_voice.direct_host import install_direct_voice
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Hello.")])
    keyless = Flask("keyless")
    install_direct_voice(keyless, r.host, url_prefix="/tp/voice", environ={"MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO": "1"},
                         budget=r.budget, summary_http=r.gateway)
    answer = keyless.test_client().post(BASE + f"/session/{sid}/summary", headers=OWNER,
                                        json={"conversation_id": "owner"}).get_json()
    assert answer["state"] == "unavailable" and answer["error"] == "no_summary_key" and not r.gateway.requests


def test_a_gateway_that_times_out_is_unavailable_once_and_nothing_is_written_after_it(rig):
    import requests
    r = rig
    calls = []

    def slow(url, **kw):
        calls.append(kw["timeout"])
        raise requests.exceptions.ReadTimeout("synthetic")
    from core.realtime_voice.summary_gateway import GatewaySummary
    assert GatewaySummary("voice-summary-tp", environ=dict(ENV), http_post=slow)("s", "p") == (None, "timeout")
    assert calls == [(5, 30)]                                     # one bounded call, never retried
    down = GatewaySummary("voice-summary-tp", environ=dict(ENV), http_post=lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("synthetic")))
    assert down("s", "p") == (None, "gateway_down")
    r.host.model = None                                            # the gateway refuses: the route's answer, then nothing
    sid, answer = summarized(r)
    assert answer["state"] == "unavailable" and answer["line"] == "Summary unavailable."
    assert r.store.summary("owner", sid) is None and len(r.gateway.requests) == 1


# ── Item 8: Keep talking is its own route; the transcript answers carry whether a proposal stands (R2 N9) ────────────────

def test_keep_talking_is_its_own_route_and_the_old_transcript_withdrawal_is_gone(rig):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    assert post(r, sid, [turn(r, "user", "t0", "We're done.")]).get_json()["proposing"] is True
    old = r.client.post(BASE + f"/session/{sid}/transcript", headers=OWNER, json={"events": [], "proposal": "withdrawn"})
    assert old.status_code == 200 and r.sessions.proposing(sid)                    # A1's control post withdraws nothing now
    answer = r.client.post(BASE + f"/session/{sid}/keep_talking", headers=OWNER, json={})
    assert answer.get_json() == {"ok": True, "proposal": "withdrawn", "proposing": False}
    assert "keep_talking" in r.host.kinds
    assert "end_confirmed" not in post(r, sid, [turn(r, "user", "t1", "Yes.")]).get_json()
    other = {**OWNER, "X-Owner": "guest"}
    assert r.client.post(BASE + f"/session/{sid}/keep_talking", headers=other, json={}).status_code == 403
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={})
    assert r.client.post(BASE + f"/session/{sid}/keep_talking", headers=OWNER, json={}).status_code == 409
    assert r.metadata["keep_talking_url"] == BASE + "/session/{session_id}/keep_talking"


def test_a_later_ordinary_turn_withdraws_the_proposal_and_the_answer_says_so(rig):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    assert post(r, sid, [turn(r, "user", "t0", "We're done.")]).get_json()["proposing"] is True
    assert post(r, sid, [turn(r, "user", "t1", "Actually, one more thing about the kiln.")]).get_json()["proposing"] is False


# ── R1 P3-10: a consult body is capped; long-ended sessions are let go from memory ──────────────────────────────────────

def test_a_consult_body_over_32_kb_is_refused_before_anything_runs(rig):
    r = rig
    sid = start(r)
    big = r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "c1", "name": "consult",
                                                                "owner_transcript_item": "x" * 40_000, "owner_item_id": "t1"})
    assert big.status_code == 413 and big.get_json()["outcome"] == "not_done" and not r.host.turns


def test_sessions_long_past_their_maximum_duration_are_let_go_and_the_store_still_answers(rig):
    r = rig
    old = start(r)
    consult(r, old, call_id="call_1", turn="t1")
    forgotten = []
    r.host.forget = lambda sids: forgotten.append(sids)
    r.clock[0] += 31 * 60 + 3601                                   # past its maximum duration, and an hour more
    new = start(r)                                                 # a new session lets go of the old one
    assert forgotten == [{old}] and not r.sessions.known(old) and r.sessions.owner_of(old) is None
    assert r.sessions.known(new)
    got = r.client.get(BASE + f"/consult/{old}/t1?conversation_id=owner", headers=OWNER).get_json()
    assert got["outcome"] == "done"                                 # the store still answers for it


# ── R1 P3-12, R2 N2: the owner's name and the host's page words come from the adapter ──────────────────────────────────

def test_the_core_names_the_owner_only_as_the_host_does(rig):
    import re
    from pathlib import Path
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    minted = r.mints[-1]
    assert "Rosa's own last words" in minted["tools"][0]["description"] and "Robert" not in str(minted["tools"])
    assert "Rosa's last words" in minted["instructions"] and "Robert" not in minted["instructions"]
    post(r, sid, [turn(r, "user", "t0", "We're done.")])
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    assert r.host.prompts[-1].startswith("Rosa ended a voice conversation")
    r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER, json={"conversation_id": "owner", "decision": "confirm"})
    assert r.store.summaries[(sid, "summary-decision")].startswith("[Voice summary confirmed by Rosa]")
    root = Path(__file__).resolve().parents[2]
    core = re.sub(r'"""[\s\S]*?"""|#.*', "", (root / "core/realtime_voice/direct_host.py").read_text())
    assert "Robert" not in core
    page = re.sub(r"//.*", "", (root / "core/realtime_voice/static/direct-voice-session.js").read_text())
    for guild in ("saved conversation", "on the record", "Robert"):
        assert guild not in page, guild
    assert r.metadata["words"] == {}


# ── Codex's A1 review (REVIEW_codex_A1.md), confirmed at 005c9e81 ───────────────────────────────────────────────────────

def test_codex_a1_c1_a_backend_that_acts_then_raises_is_unknown_first_and_on_replay_with_one_effect(rig):
    """CX A1-C1, Codex's review-evidence-codex-A1/test_codex_a1_repro.py turned to the correct behaviour (fixed by
    15bd99e4): the effect is committed, then the backend raises; the answer and its replay say unknown, never "did not go
    through", and the effect happened once."""
    r = rig
    sid = start(r)
    effects = []

    def apply_then_raise(*args, **kw):
        effects.append("committed")
        raise RuntimeError("response lost after commit")
    r.host.typed_turn = apply_then_raise
    response = consult(r, sid)
    body = response.get_json()
    assert effects == ["committed"] and response.status_code == 200
    assert body["outcome"] == "unknown" and "did not go through" not in body["spoken"]
    replay = consult(r, sid).get_json()
    assert replay == body and len(effects) == 1


# ── CX V2-5 (Codex's Spec 165 v0.2 delta review): a summary attempt has its own identity ────────────────────────────────

def ask_summary(r, sid, client=None):
    return (client or r.client).post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})


def test_two_devices_asking_at_once_get_one_generation_one_proposal_and_one_decision(rig):
    import threading
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "We looked at the agenda. We're done.")])
    r.gateway.hold = threading.Event()
    answers = []
    first = threading.Thread(target=lambda: answers.append(ask_summary(r, sid).get_json()))
    first.start()
    assert r.gateway.entered.wait(5)                                     # the first device's generation is running
    second = threading.Thread(target=lambda: answers.append(ask_summary(r, sid).get_json()))   # the other device asks
    second.start()
    threading.Event().wait(0.1)
    r.gateway.hold.set()
    first.join(5); second.join(5)
    assert len(r.gateway.requests) == 1                                  # one generation
    assert {a["state"] for a in answers} == {"proposed"} and answers[0]["written"] == answers[1]["written"]
    decided = [r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER,
                             json={"conversation_id": "owner", "decision": d}) for d in ("confirm", "decline")]
    assert decided[0].status_code == 200 and decided[1].status_code == 409   # one decision is kept
    assert r.store.summary_state("owner", sid) == "confirmed"


def test_an_attempt_recorded_elsewhere_is_pending_until_it_is_stale_and_unavailable_comes_only_from_a_concluded_one(rig):
    from datetime import datetime, timezone
    from core.realtime_voice.direct_host import record, stamp
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Words.")])
    r.store.put("owner", sid, {**record("summary_attempt", "summary_attempt:1", attempt=1),
                               "at": stamp(datetime.fromtimestamp(r.clock[0], timezone.utc))})   # another process, now
    pending = ask_summary(r, sid).get_json()
    assert pending["state"] == "pending" and not r.gateway.requests and r.store.summary("owner", sid) is None
    assert ("summary-unavailable" not in {what for (_s, what) in r.store.summaries})
    r.clock[0] += 61                                                    # its process died: no outcome after the bound
    r.host.model = None                                                 # this attempt fails at the gateway
    failed = ask_summary(r, sid).get_json()
    assert failed["state"] == "unavailable" and len(r.gateway.requests) == 1
    assert r.store.get("owner", sid, "summary_outcome:1")["state"] == "unknown"
    assert r.store.get("owner", sid, "summary_outcome:2")["state"] == "unavailable"
    r.host.model = '{"spoken": "Done.", "written": "- Words (turn-1)."}'
    again = ask_summary(r, sid).get_json()                              # Ask for a summary, after it concluded
    assert again["state"] == "proposed" and len(r.gateway.requests) == 2
    assert r.store.get("owner", sid, "summary_attempt:3") is not None


# ── The fix round's independent review (REVIEW_independent_A1_fix.md), FR IDs ──────────────────────────────────────────

LONG_TYPOGRAPHIC = "It’s ready; we’re firing now and it’ll cool by six — the glaze’s fine. " * 30


def test_fr_m1_a_long_answer_with_curly_quotes_is_done_with_its_record_and_receipt(rig):
    """FR M1 (the reviewer's probe, asserting the correct behaviour): the record is cut by its encoded size."""
    from core.realtime_voice.direct_host import RECORD_MAX, encoded
    r = rig
    sid = start(r)
    r.host.typed_turn = lambda *a, **kw: {"answered": True, "receipt": "turn-1", "receipt_kind": "reply",
                                          "spoken": LONG_TYPOGRAPHIC}
    body = consult(r, sid, call_id="call_long", turn="t0").get_json()
    assert body["outcome"] == "done" and body["spoken"] == LONG_TYPOGRAPHIC and "record_kept" not in body
    kept = r.store.get("owner", sid, "consult_outcome:t0")
    assert kept["outcome"] == "done" and encoded(kept) <= RECORD_MAX
    assert kept["result"]["spoken"].endswith(" The rest is in the conversation.")
    assert [row for row in r.store.rows if row["role"] == "receipt"]
    german = "Die Glasur ist fertig; über Nacht kühlt der Ofen ab, und morgen früh prüfen wir die Stücke. " * 30
    r.host.typed_turn = lambda *a, **kw: {"answered": True, "receipt": "turn-2", "receipt_kind": "reply", "spoken": german}
    assert consult(r, sid, call_id="call_de", turn="t1").get_json()["outcome"] == "done"
    assert encoded(r.store.get("owner", sid, "consult_outcome:t1")) <= RECORD_MAX


def test_fr_m1_a_store_failure_after_the_turn_says_the_result_and_that_its_record_was_not_kept(rig):
    from core.realtime_voice.direct_host import RECORD_LOST
    r = rig
    sid = start(r)
    r.store.refuse_kinds = {"consult_outcome"}
    body = consult(r, sid, call_id="call_1", turn="t0").get_json()
    assert body["outcome"] == "done" and body["receipt"] == "turn-1" and body["record_kept"] is False
    assert body["spoken"] == "Two items. " + RECORD_LOST


def test_fr_s1_the_same_turn_with_other_words_after_it_acted_is_never_said_as_nothing_done(rig):
    """FR S1 (the reviewer's probe, asserting the correct behaviour): Check acted, then the model's delayed call names the
    same owner item with re-completed words; it is still refused, but in the words of the turn's real outcome."""
    r = rig
    sid = start(r)
    first = consult(r, sid, call_id="host-1", words="save a note: the glaze is ready", turn="t0")
    assert first.get_json()["outcome"] == "done" and len(r.host.turns) == 1
    second = consult(r, sid, call_id="call_m", words="save a note: the glaze is ready.", name="save_note", turn="t0")
    body = second.get_json()
    assert second.status_code == 409 and body["error"] == "turn_mismatch" and len(r.host.turns) == 1
    assert body["outcome"] == "done" and "nothing was done" not in body["spoken"]
    assert body["spoken"] == "That turn was already acted on; check it in writing."


def test_fr_n10_a_replay_to_the_other_tool_follows_the_first_outcome(rig):
    r = rig
    sid = start(r)
    r.host.typed_turn = lambda *a, **kw: {"ok": False, "error": "busy", "spoken": "Master Craftsman is busy."}
    consult(r, sid, call_id="host-1", turn="t0")
    other = consult(r, sid, call_id="call_m", name="save_note", turn="t0").get_json()
    assert other["outcome"] == "not_done" and "already handled" not in other["spoken"]
    assert other["spoken"] == "That was already tried and not done: Master Craftsman is busy."


def test_fr_n6_refusals_that_run_nothing_count_toward_the_sessions_cap(rig):
    """A model looping save_note on words that are not a note: each refusal keeps a receipt record, so each is counted
    toward the session's cap, and past it nothing more is kept."""
    from core.realtime_voice.consult import CAP, CAP_LINE
    r = rig
    sid = start(r)
    for i in range(CAP):
        r.host.refusals["save_note"] = {"error": "not_a_note", "spoken": "That is not a note."}
        answer = consult(r, sid, call_id=f"call_{i}", name="save_note", turn=f"t{i}")
        assert answer.status_code == 200 and answer.get_json()["outcome"] == "not_done"
    receipts = len(r.store.rows)
    r.host.refusals["save_note"] = {"error": "not_a_note", "spoken": "That is not a note."}
    capped = consult(r, sid, call_id="call_x", name="save_note", turn="t99")
    assert capped.status_code == 429 and capped.get_json()["spoken"] == CAP_LINE
    assert len(r.store.rows) == receipts and not r.host.turns                       # nothing more kept, nothing ran
    assert consult(r, sid, call_id="call_y", turn="t100").status_code == 429
