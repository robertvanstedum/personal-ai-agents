"""The direct-voice host core (Spec 165 §4, §12.1) driven through a fake adapter that is not the Guild: an in-memory store,
one conversation ("owner"), a header for the owner. Real routes, consult ledger, bootstrap and budget (SQLite); the
provider mint is a stub. No provider, no Keychain, no microphone, no network."""
import ast
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, request

from core.realtime_voice import direct_host as core
from core.realtime_voice.budget import VoiceBudget
from core.realtime_voice.direct_host import (DirectVoiceAdapter, DirectVoiceStore, Kept, Put, Receipts, compose_instructions,
                                             direct_rules, install_direct_voice, refusal, same_record, stamp)
from core.realtime_voice.transcripts import SPEAKERS
from core.realtime_voice.voice_card import load_pinned

ROOT = Path(__file__).resolve().parents[2]
BASE = "/tp/voice/api/realtime-voice"
ENV = {core.ALLOWANCE_ENV: "2000000", "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_TP": "test-only-summary-key"}
PERSON = {"key": "test_person", "name": "Test Person", "short": "TP", "domain": "tp", "notes": ("save a note:",)}
OWNER = {"X-Owner": "robert", "X-Csrf": "ok", "Content-Type": "application/json"}


class Store(DirectVoiceStore):
    def __init__(self):
        self.markers, self.rows, self.summaries, self.start_ok = {}, [], {}, True
        self.kept_records, self.put_ok, self.lock, self.fail_keeps, self.refuse_kinds = {}, True, threading.Lock(), 0, set()

    def put(self, handle, sid, record):
        """First write wins; the same id with other content (anything but ``at``) is a conflict (Spec 165 §4.4)."""
        if not self.put_ok or record["kind"] in self.refuse_kinds:
            return Put("refused", "test store refuses")
        with self.lock:
            old = self.kept_records.get((sid, record["id"]))
            if old is None:
                self.kept_records[(sid, record["id"])] = json.loads(json.dumps(record))
                return Put("stored")
            return Put("duplicate" if same_record(old, record) else "conflict")

    def get(self, handle, sid, record_id):
        found = self.kept_records.get((sid, record_id))
        return json.loads(json.dumps(found)) if found is not None else None

    def mark_start(self, handle, sid, *, principal, max_minutes, started):
        if not self.start_ok:
            return None
        return self.markers.setdefault((sid, "start"), {"principal": principal, "conversation_id": handle,
                                                         "max_minutes": max_minutes, "started_at": stamp(started)})

    def mark_end(self, handle, sid, *, reason, capture=None):
        return self.markers.setdefault((sid, "end"), {"reason": reason, "ended_at": stamp(core.now()), **(capture or {})})

    def read_marker(self, handle, sid, what):
        return self.markers.get((sid, what))

    def keep(self, handle, principal, sid, role, item, index, text, partial, *, started, ended, interrupted=False):
        if self.fail_keeps:
            self.fail_keeps -= 1
            raise ValueError("not kept (test)")
        if not any(r["key"] == (sid, role, item, index) for r in self.rows):        # one record per stable id
            self.rows.append({"key": (sid, role, item, index), "sid": sid, "role": role, "text": text, "partial": partial,
                              "interrupted": interrupted, "started": started, "ended": ended, "principal": principal})

    def records(self, handle, sid):
        return [(SPEAKERS[r["role"]], r["text"], r["partial"], r["interrupted"]) for r in self.rows if r["sid"] == sid], True

    def summary(self, handle, sid):
        return self.summaries.get((sid, "summary"))

    def summary_state(self, handle, sid):
        decision = self.summaries.get((sid, "summary-decision"))
        if decision is not None:
            return re.match(r"\[Voice summary (\w+)", decision).group(1)
        return ("proposed" if (sid, "summary") in self.summaries else
                "unavailable" if (sid, "summary-unavailable") in self.summaries else "none")

    def keep_summary(self, handle, sid, what, text):
        old = self.summaries.get((sid, what))
        if old is not None:
            return Kept("kept" if old == text else "idempotency_mismatch", old, True)
        self.summaries[(sid, what)] = text
        return Kept("kept", text, False)


def intent_answer(fake, json):
    """v3: the understanding call (core/realtime_voice/intent_gateway.py) reaches this fake gateway too, under its own alias:
    kept apart from the summaries (``intents``), answered with ``fake.intent(json)`` (default chat), never a summary."""
    fake.intents.append(json)
    word = fake.intent(json) if callable(getattr(fake, "intent", None)) else "chat"
    if word is None:
        return SimpleNamespace(status_code=503, json=lambda: {})
    answer = {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                                                        "message": {"role": "assistant", "content": word}}]}
    return SimpleNamespace(status_code=200, json=lambda: answer)


class Gateway:
    """The model gateway, faked: one chat completion from the host's ``model`` text (None: the gateway answers 503). Every
    request is kept, and its user message is the host's prompt."""

    def __init__(self, host):
        self.host, self.requests, self.intents, self.intent = host, [], [], None
        self.entered, self.hold = threading.Event(), None       # a test may hold the gateway's answer

    def __call__(self, url, *, json, timeout, allow_redirects, headers):
        if json.get("model") == "minimoi-voice-intent":
            return intent_answer(self, json)
        self.entered.set()
        if self.hold is not None:
            assert self.hold.wait(5)
        self.requests.append({"url": url, "json": json, "timeout": timeout, "headers": headers,
                              "allow_redirects": allow_redirects})
        self.host.prompts.append(json["messages"][-1]["content"])
        if not self.host.model:
            return SimpleNamespace(status_code=503, json=lambda: {})
        answer = {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                                                            "message": {"role": "assistant", "content": self.host.model}}]}
        return SimpleNamespace(status_code=200, json=lambda: answer)


class Host(DirectVoiceAdapter):
    person = PERSON
    name = "tp_voice"
    note_max = 500
    summary_key = "voice-summary-tp"
    owner_name = "Rosa"                              # the core names the owner as the host does, never "Robert" of its own
    receipts = Receipts(("reply", "note", "reminder"))

    def __init__(self, card_root):
        self.store, self.card_root, self.turns, self.prompts, self.kinds, self.ends = Store(), card_root, [], [], [], []
        self.down, self.refusals = 0, {}
        self.deny, self.keep_ok, self.items, self.model = {}, True, [], '{"spoken": "We checked the agenda.", "written": "- Agenda: two items (turn-1)."}'

    def owner(self):
        return ("robert", "Robert") if request.headers.get("X-Owner") == "robert" else None

    def guard(self, kind):
        self.kinds.append(kind)
        return refusal(*self.deny[kind]) if kind in self.deny else None

    def csrf_ok(self):
        return request.headers.get("X-Csrf") == "ok"

    def may_keep(self):
        return self.keep_ok

    def conversation_ok(self, cid):
        return cid in ("owner", "elsewhere")

    def conversation(self, cid):
        if cid == "owner" and self.down:
            self.down -= 1
            return None, refusal("not_found", "Not now.", 404)
        return (cid, None) if cid == "owner" else (None, refusal("not_found", "No such conversation.", 404))

    def refuse(self, name, text):
        return self.refusals.pop(name, None)

    def explicit_request(self, text):
        """v3's grammar fast path, this host's own: "save a note: …" (its notes phrase)."""
        return "note" if text.lower().startswith("save a note:") else None

    def clean(self, text, limit, what):
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            return None, refusal("invalid", f"The {what} is empty or too long.", 422)
        return text.strip(), None

    def typed_turn(self, handle, principal, sid, n, name, text, *, item):
        self.turns.append((handle, principal, sid, n, name, text))
        self.items.append(item)
        if name == "save_note":
            return {"answered": True, "saved": True, "receipt": f"op-{n}", "spoken": "Saved."}
        return {"answered": True, "receipt": f"turn-{n}", "receipt_kind": "reply", "spoken": "Two items."}

    def card(self):
        return load_pinned(self.card_root)

    def instructions(self, card, handle, actions=True):
        return compose_instructions("Test rules.\n\n" + direct_rules(self.person, self.owner_name, actions), card,
                                    f"continuity of {handle}")

    def ended(self, handle, sid):
        self.ends.append(sid)


def card_release(root, release, payload, pin=True):
    folder = root / "releases" / release
    folder.mkdir(parents=True)
    raw = json.dumps({"schema": 1, "voices": {}, **payload}).encode()
    (folder / "voice.json").write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps({"files": {"voice.json": hashlib.sha256(raw).hexdigest()}}))
    if pin:
        (root / "pin.json").write_text(json.dumps({"release": release}))


@pytest.fixture
def rig(monkeypatch, tmp_path):
    card_release(tmp_path / "card", "1.0.0", {"card": "Test Person, plain and brief.", "language": "pt-BR",
                                               "accent": "light Austrian", "voices": {"openai": "cedar"}})
    app, host, mints, clock = Flask("direct-core"), Host(tmp_path / "card"), [], [time.time()]

    def mint(**kw):
        mints.append(kw)
        return {"provider": "openai", "client_secret": "ek_synthetic", "expires_at": 1, "model": "gpt-realtime-2.1"}
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint)
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    budget = VoiceBudget("sqlite:///" + str(tmp_path / "budget.db"), cap_usd=lambda: 20)
    budget.ensure_schema()
    gateway = Gateway(host)
    metadata, sessions = install_direct_voice(app, host, url_prefix="/tp/voice", environ=dict(ENV), budget=budget,
                                              clock=lambda: clock[0], summary_http=gateway)
    return SimpleNamespace(app=app, client=app.test_client(), host=host, mints=mints, clock=clock, budget=budget,
                           metadata=metadata, sessions=sessions, store=host.store, card_root=tmp_path / "card",
                           gateway=gateway)


def start(r, **body):
    answer = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": "openai",
                                                                      "conversation_id": "owner", **body})
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()["voice_session_id"]


def tap(call_id):
    """v3 (the app acts; the voice only talks): the action path acts only on a confirmed waiting action or a tap on Check
    (call id "host-…"), so a test that drives the one dispatcher (its key, replay, receipts, cap) names its call as Check's.
    The call ids stay distinct, so replay by call id and a second call on one turn are exercised exactly as before."""
    return call_id if str(call_id).startswith("host-") else "host-" + str(call_id)


def consult(r, sid, call_id="call_1", words="What is on my agenda?", name="consult", headers=OWNER, turn=None):
    return r.client.post(BASE + "/consult", headers=headers, json={"session_id": sid, "call_id": tap(call_id), "name": name,
                                                                   "owner_transcript_item": words,
                                                                   "owner_item_id": turn or item_of(call_id),
                                                                   "arguments": {"question": "the model's paraphrase"}})


def item_of(call_id):
    """A call's own owner turn (a capture id, "t" + a number), so separate calls are separate turns unless a test names one."""
    return "t" + str(int(hashlib.sha256(call_id.encode()).hexdigest(), 16) % 9000 + 1000)


def at(r, seconds):
    return stamp(datetime.fromtimestamp(r.clock[0] + seconds, timezone.utc))


def turn(r, role, item, text, offset=1, **extra):
    return {"role": role, "item_id": item, "text": text, "started_at": at(r, offset), "ended_at": at(r, offset + 1), **extra}


def post(r, sid, events, headers=OWNER):
    return r.client.post(BASE + f"/session/{sid}/transcript", headers=headers, json={"events": events})


def utter(r, sid, item, text, headers=OWNER):
    """v3: one finished owner item through the utterance route (the end and every confirmation are decided there)."""
    return r.client.post(BASE + f"/session/{sid}/utterance", headers=headers,
                         json={"conversation_id": "owner", "owner_item_id": item, "text": text})


def asked(r, sid, action_id, after, headers=OWNER):
    """v3: the page says the waiting action's question started playing after owner item ``after``."""
    return r.client.post(BASE + f"/session/{sid}/utterance/{action_id}/asked", headers=headers, json={"after_item": after})


def test_a_host_gets_the_routes_the_metadata_and_a_recorded_session(rig):
    r = rig
    assert r.metadata["person"] == {"name": "Test Person", "short": "TP"} and r.metadata["persona"] == "test_person"
    assert r.metadata["locale"] == "pt-BR" and r.metadata["card_release"] == "1.0.0"       # from the pinned card
    assert r.metadata["bootstrap_url"] == BASE + "/bootstrap" and r.metadata["heartbeat_seconds"] == 20
    sid = start(r)
    assert str(uuid.UUID(sid)) == sid and r.sessions.is_open(sid) and r.sessions.known(sid)
    assert r.store.markers[(sid, "start")]["principal"] == "robert"
    assert r.store.markers[(sid, "start")]["conversation_id"] == "owner"
    minted = r.mints[0]
    assert minted["transcription_language"] == "pt" and minted["voice"] == "cedar"       # the card's language and voice
    assert [t["name"] for t in minted["tools"]] == ["consult", "save_note"]
    assert "Ask Test Person's own turn" in minted["tools"][0]["description"]
    assert "'save a note:'" in minted["tools"][1]["description"]
    text = minted["instructions"]
    assert text.index("Test rules.") < text.index("Test Person, plain and brief.") < text.index("continuity of owner")
    assert "Test Person's own turn on Rosa's last words" in text and "Shall I sum up?" in text and "Robert" not in text
    assert r.budget.status()["reserved_usd"] == 2.0


def test_the_core_asks_the_hosts_guard_for_each_route_by_kind(rig):
    r = rig
    sid = start(r)
    consult(r, sid)
    post(r, sid, [])
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER)
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER, json={"conversation_id": "owner", "decision": "confirm"})
    assert r.host.kinds == ["bootstrap", "consult", "transcript", "end", "summary", "decision"]


def test_refusals_come_before_the_provider_and_the_allowance(rig, monkeypatch, tmp_path):
    r = rig
    body = {"persona": "test_person", "provider": "openai", "conversation_id": "owner"}
    assert r.client.post(BASE + "/bootstrap", headers={"Content-Type": "application/json"}, json=body).status_code == 401
    r.host.deny = {"bootstrap": ("csrf", "Out of date.", 403)}
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json=body).status_code == 403
    r.host.deny = {}
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json={**body, "conversation_id": "x/../y"}).status_code == 422
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json={**body, "conversation_id": "elsewhere"}).status_code == 404
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json={**body, "persona": "frau_berger"}).status_code == 400
    assert r.client.post(BASE + "/memo/bootstrap", headers=OWNER, json={}).status_code == 404   # not this host's route
    r.store.start_ok = False                                       # no durable start: no session, nothing reserved
    refused = r.client.post(BASE + "/bootstrap", headers=OWNER, json=body)
    assert refused.status_code == 503 and refused.get_json()["error"] == "unavailable"
    assert not r.mints and r.budget.status()["reserved_usd"] == 0
    app = Flask("no-allowance")
    install_direct_voice(app, Host(r.card_root), url_prefix="/tp/voice", environ={}, budget=r.budget)
    none = app.test_client().post(BASE + "/bootstrap", headers=OWNER, json=body)
    assert none.status_code == 503 and "allowance" in none.get_json()["message"]


def test_a_card_whose_language_changed_under_a_running_host_is_refused(rig):
    r = rig
    card_release(r.card_root, "1.0.1", {"card": "Test Person.", "language": "en-US"})         # the pin moves on disk
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": "openai",
                                                                   "conversation_id": "owner"}).status_code == 400
    assert not r.mints


def test_a_consult_runs_the_hosts_turn_once_on_the_owners_words_with_its_request_and_receipt_kept(rig):
    r = rig
    sid = start(r)
    first, again = consult(r, sid), consult(r, sid)
    assert first.status_code == 200 and again.get_json() == first.get_json()                # replay: one effect
    assert first.get_json()["receipt"] == "turn-1" and first.get_json()["spoken"] == "Two items."
    assert r.host.turns == [("owner", "robert", sid, 1, "consult", "What is on my agenda?")]
    assert "paraphrase" not in str(r.store.rows)
    kept = [(row["role"], row["text"]) for row in r.store.rows]
    assert kept == [("consult", "consult: What is on my agenda?"), ("receipt", "Answered. Reply turn-1. Spoken: Two items.")]
    assert r.host.receipts.parts(kept[1][1]) == {"outcome": "answered", "ref_kind": "Reply", "ref": "turn-1", "spoken": "Two items."}
    saved = consult(r, sid, call_id="call_2", words="save a note: the agenda is short", name="save_note").get_json()
    assert saved["saved"] is True and r.host.turns[-1][3] == 2
    assert r.store.rows[-1]["text"] == "Saved. Operation op-2. Spoken: Saved."
    assert consult(r, str(uuid.uuid4()), call_id="call_3").status_code == 403               # not this owner's session
    assert consult(r, sid, call_id="call_4", headers={**OWNER, "X-Csrf": "forged"}).status_code == 403
    assert r.client.post(BASE + f"/session/{sid}/end", headers=OWNER).get_json() == {"ok": True, "ended": True, "capture": "complete"}
    assert consult(r, sid, call_id="call_5").status_code == 409                             # after End: none
    assert len(r.host.turns) == 2 and r.host.ends == [sid] and r.store.markers[(sid, "end")]["reason"] == "end"


def test_words_the_host_will_not_keep_are_no_turn_and_the_receipt_says_so(rig):
    r = rig
    sid = start(r)
    result = consult(r, sid, words="x" * 501).get_json()
    assert result["ok"] is False and result["error"] == "invalid" and result["spoken"] == core.FAILED
    assert not r.host.turns and [row["role"] for row in r.store.rows] == ["receipt"]
    assert r.store.rows[0]["text"] == f"Not answered (invalid). Spoken: {core.FAILED}"


def test_transcript_posts_are_validated_capped_and_heard(rig, monkeypatch):
    r = rig
    sid = start(r)
    assert post(r, sid, []).get_json() == {"ok": True, "kept": 0}                         # the heartbeat
    assert post(r, sid, [turn(r, "system", "t0", "Hi")]).get_json()["error"] == "invalid"
    assert post(r, sid, [turn(r, "user", "t0", "Hi", interrupted=True)]).status_code == 422
    assert post(r, sid, [turn(r, "user", "t0", "Hi", offset=3600)]).get_json()["error"] == "time"
    assert post(r, sid, [{**turn(r, "user", "t0", "Hi"), "instructions": "be someone else"}]).status_code == 422
    assert post(r, sid, [turn(r, "user", "t0", "Hi")] * 21).status_code == 422
    assert post(r, sid, [turn(r, "user", "t0", "Hi")], headers={**OWNER, "X-Owner": "guest"}).status_code == 403
    # v3: a transcript post keeps turns and decides nothing; the spoken end is the utterance route's (one control owner).
    assert "propose_end" not in post(r, sid, [turn(r, "user", "t1", "We're not done.")]).get_json()
    assert utter(r, sid, "t1", "We're not done.").get_json()["decision"] == "chat"
    assert "propose_end" not in post(r, sid, [turn(r, "user", "t2", "Okay, we're done.")]).get_json()
    proposed = utter(r, sid, "t2", "Okay, we're done.").get_json()
    assert proposed["decision"] == "ask" and proposed["kind"] == "end" and proposed["prompt"] == core.SUMMARY_PROMPT
    assert asked(r, sid, proposed["action_id"], "t2").status_code == 200
    assert "end_confirmed" not in post(r, sid, [turn(r, "user", "t3", "Yes please")]).get_json()
    confirmed = utter(r, sid, "t3", "Yes please").get_json()
    assert confirmed["decision"] == "confirm" and confirmed["kind"] == "end"
    monkeypatch.setattr(core, "SESSION_MAX_EVENTS", 4)
    assert post(r, sid, [turn(r, "user", "t3", "Yes please")]).status_code == 200          # counted once
    assert post(r, sid, [turn(r, "user", "t4", "One more")]).status_code == 200
    assert post(r, sid, [turn(r, "user", "t5", "Too many")]).status_code == 429
    assert [row["text"] for row in r.store.rows] == ["We're not done.", "Okay, we're done.", "Yes please", "One more"]
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER)
    assert post(r, sid, [turn(r, "user", "t6", "late")]).status_code == 409


def test_end_keeps_the_events_it_carries_only_where_the_host_may_keep_them(rig):
    r = rig
    sid, other = start(r), start(r)
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"events": [turn(r, "user", "t0", "Bye.")]})
    assert ended.get_json() == {"ok": True, "ended": True, "capture": "complete"} and [row["text"] for row in r.store.rows] == ["Bye."]
    r.host.keep_ok = False
    ended = r.client.post(BASE + f"/session/{other}/end", headers=OWNER, json={"events": [turn(r, "user", "t0", "Private.")]})
    assert ended.get_json() == {"ok": True, "ended": True, "capture": "incomplete", "missing": 1}       # dropped (R2 N4)
    assert len(r.store.rows) == 1 and not r.sessions.is_open(other)


def test_the_summary_is_this_sessions_records_once_and_decided_in_writing(rig, monkeypatch):
    r = rig
    earlier = start(r)
    post(r, earlier, [turn(r, "user", "t0", "An earlier session's words.")])
    r.client.post(BASE + f"/session/{earlier}/end", headers=OWNER)
    sid = start(r)
    consult(r, sid)
    post(r, sid, [turn(r, "user", "t0", "We're done.")])
    ask = lambda path="summary", **b: r.client.post(BASE + f"/session/{sid}/{path}", headers=OWNER,  # noqa: E731
                                                     json={"conversation_id": "owner", **b})
    summary = ask().get_json()
    assert summary["ok"] is True and summary["state"] == "proposed" and summary["spoken"] == "We checked the agenda."
    assert summary["written"].startswith(core.PROPOSAL_HEAD) and "Agenda: two items" in summary["written"]
    assert summary["covers"] == {"records": 3, "of": 3, "omitted": False}
    assert "An earlier session" not in r.host.prompts[-1] and "consult request: consult: What is on my agenda?" in r.host.prompts[-1]
    assert r.store.markers[(sid, "end")]["reason"] == "end" and not r.sessions.is_open(sid)
    assert ask().get_json()["repeated"] is True and len(r.host.prompts) == 1              # kept once, never re-run
    assert ask("summary/decision", decision="maybe").status_code == 422
    assert ask("summary/decision", decision="confirm", text="extra").status_code == 422
    assert ask("summary/decision", decision="edit", text="x" * 500).status_code == 422
    confirmed = ask("summary/decision", decision="confirm").get_json()
    assert confirmed["state"] == "confirmed" and confirmed["authorizes"] == "nothing beyond the summary"
    assert ask("summary/decision", decision="confirm").get_json()["repeated"] is True
    late = ask("summary/decision", decision="decline")
    assert late.status_code == 409 and late.get_json()["error"] == "already_decided"
    assert r.client.post(BASE + f"/session/{earlier}/summary/decision", headers=OWNER,
                         json={"conversation_id": "owner", "decision": "confirm"}).get_json()["error"] == "no_proposal"


def test_a_summary_is_unavailable_with_nothing_kept_or_no_model_and_the_words_say_so(rig):
    r = rig
    sid = start(r)
    nothing = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    assert nothing == {"ok": False, "error": "nothing_kept", "state": "unavailable", "line": core.SUMMARY_UNAVAILABLE}
    assert r.store.summaries[(sid, "summary-unavailable")] == core.UNAVAILABLE_TEXT
    other = start(r)
    post(r, other, [turn(r, "user", "t0", "Hello")])
    r.host.model = None
    down = r.client.post(BASE + f"/session/{other}/summary", headers=OWNER, json={"conversation_id": "owner"}).get_json()
    assert down["error"] == "gateway_error" and r.store.markers[(other, "end")]["reason"] == "end"   # still revealed


def test_a_summary_belongs_to_its_owner_and_conversation(rig):
    r = rig
    sid = start(r)
    other_owner = {**OWNER, "X-Owner": "guest"}
    for headers, cid, status in ((other_owner, "owner", 403), (OWNER, "elsewhere", 404), (OWNER, "no/such", 403),
                                 (OWNER, None, 403)):
        answer = r.client.post(BASE + f"/session/{sid}/summary", headers=headers, json={"conversation_id": cid})
        assert answer.status_code == status, (headers, cid)
    assert r.client.post(BASE + "/session/not-a-uuid/summary", headers=OWNER, json={"conversation_id": "owner"}).status_code == 403
    assert r.sessions.is_open(sid) and not r.host.prompts


def test_a_failed_mint_closes_the_recorded_session_as_not_started(rig, monkeypatch):
    from core.realtime_voice.providers.openai_realtime import OpenAIRealtimeError
    r = rig

    def fail(**kw):
        raise OpenAIRealtimeError("upstream")
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", fail)
    assert r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": "openai",
                                                                   "conversation_id": "owner"}).status_code == 502
    (sid, _what), = r.store.markers.keys() - {k for k in r.store.markers if k[1] == "end"}
    assert r.store.markers[(sid, "end")]["reason"] == "not_started" and not r.sessions.is_open(sid)


def test_state_is_read_from_the_markers_and_the_clock():
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    start = {"started_at": stamp(now - timedelta(minutes=5)), "max_minutes": 30}
    assert core.state(None, None, "s") is None                                              # not the direct path
    assert core.state(start, {"reason": "end"}, "s", when=now) == "ended"
    assert core.state(start, None, "s", when=now + timedelta(minutes=30)) == "dropped"
    assert core.state({"started_at": "garbled", "max_minutes": 30}, None, "s", when=now) == "dropped"
    assert core.state(start, None, "s", live=lambda sid: True, when=now) == "live"
    assert core.state(start, None, "s", when=now) == "recovering"


def test_receipt_words_are_the_hosts_and_read_back_whole():
    receipts = Receipts(("reply", "note", "reminder"), acted_key="acted", acted={"go": ("Started", "started")},
                        acted_other=("Something done", "something done"), acted_kind="job")
    assert receipts.line("consult", {"answered": True, "receipt": "r-1", "receipt_kind": "reminder", "spoken": "At five."}) \
        == "Answered. Reminder r-1. Spoken: At five."
    assert receipts.parts("Answered. Reminder r-1. Spoken: At five.")["ref_kind"] == "Reminder"
    acted = receipts.line("consult", {"acted": "go", "receipt": "j", "receipt_kind": "job", "spoken": "Ok."})
    assert acted == "Started. Job j. Spoken: Ok."
    assert receipts.parts(acted) == {"outcome": "started", "ref_kind": "Job", "ref": "j", "spoken": "Ok."}
    assert receipts.parts(receipts.line("consult", {"acted": "zz", "spoken": ""}))["outcome"] == "something done"
    assert receipts.parts("Answered. Ticket x. Spoken: y") == {}                           # not one of this host's kinds
    assert receipts.parts("Anything else.") == {}


def test_the_core_names_no_person_and_imports_no_host():
    source = (ROOT / "core/realtime_voice/direct_host.py").read_text()
    imported = {n.module or "" for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m.startswith(("minimoi_portal", "domains", "services"))}
    code = re.sub(r'"""[\s\S]*?"""|#.*', "", source)                                      # docstrings and comments aside
    for person in ("Master Craftsman", "Chief of Staff", "master_craftsman", "mc_turns", '"mc"'):
        assert person not in code, person
    page = re.sub(r"//.*", "", (ROOT / "core/realtime_voice/static/direct-voice-session.js").read_text())
    for person in ("Master Craftsman", "Chief of Staff", "mc_turns_off", "Check with MC", "'MC'"):
        assert person not in page, person


# ── Codex's Chunk B review, in the core every host inherits (REVIEW_codex_chunk_B.md) ───────────────────────────────────

def test_one_owner_turn_is_one_effect_for_any_host(rig):
    """B1: a second call for the same owner turn, under any call id or function, gets the first one's answer."""
    r = rig
    sid = start(r)
    first = consult(r, sid, call_id="host-1", turn="t1").get_json()
    second = consult(r, sid, call_id="call_2", name="save_note", turn="t1").get_json()     # the same words, the model's
    assert len(r.host.turns) == 1 and first["receipt"] == "turn-1" and second["same_turn"] is True
    assert second["first_tool"] == "consult" and second["spoken"].startswith("That was already handled: Two items.")
    assert consult(r, sid, call_id="call_3", turn="t3").get_json()["receipt"] == "turn-2"     # his next words: a new turn
    bare = r.client.post(BASE + "/consult", headers=OWNER, json={"session_id": sid, "call_id": "host-c4", "name": "consult",
                                                                 "owner_transcript_item": "x"})
    assert bare.status_code == 400 and len(r.host.turns) == 2                              # no owner turn: refused


def test_end_intent_is_heard_once_per_turn_and_keep_talking_withdraws_it(rig):
    """B5."""
    r = rig
    sid = start(r)
    # v3: heard once per owner item on the utterance route (a retried post gets its first decision), never by a post.
    first = utter(r, sid, "t0", "Yes, we're done.").get_json()
    assert first["decision"] == "ask" and first["kind"] == "end"
    replay = utter(r, sid, "t0", "Yes, we're done.").get_json()
    assert replay["decision"] == "ask" and replay["action_id"] == first["action_id"] and replay["repeated"] is True
    asked(r, sid, first["action_id"], "t0")
    assert r.client.post(BASE + f"/session/{sid}/keep_talking", headers=OWNER, json={}).get_json()["proposal"] == "withdrawn"
    assert utter(r, sid, "t1", "Yes.").get_json()["decision"] == "chat"


def test_a_kept_answer_replays_after_end_and_nothing_new_runs(rig):
    """B7."""
    r = rig
    sid = start(r)
    result = consult(r, sid).get_json()
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER)
    assert consult(r, sid).get_json() == result and len(r.host.turns) == 1
    assert consult(r, sid, call_id="call_9").status_code == 409


def test_what_was_not_kept_and_what_still_ran_are_marked_at_the_cutoff(rig):
    """B2, B4: the end marker says capture incomplete; a running consult is outside the summary, which says so."""
    r = rig
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Kept words.")])
    r.sessions.hold(sid, "t9", "call_running")
    summary = r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER,
                            json={"conversation_id": "owner", "capture": "incomplete"}).get_json()
    assert r.store.markers[(sid, "end")]["capture"] == "incomplete"
    assert "was not kept" in summary["written"] and "still running" in summary["written"]
    assert "was not kept" in r.host.prompts[-1] and "still running" in r.host.prompts[-1]
    other = start(r)
    r.client.post(BASE + f"/session/{other}/end", headers=OWNER, json={"capture": "incomplete"})
    assert r.store.markers[(other, "end")]["capture"] == "incomplete"
