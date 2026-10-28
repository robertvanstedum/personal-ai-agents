"""Private draft: controlled Realtime media session, never a second agent backend.

Only a trusted provider sideband feeds observe(). HTTP/browser events must not
feed it. Transcripts stay in memory unless a trusted optional host retention callback keeps
scoped platform notes. The durable control journal stores hashes and private
bounded tool receipts. Existing VoiceBudget owns the shared atomic daily ledger.
No reservation or usage estimate is a guaranteed billing cap.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import threading
import time
import uuid
from core.realtime_voice.timing import VoiceTiming

MODEL = "gpt-realtime-2.1"
TOOLS = frozenset({"consult_cos_agent", "save_cos_note"})
ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


from core.realtime_voice.budget import VoiceBudgetError
from core.realtime_voice.errors import SessionRefused    # moved there (Spec 165 §4.2); the same class, re-exported


class UnresolvedOperation(SessionRefused):
    """Fixed admission classification; never owner/provider detail."""
    MESSAGES = {
        "voice_session_unresolved": "An earlier voice session has not been confirmed ended. Its status needs verification before voice can start. Typing still works.",
        "agent_action_unresolved": "An earlier agent action needs verification before voice can start. Typing still works.",
    }
    def __init__(self, code):
        if code not in self.MESSAGES:
            raise ValueError("invalid_admission_code")
        self.code = code
        super().__init__(self.MESSAGES[code])


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SessionRefused("Invalid correlation identifier")
    return value


def count(value):
    if type(value) is not int or not 0 <= value <= 10_000_000:
        raise SessionRefused("Usage unavailable")
    return value


def response_micro(usage):
    """Uncached upper estimate at published 2.1 rates, rounded up.

    No cache discount; modality totals must match aggregate totals. Unsupported
    or missing usage refuses further work instead of silently costing zero.
    Input $4/$32/$5; output $24/$64 per million text/audio/image tokens.
    Rates are micro-dollars per token. Transcription is accounted separately.
    """
    try:
        i, o = usage["input_token_details"], usage["output_token_details"]
        it, ia = (count(i[k]) for k in ("text_tokens", "audio_tokens"))
        # The SDK emits null for an omitted optional image count on audio-only
        # responses. Zero is accepted only if the aggregate still reconciles;
        # unexplained input tokens continue to refuse accounting.
        images = i.get("image_tokens")
        ii = 0 if images is None else count(images)
        ot, oa = (count(o[k]) for k in ("text_tokens", "audio_tokens"))
        if it + ia + ii != count(usage["input_tokens"]) or ot + oa != count(usage["output_tokens"]):
            raise SessionRefused("Usage unavailable")
        return it * 4 + ia * 32 + ii * 5 + ot * 24 + oa * 64
    except (KeyError, TypeError):
        raise SessionRefused("Usage unavailable") from None


def transcription_micro(usage):
    """gpt-4o-transcribe: $2.50/M text input, $6/M audio, $10/M text output.
    Kept separate from realtime responses; conservative rounding per item.
    """
    try:
        if usage["type"] != "tokens":
            raise SessionRefused("Usage unavailable")
        details = usage["input_token_details"]
        text, audio = count(details["text_tokens"]), count(details["audio_tokens"])
        output = count(usage["output_tokens"])
        if text + audio != count(usage["input_tokens"]):
            raise SessionRefused("Usage unavailable")
        return (text * 5 + audio * 12 + output * 20 + 1) // 2
    except (KeyError, TypeError):
        raise SessionRefused("Usage unavailable") from None


class Journal:
    """Uses VoiceBudget._tx (Postgres guild search path or disposable SQLite).
    Schema creation is explicit operator setup, never implicit request mutation.
    """
    def __init__(self, tx):
        self.tx = tx

    def init(self):
        with self.tx() as run:
            run("CREATE TABLE IF NOT EXISTS voice_control_owner (who TEXT PRIMARY KEY)")
            run("CREATE TABLE IF NOT EXISTS voice_control_session (id TEXT PRIMARY KEY, who TEXT NOT NULL, conversation TEXT NOT NULL, state TEXT NOT NULL, allowance BIGINT NOT NULL, used BIGINT NOT NULL DEFAULT 0, surface TEXT, provider_call_id TEXT, end_receipt TEXT)")
            run("CREATE TABLE IF NOT EXISTS voice_control_call (session_id TEXT NOT NULL, call_id TEXT NOT NULL, operation_id TEXT NOT NULL, item_id TEXT NOT NULL, text_hash TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT, PRIMARY KEY(session_id,call_id), UNIQUE(session_id,operation_id))")
            run("CREATE TABLE IF NOT EXISTS voice_control_usage (session_id TEXT NOT NULL, event_key TEXT NOT NULL, micro BIGINT NOT NULL, PRIMARY KEY(session_id,event_key))")

    @staticmethod
    def _check_admission(run, who, conversation):
        if run("SELECT id FROM voice_control_session WHERE who=? AND state IN ('connecting','active','ending','termination_uncertain')", (who,)).fetchone():
            raise UnresolvedOperation("voice_session_unresolved")
        if run("SELECT c.call_id FROM voice_control_call c JOIN voice_control_session s ON s.id=c.session_id WHERE s.who=? AND s.conversation=? AND c.state IN ('attempting','uncertain')", (who,conversation)).fetchone():
            raise UnresolvedOperation("agent_action_unresolved")

    def check_admission(self, principal, conversation):
        # Read-only early refusal before constructing the provider. create()
        # repeats this under the existing owner lock: preflight is not authority.
        with self.tx() as run:
            self._check_admission(run, digest(principal), conversation)

    def create(self, principal, conversation, allowance, surface="cos"):
        if not isinstance(principal, str) or not principal or not isinstance(conversation, str) or not 1 <= len(conversation) <= 160 or type(allowance) is not int or allowance <= 0:
            raise SessionRefused("Session identity or allowance missing")
        if surface not in {"cos", "mc"}:
            raise SessionRefused("Voice surface unavailable")
        sid = str(uuid.uuid4())
        with self.tx() as run:
            who = digest(principal)
            run("INSERT INTO voice_control_owner VALUES(?) ON CONFLICT(who) DO NOTHING", (who,))
            run("UPDATE voice_control_owner SET who=who WHERE who=?", (who,))
            self._check_admission(run, who, conversation)
            run("INSERT INTO voice_control_session(id,who,conversation,state,allowance,surface) VALUES(?,?,?,'connecting',?,?)", (sid,digest(principal),conversation,allowance,surface))
        return sid

    def check(self, sid, principal, active=True):
        with self.tx() as run:
            row = run("SELECT who,conversation,state,allowance,used FROM voice_control_session WHERE id=?", (sid,)).fetchone()
        if not row or row[0] != digest(principal) or (active and row[2] != "active"):
            raise SessionRefused("Session unavailable")
        return row

    def state(self, sid, state):
        with self.tx() as run:
            run("UPDATE voice_control_session SET state=? WHERE id=?", (state,sid))

    def provider_identity(self, sid, call_id):
        """Private reconciliation identity, retained before sideband attach."""
        call_id = identifier(call_id)
        with self.tx() as run:
            run("UPDATE voice_control_session SET used=used WHERE id=?", (sid,))
            row = run("SELECT provider_call_id FROM voice_control_session WHERE id=?", (sid,)).fetchone()
            if not row or row[0] not in (None, call_id):
                raise SessionRefused("Provider identity conflict")
            run("UPDATE voice_control_session SET provider_call_id=? WHERE id=?", (call_id,sid))

    def record_end(self, sid, receipt, call_id=None):
        """First receipt wins. Never used to retry hangup or clear old holds."""
        encoded = json.dumps(receipt,sort_keys=True,separators=(",", ":"))
        if len(encoded.encode()) > 2048:
            raise SessionRefused("End receipt unavailable")
        if call_id is not None:
            call_id = identifier(call_id)
        with self.tx() as run:
            run("UPDATE voice_control_session SET used=used WHERE id=?", (sid,))
            row = run("SELECT provider_call_id,end_receipt,state FROM voice_control_session WHERE id=?", (sid,)).fetchone()
            if not row or row[0] not in (None,call_id) and call_id is not None:
                raise SessionRefused("Provider identity conflict")
            if row[1] is not None:
                return json.loads(row[1])
            if row[2] not in {"connecting","active","ending"}:
                raise SessionRefused("Existing session requires operator disposition")
            state = "ended" if receipt["termination_confirmed"] is True else "termination_uncertain"
            run("UPDATE voice_control_session SET state=?,provider_call_id=COALESCE(provider_call_id,?),end_receipt=? WHERE id=?", (state,call_id,encoded,sid))
        return receipt

    def end_receipt(self, sid, principal, surface):
        """Authenticated historical read only. Identity stays private in SQL.
        No provider instance, new attempt, hangup, refund or state mutation.
        Missing old receipts/legacy surface attribution never become success.
        """
        if surface not in {"cos", "mc"}:
            raise SessionRefused("Voice surface unavailable")
        with self.tx() as run:
            row = run("SELECT who,surface,state,end_receipt FROM voice_control_session WHERE id=?", (sid,)).fetchone()
        if not row or row[0] != digest(principal) or row[1] != surface:
            raise SessionRefused("Session unavailable")
        if row[3] is not None:
            receipt = json.loads(row[3])
            if (not isinstance(receipt,dict) or receipt.get("session_id") != sid
                    or type(receipt.get("termination_confirmed")) is not bool):
                raise SessionRefused("End receipt unavailable")
            return receipt
        return {"session_id":sid,"reason":"recovery_required",
                "termination_confirmed":False,"journal_confirmed":True,
                "native_actions_cancelled":False}

    def claim(self, sid, call, item, name, text):
        # Both aliases enter the same host Confer parser. An explicit note has
        # one canonical effect, irrespective of the model's chosen tool alias.
        # Derive identity only from actual owner transcript, never model args.
        from domains.cos.confer_service import parse_explicit_note
        effect = "explicit_note" if parse_explicit_note(text) is not None else name
        operation = str(uuid.uuid5(uuid.UUID(sid), item + ":" + effect))
        with self.tx() as run:
            # Session lock serializes close vs admission across processes.
            run("UPDATE voice_control_session SET used=used WHERE id=? AND state='active'", (sid,))
            if run("SELECT state FROM voice_control_session WHERE id=?", (sid,)).fetchone() != ("active",):
                raise SessionRefused("Session unavailable")
            old = run("SELECT call_id,item_id,text_hash,state,receipt,operation_id FROM voice_control_call WHERE session_id=? AND (call_id=? OR operation_id=?)", (sid,call,operation)).fetchone()
            if old:
                if old[1:3] != (item,digest(text)) or old[5] != operation:
                    raise SessionRefused("Correlation conflict")
                return False, old[3], json.loads(old[4]) if old[4] else None, operation
            run("INSERT INTO voice_control_call(session_id,call_id,operation_id,item_id,text_hash,state) VALUES(?,?,?,?,?,'attempting')", (sid,call,operation,item,digest(text)))
        return True, "attempting", None, operation

    def finish(self, sid, call, state, receipt=None):
        encoded = json.dumps(receipt,ensure_ascii=False) if receipt is not None else None
        if encoded and len(encoded.encode()) > 16384:
            state, encoded = "uncertain", None
        with self.tx() as run:
            run("UPDATE voice_control_call SET state=?,receipt=? WHERE session_id=? AND call_id=? AND state='attempting'", (state,encoded,sid,call))
        return state

    def usage(self, sid, key, amount):
        with self.tx() as run:
            run("UPDATE voice_control_session SET used=used WHERE id=?", (sid,))
            old = run("SELECT micro FROM voice_control_usage WHERE session_id=? AND event_key=?", (sid,key)).fetchone()
            if old and old[0] != amount:
                raise SessionRefused("Usage conflict")
            if not old:
                run("INSERT INTO voice_control_usage VALUES(?,?,?)", (sid,key,amount))
                run("UPDATE voice_control_session SET used=used+? WHERE id=?", (amount,sid))
            return run("SELECT used,allowance FROM voice_control_session WHERE id=?", (sid,)).fetchone()


class ControlledSession:
    """Trusted service-owned lifecycle. provider: create/attach/send/hangup.

    dispatch(name, canonical_text, conversation_id, operation_id) must reuse the
    domain's existing kept-note/Confer boundary; it must not treat model args as
    owner approval. Full-tool Agent A remains the owner-approved experiment.
    Native actions already accepted cannot be undone by barge-in/End.
    """
    def __init__(self, *, journal, budget, provider, principal, conversation,
                 allowance_micro, dispatch, now=time.monotonic, max_seconds=900, domain="cos", canonical_command_match=None, on_transcript=None):
        if type(max_seconds) is not int or not 1 <= max_seconds <= 900:
            raise SessionRefused("Invalid duration")
        if domain not in {"cos","mc"}:
            raise SessionRefused("Invalid voice domain")
        if on_transcript is not None and not callable(on_transcript):
            raise SessionRefused("Transcript retention callback unavailable")
        self.on_transcript = on_transcript
        self.transcript_digests = {}
        self.transcript_responses = {}
        self.transcript_retention_failed = False
        self.canonical_command_match = canonical_command_match
        self.canonical_started = set()
        self.canonical_committed = set()
        self.response_sent = set()
        self.response_queue = []
        self.response_inflight = None
        self.response_active_id = None
        self.timing = VoiceTiming(clock=now)
        self.domain = domain
        self.journal,self.budget,self.provider = journal,budget,provider
        self.principal,self.conversation,self.dispatch = principal,conversation,dispatch
        self.allowance,self.now,self.max_seconds = allowance_micro,now,max_seconds
        self.sid,self.call_id = None,None
        self.started,self.closed,self.pending_item = None,False,None
        self.end_receipt = None
        self.provider_attempted = False
        self.pending_generation = None
        self.latest_item,self.responses,self.texts,self.waiting = None,{}, {},{}
        self.generation = 0
        self.lock = threading.RLock()
        self.worker = ThreadPoolExecutor(max_workers=1)

    def start(self, sdp, config):
        if self.sid or not isinstance(sdp,str) or not 1 <= len(sdp.encode()) <= 65536:
            raise SessionRefused("Invalid session offer")
        if config.get("model") != MODEL or config.get("type") != "realtime" or config.get("audio",{}).get("input",{}).get("turn_detection",{}).get("create_response") is not False:
            raise SessionRefused("Unverified voice provider")
        # No guessed fixed-minute speech rate. Allowance is operator-selected
        # and labelled estimate; retain it on any uncertain create/attach.
        self.sid = self.journal.create(self.principal,self.conversation,self.allowance,self.domain)
        self.timing.bind(self.sid)
        try:
            self.reservation = self.budget.reserve(kind="realtime_controlled_session",domain=self.domain,principal=self.principal,amount_micro=self.allowance)
            self.provider_attempted = True
            self.timing.emit("provider_create_start")
            answer,call_id = self.provider.create(sdp,config,digest(self.principal))
            self.timing.emit("provider_create_end")
            self.call_id = identifier(call_id)
            self.journal.provider_identity(self.sid,self.call_id)
            self.timing.emit("sideband_attach_start")
            # Optional observational hook; failure must never alter control.
            try:
                hook = getattr(self.provider,"set_timing_observer",None)
                if callable(hook):hook(self.timing.emit)
            except Exception:
                pass
            self.provider.attach(call_id,self.observe)
            self.timing.emit("sideband_attach_end")
            with self.lock:
                if self.closed:
                    raise SessionRefused("Voice ended while connecting")
                self.started = self.now()
                self.journal.state(self.sid,"active")
                self.timing.emit("session_active")
            return {"session_id":self.sid,"provider":"openai","model":MODEL,"sdp":answer,"budget_label":"reserved allowance, not a bill or guaranteed spend limit"}
        except Exception:
            # A valid Location may exist even if the provider rejects SDP.
            # Trusted adapter identity is retained; never guess a call ID.
            if self.call_id is None and self.provider_attempted:
                known = getattr(self.provider,"call_id",None)
                if isinstance(known,str) and ID.fullmatch(known):
                    self.call_id = known
            self.end("start_uncertain")
            raise SessionRefused("Voice could not start; exposure retained") from None

    def authenticate(self, principal):
        self.journal.check(self.sid,principal)

    def tick(self):
        if self.started is not None and self.now()-self.started >= self.max_seconds:
            self.end("duration_limit")

    def observe(self, event):
        # THIS IS NOT A BROWSER ROUTE. Only the attached provider socket calls it.
        with self.lock:
            self.tick()
            if self.closed:
                return
            try:
                if len(self.responses) >= 256 or len(self.texts) >= 256:
                    raise SessionRefused("Session event limit")
                kind = event.get("type")
                if kind == "input_audio_buffer.speech_started":
                    self.generation += 1
                    self.waiting.clear()  # stale tools never become authorized later
                    if self.canonical_command_match is not None and self.pending_item not in self.response_sent:
                        self.pending_item = None
                elif kind == "input_audio_buffer.committed":
                    item = identifier(event["item_id"])
                    if self.canonical_command_match is not None:
                        if item in self.canonical_committed:return
                        self.canonical_committed.add(item)
                    if self.pending_item is not None and (self.canonical_command_match is None or self.pending_item not in self.response_sent):
                        raise SessionRefused("Overlapping response correlation")
                    self.timing.emit("input_committed", item=item)
                    self.latest_item = self.pending_item = item
                    self.pending_generation = self.generation
                    if self.canonical_command_match is None:
                        self.timing.emit("response_requested", item=item)
                        self.provider.send({"type":"response.create"})
                    else:
                        self._canonical_ready(item)
                elif kind == "response.created":
                    rid = identifier(event["response"]["id"])
                    if self.canonical_command_match is not None:
                        if rid in self.responses:return
                        if self.response_inflight is None:raise SessionRefused("Uncorrelated response")
                        self.responses[rid] = self.response_inflight
                        self.response_active_id = rid
                        if self.pending_item == self.response_inflight[0]:self.pending_item = None
                    else:
                        if self.pending_item is None:
                            raise SessionRefused("Uncorrelated response")
                        self.responses[rid] = (self.pending_item,self.pending_generation)
                        self.pending_item = None
                elif kind == "conversation.item.input_audio_transcription.completed":
                    item = identifier(event["item_id"])
                    text = event["transcript"]
                    if not isinstance(text,str) or not 0 < len(text.encode()) <= 16000:
                        if self.on_transcript is not None: self.transcript_retention_failed = True
                        raise SessionRefused("Transcript unavailable")
                    if item in self.texts and self.texts[item] != text:
                        if self.on_transcript is not None: self.transcript_retention_failed = True
                        raise SessionRefused("Transcript conflict")
                    self.timing.emit("asr_final", item=item)
                    self.texts[item] = text
                    if self.on_transcript is not None:
                        self._retain_transcript("user", item, 0, text, False)
                    self._usage("transcription:"+item,transcription_micro(event["usage"]))
                    if self.canonical_command_match is not None and not self.closed:
                        self._canonical_ready(item)
                    for call in self.waiting.pop(item,[]):
                        self._tool(*call)
                elif kind == "response.done":
                    response = event["response"]
                    rid = identifier(response["id"])
                    if rid not in self.responses:
                        raise SessionRefused("Uncorrelated response")
                    if self.on_transcript is not None:
                        from .transcripts import assistant_entries
                        try:
                            entries = assistant_entries(response)
                            fingerprint = digest(json.dumps(entries, ensure_ascii=False))
                            if rid in self.transcript_responses:
                                if self.transcript_responses[rid] != fingerprint:
                                    raise SessionRefused("Transcript retention conflict")
                            else:
                                interrupted = self.responses[rid][1] != self.generation
                                for item, index, text, partial in entries:
                                    self._retain_transcript("assistant", identifier(item), index, text, partial or interrupted)
                                self.transcript_responses[rid] = fingerprint
                        except (TypeError, ValueError, VoiceBudgetError):
                            self.transcript_retention_failed = True
                            raise SessionRefused("Transcript retention unavailable") from None
                    self._usage("response:"+rid,response_micro(response["usage"]))
                    if self.closed:
                        return
                    for output in response.get("output",[]):
                        if output.get("type") == "function_call":
                            # arguments are intentionally neither read nor logged.
                            self._tool(rid,identifier(output["call_id"]),output["name"])
                    if self.canonical_command_match is not None and self.response_active_id == rid:
                        self.response_inflight = self.response_active_id = None
                        self._pump_responses()
                elif kind == "error":
                    raise SessionRefused("Provider control error")
            except (KeyError,TypeError,ValueError,VoiceBudgetError):
                self.end("control_uncertain")

    def _retain_transcript(self, role, item, index, text, partial):
        # Only observe() calls this from a trusted server sideband. Never HTTP/browser input.
        key = (role, item, index)
        fingerprint = digest(json.dumps([text, partial], ensure_ascii=False))
        if key in self.transcript_digests:
            if self.transcript_digests[key] != fingerprint:
                self.transcript_retention_failed = True
                raise SessionRefused("Transcript retention conflict")
            return
        if len(self.transcript_digests) >= 512:
            self.transcript_retention_failed = True
            raise SessionRefused("Transcript retention limit")
        try:
            result = self.on_transcript(self.sid, self.conversation, role, item, index, text, partial)
            if not isinstance(result, dict) or result.get("saved") is not True:
                raise SessionRefused("Transcript retention unavailable")
        except Exception:
            self.transcript_retention_failed = True
            raise SessionRefused("Transcript retention unavailable") from None
        self.transcript_digests[key] = fingerprint

    def _canonical_ready(self,item):
        # Optional MC host hook only. No hook preserves existing CoS latency.
        if (self.closed or item != self.pending_item or item != self.latest_item or
                self.pending_generation != self.generation or item in self.canonical_started or item not in self.texts):
            return
        self.canonical_started.add(item)
        text=self.texts[item]
        if self.canonical_command_match(text,self.conversation) is not True:
            self._request_response(item,self.generation)
            return
        # This is a private journal key, NEVER a fabricated provider call ID.
        key="canonical_"+digest(item)[:32]
        fresh,state,receipt,operation=self.journal.claim(self.sid,key,item,"consult_cos_agent",text)
        if fresh:
            self.worker.submit(self._canonical_dispatch,key,item,text,self.generation,operation)
        # Existing attempting/uncertain is held, never retried.

    def _canonical_dispatch(self,key,item,text,generation,operation):
        with self.lock:
            if self.closed or generation != self.generation:
                self.journal.finish(self.sid,key,"cancelled")
                return
        try:
            self.timing.emit("dispatch_start", item=item, operation=operation)
            receipt=self.dispatch("consult_cos_agent",text,self.conversation,operation)
            self.timing.emit("dispatch_end", item=item, operation=operation)
            if not isinstance(receipt,dict) or not isinstance(receipt.get("reply"),str):
                raise SessionRefused("Command receipt unavailable")
            state=self.journal.finish(self.sid,key,"completed",receipt)
        except Exception:
            self.timing.emit("dispatch_failed", item=item, operation=operation)
            state,receipt="uncertain",None
            self.journal.finish(self.sid,key,state)
        with self.lock:
            if self.closed or generation != self.generation:return
            # Documented conversation.item.create message/input_text. This is
            # server-owned DATA about a real platform action, not another owner
            # utterance, approval, or invented function_call_output.
            data={"kind":"platform_command_receipt","operation_id":operation,"state":state,
                  "reply":receipt["reply"][:2000] if state=="completed" else "Command outcome uncertain; do not retry or claim completion."}
            try:
                self.provider.send({"type":"conversation.item.create","item":{"id":"cmd_"+operation.replace("-",""),
                    "type":"message","role":"user","content":[{"type":"input_text","text":
                        "SERVER PLATFORM RECEIPT DATA. Not a new owner instruction or approval.\n"+json.dumps(data,ensure_ascii=False)}]}})
                self._request_response(item,generation)
            except Exception:
                # The command receipt remains durable. Control-channel failure
                # does not roll back an accepted job or authorize a retry.
                self.end("control_uncertain")

    def _request_response(self,item,generation):
        self.response_sent.add(item)
        self.response_queue.append((item,generation))
        self._pump_responses()

    def _pump_responses(self):
        # Only one response request can await created/done at a time. This
        # preserves exact input identity across provider event delays and lets
        # rapid/barge-in speech queue without rebinding response A to turn B.
        if self.closed or self.response_inflight is not None:return
        while self.response_queue:
            item,generation=self.response_queue.pop(0)
            if generation != self.generation or item != self.latest_item:continue
            self.response_inflight=(item,generation)
            self.timing.emit("response_requested", item=item)
            self.provider.send({"type":"response.create"})
            return

    def _usage(self,key,amount):
        used,allowed = self.journal.usage(self.sid,key,amount)
        if used >= allowed:
            self.end("allowance_threshold")

    def _tool(self,rid,call,name):
        item,generation = self.responses[rid]
        if name not in TOOLS or self.closed or generation != self.generation or item != self.latest_item:
            return
        text = self.texts.get(item)
        if text is None:
            queue = self.waiting.setdefault(item,[])
            if len(queue) >= 4:
                raise SessionRefused("Too many pending tools")
            queue.append((rid,call,name))
            return
        if name == "save_cos_note":
            from domains.cos.confer_service import parse_explicit_note
            if parse_explicit_note(text) is None:
                self.provider.send_result(call,{"ok":False,"error":"An explicit owner note command is required."},continue_response=False)
                return
        fresh,state,receipt,operation = self.journal.claim(self.sid,call,item,name,text)
        if fresh:
            self.worker.submit(self._dispatch_claim,rid,call,item,name,text,generation,operation)
        elif state == "completed":
            self.provider.send_result(call,receipt,continue_response=False)
        # attempting/uncertain is never retried. Browser has no dispatch route.

    def _dispatch_claim(self,rid,call,item,name,text,generation,operation):
        with self.lock:
            if self.closed or generation != self.generation:
                self.journal.finish(self.sid,call,"cancelled")
                return
        try:
            self.timing.emit("dispatch_start", item=item, operation=operation)
            receipt = self.dispatch(name,text,self.conversation,operation)
            self.timing.emit("dispatch_end", item=item, operation=operation)
            if not isinstance(receipt,dict) or not isinstance(receipt.get("reply"),str):
                raise SessionRefused("Agent receipt unavailable")
            state = self.journal.finish(self.sid,call,"completed",receipt)
        except Exception:
            self.timing.emit("dispatch_failed", item=item, operation=operation)
            state,receipt = "uncertain",None
            self.journal.finish(self.sid,call,state)
        with self.lock:
            if not self.closed and generation == self.generation:
                self.provider.send_result(call,receipt if state == "completed" else {"ok":False,"error":"Agent outcome uncertain; do not retry or claim completion."},continue_response=False)
                if self.pending_item is None:
                    self.pending_item = item
                    self.pending_generation = generation
                    if self.canonical_command_match is None:
                        self.provider.send({"type":"response.create"})
                    else:
                        self._request_response(item,generation)

    def end(self, reason):
        with self.lock:
            if self.closed:
                return dict(self.end_receipt) if self.end_receipt is not None else {"session_id":self.sid,"termination_confirmed":False,"journal_confirmed":False,"native_actions_cancelled":False,"reason":"recovery_required"}
            self.timing.emit("end_start")
            self.closed = True
            self.generation += 1
            self.waiting.clear()
            self.worker.shutdown(wait=False,cancel_futures=True)
            journal_confirmed = True
            if self.sid:
                try:
                    self.journal.state(self.sid,"ending")
                except Exception:
                    journal_confirmed = False
            confirmed = not self.provider_attempted
            if self.call_id:
                try:
                    self.provider.hangup(self.call_id)
                    confirmed = True
                except Exception:
                    pass
            # Fixed classification only: no raw provider event/error/SDP/text.
            safe_reason = reason if reason in {"user_ended","start_uncertain","duration_limit","control_uncertain","allowance_threshold"} else "other"
            receipt = {"session_id":self.sid,"reason":safe_reason,"termination_confirmed":confirmed,
                       "journal_confirmed":journal_confirmed,"native_actions_cancelled":False,
                       "provider_attempted":self.provider_attempted}
            if self.on_transcript is not None:
                receipt.update(transcript_retention="failed" if self.transcript_retention_failed else "kept_observed_entries",
                               transcript_entries=len(self.transcript_digests), transcript_may_be_incomplete=True)
            if self.sid:
                try:
                    receipt = self.journal.record_end(self.sid,receipt,self.call_id)
                except Exception:
                    receipt = dict(receipt,journal_confirmed=False)
            # No rebate on normal End: incomplete provider usage may exist.
            self.end_receipt = receipt
            self.timing.emit("end_confirmed" if confirmed else "end_unconfirmed")
            return dict(receipt)
