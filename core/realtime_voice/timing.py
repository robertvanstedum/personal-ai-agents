"""Best-effort stage metadata; never accepts audio, prompts or provider errors."""
import hashlib
import logging
import math
import re
import time
import uuid

LOG = logging.getLogger('minimoi.voice_timing')
STAGES = frozenset({
    'provider_create_start','provider_create_end','sideband_attach_start',
    'sideband_attach_end','session_active','input_committed','asr_final',
    'dispatch_start','dispatch_end','dispatch_failed','response_requested',
    'provider_first_audio_observed','end_start','end_confirmed','end_unconfirmed',
    'native_start','native_end','native_failed','context_start','context_end','context_failed',
    'gateway_request_start','gateway_request_end','gateway_request_failed',
    'routing_receipt_start','routing_receipt_end','routing_receipt_failed',
})
CORRELATION = re.compile(r'(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\Z')


class VoiceTiming:
    """Observational only. Bounded dedup; clocks/logging failures are ignored."""
    def __init__(self, *, clock=time.monotonic, correlation=None, parent=None):
        self.clock = clock
        self.correlation = uuid.uuid4().hex
        self.bind(correlation)
        self.parent = parent if isinstance(parent,str) and CORRELATION.fullmatch(parent) else "-"
        self.seen = {}
        try:self.origin = float(clock())
        except Exception:self.origin = None

    def bind(self, correlation):
        if isinstance(correlation,str) and CORRELATION.fullmatch(correlation):
            self.correlation = correlation

    def emit(self, stage, *, item=None, operation=None):
        try:
            if self.origin is None or stage not in STAGES or len(self.seen) >= 2048:return
            if item is not None and (not isinstance(item,str) or not 1<=len(item)<=160):return
            token = hashlib.sha256(item.encode()).hexdigest()[:16] if item is not None else '-'
            operation = operation if isinstance(operation,str) and CORRELATION.fullmatch(operation) else "-"
            key = (stage,token)
            if key in self.seen:return
            now = float(self.clock())
            if not math.isfinite(now) or not math.isfinite(self.origin):return
            elapsed = max(0,round((now-self.origin)*1000))
            committed = self.seen.get(('input_committed',token))
            asr_wait = max(0,elapsed-committed) if stage=='asr_final' and committed is not None else -1
            self.seen[key] = elapsed
            LOG.info('voice_timing trace=%s stage=%s item_hash=%s elapsed_ms=%d asr_after_commit_ms=%d operation=%s parent=%s',
                     self.correlation,stage,token,elapsed,asr_wait,operation,self.parent)
        except Exception:
            pass
