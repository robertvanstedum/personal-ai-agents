"""Interpret Scan documents and format follow-up research material.

Callers select and authorize document paths. These operations only read the
supplied file or transform supplied content; no route, job or store discovery.
"""
import re as _re
from pathlib import Path

from domains.curator.research.document_text import render_analysis_html as _dd_md_to_html


def parse_bibliography(md_path: Path) -> list:
    """
    Parse '## 7. Sources & Further Reading' (or similar) from a deep dive .md.
    Returns list of {raw, title, url, has_url, author, bracket_note}.
    """
    text = md_path.read_text()
    m = _re.search(
        r'##\s+\d*\.?\s*Sources[^\n]*\n(.*?)(?=\n## |\Z)',
        text, _re.DOTALL | _re.IGNORECASE
    )
    if not m:
        return []

    items = []
    for line in m.group(1).splitlines():
        line = line.strip().lstrip('- ').strip()
        if not line or line.startswith('---') or line.startswith('*Generated'):
            continue

        title_m   = _re.search(r'"([^"]+)"',   line)
        url_m     = _re.search(r'(https?://\S+)', line)
        bracket_m = _re.search(r'\[([^\]]+)\]', line)

        title        = title_m.group(1)   if title_m   else ''
        url          = url_m.group(1)     if url_m     else None
        bracket_note = bracket_m.group(1) if bracket_m else None

        author = ''
        if title_m:
            author = line[:title_m.start()].strip().rstrip('.,')

        items.append({
            'raw':          line,
            'title':        title,
            'url':          url,
            'has_url':      bool(url),
            'author':       author,
            'bracket_note': bracket_note,
        })

    return items


def parse_scan(md_path: Path) -> dict:
    """Parse a deep dive .md file into structured data for Flask rendering."""
    text  = md_path.read_text()
    lines = text.split('\n')

    title = lines[0].replace('# ', '').strip() if lines else 'Untitled'

    metadata: dict = {}
    for line in lines[1:20]:
        for key, marker in [
            ('source',   '**Source:**'),
            ('url',      '**URL:**'),
            ('hash_id',  '**Hash ID:**'),
            ('interest', '**Your Interest:**'),
            ('focus',    '**Focus:**'),
            ('date',     '**Analyzed:**'),
            ('date',     '**Date:**'),
        ]:
            if line.startswith(marker):
                metadata[key] = line.replace(marker, '').strip()

    # Also extract ## Your Interest section (multi-line, common format)
    if 'interest' not in metadata or not metadata['interest']:
        interest_m = _re.search(
            r'##\s+Your Interest\s*\n(.*?)(?=\n\*\*Focus:\*\*|\n---|\n## |\Z)',
            text, _re.DOTALL | _re.IGNORECASE
        )
        if interest_m:
            metadata['interest'] = interest_m.group(1).strip()

    # Analysis: everything from first ## up to Sources section
    analysis_start = 0
    for i, line in enumerate(lines):
        if line.startswith('## '):
            analysis_start = i
            break
    analysis_raw = '\n'.join(lines[analysis_start:])
    # Strip ## Your Interest section — already shown in the meta card
    analysis_raw = _re.sub(
        r'^##\s+Your Interest\s*\n.*?(?=\n## )',
        '', analysis_raw, flags=_re.DOTALL | _re.IGNORECASE
    ).lstrip('\n -').strip()
    sources_m = _re.search(r'\n##\s+\d*\.?\s*Sources[^\n]*', analysis_raw, _re.IGNORECASE)
    if sources_m:
        analysis_raw = analysis_raw[:sources_m.start()]

    cost_m = _re.search(r'(\d+) input \+ (\d+) output tokens.*?\$(\d+\.\d+)', text)

    return {
        'title':        title,
        'metadata':     metadata,
        'analysis_html': _dd_md_to_html(analysis_raw),
        'bibliography':  parse_bibliography(md_path),
        'cost':         cost_m.group(3) if cost_m else None,
        'tokens_in':    cost_m.group(1) if cost_m else None,
        'tokens_out':   cost_m.group(2) if cost_m else None,
    }


def build_session(dd: dict, hash_id: str, topic: str) -> str:
    """Build a synthetic research session .md from a parsed scan for use by generate_dive.py."""
    from datetime import date as _date
    today = _date.today().isoformat()

    # Extract findings from numbered scan analysis sections (### 1. Title + paragraph)
    # Prefer section-title + first sentence of body so findings are coherent, not fragments.
    analysis_html = dd.get('analysis_html', '')
    from bs4 import BeautifulSoup as _BS
    _soup = _BS(analysis_html, 'html.parser')
    findings = []
    for h in _soup.find_all(['h3', 'h2']):
        heading_text = h.get_text(strip=True)
        # Only numbered sections (e.g. "1. Why This Matters")
        if not _re.match(r'^\d+\.', heading_text):
            continue
        # Get the first non-empty paragraph or list item after the heading
        sibling = h.find_next_sibling()
        while sibling:
            text = sibling.get_text(' ', strip=True)
            if len(text) > 40:
                # Take first sentence (up to 220 chars)
                sentence = _re.split(r'(?<=[.!?])\s+', text)[0][:220]
                findings.append(f"{heading_text}: {sentence}")
                break
            sibling = sibling.find_next_sibling()
        if len(findings) >= 5:
            break
    # Fallback: strip HTML and grab long lines
    if not findings:
        plain = _re.sub(r'<[^>]+>', '\n', analysis_html)
        for line in _re.sub(r'\n{2,}', '\n', plain).strip().splitlines():
            line = line.strip()
            if len(line) > 60 and not _re.match(r'^\d+\.', line) and not line.startswith('#'):
                findings.append(line)
            if len(findings) >= 5:
                break
    findings_text = '\n'.join(f'- {f}' for f in findings) or '- (see scan analysis)'

    # Build sources in the [N] domain. "title".\n    url format that generate_dive.py parses
    bib = dd.get('bibliography', [])
    src_lines = []
    for i, item in enumerate(bib[:10], 1):
        domain = item.get('domain', '') or ''
        if not domain and item.get('url'):
            domain = _re.sub(r'^https?://(www\.)?', '', item['url'] or '').split('/')[0]
        title_s = item.get('title') or item.get('raw', '')[:80]
        url_s   = item.get('url', '') or ''
        src_lines.append(f'[{i}] {domain}. "{title_s}".')
        if url_s:
            src_lines.append(f'    {url_s}')
    sources_text = '\n'.join(src_lines) if src_lines else '(none recorded)'

    return f"""# Session Findings: {topic}-001

<!-- MACHINE-READABLE HEADER — do not remove or reorder these lines -->
date: {today}
session: {topic}-001
topic: {topic}
cost: $0.0000
duration: 1min
sources_reviewed: {len(bib)}
scan_synthetic: true
<!-- END HEADER -->

## Research Question

Analysis of scan {hash_id}: {dd['title']}

---

## Key Findings

{findings_text}

---

## Sources

{sources_text}

---

## Threads to Continue

- Deeper investigation of: {dd['title']}

---

*Generated from scan {hash_id} on {today}*
"""


def research_directions(dd: dict) -> tuple[list[str], list[str]]:
    """Derive topic-specific searches and triage targets from a Scan."""
    from bs4 import BeautifulSoup as _BS

    title = (dd.get('title') or 'Untitled scan').strip()
    motivation = (dd.get('metadata') or {}).get('interest', '').strip()
    focus = (dd.get('metadata') or {}).get('focus', '').strip()
    soup = _BS(dd.get('analysis_html', ''), 'html.parser')
    questions: list[str] = []

    for heading in soup.find_all(['h2', 'h3']):
        if 'next question' not in heading.get_text(' ', strip=True).lower():
            continue
        sibling = heading.find_next_sibling()
        while sibling and sibling.name not in ('h2', 'h3'):
            if sibling.name in ('ul', 'ol'):
                questions.extend(
                    item.get_text(' ', strip=True)
                    for item in sibling.find_all('li', recursive=False)
                    if item.get_text(' ', strip=True)
                )
            elif sibling.name == 'li':
                text = sibling.get_text(' ', strip=True)
                if text:
                    questions.append(text)
            sibling = sibling.find_next_sibling()

    # Preserve order while removing duplicates and blank entries.
    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))
    if not questions:
        questions = [
            f"What primary evidence supports or challenges the claims in {title}?",
            f"What mechanisms explain the central outcome discussed in {title}?",
            f"What historical or empirical comparisons best test the argument in {title}?",
        ]

    searches = list(dict.fromkeys([title, *questions]))[:8]
    targets = list(dict.fromkeys(
        [item for item in (motivation, focus, *questions) if item]
    ))[:6]
    return searches, targets
