"""The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §4.4, §5) in the host core, through the fake adapter of
test_direct_host.py (an in-memory store, not the Guild). Each test fails on the tree before its item's commit. No provider,
no Keychain, no microphone, no network."""
from test_direct_host import BASE, ENV, OWNER, consult, rig, start  # noqa: F401


def bare(r, sid, **fields):
    # v3: the dispatcher is driven through Check's call id (the action path acts only on Check or a confirmed action).
    return r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "host-call_1", "name": "consult",
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
    assert outcome["kind"] == "consult_outcome" and outcome["outcome"] == "done" and outcome["call_id"] == "host-call_1"
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
    assert asked["receipt"] == "turn-1" and not asked.get("same_turn") and len(r.host.turns) == 1   # its own cap (V note cap)


def test_an_unavailable_conversation_binds_nothing(rig):
    r = rig
    sid = start(r)
    r.host.down = 1
    assert consult(r, sid, call_id="c1", turn="t2").get_json()["error"] == "conversation"
    assert not r.host.turns and "consult_request:t2" not in records_of(r, sid)
    later = consult(r, sid, call_id="c3", turn="t2").get_json()
    assert later["receipt"] == "turn-1" and len(r.host.turns) == 1


def test_fr_n9_the_durable_request_comes_first_and_an_unkept_visible_one_settles_the_turn_not_done(rig):
    """FR N9 (changed from A1's "an unkept request binds nothing"): the consult_request record is put before the visible
    consult record. When the visible one cannot be kept after the put, the turn's action does not run and the turn is
    settled not_done durably, so a later dispatch, another process or the reconciliation read all say not done; the
    reveal never shows a consult with no outcome."""
    r = rig
    sid = start(r)
    r.store.fail_keeps = 1
    first = consult(r, sid, call_id="c2", turn="t2").get_json()
    assert first["error"] == "not_kept" and first["outcome"] == "not_done" and not r.host.turns
    kept = records_of(r, sid)
    assert "consult_request:t2" in kept and kept["consult_outcome:t2"]["outcome"] == "not_done"
    later = consult(r, sid, call_id="c3", turn="t2").get_json()
    assert later["outcome"] == "not_done" and later["same_turn"] is True and not r.host.turns
    assert r.client.get(BASE + f"/consult/{sid}/t2?conversation_id=owner", headers=OWNER).get_json()["outcome"] == "not_done"


def test_fr_n9_a_refused_request_record_keeps_no_visible_consult(rig):
    r = rig
    sid = start(r)
    r.store.refuse_kinds = {"consult_request"}
    assert consult(r, sid, call_id="c1", turn="t4").get_json()["error"] == "not_recorded"
    assert not r.host.turns and not [row for row in r.store.rows if row["role"] == "consult"]


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
    assert post(r, sid, []).get_json()["completed"] == beat["completed"]           # not acknowledged yet: again (FR N7)
    acked = r.client.post(BASE + f"/session/{sid}/transcript", headers=OWNER, json={"events": [], "told": ["t1"]})
    assert "completed" not in acked.get_json() and "completed" not in post(r, sid, []).get_json()   # acknowledged: gone
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
    """Adapted for v3: the model has no tools (tools() is gone), so no tool text says anything; the page says pending and
    an already-handled turn itself, in content-free words (direct-voice-session.js outcomeLine)."""
    from core.realtime_voice import direct_host
    assert not hasattr(direct_host, "tools")
    rules = direct_host.direct_rules({"name": "Test Person", "short": "TP", "notes": ("save a note:",)})
    assert "you have no tools" in rules and "Outcomes come from the app" in rules


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
    from test_direct_host import asked, post, utter
    r = rig
    sid = start(r)
    end = utter(r, sid, "t0", "We're done.").get_json()                            # v3: the end is heard here
    assert end["proposing"] is True
    asked(r, sid, end["action_id"], "t0")
    old = r.client.post(BASE + f"/session/{sid}/transcript", headers=OWNER, json={"events": [], "proposal": "withdrawn"})
    assert old.status_code == 200 and r.sessions.proposing(sid)                    # A1's control post withdraws nothing now
    answer = r.client.post(BASE + f"/session/{sid}/keep_talking", headers=OWNER, json={})
    assert answer.get_json() == {"ok": True, "proposal": "withdrawn", "proposing": False}
    assert "keep_talking" in r.host.kinds
    assert utter(r, sid, "t1", "Yes.").get_json()["decision"] == "chat"
    assert post(r, sid, []).status_code == 200
    other = {**OWNER, "X-Owner": "guest"}
    assert r.client.post(BASE + f"/session/{sid}/keep_talking", headers=other, json={}).status_code == 403
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={})
    assert r.client.post(BASE + f"/session/{sid}/keep_talking", headers=OWNER, json={}).status_code == 409
    assert r.metadata["keep_talking_url"] == BASE + "/session/{session_id}/keep_talking"


def test_a_later_ordinary_turn_withdraws_the_proposal_and_the_answer_says_so(rig):
    from test_direct_host import post, turn, utter
    r = rig
    sid = start(r)
    assert utter(r, sid, "t0", "We're done.").get_json()["proposing"] is True     # v3: heard on the utterance route
    later = utter(r, sid, "t1", "Actually, one more thing about the kiln.").get_json()
    assert later["proposing"] is False and later["decision"] == "chat" and later["dropped_kind"] == "end"
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
    assert minted["tools"] is None                                     # v3: no tools; the rules name the owner (adapted)
    assert "only after Rosa confirms" in minted["instructions"] and "Robert" not in minted["instructions"]
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


def test_fr_n6_refusals_that_run_nothing_have_a_cap_of_their_own_and_never_block_a_real_consult(rig):
    """A model looping save_note on words that are not a note: each refusal keeps a receipt record, so each is counted,
    and past REFUSAL_CAP nothing more is kept (FR N6). The count is its own (V note cap): twelve mistaken refusals leave
    every real consult the session may still make."""
    from core.realtime_voice.consult import CAP_LINE
    from core.realtime_voice.direct_host import REFUSAL_CAP
    r = rig
    sid = start(r)
    for i in range(REFUSAL_CAP):
        r.host.refusals["save_note"] = {"error": "not_a_note", "spoken": "That is not a note."}
        answer = consult(r, sid, call_id=f"call_{i}", name="save_note", turn=f"t{i}")
        assert answer.status_code == 200 and answer.get_json()["outcome"] == "not_done"
    receipts = len(r.store.rows)
    r.host.refusals["save_note"] = {"error": "not_a_note", "spoken": "That is not a note."}
    capped = consult(r, sid, call_id="call_x", name="save_note", turn="t99")
    assert capped.status_code == 429 and capped.get_json()["spoken"] == CAP_LINE
    assert len(r.store.rows) == receipts and not r.host.turns                       # nothing more kept, nothing ran
    real = consult(r, sid, call_id="call_y", turn="t100")
    assert real.status_code == 200 and real.get_json()["outcome"] == "done" and real.get_json()["receipt"] == "turn-1"


def test_fr_n7_a_finished_result_whose_answer_is_lost_is_told_on_the_next_post_and_never_after_its_ack(rig):
    """FR N7 (the review: "once" was really "at most once"): a finished result stays on the server until a post names its
    owner turn in told; a malformed told acknowledges nothing."""
    from test_direct_host import post
    r = rig
    sid = start(r)
    r.sessions.announce(sid, {"owner_item_id": "t3", "tool": "consult", "outcome": "done", "spoken": "Done."})
    r.sessions.announce(sid, {"owner_item_id": "t4", "tool": "consult", "outcome": "not_done", "spoken": "Busy."})
    lost = post(r, sid, [])                                                          # this answer never reaches the page
    assert [d["owner_item_id"] for d in lost.get_json()["completed"]] == ["t3", "t4"]
    beat = lambda told: r.client.post(BASE + f"/session/{sid}/transcript", headers=OWNER,
                                      json={"events": [], "told": told}).get_json().get("completed", [])
    assert [d["owner_item_id"] for d in beat("t3")] == ["t3", "t4"]                 # not a list: acknowledges nothing
    assert [d["owner_item_id"] for d in beat(["x9", 4])] == ["t3", "t4"]            # not capture ids
    assert [d["owner_item_id"] for d in beat(["t3"])] == ["t4"]
    assert beat(["t3", "t4"]) == [] and beat([]) == []


def test_fr_n2_a_gateway_that_trickles_its_answer_is_cut_at_the_total_deadline(monkeypatch):
    """FR N2 (the reviewer's probe, loopback only, the constants scaled down): requests' timeout is per read, so a gateway
    that sends its headers and then a few bytes at a time held the call past connect + read. The total deadline cuts it."""
    import json as _json
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from core.realtime_voice import summary_gateway as sg
    body = _json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "x" * 40}}]}).encode()

    class Trickle(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for i in range(0, len(body), 8):           # 8 bytes every 0.3 s: no read waits a whole second
                    self.wfile.write(body[i:i + 8])
                    self.wfile.flush()
                    _time.sleep(0.3)
            except OSError:
                pass                                       # the client closed it at the deadline

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 0), Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(sg, "CONNECT_TIMEOUT_S", 1)
        monkeypatch.setattr(sg, "READ_TIMEOUT_S", 1)
        monkeypatch.setattr(sg, "TOTAL_TIMEOUT_S", 1.5, raising=False)
        client = sg.GatewaySummary("voice-summary-tp", environ={"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_TP": "test-only",
                                                                sg.GATEWAY_URL_ENV: f"http://127.0.0.1:{server.server_port}/v1"})
        began = _time.monotonic()
        answer = client("s", "p")
        took = _time.monotonic() - began
    finally:                                               # the abandoned read finishes on its own, off this test's clock
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()
    assert answer == (None, "timeout") and took < 2.5              # the trickle alone runs about 3.3 s



def trickling_gateway(body, every_s):
    """A loopback gateway that sends its headers, then its body 8 bytes at a time; it records whether the client cut it."""
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = {"cut": False, "done": threading.Event()}

    class Trickle(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for i in range(0, len(body), 8):
                    self.wfile.write(body[i:i + 8])
                    self.wfile.flush()
                    _time.sleep(every_s)
            except OSError:
                seen["cut"] = True
            finally:
                seen["done"].set()

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 0), Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_v_gateway_note_the_cut_call_stops_reading_and_closes_its_connection_at_the_deadline(monkeypatch):
    """V note gateway: at f7da4657 the call cut at the deadline kept reading in its thread until the gateway finished. Now
    the thread stops at its next chunk and closes the connection: the gateway sees it cut, and the thread ends."""
    import json as _json
    import threading
    import time as _time
    from core.realtime_voice import summary_gateway as sg
    body = _json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "x" * 160}}]}).encode()
    server, seen = trickling_gateway(body, 0.2)                       # about 5.5 s of trickle
    try:
        monkeypatch.setattr(sg, "CONNECT_TIMEOUT_S", 1)
        monkeypatch.setattr(sg, "READ_TIMEOUT_S", 1)
        monkeypatch.setattr(sg, "TOTAL_TIMEOUT_S", 1.0)
        client = sg.GatewaySummary("voice-summary-tp", environ={"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_TP": "test-only",
                                                                sg.GATEWAY_URL_ENV: f"http://127.0.0.1:{server.server_port}/v1"})
        assert client("s", "p") == (None, "timeout")
        cut_by = _time.monotonic() + 2.5
        assert seen["done"].wait(max(0.0, cut_by - _time.monotonic())), "the gateway was still being read"
        assert seen["cut"] is True
        while any(t.name == "voice-summary-gateway" for t in threading.enumerate()) and _time.monotonic() < cut_by:
            _time.sleep(0.02)
        assert not any(t.name == "voice-summary-gateway" for t in threading.enumerate())
    finally:
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()


def test_v_gateway_note_an_answer_read_as_it_arrives_is_the_summary(monkeypatch):
    """The streamed read returns the gateway's plain answer whole, when it arrives inside the deadline."""
    import json as _json
    import threading
    from core.realtime_voice import summary_gateway as sg
    body = _json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "Wir haben über die Glasur gesprochen."}}]},
                       ensure_ascii=False).encode()
    server, seen = trickling_gateway(body, 0.0)
    try:
        client = sg.GatewaySummary("voice-summary-tp", environ={"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_TP": "test-only",
                                                                sg.GATEWAY_URL_ENV: f"http://127.0.0.1:{server.server_port}/v1"})
        assert client("s", "p") == ("Wir haben über die Glasur gesprochen.", None)
        assert seen["done"].wait(2) and seen["cut"] is False
    finally:
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()

# ── FR N1: "still being written" only while an attempt may really run ───────────────────────────────────────────────────

def summary_ask(r, sid):
    return r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})


def test_fr_n1_a_summary_attempt_the_store_refuses_is_unavailable_not_still_being_written(rig):
    """The reviewer's probe, asserting the correct behaviour: nothing ran, so nothing is being written."""
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Words.")])
    r.store.put_ok = False
    answer = summary_ask(r, sid)
    assert answer.status_code == 200 and answer.get_json()["state"] == "unavailable" and not r.gateway.requests
    assert answer.get_json()["error"] == "not_recorded" and r.store.summary_state("owner", sid) == "unavailable"
    r.store.put_ok = True
    assert summary_ask(r, sid).get_json()["state"] == "proposed" and len(r.gateway.requests) == 1   # a retry runs


def test_fr_n1_an_attempt_that_raises_concludes_unavailable_and_a_retry_runs_at_once(rig):
    """The reviewer's probe, asserting the correct behaviour: no 500, no minute of "pending"."""
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Words.")])
    real = r.store.records
    r.store.records = lambda *a: (_ for _ in ()).throw(RuntimeError("store down"))
    first = summary_ask(r, sid)
    r.store.records = real
    assert first.status_code == 200 and first.get_json()["state"] == "unavailable" and first.get_json()["error"] == "exception"
    assert records_of(r, sid)["summary_outcome:1"]["state"] == "unavailable"
    second = summary_ask(r, sid).get_json()
    assert second["state"] == "proposed" and len(r.gateway.requests) == 1


def test_fr_n1_an_attempt_whose_outcome_cannot_be_kept_is_never_still_being_written_here(rig):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Words.")])
    r.store.refuse_kinds = {"summary_outcome"}
    real = r.store.records
    r.store.records = lambda *a: (_ for _ in ()).throw(RuntimeError("store down"))
    assert summary_ask(r, sid).get_json()["state"] == "unavailable"
    r.store.records = real
    assert "summary_outcome:1" not in records_of(r, sid)
    assert summary_ask(r, sid).get_json()["state"] == "proposed"               # attempt 2, not "still being written"


# ── The last pass: Spec 165 v0.3 (G1–G4) and Codex's delta review at a17ddc8d (CC_22) ────────────────────────────────────

# G1 (Spec 165 v0.3 §6.4, D16 (a)): a consult whose owner words are only a confirmation or a stop word is refused in the core,
# before anything is recorded or run, on every host: v3 says the stray-yes line (addition 3). A small closed set, tested
# phrase by phrase.
BARE = ["yes", "Yes.", "YES!", "yeah", "Yep.", "Sure.", "okay", "OK, go ahead.", "Yes, go ahead.", "Go ahead", "do it",
        "Yes please.", "Yes, do it.", "Alright, go for it!", "That’s right.", "Sounds good.", "Of course.", "Right, then do it.",
        "no", "No.", "Stop.", "stop", "Cancel.", "Never mind.", "Wait.", "Hold on.", "Don't.", "Don’t!", "Nope.",
        "Forget it.", "Ja.", "Genau.", "Nein.", "Sim.", "Não.", "Yes, thanks.", "Okay, thank you.", "Yes, yes.",
        # L S1 (the last pass's verification): every phrase the reviewer listed, then the decision's words.
        "Um, yes.", "Uh, yeah.", "Oh yes.", "Well, okay.", "Yes, go.", "Go.", "Yes, proceed.", "Yes, do it now.", "Yes sir.",
        "Mm-hmm.", "Uh-huh.", "Ja bitte.", "Ja, mach das.", "Sim, pode.",
        "Hmm, okay.", "So, yes.", "Now, go ahead.", "Er, yes.", "Ah, okay.", "Mhm.", "Proceed.", "Bitte.", "Ja, mach es.",
        "Ja, gerne.", "In Ordnung.", "Einverstanden.", "Pode.", "Pode sim.", "Sim, pode fazer.", "Sim, claro.", "Certo.",
        "Isso mesmo.", "Com certeza.", "Tudo bem."]
NOT_BARE = ["Yes, save a note that the kiln is hot.", "Go ahead and check #12.", "Stop the nightly job.",
            "Ja, speicher eine Notiz, dass der Ofen heiß ist.", "Go ahead and check the build status.",
            "Sim, pode verificar o build?", "Um, what is on my agenda?", "Well, stop the nightly job.", "So now what?",
            "Hmm.", "Um.", "Well.", "Now.", "Sir.", "Go check the kiln.", "Proceed with the backup.", "Bitte speichere das.",
            "Cancel the reminder for Friday.", "No, ask about item 12 instead.", "What is stuck on #12?", "Thanks.", "",
            "Okay so what is on my agenda", "Right now, what is running?", None, 12]


def test_g1_bare_confirmations_and_stop_words_are_told_apart_phrase_by_phrase():
    from core.realtime_voice.direct_host import bare_confirmation
    assert [p for p in BARE if not bare_confirmation(p)] == []
    assert [p for p in NOT_BARE if bare_confirmation(p)] == []


def test_g1_a_bare_yes_is_refused_in_writing_runs_nothing_and_counts_as_a_refusal(rig):
    from core.realtime_voice.consult import CAP_LINE
    from core.realtime_voice.direct_host import REFUSAL_CAP
    r = rig
    sid = start(r)
    for i, words in enumerate(["Yes.", "Go ahead.", "Stop."]):
        answer = consult(r, sid, call_id=f"call_{i}", words=words, turn=f"t{i}")
        body = answer.get_json()
        assert answer.status_code == 200 and body["outcome"] == "not_done" and body["error"] == "confirmation"
        assert body["spoken"] == "Nothing's waiting; say it again if you want it."     # v3 addition 3, still refused
        assert f"consult_request:t{i}" not in records_of(r, sid)                    # never admitted: no record of an effect
    assert not r.host.turns
    for i in range(3, REFUSAL_CAP):                                                  # each counts under the refusals' cap
        consult(r, sid, call_id=f"call_{i}", words="Yes.", turn=f"t{i}")
    capped = consult(r, sid, call_id="call_cap", words="Yes.", turn="t50")
    assert capped.status_code == 429 and capped.get_json()["spoken"] == CAP_LINE
    real = consult(r, sid, call_id="call_real", words="Yes, save a note that the kiln is hot.", turn="t51", name="save_note")
    assert real.get_json()["outcome"] == "done" and len(r.host.turns) == 1          # not bare: it runs


def test_g1_the_yes_to_shall_i_sum_up_still_ends_the_session_while_a_bare_consult_yes_is_refused(rig):
    from test_direct_host import asked, utter
    r = rig
    sid = start(r)
    end = utter(r, sid, "t0", "We're done.").get_json()                            # v3: the end is the waiting action
    assert end["decision"] == "ask" and end["kind"] == "end"
    asked(r, sid, end["action_id"], "t0")
    tapped = consult(r, sid, call_id="host-yes", words="Yes.", turn="t1").get_json()   # a tap on a bare yes: refused
    assert tapped["spoken"] == "Nothing's waiting; say it again if you want it." and tapped["error"] == "confirmation"
    got = utter(r, sid, "t1", "Yes.").get_json()                                  # the spoken yes still ends it
    assert got["decision"] == "confirm" and got["kind"] == "end"
    assert not r.host.turns


def test_g1_a_hosts_in_writing_commands_are_refused_before_anything_runs_and_mc_supplies_none(rig):
    """Spec 165 v0.3 §6.4 asks A2 for CoS's commands refused with "Do that in writing." The core holds the hook and the words;
    a host supplies only its set (in_writing). MC's typed turn handles its own commands in voice (named ones only; a bare
    confirm, cancel or stop is clarified in writing by MC itself), so MC supplies none."""
    from core.realtime_voice.direct_host import DirectVoiceAdapter
    from minimoi_portal.guild_ui.mc_voice_direct import _MasterCraftsman
    r = rig
    sid = start(r)
    r.host.in_writing = lambda name, text: text.startswith("/")
    body = consult(r, sid, call_id="call_new", words="/new", turn="t0").get_json()
    assert body["outcome"] == "not_done" and body["error"] == "in_writing" and body["spoken"] == "Do that in writing."
    assert not r.host.turns and "consult_request:t0" not in records_of(r, sid)
    assert consult(r, sid, call_id="call_ok", words="What is on my agenda?", turn="t1").get_json()["outcome"] == "done"
    assert DirectVoiceAdapter().in_writing("consult", "/new") is False
    assert _MasterCraftsman.in_writing is DirectVoiceAdapter.in_writing


# G3 (Spec 165 v0.3 §5.3): End carries how the spoken summary's response ended; the core keeps it once, as a durable record,
# and only drained is heard. An outcome it does not know is not kept; a later End changes nothing (first write wins).
import pytest  # noqa: E402


@pytest.mark.parametrize("outcome", ["drained", "barged_in", "cancelled", "cleared", "no_audio", "timeout"])
def test_g3_end_keeps_how_the_spoken_summary_ended_and_only_drained_is_heard(rig, outcome):
    from test_direct_host import post, turn
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "We looked at #12. We're done.")])
    assert summary_ask(r, sid).get_json()["state"] == "proposed"
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"summary_playback": outcome})
    assert ended.status_code == 200 and ended.get_json()["ok"] is True
    kept = records_of(r, sid)["summary_playback"]
    assert kept["kind"] == "summary_playback" and kept["outcome"] == outcome and kept["heard"] is (outcome == "drained")
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"summary_playback": "drained"})
    assert records_of(r, sid)["summary_playback"]["outcome"] == outcome                # the first End's word stands


@pytest.mark.parametrize("said", [None, "heard", 5, ["drained"]])
def test_g3_an_end_with_no_or_an_unknown_playback_outcome_keeps_none(rig, said):
    r = rig
    sid = start(r)
    body = {} if said is None else {"summary_playback": said}
    assert r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json=body).status_code == 200
    assert "summary_playback" not in records_of(r, sid)



def test_l_s1_words_that_are_not_bare_still_run(rig):
    """L S1: a confirmation with a request in it is not bare, in English, German and Portuguese, and runs."""
    r = rig
    sid = start(r)
    for i, words in enumerate(["Yes, save a note that the kiln is hot.", "Ja, speicher eine Notiz, dass der Ofen heiß ist.",
                               "Go ahead and check the build status."]):
        body = consult(r, sid, call_id=f"call_{i}", words=words, turn=f"t{i}").get_json()
        assert body["outcome"] == "done", (words, body)
    assert len(r.host.turns) == 3
    assert consult(r, sid, call_id="call_um", words="Um, yes.", turn="t9").get_json()["spoken"] == "Nothing's waiting; say it again if you want it."
    assert len(r.host.turns) == 3


def test_l_n4_a_bare_yes_or_an_in_writing_command_after_end_is_session_ended_not_a_refusal(rig):
    """L N4: the session is checked first. After End, a bare yes is 409 "session ended", as any consult is, and keeps nothing."""
    r = rig
    sid = start(r)
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={})
    rows = len(r.store.rows)
    late = consult(r, sid, call_id="call_yes", words="Yes.", turn="t1")
    assert late.status_code == 409 and late.get_json()["error"] == "session ended"
    r.host.in_writing = lambda name, text: text.startswith("/")
    late = consult(r, sid, call_id="call_new", words="/new", turn="t2")
    assert late.status_code == 409 and late.get_json()["error"] == "session ended"
    assert len(r.store.rows) == rows and not r.host.turns


def test_l_n5_a_host_in_writing_hook_that_raises_is_a_refusal_before_anything_runs(rig, caplog):
    """L N5: a raising hook never becomes a 500 (which the page would say as "I didn't get the result back"): it is refused
    before dispatch, not_done, "Do that in writing.", logged, and nothing is admitted."""
    r = rig
    sid = start(r)

    def broken(name, text):
        raise RuntimeError("hook down")
    r.host.in_writing = broken
    answer = consult(r, sid, call_id="call_x", words="What is on my agenda?", turn="t0")
    body = answer.get_json()
    assert answer.status_code == 200 and body["outcome"] == "not_done" and body["error"] == "in_writing"
    assert body["spoken"] == "Do that in writing."
    assert "consult_request:t0" not in records_of(r, sid) and not r.host.turns
    assert "in_writing" in caplog.text
