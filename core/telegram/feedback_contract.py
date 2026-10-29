"""Parse Telegram feedback without runtime imports, reads or side effects."""
from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

ACTIONS = frozenset({"like", "dislike", "save"})


def field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def article_url(message):
    urls = set()
    text = field(message, "text", "")
    if not isinstance(text, str):
        raise ValueError("Request a fresh briefing: article identity is unavailable")
    entities = field(message, "entities", ()) or ()
    if not isinstance(entities, (list, tuple)):
        raise ValueError("Invalid article links")
    for entity in entities:
        kind = field(entity, "type")
        if kind == "text_link":
            url = field(entity, "url")
        elif kind == "url":
            offset, length = field(entity, "offset"), field(entity, "length")
            if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
                raise ValueError("Invalid article link")
            encoded = text.encode("utf-16-le")
            if (offset + length) * 2 > len(encoded):
                raise ValueError("Invalid article link")
            try:
                url = encoded[offset * 2:(offset + length) * 2].decode("utf-16-le")
            except UnicodeError as exc:
                raise ValueError("Invalid article link") from exc
        else:
            continue
        if not isinstance(url, str) or any(char.isspace() for char in url):
            raise ValueError("Invalid article link")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ValueError("Invalid article link") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Invalid article link")
        urls.add(url)
    if len(urls) != 1:
        raise ValueError("Request a fresh briefing: article identity is unavailable or ambiguous")
    return urls.pop()


@dataclass(frozen=True)
class FeedbackRequest:
    action: str
    rank: int
    expected_url: str
    key: tuple


def parse_callback(query):
    data = field(query, "data")
    if not isinstance(data, str):
        raise ValueError("Invalid button data")
    match = re.fullmatch(r"(like|dislike|save):([0-9]{1,12})", data)
    if match is None or int(match[2]) < 1:
        raise ValueError("Invalid button data")
    message = field(query, "message")
    message_id = field(message, "message_id")
    chat_id = field(field(message, "chat"), "id")
    if type(message_id) is not int or type(chat_id) is not int:
        raise ValueError("Invalid feedback message")
    action, rank, url = match[1], int(match[2]), article_url(message)
    return FeedbackRequest(action, rank, url, (chat_id, message_id, action, rank, url))
