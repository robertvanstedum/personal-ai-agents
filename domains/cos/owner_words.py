"""What in the owner's words is not the words to keep: one rule for a note's and a post-it's words.

Shared by the one note grammar (confer_service.parse_explicit_note, which MC's owner_actions, Chief of Staff's turn service
and both voices read) and MC's Board grammar (minimoi_portal/guild_ui/board_commands.py), so a pointer is told apart the
same way wherever he asks to keep something. It lives with the note grammar because the CoS images ship ``domains/cos``
and not the portal; the portal ships both.

* ``points_only``: words that only point at something ("this", "your last answer", "the summary above", "everything you
  said") or name a generic thing ("a reminder", "something"). The Board's referent check, moved here unchanged
  (issue #348); the Board asks for the words instead of saving the pointer.
* ``not_his_note`` (review M-2, S-1 of the A4 fix round): for the loose note forms only, where his words follow with no
  separator, after a comma, or after "called" ("save a note test", "make a note, X", "make a note called X"). What follows
  is not his note when it is nothing; a question (it ends "?"); only a pointer (``points_only``); or when it opens with a
  word or phrase in ``_NOT_HIS``: a refusal ("never mind", "scratch that", "wait"), a request or its tail ("could you",
  "will you", "right"), an acknowledgement ("sure", "thanks"), a name he addresses (MC, CoS, Master Craftsman, Grok, …,
  and "Check with"), or a word that only frames, points or times the request ("for me", "of that", "later"). That is chat,
  never a note; one content word is a note ("save a note test" keeps "test"). Quoted words are literal.
"""
from __future__ import annotations

import re

# A payload that only points at something ("this", "your last answer", "the summary above") or names a generic thing
# ("a reminder") is not the words to keep. Quoted words are always literal (the caller unquotes first).
REFERENT = re.compile(r"""(?:
    this|that|it|these|those|same|the\s+same|above|the\s+above|below|the\s+below
  | (?:all\s+of\s+)?(?:this|that|it)(?:\s+(?:above|below|again))?
  | everything(?:\s+(?:above|you\s+said))?
  | (?:(?:the|that|this|your|my|our)\s+)?(?:(?:last|previous|latest|recent|earlier|above|current|full)\s+)?
      (?:answer|reply|response|message|summary|text|note|list|plan|points?|ideas?|suggestions?|output|results?|draft|one|thing|stuff)
      (?:\s+(?:above|below|earlier|again|you\s+(?:just\s+)?(?:gave|wrote|sent|made)))?
  | what\s+(?:you|i|we)\s+(?:just\s+)?(?:said|wrote|suggested|mentioned|proposed|sent|described|discussed)(?:\s+(?:above|earlier))?
)""", re.I | re.X)
GENERIC = re.compile(r"(?:(?:a|an|another|some|one|new|a\s+new)\s+)?(?:reminder|note|post[- ]?it|task|to-?do|item|card|entry|message|thing)s?|something|anything", re.I)

# The opening words that make what follows a loose note request no note of his (one list; review S-1).
_NOT_HIS = re.compile(r"""(?:
    never\s*mind|scratch\s+that|forget\s+(?:it|that|about\s+it)|cancel(?:\s+(?:it|that))?|stop|wait|hold\s+on|hang\s+on
  | just\s+kidding|not\s+now|skip\s+(?:it|that)|nothing
  | could|can|will|would|should|shall|do|does|did|is|are|was|were|have|has|had|may|might|must
  | please|thanks|thank|okay|ok|alright|sure|right|yes|yeah|yep|no|nope|fine|cool|great
  | mc|cos|master\s+craftsman|chief(?:\s+of\s+staff)?|grok|codex|claude(?:\s+code)?|minimoi|mini\s*moi|check\s+with
  | of|for|about|on|onto|to|in|into|with|from|at|by|as|so|if|when|whether|and|or|but|then|than
  | later|now|again|too|yet|first|soon|today|tonight|tomorrow|here|there|somewhere|instead|already
  | maybe|perhaps|real|quick|quickly|this|that|it|these|those|one|something|anything|what|which|who|how|why|where
)\b""", re.I | re.X)


def points_only(words) -> bool:
    """Whether ``words`` (trailing sentence marks aside) only point at something or name a generic thing."""
    bare = str(words).rstrip(".!?").strip()
    return REFERENT.fullmatch(bare) is not None or GENERIC.fullmatch(bare) is not None


def not_his_note(words) -> bool:
    """Whether ``words``, all that follows a loose note request, are not his note (the module's rule): chat, never a
    note. The caller has already dropped the closing question mark of a request asked as a question."""
    text = " ".join(str(words).split())
    bare = text.rstrip(".!").strip(" ,;:")
    return not bare or bare.endswith("?") or points_only(bare) or _NOT_HIS.match(bare) is not None


__all__ = ["GENERIC", "REFERENT", "not_his_note", "points_only"]
