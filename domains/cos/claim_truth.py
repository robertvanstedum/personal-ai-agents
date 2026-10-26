"""Receipt truth for model replies: no note, reminder or later action without a platform receipt.

A model reply has no write or scheduling authority. Notes, reminders and Board post-its exist
only through deterministic platform operations, which return their own receipts and never pass
through here. So a reply sentence that claims one was kept or set, or that promises a later
action by the assistant ("I'll remind you at 5", "I'll nudge you tomorrow"), is untrue whatever
the owner asked and however the request was worded.

Only those sentences are removed, and one honest notice is appended. The rest of the answer
stays, including history and negatives ("as I've noted before", "you have no reminders set")
and remarks ("I'll note that the cron needs…", "I'll remind you that the key rotates monthly").
Nothing here reads or depends on the owner's text.
"""
from __future__ import annotations

import re

NOTICE = (
    "No note or reminder was saved or scheduled by this reply: one exists only with a platform "
    "receipt. Say ‘/note …’ or ‘Make a note that …’, or ‘Remind me at 5 pm to …’ in Master "
    "Craftsman (elsewhere ‘/remind YYYY-MM-DDTHH:MMZ …’ with the time in UTC)."
)

_THING = r"(?:reminders?|notes?|post[- ]?its?|alarms?|timers?)"
_PLACE = r"(?:notes?|reminders?|board|list|memory)"
_SELF = r"(?:i|we)"
_HAVE = r"(?:['’]ve|\s+have|['’]d|\s+had)"
_WILL = r"(?:['’]ll|\s+will|\s+shall|['’]m\s+going\s+to|\s+am\s+going\s+to|['’]re\s+going\s+to|\s+are\s+going\s+to)"
_ADV = r"(?:(?:just|also|now|already|successfully|then|definitely|make\s+sure\s+to|be\s+sure\s+to)\s+)*"
_LEAD = r"^\W*(?:(?:okay|ok|sure|got\s+it|done|great|all\s+set|perfect|alright|right)\W+)*"

# Claims that something was kept or set. Skipped in a sentence that negates ("no reminders are set").
_DONE = re.compile(
    "|".join([
        rf"\b{_SELF}{_HAVE}?\s+{_ADV}(?:set|scheduled|created|added|made|saved|logged|recorded|stored|put|placed|pinned)\s+"
        rf"(?:up\s+)?(?:(?:a|an|the|your|that|this|one)\s+)?(?:new\s+)?{_THING}\b",
        rf"(?:^|\b{_SELF}{_HAVE}?\s+{_ADV}|^\W*)(?:saved|added|put|logged|recorded|stored|jotted|written|noted|pinned)\s+"
        rf"(?:down\s+)?(?:(?:that|this|it|them|the\s+\w+(?:\s+\w+)?)\s+)?(?:down\s+)?(?:to|in|on|into)\s+(?:your|the|my)\s+{_PLACE}\b",
        rf"\b{_SELF}{_HAVE}\s+{_ADV}(?:noted|jotted|saved|written|logged|recorded|captured|stored)\s+(?:it|that|this|them)"
        rf"(?:\s+down)?(?:\s+for\s+(?:you|later))?\s*(?:[.!,;]|$)",
        rf"{_LEAD}(?:consider\s+it\s+)?noted\b",
        rf"{_LEAD}made\s+a\s+note\b",
        r"\bconsider\s+it\s+(?:noted|done|saved|set)\b",
        rf"\b(?:your|the|that|this)\s+(?:new\s+)?{_THING}\s+(?:is|was|has\s+been|['’]s)\s+(?:now\s+|successfully\s+|all\s+)?"
        rf"(?:set|scheduled|created|added|saved|recorded|stored|logged|pinned)\b",
        rf"{_LEAD}{_THING}\s+(?:now\s+|successfully\s+)?(?:set|scheduled|created|added|saved|recorded|stored)\b",
        r"\b(?:platform\s+)?note\s+(?:successfully\s+)?(?:saved|recorded|stored)\b",
        rf"\bjotted\s+(?:it|that|this)\s+down\b",
    ]),
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:no|not|never|none|nothing|without|unable|failed|cannot|isn['’]t|wasn['’]t|aren['’]t|haven['’]t|"
    r"hasn['’]t|didn['’]t|don['’]t|can['’]t|couldn['’]t|won['’]t)\b",
    re.IGNORECASE,
)
# Promises of a later action by the assistant. A model cannot act later on its own.
_LATER = re.compile(
    "|".join([
        rf"\b{_SELF}{_WILL}\s+{_ADV}(?:remind|nudge|ping|notify|alert|text|message|email|buzz)\s+you\b(?!\s+that\b)",
        rf"\b{_SELF}{_WILL}\s+{_ADV}(?:follow\s+up|check\s+back|check\s+in\s+(?:with\s+you\s+)?(?:later|tomorrow|tonight|at|in|on)|"
        rf"circle\s+back|get\s+back\s+to\s+you|send\s+you\s+(?:a|an)\s+(?:reminder|nudge|ping|message|text|email|update|note))\b",
        r"\byou(?:['’]ll|\s+will)\s+(?:get|receive|be\s+sent)\s+(?:a\s+)?(?:reminder|nudge|ping|notification|message|text)\b",
        r"\byou(?:['’]ll|\s+will)\s+hear\s+from\s+me\b",
        rf"\b{_SELF}{_WILL}\s+{_ADV}(?:make\s+a\s+note|(?:note|jot|write)\s+(?:it|that|this)\s+down|"
        rf"add\s+(?:it|that|this)\s+to\s+(?:your|the|my)\s+{_PLACE}|save\s+(?:it|that|this)\s+(?:to|in)\s+(?:your|the|my)\s+{_PLACE}|"
        rf"set\s+(?:up\s+)?(?:a|the|that|this|your)\s+reminder|put\s+(?:it|that|this)\s+on\s+(?:the|your)\s+board|"
        rf"create\s+(?:a|the)\s+(?:reminder|note|post[- ]?it))\b",
        rf"\b{_SELF}{_WILL}\s+{_ADV}note\s+(?:it|that|this)\s*(?:[.!]|$)",
    ]),
    re.IGNORECASE,
)
# Sentence spans within one line, keeping every character: a break needs whitespace after the
# punctuation, so "5.30 pm" or "v1.2" never split.
_BREAK = re.compile(r"[.!?]+(?=\s|$)\s*")
_MARKER_ONLY = re.compile(r"^[\s>*•\-–—]*(?:\d+[.)]\s*)?$")


def _sentences(line: str) -> list[str]:
    spans, start = [], 0
    for match in _BREAK.finditer(line):
        if match.end() > start:
            spans.append(line[start:match.end()])
            start = match.end()
    if start < len(line):
        spans.append(line[start:])
    return spans


# Quoted text is drafted or reported content, never the assistant's own claim.
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\'[^\'\n]*\'(?!\w)')
# A negation governs a claim only inside the same clause, before it.
_CLAUSE_BREAK = re.compile(r"[,;:–—]|\bbut\b|\bhowever\b", re.IGNORECASE)


def _negated_before(text: str, start: int) -> bool:
    prefix = text[:start]
    breaks = list(_CLAUSE_BREAK.finditer(prefix))
    clause = prefix[breaks[-1].end():] if breaks else prefix
    return bool(_NEGATED.search(clause))


def unsupported(sentence: str) -> bool:
    """True when one sentence claims a kept note/reminder or promises a later action.

    Text in quotes is ignored, and a claim counts unless a negation earlier in its own clause
    governs it: "I cannot promise that I will remind you" is a limitation, while "I saved a note so
    you will not forget" is still a claim (the negation is after it, in another clause)."""
    text = _QUOTED.sub(" ", sentence)
    return any(not _negated_before(text, m.start()) for pattern in (_LATER, _DONE) for m in pattern.finditer(text))


def enforce(reply: str) -> tuple[str, int]:
    """(reply without unsupported sentences plus the notice, number removed); unchanged when none.

    Works line by line so paragraphs and lists keep their shape: a claim sentence is cut from its
    line, and a line left with only a list marker is dropped."""
    removed, lines = 0, []
    for line in reply.split("\n"):
        spans = _sentences(line)
        kept = [span for span in spans if not unsupported(span)]
        if len(kept) == len(spans):
            lines.append(line)
            continue
        removed += len(spans) - len(kept)
        rest = "".join(kept).rstrip()
        if not _MARKER_ONLY.match(rest):
            lines.append(rest)
    if not removed:
        return reply, 0
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return (f"{body}\n\n{NOTICE}" if body else NOTICE), removed


FILE_NOTICE = (
    "No downloadable file was created by this reply: a download exists only with a platform "
    "delivery receipt and download link. Files in the agent workspace are not available here."
)
_FILE_CLAIM = re.compile(
    r"\b(?:ready|available)\s+(?:for|to)\s+download\b|"
    r"\bsaved\s+(?:it|this|that|them|the\s+\w+)\s+as\s+a\s+(?:downloadable\s+)?file\b|"
    r"\b(?:a|the|your)\s+downloadable\s+(?:file|document|copy)\b|"
    r"\bdownload\s+(?:link|package)\s+(?:is\s+)?(?:ready|attached)\b|"
    r"\b(?:I(?:[’']ve| have)?|(?:has|have) been|was|is(?: now)?)\s+(?:saved|written|exported|created)\b[^.!?\n]{0,160}\b(?:to|in|at)\s+(?:a\s+)?(?:file\b|[`\"']?(?:/|[A-Z]:\\))",
    re.IGNORECASE,
)

def enforce_file_claims(reply: str) -> tuple[str, int]:
    """Qualify unsupported download claims only after platform delivery processing.

    Do not erase useful inline content or alter document packets. A real delivery receipt
    bypasses this guard at the caller; a model-written path alone is never such a receipt.
    """
    claims = sum(not _negated_before(line, m.start())
                 for line in reply.splitlines() if not line.lstrip().startswith('>')
                 for m in _FILE_CLAIM.finditer(line))
    return (reply.rstrip() + "\n\n" + FILE_NOTICE, claims) if claims else (reply, 0)
