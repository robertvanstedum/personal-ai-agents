"""Read-only archive and Scan/Dive catalog queries over explicit roots.

These preserve each view’s own metadata and partial-failure contract; they do
not replace the generated-index collector in research.scan_catalog.
"""
import json
import re

def daily_editions(archive_dir):
    """Sorted list of daily editions from curator_archive/. Most recent first."""
    editions = []
    seen = set()
    try:
        for f in sorted(archive_dir.glob('curator_*.html'), reverse=True):
            parts = f.stem.replace('curator_', '').split('-')
            if len(parts) >= 3:
                date_str = '-'.join(parts[:3])
                if date_str in seen:
                    continue
                seen.add(date_str)
                count = 0
                json_f = archive_dir / f'curator_{date_str}.json'
                if json_f.exists():
                    try:
                        count = len(json.loads(json_f.read_text()))
                    except Exception:
                        pass
                editions.append({'date': date_str, 'count': count, 'file': f.name})
    except Exception:
        pass
    return editions


def bookmarked_sources(hist_path):
    """Bookmarked articles from curator_history.json. Most recent first."""
    articles = []
    try:
        hist = json.loads(hist_path.read_text())
        for art in hist.values():
            if art.get('bookmarked'):
                articles.append({
                    'date':   art.get('bookmark_date') or art.get('first_seen', ''),
                    'title':  art.get('title', ''),
                    'source': art.get('source', ''),
                    'url':    art.get('url', ''),
                })
        articles.sort(key=lambda x: x['date'], reverse=True)
    except Exception:
        pass
    return articles


def scan_metadata(md_path):
    """Extract title, source, date, hash_id from a scan .md file."""
    text = md_path.read_text()
    title_m = re.match(r'^# (.+)', text)
    title   = title_m.group(1).strip() if title_m else md_path.stem
    source_m = re.search(r'\*\*Source:\*\*\s*(.+)', text)
    source   = source_m.group(1).strip() if source_m else ''
    date_m   = re.search(r'\*\*Date:\*\*\s*(.+)', text)
    date     = date_m.group(1).strip() if date_m else ''
    hash_id  = md_path.stem.split('-')[0]
    return {'title': title, 'source': source, 'date': date, 'hash_id': hash_id}


def scans(scans_dir, *, scan_metadata):
    """Scan list read directly from interests/2026/scans/*.md files."""
    scans = []
    try:
        raw = []
        for md in scans_dir.glob('*.md'):
            m = scan_metadata(md)
            raw.append({
                'date':   m['date'],
                'title':  m['title'],
                'source': m['source'],
                'url':    f'research/scan/{m["hash_id"]}',
            })
        scans = sorted(raw, key=lambda s: s['date'], reverse=True)
    except Exception:
        pass
    return scans


def dives(dives_dir):
    """Deeper-dive summaries from the dives data directory."""
    dives = []
    try:
        for f in sorted(dives_dir.glob('*.md'), reverse=True):
            stem  = f.stem  # e.g. hellscape-taiwan-porcupine-deeper-dive-001
            parts = stem.rsplit('-deeper-dive-', 1)
            topic = parts[0] if len(parts) == 2 else stem
            title = ' '.join(w.capitalize() for w in topic.split('-'))
            dives.append({'topic': topic, 'title': title, 'file': f.name, 'slug': stem})
    except Exception:
        pass
    return dives


def observations(workspace):
    """Daily intelligence observation dates from the supplied directory."""
    entries = []
    try:
        for f in sorted(workspace.glob('intelligence_*.json'), reverse=True):
            stem = f.stem
            if 'weekly' in stem:
                continue
            raw = stem.replace('intelligence_', '')
            if len(raw) == 8:
                date_str = f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'
                try:
                    data = json.loads(f.read_text())
                    count = len(data.get('observations', []))
                except Exception:
                    count = 0
                entries.append({'date': date_str, 'count': count})
    except Exception:
        pass
    return entries


def scans_dives_data(scans_dir, dives_dir, *, scan_metadata):
    """Build scans and dives lists directly from .md files — no index.html dependency."""
    scans, dives = [], []

    # ── Scans from interests/2026/scans/*.md ─────────────────────────────────
    try:
        raw_scans = []
        for md in scans_dir.glob('*.md'):
            m = scan_metadata(md)
            raw_scans.append({
                'href':   f'/research/scan/{m["hash_id"]}',
                'date':   m['date'],
                'source': m['source'],
                'title':  m['title'],
            })
        scans = sorted(raw_scans, key=lambda s: s['date'], reverse=True)
    except Exception:
        pass

    # ── Dives from data/dives/*.md ────────────────────────────────────────────
    try:
        raw_dives = []
        for f in dives_dir.glob('*.md'):
            text    = f.read_text()
            title_m = re.match(r'^#\s+DEEPER DIVE:\s*(.+)', text, re.IGNORECASE)
            title   = title_m.group(1).strip() if title_m else ' '.join(
                w.capitalize() for w in f.stem.split('-')
            )
            date_m  = re.search(r'Generated:\s*(\d{4}-\d{2}-\d{2})', text)
            date    = date_m.group(1) if date_m else ''
            sort_key = date if date else str(f.stat().st_mtime)
            raw_dives.append({
                'href':     f'/research/dive-result/{f.stem}',
                'title':    title,
                'excerpt':  '',
                'meta':     date,
                'date':     date,
                '_sort_key': sort_key,
            })
        dives = sorted(raw_dives, key=lambda d: d['_sort_key'], reverse=True)
        for d in dives:
            del d['_sort_key']
    except Exception:
        pass

    return dives, scans
