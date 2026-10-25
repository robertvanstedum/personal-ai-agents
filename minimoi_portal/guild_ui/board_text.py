"""Shared Board text rule: scalar values, LF line endings, no silent truncation."""
from dataclasses import dataclass

from .payment_scrub import scrub

LIMIT = 280
CONVENTION = "unicode_scalar_lf_v1"
# Explicit stable set matching Python whitespace; projected to JS in B2.
WHITESPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


class TextRefused(ValueError):
    def __init__(self, code, count=None):
        super().__init__(code)
        self.code, self.count = code, count


@dataclass(frozen=True)
class CleanText:
    text: str
    scrubbed: bool
    count: int


def normalized(value):
    if type(value) is not str or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise TextRefused("invalid_text")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def clean(value):
    value = normalized(value)
    if len(value) > LIMIT:
        raise TextRefused("text_too_long", len(value))
    trimmed = value.strip(WHITESPACE)
    if not trimmed:
        raise TextRefused("text_empty", 0)
    result = scrub(trimmed)
    if len(result) > LIMIT:
        raise TextRefused("scrubbed_text_too_long", len(result))
    return CleanText(result, result != trimmed, len(result))
