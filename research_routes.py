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
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, redirect

# ── Blueprint ─────────────────────────────────────────────────────────────────

research_bp = Blueprint('research', __name__)

# Path to the research-intelligence project (relative to this file's location)
RESEARCH_ROOT  = Path(__file__).resolve().parent / '_NewDomains' / 'research-intelligence'
DEEP_DIVES_DIR = Path(__file__).resolve().parent / 'interests' / '2026' / 'deep-dives'

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

@research_bp.route('/research')
def research_home():
    return redirect('/research/sessions')

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

        # Thread lifecycle fields
        from agent.threads import load_thread as _lt
        _thread = _lt(topic)
        thread_status       = _thread.status      if _thread else "active"
        thread_expires      = _thread.expires     if _thread else None
        thread_dd_generated = _thread.deeper_dive_generated if _thread else False
        thread_dd_path      = _thread.deeper_dive_path      if _thread else None

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

_RUN_STATE_PATH = RESEARCH_ROOT / 'data' / 'run_state.json'
_RUN_STATE_MAX_AGE_MINUTES = 60

# Module-level reference to the running Popen object (single session only).
# Stored here so status checks can call proc.poll() which correctly reaps zombies.
# NOTE: This reference is lost on server restart — the stale-age check in
# _run_state_active() provides the safety net in that case.
_active_proc: subprocess.Popen | None = None

# ── Deeper Dive generation state (same pattern as run-session) ────────────────

_DD_STATE_PATH = RESEARCH_ROOT / 'data' / 'dd_run_state.json'
_DD_STATE_MAX_AGE_MINUTES = 120   # Opus calls can take 2–5 min; generous ceiling
_dd_proc: subprocess.Popen | None = None


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
    topic_dir = RESEARCH_ROOT / 'topics' / topic
    max_n = 0
    if topic_dir.exists():
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
    config_path = RESEARCH_ROOT / 'agent' / 'config.json'
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        return jsonify({"ok": False, "error": f"could not read config: {e}"}), 500

    if topic not in config.get('session_searches', {}):
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
    try:
        proc = subprocess.Popen(
            [sys.executable, 'agent/research.py',
             '--topic', topic,
             '--session-name', session_name],
            cwd=str(RESEARCH_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
    if not running:
        _active_proc = None
        _RUN_STATE_PATH.unlink(missing_ok=True)

    return jsonify({
        "running":      running,
        "topic":        state.get('topic'),
        "session_name": state.get('session_name'),
        "started_at":   state.get('started_at'),
    })


# ── API: Deeper Dive generation ───────────────────────────────────────────────

def _dd_state_active() -> bool:
    """Return True if a Deeper Dive generation is currently running (not stale)."""
    if not _DD_STATE_PATH.exists():
        return False
    try:
        state = json.loads(_DD_STATE_PATH.read_text())
        started = datetime.fromisoformat(state.get('started_at', ''))
        age = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if age > _DD_STATE_MAX_AGE_MINUTES:
            _DD_STATE_PATH.unlink(missing_ok=True)
            return False
    except Exception:
        return False
    return True


def _next_dd_output_path(topic: str) -> Path:
    """Return next sequential output path: {topic}-deeper-dive-NNN.md"""
    import re as _re2
    dd_dir = RESEARCH_ROOT / 'data' / 'deeper_dives'
    dd_dir.mkdir(parents=True, exist_ok=True)
    existing = list(dd_dir.glob(f'{topic}-deeper-dive-*.md'))
    nums = []
    for f in existing:
        m = _re2.search(r'-deeper-dive-(\d+)\.md$', f.name)
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return dd_dir / f'{topic}-deeper-dive-{n:03d}.md'


@research_bp.route('/research/deeper-dive-confirm')
def research_deeper_dive_confirm():
    """
    Confirmation page before generating a Deeper Dive.
    GET /research/deeper-dive-confirm?topic=strait-of-hormuz
    """
    topic = request.args.get('topic', '').strip()
    if not topic:
        return "topic parameter required", 400

    html = RESEARCH_ROOT / 'web' / 'deeper_dive_confirm.html'
    if not html.exists():
        return "deeper_dive_confirm.html not found", 404
    return html.read_text(), 200, {'Content-Type': 'text/html'}


@research_bp.route('/api/research/deeper-dive-confirm-data')
def api_research_deeper_dive_confirm_data():
    """
    Return thread metadata for the Deeper Dive confirmation page.
    GET /api/research/deeper-dive-confirm-data?topic=strait-of-hormuz
    """
    from agent.threads import load_thread
    topic = request.args.get('topic', '').strip()
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400

    thread = load_thread(topic)
    if not thread:
        return jsonify({"ok": False, "error": f"No thread found for '{topic}'"}), 404

    # Count sessions
    STATIC_FILES = {"CONTEXT.md", "ORIGIN.md", "STORY_FOR_CLAUDE_AI.md", "README.md"}
    SESSION_RE = _re.compile(r'^[a-z][a-z0-9-]*\.md$')
    topic_dir = RESEARCH_ROOT / 'topics' / topic
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


@research_bp.route('/api/research/generate-deeper-dive', methods=['POST'])
def api_research_generate_deeper_dive():
    """
    Spawn a Deeper Dive generation for a topic as a non-blocking subprocess.
    POST /api/research/generate-deeper-dive
    Body: {"topic": "strait-of-hormuz"}
    Returns immediately — generation runs in background.
    Poll GET /api/research/generate-deeper-dive/status for completion.
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

    out_path = _next_dd_output_path(topic)
    script   = RESEARCH_ROOT / 'scripts' / 'generate_deeper_dive.py'

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script),
             '--topic', topic,
             '--output-path', str(out_path)],
            cwd=str(RESEARCH_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[generate-deeper-dive] OSError: {e}", file=sys.stderr)
        return jsonify({"ok": False, "error": f"failed to launch: {e}"}), 500

    _dd_proc = proc

    state = {
        "topic":       topic,
        "output_path": str(out_path),
        "pid":         proc.pid,
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "status":      "running",
    }
    _DD_STATE_PATH.write_text(json.dumps(state, indent=2))

    return jsonify({"ok": True, "topic": topic, "pid": proc.pid})


@research_bp.route('/api/research/generate-deeper-dive/status')
def api_research_generate_deeper_dive_status():
    """
    Check if a Deeper Dive generation is currently running.
    GET /api/research/generate-deeper-dive/status
    Returns: {running: bool, topic, output_path} or {running: false}
    """
    global _dd_proc

    if not _DD_STATE_PATH.exists():
        return jsonify({"running": False})

    try:
        state = json.loads(_DD_STATE_PATH.read_text())
    except Exception:
        return jsonify({"running": False})

    # Liveness check
    running = False
    if _dd_proc is not None:
        running = (_dd_proc.poll() is None)
    else:
        # Fallback after server restart: check age
        try:
            started = datetime.fromisoformat(state.get('started_at', ''))
            age = (datetime.now(timezone.utc) - started).total_seconds() / 60
            running = age < _DD_STATE_MAX_AGE_MINUTES
        except Exception:
            running = False

    if not running:
        _dd_proc = None
        # Generation finished — update thread.json
        topic      = state.get('topic', '')
        out_path   = state.get('output_path', '')
        if topic and out_path and Path(out_path).exists():
            try:
                from agent.threads import load_thread, save_thread
                thread = load_thread(topic)
                if thread:
                    thread.status               = "closed"
                    thread.deeper_dive_generated = True
                    thread.deeper_dive_path      = str(Path(out_path).relative_to(RESEARCH_ROOT))
                    save_thread(topic, thread)
            except Exception as e:
                print(f"[dd-status] failed to update thread.json: {e}", file=sys.stderr)

        _DD_STATE_PATH.unlink(missing_ok=True)
        return jsonify({
            "running":     False,
            "topic":       topic,
            "output_path": out_path,
        })

    return jsonify({
        "running":    True,
        "topic":      state.get('topic'),
        "started_at": state.get('started_at'),
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


@research_bp.route('/api/research/spawn-thread', methods=['POST'])
def api_research_spawn_thread():
    """
    Spawn a new research thread from a deep dive.
    POST /api/research/spawn-thread
    Body: {
      "topic": "taiwan-defense",
      "motivation": "...",
      "queries": ["query 1", "query 2"],
      "deep_dive_id": "43c03",
      "deep_dive_title": "...",
      "duration_days": 5
    }
    duration_days is stored as metadata only — no scheduling is wired.
    """
    import re as _re
    from datetime import date as _date
    from agent.threads import cmd_create, load_thread, save_thread

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

    config_path = RESEARCH_ROOT / 'agent' / 'config.json'
    config      = json.loads(config_path.read_text())

    if topic in config.get('session_searches', {}):
        return jsonify({"ok": False, "error": f"topic '{topic}' already exists"}), 409

    # Add queries to config
    config.setdefault('session_searches', {})[topic] = [q.strip() for q in queries if q.strip()]
    config_path.write_text(json.dumps(config, indent=2))

    # Create thread record (reuse existing cmd_create)
    cmd_create(topic, motivation or "To be written", "")

    # Persist duration_days + expires (cmd_create doesn't accept these fields)
    thread = load_thread(topic)
    if thread:
        thread.duration_days = duration_days
        thread.expires = (_date.today() + timedelta(days=duration_days)).isoformat()
        save_thread(topic, thread)

    # Ensure topics/<topic>/ directory exists so sessions page sidebar shows it
    (RESEARCH_ROOT / 'topics' / topic).mkdir(parents=True, exist_ok=True)

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


@research_bp.route('/api/research/thread/<topic>/expire', methods=['POST'])
def api_research_thread_expire(topic: str):
    """
    Mark a thread as expired (Ready to close).
    POST /api/research/thread/gold-geopolitics/expire
    No body required.
    """
    from agent.threads import load_thread, save_thread
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
    from agent.threads import load_thread, save_thread
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


# ── Deep Dive helpers ──────────────────────────────────────────────────────────

def _dd_escape(text: str) -> str:
    """HTML-escape and convert **bold** / *italic* inline markdown."""
    text = _html_lib.escape(str(text))
    text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = _re.sub(r'\*([^*]+?)\*',   r'<em>\1</em>',        text)
    return text


def _dd_md_to_html(text: str) -> str:
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
            parts.append(f'<p>{_dd_escape(" ".join(buf_para))}</p>')
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
            parts.append(f'<h2>{_dd_escape(stripped[3:])}</h2>')
        elif stripped.startswith('### '):
            close_ul(); flush_para()
            parts.append(f'<h3>{_dd_escape(stripped[4:])}</h3>')
        elif stripped.startswith('- ') or stripped.startswith('* '):
            flush_para()
            if not in_ul:
                parts.append('<ul>')
                in_ul = True
            parts.append(f'<li>{_dd_escape(stripped[2:])}</li>')
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


def _parse_deep_dive_bibliography(md_path: Path) -> list:
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


def _parse_deep_dive_md(md_path: Path) -> dict:
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
            r'##\s+Your Interest\s*\n(.*?)(?=\n---|\n## |\Z)',
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
        'bibliography':  _parse_deep_dive_bibliography(md_path),
        'cost':         cost_m.group(3) if cost_m else None,
        'tokens_in':    cost_m.group(1) if cost_m else None,
        'tokens_out':   cost_m.group(2) if cost_m else None,
    }


def _find_dd_md(hash_id: str):
    """Return Path to the .md file for a given hash_id, or None."""
    # Sanitise: only lowercase hex + length guard
    if not _re.match(r'^[a-f0-9]{5}$', hash_id):
        return None
    matches = list(DEEP_DIVES_DIR.glob(f'{hash_id}-*.md'))
    return matches[0] if matches else None


# ── HTML: Deep Dive viewer ─────────────────────────────────────────────────────

@research_bp.route('/research/deep-dive/<hash_id>')
def research_deep_dive_view(hash_id: str):
    """
    Serve a deep dive page dynamically from its .md source.
    Renders bibliography with "Add to Research" buttons.
    GET /research/deep-dive/03624
    """
    md_path = _find_dd_md(hash_id)
    if not md_path:
        return f"Deep dive '{hash_id}' not found", 404

    dd      = _parse_deep_dive_md(md_path)
    title   = _html_lib.escape(dd['title'])
    meta    = dd['metadata']
    bib     = dd['bibliography']

    # ── Bibliography HTML ────────────────────────────────────────────────────
    bib_rows = []
    for idx, item in enumerate(bib):
        t   = _html_lib.escape(item['title'] or item['raw'][:80])
        auth = _html_lib.escape(item['author'])
        raw_esc = _html_lib.escape(item['raw'])

        if item['bracket_note']:
            link_html = f'<span class="bib-title">{t}</span> <span class="bib-library-tag">Library</span>'
        elif item['has_url']:
            url_esc = _html_lib.escape(item['url'])
            link_html = f'<a class="bib-title" href="{url_esc}" target="_blank" rel="noopener">{t}</a>'
        elif item['title']:
            scholar_q = urllib.parse.quote(item['title'])
            link_html = f'<a class="bib-title" href="https://scholar.google.com/scholar?q={scholar_q}" target="_blank" rel="noopener">{t}</a> <span class="bib-scholar-tag">Scholar</span>'
        else:
            link_html = f'<span class="bib-title">{t}</span>'

        bib_rows.append(f'''
        <div class="bib-item" id="bib-{idx}" data-idx="{idx}">
          <div class="bib-main">
            <div class="bib-text">
              {link_html}
              {f'<span class="bib-author">{auth}</span>' if auth else ''}
            </div>
            <button class="btn-add-research"
                    onclick="addToResearch({idx})"
                    data-title="{_html_lib.escape(item['title'] or '')}"
                    data-url="{_html_lib.escape(item['url'] or '')}"
                    data-has-url="{'true' if item['has_url'] else 'false'}"
                    data-author="{_html_lib.escape(item['author'])}"
                    data-raw="{_html_lib.escape(item['raw'][:200])}">
              + Research
            </button>
            <span class="bib-added-tick" id="tick-{idx}" style="display:none">✓ Added</span>
          </div>
        </div>''')

    bib_html = '\n'.join(bib_rows) if bib_rows else '<p class="bib-empty">No bibliography items found.</p>'

    # ── Metadata block ───────────────────────────────────────────────────────
    source_link = ''
    if meta.get('url') and meta.get('url') != 'N/A':
        src_esc = _html_lib.escape(meta['url'])
        src_label = _html_lib.escape(meta.get('source', meta['url']))
        source_link = f'<a href="{src_esc}" target="_blank" rel="noopener">{src_label}</a>'
    elif meta.get('source'):
        source_link = _html_lib.escape(meta['source'])

    meta_parts = []
    if source_link:
        meta_parts.append(f'<div class="meta-row"><span class="meta-label">Source</span>{source_link}</div>')
    if meta.get('date'):
        meta_parts.append(f'<div class="meta-row"><span class="meta-label">Analyzed</span>{_html_lib.escape(meta["date"])}</div>')
    if meta.get('interest'):
        meta_parts.append(f'<div class="meta-row"><span class="meta-label">Your interest</span>{_html_lib.escape(meta["interest"])}</div>')
    if meta.get('focus'):
        meta_parts.append(f'<div class="meta-row"><span class="meta-label">Focus</span>{_html_lib.escape(meta["focus"])}</div>')
    meta_block    = '\n'.join(meta_parts)
    interest_text = _html_lib.escape(meta.get('interest', ''))

    # Derive a short topic slug: strip stop words, take first 3 meaningful words
    _STOP = {'a','an','the','in','on','at','of','to','and','or','for','by',
             'with','from','is','are','was','were','be','been','as','it','its',
             'this','that','how','why','what','when','where','who'}
    _words = _re.sub(r'[^a-z0-9\s]', ' ', dd['title'].lower()).split()
    _keep  = [w for w in _words if w not in _STOP and len(w) > 1][:3]
    topic_slug = '-'.join(_keep) if _keep else _re.sub(r'[^a-z0-9]+', '-', dd['title'].lower()).strip('-')[:40]

    footer = ''
    if dd['cost']:
        footer = f'<p class="dd-footer">Generated by Claude Sonnet &nbsp;·&nbsp; {dd["tokens_in"]} in + {dd["tokens_out"]} out tokens &nbsp;·&nbsp; ${dd["cost"]}</p>'

    page = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} – Deep Dive</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@400;500&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #f5f0e8; --bg-texture: #ede8df; --surface: #faf7f2; --surface2: #f0ebe0;
      --border: #ddd6c8; --border2: #c8bfaf; --text: #2a2418; --text-muted: #6b5f4e;
      --text-dim: #9e9080; --accent: #8b5e2a; --accent-dim: rgba(139,94,42,0.08);
      --accent-glow: rgba(139,94,42,0.18); --shadow: rgba(42,36,24,0.08);
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Source Sans 3', sans-serif; background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.65; }}
    header {{ border-bottom: 1px solid var(--border); padding: 0 32px; height: 52px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; background: var(--surface); }}
    .site-brand {{ font-family: 'Playfair Display', serif; font-size: 20px; font-weight: 600; color: var(--text); text-decoration: none; letter-spacing: -0.02em; }}
    .header-nav {{ display: flex; gap: 4px; }}
    .nav-link {{ font-family: 'DM Mono', monospace; font-size: 11px; letter-spacing: 0.05em; color: var(--text-muted); text-decoration: none; padding: 6px 14px; border-radius: 6px; border: 1px solid transparent; transition: all 0.15s; }}
    .nav-link:hover {{ color: var(--text); background: var(--surface2); }}
    .nav-link.active {{ color: var(--accent); background: var(--accent-dim); border-color: rgba(139,94,42,0.2); }}
    main {{ max-width: 780px; margin: 0 auto; padding: 2.5rem 1.5rem 4rem; }}
    h1 {{ font-family: 'Playfair Display', serif; font-size: 1.75rem; font-weight: normal; line-height: 1.3; margin-bottom: 1.25rem; }}
    .meta-block {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 1rem 1.25rem; margin-bottom: 2rem; font-size: 0.88rem; }}
    .meta-row {{ display: flex; gap: 0.75rem; margin-bottom: 0.35rem; }}
    .meta-row:last-child {{ margin-bottom: 0; }}
    .meta-label {{ font-family: 'DM Mono', monospace; font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.06em; min-width: 90px; padding-top: 1px; }}
    .meta-row a {{ color: var(--accent); text-decoration: none; }}
    .meta-row a:hover {{ text-decoration: underline; }}
    .analysis h2 {{ font-family: 'Playfair Display', serif; font-size: 1.15rem; font-weight: 600; margin: 2rem 0 0.6rem; color: var(--text); }}
    .analysis h3 {{ font-family: 'Playfair Display', serif; font-size: 1rem; font-weight: 600; margin: 1.4rem 0 0.4rem; color: var(--text); }}
    .analysis p {{ margin-bottom: 0.9rem; }}
    .analysis ul {{ margin: 0.5rem 0 0.9rem 1.4rem; }}
    .analysis li {{ margin-bottom: 0.35rem; }}
    .analysis hr {{ border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }}
    .sources-section {{ margin-top: 2.5rem; padding-top: 1.5rem; border-top: 2px solid var(--border); }}
    .sources-section h2 {{ font-family: 'Playfair Display', serif; font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem; color: var(--text); }}
    .bib-item {{ padding: 0.75rem 0; border-bottom: 1px solid var(--border); }}
    .bib-item:last-child {{ border-bottom: none; }}
    .bib-main {{ display: flex; align-items: flex-start; gap: 0.75rem; }}
    .bib-text {{ flex: 1; font-size: 0.9rem; line-height: 1.45; }}
    .bib-title {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
    a.bib-title:hover {{ text-decoration: underline; }}
    .bib-author {{ display: block; font-size: 0.82rem; color: var(--text-muted); margin-top: 0.15rem; }}
    .bib-library-tag, .bib-scholar-tag {{ font-family: 'DM Mono', monospace; font-size: 0.7rem; color: var(--text-dim); border: 1px solid var(--border); border-radius: 3px; padding: 1px 5px; margin-left: 6px; vertical-align: middle; }}
    .btn-add-research {{ font-family: 'DM Mono', monospace; font-size: 0.72rem; color: var(--accent); background: var(--accent-dim); border: 1px solid rgba(139,94,42,0.2); border-radius: 4px; padding: 4px 10px; cursor: pointer; white-space: nowrap; flex-shrink: 0; transition: all 0.15s; }}
    .btn-add-research:hover {{ background: var(--accent); color: var(--surface); }}
    .btn-add-research:disabled {{ opacity: 0.4; cursor: not-allowed; }}
    .bib-added-tick {{ font-family: 'DM Mono', monospace; font-size: 0.72rem; color: #4a8c28; white-space: nowrap; flex-shrink: 0; }}
    .bib-empty {{ color: var(--text-muted); font-style: italic; }}
    .dd-footer {{ margin-top: 2.5rem; font-size: 0.8rem; color: var(--text-dim); font-family: 'DM Mono', monospace; }}
    #toast {{ position: fixed; bottom: 24px; right: 24px; background: #4a8c28; color: #fff; font-family: 'DM Mono', monospace; font-size: 0.82rem; padding: 10px 18px; border-radius: 6px; display: none; z-index: 200; }}
    .spawn-thread-panel {{ margin-top: 3rem; padding: 1.5rem 1.75rem; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }}
    .spawn-thread-panel h2 {{ font-family: 'Playfair Display', serif; font-size: 1.1rem; font-weight: 600; margin-bottom: 1.25rem; color: var(--text); }}
    .spawn-field {{ margin-bottom: 1rem; }}
    .spawn-field label {{ display: block; font-family: 'DM Mono', monospace; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin-bottom: 0.4rem; }}
    .spawn-field label .muted {{ font-style: italic; text-transform: none; letter-spacing: 0; color: var(--text-dim); opacity: 0.7; }}
    .spawn-field input[type="text"], .spawn-field textarea, .spawn-field select {{ width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 5px; padding: 8px 12px; font-family: 'Source Sans 3', sans-serif; font-size: 0.9rem; color: var(--text); outline: none; transition: border-color 0.15s; }}
    .spawn-field input:focus, .spawn-field textarea:focus, .spawn-field select:focus {{ border-color: var(--accent); }}
    .spawn-field textarea {{ resize: vertical; min-height: 72px; }}
    #st-queries {{ display: flex; flex-direction: column; gap: 0.4rem; }}
    .st-query-row {{ display: flex; align-items: flex-start; gap: 0.5rem; font-size: 0.88rem; }}
    .spawn-field label.st-query-row {{ text-transform: none; letter-spacing: 0; font-family: 'Source Sans 3', sans-serif; color: var(--text); }}
    .st-query-row input[type="checkbox"] {{ margin-top: 3px; flex-shrink: 0; accent-color: var(--accent); }}
    .st-query-row span {{ color: var(--text); line-height: 1.4; }}
    #st-launch {{ margin-top: 1.25rem; background: var(--accent); color: var(--surface); border: none; border-radius: 5px; padding: 10px 22px; font-family: 'DM Mono', monospace; font-size: 0.82rem; cursor: pointer; transition: opacity 0.15s; }}
    #st-launch:hover {{ opacity: 0.85; }}
    #st-launch:disabled {{ opacity: 0.45; cursor: not-allowed; }}
    #st-status {{ margin-top: 0.75rem; font-family: 'DM Mono', monospace; font-size: 0.82rem; color: var(--text-muted); min-height: 1.2em; }}
    #st-status.err {{ color: #8b2020; }}
  </style>
  <link rel="stylesheet" href="/research/static/css/nav.css">
  <script src="/research/static/js/nav.js"></script>
</head>
<body data-domain="curator" data-page="deep-dive" data-ref-id="{_html_lib.escape(hash_id)}">
<header>
  <a href="/" class="site-brand">mini-moi · CURATOR</a>
  <nav class="header-nav">
    <a href="/" class="nav-link">Daily</a>
    <a href="/curator_library.html" class="nav-link">Library</a>
    <a href="/interests/2026/deep-dives/index.html" class="nav-link active">Deep Dives</a>
    <a href="/curator_intelligence.html" class="nav-link">Observations</a>
    <a href="/research/dashboard" class="nav-link">Research</a>
    <div class="nav-more-wrapper">
      <button class="nav-more-btn">···</button>
      <div class="nav-more-dropdown">
        <a href="/curator_priorities.html">Priorities</a>
      </div>
    </div>
  </nav>
</header>
<main>
  <h1>{title}</h1>
  <div class="meta-block">
    {meta_block}
  </div>
  <div class="analysis">
    {dd['analysis_html']}
  </div>
  <div class="sources-section">
    <h2>Sources &amp; Further Reading</h2>
    {bib_html}
  </div>
  <section class="spawn-thread-panel" id="spawn-thread">
    <h2>🔬 Start Research Thread</h2>
    <div class="spawn-field">
      <label>Topic name (slug)</label>
      <input id="st-topic" type="text" placeholder="e.g. taiwan-defense" value="{topic_slug}">
    </div>
    <div class="spawn-field">
      <label>Motivation</label>
      <textarea id="st-motivation" rows="3">{interest_text}</textarea>
    </div>
    <div class="spawn-field" id="st-queries-wrapper">
      <label>Starting queries <span class="muted">(from bibliography)</span></label>
      <div id="st-queries"><span style="color:var(--text-dim);font-size:0.85rem">Loading…</span></div>
    </div>
    <div class="spawn-field">
      <label>Duration</label>
      <select id="st-duration">
        <option value="3">3 days</option>
        <option value="5" selected>5 days</option>
        <option value="7">7 days</option>
        <option value="14">14 days</option>
      </select>
    </div>
    <button id="st-launch" onclick="spawnThread()">Launch Research Thread →</button>
    <div id="st-status"></div>
  </section>
  {footer}
</main>
<div id="toast"></div>
<script>
  const HASH_ID = '{_html_lib.escape(hash_id)}';

  async function addToResearch(idx) {{
    const btn  = document.querySelector(`[data-idx="${{idx}}"] .btn-add-research`) ||
                 document.querySelector(`#bib-${{idx}} .btn-add-research`);
    const tick = document.getElementById(`tick-${{idx}}`);
    if (!btn) return;
    btn.disabled = true;
    const payload = {{
      title:        btn.dataset.title,
      url:          btn.dataset.url || null,
      has_url:      btn.dataset.hasUrl === 'true',
      author:       btn.dataset.author,
      raw:          btn.dataset.raw,
      deep_dive_id: HASH_ID,
    }};
    try {{
      const res  = await fetch('/api/research/inbox/add', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(payload),
      }});
      const data = await res.json();
      if (data.ok) {{
        btn.style.display = 'none';
        if (tick) tick.style.display = 'inline';
        showToast(`Added to Research inbox`);
      }} else {{
        btn.disabled = false;
        showToast(`Error: ${{data.error}}`, true);
      }}
    }} catch (err) {{
      btn.disabled = false;
      showToast(`Network error`, true);
    }}
  }}

  function showToast(msg, isErr) {{
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.background = isErr ? '#8b2020' : '#4a8c28';
    t.style.display = 'block';
    setTimeout(() => {{ t.style.display = 'none'; }}, 3000);
  }}
</script>
<script>
  // ── Spawn Thread ─────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async () => {{
    const container = document.getElementById('st-queries');
    try {{
      const res  = await fetch('/api/research/deep-dives/{_html_lib.escape(hash_id)}/bibliography');
      const data = await res.json();
      if (!data.ok || !data.items.length) {{
        container.innerHTML = '<span style="color:var(--text-dim);font-size:0.85rem">No bibliography items found.</span>';
        return;
      }}
      container.innerHTML = data.items
        .filter(item => item.title && item.title.trim())
        .map((item, i) => {{
          const safe = item.title.replace(/"/g, '&quot;').replace(/</g, '&lt;');
          return `<label class="st-query-row"><input type="checkbox" value="${{safe}}" checked><span>${{safe}}</span></label>`;
        }})
        .join('');
    }} catch (e) {{
      container.innerHTML = '<span style="color:#8b2020;font-size:0.85rem">Could not load bibliography.</span>';
    }}
  }});

  async function spawnThread() {{
    const btn      = document.getElementById('st-launch');
    const status   = document.getElementById('st-status');
    const topic    = document.getElementById('st-topic').value.trim();
    const motivation = document.getElementById('st-motivation').value.trim();
    const duration = parseInt(document.getElementById('st-duration').value, 10);
    const queries  = Array.from(document.querySelectorAll('#st-queries input[type="checkbox"]:checked'))
                         .map(cb => cb.value)
                         .filter(Boolean);

    status.className = '';
    if (!topic) {{ status.textContent = 'Topic slug is required.'; return; }}
    if (!/^[a-z0-9-]+$/.test(topic)) {{ status.textContent = 'Topic must be lowercase letters, numbers, and hyphens only.'; return; }}
    if (!queries.length) {{ status.textContent = 'Select at least one starting query.'; return; }}

    btn.disabled = true;
    status.textContent = 'Launching…';

    try {{
      const res  = await fetch('/api/research/spawn-thread', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
          topic,
          motivation,
          queries,
          deep_dive_id: HASH_ID,
          deep_dive_title: document.querySelector('h1') ? document.querySelector('h1').textContent : '',
          duration_days: duration,
        }}),
      }});
      const data = await res.json();
      if (data.ok) {{
        status.textContent = 'Thread created! Redirecting…';
        setTimeout(() => {{ window.location.href = data.redirect; }}, 800);
      }} else {{
        status.textContent = data.error || 'Unknown error.';
        status.className = 'err';
        btn.disabled = false;
      }}
    }} catch (e) {{
      status.textContent = 'Network error — please try again.';
      status.className = 'err';
      btn.disabled = false;
    }}
  }}
</script>
<link rel="stylesheet" href="/research/static/css/annotations.css">
<script src="/research/static/js/annotations.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', () => {{
    AnnotationSystem.initFloating();
    AnnotationSystem.initSelectionPopup();
  }});
</script>
</body>
</html>'''

    return page, 200, {'Content-Type': 'text/html'}


# ── API: Deep Dives ────────────────────────────────────────────────────────────

@research_bp.route('/api/research/deep-dives')
def api_research_deep_dives_list():
    """
    List all deep dive .md files.
    GET /api/research/deep-dives
    Returns: {ok, deep_dives: [{hash_id, title, filename}]}
    """
    if not DEEP_DIVES_DIR.exists():
        return jsonify({"ok": True, "deep_dives": []})

    results = []
    for f in sorted(DEEP_DIVES_DIR.glob('*.md'), reverse=True):
        parts   = f.stem.split('-', 1)
        hash_id = parts[0] if parts else f.stem
        title   = ''
        try:
            first_line = f.read_text().split('\n', 1)[0]
            title = first_line.replace('# ', '').strip()
        except Exception:
            pass
        results.append({'hash_id': hash_id, 'title': title, 'filename': f.name})

    return jsonify({"ok": True, "deep_dives": results})


@research_bp.route('/api/research/deep-dives/<hash_id>/bibliography')
def api_research_deep_dive_bibliography(hash_id: str):
    """
    Return parsed bibliography items for a deep dive.
    GET /api/research/deep-dives/03624/bibliography
    Returns: {ok, hash_id, items: [{raw, title, url, has_url, author, bracket_note}]}
    """
    md_path = _find_dd_md(hash_id)
    if not md_path:
        return jsonify({"ok": False, "error": f"deep dive '{hash_id}' not found"}), 404
    items = _parse_deep_dive_bibliography(md_path)
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
    deep_dive_id = body.get('deep_dive_id', '').strip()

    if not title and not raw:
        return jsonify({"ok": False, "error": "title or raw required"}), 400

    candidates_path = RESEARCH_ROOT / 'data' / 'feedback' / 'query_candidates.json'
    records = json.loads(candidates_path.read_text()) if candidates_path.exists() else []

    new_id = secrets.token_hex(4)   # 8 hex chars
    record = {
        "id":           new_id,
        "topic":        None,
        "status":       "unassigned",
        "source":       "deep_dive",
        "deep_dive_id": deep_dive_id,
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

    candidates_path = RESEARCH_ROOT / 'data' / 'feedback' / 'query_candidates.json'
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

from annotations import save_annotation, get_recent_annotations as _get_recent_annotations

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
