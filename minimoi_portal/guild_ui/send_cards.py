"""Send cards for #618. A message that asks to tell an agent something becomes a card with exact words.
Nothing is sent until the owner replies yes and the card id. edit S-n replaces the words and issues a new card.
The approval is bound to one card id and one exact text (router design v0.2 §7, Q9)."""
from __future__ import annotations

import json
import os
import re
import secrets

from . import agent_ask as ask

_AGENT = r"(?:grok|claude code|claude|codex)"
_TWO = re.compile(
    r"\bto\s+(?P<a>" + _AGENT + r")\b(?P<ta>.*?)\band\s+to\s+(?P<b>" + _AGENT + r")\b(?P<tb>.*?)\s*$",
    re.I | re.S)
_ONE = re.compile(
    r"\b(?:tell|ask|message|contact)\s+(?P<agent>" + _AGENT + r")\b(?:\s+to)?\s+(?P<body>.+?)\s*$",
    re.I | re.S)
_IT = re.compile(r"^(?:please\s+)?(?:tell|send|message)\s+(?:it|that|them)\b(?:\s+now)?(?:\s+to)?\s*(?P<body>.+?)\s*$", re.I | re.S)
_PER = re.compile(r"^per robert[.!]?\s*$", re.I)
_CODE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# The card id as typed or as speech-to-text writes it (staging, 26 Nov: "Yes, S U 4 W S" sent nothing): a comma or stop
# after the word, and spaces, dashes or dots between the characters. Still exactly S plus the four characters of one id;
# nothing fuzzy, and a remark after the id still does not approve.
_GAP = r"[\s.\-]*"
_CH = "[" + _CODE + "]"
_REPLY = re.compile(
    r"^(?P<word>yes|no|edit)[,.!:]?\s+S" + _GAP + "(?P<c1>" + _CH + ")" + _GAP + "(?P<c2>" + _CH + ")" + _GAP
    + "(?P<c3>" + _CH + ")" + _GAP + "(?P<c4>" + _CH + r")(?![" + _CODE + r"])(?P<rest>.*)$", re.I | re.S)
_NAMED = re.compile(r"\b(?:grok|claude code|claude|codex)\b", re.I)


def agent_id(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().lower())
    return {"grok": "grok", "codex": "codex", "claude": "claude-code", "claude code": "claude-code"}[key]


def _visible(text: str) -> str | None:
    """Owner words with quotes and fences removed. An unclosed quote yields nothing, so quoted text cannot send."""
    if not isinstance(text, str):
        return None
    clauses = ask._owner_clauses(text)
    if clauses is None:
        return None
    return " ".join(part.strip() for part in clauses if part and part.strip())


def _body(part: str) -> str:
    part = re.sub(r"^[,\s]+", "", part or "")
    part = re.sub(r"^(?:on|to|about)\s+", "", part, flags=re.I)
    part = re.split(r"\?", part, maxsplit=1)[0]
    part = re.sub(r"\s*(?:does that sound right)\s*$", "", part, flags=re.I)
    return part.strip(" .")


def _one_named(earlier) -> str | None:
    found = []
    for note in earlier or []:
        if not isinstance(note, dict):
            continue
        if note.get("author_kind") not in ("owner", "agent"):
            continue
        for match in _NAMED.finditer(str(note.get("text") or "")):
            found.append(agent_id(match.group(0)))
    uniq = list(dict.fromkeys(found))
    return uniq[0] if len(uniq) == 1 else None


def propose(text, earlier=()) -> list[dict] | None:
    """Cards {to, text} for a clear send, else None. A question still proposes the card. Quoted and negated text does not."""
    visible = _visible(text)
    if not visible:
        return None
    if re.search(r"\b(?:do not|don't|never|not)\s+(?:tell|ask|send|message|contact)\b", visible, re.I):
        return None
    two = _TWO.search(visible)
    if two and agent_id(two["a"]) != agent_id(two["b"]):
        left, right = _body(two["ta"]), _body(two["tb"])
        if left and right:
            return [{"to": agent_id(two["a"]), "text": left}, {"to": agent_id(two["b"]), "text": right}]
    one = _ONE.search(visible)
    if one:
        body = _body(one["body"])
        if body:
            return [{"to": agent_id(one["agent"]), "text": body}]
    it = _IT.match(visible.strip())
    if it:
        who = _one_named(earlier)
        body = _body(it["body"])
        if who and body:
            return [{"to": who, "text": body}]
    if _PER.match(visible.strip()):
        for note in reversed(list(earlier or [])):
            if isinstance(note, dict) and note.get("author_kind") == "owner":
                prior = propose(str(note.get("text") or ""), earlier)
                if prior:
                    return prior
        return None
    return None


def parse_reply(text: str):
    """('yes'|'no'|'edit', 'S-XXXX', new words or '') or ('unclear', 'S-XXXX', '') or None.
    A remark after yes does not approve. edit requires a colon and new words."""
    if not isinstance(text, str):
        return None
    match = _REPLY.match(text.strip())
    if match is None:
        return None
    code = "S-" + "".join(match[k] for k in ("c1", "c2", "c3", "c4")).upper()
    word = match["word"].lower()
    rest = match["rest"] or ""
    if word == "edit":
        body = rest[1:].strip() if rest.startswith(":") else ""
        if not body or "\n" in body:
            return "unclear", code, ""
        return "edit", code, body
    if rest.strip(" .!"):
        return "unclear", code, ""
    return ("no" if word == "no" else "yes"), code, ""


def render(cards: list[dict]) -> str:
    lines = [f'Send to {c["to"]}: "{c["text"]}" — yes {c["id"]}' for c in cards]
    lines.append("Nothing is sent until you say yes and the card id. Reply edit S-n: new words to change a card.")
    return "\n".join(lines)


class Store:
    """One file per conversation beside the request ledger. Pending cards only. A yes consumes that id."""

    def __init__(self, root: str):
        self.root = os.path.join(root, "send-cards")

    def _path(self, conversation_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "", conversation_id or "")
        if not safe:
            raise ValueError("conversation")
        return os.path.join(self.root, safe + ".json")

    def _load(self, conversation_id: str) -> dict:
        try:
            with open(self._path(conversation_id), "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, conversation_id: str, doc: dict) -> None:
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        path = self._path(conversation_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)

    def issue(self, conversation_id: str, drafts: list[dict]) -> list[dict]:
        doc = self._load(conversation_id)
        cards = doc.setdefault("cards", {})
        issued = []
        for draft in drafts:
            code = "S-" + "".join(secrets.choice(_CODE) for _ in range(4))
            while code in cards:
                code = "S-" + "".join(secrets.choice(_CODE) for _ in range(4))
            card = {"id": code, "to": draft["to"], "text": draft["text"], "state": "pending"}
            cards[code] = card
            issued.append(card)
        self._save(conversation_id, doc)
        return issued

    def get(self, conversation_id: str, code: str) -> dict | None:
        card = self._load(conversation_id).get("cards", {}).get(code)
        return card if isinstance(card, dict) else None

    def replace(self, conversation_id: str, code: str, text: str) -> dict | None:
        doc = self._load(conversation_id)
        old = doc.get("cards", {}).get(code)
        if not old or old.get("state") != "pending":
            return None
        old["state"] = "edited"
        issued = self.issue(conversation_id, [{"to": old["to"], "text": text}])
        doc = self._load(conversation_id)
        doc["cards"][code] = old
        self._save(conversation_id, doc)
        return issued[0]

    def mark(self, conversation_id: str, code: str, state: str) -> None:
        doc = self._load(conversation_id)
        if code in doc.get("cards", {}):
            doc["cards"][code]["state"] = state
            self._save(conversation_id, doc)
