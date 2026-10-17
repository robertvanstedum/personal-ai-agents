"""Draft transport for existing shared OpenAI media; no model/key fallback.
No automatic HTTP retries. Never logs events, SDP, secrets, or raw exceptions.
The existing SDK connect iterator supplies typed provider sideband events.
A successful hangup is a termination acknowledgment, NOT final billing evidence.
"""
import logging
import json
import re
import threading

from .controlled_session import SessionRefused, MODEL, TOOLS

class _NoRawRealtimeTelemetry(logging.Filter):
    def filter(self,record):
        return not str(record.msg).startswith("Received websocket message:")


LOCATION = re.compile(r"/v1/realtime/calls/(rtc_[A-Za-z0-9_-]{1,140})\Z")


class OpenAIControlledProvider:
    def __init__(self, client):
        # Integrator constructs OpenAI(api_key=existing_secret,max_retries=0,
        # timeout=10). No new secret store/credential or provider stack.
        logging.getLogger("openai.resources.realtime.realtime").addFilter(_NoRawRealtimeTelemetry())
        self.client,self.socket,self.manager = client,None,None
        self.call_id = None
        self.send_lock = threading.Lock()
        self._timing_observer = None
        self._first_audio_observed = False

    def set_timing_observer(self, callback):
        self._timing_observer = callback

    def _observe_first_audio(self):
        if self._first_audio_observed:return
        self._first_audio_observed = True
        try:
            if self._timing_observer is not None:
                self._timing_observer("provider_first_audio_observed")
        except Exception:
            pass

    def create(self,sdp,config,safety_identifier):
        # The unified WebRTC API requires multipart SDP + JSON session fields.
        # The installed generated calls.create uses a JSON body, so explicitly
        # use the existing SDK transport (auth, timeout and no-retry policy).
        from openai._legacy_response import HttpxBinaryResponseContent
        response = self.client.post('/realtime/calls',
            cast_to=HttpxBinaryResponseContent,
            body={'sdp':sdp,'session':json.dumps(config)},
            options={'headers':{'Content-Type':'multipart/form-data',
                                'Accept':'application/sdp',
                                'OpenAI-Safety-Identifier':safety_identifier},
                     'timeout':10})
        location = response.response.headers.get("location","")
        match = LOCATION.fullmatch(location)
        if not match:
            raise SessionRefused("Provider call identity unavailable")
        self.call_id = match.group(1)
        answer = response.text
        if not isinstance(answer,str) or not 0 < len(answer.encode()) <= 65536:
            raise SessionRefused("Provider SDP unavailable")
        return answer,self.call_id

    def attach(self,call_id,observe):
        if call_id != self.call_id:
            raise SessionRefused("Provider call mismatch")
        self.manager = self.client.realtime.connect(call_id=call_id)
        self.socket = self.manager.__enter__()
        # A sideband attaches to an existing call. Explicitly request effective
        # configuration; don't require replay of a prior session.created event.
        # Empty update changes no model, voice, instructions or permissions.
        timeout = threading.Timer(10,self.socket.close)
        timeout.daemon = True
        timeout.start()
        try:
            self.send({"type":"session.update","session":{"type":"realtime"}})
            for position in range(2):  # optional created, then required updated
                event = self.socket.recv().model_dump(mode="json")
                if not isinstance(event,dict):
                    raise SessionRefused("Provider control not ready")
                if event.get("type") == "session.created" and position == 0:
                    continue
                if event.get("type") != "session.updated":
                    raise SessionRefused("Provider control not ready")
                session = event.get("session")
                if not isinstance(session,dict):
                    raise SessionRefused("Provider control not ready")
                audio = session.get("audio")
                inp = audio.get("input") if isinstance(audio,dict) else None
                vad = inp.get("turn_detection") if isinstance(inp,dict) else None
                tools = session.get("tools")
                if (session.get("type") != "realtime" or session.get("model") != MODEL
                        or not isinstance(vad,dict) or vad.get("create_response") is not False
                        or not isinstance(tools,list) or len(tools) != len(TOOLS)
                        or any(not isinstance(t,dict) or t.get("type") != "function"
                               or not isinstance(t.get("name"),str) for t in tools)
                        or {t["name"] for t in tools} != TOOLS):
                    raise SessionRefused("Provider control not ready")
                break
            else:
                raise SessionRefused("Provider control not ready")
        finally:
            timeout.cancel()
        def read():
            try:
                for message in self.socket:
                    event = message.model_dump(mode="json")
                    # Raw audio is ignored, never retained/printed.
                    if event.get("type") in {"response.audio.delta","response.output_audio.delta"}:
                        self._observe_first_audio()
                    else:
                        observe(event)
                observe({"type":"error"})
            except Exception:
                observe({"type":"error"})
        threading.Thread(target=read,name="voice-sideband",daemon=True).start()

    def send(self,event):
        with self.send_lock:
            self.socket.send(event)

    def send_result(self,call_id,result,*,continue_response=False):
        self.send({"type":"conversation.item.create","item":{"type":"function_call_output","call_id":call_id,"output":json.dumps(result,ensure_ascii=False)}})
        if continue_response:
            raise SessionRefused("Continuation belongs to controlled session")

    def hangup(self,call_id):
        if call_id != self.call_id:
            raise SessionRefused("Provider call mismatch")
        # Hang up before closing sideband. No per-client session.close invention.
        try:
            self.client.realtime.calls.hangup(call_id,timeout=10)
        except Exception:
            logging.getLogger(__name__).warning("voice_control phase=provider_hangup result=unconfirmed")
            self._close_sideband()
            raise
        # The provider acknowledged hangup. Local sideband cleanup is separate:
        # its failure must not turn a confirmed hangup into an uncertain one.
        self._close_sideband()

    def _close_sideband(self):
        self.sideband_cleanup_confirmed = True
        if self.manager:
            try:
                self.manager.__exit__(None,None,None)
            except Exception:
                self.sideband_cleanup_confirmed = False
                logging.getLogger(__name__).warning("voice_control phase=sideband_cleanup result=failed")
