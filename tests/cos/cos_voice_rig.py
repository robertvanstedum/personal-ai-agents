"""A Chief of Staff direct-voice host for tests (Spec 165 v0.3 §6): her real adapter (domains/cos/direct_voice.py), her real
file store in a temporary folder, the core's real routes, bootstrap and budget (SQLite), and the canonical Confer turn
service (domains/cos/confer_service.ConferTurnService) with its operations faked: her runtime, the note store, reminders
and the runtime reset each record what reaches them. The provider mints and the summary gateway are stubs. Used by
tests/cos/test_cos_direct_voice.py and tests/realtime_voice/test_direct_contract.py. No provider, no Keychain, no
microphone, no network."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from core.realtime_voice import direct_host as core
from core.realtime_voice.budget import VoiceBudget
from core.realtime_voice.direct_host import stamp
from domains.cos import direct_voice
from domains.cos import files as cos_files
from domains.cos.confer_service import ConferTurnRequest, ConferTurnService

BASE = direct_voice.PREFIX + "/api/realtime-voice"
OWNER = {"X-Minimoi-Auth-Id": "7", "X-Minimoi-User-Tier": "owner", "X-Minimoi-Display-Name": "Robert",
         "X-Minimoi-Voice-Write-Verified": "1", "Content-Type": "application/json"}
READ = {k: v for k, v in OWNER.items() if k != "X-Minimoi-Voice-Write-Verified"}
ALLOWANCE = "2000000"
ENV = {"MINIMOI_COS_VOICE_DIRECT": "1", "MINIMOI_ROLE": "development", core.ALLOWANCE_ENV: ALLOWANCE,
       "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS": "test-only-summary-key"}


class Runtime:
    """Her agent runtime, faked: each call is recorded; it answers ``reply``, or raises ``error``, or waits on ``hold``."""

    def __init__(self):
        self.calls, self.reply, self.error, self.hold, self.entered = [], "Two items are stuck on #12.", None, None, threading.Event()

    def __call__(self, prompt, context, tool_policy):
        self.calls.append({"prompt": prompt, "channel": context.get("confer", {}).get("input_channel"),
                           "conversation_id": context.get("confer", {}).get("conversation_id")})
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        if self.error is not None:
            raise self.error
        return self.reply


class Notes:
    """The platform note store, faked as cos_memory's own dedup does it: one note per operation id."""

    def __init__(self):
        self.saved, self.refuse = {}, False

    def __call__(self, text, operation_id):
        if self.refuse:
            return {"saved": False, "deduplicated": False}
        if operation_id in self.saved:
            return {"saved": True, "deduplicated": True}
        self.saved[operation_id] = text
        return {"saved": True, "deduplicated": False}


class Gateway:
    """The model gateway, faked: a summary completion ("" as the model: 503)."""

    def __init__(self):
        self.requests, self.model = [], '{"spoken": "We checked twelve.", "written": "- #12: two items stuck."}'
        self.intents, self.intent = [], None

    def __call__(self, url, *, json, timeout, allow_redirects, headers):
        if json.get("model") == "minimoi-voice-intent":          # v3's understanding call: answered apart, never a summary
            self.intents.append(json)
            word = self.intent(json) if callable(self.intent) else "chat"
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"index": 0, "finish_reason": "stop",
                                                                               "message": {"role": "assistant", "content": word}}]})
        self.requests.append({"url": url, "json": json, "headers": headers})
        if not self.model:
            return SimpleNamespace(status_code=503, json=lambda: {})
        answer = {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                                                            "message": {"role": "assistant", "content": self.model}}]}
        return SimpleNamespace(status_code=200, json=lambda: answer)


def card_root(tmp_path, voices):
    """A card release like hers, with these voices (her real card names only openai's)."""
    real = json.loads((direct_voice.CARD_ROOT / "releases/0.1.0/voice.json").read_text())
    root = tmp_path / "card"
    folder = root / "releases" / "0.1.0"
    folder.mkdir(parents=True)
    raw = json.dumps({**real, "voices": voices}).encode()
    (folder / "voice.json").write_bytes(raw)
    (folder / "manifest.json").write_text(json.dumps({"files": {"voice.json": hashlib.sha256(raw).hexdigest()}}))
    (root / "pin.json").write_text(json.dumps({"release": "0.1.0"}))
    return root


def build(monkeypatch, tmp_path, *, environ=None, voices=None, budget=None, briefing=None, memory="(no memory yet)",
          with_store=True, files=True):
    """Her host on a fresh app. ``voices`` None: her real card (cedar on OpenAI, no Grok voice); else a copy naming these."""
    voice, recall = tmp_path / "cos_voice", tmp_path / "conversations"
    recall.mkdir(parents=True, exist_ok=True)
    if with_store:
        voice.mkdir(exist_ok=True)
    env = dict(ENV if environ is None else environ)
    env.setdefault("COS_VOICE_DIR", str(voice))
    app, mints, clock = Flask("cos-direct"), [], [time.time()]
    runtime, notes, reminders, resets, turns = Runtime(), Notes(), [], [], []
    context = {"error": None, "hold": None}

    def build_context():
        if context["hold"] is not None:
            context["hold"].wait(10)
        if context["error"] is not None:
            raise context["error"]
        return {"system_prompt": "her system prompt"}
    outbox = None
    if files:
        inbox, outbox = tmp_path / "inbox", tmp_path / "outbox"
        inbox.mkdir(exist_ok=True)
        outbox.mkdir(exist_ok=True)
        files = cos_files.CosFiles(inbox, outbox, "/minimoi/cos/inbox", "/minimoi/cos/outbox")
    else:
        files = None

    def process_turn(text, *, channel, conversation_id, request_id, on_dispatch=None):
        """chief_of_staff._process_confer_turn's shape: the canonical turn service, its operations faked."""
        turns.append({"text": text, "channel": channel, "conversation_id": conversation_id, "request_id": request_id})
        service = ConferTurnService(
            call_backend=runtime, build_context=build_context, increment_chat=lambda: None,
            backend_metadata=lambda: ("COS Agent A", "test-model"), save_note=notes,
            reset_conversation=lambda cid: resets.append(cid) or True,
            save_reminder=lambda due, words, op: reminders.append((due, words, op)) or {
                "saved": True, "reminder": {"id": "r1", "due_utc": stamp(due), "text": words}},
            workspace_note=None)
        return service.handle(ConferTurnRequest(text=text, channel=channel, conversation_id=conversation_id,
                                                request_id=request_id, on_dispatch=on_dispatch))

    def mint(**kw):
        mints.append(kw)
        return {"provider": "openai", "client_secret": "ek_synthetic", "expires_at": 1, "model": "gpt-realtime-2.1"}

    def xai(**kw):
        mints.append(kw)
        return {"provider": "xai", "ephemeral_token": "xai_synthetic", "expires_after_seconds": 300,
                "model": "grok-voice-latest", "session_config": {}}
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint)
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", xai)
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    if budget is None:
        budget = VoiceBudget("sqlite:///" + str(tmp_path / "budget.db"), cap_usd=lambda: 20)
        budget.ensure_schema()
    gateway = Gateway()
    metadata = direct_voice.install(
        app, environ=env, process_turn=process_turn,
        briefing=briefing or (lambda memory: "--- MEMORY ---\n" + memory + "\n--- TODAY ---\nTuesday"),
        memory=lambda: memory, files=lambda: files, recall=recall, budget=budget,
        clock=lambda: clock[0], summary_http=gateway,
        card_root=card_root(tmp_path, voices) if voices is not None else direct_voice.CARD_ROOT)
    return SimpleNamespace(app=app, client=app.test_client(), metadata=metadata, mints=mints, clock=clock, runtime=runtime,
                           notes=notes, reminders=reminders, resets=resets, turns=turns, budget=budget, gateway=gateway,
                           voice=voice, recall=recall, outbox=outbox, files=files, env=env, context=context)


def start(r, provider="openai", headers=OWNER):
    answer = r.client.post(BASE + "/bootstrap", headers=headers, json={"persona": "chief_of_staff", "provider": provider,
                                                                        "conversation_id": "owner"})
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()["voice_session_id"]


def tap(call_id):
    """v3 (the app acts; the voice only talks): the action path acts only on a confirmed waiting action or a tap on Check
    (call id "host-…"), so a test that drives the one dispatcher names its call as Check's; distinct ids stay distinct."""
    return call_id if str(call_id).startswith("host-") else "host-" + str(call_id)


def ask(r, sid, call_id="call_1", item="t1", words="What is stuck on #12?", name="consult", headers=OWNER):
    return r.client.post(BASE + "/consult", headers=headers, json={
        "session_id": sid, "call_id": tap(call_id), "name": name, "owner_transcript_item": words, "owner_item_id": item,
        "arguments": {"question": "the model's paraphrase"}})


def event(r, role, item, text, offset=1, **extra):
    def at(seconds):
        return stamp(datetime.fromtimestamp(r.clock[0] + seconds, timezone.utc))
    return {"role": role, "item_id": item, "text": text, "started_at": at(offset), "ended_at": at(offset + 1), **extra}


def post(r, sid, events, headers=OWNER):
    return r.client.post(BASE + f"/session/{sid}/transcript", headers=headers, json={"events": events})


def effects(r):
    """What reached her operations: runtime turns, notes newly saved, resets and reminders."""
    return len(r.runtime.calls) + len(r.notes.saved) + len(r.resets) + len(r.reminders)


def project_root():
    return Path(__file__).resolve().parents[2]
