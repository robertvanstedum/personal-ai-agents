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
                  "lê de novo", "Leia de novo, por favor", "o que eu disse"):
        assert core.read_back_request(words), words
    # Review v3 S2 (adapted): asking for the summary is its own closed set, never a read-back of the waiting action.
    for words in ("read the news", "read it back and save it", "lies das Buch", "x" * 300, "read me the summary",
                  "Read the summary, please", "lies mir die Zusammenfassung vor", "lê o resumo", "leia o resumo"):
        assert not core.read_back_request(words), words
    for words in ("read me the summary", "Read the summary, please", "lies mir die Zusammenfassung vor", "lê o resumo",
                  "Leia o resumo, por favor"):
        assert core.read_summary_request(words), words
    assert not core.read_summary_request("read me the summary of the kiln note") and not core.read_summary_request(7)


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


# ── the fix round (REVIEW_v3_build.md): each finding's regression, on every host ────────────────────────────────────────

NATURAL = "Could you jot down that the glaze needs a second firing?"      # no host's grammar hears it: the call does
TYPED_ANSWER = {"test adapter": "Two items.", "MC": "Item 12 is in build.", "CoS": "Two items are stuck on 12."}
OWNER_NAMES = {"test adapter": "Rosa", "MC": "Robert", "CoS": "Robert"}


def gateway_of(h):
    """The host's fake model gateway, which the understanding call reaches too (``intent``: its answer, ``intents``)."""
    return h.r.cos if h.kind == "MC" else h.r.gateway


def hears(h, mapping):
    """The fake understanding call answers ``mapping[words]`` for these words, chat for any other."""
    gateway_of(h).intent = lambda body: next((word for words, word in mapping.items()
                                               if f"<<<{words}>>>" in body["messages"][1]["content"]), "chat")


@pytest.mark.parametrize("label", ["note", "postit"])
def test_m1_a_request_only_the_call_heard_waits_as_a_request_and_its_outcome_is_the_typed_turns_own(host, label):
    """M1: the call names a note or post-it the grammar did not; it runs as his typed turn (consult), so it waits as a
    request (kind consult, heard as the label), and its answer is the typed turn's own, with no saved flag: the page
    says that answer, never "Saved." or "Post-it added." (test_confirm_by_voice.mjs's M1 and S8 cases: the page's half)."""
    h = host
    sid = h.start()
    hears(h, {NATURAL: label})
    ask = utter(h, sid, "t1", NATURAL).get_json()
    heard = label if label == "note" or h.kind == "MC" else "consult"        # a post-it with no Board is a request
    assert ask["decision"] == "ask" and ask["kind"] == "consult" and ask["heard"] == heard, ask
    asked(h, sid, ask["action_id"], "t2")
    yes = utter(h, sid, "t3", "yes").get_json()
    assert yes["decision"] == "confirm" and yes["kind"] == "consult"
    done = confirm(h, sid, yes).get_json()
    assert done["outcome"] == "done" and done["kind"] == "consult" and done["owner_item_id"] == "t1"
    assert done.get("saved") is not True and done["spoken"] == TYPED_ANSWER[h.kind], done
    assert h.effects() == 1


def test_m1_n5_n6_the_call_names_the_hosts_owner_and_offers_postit_only_where_there_is_a_board(host):
    h = host
    sid = h.start()
    utter(h, sid, "t1", NATURAL)
    (body,) = gateway_of(h).intents
    system = body["messages"][0]["content"]
    assert f"one thing {OWNER_NAMES[h.kind]} just said" in system and body["max_tokens"] == 16
    if h.kind != "test adapter":
        assert "Rosa" not in system
    else:
        assert "Robert" not in system                    # N5: the core names no person of its own
    offered = ("postit" in system.split("\n", 1)[0], "postit: he asks" in system)
    assert offered == ((True, True) if h.kind == "MC" else (False, False))


def test_s1_a_question_shown_on_screen_only_is_tap_only(host):
    """S1 (Codex's line, no blind yes): the question could not be spoken, so the screen asks; a spoken yes then asks it
    again and acts on nothing; Check confirms it. A spoken question later makes it confirmable by voice again."""
    h = host
    sid = h.start()
    ask = utter(h, sid, "t1", REQUEST[h.kind]).get_json()
    shown = h.client.post(h.base + f"/session/{sid}/utterance/{ask['action_id']}/asked", headers=h.headers,
                          json={"after_item": "t1", "screen": True}).get_json()
    assert shown["state"] == "asked" and shown["tap_only"] is True
    blind = utter(h, sid, "t3", "yes").get_json()
    assert blind["decision"] == "ask" and blind["again"] is True and blind["tap_only"] is True and h.effects() == 0
    asked(h, sid, ask["action_id"], "t4")                                      # the question spoken after all
    yes = utter(h, sid, "t5", "yes").get_json()
    assert yes["decision"] == "confirm" and confirm(h, sid, yes).get_json()["outcome"] == "done" and h.effects() == 1
    other = utter(h, sid, "t7", CONSULT[h.kind]).get_json()
    h.client.post(h.base + f"/session/{sid}/utterance/{other['action_id']}/asked", headers=h.headers,
                  json={"after_item": "t7", "screen": True})
    assert tap(h, sid, "t7", CONSULT[h.kind], action_id=other["action_id"]).get_json()["outcome"] == "done"
    assert h.effects() == 2
    bad = h.client.post(h.base + f"/session/{sid}/utterance/{'a' * 32}/asked", headers=h.headers,
                        json={"after_item": "t7", "screen": "yes"})
    assert bad.status_code == 422


def test_s2_asking_for_the_summary_mid_session_never_reads_the_waiting_actions_words(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    later = utter(h, sid, "t3", "Read me the summary").get_json()
    assert later["decision"] == "summary_later" and "text" not in later and REQUEST[h.kind] not in str(later)
    assert utter(h, sid, "t5", "yes").get_json()["action_id"] == ask["action_id"]     # it still waits, unread
    assert utter(h, sid, "t7", "lê o resumo").get_json()["decision"] == "summary_later"   # nothing waiting: the same


def test_s3_a_second_yes_after_a_lost_answer_confirms_once_with_the_actions_own_item_and_hash(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    utter(h, sid, "t3", "yes").get_json()                                       # its answer never reached the page
    again = utter(h, sid, "t5", "yes").get_json()
    assert again["decision"] == "confirm" and again["again"] is True and again["confirm_item_id"] == "t3"
    assert again["owner_item_id"] == "t1" and again["text_sha256"] == ask["text_sha256"]
    first = confirm(h, sid, again).get_json()
    assert first["outcome"] == "done" and h.effects() == 1
    assert confirm(h, sid, again).get_json() == first and h.effects() == 1    # the page's retry: the same receipt


def test_s4_a_tap_on_the_ends_own_words_is_never_a_consult(host):
    h = host
    sid = h.start()
    end = utter(h, sid, "t1", "We're done.").get_json()
    assert end["kind"] == "end"
    refused = tap(h, sid, "t1", "We're done.").get_json()
    assert refused["outcome"] == "not_done" and refused["error"] == "end_waiting" and h.effects() == 0
    assert refused["spoken"] == core.END_WAITING and "done." not in refused["spoken"].lower().split("end")[0]


def test_s6_a_bare_yes_right_after_the_voice_asked_a_question_is_told_nothing_waits(host):
    """S6: the app heard no request (chat), the voice asked anyway ("Shall I go ahead?"): his yes reaches nothing, and is
    told so. After a reply that asked nothing, or with his own words between, a yes is conversation as before."""
    h = host
    sid = h.start()
    assert utter(h, sid, "t0", "Could you check what is stuck on the board?").get_json()["decision"] == "chat"
    assert post(h, sid, [h.event("assistant", "t1", "Shall I go ahead?")]).status_code == 200
    stray = utter(h, sid, "t2", "Yes.").get_json()
    assert stray["decision"] == "nothing_waiting" and h.effects() == 0
    post(h, sid, [h.event("assistant", "t3", "It is in build.")])
    assert utter(h, sid, "t4", "yes").get_json()["decision"] == "chat"
    post(h, sid, [h.event("assistant", "t5", "Anything else?")])
    utter(h, sid, "t6", "Tell me about kilns.")
    assert utter(h, sid, "t7", "okay").get_json()["decision"] == "chat" and h.effects() == 0


def test_s7_a_request_ending_in_a_bare_answer_is_refused_whole_on_any_provider(host):
    h = host
    sid = h.start()
    folded = utter(h, sid, "t1", REQUEST[h.kind] + ". Yes.").get_json()
    assert folded["decision"] == "dropped" and folded["reason"] == "folded" and "action_id" not in folded
    assert utter(h, sid, "t3", "yes").get_json()["decision"] == "nothing_waiting" and h.effects() == 0
    ask = requested(h, sid, "t5")
    again = utter(h, sid, "t7", CONSULT[h.kind] + ", yes").get_json()        # a waiting action goes too
    assert again == {**again, "decision": "dropped", "reason": "folded", "action_id": ask["action_id"]}
    assert utter(h, sid, "t9", "yes").get_json()["decision"] == "nothing_waiting" and h.effects() == 0
    polite = utter(h, sid, "t11", CONSULT[h.kind] + ", please.").get_json()
    tag = utter(h, sid, "t13", CONSULT[h.kind] + ", okay?").get_json()
    assert polite["decision"] == "ask" and tag["decision"] == "ask"


def test_s7_ends_in_answer_is_a_closed_trailing_clause_in_linear_time():
    for words in ("make a note that the kiln is hot. Yes.", "ask MC about 12, yes", "Notiere: der Ofen ist heiß. Ja.",
                  "anota que o forno está quente. Sim.", "save a note: kiln. No, wait.", "add a post-it: x; okay"):
        assert core.ends_in_answer(words), words
    for words in ("make a note that the answer is yes", "ask MC about 12, please", "ask MC about 12, thanks",
                  "ask MC about 12, okay?", "Yes. Make a note that the kiln is hot.", "yes", "", None, "a, " * 9000):
        assert not core.ends_in_answer(words), words
    began = time.perf_counter()
    core.ends_in_answer(("x, " * 5000)[:core.TURN_MAX_BYTES] + "yes")
    assert time.perf_counter() - began < 0.5


def test_n1_a_request_after_the_yes_word_never_confirms_the_end(host):
    h = host
    sid = h.start()
    hears(h, {"please make a note that the kiln is hot": "note", "sure, ask " + h.short + " what is stuck on 12": "consult"})
    for n, words in enumerate(("please make a note that the kiln is hot", "sure, ask " + h.short + " what is stuck on 12")):
        end = utter(h, sid, f"t{4 * n}", "We're done.").get_json()
        asked(h, sid, end["action_id"], f"t{4 * n + 1}")
        heard = utter(h, sid, f"t{4 * n + 2}", words).get_json()
        assert heard["decision"] == "ask" and heard["kind"] != "end" and heard["replaced"] == end["action_id"], heard
        assert heard["proposing"] is False and h.effects() == 0
    end = utter(h, sid, "t9", "We're done.").get_json()
    asked(h, sid, end["action_id"], "t10")
    assert utter(h, sid, "t11", "Yes please, go ahead and sum it up.").get_json()["decision"] == "confirm"


def test_n1_end_yes_takes_more_confirmation_or_the_end_never_a_request():
    for words in ("yes", "yes, sum it up", "Sum it up please", "Yes please, go ahead and sum it up", "yes, we're done",
                  "okay", "ja, genau", "sure, wrap it up"):
        assert core.end_yes(words), words
    for words in ("please make a note that the kiln is hot", "sure, ask MC what is stuck", "yes, and could you jot down x",
                  "yes, but not yet", "ok ask CoS about it", "hello", None):
        assert not core.end_yes(words), words


def test_n2_yes_were_done_with_an_action_waiting_replaces_it_and_names_what_was_dropped(host):
    h = host
    sid = h.start()
    ask = requested(h, sid, "t1")
    end = utter(h, sid, "t3", "yes, we're done").get_json()
    assert end["decision"] == "ask" and end["kind"] == "end" and end["replaced"] == ask["action_id"]
    assert end["replaced_kind"] == ask["kind"] and h.effects() == 0


@pytest.mark.parametrize("provider", ["openai", "xai"])
def test_s6_where_the_page_asks_the_voice_never_asks_itself_and_where_it_answers_by_itself_it_asks(host, monkeypatch,
                                                                                                provider):
    """S6: on OpenAI the page creates every response, so its asking line is the question and the rules never have the
    model ask on its own (a yes to a question the app never asked would reach nothing); on xAI the model's own reply is
    the question, as before. Never "done", never his words, on both."""
    h = host
    minted = []

    def xai(**kw):
        minted.append(kw)
        return {"provider": "xai", "ephemeral_token": "xai_synthetic", "expires_after_seconds": 300,
                "model": "grok-voice-latest", "session_config": {}}
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", xai)
    answer = h.client.post(h.base + "/bootstrap", headers=h.headers,
                           json={"persona": h.persona, "provider": provider, "conversation_id": h.cid})
    assert answer.status_code == 200, answer.get_json()
    text = (minted[-1] if provider == "xai" else h.r.mints[-1])["instructions"]
    assert "without repeating or describing his words" in text and "never say it is done" in text
    if provider == "openai":
        assert "never ask him to confirm it yourself" in text and "Shall I go ahead?" not in text
        assert "ask Robert briefly to confirm" not in text                     # CoS's own rules too
    else:
        assert "never ask him to confirm it yourself" not in text and "Shall I go ahead?" in text
        if h.kind == "CoS":
            assert "ask Robert briefly to confirm" in text
