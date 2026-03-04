#!/usr/bin/env python3
"""
generate_test_report.py — A/B test report generator for curator experiments.

Produces two outputs from a single JSON results file:
  docs/test-reports/YYYY-MM-DD-{phase}-ab-test.html  ← private, full detail
  docs/test-reports/YYYY-MM-DD-{phase}-ab-test.md    ← private, full detail (markdown)
  docs/portfolio/{phase}-results.md                  ← public, sanitized

Usage:
    python3 scripts/generate_test_report.py --results path/to/results.json
    python3 scripts/generate_test_report.py --results path/to/results.json --phase phase3c

Results JSON schema:
{
  "phase": "phase3c",
  "date": "2026-03-03",
  "title": "Phase 3C: Bookmark Enrichment A/B Test",
  "description": "One-sentence description of what changed.",
  "profile": {
    "baseline_size": 581,
    "enriched_size": 822,
    "baseline_metrics": {"content_domains": 0, "source_types": 0, "content_topics": 0},
    "enriched_metrics": {"content_domains": 3, "source_types": 2, "content_topics": 66}
  },
  "rankings": [
    {
      "rank_baseline": 1,
      "rank_enriched": 1,
      "delta": 0,
      "title_baseline": "Article title A",
      "title_enriched": "Article title A",
      "source_baseline": "sourceA.com",
      "source_enriched": "sourceA.com",
      "score_baseline": 21.0,
      "score_enriched": 21.0,
      "category": "geopolitics",
      "comment": "Priority boost dominates"
    }
  ],
  "observations": {
    "winners": ["Description of article/pattern that moved up"],
    "losers": ["Description of article/pattern that moved down"],
    "new_top10": ["Descriptions of new entries"]
  },
  "cost": {
    "baseline_usd": 0.16,
    "enriched_usd": 0.16,
    "enrichment_overhead_usd": 0.001
  },
  "methodology": "Free text description of how the test was run."
}
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
REPORTS_DIR = PROJECT_DIR / 'docs' / 'test-reports'
PORTFOLIO_DIR = PROJECT_DIR / 'docs' / 'portfolio'


# ── Category label mapping for sanitization ───────────────────────────────────
# Maps source domain patterns → generic portfolio labels
# Extend this as new sources appear in reports.

SANITIZE_PATTERNS = [
    # Domain patterns → portfolio label
    (r'zerohedge\.com',        'market-data-A'),
    (r'ft\.com',               'intl-financial-press-A'),
    (r'reuters\.com',          'newswire-A'),
    (r'bloomberg\.com',        'intl-financial-press-B'),
    (r'federalreserve\.gov',   'central-bank-data'),
    (r'aljazeera\.com',        'regional-media-A'),
    (r'dw\.com',               'intl-broadcast-A'),
    (r'spiegel\.de',           'intl-press-A'),
    (r'faz\.net',              'intl-press-B'),
    (r'welt\.de',              'intl-press-C'),
    (r'duran\.',               'commentary-A'),
    (r'antiwar\.com',          'newswire-B'),
    (r'citriniresearch\.com',  'macro-substack-A'),
    (r'investing\.com',        'market-analysis-A'),
    (r'github\.com',           'technical-A'),
    (r'propublica\.org',       'investigative-A'),
    (r'treasury\.gov',         'govt-data-A'),
    # Account name patterns → generic labels
    (r'@LukeGromen',           '@geopolitics_account_A'),
    (r'@KobeissiLetter',       '@macro_account_B'),
    (r'@Citrini',              '@macro_account_C'),
    (r'@zerohedge',            '@market_account_A'),
]


def sanitize_text(text: str) -> str:
    """Replace identifiable sources/accounts with generic portfolio labels."""
    for pattern, replacement in SANITIZE_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def profile_growth_pct(baseline: int, enriched: int) -> str:
    if baseline == 0:
        return 'N/A'
    pct = round((enriched - baseline) / baseline * 100)
    return f'+{pct}%' if pct > 0 else f'{pct}%'


# ── Markdown generation ───────────────────────────────────────────────────────

def generate_private_md(data: dict) -> str:
    """Full-detail private markdown report."""
    p = data['profile']
    date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))
    growth = profile_growth_pct(p['baseline_size'], p['enriched_size'])

    lines = [
        f"# {data['title']}",
        f"**Date:** {date_str}",
        f"**Description:** {data['description']}",
        '',
        '---',
        '',
        '## Profile Size Comparison',
        '',
        '| Metric | Baseline | Enriched | Change |',
        '|--------|----------|----------|--------|',
        f"| **Profile size** | {p['baseline_size']} chars | {p['enriched_size']} chars | {growth} |",
    ]

    # Dynamic metrics rows
    bm = p.get('baseline_metrics', {})
    em = p.get('enriched_metrics', {})
    all_metrics = set(list(bm.keys()) + list(em.keys()))
    for metric in sorted(all_metrics):
        bval = bm.get(metric, 0)
        eval_ = em.get(metric, 0)
        delta = f'+{eval_ - bval}' if eval_ > bval else str(eval_ - bval)
        lines.append(f'| **{metric}** | {bval} | {eval_} | {delta} |')

    lines += ['', '---', '', '## Rankings Comparison', '']

    if data.get('rankings'):
        lines += [
            '| Rank A | Rank B | Δ | Article (A) | Score A | Score B | Comment |',
            '|--------|--------|---|-------------|---------|---------|---------|',
        ]
        for r in data['rankings']:
            delta = r.get('delta', 0)
            delta_str = f'**+{delta}**' if delta > 0 else (f'**{delta}**' if delta < 0 else 'Same')
            lines.append(
                f"| #{r.get('rank_baseline','?')} | #{r.get('rank_enriched','?')} | {delta_str} "
                f"| {r.get('title_baseline', r.get('title_enriched','—'))} "
                f"| {r.get('score_baseline','—')} | {r.get('score_enriched','—')} "
                f"| {r.get('comment','')} |"
            )

    obs = data.get('observations', {})
    if obs.get('winners'):
        lines += ['', '### Winners', '']
        for w in obs['winners']:
            lines.append(f'- {w}')
    if obs.get('losers'):
        lines += ['', '### Losers', '']
        for l in obs['losers']:
            lines.append(f'- {l}')

    cost = data.get('cost', {})
    if cost:
        lines += [
            '', '---', '', '## Cost',
            '',
            '| Component | Cost |',
            '|-----------|------|',
            f"| Baseline run | ${cost.get('baseline_usd', '—')} |",
            f"| Enriched run | ${cost.get('enriched_usd', '—')} |",
            f"| Enrichment overhead | ${cost.get('enrichment_overhead_usd', '—')} |",
        ]

    if data.get('methodology'):
        lines += ['', '---', '', '## Methodology', '', data['methodology']]

    return '\n'.join(lines) + '\n'


def generate_portfolio_md(data: dict) -> str:
    """Sanitized portfolio markdown — methodology intact, personal signals blurred."""
    p = data['profile']
    growth = profile_growth_pct(p['baseline_size'], p['enriched_size'])

    lines = [
        f"# {data['title']}",
        f"**Feature test:** {sanitize_text(data['description'])}",
        '',
        '---',
        '',
        '## Profile Growth',
        '',
        '| Metric | Baseline | Enriched | Change |',
        '|--------|----------|----------|--------|',
        f'| Profile prompt size | {p["baseline_size"]} chars | {p["enriched_size"]} chars | {growth} |',
    ]

    bm = p.get('baseline_metrics', {})
    em = p.get('enriched_metrics', {})
    for metric in sorted(set(list(bm.keys()) + list(em.keys()))):
        bval = bm.get(metric, 0)
        eval_ = em.get(metric, 0)
        delta = f'+{eval_ - bval}' if eval_ > bval else str(eval_ - bval)
        lines.append(f'| {metric} | {bval} | {eval_} | {delta} |')

    lines += ['', '---', '', '## Ranking Changes (Top 20)', '', '*Sources replaced with category labels.*', '']

    if data.get('rankings'):
        lines += [
            '| Rank A | Rank B | Δ | Category | Comment |',
            '|--------|--------|---|----------|---------|',
        ]
        for r in data['rankings']:
            delta = r.get('delta', 0)
            delta_str = f'+{delta}' if delta > 0 else (str(delta) if delta < 0 else '—')
            category = sanitize_text(r.get('category', r.get('source_enriched', '—')))
            comment = sanitize_text(r.get('comment', ''))
            lines.append(
                f"| #{r.get('rank_baseline','?')} | #{r.get('rank_enriched','?')} "
                f"| {delta_str} | [{category}] | {comment} |"
            )

    obs = data.get('observations', {})
    if obs.get('winners') or obs.get('losers'):
        lines += ['', '### Notable movements', '']
        for w in obs.get('winners', []):
            lines.append(f'- 📈 {sanitize_text(w)}')
        for l in obs.get('losers', []):
            lines.append(f'- 📉 {sanitize_text(l)}')

    cost = data.get('cost', {})
    if cost:
        lines += [
            '', '---', '', '## Cost Impact',
            '',
            f"Scoring cost unchanged: ${cost.get('baseline_usd', '—')} both runs. "
            f"Enrichment overhead: ${cost.get('enrichment_overhead_usd', '—')}.",
        ]

    if data.get('methodology'):
        lines += ['', '---', '', '## Methodology', '', sanitize_text(data['methodology'])]

    lines += [
        '',
        '---',
        '',
        '*Article titles, source names, and account identifiers have been replaced with category labels.*',
        '*Methodology, scoring mechanics, and cost figures are accurate.*',
    ]

    return '\n'.join(lines) + '\n'


# ── HTML generation ───────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 960px; margin: 40px auto; padding: 0 20px;
            background: #0d1117; color: #c9d1d9; line-height: 1.6; }}
    h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 12px; }}
    h2 {{ color: #79c0ff; margin-top: 32px; }}
    h3 {{ color: #d2a8ff; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 0.9em; }}
    th {{ background: #161b22; color: #58a6ff; padding: 8px 12px; text-align: left;
          border: 1px solid #30363d; }}
    td {{ padding: 8px 12px; border: 1px solid #21262d; }}
    tr:nth-child(even) {{ background: #161b22; }}
    .winner {{ color: #3fb950; }}
    .loser  {{ color: #f85149; }}
    .new    {{ color: #d2a8ff; }}
    .same   {{ color: #8b949e; }}
    .meta   {{ color: #8b949e; font-size: 0.85em; margin-bottom: 24px; }}
    code    {{ background: #161b22; padding: 2px 6px; border-radius: 4px;
               color: #79c0ff; font-size: 0.9em; }}
    pre     {{ background: #161b22; padding: 16px; border-radius: 6px;
               overflow-x: auto; color: #c9d1d9; font-size: 0.85em;
               border: 1px solid #30363d; }}
    .badge  {{ display: inline-block; padding: 2px 8px; border-radius: 12px;
               font-size: 0.8em; font-weight: 600; }}
    .badge-green  {{ background: #1f4a1f; color: #3fb950; }}
    .badge-red    {{ background: #4a1f1f; color: #f85149; }}
    .badge-blue   {{ background: #1f2d4a; color: #58a6ff; }}
    .badge-purple {{ background: #2d1f4a; color: #d2a8ff; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="meta"><strong>Date:</strong> {date} &nbsp;|&nbsp; {description}</p>

  {body}

</body>
</html>
"""


def generate_html(data: dict, sanitize: bool = False) -> str:
    """Generate HTML report. sanitize=True for portfolio version."""
    maybe_san = sanitize_text if sanitize else (lambda x: x)

    p = data['profile']
    growth = profile_growth_pct(p['baseline_size'], p['enriched_size'])

    # Profile section
    bm = p.get('baseline_metrics', {})
    em = p.get('enriched_metrics', {})
    metric_rows = ''
    for metric in sorted(set(list(bm.keys()) + list(em.keys()))):
        bval = bm.get(metric, 0)
        eval_ = em.get(metric, 0)
        delta = eval_ - bval
        delta_str = f'<span class="winner">+{delta}</span>' if delta > 0 else str(delta)
        metric_rows += f'<tr><td>{metric}</td><td>{bval}</td><td>{eval_}</td><td>{delta_str}</td></tr>'

    profile_html = f"""
<h2>Profile Growth</h2>
<table>
  <tr><th>Metric</th><th>Baseline</th><th>Enriched</th><th>Change</th></tr>
  <tr><td><strong>Profile prompt size</strong></td><td>{p['baseline_size']} chars</td>
      <td>{p['enriched_size']} chars</td>
      <td><strong><span class="winner">{growth}</span></strong></td></tr>
  {metric_rows}
</table>
"""

    # Rankings section
    ranking_rows = ''
    if data.get('rankings'):
        for r in data['rankings']:
            delta = r.get('delta', 0)
            if delta > 0:
                delta_html = f'<span class="badge badge-green">+{delta}</span>'
            elif delta < 0:
                delta_html = f'<span class="badge badge-red">{delta}</span>'
            else:
                delta_html = '<span class="badge badge-blue">same</span>'

            if sanitize:
                title_a = f"[{maybe_san(r.get('category', r.get('source_baseline','—')))}]"
                title_b = f"[{maybe_san(r.get('category', r.get('source_enriched','—')))}]"
            else:
                title_a = maybe_san(r.get('title_baseline', r.get('title_enriched', '—')))
                title_b = maybe_san(r.get('title_enriched', r.get('title_baseline', '—')))

            comment = maybe_san(r.get('comment', ''))
            ranking_rows += (
                f"<tr><td>#{r.get('rank_baseline','?')}</td>"
                f"<td>#{r.get('rank_enriched','?')}</td>"
                f"<td>{delta_html}</td>"
                f"<td>{title_a}</td>"
                f"<td>{r.get('score_baseline','—')}</td>"
                f"<td>{title_b}</td>"
                f"<td>{r.get('score_enriched','—')}</td>"
                f"<td>{comment}</td></tr>"
            )

    rankings_html = f"""
<h2>Rankings Comparison (Top 20)</h2>
{'<p><em>Sources replaced with category labels.</em></p>' if sanitize else ''}
<table>
  <tr><th>Rank A</th><th>Rank B</th><th>Δ</th>
      <th>Article A</th><th>Score A</th><th>Article B</th><th>Score B</th><th>Comment</th></tr>
  {ranking_rows}
</table>
""" if data.get('rankings') else ''

    # Observations
    obs = data.get('observations', {})
    obs_html = ''
    if obs.get('winners') or obs.get('losers'):
        obs_html = '<h2>Key Observations</h2>'
        if obs.get('winners'):
            obs_html += '<h3>📈 Winners</h3><ul>'
            for w in obs['winners']:
                obs_html += f'<li class="winner">{maybe_san(w)}</li>'
            obs_html += '</ul>'
        if obs.get('losers'):
            obs_html += '<h3>📉 Losers</h3><ul>'
            for l in obs['losers']:
                obs_html += f'<li class="loser">{maybe_san(l)}</li>'
            obs_html += '</ul>'
        if obs.get('new_top10'):
            obs_html += '<h3>🆕 New Top-10 Entries</h3><ul>'
            for n in obs['new_top10']:
                obs_html += f'<li class="new">{maybe_san(n)}</li>'
            obs_html += '</ul>'

    # Cost
    cost = data.get('cost', {})
    cost_html = ''
    if cost:
        cost_html = f"""
<h2>Cost</h2>
<table>
  <tr><th>Component</th><th>Cost</th></tr>
  <tr><td>Baseline run</td><td>${cost.get('baseline_usd','—')}</td></tr>
  <tr><td>Enriched run</td><td>${cost.get('enriched_usd','—')}</td></tr>
  <tr><td>Enrichment overhead</td><td>${cost.get('enrichment_overhead_usd','—')}</td></tr>
</table>
"""

    # Methodology
    meth_html = ''
    if data.get('methodology'):
        meth_html = f'<h2>Methodology</h2><p>{maybe_san(data["methodology"])}</p>'

    body = profile_html + rankings_html + obs_html + cost_html + meth_html

    if sanitize:
        body += '<p><em>Article titles, source names, and account identifiers have been replaced with category labels. Methodology, scoring mechanics, and cost figures are accurate.</em></p>'

    return HTML_TEMPLATE.format(
        title=data['title'],
        date=data.get('date', ''),
        description=maybe_san(data.get('description', '')),
        body=body,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate A/B test reports from JSON results')
    parser.add_argument('--results', required=True, help='Path to results JSON file')
    parser.add_argument('--phase',   default=None,  help='Phase label override (default: from JSON)')
    parser.add_argument('--html',    action='store_true',
                        help='Also generate HTML version (default: MD only)')
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        print(f'Error: {results_path} not found')
        return 1

    data = json.loads(results_path.read_text())
    phase = args.phase or data.get('phase', 'test')
    date  = data.get('date', datetime.now().strftime('%Y-%m-%d'))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)

    # Private MD (always)
    private_md_path = REPORTS_DIR / f'{date}-{phase}-ab-test.md'
    private_md_path.write_text(generate_private_md(data))
    print(f'✅ Private MD:   {private_md_path}')

    # Private HTML (opt-in)
    if args.html:
        private_html_path = REPORTS_DIR / f'{date}-{phase}-ab-test.html'
        private_html_path.write_text(generate_html(data, sanitize=False))
        print(f'✅ Private HTML: {private_html_path}')

    # Portfolio MD (always — sanitized)
    portfolio_md_path = PORTFOLIO_DIR / f'{phase}-results.md'
    portfolio_md_path.write_text(generate_portfolio_md(data))
    print(f'✅ Portfolio MD: {portfolio_md_path}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
