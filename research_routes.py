"""
research_routes.py — Flask Blueprint for Research Intelligence UI

All /research/* and /api/research/* routes live here.
Registered in curator_server.py with a try/except guard so any import
failure here cannot crash the main curator server.

To add new research routes: edit only this file. Never touch curator_server.py
for research features.
"""

import json
import re as _re
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

# ── Blueprint ─────────────────────────────────────────────────────────────────

research_bp = Blueprint('research', __name__)

# Path to the research-intelligence project (relative to this file's location)
RESEARCH_ROOT = Path(__file__).resolve().parent / '_NewDomains' / 'research-intelligence'

# Ensure agent/ modules are importable — done once at import time, not per-request
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))


# ── HTML pages ────────────────────────────────────────────────────────────────

@research_bp.route('/research/observe')
def research_observe_ui():
    """Serve the observation trigger page."""
    html = RESEARCH_ROOT / 'web' / 'observe.html'
    if not html.exists():
        return "observe.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


@research_bp.route('/research/candidates')
def research_candidates_ui():
    """Serve the query candidates review page."""
    html = RESEARCH_ROOT / 'web' / 'candidates.html'
    if not html.exists():
        return "candidates.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


@research_bp.route('/research/save')
def research_save_ui():
    """Serve the article save form."""
    html = RESEARCH_ROOT / 'web' / 'save.html'
    if not html.exists():
        return "save.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


# ── API: Observe ──────────────────────────────────────────────────────────────

@research_bp.route('/api/research/observe', methods=['POST'])
def api_research_observe():
    """
    Trigger an AI observation synthesis for a research topic.
    Runs agent/observe.py as a subprocess — keeps Flask worker clean,
    prevents a hung Sonnet call from blocking the server.

    Body: {"topic": "empire-landpower", "command": "observe"}
    Returns: {ok, text, tokens_in, tokens_out, cost_usd, output_file, saved_articles_used}
    """
    data    = request.get_json() or {}
    topic   = data.get("topic", "").strip()
    command = data.get("command", "observe").strip()

    if not topic:
        return jsonify({"ok": False, "error": "topic is required"}), 400
    if command not in ("observe", "status"):
        return jsonify({"ok": False, "error": f"unknown command: {command}"}), 400

    observe_script = RESEARCH_ROOT / 'agent' / 'observe.py'
    if not observe_script.exists():
        return jsonify({"ok": False, "error": "observe.py not found"}), 500

    try:
        result = subprocess.run(
            [sys.executable, str(observe_script),
             "--topic", topic, "--command", command],
            capture_output=True, text=True, timeout=120,
            cwd=str(RESEARCH_ROOT),
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "observe.py exited non-zero"
            return jsonify({"ok": False, "error": err}), 500

        payload = json.loads(result.stdout)
        return jsonify(payload)

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Sonnet call timed out (120s)"}), 504
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "observe.py returned non-JSON output",
                        "raw": result.stdout[:500]}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── API: Observations archive ────────────────────────────────────────────────

@research_bp.route('/api/research/observations')
def api_research_observations_list():
    """
    List past observation files, optionally filtered by topic.
    GET /api/research/observations?topic=empire-landpower

    Filename pattern: {topic}-{command}-{timestamp}.md
    Returns: {ok, observations: [{topic, command, timestamp, filename, generated, cost}]}
    """
    topic_filter = request.args.get('topic', '').strip()
    obs_dir = RESEARCH_ROOT / 'data' / 'observations'

    if not obs_dir.exists():
        return jsonify({"ok": True, "observations": []})

    results = []
    for f in sorted(obs_dir.glob('*.md'), reverse=True):
        # Parse filename: {topic}-{command}-{timestamp}.md
        # command is 'observe' or 'status'; timestamp contains hyphens too
        # Strategy: split on '-observe-' or '-status-'
        name = f.stem
        command = None
        for cmd in ('observe', 'status'):
            marker = f'-{cmd}-'
            if marker in name:
                idx = name.index(marker)
                topic = name[:idx]
                timestamp = name[idx + len(marker):]
                command = cmd
                break
        if not command:
            continue
        if topic_filter and topic != topic_filter:
            continue

        # Extract metadata from file header (first 8 lines)
        meta = {'generated': '', 'cost': ''}
        try:
            lines = f.read_text().splitlines()[:8]
            for line in lines:
                if line.startswith('**Generated:**'):
                    meta['generated'] = line.replace('**Generated:**', '').strip()
                elif line.startswith('**Cost:**'):
                    meta['cost'] = line.replace('**Cost:**', '').strip()
        except Exception:
            pass

        results.append({
            'topic':     topic,
            'command':   command,
            'timestamp': timestamp,
            'filename':  f.name,
            'generated': meta['generated'],
            'cost':      meta['cost'],
        })

    return jsonify({"ok": True, "observations": results})


@research_bp.route('/api/research/observations/<filename>')
def api_research_observation_detail(filename: str):
    """
    Return full content of a single observation file.
    GET /api/research/observations/empire-landpower-observe-2026-03-25T13-49-36.md

    Returns: {ok, filename, topic, command, generated, cost, tokens_in, tokens_out,
              model, content_md}
    """
    obs_dir = RESEARCH_ROOT / 'data' / 'observations'
    # Sanitise filename — no path traversal
    safe = Path(filename).name
    f = obs_dir / safe
    if not f.exists():
        return jsonify({"ok": False, "error": f"observation '{safe}' not found"}), 404

    text = f.read_text()
    lines = text.splitlines()

    meta = {'generated': '', 'model': '', 'tokens_in': '', 'tokens_out': '', 'cost': ''}
    for line in lines[:10]:
        for key, marker in [
            ('generated',  '**Generated:**'),
            ('model',      '**Model:**'),
            ('cost',       '**Cost:**'),
        ]:
            if line.startswith(marker):
                meta[key] = line.replace(marker, '').strip()
        if line.startswith('**Tokens:**'):
            # Format: "615 in / 577 out"
            tok = line.replace('**Tokens:**', '').strip()
            m = _re.match(r'(\d+)\s+in\s*/\s*(\d+)\s+out', tok)
            if m:
                meta['tokens_in'], meta['tokens_out'] = m.group(1), m.group(2)

    # Strip the header block (everything up to and including first '---')
    sep = text.find('\n---\n')
    content_md = text[sep + 5:].strip() if sep >= 0 else text

    # Parse topic/command from filename stem
    name = f.stem
    topic, command = '', 'observe'
    for cmd in ('observe', 'status'):
        marker = f'-{cmd}-'
        if marker in name:
            topic = name[:name.index(marker)]
            command = cmd
            break

    return jsonify({
        "ok":          True,
        "filename":    safe,
        "topic":       topic,
        "command":     command,
        "generated":   meta['generated'],
        "model":       meta['model'],
        "cost":        meta['cost'],
        "tokens_in":   meta['tokens_in'],
        "tokens_out":  meta['tokens_out'],
        "content_md":  content_md,
    })


# ── API: Candidates ───────────────────────────────────────────────────────────

@research_bp.route('/api/research/candidates')
def api_research_candidates():
    """Return query candidates filtered by ?topic= and ?status= (default: candidate)."""
    from agent.candidates import load as load_candidates
    topic  = request.args.get('topic',  None)
    status = request.args.get('status', 'candidate')
    data   = load_candidates()
    result = [r for r in data
              if (not topic or r.get('topic') == topic)
              and r.get('status') == status]
    return jsonify({"ok": True, "candidates": result, "count": len(result)})


@research_bp.route('/api/research/candidates/promote', methods=['POST'])
def api_research_candidates_promote():
    """Promote a query candidate into config.json session_searches."""
    from agent.candidates import cmd_promote
    body = request.get_json() or {}
    id_  = body.get('id', '').strip()
    if not id_:
        return jsonify({"ok": False, "error": "id required"}), 400
    result = cmd_promote(id_)
    return jsonify(result), (200 if result.get('ok') else 400)


@research_bp.route('/api/research/candidates/retire', methods=['POST'])
def api_research_candidates_retire():
    """Retire a query candidate (permanent, kept for audit trail)."""
    from agent.candidates import cmd_retire
    body = request.get_json() or {}
    id_  = body.get('id', '').strip()
    if not id_:
        return jsonify({"ok": False, "error": "id required"}), 400
    result = cmd_retire(id_)
    return jsonify(result), (200 if result.get('ok') else 400)


# ── API: Save ─────────────────────────────────────────────────────────────────

@research_bp.route('/api/research/save', methods=['POST'])
def api_research_save():
    """Save an article signal with optional note. Detects duplicates."""
    from agent.feedback import record_article_signal, adjust_weights as aw
    body       = request.get_json() or {}
    url        = body.get('url', '').strip()
    title      = body.get('title', '').strip()
    session_id = body.get('session_id', '').strip()
    note       = body.get('note', None)
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if not session_id:
        return jsonify({"ok": False, "error": "session_id required"}), 400
    rec = record_article_signal("save", session_id, url=url, title=title, note=note)
    if rec is None:
        return jsonify({"ok": False, "duplicate": True, "error": "Already saved"})
    aw(session_id, "save")
    return jsonify({"ok": True, "url": url, "title": title})


# ── Dashboard helpers ─────────────────────────────────────────────────────────

# Session filename pattern: lowercase, starts with a letter, hyphens and digits allowed.
# This naturally excludes CONTEXT.md, ORIGIN.md, STORY_FOR_CLAUDE_AI.md (uppercase start)
# and sources-candidates-*.md (starts with 's' but caught by the full pattern match below).
_SESSION_FILE_RE = _re.compile(r'^[a-z][a-z0-9-]*\.md$')
_NON_SESSION_PREFIXES = ('sources-candidates-',)


def _get_topic_sessions(topic: str) -> list:
    """
    Return session IDs for a topic by reading topics/{topic}/ directory.

    Inclusion rule: .md files matching ^[a-z][a-z0-9-]*\\.md$ that do NOT
    start with a known non-session prefix (sources-candidates-).

    This is an explicit inclusion pattern — more robust than a blocklist as
    new static files won't accidentally be counted as sessions.

    Used by: dashboard saved count, candidate count, last run, top find.
    Will be called by multiple routes as the dashboard grows.

    Returns list of session ID strings (stem, no .md extension), unsorted.
    """
    topic_dir = RESEARCH_ROOT / 'topics' / topic
    if not topic_dir.exists():
        return []
    return [
        p.stem
        for p in topic_dir.iterdir()
        if p.is_file()
        and _SESSION_FILE_RE.match(p.name)
        and not any(p.name.startswith(pfx) for pfx in _NON_SESSION_PREFIXES)
    ]


def _parse_session_log() -> dict:
    """
    Parse library/session-log.md markdown table.

    Reads the agent_active flag from the file header and extracts each
    session row. Budget cumulative is taken from the last row's Cumulative
    column — the running total maintained by OpenClaw.

    Note: session-log.md is markdown, not JSON. A future cleanup task
    (tracked in BACKLOG) will migrate this to session-log.json for
    simpler parsing as the project grows.

    Returns:
        {
            "agent_active": bool,
            "budget_cumulative": float,
            "rows": [
                {"date", "session_id", "duration", "cost", "cumulative", "notes"},
                ...
            ]
        }
    """
    log_path = RESEARCH_ROOT / 'library' / 'session-log.md'
    if not log_path.exists():
        return {"agent_active": False, "budget_cumulative": 0.0, "rows": []}

    text = log_path.read_text()

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


# ── HTML: Dashboard ───────────────────────────────────────────────────────────

@research_bp.route('/research/dashboard')
def research_dashboard_ui():
    """Serve the research dashboard overview page."""
    html = RESEARCH_ROOT / 'web' / 'dashboard.html'
    if not html.exists():
        return "dashboard.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


# ── API: Dashboard ────────────────────────────────────────────────────────────

@research_bp.route('/api/research/dashboard')
def api_research_dashboard():
    """
    Return summary data for all research topics defined in config.json.

    Data sources (all existing files, no new backend):
      agent/config.json                    → topic list, budget limits
      topics/{topic}/                      → session IDs via _get_topic_sessions()
      data/feedback/article_signals.json   → saved count per topic
      data/feedback/query_candidates.json  → candidate count per topic
      library/session-log.md               → last run, top find, budget total, agent_active

    Returns:
        {
            "ok": true,
            "topics": [
                {
                    "topic": str,
                    "session_count": int,
                    "saved_count": int,
                    "candidate_count": int,
                    "last_run": "YYYY-MM-DD" | null,
                    "top_find": str | null
                },
                ...
            ],
            "budget": {
                "used": float,
                "total": float,
                "warn": float,
                "pct": float
            },
            "agent_active": bool
        }
    """
    config_path = RESEARCH_ROOT / 'agent' / 'config.json'
    if not config_path.exists():
        return jsonify({"ok": False, "error": "config.json not found"}), 500
    cfg = json.loads(config_path.read_text())

    topics       = list(cfg.get('session_searches', {}).keys())
    budget_total = cfg.get('budget', {}).get('total_limit', 20.0)
    budget_warn  = cfg.get('budget', {}).get('total_warn',  18.0)

    # Load shared data files once
    signals_path    = RESEARCH_ROOT / 'data' / 'feedback' / 'article_signals.json'
    candidates_path = RESEARCH_ROOT / 'data' / 'feedback' / 'query_candidates.json'

    article_signals = json.loads(signals_path.read_text())    if signals_path.exists() else []
    all_candidates  = json.loads(candidates_path.read_text()) if candidates_path.exists() else []

    # Parse session log once; build lookup by session_id for fast per-topic access
    log = _parse_session_log()
    log_by_session = {r['session_id']: r for r in log['rows']}

    topic_summaries = []
    for topic in topics:
        session_ids = _get_topic_sessions(topic)
        session_set = set(session_ids)

        # Saves: article signals whose session_id belongs to this topic
        saved_count = sum(1 for s in article_signals
                          if s.get('session_id') in session_set)

        # Candidates: query candidates for this topic still in 'candidate' status
        candidate_count = sum(1 for c in all_candidates
                               if c.get('topic') == topic
                               and c.get('status') == 'candidate')

        # Last run + top find: most recent session-log row for this topic's sessions
        topic_rows = sorted(
            [log_by_session[sid] for sid in session_ids if sid in log_by_session],
            key=lambda r: r['date'],
            reverse=True,
        )

        last_run = None
        top_find = None
        if topic_rows:
            latest   = topic_rows[0]
            last_run = latest['date']
            tf_match = _re.search(r'top find:\s*(.+)', latest.get('notes', ''))
            top_find = tf_match.group(1).strip() if tf_match else None

        topic_summaries.append({
            "topic":           topic,
            "session_count":   len(session_ids),
            "saved_count":     saved_count,
            "candidate_count": candidate_count,
            "last_run":        last_run,
            "top_find":        top_find,
        })

    used = log['budget_cumulative']
    return jsonify({
        "ok":           True,
        "topics":       topic_summaries,
        "budget": {
            "used":  used,
            "total": budget_total,
            "warn":  budget_warn,
            "pct":   round(used / budget_total * 100, 1) if budget_total else 0.0,
        },
        "agent_active": log['agent_active'],
    })


# ── API: Threads ──────────────────────────────────────────────────────────────

@research_bp.route('/api/research/thread/<topic>')
def api_research_thread_get(topic: str):
    """
    Return thread.json + recent annotations for a topic.
    GET /api/research/thread/gold-geopolitics
    """
    from agent.threads import load_thread, load_annotations
    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404
    annotations = load_annotations(topic)
    return jsonify({
        "ok":          True,
        "thread":      thread.model_dump(),
        "annotations": [a.model_dump() for a in annotations[-20:]],
    })


@research_bp.route('/api/research/thread/<topic>/annotate', methods=['POST'])
def api_research_thread_annotate(topic: str):
    """
    Add an annotation to a thread.
    POST /api/research/thread/gold-geopolitics/annotate
    Body: {"type": "direction_shift", "note": "...", "ref_session": null, "ref_article": null}
    """
    from agent.threads import load_thread, cmd_annotate
    body        = request.get_json() or {}
    ann_type    = body.get("type", "").strip()
    note        = body.get("note", "").strip()
    ref_session = body.get("ref_session") or None
    ref_article = body.get("ref_article") or None

    if not ann_type:
        return jsonify({"ok": False, "error": "type required"}), 400
    if ann_type not in {"direction_shift", "reaction", "observation", "wrap_up"}:
        return jsonify({"ok": False, "error": f"invalid type: {ann_type}"}), 400
    if not note:
        return jsonify({"ok": False, "error": "note required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    cmd_annotate(topic, ann_type, note,
                 ref_session=ref_session, ref_article=ref_article)
    return jsonify({"ok": True, "topic": topic, "type": ann_type})


@research_bp.route('/api/research/thread/create', methods=['POST'])
def api_research_thread_create():
    """
    Create a new thread for a topic.
    POST /api/research/thread/create
    Body: {"topic": "gold-geopolitics", "motivation": "...", "prior_belief": "..."}
    """
    from agent.threads import cmd_create
    body         = request.get_json() or {}
    topic        = body.get("topic", "").strip()
    motivation   = body.get("motivation", "To be written").strip()
    prior_belief = body.get("prior_belief", "To be written").strip()

    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    cmd_create(topic, motivation, prior_belief)
    return jsonify({"ok": True, "topic": topic})


@research_bp.route('/api/research/thread/<topic>/retire', methods=['POST'])
def api_research_thread_retire(topic: str):
    """
    Retire a thread — hidden from UI, preserved on disk.
    POST /api/research/thread/gold-geopolitics/retire
    Body: {"reason": "..."}
    """
    from agent.threads import load_thread, cmd_retire
    body   = request.get_json() or {}
    reason = body.get("reason", "").strip()

    if not reason:
        return jsonify({"ok": False, "error": "reason required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    cmd_retire(topic, reason)
    return jsonify({"ok": True, "topic": topic, "status": "retired"})


@research_bp.route('/api/research/thread/<topic>/wrap-up', methods=['POST'])
def api_research_thread_wrap_up(topic: str):
    """
    Close a thread with a conclusion note.
    POST /api/research/thread/gold-geopolitics/wrap-up
    Body: {"note": "..."}
    """
    from agent.threads import load_thread, cmd_wrap_up
    body   = request.get_json() or {}
    note   = body.get("note", "").strip()

    if not note:
        return jsonify({"ok": False, "error": "note required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    cmd_wrap_up(topic, note)
    return jsonify({"ok": True, "topic": topic, "status": "closed"})


# ── HTML: Sessions ────────────────────────────────────────────────────────────

@research_bp.route('/research/sessions')
def research_sessions_ui():
    """Serve the session viewer page."""
    html = RESEARCH_ROOT / 'web' / 'sessions.html'
    if not html.exists():
        return "sessions.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


# ── API: Sessions ─────────────────────────────────────────────────────────────

def _parse_session_header(text: str) -> dict:
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


def _parse_session_sections(text: str) -> dict:
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


@research_bp.route('/api/research/sessions')
def api_research_sessions_list():
    """
    List sessions for a topic, most recent first.
    GET /api/research/sessions?topic=gold-geopolitics

    Returns: {ok, topic, sessions: [{id, date, cost, duration, sources_reviewed, file}]}
    """
    topic = request.args.get('topic', '').strip()
    if not topic:
        # Return all topics with session counts if no topic given
        topics_dir = RESEARCH_ROOT / 'topics'
        if not topics_dir.exists():
            return jsonify({"ok": True, "topics": []})
        result = []
        for td in sorted(topics_dir.iterdir()):
            if td.is_dir():
                sessions = sorted(
                    [f for f in td.glob('*.md') if not f.name.startswith('sources-') and f.name != 'CONTEXT.md' and not f.name.isupper()],
                    reverse=True
                )
                result.append({"topic": td.name, "count": len(sessions)})
        return jsonify({"ok": True, "topics": result})

    topic_dir = RESEARCH_ROOT / 'topics' / topic
    if not topic_dir.exists():
        return jsonify({"ok": False, "error": f"topic '{topic}' not found"}), 404

    session_files = sorted(
        [f for f in topic_dir.glob('*.md')
         if not f.name.startswith('sources-')
         and f.name not in ('CONTEXT.md', 'ORIGIN.md', 'STORY_FOR_CLAUDE_AI.md')
         and not f.stem.isupper()],
        reverse=True
    )

    sessions = []
    for f in session_files:
        text = f.read_text()
        hdr  = _parse_session_header(text)
        sessions.append({
            'id':               hdr.get('session', f.stem),
            'date':             hdr.get('date', ''),
            'cost':             hdr.get('cost', ''),
            'duration':         hdr.get('duration', ''),
            'sources_reviewed': hdr.get('sources_reviewed', ''),
            'file':             f.name,
        })

    return jsonify({"ok": True, "topic": topic, "sessions": sessions})


@research_bp.route('/api/research/sessions/<topic>/<session_id>')
def api_research_session_detail(topic: str, session_id: str):
    """
    Return full parsed content of a single session.
    GET /api/research/sessions/gold-geopolitics/gold-005

    Returns: {ok, topic, session_id, header, findings, sources, threads, agent_notes}
    """
    topic_dir = RESEARCH_ROOT / 'topics' / topic
    if not topic_dir.exists():
        return jsonify({"ok": False, "error": f"topic '{topic}' not found"}), 404

    # Accept session_id with or without .md
    stem = session_id.replace('.md', '')
    f = topic_dir / f'{stem}.md'
    if not f.exists():
        return jsonify({"ok": False, "error": f"session '{session_id}' not found"}), 404

    text     = f.read_text()
    header   = _parse_session_header(text)
    sections = _parse_session_sections(text)

    return jsonify({
        "ok":         True,
        "topic":      topic,
        "session_id": stem,
        "header":     header,
        **sections,
    })


@research_bp.route('/api/research/sessions/<topic>/<session_id>/annotate', methods=['POST'])
def api_research_session_annotate(topic: str, session_id: str):
    """
    Add a reaction/note to a session or individual finding.
    POST /api/research/sessions/gold-geopolitics/gold-005/annotate
    Body: {"type": "reaction"|"direction_shift"|"observation",
           "note": "...",
           "ref_article": "optional source title or url"}
    Stored as a thread annotation with ref_session set automatically.
    """
    from agent.threads import load_thread, cmd_annotate
    body        = request.get_json() or {}
    ann_type    = body.get("type", "reaction").strip()
    note        = body.get("note", "").strip()
    ref_article = body.get("ref_article") or None

    if not note:
        return jsonify({"ok": False, "error": "note required"}), 400
    if ann_type not in {"direction_shift", "reaction", "observation", "wrap_up"}:
        return jsonify({"ok": False, "error": f"invalid type: {ann_type}"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread for '{topic}' — create one first"}), 404

    stem = session_id.replace('.md', '')
    cmd_annotate(topic, ann_type, note, ref_session=stem, ref_article=ref_article)
    return jsonify({"ok": True, "topic": topic, "session_id": stem, "type": ann_type})
