"""Chief of Staff's direct voice (domains/cos/direct_voice.py; Spec 165 v0.3 §6, §12.1's CoS line), through her real
adapter and store on the core's real routes (tests/cos/cos_voice_rig.py). The shared contract also runs on her adapter
(tests/realtime_voice/test_direct_contract.py). No provider, no Keychain, no microphone, no network."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cos_voice_rig as rig  # noqa: E402

from core.realtime_voice import direct_host as core  # noqa: E402
from core.realtime_voice.budget import VoiceBudget  # noqa: E402
from core.realtime_voice.controlled_config import TRANSCRIPTION_HINT, VOICE_MANNER  # noqa: E402
from core.agent_partner import PARTNER_STYLE  # noqa: E402
from domains.cos import claim_truth, direct_voice  # noqa: E402
from domains.cos.confer_service import ConferTurnRequest, ConferTurnResult, ConferTurnService  # noqa: E402

BASE, OWNER, READ = rig.BASE, rig.OWNER, rig.READ
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def r(monkeypatch, tmp_path):
    return rig.build(monkeypatch, tmp_path)


def outcome(answer):
    return answer.get_json()["outcome"]


# ── who may: the owner; every POST with the portal's verified-write marker (§6.7) ─────────────────────────────────────

def test_only_the_owner_reaches_her_voice_and_every_write_needs_the_portals_marker(r):
    guest = {**OWNER, "X-Minimoi-User-Tier": "guest"}
    nobody = {k: v for k, v in OWNER.items() if k != "X-Minimoi-Auth-Id"}
    for headers in (guest, nobody, READ):                                # READ: the owner, but no verified-write marker
        refused = r.client.post(BASE + "/bootstrap", headers=headers, json={"persona": "chief_of_staff",
                                                                             "conversation_id": "owner"})
        assert refused.status_code == 403
    assert r.mints == [] and list(r.voice.iterdir()) == []
    sid = rig.start(r)
    assert rig.ask(r, sid, headers=READ).status_code == 403 and r.turns == []
    assert r.client.post(BASE + f"/session/{sid}/end", headers=READ, json={}).status_code == 403
    assert r.client.get(BASE + "/capabilities", headers=READ).status_code == 200          # a read: the owner only
    assert r.client.get(BASE + "/capabilities", headers=guest).status_code == 403


def test_her_transcriber_is_given_minimois_command_words_on_openai_only(r):
    """A4-2: her session names MiniMoi's words to the transcriber where the provider takes a hint (OpenAI's prompt)."""
    from core.realtime_voice.standards import TRANSCRIPTION_VOCABULARY
    rig.start(r)
    assert r.mints[0]["transcription_vocabulary"] == TRANSCRIPTION_VOCABULARY

def test_her_only_conversation_is_owner(r):
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "conversation_id": "guild-1"})
    assert refused.status_code == 422 and r.mints == []


# ── UUID request ids; retried and repeated actions (§5.1, §6.4) ─────────────────────────────────────────────────────

def test_the_request_id_is_the_uuid_of_the_owner_turn_and_retries_run_nothing_new(r):
    sid = rig.start(r)
    first = rig.ask(r, sid, "call_1", "t1").get_json()
    assert first["outcome"] == "done" and first["receipt_kind"] == "turn"
    (turn,) = r.turns
    assert turn["request_id"] == str(uuid.uuid5(core.VOICE_NS, f"cos:{sid}:t1")) == str(uuid.UUID(turn["request_id"]))
    assert (turn["channel"], turn["conversation_id"]) == ("html_voice", "owner")
    assert rig.ask(r, sid, "call_1", "t1").get_json() == first                 # the same call again: replayed
    assert rig.ask(r, sid, "host-9", "t1").get_json()["same_turn"] is True       # Check after the model: no second turn
    assert len(r.turns) == 1 and len(r.runtime.calls) == 1
    again = rig.ask(r, sid, "call_2", "t2").get_json()                         # he says it again: a new turn, a new effect
    assert again["outcome"] == "done" and len(r.runtime.calls) == 2
    assert r.turns[1]["request_id"] == str(uuid.uuid5(core.VOICE_NS, f"cos:{sid}:t2"))


def test_a_note_retried_under_its_own_request_id_is_saved_once(r):
    sid = rig.start(r)
    rid = str(uuid.uuid5(core.VOICE_NS, f"cos:{sid}:t1"))
    r.notes.saved[rid] = "the kiln is hot"                                    # an earlier dispatch of this same turn saved it
    answer = rig.ask(r, sid, "call_1", "t1", words="Save a note that the kiln is hot", name="save_note").get_json()
    assert answer["outcome"] == "done" and answer["spoken"] == "Already saved." and answer["receipt"] == rid
    assert len(r.notes.saved) == 1


# ── §6.4's mapping; outcomes by the dispatch boundary ────────────────────────────────────────────────────────────────

def test_a_reply_is_done_with_its_turn_as_the_receipt_spoken_plainly(r):
    r.runtime.reply = "**Two** items are stuck:\n\n- the kiln\n- the glaze"
    sid = rig.start(r)
    answer = rig.ask(r, sid).get_json()
    assert answer["outcome"] == "done" and answer["answered"] is True and "**" not in answer["spoken"]
    assert uuid.UUID(answer["receipt"])                                       # the turn service's turn_id


def test_a_note_is_done_with_its_operation_as_the_receipt_and_shows_at_once(r):
    """D21 (a): a note saved by voice is in her platform memory at once, with its receipt."""
    sid = rig.start(r)
    answer = rig.ask(r, sid, words="Save a note that the kiln is hot", name="save_note").get_json()
    assert answer == {**answer, "outcome": "done", "saved": True, "spoken": "Saved.", "receipt_kind": "operation"}
    assert list(r.notes.saved.values()) == ["the kiln is hot"] and r.runtime.calls == []
    by_consult = rig.ask(r, sid, "host-2", "t2", words="Make a note that the glaze is ready").get_json()
    assert by_consult["saved"] is True and by_consult["spoken"] == "Saved."   # Check on a note command saves it too


def test_a_reply_cut_off_at_its_output_limit_says_so(r):
    def cut(prompt, context, policy):
        context["completion_evidence"] = {"state": "cut_off", "reason": "output_limit", "source": "openclaw_connector"}
        return "The first part."
    r.runtime.__class__ = type("Cut", (rig.Runtime,), {"__call__": lambda self, *a: cut(*a)})
    sid = rig.start(r)
    assert rig.ask(r, sid).get_json()["spoken"] == "The first part. The rest is cut off; ask in writing."


def test_a_refusal_before_dispatch_is_not_done_with_its_own_words(r):
    sid = rig.start(r)
    long_note = rig.ask(r, sid, "call_1", "t1", words="Save a note: " + "x" * 1001).get_json()
    assert long_note["outcome"] == "not_done" and "1000 characters" in long_note["spoken"]
    r.context["error"] = RuntimeError("context unavailable")                  # before the runtime: nothing dispatched
    plain = rig.ask(r, sid, "call_2", "t2").get_json()
    assert plain["outcome"] == "not_done" and plain["spoken"] == core.FAILED
    assert rig.effects(r) == 0


@pytest.mark.parametrize("error", [RuntimeError("COS Agent A timed out before replying."),
                                   __import__("domains.cos.confer_service", fromlist=["x"]).InvalidConferTurn("odd"),
                                   __import__("domains.cos.confer_service", fromlist=["x"]).ConferOperationFailed("failed")])
def test_any_exception_after_dispatch_to_her_runtime_is_unknown_whatever_its_class(r, error):
    r.runtime.error = error
    sid = rig.start(r)
    answer = rig.ask(r, sid).get_json()
    assert answer["outcome"] == "unknown" and answer["spoken"] == core.UNKNOWN and len(r.runtime.calls) == 1
    got = r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).get_json()
    assert got["outcome"] == "unknown" and len(r.runtime.calls) == 1        # reconciliation runs nothing


def test_a_note_the_store_says_was_not_saved_after_the_operation_is_unknown(r):
    """The ConferOperationFailed after the operation check (confer_service.py, the operation's own result): dispatched,
    so unknown (§6.4), never "nothing was done"."""
    r.notes.refuse = True
    sid = rig.start(r)
    answer = rig.ask(r, sid, words="Save a note that the kiln is hot", name="save_note").get_json()
    assert answer["outcome"] == "unknown" and answer.get("saved") is not True


def typed_confers_bound(monkeypatch, seconds, slack=0.1):
    """Typed Confer's own bound (openclaw_backend.turn_timeout_seconds), scaled down for a test."""
    import contextvars
    monkeypatch.setattr(direct_voice.openclaw_backend, "turn_timeout_seconds",
                        contextvars.ContextVar("cos_turn_timeout_seconds_test", default=seconds))
    monkeypatch.setattr(direct_voice, "TURN_SLACK_S", slack)


def test_a_turn_past_typed_confers_own_bound_is_unknown_and_recorded(r, monkeypatch):
    """A2-D1: past typed Confer's bound (scaled to 0.2 s here), a dispatched turn's outcome is unknown, recorded."""
    typed_confers_bound(monkeypatch, 0.2)
    r.runtime.hold = threading.Event()
    sid = rig.start(r)
    began = time.monotonic()
    answer = rig.ask(r, sid).get_json()
    assert answer["outcome"] == "unknown" and time.monotonic() - began < 5
    r.runtime.hold.set()
    kept = r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).get_json()
    assert kept["outcome"] == "unknown" and kept["recorded"] is True         # recorded, not left running


def test_a_long_consult_answers_pending_then_lands_done(r, monkeypatch):
    """A2-D1: a 60 s consult (scaled: the core's 25 s wait as 0.2 s, the turn as 0.6 s; typed Confer's bound as it is,
    120 s + slack) gets pending on its request, then done when it lands, said on the next heartbeat."""
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.2)
    r.runtime.hold = threading.Event()
    sid = rig.start(r)
    first = rig.ask(r, sid).get_json()
    assert first["outcome"] == "pending"
    threading.Timer(0.4, r.runtime.hold.set).start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        beat = rig.post(r, sid, []).get_json()
        if beat.get("completed"):
            break
        time.sleep(0.05)
    assert [(c["owner_item_id"], c["outcome"]) for c in beat["completed"]] == [("t1", "done")]
    kept = r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).get_json()
    assert kept["outcome"] == "done" and len(r.runtime.calls) == 1


def test_a_turn_that_had_not_dispatched_by_its_deadline_never_does(r, monkeypatch):
    typed_confers_bound(monkeypatch, 0.2)
    r.context["hold"] = threading.Event()
    sid = rig.start(r)
    answer = rig.ask(r, sid).get_json()
    assert answer["outcome"] == "not_done" and answer["spoken"] == direct_voice.LATE
    r.context["hold"].set()
    time.sleep(0.3)
    assert r.runtime.calls == []                                              # stopped at the boundary: nothing was done


def test_her_voice_turn_has_typed_confers_own_bound_and_the_request_answers_inside_the_portals():
    """A2-D1: the turn's bound is typed Confer's own (reused from openclaw_backend, not copied), so a 60 s consult runs to
    its end; the consult's HTTP answer still comes by the core's 25 s (pending), inside the portal's 40 s."""
    from domains.cos.backends import openclaw_backend
    from minimoi_portal import proxy
    consult_read = proxy._route_timeout("/app/cos", BASE.lstrip("/") + "/consult", "POST")[1]
    assert direct_voice.turn_deadline() == openclaw_backend.turn_timeout_seconds.get() + direct_voice.TURN_SLACK_S
    assert direct_voice.turn_deadline() > 60 and openclaw_backend.turn_timeout_seconds.get() == 120
    assert core.CONSULT_WAIT_S < consult_read == 40


def test_a_reminder_result_maps_to_its_receipt():
    result = ConferTurnResult(turn_id="t", conversation_id="owner", channel="html_voice", user_text="x",
                              reply="Reminder saved for 2026-10-29T14:00:00Z: stretch. Delivery: Telegram.",
                              backend_label="COS Platform", model_label="", created_at="now",
                              operation={"type": "reminder_set", "reminder_id": "r-1", "due_utc": "2026-10-29T14:00:00Z"})
    mapped = direct_voice.ChiefOfStaff._result("consult", result, None, None)
    assert mapped["outcome"] == "done" and mapped["receipt"] == "r-1" and mapped["receipt_kind"] == "reminder"


# ── refused before dispatch (§6.4): commands, notes, bare confirmations ──────────────────────────────────────────────

@pytest.mark.parametrize("words", ["/new", " /NEW ", "/remind 2026-10-29T14:00Z stretch", "/note the kiln", "/status",
                                   "/reset everything"])
def test_commands_are_done_in_writing_before_anything_is_recorded_or_sent(r, words):
    sid = rig.start(r)
    for name in ("consult", "save_note"):
        answer = rig.ask(r, sid, f"call_{name}", "t1", words=words, name=name).get_json()
        assert answer["outcome"] == "not_done" and answer["spoken"] == core.IN_WRITING
    assert r.turns == [] and rig.effects(r) == 0 and r.resets == [] and r.reminders == []
    assert r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).status_code == 404   # not admitted


def test_the_command_refusal_mirrors_the_turn_services_own_parsing():
    """Whatever the turn service would treat as a command (it resets her runtime, keeps a note by /note, sets a reminder),
    her voice refuses; the spoken note forms it treats as notes, and ordinary words, pass."""
    adapter = direct_voice.ChiefOfStaff(store=None, process_turn=None, briefing=None, memory=None)
    for words in ("/new", "/NEW", "  /new  ", "/remind 2026-10-29T14:00Z stretch", "/note kiln", "/note", "/whatever",
                  "Save a note that the kiln is hot", "save a note: kiln", "Remind me at 5 pm to stretch.",
                  "slash new", "What's new?", "new", "Start a /new session?"):
        seen = []
        service = ConferTurnService(call_backend=lambda *a: seen.append("runtime") or "ok", build_context=lambda: {},
                                    increment_chat=lambda: None, backend_metadata=lambda: ("x", "y"),
                                    save_note=lambda *a: seen.append("note") or {"saved": True},
                                    reset_conversation=lambda c: seen.append("reset") or True,
                                    save_reminder=lambda d, t, o: seen.append("reminder") or {
                                        "saved": True, "reminder": {"id": "r", "due_utc": "x"}})
        try:
            service.handle(ConferTurnRequest(text=words, channel="html_voice"))
        except Exception:
            pass
        command = bool(set(seen) & {"reset", "reminder"}) or words.strip().startswith("/")
        assert adapter.in_writing("consult", words) is command, words


def test_save_note_without_an_explicit_note_saves_nothing_and_leaves_the_turn_free(r):
    sid = rig.start(r)
    refused = rig.ask(r, sid, "call_1", "t1", words="Remember the kiln is hot", name="save_note").get_json()
    assert refused["outcome"] == "not_done" and refused["spoken"] == direct_voice.NOT_A_NOTE and rig.effects(r) == 0
    assert rig.ask(r, sid, "call_2", "t1", words="Remember the kiln is hot").get_json()["outcome"] == "done"   # still free


def test_a_bare_confirmation_is_confirmed_in_writing(r):
    """v3 addition 3: a tap on Check whose last words are a bare yes is still refused before anything runs (D16), with the
    stray-yes line instead of "Confirm that in writing." (adapted)."""
    sid = rig.start(r)
    for n, words in enumerate(("Yes.", "Ja, genau.", "go ahead", "Okay, sure.")):
        assert rig.ask(r, sid, f"call_{n}", "t1", words=words).get_json()["spoken"] == "Nothing's waiting; say it again if you want it."
    assert rig.effects(r) == 0


def test_a_spoken_reminder_leaves_no_reminder_record(r):
    """D20 (a): "Remind me at 5 pm to stretch." by voice is an ordinary turn of hers; no reminder is set by it, and her
    rules say reminders are set in writing. (The release check repeats this against the real reminder store:
    tests/cos/test_cos_direct_voice_wiring.py.)"""
    r.runtime.reply = "I've set a reminder for 5 pm to stretch."
    sid = rig.start(r)
    answer = rig.ask(r, sid, words="Remind me at 5 pm to stretch.").get_json()
    assert r.reminders == [] and answer["outcome"] == "done"
    assert "I've set a reminder" not in answer["spoken"]                     # claim_truth: no scheduling claim survives


# ── file claims (§6.4: _ui_turn's outbox snapshot and enforce_file_claims) ──────────────────────────────────────────

def test_a_download_claim_without_a_delivered_file_gets_the_platform_notice(r):
    r.runtime.reply = "Done. I saved it as a downloadable file."
    sid = rig.start(r)
    spoken = rig.ask(r, sid).get_json()["spoken"]
    assert core.speakable(claim_truth.FILE_NOTICE) in spoken


def test_a_file_her_turn_delivered_is_its_receipt_and_needs_no_notice(r):
    def writes(prompt, context, policy):
        (r.outbox / "report.md").write_text("# Report")
        return "Done. I saved it as a downloadable file."
    r.runtime.__class__ = type("Writes", (rig.Runtime,), {"__call__": lambda self, *a: writes(*a)})
    sid = rig.start(r)
    answer = rig.ask(r, sid).get_json()
    assert answer["files"] == ["report.md"] and core.speakable(claim_truth.FILE_NOTICE) not in answer["spoken"]


# ── the summary (D22 (a)); one ledger (D10 (a)) ──────────────────────────────────────────────────────────────────────

def test_her_summary_is_a_plain_gateway_completion_under_her_own_key_with_no_tools(r):
    sid = rig.start(r)
    rig.post(r, sid, [rig.event(r, "user", "t0", "We looked at #12. We're done.")])
    answer = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    (sent,) = r.gateway.requests
    assert answer["state"] == "proposed" and sent["json"]["model"] == "minimoi-voice-summary"
    assert not {"tools", "tool_choice", "user"} & set(sent["json"])
    assert sent["headers"]["Authorization"] == "Bearer test-only-summary-key" and r.runtime.calls == []
    assert direct_voice.ChiefOfStaff.summary_key == "voice-summary-cos"


def test_without_her_key_the_summary_is_unavailable_never_her_runtime(monkeypatch, tmp_path):
    env = {k: v for k, v in rig.ENV.items() if k != "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS"}
    r = rig.build(monkeypatch, tmp_path, environ=env)
    sid = rig.start(r)
    rig.post(r, sid, [rig.event(r, "user", "t0", "We're done.")])
    answer = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    assert answer["state"] == "unavailable" and r.gateway.requests == [] and r.runtime.calls == []


def test_one_ledger_for_mc_and_cos_concurrent_reservations_add_up(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tests" / "realtime_voice"))
    import test_direct_host as fake_mc_side                               # the core's fake host, on the same ledger
    budget = VoiceBudget("sqlite:///" + str(tmp_path / "one.db"), cap_usd=lambda: 20)
    budget.ensure_schema()
    cos = rig.build(monkeypatch, tmp_path / "cos", budget=budget)
    from flask import Flask
    other_app, host = Flask("other"), fake_mc_side.Host(tmp_path / "card")
    fake_mc_side.card_release(tmp_path / "card", "1.0.0", {"card": "Other.", "voices": {"openai": "cedar"}})
    core.install_direct_voice(other_app, host, url_prefix="/tp/voice", environ=dict(fake_mc_side.ENV), budget=budget)
    other = other_app.test_client()
    results, barrier = [], threading.Barrier(6)

    def go(i):
        barrier.wait()
        if i % 2:
            got = cos.app.test_client().post(BASE + "/bootstrap", headers=OWNER, json={
                "persona": "chief_of_staff", "provider": "openai", "conversation_id": "owner"})
        else:
            got = other.post(fake_mc_side.BASE + "/bootstrap", headers=fake_mc_side.OWNER, json={
                "persona": "test_person", "provider": "openai", "conversation_id": "owner"})
        results.append(got.status_code)
    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200] * 6
    assert budget.status()["reserved_usd"] == pytest.approx(6 * int(rig.ALLOWANCE) / 1_000_000)
    with budget._tx() as run:
        domains = sorted(row[0] for row in run("SELECT domain FROM voice_budget_entry", ()).fetchall())
    assert domains == ["cos"] * 3 + ["tp"] * 3


# ── her session text (§6.3) ──────────────────────────────────────────────────────────────────────────────────────────

def instructions_of(r, provider="openai"):
    rig.start(r, provider)
    session = r.mints[-1]
    return session["instructions"]


def test_her_session_text_is_rules_then_card_then_her_briefing_and_continuity_as_data(r):
    text = instructions_of(r)
    rules, card, data = (text.index(h) for h in ("=== OPERATING RULES ===", "=== WHO YOU ARE ===",
                                                  "=== THIS CONVERSATION SO FAR (DATA) ==="))
    assert rules < card < data
    assert text.index("your two tools are 'consult'") < text.index("You are the low-latency voice of Robert's Chief")
    assert text.index(direct_voice.BRIEFING_HEAD.strip()) < text.index(direct_voice.CONTINUITY_HEAD.strip())
    assert "Set reminders in writing" in text and "remind me at <time>" not in text
    assert "consult_cos_agent" not in text and "save_cos_note" not in text
    assert r.mints[-1]["transcription_language"] is None and r.mints[-1]["voice"] == "ballad"   # bilingual (D19), ballad


def test_no_voice_manner_text_is_carried_beyond_the_clauses_placed_by_the_spec(r):
    """§6.2: VOICE_MANNER's clauses go to her card (restated, from the persona) or her rules; the core-rule clauses wait for
    D17. No VOICE_MANNER sentence is carried as it is, nor PARTNER_STYLE or the managed transcription hint."""
    text = instructions_of(r)
    for sentence in re.split(r"(?<=[.;])\s+", VOICE_MANNER.split("\n", 1)[1]):
        if len(sentence) > 25:
            assert sentence.rstrip(".;") not in text, sentence
    assert PARTNER_STYLE.strip()[:60] not in text and TRANSCRIPTION_HINT not in text
    for clause in ("Board post-it", "Viennese", "complete native conversation history"):
        assert clause not in text, clause


def test_on_grok_she_has_no_tools_and_acts_through_check_with_cos(monkeypatch, tmp_path):
    r = rig.build(monkeypatch, tmp_path, voices={"openai": "cedar", "xai": "ara"})   # a test card naming a Grok voice
    answer = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "xai",
                                                                      "conversation_id": "owner"})
    assert answer.status_code == 200 and "page_responses" not in answer.get_json()
    session = r.mints[-1]
    assert not session.get("tools") and "press Check with CoS" in session["instructions"]
    assert "Use consult whenever" not in session["instructions"] and "ask Robert to press Check with CoS" in session["instructions"]
    sid = answer.get_json()["voice_session_id"]
    model = r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "call_1", "name": "consult",
                                                                  "owner_transcript_item": "What is stuck?", "owner_item_id": "t1"}).get_json()
    assert model["error"] == "not_confirmed" and r.turns == []              # the model's own call runs nothing (B N7, v3)
    assert rig.ask(r, sid, "host-1", "t1").get_json()["outcome"] == "done"   # Check with CoS acts


def test_on_openai_the_page_makes_every_reply_that_may_act(r):
    answer = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "openai",
                                                                      "conversation_id": "owner"}).get_json()
    session = r.mints[-1]
    assert answer["page_responses"] is True and session["turn_detection"]["create_response"] is False
    assert session["turn_detection"]["interrupt_response"] is True
    assert [t["name"] for t in session["tools"]] == ["consult", "save_note"]


def test_over_the_bound_continuity_is_cut_first_then_memory_each_marked_rules_and_card_never():
    rules, card = "R" * 1000, "C" * 1000
    briefing = lambda memory: "B" * 1000 + "\n--- MEMORY ---\n" + memory   # noqa: E731
    memory = "\n".join(f"[note] 2026-10-{d:02d}: entry {d} " + "m" * 200 for d in range(1, 29))
    continuity = "\n".join(f"Robert: turn {i} " + "c" * 300 for i in range(20))
    whole = direct_voice.bounded(rules, card, briefing, memory, continuity, limit=100_000)
    assert direct_voice.CONTINUITY_CUT not in whole and direct_voice.MEMORY_CUT[:13] not in whole
    some = direct_voice.bounded(rules, card, briefing, memory, continuity, limit=12_000)
    assert len(some.encode()) <= 12_000 and direct_voice.CONTINUITY_CUT in some and "[Memory cut" not in some
    assert "turn 19" in some and "turn 0 " not in some                       # the oldest turns go first
    tight = direct_voice.bounded(rules, card, briefing, memory, continuity, limit=6_000)
    assert len(tight.encode()) <= 6_000 and "[Memory cut: the oldest" in tight and "Robert: turn" not in tight
    assert "entry 28" in tight and "entry 1 " not in tight and rules in tight and card in tight
    with pytest.raises(core.VoiceUnavailable, match="does not fit"):
        direct_voice.bounded(rules, card, briefing, memory, continuity, limit=2_500)


def test_a_briefing_too_big_for_any_session_refuses_voice_and_says_so(monkeypatch, tmp_path):
    r = rig.build(monkeypatch, tmp_path, briefing=lambda memory: "B" * 30_000 + memory)
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "openai",
                                                                       "conversation_id": "owner"})
    assert refused.status_code == 503 and refused.get_json()["error"] == direct_voice.TOO_LONG
    assert r.mints == [] and list(r.voice.iterdir()) == [] and r.budget.status()["reserved_usd"] == 0


def test_the_briefing_is_built_once_per_session(monkeypatch, tmp_path):
    built = []
    r = rig.build(monkeypatch, tmp_path, briefing=lambda memory: built.append(1) or "briefing " + memory)
    rig.start(r)
    assert len(built) == 1


def test_continuity_is_her_last_settled_sessions_never_a_live_one(r):
    first = rig.start(r)
    rig.post(r, first, [rig.event(r, "user", "t0", "The kiln is hot."), rig.event(r, "assistant", "a0", "Noted, careful.")])
    r.client.post(BASE + f"/session/{first}/end", headers=OWNER, json={"last_item_id": "a0", "unacknowledged": []})
    live = rig.start(r)
    rig.post(r, live, [rig.event(r, "user", "t0", "Live words, never continuity.")])
    text = instructions_of(r)
    assert "Robert: The kiln is hot." in text and "CoS: Noted, careful." in text and "Live words" not in text


# ── her switches (§6.8); no store, no voice; not admitted ────────────────────────────────────────────────────────────

def test_off_nothing_is_installed(monkeypatch, tmp_path):
    r = rig.build(monkeypatch, tmp_path, environ={"MINIMOI_ROLE": "development", core.ALLOWANCE_ENV: rig.ALLOWANCE})
    assert r.metadata is None and r.client.post(BASE + "/bootstrap", headers=OWNER, json={}).status_code == 404
    assert r.client.get(BASE + "/capabilities", headers=READ).status_code == 404


@pytest.mark.parametrize("role", ["production", None])
def test_in_production_a_start_also_needs_her_production_switch(monkeypatch, tmp_path, role):
    env = {k: v for k, v in rig.ENV.items() if k != "MINIMOI_ROLE"} | ({"MINIMOI_ROLE": role} if role else {})
    r = rig.build(monkeypatch, tmp_path, environ=env)
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "conversation_id": "owner"})
    assert refused.status_code == 503 and refused.get_json()["error"] == core.NOT_ON and r.mints == []
    assert r.client.get(BASE + "/capabilities", headers=READ).get_json()["direct_voice"] is False
    on = rig.build(monkeypatch, tmp_path / "on", environ={**env, direct_voice.PRODUCTION_SWITCH: "1"})
    assert rig.start(on)


def test_the_production_filter_holds_her_start_off_unless_its_switch_is_exactly_on():
    base = {"MINIMOI_ROLE": "production", "MINIMOI_COS_VOICE_DIRECT": "1"}
    assert direct_voice.production_environ(base) == {"MINIMOI_ROLE": "production"}
    assert not direct_voice.may_start(base)
    for value in ("0", "true", "yes", ""):
        assert not direct_voice.may_start({**base, direct_voice.PRODUCTION_SWITCH: value})
    assert direct_voice.may_start({**base, direct_voice.PRODUCTION_SWITCH: "1"})
    assert direct_voice.may_start({"MINIMOI_ROLE": "standby", "MINIMOI_COS_VOICE_DIRECT": "1"})      # staging
    assert not direct_voice.may_start({"MINIMOI_ROLE": "standby", direct_voice.PRODUCTION_SWITCH: "1"})


def test_her_production_switch_is_read_only_by_cos_scheduler_code():
    """Read in cos-scheduler only (§6.8): no portal, core or other service code names it."""
    readers = [str(p.relative_to(ROOT)) for top in ("minimoi_portal", "core", "domains", "services", "utils")
               for p in (ROOT / top).rglob("*.py") if direct_voice.PRODUCTION_SWITCH in p.read_text(errors="replace")]
    assert readers and all(path.startswith("domains/cos/") for path in readers), readers


def test_no_store_no_voice(monkeypatch, tmp_path):
    r = rig.build(monkeypatch, tmp_path, with_store=False)
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "conversation_id": "owner"})
    assert refused.status_code == 503 and refused.get_json()["error"] == direct_voice.NO_STORE and r.mints == []
    assert r.client.get(BASE + "/capabilities", headers=READ).get_json()["direct_voice"] is False


def test_her_real_card_speaks_ara_on_grok(r):
    answer = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "xai",
                                                                      "conversation_id": "owner"})
    assert answer.status_code == 200 and r.mints[-1]["voice"] == "ara" and r.metadata["unvoiced"] == {}
    assert "voice_notices" not in answer.get_json()


def test_a_card_naming_no_grok_voice_refuses_a_grok_start(monkeypatch, tmp_path):
    r = rig.build(monkeypatch, tmp_path, voices={"openai": "marin"})          # D2 (a): she takes no provider default
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "xai",
                                                                       "conversation_id": "owner"})
    words = "Voice unavailable: no Grok voice is chosen for Chief of Staff yet."
    assert refused.status_code == 503 and refused.get_json()["error"] == words and r.mints == []
    assert r.metadata["unvoiced"] == {"xai": words} and r.metadata["model_actions"] == {"openai": True, "xai": False}


# ── her records: the needs-attention list, detail and download through her store (§6.5, D7) ─────────────────────────

def test_an_incomplete_session_needs_attention_and_downloads_from_her_store(r):
    sid = rig.start(r)
    rig.post(r, sid, [rig.event(r, "user", "t0", "Check the board."), rig.event(r, "assistant", "a0", "Two it", partial=True)])
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": ["t1"]})
    assert ended.get_json()["capture"] == "incomplete"
    listed = r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]
    assert [v["session_id"] for v in listed] == [sid] and listed[0]["reasons"] == ["incomplete"]
    assert r.client.get(BASE + "/capabilities", headers=READ).get_json()["direct_records"] is True
    text = r.client.get(BASE + f"/sessions/{sid}/transcript?conversation_id=owner", headers=READ).get_data(True)
    assert "Robert (speech recognition): Check the board." in text and "Chief of Staff (partial): Two it" in text
    projected = [p for p in r.recall.iterdir() if p.name.startswith("voice-")]
    assert len(projected) == 1                                               # her recall has it once settled (D6)


def test_the_records_stay_out_of_her_runtimes_recall_while_live(r):
    sid = rig.start(r)
    rig.post(r, sid, [rig.event(r, "user", "t0", "Not for her runtime yet.")])
    assert [p.name for p in r.recall.iterdir()] == []
    assert "Not for her runtime yet." in (r.voice / f"{sid}.jsonl").read_text()


# ── no copy, by architecture (§12.1) ─────────────────────────────────────────────────────────────────────────────────

def test_her_adapter_and_store_import_the_core_and_define_only_its_hooks():
    sys.path.insert(0, str(ROOT / "tests" / "realtime_voice"))
    import test_no_copy as nocopy
    for module in ("domains/cos/direct_voice.py", "domains/cos/voice_store.py"):
        source = (ROOT / module).read_text()
        names = nocopy.aliases(__import__("ast").parse(source))
        assert any(v.startswith(nocopy.DIRECT) for v in names.values()), module
        assert nocopy.python_copies(source) == [], module


def test_the_shared_session_module_sends_no_opening_instruction_for_any_host():
    """A2 note (A1's review): MC's page sends no opening instruction, and CoS's page will use the same session module
    (A3), so she sends none either. Were a host to pass one, the controller sends it through its request recorder
    (this._ask), as a tracked page request that cannot take another response's place."""
    module = (ROOT / "core/realtime_voice/static/direct-voice-session.js").read_text()
    block = module[module.index("new RealtimeVoiceController({"):]
    assert "openingInstruction: null," in block[:block.index("});")]
    controller = (ROOT / "core/realtime_voice/static/realtime-voice-controller.js").read_text()
    assert "this._ask(null, (tag) => this._instruct(this._openingInstruction, tag));" in controller


# ── Gate 2 Q N3, Q N4 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_with_no_continuity_the_cut_marker_names_only_what_was_cut():
    """Q N3: with no settled session to carry and the text over its bound, memory is cut and marked; the text says there
    was no continuity, never that continuity was cut."""
    rules, card = "R" * 1000, "C" * 1000
    briefing = lambda memory: "B" * 1000 + "\n--- MEMORY ---\n" + memory   # noqa: E731
    memory = "\n".join(f"[note] 2026-10-{d:02d}: entry {d} " + "m" * 200 for d in range(1, 29))
    tight = direct_voice.bounded(rules, card, briefing, memory, "", limit=6_000)
    assert len(tight.encode()) <= 6_000 and "[Memory cut: the oldest" in tight
    assert direct_voice.CONTINUITY_CUT not in tight and direct_voice.NO_CONTINUITY in tight
    some = direct_voice.bounded(rules, card, briefing, memory, "Robert: one turn", limit=6_000)    # guard: there was some
    assert direct_voice.CONTINUITY_CUT in some and direct_voice.NO_CONTINUITY not in some


@pytest.mark.parametrize("role", ["Production", "prod", "PRODUCTION", "producton", "", "staging"])
def test_an_unrecognised_role_counts_as_production_and_holds_her_start_off(role):
    """Q N4: only the named non-production roles (standby, development) start on the install switch alone; any other
    MINIMOI_ROLE, an empty one included, is production (fail closed)."""
    env = {"MINIMOI_ROLE": role, "MINIMOI_COS_VOICE_DIRECT": "1"}
    assert direct_voice.production(env) is True
    assert not direct_voice.may_start(env)
    assert direct_voice.may_start({**env, direct_voice.PRODUCTION_SWITCH: "1"})


@pytest.mark.parametrize("role", ["standby", "development"])
def test_the_named_non_production_roles_start_on_the_install_switch(role):
    assert direct_voice.production({"MINIMOI_ROLE": role}) is False
    assert direct_voice.may_start({"MINIMOI_ROLE": role, "MINIMOI_COS_VOICE_DIRECT": "1"})


# ── Gate 2 Q S2, E S2 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_admitted_with_no_store_the_capabilities_say_why_voice_is_unavailable(monkeypatch, tmp_path):
    """Q S2: admitted (her switch on, outside production) but no store: the page is told "there is no place to keep what
    is said", never that she is not admitted."""
    r = rig.build(monkeypatch, tmp_path, with_store=False)
    caps = r.client.get(BASE + "/capabilities", headers=READ).get_json()
    assert caps["direct_voice"] is False and caps["direct_refused"] == direct_voice.NO_STORE


def test_a_consult_still_running_at_end_is_listed_until_it_lands_then_with_its_late_result(r, monkeypatch):
    """E S2 (the tester's E4, CoS on OpenAI): End while a consult runs. The session is listed while it runs, its detail
    names it pending; when it lands after End its outcome and receipt are kept and projected, the receipt marked late in
    her recall, and the session is listed with that result until he dismisses it."""
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.2)
    r.runtime.hold = threading.Event()
    sid = rig.start(r)
    assert rig.post(r, sid, [rig.event(r, "user", "t1", "What is stuck on #12?")]).status_code == 200
    assert rig.ask(r, sid).get_json()["outcome"] == "pending"
    end = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": []})
    assert end.get_json()["capture"] == "complete"
    detail = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=READ).get_json()
    assert detail["pending"] == ["t1"] and detail["reasons"] == ["pending_consult"] and detail["needs_attention"] is True
    listed = r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]
    assert [v["session_id"] for v in listed] == [sid]
    assert r.client.get(BASE + "/capabilities", headers=READ).get_json()["direct_records"] is True
    r.runtime.hold.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        kept = r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).get_json()
        if kept["outcome"] == "done":
            break
        time.sleep(0.05)
    time.sleep(0.1)                                          # the worker keeps the outcome, then the receipt
    (view,) = r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]
    assert view["session_id"] == sid and view["reasons"] == ["late_result"] and view["late"] == ["t1"]
    detail = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=READ).get_json()
    assert [rec["item_id"] for rec in detail["records"] if rec["role"] == "receipt"] == ["kt1"]
    (projection,) = [p for p in r.recall.iterdir() if p.name.startswith("voice-")]
    rows = [json.loads(line) for line in projection.read_text().splitlines()]
    assert [x["outcome"] for x in rows if x["kind"] == "consult"] == ["done"]
    assert [x.get("late") for x in rows if x["kind"] == "turn" and x["speaker"] == "consult receipt"] == [True]
    gone = r.client.post(BASE + f"/sessions/{sid}/dismiss", headers=OWNER, json={"conversation_id": "owner"})
    assert gone.get_json()["dismissed"] is True
    assert r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"] == []
    assert len(r.runtime.calls) == 1


# ── Gate 2 E S1, Q S6 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_second_call_on_a_turn_still_running_is_told_it_is_already_being_handled(r, monkeypatch):
    """E S1 (the tester's E2): a second call on an owner turn that is still running (two calls in one reply, or Check while
    the model's call runs) gets 409 in flight, told the turn is already being handled, never "the answer will be in the
    conversation" for a request that never runs. The first call's outcome is the one effect, and its answer."""
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.2)
    r.runtime.hold = threading.Event()
    sid = rig.start(r)
    first = rig.ask(r, sid).get_json()
    assert first["outcome"] == "pending" and first["spoken"] == core.PENDING          # the first call's own pending
    for call_id, name in (("call_2", "consult"), ("host-3", "consult"), ("call_4", "save_note")):
        second = rig.ask(r, sid, call_id=call_id, name=name)
        assert second.status_code == 409, call_id
        assert second.get_json() == core.IN_HAND and second.get_json()["spoken"] == core.HANDLING, call_id
    assert core.HANDLING == ("That turn is already being handled; its answer will be said once it is back. Ask anything "
                             "else in it on its own.")
    r.runtime.hold.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and rig.post(r, sid, []).get_json().get("completed") is None:
        time.sleep(0.05)
    assert len(r.runtime.calls) == 1                                                   # one effect


@pytest.mark.parametrize("said,folded", [
    ("Save a note that the kiln is hot.", "Save a note that the kiln is hot. Name a long river."),     # the reviewer's
    ("Name a large city.", "Name a large city. Name a long river."),                                  # xa3's own words
])
def test_on_grok_check_refuses_words_that_begin_with_a_request_already_checked(monkeypatch, tmp_path, said, folded):
    """Q S6 (live probe xa3: item b30aacef "Name a large city." was overtaken before its audio, and xAI folded it into the
    next item, c651e031 "Name a large city. Name a long river."): Check on the folded words would send a request already
    acted on under a new key, so a second note could be saved. On a provider in FOLDS_OVERTAKEN, Check refuses words that
    begin with the kept words of an item already consulted: "Check it in writing." Nothing runs; a new request still does."""
    r = rig.build(monkeypatch, tmp_path, voices={"openai": "cedar", "xai": "ara"})
    sid = rig.start(r, provider="xai")
    first = rig.ask(r, sid, call_id="host-1", item="t0", words=said).get_json()
    assert first["outcome"] == "done"
    before = rig.effects(r)
    refused = rig.ask(r, sid, call_id="host-2", item="t2", words=folded)
    assert refused.status_code == 200
    answer = refused.get_json()
    assert answer["outcome"] == "not_done" and answer["error"] == "repeated_words" and answer["spoken"] == core.FOLDED
    assert "Check it in writing" in core.FOLDED
    assert rig.effects(r) == before                                                    # no second note, no second turn
    fresh = rig.ask(r, sid, call_id="host-3", item="t3", words="Name a long river.").get_json()
    assert fresh["outcome"] == "done" and rig.effects(r) == before + 1                 # a new request still acts


def test_on_openai_the_fold_rule_does_not_apply(monkeypatch, tmp_path):
    """Q S6, guard: OpenAI does not fold an overtaken turn (FOLDS_OVERTAKEN is xAI's), so a turn of his that begins with
    an earlier request is his own words, a new turn, and acts (§5.1: a repeat is a new effect)."""
    r = rig.build(monkeypatch, tmp_path)
    sid = rig.start(r)
    assert rig.ask(r, sid, call_id="call_1", item="t0", words="Name a large city.").get_json()["outcome"] == "done"
    again = rig.ask(r, sid, call_id="call_2", item="t2", words="Name a large city. Name a long river.").get_json()
    assert again["outcome"] == "done" and len(r.runtime.calls) == 2


# ── Gate 2 verification V S1 ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_check_still_running_when_the_summary_is_decided_stays_listed_until_it_lands_and_is_seen(r, monkeypatch):
    """V S1 (the reviewer's probe): Sum up while a check still runs, then Confirm the card. The session stayed off Confer's
    list (a decided summary left it), so the late result was never shown and the reveal's "reload to see it under Voice
    sessions to look at" was untrue. A session with a pending consult, or one that landed after End, is listed until he
    dismisses it, whatever the summary's state."""
    monkeypatch.setattr(core, "CONSULT_WAIT_S", 0.2)
    r.runtime.hold = threading.Event()
    sid = rig.start(r)
    assert rig.post(r, sid, [rig.event(r, "user", "t1", "What is stuck on #12?")]).status_code == 200
    assert rig.ask(r, sid).get_json()["outcome"] == "pending"
    proposed = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER,
                             json={"conversation_id": "owner", "last_item_id": "t1", "unacknowledged": []}).get_json()
    assert proposed["state"] == "proposed"
    decided = r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER,
                            json={"conversation_id": "owner", "decision": "confirm"}).get_json()
    assert decided["state"] == "confirmed"
    detail = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=READ).get_json()
    assert detail["pending"] == ["t1"] and "pending_consult" in detail["reasons"] and detail["needs_attention"] is True
    assert [v["session_id"] for v in r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]] == [sid]
    assert r.client.get(BASE + "/capabilities", headers=READ).get_json()["direct_records"] is True
    r.runtime.hold.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if r.client.get(BASE + f"/consult/{sid}/t1?conversation_id=owner", headers=READ).get_json()["outcome"] == "done":
            break
        time.sleep(0.05)
    time.sleep(0.1)                                          # the worker keeps the outcome, then the receipt
    (view,) = r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]
    assert view["session_id"] == sid and view["reasons"] == ["late_result"] and view["summary"] == "confirmed"
    detail = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=READ).get_json()
    (receipt,) = [rec for rec in detail["records"] if rec["role"] == "receipt"]
    assert receipt["text"].startswith("[Arrived after the summary]")              # §5.2's mark
    r.client.post(BASE + f"/sessions/{sid}/dismiss", headers=OWNER, json={"conversation_id": "owner"})
    assert r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"] == []


def test_a_decided_session_with_nothing_running_or_late_still_leaves_the_list(r):
    """V S1, guard: a settled, decided session leaves the list (§6.5, "not a history product"), as before."""
    sid = rig.start(r)
    rig.post(r, sid, [rig.event(r, "user", "t1", "We looked at #12. We're done.")])
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner", "last_item_id": "t1",
                                                                          "unacknowledged": []})
    assert [v["session_id"] for v in r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"]] == [sid]
    r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER, json={"conversation_id": "owner", "decision": "confirm"})
    assert r.client.get(BASE + "/sessions", headers=READ).get_json()["sessions"] == []
