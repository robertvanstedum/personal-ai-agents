"""The Shop floor footer's tokens (usage-record U3): "Done in 1.2s · 96 output
tokens", joined from the environment's usage store.

MC takes one turn at a time (the relay and the portal both hold one in
flight), so the gateway's usage records for actor ``mc`` whose time falls in
a turn's window are exactly that turn's calls. OpenClaw's own ``usage`` is
never used (its compat API reports zeros).

Read-only and never failing: the store is a folder of JSON lines written by
the gateway (services/usage; the record format is the contract, not the code).
With no store, or no matching record, the footer says "tokens unknown", never
a guess. A turn that ended moments ago may not have its record yet (the
gateway writes asynchronously): then ``tokens_text`` is None and the page asks
again shortly.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

READ_TAIL_BYTES = 1024 * 1024
BEFORE = timedelta(seconds=1)       # clock skew between the portal and the gateway containers
AFTER = timedelta(seconds=3)        # the gateway records a call just after it answers
PENDING = timedelta(seconds=20)     # younger than this and unmatched: not yet known, not unknown


def _store() -> str | None:
    return os.environ.get("MINIMOI_USAGE_DIR") or None


def _parse(value):
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _mc_records(folder: str) -> list[tuple[datetime, dict]]:
    try:
        names = sorted(n for n in os.listdir(folder) if re.fullmatch(r"usage-\d{4}-\d{2}\.jsonl", n))[-2:]
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with open(os.path.join(folder, name), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - READ_TAIL_BYTES))
                raw = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for text in raw.splitlines():
            try:
                rec = json.loads(text)
            except ValueError:
                continue
            if (isinstance(rec, dict) and rec.get("v") == 1 and rec.get("actor") == "mc" and rec.get("kind") == "model"
                    and rec.get("emitter") == "gateway" and rec.get("status") == "ok"):
                at = _parse(rec.get("occurred_at"))
                if at is not None:
                    out.append((at, rec))
    return out


def tokens_text(output_tokens: int) -> str:
    return f"{output_tokens} output token{'s' if output_tokens != 1 else ''}"


def add_tokens(turns: dict, *, now: datetime | None = None, folder: str | None = None) -> dict:
    """Add ``output_tokens`` and ``tokens_text`` to each turn (see the module doc)."""
    now = now or datetime.now(timezone.utc)
    folder = folder if folder is not None else _store()
    records = _mc_records(folder) if folder else []
    for turn in turns.values():
        window = turn.pop("window", None)
        turn["output_tokens"], turn["tokens_text"] = None, "tokens unknown"
        if window is None or not folder:
            continue
        start, end = window
        inside = [r for at, r in records if start - BEFORE <= at <= end + AFTER]
        counts = [r.get("output_tokens") for r in inside]
        if inside and all(isinstance(c, int) and not isinstance(c, bool) for c in counts):
            turn["output_tokens"] = sum(counts)
            turn["tokens_text"] = tokens_text(turn["output_tokens"])
        elif not inside and now - end < PENDING:
            turn["tokens_text"] = None                # not recorded yet: the page asks again
    return turns


__all__ = ["add_tokens", "tokens_text"]
