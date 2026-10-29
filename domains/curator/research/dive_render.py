"""Dive and challenger presentation; no route, filesystem or provider access."""
import re as _re
import html as _html_lib

def render_challenger(ch: dict) -> str:
    """Build the challenger process section HTML for a dive page.
    Returns empty string when show_process is false or challenger is disabled.
    """
    if not ch.get('show_process') or not ch.get('enabled'):
        return ''

    points          = ch.get('challenge_points', [])
    accepted_count  = _html_lib.escape(str(ch.get('accepted_count', 0)))
    rejected_count  = _html_lib.escape(str(ch.get('rejected_count', 0)))
    key_change      = _html_lib.escape(ch.get('key_change', ''))
    # Strip residual markdown symbols from first_pass_summary before display
    _fps_raw = ch.get('first_pass_summary', '') or ''
    _fps_raw = _re.sub(r'^#{1,6}\s+.*$', '', _fps_raw, flags=_re.MULTILINE)
    _fps_raw = _re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', _fps_raw)
    _fps_raw = _re.sub(r'\*+', '', _fps_raw)            # unpaired asterisks
    _fps_raw = _re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', _fps_raw)
    _fps_raw = _re.sub(r'_+', '', _fps_raw).strip()
    first_pass_sum  = _html_lib.escape(_fps_raw)

    points_html = ''
    for p in points:
        accepted     = p.get('accepted', False)
        border_color = '#C68A5E' if accepted else '#888'
        label_color  = '#C68A5E' if accepted else '#888'
        mark         = '✓' if accepted else '✗'
        ptype        = _html_lib.escape(p.get('type', '').replace('_', ' '))
        desc         = _html_lib.escape(p.get('description', ''))
        impact_note  = ' · high impact' if p.get('impact') == 'high' else ''
        points_html += (
            f'<div style="margin-bottom:0.5rem;padding-left:1rem;'
            f'border-left:2px solid {border_color};">'
            f'<span style="font-size:0.75rem;text-transform:uppercase;'
            f'letter-spacing:0.05em;color:{label_color};">'
            f'{mark} {ptype}{impact_note}</span><br>{desc}</div>\n'
        )

    key_change_html = (
        f'<div style="margin-top:0.75rem;font-style:italic;color:#666;">'
        f'Key change: {key_change}</div>'
        if key_change else ''
    )

    return (
        '<div class="challenger-process" '
        'style="margin-top:2rem;border-top:1px solid #C68A5E44;">\n'
        '  <details>\n'
        '    <summary style="cursor:pointer;padding:0.75rem 0;color:#C68A5E;'
        'font-size:0.85rem;letter-spacing:0.05em;text-transform:uppercase;">\n'
        f'      Challenger review\n'
        f'      <span style="color:#888;font-weight:normal;text-transform:none;letter-spacing:0;">'
        f'({len(points)} points · {accepted_count} accepted · {rejected_count} rejected)'
        f'</span>\n'
        '    </summary>\n'
        '    <div style="padding:0.75rem 0 1rem 0;font-size:0.9rem;line-height:1.6;">\n'
        f'      {points_html}{key_change_html}\n'
        '    </div>\n'
        '  </details>\n'
        '  <details>\n'
        '    <summary style="cursor:pointer;padding:0.75rem 0;color:#888;'
        'font-size:0.85rem;letter-spacing:0.05em;text-transform:uppercase;">\n'
        '      Initial draft\n'
        '    </summary>\n'
        '    <div style="padding:0.75rem 0 1rem 0;font-size:0.9rem;line-height:1.6;'
        'color:#666;font-style:italic;">\n'
        f'      {first_pass_sum or "(not stored)"}\n'
        '    </div>\n'
        '  </details>\n'
        '</div>'
    )


def render_markdown(text: str) -> str:
    lines  = text.splitlines()
    out    = []
    in_p   = False
    for line in lines:
        if _re.match(r'^#{1,6}\s', line):
            if in_p: out.append('</p>'); in_p = False
            level = len(_re.match(r'^(#+)', line).group(1))
            content = _html_lib.escape(line.lstrip('#').strip())
            out.append(f'<h{level}>{content}</h{level}>')
        elif line.strip() == '---':
            if in_p: out.append('</p>'); in_p = False
            out.append('<hr>')
        elif line.startswith('- ') or line.startswith('* '):
            if in_p: out.append('</p>'); in_p = False
            out.append(f'<li>{_html_lib.escape(line[2:].strip())}</li>')
        elif line.strip() == '':
            if in_p: out.append('</p>'); in_p = False
        else:
            # Italics from *...*
            escaped = _html_lib.escape(line.strip())
            escaped = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
            if not in_p: out.append('<p>'); in_p = True
            out.append(escaped + ' ')
    if in_p: out.append('</p>')
    return '\n'.join(out)



def render_dive(raw, title, ch_section_html):
    body_html = render_markdown(raw)
    safe_title = _html_lib.escape(title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #f5f0e8; --surface: #faf7f2; --border: #ddd6c8;
      --text: #2a2418; --text-muted: #6b5f4e; --text-dim: #9e9080;
      --accent: #8b5e2a;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Source Sans 3', sans-serif; background: var(--bg); color: var(--text); padding: 0; }}
    .page-header {{
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 0 2rem; height: 52px; display: flex; align-items: center;
      justify-content: space-between; position: sticky; top: 0; z-index: 100;
    }}
    .page-header a {{ font-family: 'Playfair Display', serif; font-size: 20px; font-weight: 600; color: var(--text); text-decoration: none; }}
    .back-link {{ font-family: 'DM Mono', monospace; font-size: 11px; color: var(--text-dim); text-decoration: none; }}
    .back-link:hover {{ color: var(--accent); }}
    .container {{ max-width: 760px; margin: 0 auto; padding: 3rem 1.5rem 5rem; }}
    h1 {{ font-family: 'Playfair Display', serif; font-size: 1.6rem; font-weight: normal; margin-bottom: 0.5rem; line-height: 1.3; }}
    h2 {{ font-family: 'Playfair Display', serif; font-size: 1.15rem; font-weight: 600; margin: 2rem 0 0.75rem; color: var(--text); }}
    h3 {{ font-family: 'DM Mono', monospace; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin: 1.5rem 0 0.5rem; }}
    p {{ font-size: 1rem; line-height: 1.7; color: var(--text); margin-bottom: 1rem; }}
    li {{ font-size: 1rem; line-height: 1.6; color: var(--text); margin-left: 1.5rem; margin-bottom: 0.35rem; list-style: disc; }}
    hr {{ border: none; border-top: 1px solid var(--border); margin: 2.5rem 0; }}
    em {{ color: var(--text-muted); font-style: italic; }}
  </style>
</head>
<body>
<header class="page-header">
  <a href="/">mini-moi</a>
  <a href="/scans-dives" class="back-link">← Scans &amp; Dives</a>
</header>
<div class="container">
{body_html}
{ch_section_html}
</div>
</body>
</html>"""
