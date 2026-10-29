"""
owner_lock.py — the system, command and feedback bots answer only Robert.

Telegram lets anyone who finds a bot message it, and has no allow-list of its
own, so each bot checks every update itself before a handler runs.

The check compares the chat the update came from with the owner's chat id,
the same id the German handlers have always compared (message.chat_id). In a
private chat that id is also the sender's user id; groups and channels have
negative ids, so they never match.

A stranger gets silence. A stranger's button tap is answered with nothing, so
their client stops spinning, and nothing else happens. Each refusal logs one
line with the sender's numeric id, never a name or text. An owner id that is
missing or not a positive integer refuses everyone (fail closed).
"""
from __future__ import annotations

import functools
import logging
import re

log = logging.getLogger(__name__)

_OWNER_ID = re.compile(r"[1-9][0-9]{0,19}")


def valid_owner_id(value) -> int | None:
    """The owner's chat id as a positive int, or None when it is unset or malformed."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and _OWNER_ID.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _numeric(value):
    return value if type(value) is int else "unknown"


def allows(owner_id, chat_id, sender_id, where: str) -> bool:
    """True when chat_id is the owner's chat; otherwise one refusal log line and False."""
    owner = valid_owner_id(owner_id)
    if owner is None:
        log.warning("telegram %s: refused sender %s, owner chat id is not configured",
                    where, _numeric(sender_id))
        return False
    if type(chat_id) is int and chat_id == owner:
        return True
    log.warning("telegram %s: refused non-owner sender %s", where, _numeric(sender_id))
    return False


def owner_only(owner_id):
    """Decorator for a python-telegram-bot handler: run it only for the owner's chat.

    owner_id is a callable returning the owner's chat id. It is read on every
    update, so each bot's own constant stays the single source.
    """
    def decorate(handler):
        @functools.wraps(handler)
        async def wrapped(update, context):
            try:
                owner = owner_id()
            except Exception as e:
                log.warning("telegram %s: could not read the owner chat id (%s)",
                            handler.__name__, type(e).__name__)
                owner = None
            chat = getattr(update, "effective_chat", None)
            user = getattr(update, "effective_user", None)
            sender = getattr(user if user is not None else chat, "id", None)
            if allows(owner, getattr(chat, "id", None), sender, handler.__name__):
                return await handler(update, context)
            query = getattr(update, "callback_query", None)
            if query is not None:
                try:
                    await query.answer()
                except Exception as e:
                    log.warning("telegram %s: could not answer a refused button tap (%s)",
                                handler.__name__, type(e).__name__)
            return None
        wrapped.owner_locked = True
        return wrapped
    return decorate
