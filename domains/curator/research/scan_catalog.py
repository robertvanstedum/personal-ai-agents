"""Read supplied Scan/Dive directories; never publish or discover configuration."""
import re
from datetime import datetime

def collect_catalog(scans_dir, dives_dir, *, now):
    # ── Collect Scans (article-level analyses) ───────────────────────────────
    scans = []
    for md_file in scans_dir.glob("*.md"):
        with open(md_file, 'r') as f:
            content = f.read()

        title_match  = re.search(r'^# (.+)$', content, re.MULTILINE)
        source_match = re.search(r'\*\*Source:\*\* (.+)$', content, re.MULTILINE)
        date_match   = re.search(r'\*\*Date:\*\* (.+)$', content, re.MULTILINE)

        if title_match and source_match and date_match:
            title    = title_match.group(1).strip()
            source   = source_match.group(1).strip()
            date_str = date_match.group(1).strip()
            hash_id  = md_file.stem[:5]
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                date_obj = now()

            scans.append({
                'title':   title,
                'source':  source,
                'date':    date_obj,
                'hash_id': hash_id,
            })

    scans.sort(key=lambda x: x['date'], reverse=True)

    # ── Collect Dives (thread-level analyses) ────────────────────────────────
    research_dd_dir = dives_dir
    dives = []

    if research_dd_dir.exists():
        for md_file in research_dd_dir.glob("*.md"):
            with open(md_file, 'r') as f:
                content = f.read()

            title_m    = re.search(r'^#\s+DEEPER DIVE:\s+(.+)$', content, re.MULTILINE)
            date_m     = re.search(r'Generated:\s*(\d{4}-\d{2}-\d{2})', content)
            sessions_m = re.search(r'(\d+)\s+sessions?', content)
            sources_m  = re.search(r'(\d+)\s+sources?', content)
            cost_m     = re.search(r'Est\.\s*cost:\s*\$([0-9.]+)', content)

            if not (title_m and date_m):
                continue

            topic    = title_m.group(1).strip()
            date_str = date_m.group(1).strip()
            sessions = sessions_m.group(1) if sessions_m else '?'
            sources  = sources_m.group(1)  if sources_m  else '?'
            cost     = f'${cost_m.group(1)}' if cost_m else ''
            stem     = md_file.stem

            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                date_obj = now()

            # Extract first sentence from Revised Framing section
            excerpt = ''
            framing_m = re.search(
                r'##\s+[0-9.]*\s*REVISED FRAMING[^\n]*\n+(.*?)(?=\n##|\Z)',
                content, re.DOTALL | re.IGNORECASE
            )
            if framing_m:
                raw = framing_m.group(1).strip()
                # Strip markdown bold/italic markers
                raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
                raw = re.sub(r'\*(.+?)\*', r'\1', raw)
                raw = re.sub(r'^#+\s+', '', raw, flags=re.MULTILINE)
                # Take first non-empty line
                for line in raw.splitlines():
                    line = line.strip()
                    if line:
                        excerpt = line[:160] + ('…' if len(line) > 160 else '')
                        break

            stats_parts = [f'{sessions} sessions', f'{sources} sources']
            if cost:
                stats_parts.append(cost)
            stats = ' · '.join(stats_parts)

            dives.append({
                'topic':   topic,
                'stem':    stem,
                'date':    date_obj,
                'stats':   stats,
                'excerpt': excerpt,
            })

    dives.sort(key=lambda x: x['date'], reverse=True)

    return scans, dives
