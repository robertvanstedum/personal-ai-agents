"""The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §4.4, §5) on MC's real direct-voice host: the synthetic Guild mount of
test_mc_voice_direct.py. Each test fails on the tree before its item's commit. No provider, no Keychain, no microphone,
no network."""
from core.realtime_voice.direct_host import Put, record
from minimoi_portal.guild_ui import mc_voice_direct as direct, mc_voice_reveal as reveal
from test_mc_voice_direct import BASE, CID, call, post, ready, shown, start, turn  # noqa: F401


# ── Item 2: the floor store keeps durable records, first write wins (§4.4) ─────────────────────────────────────────────

def test_mcs_floor_store_puts_a_record_once_and_a_different_one_under_its_id_is_a_conflict(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    store = direct._FloorStore()                                                  # the conversation's floor (the fixture's)
    conv = {"id": CID}
    first = record("consult_request", "consult_request:t4", owner_item_id="t4", text_sha256="a" * 64)
    assert store.put(conv, sid, first).outcome == "stored"
    again = {**first, "at": "2026-10-28T12:00:00.000Z"}                         # the same record, written later
    assert store.put(conv, sid, again).outcome == "duplicate"
    other = {**first, "text_sha256": "b" * 64}
    assert store.put(conv, sid, other).outcome == "conflict"
    assert store.get(conv, sid, "consult_request:t4") == first                   # nothing was changed
    assert store.get(conv, sid, "consult_request:t5") is None
    assert isinstance(store.put(conv, sid, first), Put)


def test_codex_b1_check_then_the_models_note_saves_one_note_with_one_durable_request(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Make a note that the glaze is ready"
    call(r, sid, call_id="host-1", name="consult", words=words, owner_item_id="t1")             # Check with MC
    second = call(r, sid, call_id="call_model", name="save_note", words=words, owner_item_id="t1").get_json()
    assert len(r.cos.calls) == 1 and second["same_turn"] is True
    assert reveal.get_record(r.notes, sid, "consult_request:t1")["owner_item_id"] == "t1"
    assert reveal.get_record(r.notes, sid, "consult_outcome:t1")["call_id"] == "host-1"
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
    rows, _sessions = shown(r)
    assert not [row for row in rows if (row.get("text") or "").startswith(reveal.RECORD)]   # never shown


# ── Item 3: MC's typed-turn request id derives from the key (§5.1) ──────────────────────────────────────────────────

def test_mcs_typed_turn_request_id_is_the_consult_key_and_its_reply_is_the_sessions(ready):
    import uuid
    from minimoi_portal.guild_ui.stores import Author
    r = ready
    sid = start(r)["voice_session_id"]
    result = call(r, sid, call_id="call-a", owner_item_id="t5").get_json()
    note_id = "vc-" + uuid.UUID(sid).hex + "-t5"
    assert result["note_request_id"] == note_id and r.turns[0]["note_id"] == note_id
    assert reveal.consult_note_id(sid, "t5") == note_id
    # However a second dispatch of the same owner turn reached MC, it is the same note: kept once, so MC's one reply per
    # note answers it from the store (api.run_mc_turn).
    assert r.notes.add_note(note_id, "What is stuck on #12?", Author("robert", "owner", "Robert"), page="voice").repeated
    assert reveal.tag_for_turn(r.notes, note_id, "mc-notice-1") == sid          # a later note of this turn: tagged
    r.notes.add_note(reveal.reply_id(note_id), "Item 12 is in build.", Author("master_craftsman", "agent", "MC"))
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
    rows, _sessions = shown(r)
    kinds = {row["request_id"]: row["voice"]["kind"] for row in rows if "voice" in row}
    assert kinds[note_id] == "consult_note" and kinds[reveal.reply_id(note_id)] == "consult_reply"


# ── R1 P3-5, R2 S3: a refusal that ran nothing leaves the turn free (test_a1_repro.py) ─────────────────────────────────

def test_save_note_on_words_that_are_not_a_note_leaves_the_turn_for_the_models_consult(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Remind me at five to call the kiln supplier"
    first = call(r, sid, call_id="m1", name="save_note", words=words, owner_item_id="t1").get_json()
    second = call(r, sid, call_id="m2", name="consult", words=words, owner_item_id="t1").get_json()
    assert first["error"] == "not_a_note" and first["saved"] is False
    assert not second.get("same_turn") and second["answered"] is True and len(r.turns) == 1


# ── Item 4 / R1 P1-1: MC's turn dispatched with no result is unknown (test_a1_repro.py) ────────────────────────────────

def _after_dispatch(r, monkeypatch, outcome):
    from minimoi_portal.guild_ui import api

    def turn(actual, **kw):
        r.turns.append(kw)
        if outcome == "raise":
            raise RuntimeError("after dispatch")
        return {"status": outcome, "failure_class": "local_timeout", "reply_note": None}, 200
    monkeypatch.setattr(api, "run_mc_turn", turn)


def test_mcs_timeout_uncertain_or_cancelled_turn_is_unknown_never_did_not_go_through(ready, monkeypatch):
    r = ready
    for n, status in enumerate(("timeout_uncertain", "cancelled"), 1):
        _after_dispatch(r, monkeypatch, status)
        sid = start(r)["voice_session_id"]
        out = call(r, sid, call_id=f"c{n}", owner_item_id="t1").get_json()
        assert len(r.turns) == n                                                   # dispatched to MC
        assert out["outcome"] == "unknown" and out["ok"] is False and "did not go through" not in out["spoken"]
        receipts = [row["text"] for row in r.notes.rows.values() if "Outcome unknown" in (row.get("text") or "")]
        assert len(receipts) == n and "Not answered" not in receipts[-1]
        assert call(r, sid, call_id=f"c{n}", owner_item_id="t1").get_json()["outcome"] == "unknown"   # the page's retry
        r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
        shown_rows, _ = shown(r)
        lines = [row["voice"] for row in shown_rows if row.get("voice", {}).get("kind") == "consult_receipt"
                 and row["voice"]["session_id"] == sid]
        assert lines[0]["outcome"] == "result unknown"                             # revealed as unknown, not "not done"


def test_an_exception_after_mcs_turn_was_dispatched_is_unknown(ready, monkeypatch):
    r = ready
    _after_dispatch(r, monkeypatch, "raise")
    sid = start(r)["voice_session_id"]
    out = call(r, sid, call_id="c1", owner_item_id="t1").get_json()
    assert len(r.turns) == 1 and out["outcome"] == "unknown" and "did not go through" not in out["spoken"]



# ── Item 4: MC's typed turn in the worker, under the request's own context ─────────────────────────────────────────────

def test_a_long_mc_turn_is_pending_and_its_reply_lands_in_the_conversation_and_the_next_heartbeat(ready, monkeypatch):
    import threading
    from core.realtime_voice import direct_host as core
    from minimoi_portal.guild_ui import api
    r = ready
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.05)
    release = threading.Event()

    def slow(actual, **kw):
        r.turns.append(kw)
        assert release.wait(5)
        return {"status": "answered", "reply_note": {"text": "Item **12** is in build.", "request_id": "reply-1"}}, 200
    monkeypatch.setattr(api, "run_mc_turn", slow)
    sid = start(r)["voice_session_id"]
    first = call(r, sid, call_id="c1", owner_item_id="t1").get_json()
    assert first["outcome"] == "pending" and len(r.turns) == 1
    release.set()
    for _ in range(200):
        if reveal.get_record(r.notes, sid, "consult_outcome:t1"):
            break
        threading.Event().wait(0.01)
    assert reveal.get_record(r.notes, sid, "consult_outcome:t1")["outcome"] == "done"
    beat = post(r, sid, []).get_json()
    assert beat["completed"][0]["owner_item_id"] == "t1" and beat["completed"][0]["spoken"] == "Item 12 is in build."
    assert any("Answered. Reply reply-1." in (row.get("text") or "") for row in r.notes.rows.values())


# ── R1 P2-3: Codex's B1 path, Check and then the model's save_note (test_a1_repro.py) ───────────────────────────────────

def test_check_then_the_models_save_note_says_saved_because_the_check_saved_it(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    words = "Make a note that the kiln needs a new seal"
    first = call(r, sid, call_id="host-1", name="consult", words=words, owner_item_id="t1").get_json()
    second = call(r, sid, call_id="call_model", name="save_note", words=words, owner_item_id="t1").get_json()
    assert len(r.cos.calls) == 1 and first["saved"] is True
    assert second["same_turn"] is True and second["saved"] is True and second["first_tool"] == "consult"
    assert second["spoken"].startswith("That was already handled:")


# ── Item 5: a consult with no receipt (the process died mid-turn) is shown as unknown ──────────────────────────────────

def test_a_revealed_consult_with_no_receipt_is_shown_as_result_unknown(ready):
    from core.realtime_voice.direct_host import digest
    r = ready
    sid = start(r)["voice_session_id"]
    store, conv = direct._FloorStore(), {"id": CID}
    from core.realtime_voice.transcripts import note_parts
    from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, PAGE
    from minimoi_portal.guild_ui.stores import Author
    for key, text in note_parts(sid, "consult", "kt3", 0, "consult: Run the nightly job.", False, limit=2000):
        r.notes.add_note(key, text, Author(AUTHOR_PREFIX + sid, "platform", "Voice transcript"), page=PAGE)
    store.put(conv, sid, record("consult_request", "consult_request:t3", owner_item_id="t3",
                                text_sha256=digest("Run the nightly job.")))
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers)
    rows, _ = shown(r)
    line = [row["voice"] for row in rows if row.get("voice", {}).get("session_id") == sid and row["voice"].get("unresolved")]
    assert len(line) == 1 and line[0]["kind"] == "consult_receipt" and line[0]["outcome"] == "result unknown"
    got = r.client.get(BASE + f"/consult/{sid}/t3?conversation_id={CID}", headers=r.headers).get_json()
    assert got["outcome"] == "unknown" and got["recorded"] is True and not r.turns


# ── Item 6: MC's thread reads a late receipt as late ───────────────────────────────────────────────────────────────────

def test_mcs_thread_says_a_late_receipt_arrived_after_the_summary():
    parts = reveal.receipt_parts("[Arrived after the summary] Answered. Reply mc-1. Spoken: Item 12 is in build.")
    assert parts == {"outcome": "answered · arrived after the summary", "ref_kind": "Reply", "ref": "mc-1",
                     "spoken": "Item 12 is in build."}


# ── Item 7: what every later view says of capture (R2 N6 with it) ─────────────────────────────────────────────────────

def test_every_later_view_says_incomplete_n_and_a_dropped_session_may_not_be_whole(ready):
    r = ready
    sid = start(r)["voice_session_id"]
    post(r, sid, [turn(r, "user", "t0", "Kept.")])
    r.client.post(BASE + f"/session/{sid}/end", headers=r.headers, json={"last_item_id": "t2", "unacknowledged": ["t1", "t2"]})
    dropped = start(r)["voice_session_id"]
    post(r, dropped, [turn(r, "user", "t0", "Said, then the page went away.")])
    from datetime import datetime, timedelta, timezone
    _rows, sessions = shown(r, when=datetime.now(timezone.utc) + timedelta(minutes=31))   # no End ever came: dropped
    by_id = {s["session_id"]: s for s in sessions}
    assert by_id[sid]["capture"] == "incomplete" and by_id[sid]["missing"] == 2
    assert by_id[dropped]["state"] == "dropped" and by_id[dropped]["capture"] == "unknown"
