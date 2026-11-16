"""#652 (Robert, 16 Nov 2026, CoS by phone): his status question ran as a lookup; CoS's typed answer ended "Want me to ask him
whether last night's deploy finished?"; the app held nothing, so his "Okay, go ahead, put it there…" was chat, and the voice
said "Got it, I'll handle that handoff for you" with nothing sent. Now an answer that ends by offering to do something
waits as an offer once the voice has said it: his yes runs as his own typed turn on HIS words (never the answer's), not
read-only; a no or other words drop it; nothing said before the offer is said touches it. And the voice is told never to
say it sent or will send anything. Run on the test adapter and on Chief of Staff's real adapter (test_direct_contract's
driver); the page's half is tests/realtime_voice/test_voice_653_check_once.mjs."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cos"))

from core.realtime_voice import direct_host as core  # noqa: E402
from test_direct_contract import driver, post  # noqa: E402
from test_direct_host import rig  # noqa: E402,F401  (the test adapter's fixture, for the driver)

QUESTION = "Did last night's deployment release to production?"
OFFER = ("I can't confirm a production release from here. That sits with Master Craftsman. Want me to ask him whether "
         "last night's deploy finished?")
OFFER_SAID = "About what you asked earlier: " + OFFER
YES = "Okay, go ahead, put it there. I'll just wait, let's see if he replies."
PHANTOM = "Die Reise war ein voller Erfolg, und wir konnten alle Sehenswürdigkeiten besuchen."


@pytest.fixture(params=("test adapter", "CoS"))
def h(request, monkeypatch, tmp_path):
    h = driver(request.param, request, monkeypatch, tmp_path)
    h.ran = []                                       # (words, read_only) of every typed turn, whichever host
    if h.kind == "test adapter":
        host = h.r.host

        def typed_turn(handle, principal, sid, n, name, text, *, item, read_only=False):
            h.ran.append((text, read_only))
            host.turns.append((handle, principal, sid, n, name, text))
            return {"answered": True, "receipt": f"turn-{n}", "receipt_kind": "reply", "spoken": h.answer}
        host.typed_turn = typed_turn
    else:
        from domains.cos import direct_voice
        real = direct_voice.ChiefOfStaff.typed_turn

        def typed_turn(self, handle, principal, sid, n, name, text, *, item, read_only=False):
            h.ran.append((text, read_only))
            h.r.runtime.reply = h.answer
            return real(self, handle, principal, sid, n, name, text, item=item, read_only=read_only)
        monkeypatch.setattr(direct_voice.ChiefOfStaff, "typed_turn", typed_turn)
    h.answer = OFFER
    h.r.gateway.intent = lambda body: next((word for words, word in h.labels.items()
                                             if f"<<<{words}>>>" in body["messages"][1]["content"]), "chat")
    h.labels = {QUESTION: "lookup", YES: "yes"}
    return h


def utter(h, sid, item, text):
    return h.client.post(h.base + f"/session/{sid}/utterance", headers=h.headers,
                         json={"conversation_id": h.cid, "owner_item_id": item, "text": text}).get_json()


def act(h, sid, decision):
    return h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": "confirm-" + decision["action_id"] + decision["confirm_item_id"],
        "action_id": decision["action_id"], "confirm_item_id": decision["confirm_item_id"],
        "owner_item_id": decision["owner_item_id"], "text_sha256": decision["text_sha256"]}).get_json()


def offered(h):
    """A session where his question ran as a lookup (t0) and its answer, the offer, has come back: (sid, the answer)."""
    sid = h.start()
    lookup = utter(h, sid, "t0", QUESTION)
    assert lookup["decision"] == "confirm" and lookup.get("lookup") is True, lookup
    answer = act(h, sid, lookup)
    assert answer["outcome"] == "done" and answer["spoken"] == OFFER, answer
    assert h.ran == [(QUESTION, True)]
    return sid, answer


# ── the offer, read from the answer ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spoken, question", [
    (OFFER, "Want me to ask him whether last night's deploy finished?"),
    ("Two items are stuck. Shall I close the older one?", "Shall I close the older one?"),
    ("Do you want me to send it to Ana?", "Do you want me to send it to Ana?"),
    ("Would you like me to make a card for it?", "Would you like me to make a card for it?"),
    ("Soll ich MC fragen?", "Soll ich MC fragen?"),
    ("Done. So, want me to tell MC?", "So, want me to tell MC?"),
])
def test_an_answer_that_ends_by_offering_something_is_an_offer(spoken, question):
    assert core.offer_of(spoken) == question


@pytest.mark.parametrize("spoken", [
    "Two items are stuck on #12.", "Want me to check? Never mind, it is done.", "What should I call it?",
    "Shall I sum up? The rest is in the chat.", "", None, "Want me to " + "x" * 500 + "?",
])
def test_an_answer_that_offers_nothing_at_its_end_is_no_offer(spoken):
    assert core.offer_of(spoken) is None


def test_the_voice_said_the_offer_when_its_turn_asks_and_holds_most_of_its_words():
    question = core.offer_of(OFFER)
    assert core.said_offer(OFFER_SAID, question)
    assert core.said_offer("About what you asked earlier: want me to ask him if last night's deploy is finished?", question)
    assert not core.said_offer("Let me check with Master Craftsman.", question)            # no question in it
    assert not core.said_offer("Anything else on your mind?", question)                     # a question, not the offer


# ── his yes to it runs his own words, not read-only ────────────────────────────────────────────────────────────────────

def test_his_yes_to_a_said_offer_runs_his_own_words_as_a_confirmed_request_never_read_only(h):
    sid, _answer = offered(h)
    assert post(h, sid, [h.event("assistant", "t1", "Let me check that through the app.")]).status_code == 200
    # #651's phantom line before the offer was said: heard (chat), and it neither answers nor drops the offer.
    assert utter(h, sid, "t2", PHANTOM)["decision"] == "chat"
    assert post(h, sid, [h.event("assistant", "t3", OFFER_SAID)]).status_code == 200
    h.answer = "Sent to MC. His answer will come back as a note in this chat."
    yes = utter(h, sid, "t4", YES)
    assert yes["decision"] == "confirm" and yes["offer"] is True and yes["kind"] == "consult", yes
    assert yes["owner_item_id"] == "t4" and yes["confirm_item_id"] == "t4"
    pending = [q for q in h.r.gateway.intents if f"<<<{YES}>>>" in q["messages"][1]["content"]][-1]["messages"][1]["content"]
    assert "Waiting for his answer: a request to" in pending and "Want me to ask him whether" in pending   # data, the offer
    done = act(h, sid, yes)
    assert done["outcome"] == "done" and done["owner_item_id"] == "t4", done
    assert h.ran[-1] == (YES, False)                                    # his words, never the answer's, and may act
    assert all(OFFER not in words for words, _ro in h.ran)
    again = act(h, sid, yes)                                            # the page's retried confirm
    assert again.get("receipt") == done.get("receipt") and len(h.ran) == 2   # once, however often the page asks


def test_a_bare_yes_answers_the_offer_and_runs_once(h):
    """D16 refuses a consult whose words are only a yes; here the yes is the confirmation of a waiting offer, so his
    "Yes." itself is what answers the offer in the typed turn's thread."""
    sid, _answer = offered(h)
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])
    yes = utter(h, sid, "t2", "Yes.")
    assert yes["decision"] == "confirm" and yes["offer"] is True, yes
    assert act(h, sid, yes)["outcome"] == "done" and h.ran[-1] == ("Yes.", False)


def test_no_or_other_words_drop_the_offer_and_a_later_yes_runs_nothing(h):
    sid, _answer = offered(h)
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])
    no = utter(h, sid, "t2", "No.")
    assert no["decision"] == "dropped" and no["reason"] == "no" and no["kind"] == "offer"
    assert utter(h, sid, "t4", "Yes.")["decision"] in ("chat", "nothing_waiting") and len(h.ran) == 1

    sid, _answer = offered_again(h)
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])
    other = utter(h, sid, "t2", "Tell me about kilns.")
    assert other["decision"] == "chat" and other["dropped_kind"] == "offer"
    assert utter(h, sid, "t4", "Yes.")["decision"] == "nothing_waiting" and len(h.ran) == 1


def offered_again(h):
    h.ran.clear()
    return offered(h)


def test_a_yes_before_the_offer_was_said_is_not_its_yes(h):
    sid, _answer = offered(h)
    yes = utter(h, sid, "t2", "Yes.")                                   # nothing said yet: a plain yes, never the offer's
    assert yes["decision"] in ("chat", "nothing_waiting") and len(h.ran) == 1
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])              # said, but before his t2: t2 is not its answer
    assert len(h.ran) == 1


def test_his_own_request_comes_before_an_offer_and_check_never_sends_the_offers_words(h):
    sid, _answer = offered(h)
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])
    h.labels["Close item 12."] = "consult"
    ask = utter(h, sid, "t2", "Close item 12.")
    assert ask["decision"] == "ask" and "replaced" not in ask           # the page never showed the offer
    # Check, tapped on the lookup's own words: never the offer's words; it is a lookup already checked.
    tap = h.client.post(h.base + "/consult", headers=h.headers, json={
        "session_id": sid, "call_id": "host-1", "name": "consult", "owner_transcript_item": QUESTION,
        "owner_item_id": "t0"}).get_json()
    assert all(OFFER not in words for words, _ro in h.ran), tap


def test_an_answer_while_a_request_waits_is_no_offer(h):
    sid = h.start()
    h.labels["Close item 12."] = "consult"
    ask = utter(h, sid, "t0", "Close item 12.")
    assert ask["decision"] == "ask"
    lookup = utter(h, sid, "t1", QUESTION)                               # a lookup leaves the waiting request alone
    assert act(h, sid, lookup)["spoken"] == OFFER
    post(h, sid, [h.event("assistant", "t2", "Shall I go ahead?"), h.event("assistant", "t3", OFFER_SAID)])
    h.client.post(h.base + f"/session/{sid}/utterance/{ask['action_id']}/asked", headers=h.headers,
                  json={"after_item": "t3"})
    yes = utter(h, sid, "t4", "Yes.")
    assert yes["decision"] == "confirm" and yes["action_id"] == ask["action_id"] and yes["owner_item_id"] == "t0"
    assert "offer" not in yes


def test_an_offer_waits_its_lifetime_only(h):
    sid, _answer = offered(h)
    h.r.clock[0] += core.LIFETIME_S + 1
    post(h, sid, [h.event("assistant", "t1", OFFER_SAID)])
    assert utter(h, sid, "t2", "Yes.")["decision"] in ("chat", "nothing_waiting") and len(h.ran) == 1


# ── the voice never claims a send ─────────────────────────────────────────────────────────────────────────────────────

def test_the_voice_is_told_never_to_claim_a_send_or_promise_one():
    from domains.cos import direct_voice
    rules = core.direct_rules({"name": "Master Craftsman", "short": "MC"}, "Robert")
    assert "never claim anything was checked, saved or done otherwise" in rules
    assert "Never say you sent, passed on or handed off anything, or that you will" in rules
    hers = direct_voice.STATUS_TO_MC + direct_voice.her_rules()
    assert "Never say you sent, passed or handed anything to Master Craftsman, or that you will" in hers
    assert "or that anything was sent or handed off, unless the app says so" in hers
    assert "or says yes when an answer offers to ask him" in hers


def test_the_voice_says_one_check_line_a_question():
    """#653: the shared rules and hers say the check line once a question, and a garbled line gets a request to repeat."""
    from domains.cos import direct_voice
    rules = core.direct_rules({"name": "Chief of Staff", "short": "CoS"}, "Robert", platform_status=direct_voice.STATUS_TO_MC)
    assert "That one line is all for a question, however long its check takes and whatever is said meanwhile" in rules
    assert "If his words are unclear or make no sense here, ask him briefly to say them again" in rules
    assert 'Say "Let me check with Master Craftsman" once for such a question, and never again for it' in rules


def test_the_page_says_one_check_line_and_an_answer_in_hand_replaces_its_pending_line():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run([node, "tests/realtime_voice/test_voice_653_check_once.mjs"],
                          cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
