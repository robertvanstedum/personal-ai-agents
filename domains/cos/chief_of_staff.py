#!/usr/bin/env python3
"""
domains/cos/chief_of_staff.py — Chief of Staff Agent
Guild Phase 3

Flask service on port 8769:
  /chat   — conversational endpoint (Telegram + future HTML portal)
  /status — agent state
  /health — liveness probe

Telegram polling is handled by telegram_cos_bot.py (minimoi_cos_bot).
DISABLE_TELEGRAM_POLL=1 must be set when cos-scheduler runs alongside cos-bot.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, render_template, redirect

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent.parent.parent   # repo root
COS_CONTEXT_FILE = BASE_DIR / "domains/guild/config/cos_context.json"
COS_SOUL_FILE    = BASE_DIR / "domains/guild/config/cos_soul.md"
COS_MEMORY_FILE  = BASE_DIR / "data/cos_memory.md"
AGENDA_FILE      = BASE_DIR / "data/guild/cos_agenda.json"
LOGS_DIR         = BASE_DIR / "logs"

# ── DB (optional — degrades gracefully if Docker is down) ────────────────────

DSN = os.environ.get("DATABASE_URL", "postgresql://minimoi:simple123@localhost:5432/personal_agents")
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

--- BUILD STATE (live) ---
{build_state}

--- TODAY ---
{date_str}

Hard limits (never cross these):
- Do not take external actions (no API calls, no messages to third parties) without Robert confirming
- Do not stop, restart, or modify services — that is Operations' domain
- Do not delete or overwrite any data
- Do not make decisions that belong to Robert — recommend, don't decide
- Do not access files or data outside what is provided in this prompt
"""


def _read_build_state() -> str:
    """Query guild.design_log for active items — for /chat context."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status,
                           COALESCE(spec_title, spec_file, file_path) AS title,
                           EXTRACT(EPOCH FROM (NOW() - last_transition_at))/86400 AS age_days,
                           blocked_reason,
                           github_issue
                    FROM guild.design_log
                    WHERE status IN ('spec_ready','in_build','blocked','incomplete')
                    ORDER BY
                      CASE status
                        WHEN 'blocked'    THEN 1
                        WHEN 'in_build'   THEN 2
                        WHEN 'spec_ready' THEN 3
                        WHEN 'incomplete' THEN 4
                      END,
                      last_transition_at DESC
                """)
                rows = cur.fetchall()
        if not rows:
            return "No active build items."
        lines = []
        for status, title, age_days, blocked_reason, github_issue in rows:
            age = f"{int(age_days)}d" if age_days else "?"
            line = f"[{status.upper()}] {title} (age: {age})"
            if blocked_reason:
                line += f" — {blocked_reason}"
            if github_issue:
                line += f" #{github_issue}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        return f"Build state unavailable: {e}"


def _run_build_discipline_check() -> dict:
    """
    Loop F — daily staleness check.
    Flags spec_ready/in_build items past threshold as blocked.
    Threshold from cos_context.json build_discipline.staleness_days (default 3).
    """
    try:
        raw = json.loads(COS_CONTEXT_FILE.read_text())
        threshold_days = raw.get("build_discipline", {}).get("staleness_days", 3)
    except Exception:
        threshold_days = 3

    flagged = []
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, COALESCE(spec_title, spec_file, file_path) AS title,
                       status, last_transition_at
                FROM guild.design_log
                WHERE status IN ('spec_ready','in_build')
                  AND last_transition_at < NOW() - INTERVAL '%s days'
            """, (threshold_days,))
            stale = cur.fetchall()

            for log_id, title, prev_status, _ in stale:
                cur.execute(
                    "UPDATE guild.design_log SET status='blocked', "
                    "blocked_reason=%s, last_transition_at=NOW() WHERE id=%s",
                    (f"stale: no progress in {threshold_days}+ days", log_id)
                )
                cur.execute(
                    "INSERT INTO guild.design_log_transitions "
                    "(design_log_id, from_status, to_status, triggered_by, reason) "
                    "VALUES (%s,%s,'blocked','cos_staleness',%s)",
                    (log_id, prev_status,
                     f"stale: no progress in {threshold_days}+ days")
                )
                flagged.append(title)

        conn.commit()
        conn.close()
    except Exception as e:
        log.error("build_discipline_check error: %s", e)
        return {"flagged": [], "error": str(e)}

    if flagged:
        msg = (
            f"⏰ <b>Build discipline:</b> {len(flagged)} item(s) stale "
            f"({threshold_days}+ days) → flagged blocked\n"
            + "\n".join(f"  • {t}" for t in flagged)
            + "\nReview at /guild/build/queue"
        )
        from utils.role import is_production
        if not is_production():
            log.info("build_discipline_check: standby — Telegram suppressed")
        else:
            try:
                from utils.telegram import get_cos_token, get_chat_id as _get_chat_id
                token   = get_cos_token()
                chat_id = _get_chat_id() or os.environ.get("TELEGRAM_CHAT_ID", "8379221702")
                if token:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                        timeout=5,
                    )
            except Exception:
                pass

    log.info("build_discipline_check: %d item(s) flagged stale", len(flagged))
    return {"flagged": flagged, "threshold_days": threshold_days}


def _run_guest_nudge_check() -> dict:
    """
    Loop G — hourly guest access request staleness nudge.
    First nudge after 2h pending; repeat every 6h thereafter.
    Sends one combined Telegram message for all stale requests.
    """
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name
                    FROM guild.guest_requests
                    WHERE status = 'requested'
                      AND (
                        (last_nudged_at IS NULL     AND requested_at < NOW() - INTERVAL '2 hours')
                        OR
                        (last_nudged_at IS NOT NULL AND last_nudged_at < NOW() - INTERVAL '6 hours')
                      )
                """)
                stale = cur.fetchall()

                if not stale:
                    return {"nudged": 0}

                ids = [r["id"] for r in stale]
                names = [r["name"] for r in stale]

                cur.execute(
                    "UPDATE guild.guest_requests SET last_nudged_at=NOW() WHERE id = ANY(%s)",
                    [ids]
                )
    except Exception as e:
        log.error("guest_nudge_check error: %s", e)
        return {"nudged": 0, "error": str(e)}

    count = len(names)
    if count == 1:
        body = f"  • {names[0]}"
    else:
        body = "\n".join(f"  • {n}" for n in names)

    msg = (
        f"🔔 <b>Guest access — {count} pending request{'s' if count > 1 else ''}</b>\n"
        f"{body}\n"
        f"Review at /guild"
    )
    from utils.role import is_production
    if not is_production():
        log.info("guest_nudge_check: standby — Telegram suppressed")
    else:
        try:
            from utils.telegram import get_cos_token, get_chat_id as _get_chat_id
            token   = get_cos_token()
            chat_id = _get_chat_id() or os.environ.get("TELEGRAM_CHAT_ID", "8379221702")
            if token:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=5,
                )
        except Exception:
            pass

    log.info("guest_nudge_check: %d request(s) nudged", count)
    return {"nudged": count}


# ── EC2 Health Monitor (loop_h) ───────────────────────────────────────────────

_EXPECTED_CONTAINERS = [
    "postgres-ai-agents",
    "minimoi-curator",
    "minimoi-german",
    "minimoi-portuguese",
    "minimoi-portal",
    "minimoi-system-bot",
    "minimoi-cos-bot",
]

_HEALTH_ENDPOINTS = [
    ("curator",    "http://minimoi-curator:8766/health"),
    ("german",     "http://minimoi-german:8767/health"),
    ("portal",     "http://minimoi-portal:5001/health"),
    ("portuguese", "http://minimoi-portuguese:8770/health"),
]

_ec2_alert_cooldown: dict = {}
_COOLDOWN_SECONDS = 3600  # 1 hour between repeat alerts for same condition


def _ec2_alert(token: str, chat_id: str, key: str, message: str):
    """Send alert only if not sent for this key within the cooldown window."""
    now = datetime.now(timezone.utc)
    last = _ec2_alert_cooldown.get(key)
    if last and (now - last).total_seconds() < _COOLDOWN_SECONDS:
        return
    _ec2_alert_cooldown[key] = now
    _tg_send(token, chat_id, message)


def _get_instance_id() -> str:
    try:
        r = requests.get("http://169.254.169.254/latest/meta-data/instance-id", timeout=2)
        return r.text.strip()
    except Exception:
        return ""


def _list_running_containers() -> set:
    """List running container names via Docker socket. No CLI binary required."""
    import http.client
    import socket as _socket

    class _UnixConn(http.client.HTTPConnection):
        def __init__(self, path):
            super().__init__("localhost")
            self._path = path
        def connect(self):
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.connect(self._path)
            self.sock = s

    conn = _UnixConn("/var/run/docker.sock")
    conn.request("GET", "/containers/json")
    resp = conn.getresponse()
    data = json.loads(resp.read())
    return {name.lstrip("/") for c in data for name in c.get("Names", [])}


def _check_ec2_health() -> dict:
    """
    Loop H — EC2 health check every 30 minutes. Production only.
    Detects problems and alerts Robert. Does not fix anything.
    Two-layer: subprocess (docker ps, df, free, curl) + boto3 CloudWatch (CPU).
    """
    from utils.role import is_production
    if not is_production():
        return {"skipped": "standby node"}

    try:
        from utils.telegram import get_cos_token, get_chat_id as _gc
        token   = get_cos_token()
        chat_id = _gc()
    except Exception as e:
        log.error("EC2 health: could not get telegram credentials: %s", e)
        return {"error": str(e)}

    if not token or not chat_id:
        return {"error": "missing token or chat_id"}

    issues = []

    # ── 1. Container check ────────────────────────────────────────────────────
    try:
        running = _list_running_containers()
        for name in _EXPECTED_CONTAINERS:
            if name not in running:
                svc = name.replace("minimoi-", "")
                _ec2_alert(token, chat_id, f"container_{name}",
                    f"⚠️ <b>EC2 Health Alert</b>\n"
                    f"Container <code>{name}</code> is not running.\n\n"
                    f"Diagnose: <code>docker logs {name} --tail 50</code>\n"
                    f"Restart:  <code>cd /opt/minimoi &amp;&amp; docker-compose -f docker-compose.prod.yml up -d --force-recreate {svc}</code>"
                )
                issues.append(f"container_down:{name}")
    except Exception as e:
        log.error("EC2 health: container check failed: %s", e)

    # ── 2. Disk check ─────────────────────────────────────────────────────────
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            pct = int(parts[4].rstrip("%")) if len(parts) >= 5 else 0
            if pct >= 80:
                _ec2_alert(token, chat_id, "disk_high",
                    f"⚠️ <b>EC2 Health Alert</b>\n"
                    f"Disk usage: <b>{pct}%</b> (threshold: 80%)\n"
                    f"Used: {parts[2]} of {parts[1]}\n\n"
                    f"Free space: <code>docker system prune -af</code>"
                )
                issues.append(f"disk:{pct}%")
    except Exception as e:
        log.error("EC2 health: df failed: %s", e)

    # ── 3. Memory check ───────────────────────────────────────────────────────
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, val = line.split(":", 1)
                meminfo[key.strip()] = int(val.strip().split()[0])  # kB
        total = meminfo.get("MemTotal", 0)
        avail = meminfo.get("MemAvailable", 0)
        used  = total - avail
        pct   = int(used / total * 100) if total > 0 else 0
        if pct >= 85:
            _ec2_alert(token, chat_id, "memory_high",
                f"⚠️ <b>EC2 Health Alert</b>\n"
                f"Memory usage: <b>{pct}%</b> (threshold: 85%)\n"
                f"Used: {used // 1024}MB / {total // 1024}MB — Available: {avail // 1024}MB\n\n"
                f"Check consumers: <code>docker stats --no-stream</code>"
            )
            issues.append(f"memory:{pct}%")
    except Exception as e:
        log.error("EC2 health: memory check failed: %s", e)

    # ── 4. /health endpoint check ─────────────────────────────────────────────
    for name, url in _HEALTH_ENDPOINTS:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code != 200:
                _ec2_alert(token, chat_id, f"health_{name}",
                    f"⚠️ <b>EC2 Health Alert</b>\n"
                    f"<b>{name}</b> /health returned {r.status_code}\n\n"
                    f"Diagnose: <code>docker logs minimoi-{name} --tail 50</code>\n"
                    f"Restart:  <code>cd /opt/minimoi &amp;&amp; docker-compose -f docker-compose.prod.yml up -d --force-recreate {name}</code>"
                )
                issues.append(f"health_{name}:{r.status_code}")
        except Exception as e:
            _ec2_alert(token, chat_id, f"health_{name}",
                f"⚠️ <b>EC2 Health Alert</b>\n"
                f"<b>{name}</b> /health unreachable\n\n"
                f"Diagnose: <code>docker logs minimoi-{name} --tail 50</code>\n"
                f"Restart:  <code>cd /opt/minimoi &amp;&amp; docker-compose -f docker-compose.prod.yml up -d --force-recreate {name}</code>"
            )
            issues.append(f"health_{name}:unreachable")

    # ── 5. CloudWatch CPU (best-effort, non-blocking) ─────────────────────────
    try:
        import boto3
        from datetime import timedelta
        cw  = boto3.client("cloudwatch", region_name="us-east-1")
        end = datetime.now(timezone.utc)
        iid = _get_instance_id()
        if iid:
            resp = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": iid}],
                StartTime=end - timedelta(minutes=10),
                EndTime=end,
                Period=300,
                Statistics=["Average"],
            )
            pts = resp.get("Datapoints", [])
            if pts:
                cpu = max(d["Average"] for d in pts)
                if cpu >= 90:
                    _ec2_alert(token, chat_id, "cpu_high",
                        f"⚠️ <b>EC2 Health Alert</b>\n"
                        f"CPU: <b>{cpu:.1f}%</b> 10-min avg (threshold: 90%)\n\n"
                        f"Check: <code>docker stats --no-stream</code>"
                    )
                    issues.append(f"cpu:{cpu:.0f}%")
    except Exception as e:
        log.debug("EC2 health: CloudWatch CPU skipped: %s", e)

    status = "ok" if not issues else f"issues: {', '.join(issues)}"
    log.info("EC2 health check complete: %s", status)
    return {"status": status, "issues": issues}


def _build_system_prompt() -> str:
    try:
        raw_context = json.loads(COS_CONTEXT_FILE.read_text())
        soul_enabled = raw_context.get("cos_soul_enabled", True)
        cos_context  = json.dumps(raw_context, indent=2)
    except Exception:
        soul_enabled = True
        cos_context  = "(context unavailable)"

    try:
        cos_soul = COS_SOUL_FILE.read_text().strip() if soul_enabled else ""
    except Exception:
        cos_soul = ""

    cos_memory = _read_memory()

    try:
        ops = requests.get("http://localhost:8768/status", timeout=3).json()
        ops_str = json.dumps(ops, indent=2)
    except Exception:
        ops_str = "Operations status unavailable"

    build_state = _read_build_state()
    date_str = datetime.now().strftime("%A, %B %d, %Y %H:%M")

    return _SYSTEM_PROMPT.format(
        cos_soul=cos_soul,
        cos_context=cos_context,
        cos_memory=cos_memory,
        ops_status=ops_str,
        build_state=build_state,
        date_str=date_str,
    )

# ── Tools ─────────────────────────────────────────────────────────────────────

# ── Backend ───────────────────────────────────────────────────────────────────
# Active backend instance — initialized in main(). Always GrokBackend unless
# explicitly swapped. Swapping does not change the coordination layer.
_backend = None


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

# ── Coordination layer: call_backend() ────────────────────────────────────────

def call_backend(prompt: str, context: dict, tool_policy: dict) -> str:
    """
    Coordination-layer dispatcher — the formal boundary from cos_interface.md v0.2.

    Assembles context (pre-built system prompt, recent memory) and delegates to
    the active backend. Tool dispatch and memory writes stay in this layer;
    the backend calls back through _dispatch_tool and _append_memory via the
    callables passed at GrokBackend.__init__.
    """
    if _backend is None:
        raise RuntimeError("Backend not initialized — init_backend() was not called in main()")
    return _backend.call_backend(prompt, context, tool_policy)


def _chat(text: str) -> str:
    """
    Coordination layer: build context and route to call_backend().
    Kept as the internal call site for the Flask /chat endpoint and Telegram handler.
    """
    context = {
        "recent_memory": _read_memory(),
        "system_prompt": _build_system_prompt(),
    }
    tool_policy = {"observation": True, "mutation": False}
    return call_backend(text, context, tool_policy)


def init_backend():
    """Initialize the active backend. Called once from main() at startup."""
    global _backend
    from domains.cos.backends.grok_backend import GrokBackend
    _backend = GrokBackend(write_memory=_append_memory, dispatch_tool=_dispatch_tool)
    log.info("Backend initialized: GrokBackend (grok-4-1-fast-reasoning)")

# ── Agent state ───────────────────────────────────────────────────────────────

_state = {
    "state":        "starting",
    "started_at":   datetime.now(timezone.utc).isoformat(),
    "chat_count":   0,
}
_state_lock = threading.Lock()

# ── Loop state (updated by each run) ─────────────────────────────────────────

_loop_state: dict[str, dict] = {
    "loop_a": {"name": "career_focus_scout",    "last_run": None, "last_result": None, "error": None},
    "loop_b": {"name": "german_watch",           "last_run": None, "last_result": None, "error": None},
    "loop_c": {"name": "curator_scout",          "last_run": None, "last_result": None, "error": None},
    "loop_d": {"name": "novelty_watch",          "last_run": None, "last_result": None, "error": None},
    "loop_f": {"name": "build_discipline_check", "last_run": None, "last_result": None, "error": None},
    "loop_g": {"name": "guest_nudge_check",      "last_run": None, "last_result": None, "error": None},
    "loop_h": {"name": "ec2_health_check",       "last_run": None, "last_result": None, "error": None},
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
        dev_query = text[4:].strip().lower()
        if dev_query in ("push-private", "push"):
            try:
                import subprocess as _sp
                sync_script = BASE_DIR / "scripts" / "sync_private_repo.sh"
                result = _sp.run(
                    ["bash", str(sync_script)],
                    capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR)
                )
                if result.returncode == 0:
                    _tg_send(token, chat_id, "📦 Private repo synced.")
                else:
                    _tg_send(token, chat_id, f"❌ Sync failed:\n{result.stderr[:200]}")
            except Exception as e:
                _tg_send(token, chat_id, f"❌ Sync error: {e}")
            return
        try:
            d = requests.get("http://localhost:8771/status", timeout=5).json()
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
            _tg_send(token, chat_id, "❌ Design/Dev agent unreachable (port 8771).")
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
    """Long-poll system bot for incoming messages. Runs in background thread."""
    from utils.telegram import get_system_token, get_chat_id as _get_chat_id
    token = get_system_token()
    chat_id = _get_chat_id() or os.environ.get("TELEGRAM_CHAT_ID")

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
    snap["backend_label"] = getattr(_backend, "backend_label", "unknown") if _backend else "uninitialized"
    snap["model_label"]   = getattr(_backend, "model_label",   "unknown") if _backend else ""
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


# ── Web UI data helpers ───────────────────────────────────────────────────────

def _parse_memory_for_feed() -> list:
    """Parse cos_memory.md into structured entries for the Feed tab."""
    mem = _read_memory()
    entries = []
    current_section = "General"
    for line in mem.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip()
            continue
        if stripped.startswith("#"):
            continue
        # Tagged entries: [chat] YYYY-MM-DD: content  or  [action] YYYY-MM-DD: content
        if stripped.startswith("[") and "] " in stripped:
            close = stripped.index("]")
            tag = stripped[1:close]
            rest = stripped[close + 2:]
            if ": " in rest:
                date_part, content = rest.split(": ", 1)
            else:
                date_part, content = "", rest
            entries.append({
                "type": tag,
                "date": date_part.strip(),
                "content": content.strip(),
                "section": current_section,
            })
    return sorted(entries, key=lambda x: x.get("date", ""), reverse=True)


def _get_agenda_pending() -> list:
    """Fetch pending cos_agenda items from DB (fallback: file)."""
    if _DB_AVAILABLE:
        try:
            with _db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT domain, description, confidence, "
                        "created_at::text AS created_at FROM guild.cos_agenda "
                        "WHERE status='pending' ORDER BY created_at DESC LIMIT 25"
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.warning("agenda_pending DB error: %s", e)
    # File fallback
    try:
        items = json.loads(AGENDA_FILE.read_text()) if AGENDA_FILE.exists() else []
        return [i for i in items if i.get("status") == "pending"][:25]
    except Exception:
        return []


def _get_agent_log_listing() -> list:
    """List recent files across agent_logs/ subdirectories."""
    logs_dir = BASE_DIR / "agent_logs"
    if not logs_dir.exists():
        return []
    files = []
    for subdir in sorted(logs_dir.iterdir()):
        if not subdir.is_dir():
            continue
        for f in sorted(subdir.iterdir(), reverse=True)[:5]:
            if f.is_file():
                files.append({
                    "dir": subdir.name,
                    "name": f.name,
                    "path": str(f.relative_to(BASE_DIR)),
                    "size": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
                })
    return sorted(files, key=lambda x: (x["mtime"], x["name"]), reverse=True)[:40]


# ── Web UI (v3: landing + four-tab SPA with sidebar photos) ───────────────────

_VALID_TABS = {"confer", "record", "track", "store"}

@app.route("/ui")
def ui():
    return render_template("cos_landing.html")


@app.route("/ui/<tab>")
def ui_tab(tab):
    if tab not in _VALID_TABS:
        return redirect("/ui")
    return render_template("cos_ui.html", initial_tab=tab)


@app.route("/ui/send", methods=["POST"])
def ui_send():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"reply": "Nothing to respond to."})
    try:
        reply = _chat(text)
        _inc_chat()
        return jsonify({"reply": reply})
    except Exception as e:
        _log_file("ui_send_error", str(e))
        return jsonify({"reply": f"CoS error: {e}"}), 500


@app.route("/ui/memory")
def ui_memory():
    mem = _read_memory()
    return render_template("cos_memory.html", memory=mem, memory_chars=len(mem))


@app.route("/ui/api/notes")
def ui_notes():
    return jsonify(_parse_memory_for_feed())


@app.route("/ui/api/todo")
def ui_todo():
    mem_entries = _parse_memory_for_feed()
    action_types = {"action", "task", "todo", "follow-up", "followup", "decision"}
    mem_actions = [e for e in mem_entries if e.get("type", "").lower() in action_types]
    agenda = _get_agenda_pending()
    return jsonify({"memory_actions": mem_actions, "agenda": agenda})


@app.route("/ui/api/repository")
def ui_repository():
    return jsonify(_get_agent_log_listing())


@app.route("/ui/transcribe", methods=["POST"])
def ui_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file"}), 400
    audio_file = request.files["audio"]
    try:
        from openai import OpenAI as _OAI
        from get_secret import get_secret
        api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY not configured"}), 500
        client = _OAI(api_key=api_key)
        audio_file.stream.seek(0)
        fname = audio_file.filename or "audio.webm"
        content_type = audio_file.content_type or "audio/webm"
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=(fname, audio_file.stream, content_type),
            response_format="text",
        )
        return jsonify({"transcript": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    from utils.role import role_label, is_production
    _role = role_label()
    print(f"[chief_of_staff] Starting as {_role} — "
          f"{'all loops + Telegram active' if is_production() else 'Telegram suppressed on standby'}")

    PORT = int(os.environ.get("PORT", 8769))

    print(f"""
💼  Chief of Staff starting on port {PORT}…
   /chat       — conversational endpoint (POST {{"text": "..."}})
   /status     — agent state
   /health     — liveness probe
   /loops      — intelligence loop status
   /event      — receive events from Guild agents (POST)
   /ui             — web UI (v2: all four tabs functional)
   /ui/send        — POST {{"text":"..."}} from browser
   /ui/memory      — read-only cos_memory.md dump
   /ui/feed        — parsed memory entries (JSON)
   /ui/todo        — action items + agenda pending (JSON)
   /ui/archive     — agent_logs listing (JSON)
   /ui/transcribe  — POST audio → Whisper transcript (JSON)
""")

    # 1. Check DB availability
    if _check_db():
        print("   DB: connected ✅")
    else:
        print("   DB: unavailable — queue_recommendation will use file fallback")

    # 2. Initialize backend (GrokBackend wrapping the Grok API call)
    try:
        init_backend()
        print("   Backend: GrokBackend initialized ✅")
    except Exception as e:
        print(f"   ⚠️  Backend init failed: {e} — chat will fail")

    # 3. Start Telegram polling thread (skipped when cos-bot container handles polling)
    if os.environ.get("DISABLE_TELEGRAM_POLL"):
        print("   Telegram poll: disabled (DISABLE_TELEGRAM_POLL set — cos-bot handles polling)")
    else:
        t = threading.Thread(target=_telegram_poll_loop, daemon=True, name="tg-poll")
        t.start()
        print("   Thread started: tg-poll")

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

    # 5. Start APScheduler
    scheduler = BackgroundScheduler()

    # Loop F — build discipline check (always available, no external imports)
    scheduler.add_job(
        lambda: _run_loop("loop_f", _run_build_discipline_check),
        "cron", hour=7, minute=30, id="loop_f", misfire_grace_time=600
    )
    # Loop G — guest access staleness nudge (hourly, always available)
    scheduler.add_job(
        lambda: _run_loop("loop_g", _run_guest_nudge_check),
        "interval", hours=1, id="loop_g", misfire_grace_time=300
    )
    # Loop H — EC2 health check every 30 min (is_production() guard inside)
    scheduler.add_job(
        lambda: _run_loop("loop_h", _check_ec2_health),
        "interval", minutes=30, id="loop_h", misfire_grace_time=300
    )

    if loops_ok:
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
        print("   Scheduler: loop_a(6+18h) loop_b(Sun 9h) loop_c(Sun 10h) loop_d(1st+15th 8h) loop_f(daily 7:30) loop_g(hourly) loop_h(30min) ✅")
    else:
        print("   Scheduler: loop_f(daily 7:30) loop_g(hourly) loop_h(30min) ✅ — loop_a/b/c/d disabled (import error)")

    scheduler.start()

    # 6. Mark running
    with _state_lock:
        _state["state"] = "running"
    print(f"   State: running\n")

    # 7. Serve
    app.run(host="0.0.0.0", port=PORT, debug=False)


if __name__ == "__main__":
    main()
