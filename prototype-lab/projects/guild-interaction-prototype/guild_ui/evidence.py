"""Evidence Robert filed in this browser (rev 3.1): vendor warnings and refill
receipts. The browser keeps them (localStorage + one small cookie); the server
only reads and validates the cookie per request, never stores anything.

Payment-method details (card brand + digits, card numbers, billing email)
are never kept: the browser strips them before storing, and this module
strips them again and rejects anything that still looks like payment data.
"""
from __future__ import annotations

import base64
import json
import re

from .usage import VENDOR_KINDS

MAX_ITEMS = 6
CARD_BRANDS = r"(?:visa|master\s?card|amex|american\s+express|discover|diners|jcb|union\s?pay)"
PAYMENT_PATTERNS = [
    re.compile(CARD_BRANDS + r"[^\n\d]{0,20}\d{2,6}", re.I),                       # "Mastercard - 1234"
    re.compile(r"(?:ending|ends)\s+(?:in|with)\s*\d{2,6}", re.I),                  # "ending in 1234"
    re.compile(r"[•*xX]{2,}[\s-]*\d{2,6}"),                                        # "•••• 1234"
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                                         # full card numbers
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                                   # billing email
    re.compile(CARD_BRANDS, re.I),                                                 # a bare brand name
]
REDACTED = "[payment detail removed]"


def strip_payment(text: str) -> str:
    for pat in PAYMENT_PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def cookie_name(namespace: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", namespace) + "_evidence_v1"


def _int(v, lo, hi):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def parse_cookie(raw: str | None, tools: dict, balances: set) -> dict:
    """Decode and validate; anything malformed is dropped, never trusted."""
    out = {"vendor": [], "refills": []}
    if not raw or len(raw) > 4000:
        return out
    try:
        pad = "=" * (-len(raw) % 4)
        doc = json.loads(base64.urlsafe_b64decode(raw + pad).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return out
    if not isinstance(doc, dict):
        return out
    for e in (doc.get("v") or [])[:MAX_ITEMS]:
        if not isinstance(e, dict) or e.get("tool") not in tools or e.get("kind") not in VENDOR_KINDS:
            continue
        text = e.get("text")
        seen = _int(e.get("seen_at"), -20160, 40320)
        if not isinstance(text, str) or seen is None:
            continue
        out["vendor"].append({"tool": e["tool"], "kind": e["kind"], "text": strip_payment(text[:200]), "seen_at": seen,
                              "stated_reset_at": _int(e.get("stated_reset_at"), -20160, 40320),
                              "stated_remaining_pct": _int(e.get("stated_remaining_pct"), 0, 100), "filed": True})
    for e in (doc.get("r") or [])[:MAX_ITEMS]:
        if not isinstance(e, dict) or e.get("source") not in balances:
            continue
        amount, paid = e.get("amount"), _int(e.get("paid_at"), -20160, 40320)
        vendor = e.get("vendor")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not (0 < amount <= 100000) or paid is None:
            continue
        if not isinstance(vendor, str):
            continue
        out["refills"].append({"source": e["source"], "vendor": strip_payment(vendor[:60]), "amount": round(float(amount), 2),
                               "paid_at": paid, "filed": True})
    return out
