#!/usr/bin/env python3
"""
cost_report.py - Unified AI cost tracker across chat and curator runs.

Reads two sources:
  - ~/.openclaw/workspace/logs/usage/daily_usage.json  (chat / Sonnet costs)
  - ~/Projects/personal-ai-agents/curator_costs.json   (curator API costs per model)

Usage:
    python cost_report.py          # today's breakdown
    python cost_report.py week     # last 7 days, day by day
    python cost_report.py month    # this calendar month, day by day
    python cost_report.py year     # this calendar year, month by month
"""

import json
import sys
import calendar
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


def fmt(amount: float) -> str:
    return f"${amount:.2f}"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_today(chat: dict, curator_runs: list):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    month = today[:7]

    chat_today    = chat.get(today, 0.0)
    curator_today = [r for r in curator_runs if r['date'] == today]

    by_model = defaultdict(lambda: {'cost': 0.0, 'runs': 0})
    for r in curator_today:
        by_model[r['model']]['cost'] += r.get('cost_usd', 0.0)
        by_model[r['model']]['runs'] += 1

    curator_total = sum(v['cost'] for v in by_model.values())
    grand_total   = chat_today + curator_total

    month_chat    = sum(v for k, v in chat.items() if k.startswith(month))
    month_curator = sum(r.get('cost_usd', 0.0) for r in curator_runs if r['date'].startswith(month))
    month_total   = month_chat + month_curator

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
    today_date = datetime.now(timezone.utc).date()
    last7      = [(today_date - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    c_by_d     = curator_by_date(curator_runs)
    recent     = []
    for d in last7:
        total = chat.get(d, 0.0) + sum(c_by_d.get(d, {}).values())
        if total > 0:
            marker = " <- today" if d == today else ""
            recent.append(f"  {d}  {fmt(total)}{marker}")

    lines += [
        "-" * 32,
        f"Today total:      {fmt(grand_total)}",
        "",
        "Recent days (chat + curator):",
    ] + recent + [
        "-" * 32,
        f"Month so far:     {fmt(month_total)}",
    ]
    print('\n'.join(lines))


def report_days(chat: dict, curator_runs: list, dates: list, label: str):
    """Day-by-day table for a list of date strings."""
    c_by_d   = curator_by_date(curator_runs)
    today    = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    col_w    = 10
    header   = f"{'Date':<12} {'Chat':>{col_w}} {'Curator':>{col_w}} {'Total':>{col_w}}"
    sep      = "-" * len(header)

    lines = [f"Cost Report - {label}", sep, header, sep]

    tot_chat = tot_cur = 0.0
    for d in dates:
        cc    = chat.get(d, 0.0)
        cur   = sum(c_by_d.get(d, {}).values())
        tot   = cc + cur
        tot_chat += cc
        tot_cur  += cur
        marker = " <- today" if d == today else ""
        lines.append(f"{d:<12} {fmt(cc):>{col_w}} {fmt(cur):>{col_w}} {fmt(tot):>{col_w}}{marker}")

    lines += [
        sep,
        f"{'TOTAL':<12} {fmt(tot_chat):>{col_w}} {fmt(tot_cur):>{col_w}} {fmt(tot_chat + tot_cur):>{col_w}}",
    ]

    # Curator model breakdown if any curator data exists
    curator_in_range = [r for r in curator_runs if r['date'] in dates]
    if curator_in_range:
        model_totals = defaultdict(float)
        model_runs   = defaultdict(int)
        for r in curator_in_range:
            model_totals[r['model']] += r.get('cost_usd', 0.0)
            model_runs[r['model']]   += 1
        lines.append("")
        lines.append("Curator by model:")
        for model in sorted(model_totals):
            n   = model_runs[model]
            avg = model_totals[model] / n if n else 0
            lines.append(f"  {model:<24} {fmt(model_totals[model])}  ({n} runs, avg {fmt(avg)}/run)")

    print('\n'.join(lines))


def report_year(chat: dict, curator_runs: list):
    """Month-by-month table for the current calendar year."""
    year     = datetime.now(timezone.utc).year
    today    = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cur_month = today[:7]

    col_w  = 10
    header = f"{'Month':<12} {'Chat':>{col_w}} {'Curator':>{col_w}} {'Total':>{col_w}}"
    sep    = "-" * len(header)
    lines  = [f"Cost Report - {year}", sep, header, sep]

    tot_chat = tot_cur = 0.0
    for m in range(1, 13):
        month_str = f"{year}-{m:02d}"
        # Skip future months
        if month_str > cur_month:
            break
        cc  = sum(v for k, v in chat.items() if k.startswith(month_str))
        cur = sum(r.get('cost_usd', 0.0) for r in curator_runs if r['date'].startswith(month_str))
        tot = cc + cur
        tot_chat += cc
        tot_cur  += cur
        month_label = datetime(year, m, 1).strftime('%b %Y')
        marker = " <- current" if month_str == cur_month else ""
        lines.append(f"{month_label:<12} {fmt(cc):>{col_w}} {fmt(cur):>{col_w}} {fmt(tot):>{col_w}}{marker}")

    lines += [
        sep,
        f"{'TOTAL':<12} {fmt(tot_chat):>{col_w}} {fmt(tot_cur):>{col_w}} {fmt(tot_chat + tot_cur):>{col_w}}",
    ]
    print('\n'.join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    chat         = load_chat_costs()
    curator_runs = load_curator_costs()
    arg          = sys.argv[1].lower() if len(sys.argv) > 1 else 'today'
    now          = datetime.now(timezone.utc)

    if arg == 'today':
        report_today(chat, curator_runs)

    elif arg == 'week':
        today = now.date()
        dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
        report_days(chat, curator_runs, dates, "Last 7 Days")

    elif arg == 'month':
        month_str = now.strftime('%Y-%m')
        _, days_in_month = calendar.monthrange(now.year, now.month)
        dates = [f"{month_str}-{d:02d}" for d in range(1, days_in_month + 1)
                 if f"{month_str}-{d:02d}" <= now.strftime('%Y-%m-%d')]
        report_days(chat, curator_runs, dates, now.strftime('%B %Y'))

    elif arg == 'year':
        report_year(chat, curator_runs)

    else:
        print(f"Unknown argument: {arg}")
        print("Usage: python cost_report.py [today|week|month|year]")


if __name__ == '__main__':
    main()
