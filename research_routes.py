"""
research_routes.py — Flask Blueprint for Research Intelligence UI

All /research/* and /api/research/* routes live here.
Registered in curator_server.py with a try/except guard so any import
failure here cannot crash the main curator server.

To add new research routes: edit only this file. Never touch curator_server.py
for research features.
"""

import json
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
