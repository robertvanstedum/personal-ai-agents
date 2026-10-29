"""Pure parsing and display formatting for Research document text.

Callers select and read authorized documents. This module only transforms the
supplied strings; it does not resolve paths, access stores or load Flask.
"""
import html as _html_lib
import re as _re


def parse_session_header(text: str) -> dict:
    """Extract the machine-readable header block from a session .md file."""
    header = {}
    m = _re.search(
        r'<!-- MACHINE-READABLE HEADER.*?-->(.*?)<!-- END HEADER -->',
        text, _re.DOTALL
    )
    if m:
        for line in m.group(1).strip().splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                header[k.strip()] = v.strip()
    return header


def parse_session_sections(text: str) -> dict:
    """
    Parse a session .md file into structured sections.
    Returns dict with keys: findings, sources, threads, agent_notes, cost_table
    Each finding is a plain string. Each source is {num, domain, title, url}.
    """
    sections: dict = {
        'findings': [],
        'sources': [],
        'threads': [],
        'agent_notes': '',
        'cost_table': '',
    }

    # ── Key Findings ─────────────────────────────────────────────────────────
    fm = _re.search(r'## Key Findings\s*\n(.*?)(?=\n## |\Z)', text, _re.DOTALL)
    if fm:
        for line in fm.group(1).splitlines():
            line = line.strip().lstrip('- ').strip()
            if line:
                sections['findings'].append(line)

    # ── Sources ──────────────────────────────────────────────────────────────
    sm = _re.search(r'## Sources\s*\n(.*?)(?=\n## |\Z)', text, _re.DOTALL)
    if sm:
        entries = _re.findall(
            r'\[(\d+)\]\s+(\S+)\.\s+"([^"]+)"\.\s*\n\s*(https?://\S+)',
            sm.group(1)
        )
        for num, domain, title, url in entries:
            sections['sources'].append({
                'num': int(num), 'domain': domain,
                'title': title, 'url': url
            })

    # ── Threads to Continue ───────────────────────────────────────────────────
    tm = _re.search(r'## Threads to Continue\s*\n(.*?)(?=\n## |\Z)', text, _re.DOTALL)
    if tm:
        for line in tm.group(1).splitlines():
            line = line.strip().lstrip('- ').strip()
            if line:
                sections['threads'].append(line)

    # ── Agent Notes ───────────────────────────────────────────────────────────
    am = _re.search(r'## Agent Notes\s*\n(.*?)(?=\n## |\Z)', text, _re.DOTALL)
    if am:
        sections['agent_notes'] = am.group(1).strip()

    # ── Cost Breakdown ────────────────────────────────────────────────────────
    cm = _re.search(r'## Cost Breakdown\s*\n(.*?)(?=\n---|\Z)', text, _re.DOTALL)
    if cm:
        sections['cost_table'] = cm.group(1).strip()

    return sections


def escape_inline(text: str) -> str:
    """HTML-escape and convert **bold** / *italic* inline markdown."""
    text = _html_lib.escape(str(text))
    text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = _re.sub(r'\*([^*]+?)\*',   r'<em>\1</em>',        text)
    return text


def render_analysis_html(text: str) -> str:
    """
    Lightweight markdown→HTML for deep dive analysis sections.
    Handles: ## h2, ### h3, - list items, --- hr, paragraphs, blank lines.
    Excludes the Sources section (handled separately with buttons).
    """
    lines    = text.split('\n')
    parts    = []
    in_ul    = False
    buf_para = []

    def flush_para():
        if buf_para:
            parts.append(f'<p>{escape_inline(" ".join(buf_para))}</p>')
            buf_para.clear()

    def close_ul():
        nonlocal in_ul
        if in_ul:
            parts.append('</ul>')
            in_ul = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('## '):
            close_ul(); flush_para()
            parts.append(f'<h2>{escape_inline(stripped[3:])}</h2>')
        elif stripped.startswith('### '):
            close_ul(); flush_para()
            parts.append(f'<h3>{escape_inline(stripped[4:])}</h3>')
        elif stripped.startswith('- ') or stripped.startswith('* '):
            flush_para()
            if not in_ul:
                parts.append('<ul>')
                in_ul = True
            parts.append(f'<li>{escape_inline(stripped[2:])}</li>')
        elif stripped.startswith('---'):
            close_ul(); flush_para()
            parts.append('<hr>')
        elif stripped == '':
            close_ul(); flush_para()
        else:
            if in_ul:
                close_ul()
            buf_para.append(stripped)

    close_ul(); flush_para()
    return '\n'.join(parts)
