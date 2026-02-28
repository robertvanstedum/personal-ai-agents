#!/usr/bin/env python3
"""
cost_report.py - Unified AI cost tracker across chat and curator runs.

Reads two sources:
  - ~/.openclaw/workspace/logs/usage/daily_usage.json  (chat / Sonnet costs)
  - ~/Projects/personal-ai-agents/curator_costs.json   (curator API costs per model)

Usage:
    python cost_report.py          # today's breakdown
    python cost_report.py week     # last 7 days table
    python cost_report.py month    # last 30 days table
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHAT_LOG    = Path.home() / '.openclaw' / 'workspace' / 'logs' / 'usage' / 'daily_usage.json'
CURATOR_LOG = Path(__file__).parent / 'curator_costs.json'


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_chat_costs() -> dict:
    """Returns {date_str: cost_usd} from daily_usage.json."""
    if not CHAT_LOG.exists():
        return {}
    data = json.loads(CHAT_LOG.read_text())
    return {date: day.get('cost_usd', 0.0) for date, day in data.get('days', {}).items()}


def load_curator_costs() -> list:
    """Returns list of cost records from curator_costs.json. Creates file if missing."""
    if not CURATOR_LOG.exists():
        CURATOR_LOG.write_text(json.dumps({"runs": []}, indent=2))
        return []
    data = json.loads(CURATOR_LOG.read_text())
    return data.get('runs', [])


def curator_by_date(runs: list) -> dict:
    """Aggregate curator runs into {date: {model: cost}} structure."""
    result = defaultdict(lambda: defaultdict(float))
    for r in runs:
        result[r['date']][r['model']] += r.get('cost_usd', 0.0)
    return result


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def fmt(amount: float) -> str:
    return f"${amount:.2f}"


def report_today(chat: dict, curator_runs: list):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    month = today[:7]

    chat_today = chat.get(today, 0.0)
    curator_today = [r for r in curator_runs if r['date'] == today]

    # Aggregate curator by model
    by_model = defaultdict(lambda: {'cost': 0.0, 'runs': 0})
    for r in curator_today:
        by_model[r['model']]['cost']  += r.get('cost_usd', 0.0)
        by_model[r['model']]['runs']  += 1

    curator_total = sum(v['cost'] for v in by_model.values())
    grand_total   = chat_today + curator_total

    # Month total
    month_chat     = sum(v for k, v in chat.items() if k.startswith(month))
    month_curator  = sum(r.get('cost_usd', 0.0) for r in curator_runs if r['date'].startswith(month))
    month_total    = month_chat + month_curator

    lines = [
        f"Cost Report - {today}",
        "-" * 32,
        f"Chat (Sonnet):    {fmt(chat_today)}",
    ]

    if by_model:
        lines.append("Curator:")
        for model, v in sorted(by_model.items()):
            runs_label = f"{v['runs']} run{'s' if v['runs'] != 1 else ''}"
            lines.append(f"  {model:<22} {fmt(v['cost'])}  {runs_label}")
    else:
        lines.append("Curator:          $0.00  (no runs yet today)")

    # Last 7 days for context
    from datetime import date as date_type
    today_date = datetime.now(timezone.utc).date()
    last7 = [(today_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    c_by_d = curator_by_date(curator_runs)
    recent_lines = []
    for d in last7:
        cc = chat.get(d, 0.0)
        cur = sum(c_by_d.get(d, {}).values())
        if cc + cur > 0:
            marker = " <- today" if d == today else ""
            recent_lines.append(f"  {d}  {fmt(cc + cur)}{marker}")

    lines += [
        "-" * 32,
        f"Today total:      {fmt(grand_total)}",
        "",
        "Recent days (chat + curator):",
    ] + recent_lines + [
        "-" * 32,
        f"Month so far:     {fmt(month_total)}",
    ]
    print('\n'.join(lines))


def report_range(chat: dict, curator_runs: list, days: int, label: str):
    today  = datetime.now(timezone.utc).date()
    dates  = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days - 1, -1, -1)]
    c_by_d = curator_by_date(curator_runs)

    col_w = 10
    header = f"{'Date':<12} {'Chat':>{col_w}} {'Curator':>{col_w}} {'Total':>{col_w}}"
    sep    = "-" * len(header)

    lines = [f"Cost Report - {label}", sep, header, sep]

    tot_chat = tot_cur = 0.0
    for d in dates:
        chat_cost = chat.get(d, 0.0)
        cur_cost  = sum(c_by_d.get(d, {}).values())
        total     = chat_cost + cur_cost
        tot_chat += chat_cost
        tot_cur  += cur_cost
        # Only show rows with any spend
        marker = " <-- today" if d == today.strftime('%Y-%m-%d') else ""
        lines.append(f"{d:<12} {fmt(chat_cost):>{col_w}} {fmt(cur_cost):>{col_w}} {fmt(total):>{col_w}}{marker}")

    lines += [
        sep,
        f"{'TOTAL':<12} {fmt(tot_chat):>{col_w}} {fmt(tot_cur):>{col_w}} {fmt(tot_chat + tot_cur):>{col_w}}",
    ]

    # Model breakdown for curator
    if any(c_by_d.get(d) for d in dates):
        lines.append("")
        lines.append("Curator model breakdown:")
        model_totals = defaultdict(float)
        model_runs   = defaultdict(int)
        for r in curator_runs:
            if r['date'] in dates:
                model_totals[r['model']] += r.get('cost_usd', 0.0)
                model_runs[r['model']]   += 1
        for model in sorted(model_totals):
            n = model_runs[model]
            avg = model_totals[model] / n if n else 0
            lines.append(f"  {model:<24} {fmt(model_totals[model])}  ({n} runs, avg {fmt(avg)}/run)")

    print('\n'.join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    chat         = load_chat_costs()
    curator_runs = load_curator_costs()

    arg = sys.argv[1].lower() if len(sys.argv) > 1 else 'today'

    if arg == 'today':
        report_today(chat, curator_runs)
    elif arg == 'week':
        report_range(chat, curator_runs, days=7, label="Last 7 Days")
    elif arg == 'month':
        report_range(chat, curator_runs, days=30, label="Last 30 Days")
    else:
        print(f"Unknown argument: {arg}")
        print("Usage: python cost_report.py [today|week|month]")


if __name__ == '__main__':
    main()
