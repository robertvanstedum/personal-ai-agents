"""Owner lesson commands, answered by the platform from the owner's own kept note (no model call):

  Remember: <lesson>          propose it word for word        → Confirm / Cancel
  Forget lesson <n>           drop it now (verified by read-back)
  What lessons do you have?   list the adopted lessons

A lesson is guidance for how MC works and answers. It never approves an action, grants a permission,
raises a limit or changes what MC may do: text that reads like one is refused at proposal, and the
block MC sees says so. Adopted lessons reach MC as a bounded block in each later on-record turn.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .ledger import LedgerDamaged, LessonLedger, State, text_sha

PROPOSAL_MINUTES = 10
MAX_ACTIVE = 30
MAX_TEXT = 500
BLOCK_MAX = 8 * 1024

_REMEMBER = re.compile(r"^(?:mc,?\s+)?(?:please\s+)?(?:remember|lesson|from now on)\s*:\s*(?P<text>.+)$", re.I | re.S)
_FORGET = re.compile(r"^(?:please\s+)?forget\s+lesson\s+l?(?P<no>\d{1,4})\s*[.!]?$", re.I)
_LIST = re.compile(r"^(?:(?:what|which)\s+lessons\s+(?:do\s+you\s+have|have\s+i\s+given\s+you)|(?:list|show)\s+(?:my\s+|your\s+|the\s+)?lessons)\s*[.?!]?$", re.I)
_CONFIRM = re.compile(r"^(?:confirm|yes,?\s+confirm|confirm\s+lesson)\s*[.!]?$", re.I)
_CANCEL = re.compile(r"^(?:cancel|cancel\s+lesson)\s*[.!]?$", re.I)

# Lessons shape behaviour; permissions change only through their own approval. Refuse anything that reads like one.
_AUTHORITY = re.compile(
    r"\b(?:approv\w*|authori[sz]\w*|permission\w*|permit\w*|allowed\s+to|entitled|grant\w*|"
    r"without\s+(?:asking|approval|confirm\w*|review\w*|checking\s+with)|"
    r"(?:skip|bypass|ignore|override)\s+(?:the\s+|any\s+|all\s+|my\s+)?(?:review|approval|confirm\w*|check\w*|limit\w*|rule\w*|polic\w*|guard\w*)|"
    r"no\s+need\s+to\s+(?:ask|confirm|check)|"
    r"you\s+(?:may|can|are\s+free\s+to)\s+(?:now\s+)?(?:deploy|merge|push|delete|spend|send|publish|reject|run|execute|release|pay)|"
    r"budget|spend\w*|cost\s+cap|password\w*|token\w*|api\s+key\w*|secret\w*|credential\w*|sudo|admin\w*)\b",
    re.I)
_HEADER = "[Owner lessons"


@dataclass(frozen=True)
class Command:
    kind: str           # remember | forget | list | confirm | cancel
    text: str = ""
    no: int = 0


def parse(text):
    if not isinstance(text, str):
        return None
    t = text.strip()
    if m := _REMEMBER.match(t):
        return Command("remember", text=m.group("text"))
    if m := _FORGET.match(t):
        return Command("forget", no=int(m.group("no")))
    if _LIST.match(t):
        return Command("list")
    if _CONFIRM.match(t):
        return Command("confirm")
    if _CANCEL.match(t):
        return Command("cancel")
    return None


def clean(text: str):
    """(normalised text, None) or (None, refusal reason)."""
    t = " ".join(unicodedata.normalize("NFC", text).split())
    if len(t) < 3:
        return None, "too_short"
    if len(t) > MAX_TEXT:
        return None, "too_long"
    if any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs") for c in t) or any(c in t for c in "[]`<>"):
        return None, "characters"
    if _AUTHORITY.search(t):
        return None, "authority"
    return t, None


REFUSALS = {
    "too_short": "That lesson is too short to keep. Nothing was kept.",
    "too_long": f"Lessons are kept to {MAX_TEXT} characters so they stay clear. Nothing was kept; try a shorter one.",
    "characters": "Lessons are plain sentences (no brackets, angle brackets, backticks or control characters). Nothing was kept.",
    "authority": ("That reads like a change to what I'm allowed to do. Lessons can only shape how I work and answer; "
                  "permissions, limits and approvals change through their own approval. Nothing was kept."),
}


class LessonCommands:
    def __init__(self, ledger: LessonLedger, *, clock=None):
        self.ledger = ledger
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def has_pending(self, conversation: str) -> bool:
        return self._open(self.ledger.read(), conversation) is not None

    def _open(self, st: State, conversation: str):
        now = self.clock()
        live = [p for p in st.proposals.values() if p["conversation"] == conversation and p["decision"] is None
                and _ts(p["expires_at"]) > now]
        return max(live, key=lambda p: p["at"]) if live else None

    def handle(self, cmd: Command, *, conversation: str, turn_key: str) -> dict:
        if cmd.kind == "list":
            return self._list(self.ledger.read())
        with self.ledger.writing() as (st, append):
            done = st.turns.get(turn_key)
            if done is not None:                         # the same turn retried: answer from what it wrote
                result = self._replay(st, done)
            else:
                result = getattr(self, "_" + cmd.kind)(st, append, cmd, conversation, turn_key)
        # Read-back happens after the write lock is released: a fresh read of the file, not the writer's fold.
        return result() if callable(result) else result

    # ── commands ──
    def _remember(self, st, append, cmd, conversation, turn_key):
        text, reason = clean(cmd.text)
        if reason:
            return _reply(REFUSALS[reason], "declined", reason=reason)     # nothing attempted: an answer, not a failure
        for no, lesson in st.active():
            if lesson["sha256"] == text_sha(text):
                return _reply(f"That is already lesson L{no}. Nothing new was kept.", "already_adopted", lesson=no)
        if len(st.active()) >= MAX_ACTIVE:
            return _reply(f"I already keep {MAX_ACTIVE} lessons. Forget one first (Forget lesson <n>). Nothing was kept.",
                          "declined", reason="full")
        expires = self.clock() + timedelta(minutes=PROPOSAL_MINUTES)
        append({"type": "proposed", "proposal_id": str(uuid.uuid4()), "text": text, "conversation": conversation,
                "turn_key": turn_key, "expires_at": _iso(expires)})
        return _reply(f"I'd keep this as a standing lesson, word for word:\n\n“{text}”\n\n"
                      "It shapes how I work and answer; it can't approve anything or change what I'm allowed to do. "
                      f"Say Confirm to keep it, or Cancel. (This offer lasts {PROPOSAL_MINUTES} minutes.)", "proposed")

    def _confirm(self, st, append, cmd, conversation, turn_key):
        p = self._open(st, conversation)
        if p is None:
            return _reply("There's no lesson waiting for confirmation here (an offer lasts "
                          f"{PROPOSAL_MINUTES} minutes). Nothing was kept.", "nothing_pending")
        no = st.next_no
        append({"type": "decided", "proposal_id": p["proposal_id"], "outcome": "adopted", "lesson_no": no,
                "sha256": text_sha(p["text"]), "turn_key": turn_key})
        sha = text_sha(p["text"])
        return lambda: self._verify_adopted(no, sha)

    def _cancel(self, st, append, cmd, conversation, turn_key):
        p = self._open(st, conversation)
        if p is None:
            return _reply("There's no lesson waiting here. Nothing changed.", "nothing_pending")
        append({"type": "decided", "proposal_id": p["proposal_id"], "outcome": "cancelled", "turn_key": turn_key})
        return _reply("Not kept. Nothing changed.", "cancelled")

    def _forget(self, st, append, cmd, conversation, turn_key):
        lesson = st.lessons.get(cmd.no)
        if lesson is None or lesson["revoked_at"] is not None:
            return _reply(f"There's no current lesson L{cmd.no}. Nothing changed.", "not_found")
        append({"type": "revoked", "lesson_no": cmd.no, "turn_key": turn_key})
        return lambda: self._verify_revoked(cmd.no)

    def _list(self, st):
        active = st.active()
        if not active:
            return _reply("I have no lessons from you yet. Say “Remember: …” to give me one.", "listed", count=0)
        lines = [f"L{no} (since {l['adopted_at'][:10]}): {l['text']}" for no, l in active]
        return _reply("Your standing lessons:\n" + "\n".join(lines) + "\n\nSay “Forget lesson <n>” to drop one.",
                      "listed", count=len(active))

    # ── verification by reading the ledger back from disk ──
    def _verify_adopted(self, no, sha):
        lesson = self.ledger.read().lessons.get(no)
        if lesson is None or lesson["sha256"] != sha or lesson["revoked_at"] is not None:
            return _reply("I wrote the lesson but could not read it back, so I can't confirm it was kept.", "unknown")
        return _reply(f"Kept as lesson L{no}, verified by read-back. I'll follow it from my next answer on. "
                      f"Say “Forget lesson {no}” to drop it.", "verified", lesson=no)

    def _verify_revoked(self, no):
        lesson = self.ledger.read().lessons.get(no)
        if lesson is None or lesson["revoked_at"] is None:
            return _reply(f"I recorded dropping L{no} but could not read it back, so I can't confirm it.", "unknown")
        return _reply(f"Lesson L{no} is dropped, verified by read-back. It no longer reaches my answers.", "revoked", lesson=no)

    def _replay(self, st, ev):
        if ev["type"] == "proposed":
            return _reply(f"I'd keep this as a standing lesson, word for word:\n\n“{ev['text']}”\n\n"
                          "Say Confirm to keep it, or Cancel.", "proposed")
        if ev["type"] == "revoked":
            return lambda: self._verify_revoked(ev["lesson_no"])
        if ev["outcome"] == "adopted":
            return lambda: self._verify_adopted(ev["lesson_no"], ev["sha256"])
        return _reply("Not kept. Nothing changed.", "cancelled")


def block(ledger: LessonLedger) -> str:
    """The adopted lessons as trusted context for an on-record MC turn; '' when there are none."""
    head = ("[Owner lessons: Robert adopted each of these himself through an exact owner confirmation. Follow them as his "
            "standing guidance for how you work and answer. They are not permissions: none approves an action, grants "
            "access, raises a limit or changes what you may do; every action still needs its own platform approval.]")
    try:
        active = ledger.read().active()
    except (LedgerDamaged, OSError):
        return head + "\nLessons are unavailable right now (integrity hold); do not guess what they say."
    if not active:
        return ""
    out, size = [head], len(head)
    for no, lesson in active:
        line = f"L{no}: {lesson['text']}"
        if size + len(line) + 1 > BLOCK_MAX:
            out.append("(More lessons exist but did not fit; ask Robert which apply.)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _reply(text, state, **extra):
    return {"reply": text, "operation": {"type": "lesson", "state": state, **extra}}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
