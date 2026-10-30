"""Session discovery and log interpretation with explicit inputs."""
import re as _re

def list_sessions(topic_dirs, *, pattern, excluded_prefixes):
    sessions = set()
    for topic_dir in topic_dirs:
        if not topic_dir.exists():
            continue
        sessions.update(
            p.stem
            for p in topic_dir.iterdir()
            if p.is_file()
            and pattern.match(p.name)
            and not any(p.name.startswith(pfx) for pfx in excluded_prefixes)
        )
    return sorted(sessions)


def parse_session_log(text):
    # agent_active flag — line like "agent_active: false"
    active_match = _re.search(r'^agent_active:\s*(true|false)', text,
                               _re.MULTILINE | _re.IGNORECASE)
    agent_active = bool(active_match and active_match.group(1).lower() == 'true')

    # Parse markdown table rows
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        cols = [c.strip() for c in line.split('|')[1:-1]]
        if len(cols) < 5:
            continue
        # Skip header row and separator row
        if cols[0] in ('Date', '') or _re.match(r'^-+$', cols[0]):
            continue
        try:
            cost_str = cols[3].replace('$', '').replace('—', '0').strip()
            cum_str  = cols[4].replace('$', '').replace('—', '0').strip()
            rows.append({
                "date":       cols[0],
                "session_id": cols[1],
                "duration":   cols[2],
                "cost":       float(cost_str) if cost_str else 0.0,
                "cumulative": float(cum_str)  if cum_str  else 0.0,
                "notes":      cols[5] if len(cols) > 5 else '',
            })
        except (ValueError, IndexError):
            continue

    budget_cumulative = rows[-1]['cumulative'] if rows else 0.0
    return {
        "agent_active":      agent_active,
        "budget_cumulative": budget_cumulative,
        "rows":              rows,
    }
