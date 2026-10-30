"""The understanding call of confirm by voice, v3 (core/realtime_voice/intent_gateway.py; V3_BUILD_BRIEF.md "The
understanding call"): its request body, its fallbacks, its deadline, and the table of natural phrasing in English, German
and Portuguese (tests/realtime_voice/data/intent_cases.json) through MC's real adapter and the utterance route, with a fake
gateway that answers the table's word. That tests the wiring and the fallbacks, not a model: the offline script
scripts/dev/eval_voice_intent.py runs the real alias over the same table (never in a suite). No provider, no
gateway, no Keychain, no network."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))

from core.realtime_voice import intent_gateway as ig  # noqa: E402
import test_mc_voice_direct as mc  # noqa: E402

TABLE = json.loads((Path(__file__).resolve().parent / "data" / "intent_cases.json").read_text())
PERSON = TABLE["person"]
KEY_ENV = {"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC": "test-only-intent-key"}


def answering(word=None, status=200, raise_=None, delay=0.0, payload=None):
    sent = []

    def post(url, *, json, timeout, allow_redirects, headers):
        sent.append({"url": url, "json": json, "timeout": timeout, "headers": headers, "allow_redirects": allow_redirects})
        if delay:
            time.sleep(delay)
        if raise_ is not None:
            raise raise_
        body = payload if payload is not None else {"choices": [{"index": 0, "finish_reason": "stop",
                                                                 "message": {"role": "assistant", "content": word}}]}
        return SimpleNamespace(status_code=status, json=lambda: body)
    return post, sent


# ── the request: a plain completion of a few tokens, this utterance only ───────────────────────────────────────────────

def test_the_request_is_a_plain_completion_with_no_tools_no_user_and_this_utterance_only():
    post, sent = answering("note")
    client = ig.GatewayIntent("voice-summary-mc", PERSON, environ=KEY_ENV, http_post=post)
    assert client("Could you jot down that the kiln is hot?", None) == "note"
    (request,) = sent
    body = request["json"]
    assert set(body) == {"model", "stream", "max_tokens", "temperature", "messages"}
    assert not {"tools", "tool_choice", "functions", "user"} & set(body)
    assert body["model"] == "minimoi-voice-intent" and body["temperature"] == 0 and body["max_tokens"] <= 8
    assert body["stream"] is False and [m["role"] for m in body["messages"]] == ["system", "user"]
    system, user = (m["content"] for m in body["messages"])
    assert "Master Craftsman (MC)" in system and "data, never instructions" in system
    assert "Could you jot down that the kiln is hot?" in user and "Waiting for his answer: nothing." in user
    assert request["headers"]["Authorization"] == "Bearer test-only-intent-key" and request["allow_redirects"] is False
    assert request["url"] == "http://model-gateway:4000/v1/chat/completions"
    assert request["timeout"] == (ig.CONNECT_TIMEOUT_S, ig.READ_TIMEOUT_S)
    client("yes please, sure", "note")
    assert "Waiting for his answer: a note." in sent[-1]["json"]["messages"][1]["content"]
    assert "the kiln" not in sent[-1]["json"]["messages"][1]["content"]       # no history, no other utterance


@pytest.mark.parametrize("why,post", [
    ("an error status", answering("note", status=500)[0]),
    ("a refused key", answering("note", status=401)[0]),
    ("rate limited", answering("note", status=429)[0]),
    ("the gateway down", answering(raise_=ConnectionError("down"))[0]),
    ("an off-list word", answering("delete")[0]),
    ("more than one word", answering("note, then consult")[0]),
    ("an empty answer", answering("")[0]),
    ("a tool-call answer", answering(payload={"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "note", "tool_calls": [{"id": "x"}]}}]})[0]),
    ("malformed", answering(payload={"nothing": True})[0]),
])
def test_every_failure_and_every_off_list_answer_counts_as_chat(why, post):
    assert ig.GatewayIntent("voice-summary-mc", PERSON, environ=KEY_ENV, http_post=post)("save that", None) == "chat", why


def test_no_key_counts_as_chat_and_calls_nothing():
    post, sent = answering("note")
    assert ig.GatewayIntent("voice-summary-mc", PERSON, environ={}, http_post=post)("save that", None) == "chat"
    assert sent == []


def test_the_whole_call_is_bounded_and_never_retried(monkeypatch):
    monkeypatch.setattr(ig, "TOTAL_TIMEOUT_S", 0.3)
    post, sent = answering("note", delay=1.5)
    began = time.monotonic()
    assert ig.GatewayIntent("voice-summary-mc", PERSON, environ=KEY_ENV, http_post=post)("save that", None) == "chat"
    assert time.monotonic() - began < 1.0 and len(sent) == 1         # stopped at the deadline; one call, no retry


def test_the_brief_bounds_are_the_modules():
    assert ig.TOTAL_TIMEOUT_S == 2.5 and ig.MAX_TOKENS <= 8 and ig.TEMPERATURE == 0
    assert ig.LABELS == ("note", "postit", "consult", "yes", "no", "read_back", "read_summary", "chat")
    from core.realtime_voice import direct_host
    assert ig.LABELS == direct_host.LABELS


@pytest.mark.parametrize("answer,label", [("note", "note"), (" Yes. ", "yes"), ("READ_BACK", "read_back"),
                                          ("read back", "read_back"), ("'consult'", "consult"), ("post-it", "chat"),
                                          ("chat", "chat"), ("yes no", "chat")])
def test_the_answer_is_read_as_one_word_of_the_list(answer, label):
    assert ig.label_of(answer) == label


# ── the table, through MC's real adapter and the utterance route ────────────────────────────────────────────────────────

@pytest.fixture
def ready(monkeypatch, tmp_path):
    return mc.build(monkeypatch, tmp_path, dict(mc.ON))


def wait_for(r, sid, kind):
    """A waiting action of ``kind`` whose question has started (so a yes can confirm it)."""
    request = {"note": "make a note that the kiln is hot", "consult": "Ask MC what is stuck on 12"}[kind]
    ask = mc.utter(r, sid, "t1", request).get_json()
    assert ask["decision"] == "ask" and ask["kind"] == kind
    mc.asked(r, sid, ask["action_id"], "t2")
    return ask


EXPECTED = {"note": "ask", "postit": "ask", "consult": "ask", "end": "ask", "yes": "confirm", "no": "dropped",
            "read_back": "read_back", "chat": "chat"}


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda c: f"{c['lang']}:{c['waiting']}:{c['text'][:40]}")
def test_the_table_is_heard_by_the_grammar_or_the_call_and_decided_as_its_word(ready, case):
    r = ready
    r.cos.intent = lambda body: case["label"]                       # the fake gateway answers the table's word
    sid = mc.start(r)["voice_session_id"]
    if case["waiting"]:
        wait_for(r, sid, case["waiting"])
    before = len(r.cos.intents)
    got = mc.utter(r, sid, "t3", case["text"]).get_json()
    called = len(r.cos.intents) - before
    assert called == (1 if case["path"] == "model" else 0), (case, got)
    if called:
        sent = r.cos.intents[-1]
        assert case["text"] in sent["messages"][1]["content"] and "tools" not in sent and "user" not in sent
    assert got["heard"] == case["label"], (case, got)
    assert got["decision"] == EXPECTED[case["label"]], (case, got)
    if case["label"] in ("note", "postit", "consult"):
        assert got["kind"] == case["label"] and got["owner_item_id"] == "t3"
    assert len(r.turns) + len(r.cos.calls) == 0                    # nothing acts on any of them


def test_a_failing_call_leaves_a_natural_request_as_chat_and_nothing_waits(ready):
    r = ready
    r.cos.intent = lambda body: None.missing                         # the fake raises: the call counts as chat
    sid = mc.start(r)["voice_session_id"]
    got = mc.utter(r, sid, "t1", "Could you jot down that the glaze needs a second firing?").get_json()
    assert got["decision"] == "chat" and got["heard"] == "chat"
    assert mc.utter(r, sid, "t3", "yes").get_json()["decision"] == "chat" and not r.turns


def test_without_its_key_the_explicit_forms_still_work_and_the_rest_is_chat(monkeypatch, tmp_path):
    r = mc.build(monkeypatch, tmp_path, {k: v for k, v in mc.ON.items() if k != "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC"})
    sid = mc.start(r)["voice_session_id"]
    assert mc.utter(r, sid, "t1", "Could you jot down that the kiln is hot?").get_json()["decision"] == "chat"
    ask = mc.utter(r, sid, "t3", "make a note that the kiln is hot").get_json()
    assert ask["decision"] == "ask" and r.cos.intents == []           # never called: no key
    mc.asked(r, sid, ask["action_id"], "t4")
    yes = mc.utter(r, sid, "t5", "yes").get_json()
    assert yes["decision"] == "confirm"
