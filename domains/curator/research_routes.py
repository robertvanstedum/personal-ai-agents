"""
research_routes.py — Flask Blueprint for Research Intelligence UI

All /research/* and /api/research/* routes live here.
Registered in curator_server.py with a try/except guard so any import
failure here cannot crash the main curator server.

To add new research routes: edit only this file. Never touch curator_server.py
for research features.
"""

import html as _html_lib
import json
import os
import re as _re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, redirect, render_template_string

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # repo root, for core.get_secret import below
from core.get_secret import get_secret
from domains.curator.research.document_text import (
    parse_session_header as _parse_session_header,
    parse_session_sections as _parse_session_sections,
    escape_inline as _dd_escape,
    render_analysis_html as _dd_md_to_html,
)

from domains.curator.research.scan_render import render_scan_page as _render_scan_page
from domains.curator.research.scan_documents import (
    parse_bibliography as _parse_scan_bibliography,
    parse_scan as _parse_scan_md,
    build_session as _build_session_from_scan,
    research_directions as _scan_research_directions,
)

# ── Blueprint ─────────────────────────────────────────────────────────────────

research_bp = Blueprint('research', __name__)

# Application code and private research state have separate permanent roots.
_REPO_ROOT     = Path(__file__).resolve().parent.parent.parent
from domains.curator.research.paths import configured
PATHS = configured()
RESEARCH_ROOT = PATHS.code
DATA_ROOT = PATHS.data
STATE_ROOT = PATHS.state
SCANS_DIR      = _REPO_ROOT / 'interests' / '2026' / 'scans'
DIVES_DIR      = DATA_ROOT / 'dives'


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
            cwd=str(_REPO_ROOT),
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
    obs_dir = DATA_ROOT / 'observations'

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
    obs_dir = DATA_ROOT / 'observations'
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
    from domains.curator.research.agent.candidates import load as load_candidates
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
    from domains.curator.research.agent.candidates import cmd_promote
    body = request.get_json() or {}
    id_  = body.get('id', '').strip()
    if not id_:
        return jsonify({"ok": False, "error": "id required"}), 400
    result = cmd_promote(id_)
    return jsonify(result), (200 if result.get('ok') else 400)


@research_bp.route('/api/research/candidates/retire', methods=['POST'])
def api_research_candidates_retire():
    """Retire a query candidate (permanent, kept for audit trail)."""
    from domains.curator.research.agent.candidates import cmd_retire
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
    from domains.curator.research.agent.feedback import record_article_signal, adjust_weights as aw
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
    topic_dirs = [
        STATE_ROOT / 'topics' / topic,
        DATA_ROOT / 'threads' / topic / 'sessions',
    ]
    sessions = set()
    for topic_dir in topic_dirs:
        if not topic_dir.exists():
            continue
        sessions.update(
            p.stem
            for p in topic_dir.iterdir()
            if p.is_file()
            and _SESSION_FILE_RE.match(p.name)
            and not any(p.name.startswith(pfx) for pfx in _NON_SESSION_PREFIXES)
        )
    return sorted(sessions)


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
    log_path = STATE_ROOT / 'library' / 'session-log.md'
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
    config_path = PATHS.config
    if not config_path.exists():
        return jsonify({"ok": False, "error": "config.json not found"}), 500
    cfg = json.loads(config_path.read_text())

    topics       = list(cfg.get('session_searches', {}).keys())
    threads_dir  = DATA_ROOT / 'threads'
    if threads_dir.exists():
        topics.extend(
            path.name
            for path in threads_dir.iterdir()
            if path.is_dir()
            and (path / 'thread.json').exists()
            and path.name not in topics
        )
    budget_total = cfg.get('budget', {}).get('total_limit', 20.0)
    budget_warn  = cfg.get('budget', {}).get('total_warn',  18.0)

    # Load shared data files once
    signals_path    = DATA_ROOT / 'feedback' / 'article_signals.json'
    candidates_path = DATA_ROOT / 'feedback' / 'query_candidates.json'

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

        # Thread lifecycle fields
        from domains.curator.research.agent.threads import load_thread as _lt
        _thread = _lt(topic)
        thread_status       = _thread.status      if _thread else "active"
        thread_expires      = _thread.expires     if _thread else None
        thread_dd_generated = _thread.deeper_dive_generated if _thread else False
        thread_dd_path      = _thread.deeper_dive_path      if _thread else None

        # Compute session count at time of last Dive (for repeatable dive gate)
        sessions_at_last_dive = 0
        if thread_dd_generated and thread_dd_path:
            _dd_full = PATHS.persisted(thread_dd_path)
            if _dd_full.exists():
                _m = _re.search(r'(\d+)\s+sessions?', _dd_full.read_text()[:300])
                if _m:
                    sessions_at_last_dive = int(_m.group(1))

        topic_summaries.append({
            "topic":                  topic,
            "session_count":          len(session_ids),
            "saved_count":            saved_count,
            "candidate_count":        candidate_count,
            "last_run":               last_run,
            "top_find":               top_find,
            "status":                 thread_status,
            "expires":                thread_expires,
            "deeper_dive_generated":  thread_dd_generated,
            "deeper_dive_path":       thread_dd_path,
            "sessions_at_last_dive":  sessions_at_last_dive,
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
        "agent_active": _run_state_active(),
    })


# ── API: Run Session ──────────────────────────────────────────────────────────

_RUN_STATE_PATH = DATA_ROOT / 'run_state.json'
_RUN_STATE_MAX_AGE_MINUTES = 60

# Module-level reference to the running Popen object (single session only).
# Stored here so status checks can call proc.poll() which correctly reaps zombies.
# NOTE: This reference is lost on server restart — the stale-age check in
# _run_state_active() provides the safety net in that case.
_active_proc: subprocess.Popen | None = None

# ── Deeper Dive generation state (same pattern as run-session) ────────────────

_DD_STATE_PATH = DATA_ROOT / 'dd_run_state.json'
_DD_STATE_MAX_AGE_MINUTES = 120   # Opus calls can take 2–5 min; generous ceiling
_dd_proc: subprocess.Popen | None = None

# Article-level Scan generation follows the same non-blocking pattern. The
# request returns after the job is durable; the browser polls the status route.
_SCAN_STATE_PATH = DATA_ROOT / 'scan_run_state.json'
_SCAN_STATE_MAX_AGE_MINUTES = 15
_scan_proc: subprocess.Popen | None = None


def _is_proc_alive() -> bool:
    """Check liveness using proc.poll() if we have the object, else fall back to os.kill."""
    global _active_proc
    if _active_proc is not None:
        return _active_proc.poll() is None  # None = still running; int = exited
    # Fallback for restarts: os.kill with signal 0.
    # PID recycling is an accepted risk for a personal tool.
    if not _RUN_STATE_PATH.exists():
        return False
    try:
        state = json.loads(_RUN_STATE_PATH.read_text())
        os.kill(state['pid'], 0)
        return True
    except (ProcessLookupError, KeyError, json.JSONDecodeError, OSError):
        return False


def _run_state_active() -> bool:
    """Return True if a research session is currently running (not stale)."""
    if not _RUN_STATE_PATH.exists():
        return False
    try:
        state = json.loads(_RUN_STATE_PATH.read_text())
        started = datetime.fromisoformat(state['started_at'])
        if datetime.now(timezone.utc) - started > timedelta(minutes=_RUN_STATE_MAX_AGE_MINUTES):
            _RUN_STATE_PATH.unlink(missing_ok=True)
            return False
        return _is_proc_alive()
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return False


def _next_session_name(topic: str) -> str:
    """Auto-generate the next session name as {topic}-NNN."""
    import re as _re2
    max_n = 0
    topic_dirs = [
        STATE_ROOT / 'topics' / topic,
        DATA_ROOT / 'threads' / topic / 'sessions',
    ]
    for topic_dir in topic_dirs:
        if not topic_dir.exists():
            continue
        pat = _re2.compile(rf'^{_re2.escape(topic)}-(\d+)\.md$')
        for f in topic_dir.glob('*.md'):
            m = pat.match(f.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"{topic}-{max_n + 1:03d}"


@research_bp.route('/api/research/run-session', methods=['POST'])
def api_research_run_session():
    """
    Spawn a research session for a topic as a non-blocking subprocess.
    POST /api/research/run-session
    Body: {"topic": "strait-of-hormuz"}
    Returns immediately — session runs in background.
    Poll GET /api/research/run-session/status for completion.
    """
    body  = request.get_json() or {}
    topic = body.get('topic', '').strip()

    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    # Validate topic exists in config
    config_path = PATHS.config
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        return jsonify({"ok": False, "error": f"could not read config: {e}"}), 500

    dynamic_searches = []
    try:
        from domains.curator.research.agent.threads import load_thread
        thread = load_thread(topic)
        dynamic_searches = thread.session_searches if thread else []
    except Exception:
        pass

    if topic not in config.get('session_searches', {}) and not dynamic_searches:
        return jsonify({"ok": False, "error": f"topic '{topic}' not found in config"}), 400

    # Reject if a session is already running
    if _run_state_active():
        try:
            running_topic = json.loads(_RUN_STATE_PATH.read_text()).get('topic', 'unknown')
        except Exception:
            running_topic = 'unknown'
        return jsonify({
            "ok": False,
            "error": f"session already running for '{running_topic}'",
            "running_topic": running_topic,
        }), 409

    session_name = _next_session_name(topic)

    # Launch non-blocking subprocess — research.py can run 1-35 min,
    # far too long to block a Flask worker.
    global _active_proc
    log_dir = DATA_ROOT / 'session_logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f'{session_name}.log'
    try:
        log_file = open(log_path, 'wb')
        proc = subprocess.Popen(
            [sys.executable, str(RESEARCH_ROOT / 'agent' / 'research.py'),
             '--topic', topic,
             '--session-name', session_name],
            cwd=str(_REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        # Log to stderr for debugging via curator_server_stderr.log
        print(f"[run-session] OSError launching research.py: {e}", file=sys.stderr)
        return jsonify({"ok": False, "error": f"failed to launch agent: {e}"}), 500

    _active_proc = proc

    # Write state file — single source of truth for running state.
    # NOTE: PID recycling is an accepted risk for a personal tool. The 60-minute
    # stale timeout in _run_state_active() provides a safety net against orphaned state.
    state = {
        "topic":        topic,
        "session_name": session_name,
        "pid":          proc.pid,
        "started_at":   datetime.now(timezone.utc).isoformat(),
    }
    _RUN_STATE_PATH.write_text(json.dumps(state, indent=2))

    return jsonify({"ok": True, "topic": topic, "session_name": session_name, "pid": proc.pid})


@research_bp.route('/api/research/run-session/status')
def api_research_run_session_status():
    """
    Check if a research session subprocess is currently running.
    GET /api/research/run-session/status
    Returns: {running: bool, topic, session_name, started_at} or {running: false}
    """
    if not _RUN_STATE_PATH.exists():
        return jsonify({"running": False})

    try:
        state = json.loads(_RUN_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        _RUN_STATE_PATH.unlink(missing_ok=True)
        return jsonify({"running": False})

    # Stale check — server restart could leave a stale PID
    try:
        started = datetime.fromisoformat(state['started_at'])
        if datetime.now(timezone.utc) - started > timedelta(minutes=_RUN_STATE_MAX_AGE_MINUTES):
            _RUN_STATE_PATH.unlink(missing_ok=True)
            return jsonify({"running": False})
    except (KeyError, ValueError):
        _RUN_STATE_PATH.unlink(missing_ok=True)
        return jsonify({"running": False})

    # Liveness check via proc.poll() (preferred) or os.kill fallback
    global _active_proc
    running = _is_proc_alive()
    failed = False
    error_excerpt = None
    if not running:
        # proc.poll() returns the exit code once finished (None while running).
        # A nonzero code means it crashed rather than completing -- surface
        # that explicitly instead of silently reporting "not running", which
        # looks identical to a normal completion from the UI's perspective.
        if _active_proc is not None and _active_proc.poll() not in (None, 0):
            failed = True
            log_path = DATA_ROOT / 'session_logs' / f"{state.get('session_name')}.log"
            if log_path.exists():
                try:
                    error_excerpt = log_path.read_text(errors='replace').strip()[-500:]
                except OSError:
                    pass
        _active_proc = None
        _RUN_STATE_PATH.unlink(missing_ok=True)

    resp = {
        "running":      running,
        "topic":        state.get('topic'),
        "session_name": state.get('session_name'),
        "started_at":   state.get('started_at'),
    }
    if failed:
        resp["failed"] = True
        resp["error"] = error_excerpt or "Session process exited with an error; no output captured."
    return jsonify(resp)


# ── API: Deeper Dive generation ───────────────────────────────────────────────

def _dd_state_active() -> bool:
    """Return True while the current Deeper Dive subprocess is alive."""
    global _dd_proc
    if not _DD_STATE_PATH.exists():
        return False
    try:
        state = json.loads(_DD_STATE_PATH.read_text())
        started = datetime.fromisoformat(state.get('started_at', ''))
        age = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if age > _DD_STATE_MAX_AGE_MINUTES:
            _DD_STATE_PATH.unlink(missing_ok=True)
            return False
        if _dd_proc is not None:
            return _dd_proc.poll() is None
        os.kill(int(state['pid']), 0)
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _dive_error_excerpt(state: dict) -> str:
    """Return a safe, bounded excerpt from a failed Dive job log."""
    try:log_path = PATHS.persisted(state.get('log_path', ''))
    except ValueError:log_path = None
    if log_path is not None and log_path.exists():
        try:
            text = log_path.read_text(errors='replace').strip()
            if text:
                return text[-500:]
        except OSError:
            pass
    return "Deeper Dive generation failed. Please try again."


def _scan_state_active() -> bool:
    """Return True while the current article-level Scan process is alive."""
    global _scan_proc
    if not _SCAN_STATE_PATH.exists():
        return False
    try:
        state = json.loads(_SCAN_STATE_PATH.read_text())
        started = datetime.fromisoformat(state.get('started_at', ''))
        if datetime.now(timezone.utc) - started > timedelta(minutes=_SCAN_STATE_MAX_AGE_MINUTES):
            return False
        if _scan_proc is not None:
            return _scan_proc.poll() is None
        os.kill(int(state['pid']), 0)
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _scan_error_excerpt(state: dict) -> str:
    try:log_path = PATHS.persisted(state.get('log_path', ''))
    except ValueError:log_path = None
    if log_path is not None and log_path.exists():
        try:
            text = log_path.read_text(errors='replace').strip()
            if text:
                return text[-500:]
        except OSError:
            pass
    return "Scan generation failed. Please try again."


@research_bp.route('/api/research/generate-scan', methods=['POST'])
def api_research_generate_scan():
    """Start a non-blocking article-level Scan from submitted article data."""
    global _scan_proc

    body = request.get_json() or {}
    hash_id = str(body.get('hash_id', '')).strip().lower()
    interest = str(body.get('interest', '')).strip()
    focus = str(body.get('focus', '')).strip()
    raw_article = body.get('article') or {}

    if not _re.match(r'^[a-f0-9]{5}$', hash_id):
        return jsonify({"ok": False, "error": "invalid article ID"}), 400
    if not interest:
        return jsonify({"ok": False, "error": "interest is required"}), 400

    article = {
        "hash_id": hash_id,
        "title": str(raw_article.get('title', '')).strip(),
        "url": str(raw_article.get('url') or raw_article.get('link') or '').strip(),
        "source": str(raw_article.get('source', '')).strip(),
        "category": str(raw_article.get('category', 'other')).strip() or 'other',
        "published": raw_article.get('published', ''),
        "summary": raw_article.get('summary', ''),
    }
    if not article["title"] or not article["url"]:
        return jsonify({"ok": False, "error": "article title and URL are required"}), 400

    existing = _find_dd_md(hash_id)
    if existing:
        return jsonify({
            "ok": True,
            "running": False,
            "view_url": f"/research/scan/{hash_id}",
        })

    if _scan_state_active():
        state = json.loads(_SCAN_STATE_PATH.read_text())
        return jsonify({
            "ok": False,
            "error": f"A Scan is already running for '{state.get('title', 'another article')}'.",
        }), 409

    jobs_dir = DATA_ROOT / 'scan_jobs'
    jobs_dir.mkdir(parents=True, exist_ok=True)
    payload_path = jobs_dir / f'{hash_id}.json'
    log_path = jobs_dir / f'{hash_id}.log'
    payload_path.write_text(json.dumps({
        "hash_id": hash_id,
        "article": article,
        "interest": interest,
        "focus": focus,
    }, indent=2))

    script = _REPO_ROOT / 'domains' / 'curator' / 'scan_job.py'
    try:
        with open(log_path, 'wb') as log_file:
            _scan_proc = subprocess.Popen(
                [sys.executable, str(script), '--payload', str(payload_path)],
                cwd=str(_REPO_ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify({"ok": False, "error": f"failed to start Scan: {exc}"}), 500

    state = {
        "hash_id": hash_id,
        "title": article["title"],
        "pid": _scan_proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "payload_path": str(payload_path),
        "log_path": str(log_path),
    }
    _SCAN_STATE_PATH.write_text(json.dumps(state, indent=2))
    return jsonify({"ok": True, "running": True, "hash_id": hash_id})


@research_bp.route('/api/research/generate-scan/status')
def api_research_generate_scan_status():
    """Poll the current article-level Scan job."""
    global _scan_proc

    hash_id = request.args.get('hash_id', '').strip().lower()
    if not _re.match(r'^[a-f0-9]{5}$', hash_id):
        return jsonify({"ok": False, "error": "invalid article ID"}), 400

    if _find_dd_md(hash_id):
        _scan_proc = None
        _SCAN_STATE_PATH.unlink(missing_ok=True)
        return jsonify({
            "ok": True,
            "running": False,
            "view_url": f"/research/scan/{hash_id}",
        })

    if not _SCAN_STATE_PATH.exists():
        return jsonify({"ok": False, "running": False, "error": "Scan job not found"}), 404

    try:
        state = json.loads(_SCAN_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return jsonify({"ok": False, "running": False, "error": "Scan status is unavailable"}), 500

    if state.get('hash_id') != hash_id:
        return jsonify({"ok": False, "running": False, "error": "A different Scan job is active"}), 409

    if _scan_state_active():
        return jsonify({"ok": True, "running": True, "started_at": state.get('started_at')})

    failed = _scan_proc is None or _scan_proc.poll() not in (None, 0)
    _scan_proc = None
    return jsonify({
        "ok": False,
        "running": False,
        "failed": failed,
        "error": _scan_error_excerpt(state),
    }), 500


def _next_dive_output_path(topic: str) -> Path:
    """Return next sequential output path for a Dive: {topic}-dive-NNN.md"""
    import re as _re2
    dd_dir = DIVES_DIR
    dd_dir.mkdir(parents=True, exist_ok=True)
    # Support both legacy deeper-dive-NNN and new dive-NNN naming
    existing = list(dd_dir.glob(f'{topic}-*.md'))
    nums = []
    for f in existing:
        m = _re2.search(r'-(?:deeper-dive|dive)-(\d+)\.md$', f.name)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return dd_dir / f'{topic}-dive-{n:03d}.md'


def _start_dive_job(topic: str, out_path: Path) -> tuple[subprocess.Popen, Path]:
    """Start a Dive and persist its output so failures remain diagnosable."""
    script = RESEARCH_ROOT / 'scripts' / 'generate_dive.py'
    log_dir = DATA_ROOT / 'dive_jobs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f'{out_path.stem}.log'
    log_file = open(log_path, 'wb')
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script),
             '--topic', topic,
             '--output-path', str(out_path)],
            cwd=str(_REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log_file.close()
        raise
    return proc, log_path


def _build_challenger_html(ch: dict) -> str:
    """Build the challenger process section HTML for a dive page.
    Returns empty string when show_process is false or challenger is disabled.
    """
    if not ch.get('show_process') or not ch.get('enabled'):
        return ''

    points          = ch.get('challenge_points', [])
    accepted_count  = ch.get('accepted_count', 0)
    rejected_count  = ch.get('rejected_count', 0)
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


@research_bp.route('/research/dive-result/<stem>')
def research_dive_result(stem: str):
    """
    Serve a research Dive output file by filename stem.
    GET /research/dive-result/strait-of-hormuz-deeper-dive-005

    Renders the markdown file from data/dives/ as a styled read-only page.
    """
    # Sanitise: only slug-safe characters
    if not _re.match(r'^[a-zA-Z0-9_-]+$', stem):
        return "invalid filename", 400

    md_path = DIVES_DIR / f'{stem}.md'
    if not md_path.exists():
        return f"Dive '{stem}' not found", 404

    raw   = md_path.read_text()
    title = stem.replace('-', ' ').title()
    # Extract first H1 for page title
    h1_m  = _re.search(r'^#\s+(.+)$', raw, _re.MULTILINE)
    if h1_m:
        title = h1_m.group(1).strip()

    # Load challenger sidecar if present
    ch_section_html = ''
    ch_meta_path = DIVES_DIR / f'{stem}-challenger.json'
    if ch_meta_path.exists():
        try:
            ch_section_html = _build_challenger_html(json.loads(ch_meta_path.read_text()))
        except Exception:
            pass

    # Lightweight markdown → HTML
    def _md_to_html(text: str) -> str:
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

    body_html = _md_to_html(raw)
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
</html>""", 200, {'Content-Type': 'text/html'}


@research_bp.route('/research/dive-confirm')
def research_dive_confirm():
    """
    Confirmation page before generating a Deeper Dive.
    GET /research/dive-confirm?topic=strait-of-hormuz
    """
    topic = request.args.get('topic', '').strip()
    if not topic:
        return "topic parameter required", 400

    html = RESEARCH_ROOT / 'web' / 'dive_confirm.html'
    if not html.exists():
        return "dive_confirm.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


@research_bp.route('/api/research/dive-confirm-data')
def api_research_dive_confirm_data():
    """
    Return thread metadata for the Deeper Dive confirmation page.
    GET /api/research/dive-confirm-data?topic=strait-of-hormuz
    """
    from domains.curator.research.agent.threads import load_thread
    topic = request.args.get('topic', '').strip()
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    # Count sessions
    STATIC_FILES = {"CONTEXT.md", "ORIGIN.md", "STORY_FOR_CLAUDE_AI.md", "README.md"}
    SESSION_RE = _re.compile(r'^[a-z][a-z0-9-]*\.md$')
    topic_dir = STATE_ROOT / 'topics' / topic
    sessions = []
    if topic_dir.exists():
        sessions = [
            p for p in topic_dir.iterdir()
            if p.is_file()
            and SESSION_RE.match(p.name)
            and p.name not in STATIC_FILES
            and not p.name.startswith("sources-candidates-")
        ]

    # Count unique sources across sessions
    source_count = 0
    seen_urls: set = set()
    for sf in sessions:
        text = sf.read_text()
        # Simple URL extraction from bibliography-style lines
        for url in _re.findall(r'https?://\S+', text):
            url = url.rstrip(')')
            if url not in seen_urls:
                seen_urls.add(url)
                source_count += 1

    return jsonify({
        "ok":            True,
        "topic":         topic,
        "motivation":    thread.motivation,
        "session_count": len(sessions),
        "source_count":  source_count,
        "expires":       thread.expires,
        "opened":        thread.opened[:10] if thread.opened else None,
    })


@research_bp.route('/api/research/generate-dive', methods=['POST'])
def api_research_generate_dive():
    """
    Spawn a Dive generation for a topic as a non-blocking subprocess.
    POST /api/research/generate-dive
    Body: {"topic": "strait-of-hormuz"}
    Returns immediately — generation runs in background.
    Poll GET /api/research/generate-dive/status for completion.
    """
    global _dd_proc

    body  = request.get_json() or {}
    topic = body.get('topic', '').strip()
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    if _dd_state_active():
        try:
            running_topic = json.loads(_DD_STATE_PATH.read_text()).get('topic', 'unknown')
        except Exception:
            running_topic = 'unknown'
        return jsonify({
            "ok": False,
            "error": f"A Deeper Dive is already running for '{running_topic}'. Wait for it to finish."
        }), 409

    out_path = _next_dive_output_path(topic)

    try:
        proc, log_path = _start_dive_job(topic, out_path)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[generate-dive] OSError: {e}", file=sys.stderr)
        return jsonify({"ok": False, "error": f"failed to launch: {e}"}), 500

    _dd_proc = proc

    state = {
        "topic":       topic,
        "output_path": str(out_path),
        "log_path":    str(log_path),
        "pid":         proc.pid,
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "status":      "running",
    }
    _DD_STATE_PATH.write_text(json.dumps(state, indent=2))

    return jsonify({"ok": True, "topic": topic, "pid": proc.pid})


@research_bp.route('/api/research/generate-dive/status')
def api_research_generate_dive_status():
    """
    Check if a Dive generation is currently running.
    GET /api/research/generate-dive/status
    Returns: {running: bool, topic, output_path} or {running: false}
    """
    global _dd_proc

    if not _DD_STATE_PATH.exists():
        return jsonify({"running": False})

    try:
        state = json.loads(_DD_STATE_PATH.read_text())
    except Exception:
        return jsonify({"running": False})

    # Liveness check. If the Flask process restarted, use the persisted PID.
    running = False
    return_code = None
    if _dd_proc is not None:
        return_code = _dd_proc.poll()
        running = return_code is None
    else:
        try:
            os.kill(int(state['pid']), 0)
            running = True
        except (OSError, ValueError, KeyError):
            running = False

    if not running:
        _dd_proc = None
        topic      = state.get('topic', '')
        out_path   = state.get('output_path', '')
        try:resolved_output = PATHS.persisted(out_path)
        except ValueError:resolved_output = None
        output_exists = bool(resolved_output is not None and resolved_output.exists())
        if topic and output_exists:
            try:
                from domains.curator.research.agent.threads import load_thread, save_thread
                thread = load_thread(topic)
                if thread:
                    thread.deeper_dive_generated = True
                    thread.deeper_dive_path      = PATHS.saved_label(resolved_output)
                    save_thread(topic, thread)
            except Exception as e:
                print(f"[dd-status] failed to update thread.json: {e}", file=sys.stderr)

        _DD_STATE_PATH.unlink(missing_ok=True)
        if not output_exists:
            return jsonify({
                "running":     False,
                "failed":      True,
                "topic":       topic,
                "output_path": out_path,
                "return_code": return_code,
                "error":       _dive_error_excerpt(state),
            }), 500

        return jsonify({
            "running":     False,
            "failed":      False,
            "topic":       topic,
            "output_path": out_path,
            "view_url":    f"/research/dive-result/{Path(out_path).stem}",
        })

    return jsonify({
        "running":    True,
        "topic":      state.get('topic'),
        "started_at": state.get('started_at'),
    })


@research_bp.route('/api/research/scan-to-dive', methods=['POST'])
def api_research_scan_to_dive():
    """
    Create a thread from a scan and immediately generate a Deeper Dive.
    POST /api/research/scan-to-dive
    Body: {"hash_id": "03624"}

    Creates a 1-session thread from the scan's content, then fires generate_dive.py
    exactly as /api/research/generate-dive does. Returns immediately — poll
    /api/research/generate-dive/status for completion.
    """
    global _dd_proc
    from domains.curator.research.agent.threads import cmd_create, load_thread, save_thread

    body    = request.get_json() or {}
    hash_id = body.get('hash_id', '').strip()

    if not _re.match(r'^[a-f0-9]{5}$', hash_id):
        return jsonify({"ok": False, "error": "invalid hash_id"}), 400

    md_path = _find_dd_md(hash_id)
    if not md_path:
        return jsonify({"ok": False, "error": f"scan '{hash_id}' not found"}), 404

    if _dd_state_active():
        try:
            running_topic = json.loads(_DD_STATE_PATH.read_text()).get('topic', 'unknown')
        except Exception:
            running_topic = 'unknown'
        return jsonify({
            "ok": False,
            "error": f"A Deeper Dive is already running for '{running_topic}'. Wait for it to finish."
        }), 409

    dd = _parse_scan_md(md_path)

    # Auto-generate slug from title (same stop-word logic as scan page)
    _STOP = {'a','an','the','in','on','at','of','to','and','or','for','by',
             'with','from','is','are','was','were','be','been','as','it','its',
             'this','that','how','why','what','when','where','who'}
    _words = _re.sub(r'[^a-z0-9\s]', ' ', dd['title'].lower()).split()
    _keep  = [w for w in _words if w not in _STOP and len(w) > 1][:4]
    base_slug = '-'.join(_keep) if _keep else _re.sub(r'[^a-z0-9]+', '-', dd['title'].lower()).strip('-')[:40]

    # Ensure unique slug
    config_path = PATHS.config
    try:
        config = json.loads(config_path.read_text())
    except Exception:
        config = {}
    thread_root = DATA_ROOT / 'threads'
    existing_topics = set(config.get('session_searches', {}))
    if thread_root.exists():
        existing_topics.update(
            path.name for path in thread_root.iterdir() if path.is_dir()
        )

    topic  = base_slug
    suffix = 1
    while topic in existing_topics:
        topic = f"{base_slug}-{suffix}"
        suffix += 1

    motivation = dd['metadata'].get('interest', '') or dd['title']

    # Create thread record
    cmd_create(topic, motivation or "Explore this article", "")

    # Seed follow-on research in the durable thread record so container
    # replacement cannot erase the generated topic configuration.
    searches, targets = _scan_research_directions(dd)
    thread = load_thread(topic)
    if thread:
        thread.session_searches = searches
        thread.triage_targets = targets
        save_thread(topic, thread)

    # Web-created sessions live in mounted data, not the container filesystem.
    topics_dir = DATA_ROOT / 'threads' / topic / 'sessions'
    topics_dir.mkdir(parents=True, exist_ok=True)
    session_md   = _build_session_from_scan(dd, hash_id, topic)
    session_path = topics_dir / f'{topic}-001.md'
    session_path.write_text(session_md)

    # Fire generate_dive.py subprocess (identical to api_research_generate_dive)
    out_path = _next_dive_output_path(topic)
    try:
        proc, log_path = _start_dive_job(topic, out_path)
    except (OSError, subprocess.SubprocessError) as e:
        return jsonify({"ok": False, "error": f"failed to launch: {e}"}), 500

    _dd_proc = proc
    _DD_STATE_PATH.write_text(json.dumps({
        "topic":       topic,
        "output_path": str(out_path),
        "log_path":    str(log_path),
        "pid":         proc.pid,
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "status":      "running",
    }, indent=2))

    return jsonify({"ok": True, "topic": topic, "pid": proc.pid})


# ── API: Threads ──────────────────────────────────────────────────────────────

@research_bp.route('/api/research/thread/<topic>')
def api_research_thread_get(topic: str):
    """
    Return thread.json + recent annotations for a topic.
    GET /api/research/thread/gold-geopolitics
    """
    from domains.curator.research.agent.threads import load_thread, load_annotations
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
    from domains.curator.research.agent.threads import load_thread, cmd_annotate
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
    from domains.curator.research.agent.threads import cmd_create
    body         = request.get_json() or {}
    topic        = body.get("topic", "").strip()
    motivation   = body.get("motivation", "To be written").strip()
    prior_belief = body.get("prior_belief", "To be written").strip()

    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    cmd_create(topic, motivation, prior_belief)
    return jsonify({"ok": True, "topic": topic})


@research_bp.route('/api/research/spawn-thread', methods=['POST'])
def api_research_spawn_thread():
    """
    Spawn a new research thread from a Scan or Dive.
    POST /api/research/spawn-thread
    Body: {
      "topic": "taiwan-defense",
      "motivation": "...",
      "queries": ["query 1", "query 2"],
      "scan_id": "43c03",
      "scan_title": "...",
      "duration_days": 5
    }
    duration_days is stored as metadata only — no scheduling is wired.
    """
    import re as _re
    from datetime import date as _date
    from domains.curator.research.agent.threads import cmd_create, load_thread, save_thread

    body            = request.get_json() or {}
    topic           = body.get("topic", "").strip()
    motivation      = body.get("motivation", "").strip()
    queries         = body.get("queries") or []
    duration_days   = int(body.get("duration_days", 14))

    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400
    if not _re.match(r'^[a-z0-9-]+$', topic):
        return jsonify({"ok": False, "error": "topic must be lowercase letters, numbers, and hyphens only"}), 400
    if not queries:
        return jsonify({"ok": False, "error": "at least one query required"}), 400

    config_path = PATHS.config
    config      = json.loads(config_path.read_text())
    thread_root = DATA_ROOT / 'threads'

    if (
        topic in config.get('session_searches', {})
        or (thread_root / topic / 'thread.json').exists()
    ):
        return jsonify({"ok": False, "error": f"topic '{topic}' already exists"}), 409

    # Create thread record (reuse existing cmd_create)
    cmd_create(topic, motivation or "To be written", "")

    # Persist duration_days + expires (cmd_create doesn't accept these fields)
    thread = load_thread(topic)
    if thread:
        thread.session_searches = [q.strip() for q in queries if q.strip()]
        thread.triage_targets = [motivation] if motivation else []
        thread.duration_days = duration_days
        thread.expires = (_date.today() + timedelta(days=duration_days)).isoformat()
        save_thread(topic, thread)

    # Web-created sessions live in mounted data so container replacement
    # cannot erase the thread.
    (thread_root / topic / 'sessions').mkdir(parents=True, exist_ok=True)

    return jsonify({
        "ok": True,
        "status": "created",
        "topic": topic,
        "redirect": f"/research/sessions?topic={topic}"
    })


@research_bp.route('/api/research/thread/<topic>/retire', methods=['POST'])
def api_research_thread_retire(topic: str):
    """
    Retire a thread — hidden from UI, preserved on disk.
    POST /api/research/thread/gold-geopolitics/retire
    Body: {"reason": "..."}
    """
    from domains.curator.research.agent.threads import load_thread, cmd_retire
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
    from domains.curator.research.agent.threads import load_thread, cmd_wrap_up
    body   = request.get_json() or {}
    note   = body.get("note", "").strip()

    if not note:
        return jsonify({"ok": False, "error": "note required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    cmd_wrap_up(topic, note)
    return jsonify({"ok": True, "topic": topic, "status": "closed"})


@research_bp.route('/api/research/thread/<topic>/expire', methods=['POST'])
def api_research_thread_expire(topic: str):
    """
    Mark a thread as expired (Ready to close).
    POST /api/research/thread/gold-geopolitics/expire
    No body required.
    """
    from domains.curator.research.agent.threads import load_thread, save_thread
    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404
    thread.status = "expired"
    save_thread(topic, thread)
    return jsonify({"ok": True, "topic": topic, "status": "expired"})


@research_bp.route('/api/research/thread/<topic>/archive', methods=['POST'])
def api_research_thread_archive(topic: str):
    """
    Archive a thread — hidden from dashboard, preserved on disk.
    POST /api/research/thread/gold-geopolitics/archive
    No body required.
    """
    from domains.curator.research.agent.threads import load_thread, save_thread
    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404
    thread.status = "archived"
    save_thread(topic, thread)
    return jsonify({"ok": True, "topic": topic, "status": "archived"})


# ── HTML: Sessions ────────────────────────────────────────────────────────────

@research_bp.route('/research/sessions')
def research_sessions_ui():
    """Serve the session viewer page."""
    html = RESEARCH_ROOT / 'web' / 'sessions.html'
    if not html.exists():
        return "sessions.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


# ── API: Sessions ─────────────────────────────────────────────────────────────


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
        topics_dir = STATE_ROOT / 'topics'
        if not topics_dir.exists():
            return jsonify({"ok": True, "topics": []})
        result = []
        for td in sorted(topics_dir.iterdir()):
            if td.is_dir():
                sessions = sorted(
                    [f for f in td.glob('*.md') if not f.name.startswith('sources-') and f.name != 'CONTEXT.md' and not f.name.isupper()],
                    reverse=True
                )
                # Skip topics where every session is scan_synthetic scaffolding
                real_sessions = [f for f in sessions
                                 if _parse_session_header(f.read_text()).get('scan_synthetic') != 'true']
                if real_sessions:
                    result.append({"topic": td.name, "count": len(real_sessions)})
        return jsonify({"ok": True, "topics": result})

    topic_dir = STATE_ROOT / 'topics' / topic
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
        if hdr.get('scan_synthetic') == 'true':
            continue   # scaffolding — not a real research session
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
    topic_dir = STATE_ROOT / 'topics' / topic
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
    from domains.curator.research.agent.threads import load_thread, cmd_annotate
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


# ── Deep Dive helpers ──────────────────────────────────────────────────────────


def _find_dd_md(hash_id: str):
    """Return Path to the .md file for a given hash_id, or None."""
    # Sanitise: only lowercase hex + length guard
    if not _re.match(r'^[a-f0-9]{5}$', hash_id):
        return None
    matches = list(SCANS_DIR.glob(f'{hash_id}-*.md'))
    return matches[0] if matches else None


# ── HTML: Deep Dive viewer ─────────────────────────────────────────────────────

@research_bp.route('/research/deep-dive/<hash_id>')
def research_deep_dive_compat(hash_id: str):
    """Backward-compat redirect — old URL generated by scans/index.html."""
    return redirect(f'/research/scan/{hash_id}', 301)

@research_bp.route('/research/scan/<hash_id>')
def research_scan_view(hash_id: str):
    """
    Serve a Scan page dynamically from its .md source.
    Renders bibliography with "Add to Research" buttons.
    GET /research/scan/03624
    """
    md_path = _find_dd_md(hash_id)
    if not md_path:
        return f"Scan '{hash_id}' not found", 404

    dd      = _parse_scan_md(md_path)
    return _render_scan_page(dd, hash_id), 200, {'Content-Type': 'text/html'}


# ── API: Deep Dives ────────────────────────────────────────────────────────────

@research_bp.route('/api/research/scans')
def api_research_scans_list():
    """
    List all Scan .md files.
    GET /api/research/scans
    Returns: {ok, scans: [{hash_id, title, filename}]}
    """
    if not SCANS_DIR.exists():
        return jsonify({"ok": True, "scans": []})

    results = []
    for f in sorted(SCANS_DIR.glob('*.md'), reverse=True):
        parts   = f.stem.split('-', 1)
        hash_id = parts[0] if parts else f.stem
        title   = ''
        try:
            first_line = f.read_text().split('\n', 1)[0]
            title = first_line.replace('# ', '').strip()
        except Exception:
            pass
        results.append({'hash_id': hash_id, 'title': title, 'filename': f.name})

    return jsonify({"ok": True, "scans": results})


@research_bp.route('/api/research/scans/<hash_id>/bibliography')
def api_research_scan_bibliography(hash_id: str):
    """
    Return parsed bibliography items for a Scan.
    GET /api/research/scans/03624/bibliography
    Returns: {ok, hash_id, items: [{raw, title, url, has_url, author, bracket_note}]}
    """
    md_path = _find_dd_md(hash_id)
    if not md_path:
        return jsonify({"ok": False, "error": f"scan '{hash_id}' not found"}), 404
    items = _parse_scan_bibliography(md_path)
    return jsonify({"ok": True, "hash_id": hash_id, "items": items})


@research_bp.route('/api/research/inbox/add', methods=['POST'])
def api_research_inbox_add():
    """
    Add a deep dive bibliography item to the unassigned research inbox.
    POST /api/research/inbox/add
    Body: {title, url, has_url, author, raw, deep_dive_id}
    Writes to query_candidates.json with topic=null, status=unassigned.
    """
    body        = request.get_json() or {}
    title       = body.get('title', '').strip()
    url         = body.get('url') or None
    has_url     = bool(body.get('has_url', False))
    author      = body.get('author', '').strip()
    raw         = body.get('raw', '').strip()
    deep_dive_id = body.get('scan_id', body.get('deep_dive_id', '')).strip()  # accept both old and new key

    if not title and not raw:
        return jsonify({"ok": False, "error": "title or raw required"}), 400

    candidates_path = DATA_ROOT / 'feedback' / 'query_candidates.json'
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    records = json.loads(candidates_path.read_text()) if candidates_path.exists() else []

    new_id = secrets.token_hex(4)   # 8 hex chars
    record = {
        "id":           new_id,
        "topic":        None,
        "status":       "unassigned",
        "source":   "scan",
        "scan_id":  deep_dive_id,
        "title":        title or raw[:120],
        "url":          url,
        "has_url":      has_url,
        "author":       author,
        "query":        title or raw[:120],   # keep 'query' for backwards-compat display
        "timestamp":    datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
    }
    records.append(record)
    candidates_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return jsonify({"ok": True, "id": new_id, "title": record['title']})


@research_bp.route('/api/research/candidates/assign', methods=['POST'])
def api_research_candidates_assign():
    """
    Assign an unassigned inbox item to a topic.
    POST /api/research/candidates/assign
    Body: {id, topic}
    Updates status from 'unassigned' → 'candidate', sets topic.
    """
    body  = request.get_json() or {}
    id_   = body.get('id', '').strip()
    topic = body.get('topic', '').strip()
    if not id_:
        return jsonify({"ok": False, "error": "id required"}), 400
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    candidates_path = DATA_ROOT / 'feedback' / 'query_candidates.json'
    if not candidates_path.exists():
        return jsonify({"ok": False, "error": "candidates file not found"}), 404

    records = json.loads(candidates_path.read_text())
    updated = False
    title   = ''
    for r in records:
        if r.get('id') == id_:
            r['topic']  = topic
            r['status'] = 'candidate'
            title = r.get('title', r.get('query', ''))
            updated = True
            break

    if not updated:
        return jsonify({"ok": False, "error": f"id '{id_}' not found"}), 404

    candidates_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return jsonify({"ok": True, "id": id_, "topic": topic, "title": title})

# ─────────────────────────────────────────────
# ANNOTATIONS — Comment anywhere
# Added: 2026-03-27
# ─────────────────────────────────────────────

from scripts.annotations import save_annotation, get_recent_annotations as _get_recent_annotations

@research_bp.route('/api/research/annotate', methods=['POST'])
def annotate():
    """
    Save an annotation from any research page.
    Body: { note, domain, page, topic?, ref_type?, ref_id?,
            ref_title?, ref_text?, url?, type? }
    """
    try:
        data = request.get_json()
        if not data or not data.get('note', '').strip():
            return jsonify({'error': 'note required'}), 400

        record = save_annotation(
            note=data['note'],
            domain=data.get('domain', 'research'),
            page=data.get('page', 'unknown'),
            topic=data.get('topic'),
            ref_type=data.get('ref_type'),
            ref_id=data.get('ref_id'),
            ref_title=data.get('ref_title'),
            ref_text=data.get('ref_text'),
            url=data.get('url'),
            annotation_type=data.get('type', 'reaction')
        )
        return jsonify({'status': 'saved', 'note_id': record['note_id']})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@research_bp.route('/api/research/annotations', methods=['GET'])
def get_annotations():
    """
    Get recent annotations.
    Query params: domain, topic, limit (default 10)
    """
    try:
        domain = request.args.get('domain', 'research')
        topic = request.args.get('topic')
        limit = int(request.args.get('limit', 10))
        annotations = _get_recent_annotations(domain, topic, limit)
        return jsonify({'annotations': annotations, 'count': len(annotations)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@research_bp.route('/research/static/<path:filename>')
def research_static(filename):
    """Serve static assets for research pages (annotations.js, annotations.css, etc.)"""
    from flask import send_from_directory
    static_dir = RESEARCH_ROOT / 'web' / 'static'
    return send_from_directory(str(static_dir), filename)


# ── Leanings — UI page ────────────────────────────────────────────────────────

@research_bp.route('/api/research/topics/status', methods=['GET'])
def api_topics_status():
    """
    Return all Topics with state, expiry, and session count.
    Reads data/threads/*/thread.json directly — no config.json dependency.
    """
    threads_dir = DATA_ROOT / 'threads'
    topics = []
    if threads_dir.exists():
        import re as _re3
        from datetime import date as _date2
        _sess_re  = _re3.compile(r'^[a-z][a-z0-9-]*\.md$')
        _static   = {"CONTEXT.md", "ORIGIN.md", "STORY_FOR_CLAUDE_AI.md", "README.md"}
        for topic_dir in sorted(threads_dir.iterdir()):
            thread_file = topic_dir / 'thread.json'
            if not thread_file.exists():
                continue
            t      = json.loads(thread_file.read_text())
            slug   = t.get('topic', topic_dir.name)
            status = t.get('status', 'dormant')
            expires = t.get('expires')

            days_remaining = None
            if expires:
                try:
                    days_remaining = (_date2.fromisoformat(expires) - _date2.today()).days
                except Exception:
                    pass

            # Count static and web-created durable sessions.
            session_dirs = [
                STATE_ROOT / 'topics' / slug,
                topic_dir / 'sessions',
            ]
            sessions_by_name = {}
            for sessions_dir in session_dirs:
                if not sessions_dir.exists():
                    continue
                for path in sessions_dir.iterdir():
                    if (
                        path.is_file()
                        and _sess_re.match(path.name)
                        and path.name not in _static
                        and not path.name.startswith("sources-candidates-")
                    ):
                        sessions_by_name[path.name] = path
            sessions = list(sessions_by_name.values())
            session_count = len(sessions)
            last_run = None
            if sessions:
                newest = max(sessions, key=lambda p: p.stat().st_mtime)
                last_run = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d")

            topics.append({
                "slug":           slug,
                "status":         status,
                "expires":        expires,
                "days_remaining": days_remaining,
                "duration_days":  t.get('duration_days'),
                "session_count":  session_count,
                # Field name matches dashboard.html's t.last_run and the
                # other thread-status endpoint's convention in this file —
                # was "last_session" here, which the dashboard never read,
                # so it always showed "never run" regardless of activity.
                "last_run":       last_run,
                "motivation":     (t.get('motivation') or '')[:100],
            })

    return jsonify({"ok": True, "topics": topics})


@research_bp.route('/api/research/topic/<slug>/activate', methods=['POST'])
def api_activate_topic(slug: str):
    """
    Activate a dormant or paused Topic.
    Body (optional): {duration_days: int, note: str}
    """
    thread_file = DATA_ROOT / 'threads' / slug / 'thread.json'
    if not thread_file.exists():
        return jsonify({"ok": False, "error": f"topic '{slug}' not found"}), 404

    body        = request.get_json() or {}
    note        = body.get('note', '')
    t           = json.loads(thread_file.read_text())
    from_status = t.get('status', 'dormant')

    if from_status not in {'dormant', 'paused'}:
        return jsonify({"ok": False, "error": f"can only activate from dormant or paused, not '{from_status}'"}), 400

    days    = int(body.get('duration_days') or t.get('duration_days') or 14)
    now     = datetime.now(timezone.utc)
    expires = (now.date() + timedelta(days=days)).isoformat()

    t['status']         = 'active-pull'
    t['activated_at']   = now.isoformat()
    t['expires']        = expires
    t['duration_days']  = days
    t['paused_at']      = None
    t['remaining_days'] = None

    if 'state_history' not in t:
        t['state_history'] = []
    t['state_history'].append({
        "from": from_status,
        "to":   "active-pull",
        "at":   now.isoformat(),
        "by":   "briefing-ui",
        "note": note or f"activated from briefing, expires {expires}",
    })

    thread_file.write_text(json.dumps(t, indent=2))
    return jsonify({"ok": True, "slug": slug, "status": "active-pull", "expires": expires, "duration_days": days})


# ── Leanings — UI page ────────────────────────────────────────────────────────

@research_bp.route('/research/leanings')
def research_leanings_ui():
    """Serve the Leanings page."""
    html_path = RESEARCH_ROOT / 'web' / 'leanings.html'
    if not html_path.exists():
        return "leanings.html not found", 404
    tips_file = PATHS.tips
    try:
        tips = json.loads(tips_file.read_text()) if tips_file.exists() else {}
    except Exception:
        tips = {}
    entry = tips.get('curator.leanings', {})
    tip = entry.get('text') if entry.get('active') else None
    return render_template_string(html_path.read_text(), tip=tip)


# ── Leanings — API ────────────────────────────────────────────────────────────

@research_bp.route('/api/research/leanings', methods=['GET'])
def api_list_leanings():
    """List all leanings, with badge flag for each."""
    try:
        from domains.curator.research.agent.leanings import list_leanings, needs_badge
        leanings = list_leanings()
        for l in leanings:
            l['badge'] = needs_badge(l)
        return jsonify({"ok": True, "leanings": leanings, "count": len(leanings)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings', methods=['POST'])
def api_create_leaning():
    """Create a new leaning. Body: {title, state?, topics?, notes?}"""
    body  = request.get_json() or {}
    title = body.get("title", "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    try:
        from domains.curator.research.agent.leanings import create_leaning
        leaning = create_leaning(
            title  = title,
            state  = body.get("state", "question"),
            topics = body.get("topics", []),
            notes  = body.get("notes", ""),
        )
        return jsonify({"ok": True, "leaning": leaning}), 201
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>', methods=['GET'])
def api_get_leaning(leaning_id):
    """Get a single leaning by ID."""
    try:
        from domains.curator.research.agent.leanings import get_leaning, needs_badge
        leaning = get_leaning(leaning_id)
        if leaning is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        leaning['badge'] = needs_badge(leaning)
        return jsonify({"ok": True, "leaning": leaning})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>', methods=['PATCH'])
def api_update_leaning(leaning_id):
    """Update title, state, notes, or topics. Body: {title?, state?, notes?, topics?}"""
    body = request.get_json() or {}
    try:
        from domains.curator.research.agent.leanings import update_leaning
        leaning = update_leaning(leaning_id, **{
            k: v for k, v in body.items()
            if k in ("title", "state", "notes", "topics")
        })
        if leaning is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "leaning": leaning})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>', methods=['DELETE'])
def api_delete_leaning(leaning_id):
    """Delete a leaning."""
    try:
        from domains.curator.research.agent.leanings import delete_leaning
        ok = delete_leaning(leaning_id)
        if not ok:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>/evidence', methods=['POST'])
def api_add_evidence(leaning_id):
    """Add an evidence item. Body: {title, url, source?, stance?, note?}"""
    body  = request.get_json() or {}
    title = body.get("title", "").strip()
    url   = body.get("url", "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    try:
        from domains.curator.research.agent.leanings import add_evidence
        ev = add_evidence(
            leaning_id = leaning_id,
            title      = title,
            url        = url,
            source     = body.get("source", ""),
            stance     = body.get("stance", "neutral"),
            note       = body.get("note", ""),
        )
        if ev is None:
            return jsonify({"ok": False, "error": "leaning not found"}), 404
        return jsonify({"ok": True, "evidence": ev}), 201
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>/evidence/<ev_id>', methods=['DELETE'])
def api_remove_evidence(leaning_id, ev_id):
    """Remove an evidence item from a leaning."""
    try:
        from domains.curator.research.agent.leanings import remove_evidence
        ok = remove_evidence(leaning_id, ev_id)
        if not ok:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@research_bp.route('/api/research/leanings/<leaning_id>/teammate-read', methods=['POST'])
def api_teammate_read(leaning_id):
    """
    Trigger an on-demand Sonnet teammate read for a leaning.
    Reads linked thread session summaries as context.
    Budget-gated: ~$0.01-0.02 per call.
    """
    try:
        from domains.curator.research.agent.leanings import get_leaning, build_teammate_prompt, store_teammate_read
        import anthropic as _anthropic

        leaning = get_leaning(leaning_id)
        if leaning is None:
            return jsonify({"ok": False, "error": "leaning not found"}), 404

        # Gather brief excerpts from linked thread sessions (free text, no API call)
        thread_summaries = []
        for topic in leaning.get("topics", []):
            thread_dir = DATA_ROOT / "threads" / topic
            if not thread_dir.exists():
                continue
            sessions = sorted(thread_dir.glob("*.md"))[-3:]  # last 3 sessions
            for s in sessions:
                text = s.read_text()
                # Extract the findings section (first 400 chars is enough for context)
                excerpt = text[:600].replace('\n', ' ').strip()
                thread_summaries.append(f"[{topic}] {excerpt}…")

        system_prompt, user_prompt = build_teammate_prompt(leaning, thread_summaries)

        api_key = get_secret("ANTHROPIC_API_KEY", "anthropic", "api_key")
        if not api_key:
            return jsonify({"ok": False, "error": "Anthropic API key not configured"}), 500

        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model      = "claude-sonnet-4-5",
            max_tokens = 512,
            system     = system_prompt,
            messages   = [{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip()

        tokens_in  = msg.usage.input_tokens
        tokens_out = msg.usage.output_tokens
        cost_usd   = (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000

        tr = store_teammate_read(leaning_id, text)

        return jsonify({
            "ok":         True,
            "text":       text,
            "tokens_in":  tokens_in,
            "tokens_out": tokens_out,
            "cost_usd":   round(cost_usd, 4),
            "teammate_read": tr,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
