"""Bounded terminal control response, separate from user-visible answer text.

Parsing is NOT permission to dispatch. The host must also prove this exact run
finished without tools, retain the live turn claim, and atomically retire the
old run before granting a continuation any tool authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

MARKER = "[[MINIMOI_DEPTH_V1]]\n"
MAX_CONTROL_BYTES = 1024
_NONCE = re.compile(r"[0-9a-f]{32}")


class InvalidDepthControl(ValueError):
    """Recognized control framing was malformed; never retry it as a turn."""


@dataclass(frozen=True)
class DepthRequest:
    nonce: str
    reason: str


def instruction(nonce: str) -> str:
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise ValueError("invalid depth nonce")
    return (
        "For this turn only, choose the reasoning depth before composing an answer. "
        "Use deeper reasoning for substantive design decisions, diagnosis across components, "
        "or recommendations that require comparing alternatives, testing assumptions, or "
        "handling interacting failure and recovery cases. Recognizing a familiar pattern "
        "or being able to give a short answer does not replace that analysis. "
        "Use the quick route for ordinary conversation, language practice, simple questions "
        "and straightforward bounded tasks. The owner need not use special wording to get depth. "
        "This routing choice precedes routine instructions to announce a plan, read evidence "
        "or call a tool; it does not approve any action or expand tool authority. "
        "When choosing deeper reasoning, before any tool call or answer, return ONLY this control response, "
        "with a short reason (at most 240 characters). Do not call a tool first. "
        "The platform will continue the same turn; do not answer or explain the control response. "
        "Otherwise answer or use your permitted tools normally.\n"
        + MARKER + json.dumps({"kind": "request_depth", "nonce": nonce, "reason": "short reason"})
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDepthControl("duplicate control field")
        result[key] = value
    return result


class ControlBuffer:
    """Hold only a possible control prefix; ordinary prose streams immediately.

    feed() returns text safe to release as ordinary answer text. finish() returns
    a typed control request only after successful terminal completion. A control
    marker occurring after ordinary prose has begun is just text, never control.
    """

    def __init__(self, nonce: str):
        if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
            raise ValueError("invalid depth nonce")
        self.nonce = nonce
        self.pending = ""
        self.ordinary = False
        self.closed = False

    def feed(self, text: str) -> str:
        if self.closed:
            raise InvalidDepthControl("control stream already closed")
        if self.ordinary:
            return text
        self.pending += text
        # Exact framing deliberately avoids treating arbitrary JSON answers or
        # quoted snippets as instructions to route another call.
        if MARKER.startswith(self.pending):
            return ""
        if self.pending.startswith(MARKER):
            if len(self.pending.encode("utf-8")) > MAX_CONTROL_BYTES:
                self.closed = True
                raise InvalidDepthControl("control response too large")
            return ""
        self.ordinary = True
        result, self.pending = self.pending, ""
        return result

    def finish(self, *, completed: bool) -> DepthRequest | None:
        if self.closed:
            raise InvalidDepthControl("control stream already closed")
        self.closed = True
        if self.ordinary or not self.pending:
            return None
        if not completed or not self.pending.startswith(MARKER):
            raise InvalidDepthControl("control response did not complete")
        try:
            value = json.loads(self.pending[len(MARKER):], object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise InvalidDepthControl("invalid terminal control response") from exc
        if (not isinstance(value, dict) or set(value) != {"kind", "nonce", "reason"}
                or value["kind"] != "request_depth" or value["nonce"] != self.nonce
                or not isinstance(value["reason"], str) or not value["reason"].strip()
                or len(value["reason"]) > 240
                or any(ord(c) < 32 for c in value["reason"])):
            raise InvalidDepthControl("invalid terminal control fields")
        return DepthRequest(self.nonce, value["reason"].strip())
