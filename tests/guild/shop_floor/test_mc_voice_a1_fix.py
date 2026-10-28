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
