"""Robert's approvals by Telegram reply: what counts, and the receipt that proves it.

An answer counts only when ALL hold: it is a plain message (not edited, forwarded or sent via a bot) in Robert's own
private chat with the approvals bot, from his own user id, and it is a reply to the exact message that carried the
decision. Anything else, including text an agent relays or a page quotes, is not an answer; the bot says so.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

# An approval is the whole short reply (a condition after "yes" would be an instruction nobody checks); a decline may
# carry his reasons, since saying no is always safe.
_YES = re.compile(r"^(?:yes|yep|yeah|approve|approved|ok|okay|go\s+ahead|do\s+it|ship\s+it)"
                  r"(?:[,.!]?\s*(?:please|thanks|thank\s+you|go\s+ahead|approved))?\s*[.!👍]*$", re.I)
_NO = re.compile(r"^(?:no|nope|decline|declined|hold|stop|wait|not\s+now|don['’]t)\b", re.I)
# One code at a time, for a batched message answered differently per decision (R7.4-c): the Guild command's grammar.
_CODE_COMMAND = re.compile(r"^(?P<word>approve|approved|yes|ok|no|decline|declined|hold)[,:]?\s+(?:decision\s+)?"
                           r"(?P<code>D-?\d{1,9})\s*[.!]?$", re.I)


def parse_answer(text):
    """'approved' | 'declined' | None. Robert's exact words are kept in the receipt either way."""
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        return None
    t = text.strip()
    if _NO.match(t):
        return "declined"
    if _YES.match(t):
        return "approved"
    return None


def parse_code_command(text):
    """('approved' | 'declined', 'D-12') for the whole short reply naming one code ("approve D-12", "no D-12"), else
    None. Used only for a reply to a batched message, to answer one of its decisions differently."""
    if not isinstance(text, str) or len(text) > 60:
        return None
    m = _CODE_COMMAND.match(text.strip())
    if not m:
        return None
    answer = "declined" if m.group("word").lower() in ("no", "decline", "declined", "hold") else "approved"
    return answer, "D-" + m.group("code").upper().lstrip("D-")


def verify(update, *, owner_chat_id: int, owner_user_id: int, bot_id: int | None = None):
    """(reply_to_message_id, message) when ``update`` is a message Robert himself sent as a reply in his private chat;
    else (None, reason). Never trusts text: only Telegram's own fields."""
    msg = update.get("message") if isinstance(update, dict) else None
    if not isinstance(msg, dict):
        return None, "not_a_message"            # edited_message, channel posts, callbacks: never an answer
    chat, sender, reply = msg.get("chat") or {}, msg.get("from") or {}, msg.get("reply_to_message")
    if chat.get("id") != owner_chat_id or chat.get("type") != "private":
        return None, "not_owner_chat"
    if sender.get("id") != owner_user_id or sender.get("is_bot") is not False:
        return None, "not_owner"
    if any(k in msg for k in ("forward_origin", "forward_from", "forward_from_chat", "forward_date", "via_bot",
                              "is_automatic_forward")):
        return None, "forwarded"
    if not isinstance(reply, dict) or type(reply.get("message_id")) is not int:
        return None, "not_a_reply"
    replied_from = reply.get("from") or {}
    if replied_from.get("is_bot") is not True or (bot_id is not None and replied_from.get("id") != bot_id):
        return None, "not_a_reply_to_the_bot"
    if not isinstance(msg.get("text"), str):
        return None, "no_text"
    return reply["message_id"], msg


def receipt(update, msg, decision):
    """The append-only proof of Robert's answer: Telegram's ids and his exact words, bound to the decision's text."""
    return {"channel": "telegram", "update_id": update["update_id"], "message_id": msg["message_id"],
            "reply_to_message_id": msg["reply_to_message"]["message_id"], "chat_id": msg["chat"]["id"],
            "from_id": msg["from"]["id"], "date": msg.get("date"), "text": msg["text"],
            "decision_sha256": decision["text_sha256"]}


def decision_message(decision):
    who = decision["requester"]
    by = {"claude-code": "Claude Code", "codex": "Codex", "mc": "Master Craftsman"}.get(who.get("agent"), "An agent")
    where = ", ".join(x for x in (f"session {who.get('session', '')[:8]}" if who.get("session") else "",
                                  f"branch {who['branch']}" if who.get("branch") else "") if x)
    return (f"Decision {decision['code']} waiting on you\nAsked by {by}{' (' + where + ')' if where else ''}\n\n"
            f"{decision['text']}\n\nReply to THIS message: yes to approve, no to decline. "
            f"Or in the Guild chat: approve {decision['code']}.")


def runtime_message(decision):
    """A runtime decision that arrives alone (a solo class, R7.4-c: an unknown resolution, a reassignment, a
    cross-vendor review admission), in the authority's own words, which say what yes and no do."""
    return (f"Decision {decision['code']} waiting on you, on its own\n\n{decision['text']}\n\n"
            f"Reply to THIS message: yes or no. Or in the Guild chat: approve {decision['code']} / no {decision['code']}.")


def batch_message(decisions):
    """Routine runtime decisions waiting at the same sweep, in one message (R7.4-c, at most five), each in full words.
    One "yes" (the whole short reply) approves every decision listed; "no" declines them all; one code at a time
    ("approve D-12", "no D-13") answers them differently."""
    codes = [d["code"] for d in decisions]
    n = len(decisions)
    body = "\n\n".join(f"{d['code']}: {d['text']}" for d in decisions)
    return (f"Decisions {', '.join(codes)} waiting on you, together\n\n{body}\n\n"
            f"Reply to THIS message: yes approves all {n} listed, no declines all {n}. To answer them differently, "
            f"reply with one code at a time (approve {codes[0]}, no {codes[-1]}), or in the Guild chat.")


class Bot:
    """The approvals bot's only two calls. The token stays in this object; errors never echo the URL."""
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token, *, opener=None):
        if not isinstance(token, str) or not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,64}", token):
            raise ValueError("telegram_token_invalid")
        self._token = token
        self.id = int(token.split(":", 1)[0])
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def _call(self, method, payload, timeout):
        req = urllib.request.Request(self.API.format(token=self._token, method=method),
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with self._open(req, timeout=timeout) as r:
                body = json.loads(r.read(1_000_000))
        except Exception as exc:  # never the URL: it holds the token
            raise RuntimeError(f"telegram_{method}_failed:{type(exc).__name__}") from None
        if not body.get("ok"):
            raise RuntimeError(f"telegram_{method}_refused")
        return body["result"]

    def send(self, chat_id, text, *, reply_to=None):
        payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if reply_to:
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self._call("sendMessage", payload, 20)["message_id"]

    def updates(self, offset, *, wait=25):
        return self._call("getUpdates", {"offset": offset, "timeout": wait, "allowed_updates": ["message"]}, wait + 15)
