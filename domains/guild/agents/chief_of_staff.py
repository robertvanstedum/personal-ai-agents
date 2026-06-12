#!/usr/bin/env python3
"""
domains/guild/agents/chief_of_staff.py — Chief of Staff Agent
Guild Phase 3

Flask service on port 8769:
  /chat   — conversational endpoint (Telegram + future HTML portal)
  /status — agent state
  /health — liveness probe

Telegram polling thread handles !cos / !chief / !ops on rvsopenbot.
"""

import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import keyring
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request
from openai import OpenAI

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent.parent.parent.parent   # repo root
COS_CONTEXT_FILE = BASE_DIR / "domains/guild/config/cos_context.json"
COS_SOUL_FILE    = BASE_DIR / "domains/guild/config/cos_soul.md"
COS_MEMORY_FILE  = BASE_DIR / "data/guild/memory/cos_memory.md"
AGENDA_FILE      = BASE_DIR / "data/guild/cos_agenda.json"
LOGS_DIR         = BASE_DIR / "logs"

# ── DB (optional — degrades gracefully if Docker is down) ────────────────────

DSN = "postgresql://minimoi:simple123@localhost:5432/personal_agents"
_DB_AVAILABLE = False

def _check_db():
    global _DB_AVAILABLE
    try:
        import psycopg2
        conn = psycopg2.connect(DSN)
        conn.close()
        _DB_AVAILABLE = True
    except Exception:
        _DB_AVAILABLE = False
    return _DB_AVAILABLE

@contextmanager
def _db():
    import psycopg2, psycopg2.extras
    conn = None
    try:
        conn = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
        yield conn
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cos] %(levelname)s %(message)s"
)
log = logging.getLogger("cos")

def _log_file(event: str, detail: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}: {detail}\n"
    try:
        (LOGS_DIR / "cos.log").open("a").write(line)
    except Exception:
        print(line, end="")

# ── Memory ────────────────────────────────────────────────────────────────────

MEMORY_CAP = 7_500

def _read_memory() -> str:
    if COS_MEMORY_FILE.exists():
        return COS_MEMORY_FILE.read_text()
    return "(no memory yet)"

def _append_memory(entry: str):
    """Append a dated entry to cos_memory.md. Refuses if file > MEMORY_CAP chars."""
    try:
        current = _read_memory()
        if current == "(no memory yet)":
            current = ""
        if len(current) > MEMORY_CAP:
            _log_file("memory_write_blocked",
                      f"File at {len(current)} chars — distillation needed")
            return False
        dated = f"\n[chat] {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {entry}\n"
        COS_MEMORY_FILE.write_text(current + dated)
        return True
    except Exception as e:
        _log_file("memory_write_error", str(e))
        return False

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Chief of Staff for mini-moi — Robert van Stedum's personal AI agent platform.

--- SOUL ---
{cos_soul}

--- CONTEXT FILE ---
{cos_context}

--- MEMORY ---
{cos_memory}

--- OPERATIONS STATUS (live) ---
{ops_status}

--- TODAY ---
{date_str}

Hard limits (never cross these):
- Do not take external actions (no API calls, no messages to third parties) without Robert confirming
- Do not stop, restart, or modify services — that is Operations' domain
- Do not delete or overwrite any data
- Do not make decisions that belong to Robert — recommend, don't decide
- Do not access files or data outside what is provided in this prompt
"""

def _build_system_prompt() -> str:
    try:
        cos_soul = COS_SOUL_FILE.read_text().strip()
    except Exception:
        cos_soul = "(soul unavailable)"

    try:
        cos_context = json.dumps(json.loads(COS_CONTEXT_FILE.read_text()), indent=2)
    except Exception:
        cos_context = "(context unavailable)"

    cos_memory = _read_memory()

    try:
        ops = requests.get("http://localhost:8768/status", timeout=3).json()
        ops_str = json.dumps(ops, indent=2)
    except Exception:
        ops_str = "Operations status unavailable"

    date_str = datetime.now().strftime("%A, %B %d, %Y %H:%M")

    return _SYSTEM_PROMPT.format(
        cos_soul=cos_soul,
        cos_context=cos_context,
        cos_memory=cos_memory,
        ops_status=ops_str,
        date_str=date_str,
    )

# ── Tools ─────────────────────────────────────────────────────────────────────

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ops_status",
            "description": "Get the current live status of the Operations agent — disk usage, service health, uptime, open escalations.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ops_log",
            "description": "Get the last N maintenance actions Operations has taken — what it fixed, what it escalated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of recent actions to return. Default 5.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_domain_health",
            "description": "Check whether the core mini-moi services are responding — Curator (8766), German (8767), Portal (5001).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queue_recommendation",
            "description": "Add an actionable item to the CoS recommendations queue for Robert to review later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Which domain: curator, german, career_focus, mini_moi, operations",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the recommendation is",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0.0 to 1.0",
                    },
                },
                "required": ["domain", "description"],
            },
        },
    },
]


def _dispatch_tool(name: str, args: dict) -> dict:
    """Execute a tool call and return the result dict."""
    if name == "get_ops_status":
        try:
            return requests.get("http://localhost:8768/status", timeout=3).json()
        except Exception as e:
            return {"error": f"Operations unreachable: {e}"}

    elif name == "get_ops_log":
        try:
            rows = requests.get("http://localhost:8768/log", timeout=3).json()
            n = args.get("n", 5)
            # Normalise datetime objects that may have been serialised
            result = []
            for r in rows[:n]:
                r2 = dict(r)
                if r2.get("timestamp"):
                    r2["timestamp"] = str(r2["timestamp"])[:16]
                result.append(r2)
            return {"entries": result}
        except Exception as e:
            return {"error": f"Operations log unavailable: {e}"}

    elif name == "get_domain_health":
        results = {}
        for svc_name, port in [("curator", 8766), ("german", 8767), ("portal", 5001)]:
            try:
                r = requests.get(f"http://localhost:{port}/", timeout=2)
                results[svc_name] = "ok" if r.status_code < 500 else f"error {r.status_code}"
            except Exception:
                results[svc_name] = "not responding"
        return results

    elif name == "queue_recommendation":
        entry = {
            "domain": args.get("domain", "unknown"),
            "description": args.get("description", ""),
            "confidence": args.get("confidence", 0.8),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        # Try DB first, fall back to JSON file
        queued_via = "file"
        if _DB_AVAILABLE:
            try:
                with _db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO guild.cos_agenda
                               (domain, description, confidence, status, created_at)
                               VALUES (%s, %s, %s, 'pending', NOW())""",
                            (entry["domain"], entry["description"], entry["confidence"]),
                        )
                queued_via = "db"
            except Exception as e:
                _log_file("queue_recommendation_db_error", str(e))

        if queued_via == "file":
            AGENDA_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if AGENDA_FILE.exists():
                try:
                    existing = json.loads(AGENDA_FILE.read_text())
                except Exception:
                    pass
            existing.append(entry)
            AGENDA_FILE.write_text(json.dumps(existing, indent=2))

        _log_file("recommendation_queued",
                  f"[{entry['domain']}] {entry['description'][:80]} (via {queued_via})")
        return {"queued": True, "via": queued_via, "domain": entry["domain"]}

    return {"error": f"Unknown tool: {name}"}

# ── LLM chat loop ─────────────────────────────────────────────────────────────

def _get_xai_client() -> OpenAI:
    api_key = keyring.get_password("xai", "api_key")
    if not api_key:
        raise ValueError("xAI API key not found in keyring (xai / api_key)")
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")


def _chat(text: str) -> str:
    """Run the CoS chat loop. Returns the final reply string."""
    system = _build_system_prompt()
    messages = [{"role": "user", "content": text}]

    client = _get_xai_client()
    MAX_TOOL_ROUNDS = 3

    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[{"role": "system", "content": system}] + messages,
            tools=_TOOLS,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=600,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            _maybe_update_memory(text, reply)
            return reply

        # Append assistant message with tool calls
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        # Dispatch each tool call and append results
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            result = _dispatch_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    # Fallback if loop exhausted
    return "I ran into a loop trying to gather data. Try asking again."


def _maybe_update_memory(user_text: str, reply: str):
    """Make a cheap secondary call to check if this conversation is memory-worthy."""
    try:
        client = _get_xai_client()
        check_prompt = (
            "Review this conversation snippet. "
            "Did it contain a new fact about Robert's goals, a decision he made, "
            "or a non-obvious system observation worth remembering long-term?\n\n"
            f"User: {user_text}\n"
            f"CoS: {reply}\n\n"
            "If yes: reply with a single sentence starting with the key fact (no preamble).\n"
            "If no: reply with exactly the word NONE."
        )
        resp = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[{"role": "user", "content": check_prompt}],
            temperature=0.0,
            max_tokens=80,
        )
        result = (resp.choices[0].message.content or "").strip()
        if result and result.upper() != "NONE":
            _append_memory(result)
    except Exception as e:
        _log_file("memory_check_error", str(e))

# ── Agent state ───────────────────────────────────────────────────────────────

_state = {
    "state":        "starting",
    "started_at":   datetime.now(timezone.utc).isoformat(),
    "chat_count":   0,
}
_state_lock = threading.Lock()

# ── Loop state (updated by each run) ─────────────────────────────────────────

_loop_state: dict[str, dict] = {
    "loop_a": {"name": "career_focus_scout", "last_run": None, "last_result": None, "error": None},
    "loop_b": {"name": "german_watch",       "last_run": None, "last_result": None, "error": None},
    "loop_c": {"name": "curator_scout",      "last_run": None, "last_result": None, "error": None},
    "loop_d": {"name": "novelty_watch",      "last_run": None, "last_result": None, "error": None},
}
_loop_lock = threading.Lock()


def _run_loop(loop_id: str, fn):
    """Wrapper that records last_run, result, and any error for /loops status."""
    try:
        result = fn()
        with _loop_lock:
            _loop_state[loop_id]["last_run"] = datetime.now(timezone.utc).isoformat()
            _loop_state[loop_id]["last_result"] = result
            _loop_state[loop_id]["error"] = None
    except Exception as e:
        log.error("Loop %s error: %s", loop_id, e)
        with _loop_lock:
            _loop_state[loop_id]["last_run"] = datetime.now(timezone.utc).isoformat()
            _loop_state[loop_id]["error"] = str(e)

def _inc_chat():
    with _state_lock:
        _state["chat_count"] += 1
        _state["state"] = "running"
        _state["last_chat"] = datetime.now(timezone.utc).isoformat()

# ── Telegram polling (rvsopenbot) ─────────────────────────────────────────────

def _tg_send(token: str, chat_id: str, text: str):
    """Send a Telegram message via rvsopenbot."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        _log_file("tg_send_error", str(e))


def _handle_tg_text(text: str, token: str, chat_id: str):
    """Route incoming Telegram text to !ops or !cos handlers."""
    lower = text.lower().strip()

    # ── !ops commands (moved from minimoi_cmd_bot) ────────────────────────────
    if lower.startswith("!ops"):
        parts = text.strip().split()
        cmd = parts[1].lower() if len(parts) > 1 else "status"

        if cmd == "help":
            _tg_send(token, chat_id,
                "⚙️ <b>Operations commands</b>\n"
                "  <code>!ops disk</code>    — disk usage + service summary\n"
                "  <code>!ops status</code>  — full agent state\n"
                "  <code>!ops log</code>     — last 5 maintenance actions\n"
                "  <code>!ops help</code>    — this list\n\n"
                "💬 <b>CoS chat</b>\n"
                "  <code>!cos [anything]</code> — ask the Chief of Staff")
            return

        try:
            if cmd in ("disk", "status"):
                d = requests.get("http://localhost:8768/status", timeout=5).json()
                la = d.get("last_action") or {}
                disk_pct  = la.get("disk_pct", "?")
                services  = la.get("services", "?")
                uptime_s  = d.get("uptime_seconds", 0)
                uptime_h  = uptime_s // 3600
                uptime_m  = (uptime_s % 3600) // 60
                escalations = d.get("open_escalations", 0)
                checks    = d.get("checks_run", 0)
                if cmd == "disk":
                    esc_note = f"\n⚠️ Open escalations: {escalations}" if escalations else ""
                    _tg_send(token, chat_id,
                        f"⚙️ <b>Disk</b> — {disk_pct}% used\n"
                        f"Services: {services}\n"
                        f"Checks run: {checks}{esc_note}")
                else:
                    esc_icon = "⚠️" if escalations else "✅"
                    _tg_send(token, chat_id,
                        f"⚙️ <b>Operations</b> — {d.get('state', '?')}\n"
                        f"Uptime: {uptime_h}h {uptime_m}m\n"
                        f"Checks run: {checks}\n"
                        f"Disk: {disk_pct}%  •  Services: {services}\n"
                        f"{esc_icon} Open escalations: {escalations}")
            elif cmd == "log":
                rows = requests.get("http://localhost:8768/log", timeout=5).json()[:5]
                if not rows:
                    _tg_send(token, chat_id, "⚙️ No maintenance log entries yet.")
                    return
                lines = ["⚙️ <b>Last 5 maintenance actions</b>"]
                for row in rows:
                    ts   = str(row.get("timestamp") or "")[:16].replace("T", " ")
                    tier = row.get("tier", "?")
                    act  = row.get("action", "?")
                    out  = (row.get("outcome") or "")[:60]
                    auto = " ✅" if row.get("auto_resolved") else ""
                    lines.append(f"[T{tier}] {ts}  {act}{auto}\n  {out}")
                _tg_send(token, chat_id, "\n\n".join(lines))
            else:
                _tg_send(token, chat_id,
                    f"Unknown ops command: <code>{cmd}</code>\nTry <code>!ops help</code>")
        except requests.exceptions.ConnectionError:
            _tg_send(token, chat_id, "❌ Operations agent unreachable (port 8768).")
        except Exception as e:
            _tg_send(token, chat_id, f"❌ !ops error: {e}")
        return

    # ── !dev command ──────────────────────────────────────────────────────────
    if lower.startswith("!dev"):
        try:
            d = requests.get("http://localhost:8770/status", timeout=5).json()
            events    = d.get("events_processed", 0)
            archived  = d.get("docs_archived", 0)
            mem_chars = d.get("memory_chars", 0)
            mem_cap   = d.get("memory_cap", 8000)
            uptime_s  = d.get("uptime_seconds", 0)
            uptime_h  = uptime_s // 3600
            uptime_m  = (uptime_s % 3600) // 60
            last_evt  = d.get("last_event", {})
            watching  = d.get("watching", [])
            last_line = ""
            if last_evt:
                last_line = (
                    f"\nLast: {last_evt.get('file', '?')} "
                    f"({last_evt.get('doc_type', '?')})"
                )
            _tg_send(token, chat_id,
                f"🔍 <b>Design/Dev Agent</b>\n"
                f"Uptime: {uptime_h}h {uptime_m}m\n"
                f"Events processed: {events}\n"
                f"Docs archived: {archived}\n"
                f"Memory: {mem_chars}/{mem_cap} chars\n"
                f"Watching: {', '.join(watching)}"
                f"{last_line}"
            )
        except requests.exceptions.ConnectionError:
            _tg_send(token, chat_id, "❌ Design/Dev agent unreachable (port 8770).")
        except Exception as e:
            _tg_send(token, chat_id, f"❌ !dev error: {e}")
        return

    # ── !cos / !chief commands ────────────────────────────────────────────────
    if lower.startswith("!cos") or lower.startswith("!chief"):
        if lower.startswith("!cos"):
            query = text[4:].strip()
        else:
            query = text[7:].strip()

        if not query:
            _tg_send(token, chat_id,
                "💬 <b>Chief of Staff</b> — ask me anything.\n"
                "Example: <code>!cos check disk space</code>\n"
                "Example: <code>!cos what's on my agenda this week?</code>\n"
                "Or just type naturally — no prefix needed.")
            return

        _tg_send(token, chat_id, "⏳ Thinking…")
        try:
            reply = _chat(query)
            _inc_chat()
            _tg_send(token, chat_id, reply)
        except Exception as e:
            _log_file("chat_error", str(e))
            _tg_send(token, chat_id, f"❌ CoS error: {e}")
        return

    # ── Plain text — route to CoS chat (no prefix required) ──────────────────
    _tg_send(token, chat_id, "⏳ Thinking…")
    try:
        reply = _chat(text)
        _inc_chat()
        _tg_send(token, chat_id, reply)
    except Exception as e:
        _log_file("chat_error", str(e))
        _tg_send(token, chat_id, f"❌ CoS error: {e}")


def _telegram_poll_loop():
    """Long-poll rvsopenbot for incoming messages. Runs in background thread."""
    token = keyring.get_password("telegram", "bot_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        _log_file("telegram_poll", "rvsopenbot token not found — polling disabled")
        return
    if not chat_id:
        _log_file("telegram_poll", "TELEGRAM_CHAT_ID not set — polling disabled")
        return

    log.info(f"Telegram polling started (rvsopenbot, chat_id={chat_id})")
    offset = 0

    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"timeout": 30, "offset": offset, "allowed_updates": ["message"]},
                timeout=40,
            )
            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                incoming_chat_id = str(msg.get("chat", {}).get("id", ""))

                # Only respond to Robert's chat
                if incoming_chat_id != str(chat_id):
                    continue
                if not text:
                    continue

                log.info(f"Telegram message received: {text[:60]!r}")

                lower = text.lower()
                # !ops / !cos / !chief go to the command handler
                # Everything else routes to CoS chat (OpenClaw-style natural language)
                threading.Thread(
                    target=_handle_tg_text,
                    args=(text, token, chat_id),
                    daemon=True,
                ).start()

        except requests.exceptions.Timeout:
            pass  # normal long-poll timeout, loop again
        except Exception as e:
            _log_file("telegram_poll_error", str(e))
            time.sleep(5)

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/chat", methods=["POST"])
def chat_endpoint():
    """Primary chat endpoint — used by Telegram handler and future HTML portal."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"reply": "Nothing to respond to."})
    try:
        reply = _chat(text)
        _inc_chat()
        return jsonify({"reply": reply})
    except Exception as e:
        _log_file("chat_endpoint_error", str(e))
        return jsonify({"reply": f"CoS error: {e}"}), 500


@app.route("/status")
def status():
    with _state_lock:
        snap = dict(_state)
    snap["uptime_seconds"] = int(
        (datetime.now(timezone.utc) -
         datetime.fromisoformat(snap["started_at"])).total_seconds()
    )
    snap["db_available"] = _DB_AVAILABLE
    snap["memory_chars"] = len(_read_memory())
    return jsonify(snap)


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/loops")
def loops_status():
    """Intelligence loop status — last run time and result per loop."""
    with _loop_lock:
        snap = {k: dict(v) for k, v in _loop_state.items()}
    return jsonify(snap)


@app.route("/event", methods=["POST"])
def receive_event():
    """
    Receive structured events from other Guild agents (Design/Dev, Operations, loops).
    Logs to cos.log and queues high-priority items to agenda.
    Design/Dev pings this in parallel with Telegram for every doc event.
    """
    body = request.get_json(silent=True) or {}
    source     = body.get("source", "unknown")
    event_type = body.get("event_type", "unknown")
    file_path  = body.get("file_path", "")
    doc_type   = body.get("doc_type", "")
    summary    = body.get("summary", "")
    rule       = body.get("escalation_rule", {})

    _log_file("external_event",
              f"[{source}] {event_type}: {Path(file_path).name} — {summary[:80]}")

    # Queue to agenda if priority is high/medium
    priority = rule.get("priority", "info") if isinstance(rule, dict) else "info"
    if priority in ("high", "medium"):
        entry = {
            "domain": source,
            "description": f"[{event_type}] {Path(file_path).name}: {summary}",
            "confidence": 0.8,
            "loop_name": source,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Try DB first
        queued_via = "file"
        if _DB_AVAILABLE:
            try:
                with _db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO guild.cos_agenda
                               (domain, description, confidence, loop_name, status, created_at)
                               VALUES (%s, %s, %s, %s, 'pending', NOW())""",
                            (source, entry["description"], 0.8, source),
                        )
                queued_via = "db"
            except Exception as e:
                _log_file("event_db_error", str(e))

        if queued_via == "file":
            AGENDA_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing: list = []
            if AGENDA_FILE.exists():
                try:
                    existing = json.loads(AGENDA_FILE.read_text())
                except Exception:
                    pass
            existing.append(entry)
            AGENDA_FILE.write_text(json.dumps(existing, indent=2))

    return jsonify({"received": True, "queued": priority in ("high", "medium")})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    PORT = int(os.environ.get("PORT", 8769))

    print(f"""
💼  Chief of Staff starting on port {PORT}…
   /chat    — conversational endpoint (POST {{"text": "..."}})
   /status  — agent state
   /health  — liveness probe
   /loops   — intelligence loop status
   /event   — receive events from Guild agents (POST)
""")

    # 1. Check DB availability
    if _check_db():
        print("   DB: connected ✅")
    else:
        print("   DB: unavailable — queue_recommendation will use file fallback")

    # 2. Verify xAI key exists
    api_key = keyring.get_password("xai", "api_key")
    if api_key:
        print("   xAI: key found ✅")
    else:
        print("   ⚠️  xAI API key not found in keyring — chat will fail")

    # 3. Start Telegram polling thread
    t = threading.Thread(target=_telegram_poll_loop, daemon=True, name="tg-poll")
    t.start()
    print("   Thread started: tg-poll (rvsopenbot)")

    # 4. Import loops (deferred so CoS starts even if a loop has an import error)
    # Ensure repo root is on sys.path — needed when running under launchd where
    # the working directory may differ from the script directory.
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        from domains.guild.agents.loops.cos_job_search  import run_career_focus_scout
        from domains.guild.agents.loops.cos_german_watch import run_german_watch
        from domains.guild.agents.loops.cos_curator_watch import run_curator_scout
        from domains.guild.agents.loops.cos_novelty_watch import run_novelty_watch
        loops_ok = True
    except Exception as e:
        log.error("Loop import failed — scheduler disabled: %s", e)
        print(f"   ⚠️  Loop import failed: {e}")
        loops_ok = False

    # 5. Start APScheduler (only if loops imported cleanly)
    if loops_ok:
        scheduler = BackgroundScheduler()
        # Loop A — twice daily
        scheduler.add_job(
            lambda: _run_loop("loop_a", run_career_focus_scout),
            "cron", hour="6,18", id="loop_a", misfire_grace_time=600
        )
        # Loop B — weekly Sunday 09:00
        scheduler.add_job(
            lambda: _run_loop("loop_b", run_german_watch),
            "cron", day_of_week="sun", hour=9, id="loop_b", misfire_grace_time=600
        )
        # Loop C — weekly Sunday 10:00
        scheduler.add_job(
            lambda: _run_loop("loop_c", run_curator_scout),
            "cron", day_of_week="sun", hour=10, id="loop_c", misfire_grace_time=600
        )
        # Loop D — 1st and 15th of month 08:00
        scheduler.add_job(
            lambda: _run_loop("loop_d", run_novelty_watch),
            "cron", day="1,15", hour=8, id="loop_d", misfire_grace_time=600
        )
        scheduler.start()
        print("   Scheduler: loop_a(6+18h) loop_b(Sun 9h) loop_c(Sun 10h) loop_d(1st+15th 8h) ✅")
    else:
        print("   Scheduler: disabled (loop import error — see logs)")

    # 6. Mark running
    with _state_lock:
        _state["state"] = "running"
    print(f"   State: running\n")

    # 7. Serve
    app.run(host="localhost", port=PORT, debug=False)


if __name__ == "__main__":
    main()
