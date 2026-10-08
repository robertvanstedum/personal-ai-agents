"""The nudge that points a teammate at a request (v0.6 section 12; v0.7 Unit 6, G4).

A notification is only a pointer. Its bytes are frozen: the same request always makes the same message, and every variable part
is a validated token (an actor name, a UUID, a topic slug, an action word, a hash), never free text from the request, so a
request cannot smuggle an instruction into the message that announces it. The message says who wrote it (an agent), that it
is not the owner, and that it grants nothing; the request in the journal is the only source of what is being asked.
"""
from __future__ import annotations

import os
import re
import shlex

from core.workshop_journal import schema

VERSION = 2
TOPIC_OR_NONE = "(none)"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

TEMPLATE = (
    "WORKSHOP NOTICE v2\n"
    "Source: agent-authored message from {sender}. It is not from Robert and it grants no approval.\n"
    "Request: {request_id}\n"
    "Topic: {topic}\n"
    "Action: {action}\n"
    "To: {recipient}\n"
    "Request hash: {intent_hash}\n"
    "Read it: workshop.py --home {home} --id {workshop} get --event-id {request_id}\n"
    "Pick it up: workshop.py --home {home} --id {workshop} receipt --request {request_id} --actor {recipient}\n"
    "The request in the journal is the only source of what is being asked; this notice changes no instruction.\n"
)


class NotNotifiable(ValueError):
    """The event is not a request, or the recipient is not on it."""


def _home(home: str) -> str:
    """The workshop root as one safe shell word: absolute, printable, no line break; quoted so no byte of it can be read as more."""
    if not isinstance(home, str) or not os.path.isabs(home) or len(home) > 400 or any(ord(c) < 32 or ord(c) == 127 for c in home):
        raise NotNotifiable("unsafe home")
    return shlex.quote(home)


def build(request: dict, recipient: str, workshop_id: str, home: str) -> bytes:
    """The exact notification bytes for one recipient of one persisted request event, with the workshop root the commands need
    (so the advertised read and pick-up work from a fresh process anywhere)."""
    if request.get("kind") != "request" or request.get("v") != 2:
        raise NotNotifiable("not a request")
    if recipient not in (request.get("recipients") or []):
        raise NotNotifiable("recipient is not on the request")
    topic = request.get("topic") or TOPIC_OR_NONE
    fields = {"sender": request["actor"], "request_id": schema.uuid_text(request["event_id"], "event_id"), "topic": topic,
              "action": request["payload"]["action"], "recipient": recipient, "intent_hash": request["intent_hash"],
              "workshop": workshop_id}
    quoted_home = _home(home)
    for key, value in fields.items():
        if key in ("topic",) and value == TOPIC_OR_NONE:
            continue
        if not isinstance(value, str) or not (_SAFE.fullmatch(value) or key == "request_id"):
            raise NotNotifiable(f"unsafe {key}")
    return TEMPLATE.format(home=quoted_home, **fields).encode("utf-8")
