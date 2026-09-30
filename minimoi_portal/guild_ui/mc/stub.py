"""The scripted stub (MC spec v0.5 §2.3, H2): a fixed reply, ``backend_kind``
"stub", no citations and no blocks. Never an agent, never a model call, and
never a fallback for "unavailable". Its kept rows carry the stub's author
(backend.reply_author), so they read as the stub forever.

It also streams (streaming spec v0.2 §5, the scripted streaming stub): a few
deltas with short delays, a ``stop`` and a usage chunk labelled as the stub's
(``source="stub"``, never written to the usage store). It is used for the
transport probe and the browser checks. ``stream_text`` and ``stream_delay_s``
can be set on an instance by a test; ``stream_script`` replaces the events."""
from __future__ import annotations

import re
import time

from .backend import Health, MasterCraftsmanBackend, TurnResult
from .stream import Delta, Failure, Finish, Usage

STUB_REPLY = ("Stub reply (scripted, not Master Craftsman): your note is kept. "
              "No agent read it and nothing was checked.")


class StubBackend(MasterCraftsmanBackend):
    kind = "stub"
    supports_streaming = True
    stream_text = STUB_REPLY
    stream_delay_s = 0.08
    stream_script = None          # a list of events, or None for stream_text in word-sized deltas

    def health(self) -> Health:
        return Health("stub")

    def turn(self, req, cancel=None) -> TurnResult:
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        return TurnResult("answered", self.kind, text=STUB_REPLY)

    def stream_turn(self, req, cancel=None):
        return self._stream(cancel)

    def _events(self):
        if self.stream_script is not None:
            return list(self.stream_script)
        pieces = re.findall(r"\S+\s*|\s+", self.stream_text)
        return [Delta(p) for p in pieces] + [Finish("stop"), Usage(len(self.stream_text) // 4 or 1, len(pieces), "stub")]

    def _stream(self, cancel):
        for event in self._events():
            if cancel is not None and cancel.wait(self.stream_delay_s if isinstance(event, Delta) else 0):
                yield Failure("stopped", "cancelled")
                return
            if isinstance(event, tuple) and event[0] == "sleep":      # ("sleep", seconds) in a test script
                if cancel is not None and cancel.wait(event[1]):
                    yield Failure("stopped", "cancelled")
                    return
                if cancel is None:
                    time.sleep(event[1])
                continue
            if cancel is None and isinstance(event, Delta):
                time.sleep(self.stream_delay_s)
            yield event
