"""The opening briefing (decision B, S1a): one line, computed by rules, no model.

It is recomputed on every read and never stored. Any part that is unknown
says unknown.
"""
from __future__ import annotations

from datetime import datetime

LABEL = "Guild platform · rules · no model"


def _hhmm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).strftime("%H:%M UTC")
    except ValueError:
        return "unknown time"


def opening_briefing(lights: list[dict], needs: dict, observed_at: str) -> dict:
    parts = []
    if needs["status"] != "ok":
        parts.append({"light_id": None, "word": "needs you unknown"})
    elif needs["total"]:
        first = needs["items"][0]
        parts.append({"light_id": None,
                      "word": f"{needs['total']} need{'s' if needs['total'] == 1 else ''} you "
                              f"({first['tag']} #{first['item_id']})"})
    else:
        parts.append({"light_id": None, "word": "nothing needs you"})
    grey = 0
    for light in lights:
        if light["source"] == "not_instrumented" and light["rule"] == "not_instrumented":
            grey += 1
            continue
        parts.append({"light_id": light["id"], "word": f"{light['short']} {light['word'] if light['state'] != 'unknown' else 'unknown'}"})
    if grey:
        parts.append({"light_id": None, "word": f"{grey} not instrumented"})
    text = " · ".join([f"As of {_hhmm(observed_at)}"] + [p["word"] for p in parts])
    return {"text": text, "parts": parts, "observed_at": observed_at, "label": "platform rules",
            "display_label": LABEL}
