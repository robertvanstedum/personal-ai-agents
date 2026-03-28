#!/usr/bin/env python3
"""
agent/run.py — Research Intelligence Agent session runner.

Usage:
  python agent/run.py start --session-name NAME [--estimated-cost FLOAT]
  python agent/run.py end --cost FLOAT --duration STR --notes STR

Exit codes:
  0 — clear to proceed / end logged successfully
  1 — hard stop (limit already reached)
  2 — warn-ahead abort (estimated cost would breach a limit)

Spec: docs/specs/phase1-agent-runner-spec-2026-03-21.md
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import keyring
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}. Run: pip install keyring requests")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    config_path = ROOT / "agent" / "config.json"
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}")
        sys.exit(1)
    return json.loads(config_path.read_text())


# ── Session log parsing ───────────────────────────────────────────────────────

def parse_cost(val):
    """Parse '$1.23', '1.23', 'TBD', 'RUNNING' → float. Non-numeric → 0.0."""
    if not val:
        return 0.0
    cleaned = val.strip().lstrip("$")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_session_log(log_path):
    """
    Returns dict:
      cumulative  — float, last numeric Cumulative column value
      today       — float, sum of Cost for today's rows
      weekly      — float, sum of Cost for last 7 days
      running     — list of row dicts where Duration == 'RUNNING'
    """
    today_str = date.today().isoformat()
    week_ago  = date.today() - timedelta(days=7)

    cumulative   = 0.0
    today_total  = 0.0
    weekly_total = 0.0
    running_rows = []

    if not log_path.exists():
        return dict(cumulative=0.0, today=0.0, weekly=0.0, running=[])

    for line in log_path.read_text().splitlines():
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5:
            continue
        row_date, session, duration, cost_str, cumul_str = parts[0], parts[1], parts[2], parts[3], parts[4]

        # Skip header and separator rows
        if row_date in ("Date", "") or re.match(r"^-+$", row_date):
            continue

        cost  = parse_cost(cost_str)
        cumul = parse_cost(cumul_str)

        # Track last valid cumulative
        if cumul > 0:
            cumulative = cumul

        # Parse date — skip rows with non-date values
        try:
            row_dt = date.fromisoformat(row_date)
        except ValueError:
            continue

        if row_dt.isoformat() == today_str:
            today_total += cost

        if row_dt >= week_ago:
            weekly_total += cost

        if duration.strip().upper() == "RUNNING":
            notes = parts[5] if len(parts) > 5 else ""
            running_rows.append({
                "date": row_date, "session": session,
                "duration": duration, "cost": cost_str,
                "cumulative": cumul_str, "notes": notes,
            })

    return dict(
        cumulative=cumulative,
        today=today_total,
        weekly=weekly_total,
        running=running_rows,
    )


# ── Session log writing ───────────────────────────────────────────────────────

def append_running_row(log_path, session_name, cumulative_before, extra_notes=""):
    now_str   = datetime.now().strftime("%H:%M")
    today_str = date.today().isoformat()
    notes     = f"Started {now_str}"
    if extra_notes:
        notes += f" | {extra_notes}"
    row = f"| {today_str} | {session_name} | RUNNING | TBD | ${cumulative_before:.2f} | {notes} |"
    with log_path.open("a") as f:
        f.write("\n" + row)
    print(f"  → Session row appended (RUNNING): {session_name}")


def close_running_row(log_path, cost, duration, notes):
    today_str = date.today().isoformat()
    text      = log_path.read_text()
    lines     = text.splitlines()

    # Find last RUNNING row for today
    target_idx = None
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 3 and parts[0] == today_str and parts[2].upper() == "RUNNING":
            target_idx = i

    if target_idx is None:
        print("WARNING: no RUNNING row found for today — appending end row directly")
        totals    = parse_session_log(log_path)
        new_cumul = totals["cumulative"] + cost
        row = f"| {today_str} | manual-close | {duration} | ${cost:.2f} | ${new_cumul:.2f} | {notes} |"
        with log_path.open("a") as f:
            f.write("\n" + row)
        return

    # Compute new cumulative from stub's cumulative-before + this session's cost
    parts = [p.strip() for p in lines[target_idx].strip("|").split("|")]
    cumul_before = parse_cost(parts[4]) if len(parts) >= 5 else 0.0
    new_cumul    = cumul_before + cost

    lines[target_idx] = (
        f"| {parts[0]} | {parts[1]} | {duration} | ${cost:.2f} | ${new_cumul:.2f} | {notes} |"
    )
    # Ensure trailing newline so subsequent appends don't concatenate onto the last row
    log_path.write_text("\n".join(lines) + "\n")
    print(f"  → RUNNING row closed: {duration}, ${cost:.2f}. Cumulative: ${new_cumul:.2f}")


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if not r.ok:
            print(f"  WARNING: Telegram returned {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"  WARNING: Telegram send failed: {e}")


def hard_stop_message(reason, current, limit):
    return (
        f"[Research Intel] 🛑 Budget limit reached\n\n"
        f"Limit: {reason}\n"
        f"Current: ${current:.2f}\n"
        f"Limit:   ${limit:.2f}\n\n"
        f"All research activity stopped. Reply to extend budget or adjust limits."
    )


def warn_message(today, today_limit, weekly, weekly_limit, total, total_limit):
    headroom_day   = max(0.0, today_limit  - today)
    headroom_week  = max(0.0, weekly_limit - weekly)
    headroom_total = max(0.0, total_limit  - total)
    return (
        f"[Research Intel] ⚠️ Budget warning\n\n"
        f"Today: ${today:.2f} / ${today_limit:.2f} | "
        f"This week: ${weekly:.2f} / ${weekly_limit:.2f} | "
        f"Total: ${total:.2f} / ${total_limit:.2f}\n"
        f"Headroom: ${headroom_day:.2f} today · "
        f"${headroom_week:.2f} this week · ${headroom_total:.2f} total\n\n"
        f"Continuing this session."
    )


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_start(args, cfg):
    budget   = cfg["budget"]
    log_path = ROOT / cfg["session_log"]
    totals   = parse_session_log(log_path)

    cumulative = totals["cumulative"]
    today      = totals["today"]
    weekly     = totals["weekly"]
    running    = totals["running"]

    token   = keyring.get_password("telegram", cfg["telegram_bot"])
    chat_id = cfg["chat_id"]

    def can_telegram():
        return bool(token and chat_id != "REPLACE_WITH_CHAT_ID")

    # ── Crash detection ───────────────────────────────────────────────────────
    orphan_warning = ""
    if running:
        names = ", ".join(r["session"] for r in running)
        orphan_warning = f"orphan RUNNING session(s) detected: [{names}]"
        print(f"WARNING: {orphan_warning}")

    # ── Hard stops (exit 1) ───────────────────────────────────────────────────
    if cumulative >= budget["total_limit"]:
        msg = hard_stop_message("Total pilot budget", cumulative, budget["total_limit"])
        print(msg)
        if can_telegram():
            send_telegram(token, chat_id, msg)
        sys.exit(1)

    if today >= budget["daily_limit"]:
        msg = hard_stop_message("Daily limit", today, budget["daily_limit"])
        print(msg)
        if can_telegram():
            send_telegram(token, chat_id, msg)
        sys.exit(1)

    if weekly >= budget["weekly_limit"]:
        msg = hard_stop_message("Weekly limit", weekly, budget["weekly_limit"])
        print(msg)
        if can_telegram():
            send_telegram(token, chat_id, msg)
        sys.exit(1)

    # ── Warn-ahead (exit 2, no Telegram) ─────────────────────────────────────
    if args.estimated_cost is not None:
        est      = args.estimated_cost
        breaches = []
        if today + est > budget["daily_limit"]:
            breaches.append(
                f"  Daily:  ${today:.2f} + ${est:.2f} estimated > ${budget['daily_limit']:.2f} limit"
            )
        if weekly + est > budget["weekly_limit"]:
            breaches.append(
                f"  Weekly: ${weekly:.2f} + ${est:.2f} estimated > ${budget['weekly_limit']:.2f} limit"
            )
        if cumulative + est > budget["total_limit"]:
            breaches.append(
                f"  Total:  ${cumulative:.2f} + ${est:.2f} estimated > ${budget['total_limit']:.2f} limit"
            )
        if breaches:
            print("WARN-AHEAD: estimated cost would breach limits:")
            for b in breaches:
                print(b)
            sys.exit(2)

    # ── Budget warnings (proceed, notify) ─────────────────────────────────────
    warned = False
    if cumulative >= budget["total_warn"] or today >= budget["daily_warn"]:
        msg = warn_message(
            today, budget["daily_limit"],
            weekly, budget["weekly_limit"],
            cumulative, budget["total_limit"],
        )
        print(msg)
        if can_telegram():
            send_telegram(token, chat_id, msg)
        warned = True

    # ── Clear ─────────────────────────────────────────────────────────────────
    if not warned:
        print(
            f"  Budget OK — "
            f"today: ${today:.2f} / ${budget['daily_limit']:.2f} | "
            f"week: ${weekly:.2f} / ${budget['weekly_limit']:.2f} | "
            f"total: ${cumulative:.2f} / ${budget['total_limit']:.2f}"
        )

    append_running_row(log_path, args.session_name, cumulative, extra_notes=orphan_warning)
    sys.exit(0)


def cmd_end(args, cfg):
    log_path = ROOT / cfg["session_log"]
    close_running_row(log_path, args.cost, args.duration, args.notes)
    sys.exit(0)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Research Intel agent session runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Check budget and open a session")
    p_start.add_argument("--session-name", required=True, help="e.g. burst, kotkin-thread")
    p_start.add_argument("--estimated-cost", type=float, default=None,
                         help="Pre-session estimate for warn-ahead check")

    p_end = sub.add_parser("end", help="Close an open session and log cost")
    p_end.add_argument("--cost",     type=float, required=True, help="Actual cost in USD")
    p_end.add_argument("--duration", required=True,             help='e.g. "4min"')
    p_end.add_argument("--notes",    required=True,             help="What happened this session")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cfg = load_config()

    if args.command == "start":
        cmd_start(args, cfg)
    elif args.command == "end":
        cmd_end(args, cfg)


if __name__ == "__main__":
    main()
