"""Confirm by voice, v3 (the app acts; the voice only talks): the server's one waiting action, the utterance route and the
action path (V3_BUILD_BRIEF.md "The server holds the waiting action and enforces it"; DESIGN_confirm_by_voice_v3_simple.md;
Codex's safety lines, REVIEW_codex_confirm_by_voice.md). Run over every host: the test adapter of test_direct_host.py, MC's
real adapter on the synthetic Guild mount and Chief of Staff's real adapter and file store (test_direct_contract's driver).
No provider, no gateway, no Keychain, no microphone, no network: the understanding call is not reached here (the grammar
fast path hears every utterance below, or the host has no call, which counts as chat); tests/realtime_voice/
test_intent_gateway.py drives it with a fake gateway."""
import hashlib
import re
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cos"))

from core.realtime_voice import direct_host as core  # noqa: E402
from test_direct_contract import HOSTS, driver, host, post, rig  # noqa: E402,F401  (fixtures, for the host matrix)

REQUEST = {"test adapter": "save a note: the kiln is hot", "MC": "make a note that the kiln is hot",
           "CoS": "save a note that the kiln is hot"}
CONSULT = {"test adapter": "ask TP what is stuck on 12", "MC": "ask MC what is stuck on 12",
           "CoS": "ask CoS what is stuck on 12"}


def utter(h, sid, item, text):
    return h.client.post(h.base + f"/session/{sid}/utterance", headers=h.headers,
                         json={"conversation_id": h.cid, "owner_item_id": item, "text": text})


def asked(h, sid, action_id, after):
    return h.client.post(h.base + f"/session/{sid}/utterance/{action_id}/asked", headers=h.headers,
                         json={"after_item": after})


def confirm(h, sid, decision, call_id=None):
    """The page's action call for a confirmed action: its id, the yes item, the action's owner item and text hash."""
    return h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": call_id or "confirm-" + decision["action_id"], "action_id": decision["action_id"],
        "confirm_item_id": decision["confirm_item_id"], "owner_item_id": decision["owner_item_id"],
        "text_sha256": decision["text_sha256"]})


def tap(h, sid, item, words, action_id=None, call_id="host-1"):
    return h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": call_id, "name": "consult", "owner_transcript_item": words, "owner_item_id": item,
        **({"action_id": action_id} if action_id else {})})


def requested(h, sid, item="t1", text=None):
    """His request at ``item``, heard (ask), and its question started after the assistant's t(item+1)."""
    ask = utter(h, sid, item, text or REQUEST[h.kind]).get_json()
    assert ask["decision"] == "ask" and ask["kind"] in ("note", "consult") and ask["owner_item_id"] == item, ask
    question = f"t{int(item[1:]) + 1}"
    assert asked(h, sid, ask["action_id"], question).get_json()["state"] == "asked"
    return ask


def last_effect_words(h):
    """The words the test adapter's typed turn acted on last (the request's own text, never the yes)."""
    return h.r.host.turns[-1][5] if h.kind == "test adapter" else None


# ── request, ask, yes: one effect on the request's own owner item and words ─────────────────────────────────────────────

def test_request_ask_yes_acts_once_on_the_requests_own_item_and_words_never_the_yes(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    assert ask["text_sha256"] == hashlib.sha256(REQUEST[h.kind].encode()).hexdigest()
    assert h.effects() == 0                                                     # nothing acts on the request alone
    yes = utter(h, sid, "t3", "Yes, go ahead.").get_json()
    assert yes["decision"] == "confirm" and yes["action_id"] == ask["action_id"] and yes["confirm_item_id"] == "t3"
    assert yes["owner_item_id"] == "t1" and yes["text_sha256"] == ask["text_sha256"]
    assert h.effects() == 0                                                     # the utterance route never acts
    done = confirm(h, sid, yes).get_json()
    assert done["outcome"] == "done" and done["owner_item_id"] == "t1" and h.effects() == 1, done
    words = last_effect_words(h)
    if h.kind == "test adapter":
        assert words == REQUEST[h.kind] and h.r.host.items == ["t1"]            # the request's item and words
    rid = core.request_id("t1")
    got = h.client.get(h.base + f"/consult/{sid}/t1?conversation_id={h.cid}", headers=h.headers).get_json()
    assert got["outcome"] == "done" and got["recorded"] is True                 # reconcile names the request's item
    assert h.client.get(h.base + f"/consult/{sid}/t3?conversation_id={h.cid}", headers=h.headers).status_code == 404
    assert rid == "consult_request:t1"


def test_a_retried_confirm_and_a_duplicate_yes_make_one_effect_with_the_same_receipt(host):
    h = host
    sid = h.start()
    requested(h, sid, "t1")
    yes = utter(h, sid, "t3", "yes").get_json()
    first = confirm(h, sid, yes).get_json()
    again = confirm(h, sid, yes).get_json()                                     # the page's retry: the same call id
    assert again == first and h.effects() == 1
    twice = utter(h, sid, "t4", "yes, yes").get_json()                          # a second yes: the same action, again
    assert twice["decision"] == "confirm" and twice["action_id"] == yes["action_id"] and twice["again"] is True
    other_call = confirm(h, sid, {**yes, **twice}, call_id="confirm-again").get_json()
    assert h.effects() == 1 and other_call.get("receipt") == first.get("receipt")
    assert utter(h, sid, "t3", "yes").get_json()["repeated"] is True           # a retried post: its first decision


def test_the_spoken_confirmation_must_match_the_action_the_server_heard(host):
    h = host
    sid = h.start()
    requested(h, sid, "t1")
    yes = utter(h, sid, "t3", "yes").get_json()
    for wrong in ({"confirm_item_id": "t4"}, {"owner_item_id": "t3"}, {"text_sha256": "0" * 64}):
        refused = confirm(h, sid, {**yes, **wrong}, call_id="confirm-wrong").get_json()
        assert refused["outcome"] == "not_done" and refused["error"] == "not_confirmed"
    assert h.effects() == 0
    assert confirm(h, sid, yes).get_json()["outcome"] == "done" and h.effects() == 1


# ── no, anything else, silence ─────────────────────────────────────────────────────────────────────────────────────────

def test_no_drops_it_and_nothing_acts(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    no = utter(h, sid, "t3", "No.").get_json()
    assert no == {**no, "decision": "dropped", "action_id": ask["action_id"], "reason": "no"}
    late = confirm(h, sid, {**ask, "confirm_item_id": "t3"}).get_json()
    assert late["outcome"] == "not_done" and h.effects() == 0
    assert utter(h, sid, "t5", "yes").get_json()["decision"] == "chat"        # after a no, a yes is conversation


def test_anything_else_drops_it_and_is_handled_as_new_a_new_request_waits_instead(host):
    h = host
    sid = h.start()
    first = requested(h, sid, "t1")
    other = utter(h, sid, "t3", "Tell me a joke about kilns.").get_json()
    assert other["decision"] == "chat" and other["dropped"] == first["action_id"]
    second = requested(h, sid, "t5", CONSULT[h.kind])
    assert second["kind"] == "consult" and second["action_id"] != first["action_id"]
    replaced = requested(h, sid, "t7")                                         # a new request replaces the waiting one
    assert replaced["replaced"] == second["action_id"]
    yes = utter(h, sid, "t9", "yes").get_json()
    assert yes["action_id"] == replaced["action_id"]                            # a yes confirms only the newest
    stale = tap(h, sid, "t5", CONSULT[h.kind], action_id=second["action_id"]).get_json()
    assert stale["outcome"] == "not_done" and stale["error"] == "not_waiting" and h.effects() == 0


def test_silence_past_thirty_seconds_drops_it_and_a_late_yes_does_nothing(host):
    h = host
    sid = h.start()
    clock = h.r.clock
    ask = requested(h, sid, "t1")
    clock[0] += core.ANSWER_S + 1
    late = utter(h, sid, "t3", "yes").get_json()
    assert late["decision"] == "nothing_waiting" and h.effects() == 0           # a stray yes: said so, nothing acts
    assert asked(h, sid, ask["action_id"], "t4").status_code == 409             # expired: no question re-arms it
    assert tap(h, sid, "t1", REQUEST[h.kind], action_id=ask["action_id"]).get_json()["error"] == "not_waiting"
    assert h.effects() == 0


def test_the_whole_life_of_a_waiting_action_is_capped_whatever_re_arms_it(host):
    h = host
    sid = h.start()
    clock = h.r.clock
    ask = requested(h, sid, "t1")
    for n in range(5):                                                          # read back again and again
        clock[0] += 25
        assert utter(h, sid, f"t{3 + 2 * n}", "read it back").get_json()["decision"] in ("read_back", "nothing_waiting")
        asked(h, sid, ask["action_id"], f"t{4 + 2 * n}")
    assert utter(h, sid, "t20", "yes").get_json()["decision"] != "confirm" and h.effects() == 0


# ── a yes before the question, for a replaced action, with nothing waiting ──────────────────────────────────────────────

def test_a_yes_before_the_question_started_does_nothing_and_it_is_asked_again(host):
    h = host
    sid = h.start()
    ask = utter(h, sid, "t1", REQUEST[h.kind]).get_json()
    early = utter(h, sid, "t2", "yes").get_json()                               # no question yet
    assert early["decision"] == "ask" and early["again"] is True and early["action_id"] == ask["action_id"]
    asked(h, sid, ask["action_id"], "t3")
    over = utter(h, sid, "t3", "yes").get_json()                                # said over the question's start
    assert over["decision"] == "ask" and h.effects() == 0
    yes = utter(h, sid, "t5", "yes").get_json()
    assert yes["decision"] == "confirm"
    assert confirm(h, sid, yes).get_json()["outcome"] == "done" and h.effects() == 1


def test_a_yes_with_nothing_waiting_never_reaches_the_person(host):
    h = host
    sid = h.start()
    for n, words in enumerate(("Yes.", "okay", "sure, go ahead")):
        assert utter(h, sid, f"t{n * 2}", words).get_json()["decision"] == "chat"
    assert h.effects() == 0
    refused = tap(h, sid, "t4", "sure, go ahead").get_json()                    # a tap on a bare yes (v3 addition 3)
    assert refused["outcome"] == "not_done" and refused["spoken"] == core.NOTHING_WAITING and h.effects() == 0


# ── one control owner: the action and "Shall I sum up?" ─────────────────────────────────────────────────────────────────

def test_an_action_yes_never_ends_the_session_and_the_ends_yes_never_acts(host):
    h = host
    sid = h.start()
    requested(h, sid, "t1")
    yes = utter(h, sid, "t3", "yes").get_json()
    assert yes["decision"] == "confirm" and yes["kind"] != "end" and yes["proposing"] is False
    end = utter(h, sid, "t5", "Okay, we're done.").get_json()
    assert end["decision"] == "ask" and end["kind"] == "end" and end["prompt"] == core.SUMMARY_PROMPT
    assert end["proposing"] is True
    asked(h, sid, end["action_id"], "t6")
    sum_up = utter(h, sid, "t7", "yes please").get_json()
    assert sum_up["decision"] == "confirm" and sum_up["kind"] == "end"
    assert h.effects() == 0                                                     # the end's yes acts on nothing
    refused = h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": "confirm-end", "action_id": sum_up["action_id"], "confirm_item_id": "t7",
        "owner_item_id": "t5", "text_sha256": "x"}).get_json()
    assert refused["outcome"] == "not_done" and h.effects() == 0                # the end is never an action


def test_a_request_replaces_the_end_and_its_yes_acts_without_ending(host):
    h = host
    sid = h.start()
    end = utter(h, sid, "t1", "We're done.").get_json()
    assert end["kind"] == "end"
    ask = requested(h, sid, "t3")
    assert ask["replaced"] == end["action_id"] and ask["proposing"] is False
    yes = utter(h, sid, "t5", "yes").get_json()
    assert yes["kind"] != "end" and yes["proposing"] is False


def test_keep_talking_withdraws_the_end_and_a_later_yes_ends_nothing(host):
    h = host
    sid = h.start()
    end = utter(h, sid, "t0", "We're done.").get_json()
    asked(h, sid, end["action_id"], "t1")
    assert h.client.post(h.base + f"/session/{sid}/keep_talking", headers=h.headers, json={}).status_code == 200
    assert utter(h, sid, "t2", "Yes.").get_json()["decision"] == "chat"


# ── read-back ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_read_back_reads_his_words_pauses_the_window_and_a_yes_after_its_question_acts_once(host):
    h = host
    sid = h.start()
    clock = h.r.clock
    ask = requested(h, sid, "t1")
    clock[0] += 20
    back = utter(h, sid, "t3", "Read it back, please.").get_json()
    assert back["decision"] == "read_back" and back["text"] == REQUEST[h.kind] and back["action_id"] == ask["action_id"]
    clock[0] += 40                                                              # a long read-back: the window waits
    assert asked(h, sid, ask["action_id"], "t4").get_json()["expires_in"] == core.ANSWER_S
    yes = utter(h, sid, "t5", "ja, mach das").get_json()
    assert yes["decision"] == "confirm" and confirm(h, sid, yes).get_json()["outcome"] == "done" and h.effects() == 1


def test_read_back_after_expiry_reads_nothing(host):
    h = host
    sid = h.start()
    requested(h, sid, "t1")
    h.r.clock[0] += core.ANSWER_S + 1
    late = utter(h, sid, "t3", "lies es vor").get_json()
    assert late["decision"] == "nothing_waiting" and "text" not in late


# ── Check's tap converges on the waiting action ─────────────────────────────────────────────────────────────────────────

def test_a_tap_confirms_the_waiting_action_through_the_same_consumption(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    done = tap(h, sid, "t3", "yes", action_id=ask["action_id"]).get_json()     # his last words are "yes"; the tap names it
    assert done["outcome"] == "done" and done["owner_item_id"] == "t1" and h.effects() == 1
    assert utter(h, sid, "t3", "yes").get_json()["decision"] == "chat"         # consumed: nothing waits now
    assert tap(h, sid, "t3", "yes", action_id=ask["action_id"], call_id="host-2").get_json().get("same_turn") is True
    assert h.effects() == 1


def test_concurrent_check_and_yes_make_one_effect(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    yes = utter(h, sid, "t3", "yes").get_json()
    answers = []
    first = threading.Thread(target=lambda: answers.append(tap(h, sid, "t3", "yes", action_id=ask["action_id"]).get_json()))
    second = threading.Thread(target=lambda: answers.append(confirm(h, sid, yes).get_json()))
    first.start(), second.start()
    first.join(10), second.join(10)
    assert len(answers) == 2 and h.effects() == 1
    assert {a["owner_item_id"] for a in answers} == {"t1"}


def test_a_tap_with_nothing_waiting_acts_on_his_last_words_as_before(host):
    h = host
    sid = h.start()
    done = tap(h, sid, "t1", "What is stuck on #12?").get_json()
    assert done["outcome"] == "done" and h.effects() == 1


# ── the fold (xAI, LV6) ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_folded_request_plus_yes_makes_no_effect_and_no_note_holding_yes(host, monkeypatch):
    h = host
    if h.kind == "test adapter":
        from test_direct_binding import boot, xai_mint
        xai_mint(monkeypatch, [])
        sid = boot(h.r, "xai").get_json()["voice_session_id"]
    elif h.kind == "CoS":
        import cos_voice_rig as cos
        sid = cos.start(h.r, provider="xai")
    else:
        pytest.skip("MC's synthetic mount starts OpenAI only; the core's fold rule is the same for every host")
    ask = utter(h, sid, "t1", REQUEST[h.kind]).get_json()
    asked(h, sid, ask["action_id"], "t2")
    folded = utter(h, sid, "t3", REQUEST[h.kind] + ". Yes.").get_json()       # the provider's fold of t1 into t3
    assert folded["decision"] == "dropped" and folded["reason"] == "folded" and h.effects() == 0
    assert utter(h, sid, "t5", "yes").get_json()["decision"] == "nothing_waiting" and h.effects() == 0


# ── no model action ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_models_call_runs_nothing_on_the_action_path(host):
    h = host
    sid = h.start()
    for body in ({"call_id": "call_model", "name": "consult"}, {"call_id": "call_model2", "name": "save_note"},
                 {"call_id": "call_model3", "name": "consult", "action_id": "a" * 32}):
        refused = h.client.post(h.base + "/consult", headers=h.headers, json={
            "session_id": sid, "owner_transcript_item": REQUEST[h.kind], "owner_item_id": "t1", **body}).get_json()
        assert refused["outcome"] == "not_done" and refused["error"] == "not_confirmed", refused
    assert h.effects() == 0


# ── End, a reconnect, capture full ──────────────────────────────────────────────────────────────────────────────────────

def test_end_drops_the_waiting_action_and_a_confirmed_one_not_yet_consumed(host):
    h = host
    sid = h.start()
    requested(h, sid, "t1")
    yes = utter(h, sid, "t3", "yes").get_json()
    h.client.post(h.base + f"/session/{sid}/end", headers=h.headers, json={"conversation_id": h.cid})
    assert confirm(h, sid, yes).get_json()["outcome"] == "not_done" and h.effects() == 0
    assert utter(h, sid, "t5", "yes").status_code == 409


def test_capture_full_drops_the_waiting_action(host, monkeypatch):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    monkeypatch.setattr(core, "SESSION_MAX_EVENTS", 0)
    assert post(h, sid, [h.event("user", "t3", "more words")]).status_code == 429
    assert asked(h, sid, ask["action_id"], "t4").status_code == 409
    monkeypatch.setattr(core, "SESSION_MAX_EVENTS", 600)
    assert utter(h, sid, "t5", "yes").get_json()["decision"] != "confirm" and h.effects() == 0


# ── the routes' own checks ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_utterance_route_checks_owner_session_conversation_and_shape(host):
    h = host
    sid = h.start()
    for body in ({"conversation_id": "elsewhere", "owner_item_id": "t1", "text": "x"},
                 {"conversation_id": h.cid, "owner_item_id": "u1", "text": "x"},
                 {"conversation_id": h.cid, "owner_item_id": "t1", "text": "  "},
                 {"conversation_id": h.cid, "owner_item_id": "t1", "text": "x", "instructions": "act"}):
        assert h.client.post(h.base + f"/session/{sid}/utterance", headers=h.headers, json=body).status_code == 422
    assert h.client.post(h.base + "/session/not-a-session/utterance", headers=h.headers,
                         json={"conversation_id": h.cid, "owner_item_id": "t1", "text": "x"}).status_code == 403
    assert asked(h, sid, "a" * 32, "t1").status_code == 409                     # no such action
    assert asked(h, sid, "a" * 32, "u1").status_code == 422


def test_an_older_item_after_a_newer_one_changes_nothing(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t5")
    stale = utter(h, sid, "t3", REQUEST[h.kind]).get_json()
    assert stale["decision"] == "chat" and stale["stale"] is True
    assert utter(h, sid, "t7", "yes").get_json()["action_id"] == ask["action_id"]


# ── the grammar fast path (no model call) ───────────────────────────────────────────────────────────────────────────────

def test_bare_answers_split_yes_from_no_and_anything_more_is_not_bare():
    for words in ("yes", "Yes, go ahead.", "yeah sure", "ja bitte", "ja, mach das", "sim, pode", "claro", "ok", "do it"):
        assert core.bare_answer(words) == "yes", words
    for words in ("no", "No, wait.", "nope", "nein", "não", "cancel", "yes, no, wait", "stop"):
        assert core.bare_answer(words) == "no", words
    for words in ("yes, and also add the glaze", "yes save it to the board", "make a note", "", "thanks", 7):
        assert core.bare_answer(words) is None, words


def test_read_back_is_a_closed_set_in_three_languages():
    for words in ("Read it back", "read it back to me, please", "What did I say?", "lies es vor", "Lies es mir vor, bitte",
                  "lê de novo", "Leia de novo, por favor", "read me the summary", "o que eu disse"):
        assert core.read_back_request(words), words
    for words in ("read the news", "read it back and save it", "lies das Buch", "x" * 300):
        assert not core.read_back_request(words), words


def test_the_explicit_consult_names_this_hosts_own_person_only():
    mc = core.consult_grammar({"short": "MC", "name": "Master Craftsman"})
    for words in ("Ask MC what is stuck on 12", "check with MC whether the kiln is hot", "Frag MC, ob das läuft",
                  "frag mal den MC was offen ist", "Pergunte ao MC o que falta", "please ask Master Craftsman about 12"):
        assert core.explicit_consult(mc, words), words
    for words in ("ask CoS what is stuck", "Ask MC", "ask MC?", "MC, what is stuck?", "I asked MC yesterday"):
        assert not core.explicit_consult(mc, words), words
    cos = core.consult_grammar({"short": "CoS", "name": "Chief of Staff"})
    assert core.explicit_consult(cos, "pergunte à CoS o que falta") and not core.explicit_consult(cos, "ask MC about it")


def test_the_new_grammar_runs_in_linear_time_on_crafted_input():
    mc = core.consult_grammar({"short": "MC", "name": "Master Craftsman"})
    for crafted in ("ask " + " " * 20000 + "x", "ask MC" + "!" * 20000, " " * 20000 + "ask the the MC", "please " * 3000,
                    "ask MC " + "a " * 20000):
        began = time.perf_counter()
        core.explicit_consult(mc, crafted[:core.TURN_MAX_BYTES])
        core.read_back_request(crafted)
        core.bare_answer(crafted)
        assert time.perf_counter() - began < 0.5, crafted[:20]


def test_fast_label_order_with_and_without_something_waiting():
    class A(core.DirectVoiceAdapter):
        person = {"key": "x", "name": "Master Craftsman", "short": "MC", "domain": "mc", "notes": ("make a note that …",)}

        def explicit_request(self, text):
            return "note" if text.lower().startswith("make a note") else None
    a, g = A(), core.consult_grammar({"short": "MC", "name": "Master Craftsman"})
    assert core.fast_label(a, g, "make a note that x", None) == "note"
    assert core.fast_label(a, g, "ask MC about x", "note") == "consult"
    assert core.fast_label(a, g, "yes", "note") == "yes" and core.fast_label(a, g, "no", "consult") == "no"
    assert core.fast_label(a, g, "read it back", "note") == "read_back"
    assert core.fast_label(a, g, "yes, sum it up", "end") == "yes"            # today's spoken end, kept
    assert core.fast_label(a, g, "we're done", "note") == "end"
    assert core.fast_label(a, g, "could you jot down that x", None) is None    # the understanding call's
    assert re.fullmatch(r"[0-9a-f]{64}", core.digest("x"))
