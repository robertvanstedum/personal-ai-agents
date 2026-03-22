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
