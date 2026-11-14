"""What kind of device a voice call came from: phone, tablet or desktop (#570, #567 direction, 13 Nov 2026).

Robert's voice is consistently worse on his iPhone than on his MacBook, and nothing recorded which device a call came
from. Each call now records its device class next to its path and provider, so phone versus desktop is read from the
records, not from memory. Only the class is kept: never the user agent string itself.
"""
from __future__ import annotations

import re

DEVICES = ("phone", "tablet", "desktop", "unknown")

_TABLET = re.compile(r"\biPad\b|\bTablet\b|Android(?!.*\bMobile\b)", re.I)
_PHONE = re.compile(r"\biPhone\b|\biPod\b|\bAndroid\b.*\bMobile\b|\bMobile\b.*\bAndroid\b|Windows Phone", re.I)
_DESKTOP = re.compile(r"\bMacintosh\b|\bWindows NT\b|\bX11\b|\bLinux x86_64\b|\bCrOS\b", re.I)


def device_class(user_agent) -> str:
    """"phone", "tablet", "desktop" or "unknown" from a User-Agent header. An iPad that asks for the desktop site reports
    itself as a Macintosh and is counted as desktop: the header cannot tell them apart."""
    ua = user_agent if isinstance(user_agent, str) else ""
    if not ua.strip():
        return "unknown"
    if _PHONE.search(ua):
        return "phone"
    if _TABLET.search(ua):
        return "tablet"
    if _DESKTOP.search(ua):
        return "desktop"
    return "unknown"


__all__ = ["DEVICES", "device_class"]
