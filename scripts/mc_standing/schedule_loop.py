#!/usr/bin/env python3
"""The standing worker's own scheduler (standing v0.3.1 §3: one scheduler, America/Chicago).

A long-running loop for a dedicated container, so the brief needs no host cron:

* every tick (default 60 s): acknowledge the owner's control document if no cycle is running
  (``run_cycle.py --ack-control-only`` semantics);
* from 05:40 until the 09:00 catch-up cutoff (local), run the day's cycle. A cycle that ends
  non-final (delivery pending, paused, unknown) is retried no sooner than ``--retry-s`` later;
  once the day's cycle is final the loop stops trying until the next day. Outside the window
  nothing is attempted: a restart never causes a catch-up flood (the cycle itself also refuses
  to send after 09:00 or for another date).

Arguments after ``--`` are passed unchanged to ``run_cycle.py`` (watchlist, roots, ingress,
token, control, disable, telegram, envelope, jobs root ...). Each tick prints one JSON line of
fixed fields; exceptions are caught per tick and reported by type only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_cycle  # noqa: E402

ZONE = ZoneInfo("America/Chicago")
START, CUTOFF = dtime(5, 40), dtime(9, 0)
FINAL_CODES = (0,)          # run_cycle exit 0: delivered / already final / paused / skipped


class Loop:
    def __init__(self, cycle_args: list[str], *, retry_s: float = 300, cycle=run_cycle.main,
                 clock=lambda: datetime.now(timezone.utc)):
        self.cycle_args, self.retry_s, self.cycle, self.clock = cycle_args, retry_s, cycle, clock
        self.done_for: str | None = None        # local date whose cycle reached a final state
        self.next_attempt: datetime | None = None

    def tick(self) -> dict:
        now = self.clock()
        local = now.astimezone(ZONE)
        out = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ack": None, "cycle": None}
        try:
            out["ack"] = self.cycle(self.cycle_args + ["--ack-control-only"])
        except Exception as exc:
            out["ack"] = f"error:{type(exc).__name__}"
        today = local.date().isoformat()
        in_window = START <= local.time() < CUTOFF
        if not in_window or self.done_for == today or (self.next_attempt and now < self.next_attempt):
            return out
        try:
            code = self.cycle(list(self.cycle_args))
        except SystemExit as exc:              # configuration refusals exit; keep the loop alive
            code = exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:
            code = f"error:{type(exc).__name__}"
        out["cycle"] = code
        if code == 0 and self._final(today):
            self.done_for = today
        else:
            self.next_attempt = datetime.fromtimestamp(now.timestamp() + self.retry_s, timezone.utc)
        return out

    def _final(self, today: str) -> bool:
        """Final means the cycle record for today is delivered/missed/failed/held, not merely
        that the process exited 0 (a paused cycle also exits 0 and must be retried after resume)."""
        try:
            from core.mc_standing.spool import Spool
            root = Path(self.cycle_args[self.cycle_args.index("--state-root") + 1])
            spool = Spool(root)
            records = [spool.read("cycles", n) for n in spool.names("cycles") if f".{today}." in n]
        except Exception:
            return False
        return any(r and r.get("state") in ("delivered", "missed", "failed", "held") for r in records)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cycle_args = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--tick-s", type=float, default=60)
    p.add_argument("--retry-s", type=float, default=300)
    p.add_argument("--once", action="store_true", help="run one tick and exit (for checks)")
    args = p.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    if "--state-root" not in cycle_args:
        print(json.dumps({"state": "config_error", "reason": "cycle_args_need_state_root"}))
        return 2
    loop = Loop(cycle_args, retry_s=args.retry_s)
    while True:
        print(json.dumps(loop.tick()), flush=True)
        if args.once:
            return 0
        time.sleep(args.tick_s)


if __name__ == "__main__":
    raise SystemExit(main())
