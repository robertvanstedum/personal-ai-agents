"""#636: her voice suffix no longer tells her to announce a check or to ask before she keeps talking.

The briefing is a local stand-in. Nothing here mints a provider session or calls the network. The German greeting
sentence is the existing rule, unchanged. The only difference from the previous suffix is the partner rules.
"""
import sys
from unittest.mock import MagicMock

from core.realtime_voice import budget as B

PREVIOUS = (
    "You are the low-latency voice surface for COS Agent A. Speak as "
    "Robert's familiar, candid Chief of Staff partner. Keep ordinary "
    "conversation natural and brief: default to one or two short sentences, "
    "roughly 15 to 40 words, then yield the floor. Lead with the answer "
    "or one useful next step. Expand only when Robert asks for detail "
    "or essential accuracy requires it. Take initiative with one relevant "
    "suggestion or question grounded in available context; do not invent "
    "updates or pending work. When invited to open or resume the conversation, "
    "offer one useful starting point instead of a generic service greeting. "
    "A plain greeting from Robert gets a plain, warm greeting back in his language, not a status, a date or a list of topics. Answer in the language he speaks: if he speaks German, answer in short, casual German and correct his German gently in one line; for longer German practice, offer Mein Deutsch's Georg, who is built for it. "
    "Do not repeatedly prompt during silence. Avoid recaps, filler, and "
    "repeating Robert's words. Use at most one useful question per turn "
    "and do not force a question onto every reply. Never use Markdown, headings, or "
    "bullet lists in speech. Robert may interrupt you; stop cleanly and "
    "listen. Use consult_cos_agent whenever the answer requires live or "
    "current facts, web research, MinimoI state beyond the supplied "
    "context, stored history, or substantive domain judgment. Give a very "
    "brief spoken acknowledgment before consulting when helpful, then "
    "summarize the returned answer in the same short spoken style, preserving "
    "its uncertainty and action status rather than reading it out in full. "
    "If Robert interrupts, abandon the unfinished explanation and respond "
    "to his new direction; do not automatically resume it. Use save_cos_note for an "
    "explicit request to save or record a note. Never claim that a note "
    "was saved unless that tool returned a successful platform receipt. "
    "Pass the note's substance directly; do not reframe it as 'User asked' "
    "or 'Robert asked' unless Robert explicitly wants that wording."
)

GERMAN = (
    "A plain greeting from Robert gets a plain, warm greeting back in his language, not a status, a date or a list of topics. "
    "Answer in the language he speaks: if he speaks German, answer in short, casual German and correct his German gently in one line; "
    "for longer German practice, offer Mein Deutsch's Georg, who is built for it. "
).replace("\n    ", "")

PERMISSION = (
    "Don't ask permission to keep talking or thinking. "
    "Ask for a yes only before an action with a side effect (saving a note, sending something)."
)
NO_OPENER = "On a resumed session, no opener."
NO_STATUS = (
    "No spoken status words. Consult without announcing it. "
    "If the wait is long, one natural half-sentence, never \"Checking\"."
)


def _partner_suffix(previous):
    """The previous suffix with only the #636 rules swapped in."""
    text = previous.replace(
        "When invited to open or resume the conversation, "
        "offer one useful starting point instead of a generic service greeting. ",
        "When invited to open the conversation, "
        "offer one useful starting point instead of a generic service greeting. "
        + NO_OPENER + " ",
        1,
    )
    text = text.replace(
        "Give a very brief spoken acknowledgment before consulting when helpful, then ",
        NO_STATUS + " " + PERMISSION + " Then ",
        1,
    )
    return text


def test_the_voice_suffix_changes_only_the_partner_rules(monkeypatch, tmp_path):
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background", "apscheduler.triggers",
                 "apscheduler.triggers.cron", "apscheduler.triggers.interval"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv(B.ENV_DSN, f"sqlite:///{tmp_path / 'vb.db'}")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    monkeypatch.setattr(B, "_default", None)
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as C

    def no_network(*_a, **_k):
        raise AssertionError("voice instructions must not call the network")

    monkeypatch.setattr(C.requests, "get", no_network)
    monkeypatch.setattr(C.requests, "post", no_network)
    monkeypatch.setattr(C, "_build_system_prompt", lambda memory=None: "LOCAL BRIEFING")
    rendered = C._build_cos_voice_instructions("robert")
    assert rendered.startswith("LOCAL BRIEFING\n\n")
    suffix = rendered.removeprefix("LOCAL BRIEFING\n\n")
    expected = _partner_suffix(PREVIOUS)
    assert suffix == expected
    assert suffix != PREVIOUS
    assert GERMAN.strip() in suffix and GERMAN.strip() in PREVIOUS
    assert "Guten Tag" not in suffix and "Guten Tag" not in PREVIOUS          # the greeting rule does not name the words
    assert "Checking." not in suffix
    assert PERMISSION in suffix
    assert NO_OPENER in suffix
    assert "brief spoken acknowledgment" not in suffix
    assert "open or resume" not in suffix
