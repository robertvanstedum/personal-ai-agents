"""Pure admission rules for owner-only private Telegram interactions.

Entrypoints supply their configured owner. This module never reads secrets,
configuration or a domain runtime; transport authenticity is a separate check.
"""
from functools import wraps
import re
from collections.abc import Mapping


def owner_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    return int(value) if re.fullmatch(r"[1-9][0-9]{0,19}", value) else None


def _field(value, name, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def admits(sender, chat, configured_owner):
    expected = owner_id(configured_owner)
    return bool(
        expected is not None
        and _field(sender, "is_bot", False) is False
        and _field(chat, "type") == "private"
        and type(_field(sender, "id")) is int
        and type(_field(chat, "id")) is int
        and _field(sender, "id") == expected == _field(chat, "id")
    )


def admits_update(update, configured_owner):
    message = getattr(update, "message", None)
    query = getattr(update, "callback_query", None)
    if query is not None:
        return admits(getattr(query, "from_user", None),
                      getattr(getattr(query, "message", None), "chat", None), configured_owner)
    return message is not None and admits(getattr(message, "from_user", None),
                                          getattr(message, "chat", None), configured_owner)


def admits_message(message, configured_owner):
    return isinstance(message, Mapping) and admits(message.get("from"), message.get("chat"), configured_owner)


def admits_callback(query, configured_owner):
    return (isinstance(query, Mapping) and isinstance(query.get("message"), Mapping)
            and admits(query.get("from"), query["message"].get("chat"), configured_owner))


def owner_only(resolve_owner):
    """Guard a polling handler, including when called outside its dispatcher."""
    def decorate(handler):
        @wraps(handler)
        async def guarded(update, context):
            if not admits_update(update, resolve_owner()):
                return None
            return await handler(update, context)
        return guarded
    return decorate
