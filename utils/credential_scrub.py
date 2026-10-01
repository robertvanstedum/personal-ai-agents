"""Credential guard for text MiniMoi keeps (Spec 160 §3.4).

``SECRET`` detects a credential (the Workshop's ``_SECRET`` alternatives, in
``minimoi_portal/workshop/record.py``, are all covered here, so that module can
import this one). ``scrub`` replaces each match in place with
``[credential removed]`` and says whether anything changed. Spec 160's list:

- ``password``, ``pw``, ``secret``, ``token`` or ``api key`` followed by a
  value (``password: x``, ``token=x``, ``my password is x``): the value goes,
  the word stays;
- ``Bearer …``, JWTs, ``ghp_`` and ``github_pat_``, ``sk-ant-``, ``xai-`` and
  ``sk-``, ``tvly-``, ``AKIA…``, PEM blocks, Telegram bot tokens, and
  ``scheme://user:pass@`` (the ``user:pass`` goes).

Nothing here logs or returns the matched text.
"""
from __future__ import annotations

import re

REMOVED = "[credential removed]"

_TOKENS = (
    r"-----BEGIN [A-Z0-9 ]*-----[\s\S]*?(?:-----END [A-Z0-9 ]*-----|\Z)",   # a PEM block, to its end
    r"-----BEGIN",                                                          # a bare PEM start
    r"\bbearer\s+\S+",
    r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_\-]*)?",     # a JWT
    r"\bgithub_pat_[A-Za-z0-9_]*",
    r"\bghp_[A-Za-z0-9]{10,}",
    r"\bsk-ant-[A-Za-z0-9_\-]{8,}",
    r"\bsk-[A-Za-z0-9_\-]{8,}",
    r"\bxai-[A-Za-z0-9]{16,}",
    r"\btvly-[A-Za-z0-9]{8,}",
    r"\bAKIA[0-9A-Z]{12,}",
    r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b",                                     # a Telegram bot token
)
_WHOLE = re.compile("|".join(f"(?:{p})" for p in _TOKENS), re.IGNORECASE)
# scheme://user:pass@ : the user:pass part goes, the scheme and host stay.
_URL_AUTH = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@")
# A keyword with a value after ":", "=" or " is ": the value goes.
_KEYED = re.compile(
    r"(?i)\b(password|passwort|pw|secret|token|api[ _\-]?key)(\s*[:=]\s*|\s+is\s+)(\[credential removed\]|[^\s,;]+)")

SECRET = re.compile(
    "|".join([f"(?:{p})" for p in _TOKENS] + [_URL_AUTH.pattern.replace("(?i)", ""),
                                               _KEYED.pattern.replace("(?i)", "")]),
    re.IGNORECASE,
)


def scrub(text: str) -> tuple[str, bool]:
    """(text with every credential replaced, whether anything changed)."""
    if not text:
        return text, False
    out = _WHOLE.sub(REMOVED, text)
    out = _URL_AUTH.sub(lambda m: f"{m.group(1)}{REMOVED}@", out)
    out = _KEYED.sub(lambda m: m.group(0) if m.group(3) == REMOVED else f"{m.group(1)}{m.group(2)}{REMOVED}", out)
    return out, out != text


__all__ = ["SECRET", "REMOVED", "scrub"]
