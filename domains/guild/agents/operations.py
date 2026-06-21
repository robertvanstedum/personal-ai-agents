#!/usr/bin/env python3
"""
domains/guild/agents/operations.py — Operations Agent
Guild Phase 2

Persistent Flask service on port 8768 (com.user.operations launchd).
Two background threads:
  _health_loop  — every 5 min: service health, disk check, Tier 1 actions
  _audit_loop   — every hour: deeper audit, log age, cron history, escalations

Build order: memory helper → /status → loop logic
"""

import gzip
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, jsonify

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent.parent.parent.parent   # repo root
RULES_FILE = BASE_DIR / "domains/guild/config/ops_maintenance_rules.json"
MEMORY_FILE = BASE_DIR / "data/guild/memory/ops_memory.md"
LOGS_DIR   = BASE_DIR / "logs"

# ── Telegram (reuse telegram_bot.py from repo root) ───────────────────────────

sys.path.insert(0, str(BASE_DIR))
try:
    from telegram_bot import send_message as _tg_send, get_token as _tg_token, get_chat_id as _tg_chat_id
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False

def _send_telegram(text: str):
    """Fire-and-forget Telegram send. Only for Tier 4. Suppressed on standby."""
    from utils.role import is_production
    if not is_production():
        _log_file("telegram_suppressed_standby", f"standby — not sent: {text[:80]}")
        return
    if not _TELEGRAM_AVAILABLE:
        _log_file("telegram_unavailable", f"telegram_bot import failed — message not sent: {text[:80]}")
        return
    try:
        token   = _tg_token()
        chat_id = _tg_chat_id() or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            _log_file("telegram_missing_creds", "bot_token or TELEGRAM_CHAT_ID not found")
            return
        _tg_send(token, chat_id, text)
    except Exception as e:
        _log_file("telegram_error", str(e))

# ── Config ────────────────────────────────────────────────────────────────────

def _load_rules() -> dict:
    try:
        return json.loads(RULES_FILE.read_text())
    except Exception as e:
        _log_file("rules_load_error", str(e))
        return {}

RULES: dict = {}   # populated on startup

# ── Database ──────────────────────────────────────────────────────────────────

DSN = "postgresql://minimoi:simple123@localhost:5432/personal_agents"

@contextmanager
def _db():
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

def _db_log(action: str, tier: int, outcome: str, auto_resolved: bool = False):
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO guild.ops_maintenance_log
                       (tier, action, outcome, auto_resolved)
                       VALUES (%s, %s, %s, %s)""",
                    (tier, action, outcome, auto_resolved)
                )
    except Exception as e:
        _log_file("db_log_error", f"{action}: {e}")

def _db_update_state(state: str, last_action: dict = None):
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO guild.agent_state
                       (agent_name, current_state, last_checkin, current_agenda, updated_at)
                       VALUES ('operations', %s, NOW(), %s, NOW())
                       ON CONFLICT (agent_name)
                       DO UPDATE SET current_state = EXCLUDED.current_state,
                                     last_checkin  = EXCLUDED.last_checkin,
                                     current_agenda = EXCLUDED.current_agenda,
                                     updated_at     = EXCLUDED.updated_at""",
                    (state, json.dumps(last_action) if last_action else None)
                )
    except Exception as e:
        _log_file("db_state_error", str(e))

def _db_escalate(tier: int, description: str,
                 action_taken: str = None, options: str = None) -> int:
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO guild.ops_escalation_queue
                       (tier, description, action_taken, suggested_options)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (tier, description, action_taken, options)
                )
                return cur.fetchone()["id"]
    except Exception as e:
        _log_file("db_escalate_error", str(e))
        return -1

def _db_open_escalations() -> int:
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM guild.ops_escalation_queue WHERE status='pending'"
                )
                return cur.fetchone()["n"]
    except Exception:
        return 0

def _db_last_log(n: int = 20) -> list:
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, timestamp, tier, action, outcome, auto_resolved
                       FROM guild.ops_maintenance_log
                       ORDER BY timestamp DESC LIMIT %s""",
                    (n,)
                )
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        return []

# ── Agent state (in-memory for fast /status) ──────────────────────────────────

_state = {
    "state":           "starting",
    "started_at":      datetime.now(timezone.utc).isoformat(),
    "last_checkin":    None,
    "last_action":     None,
    "checks_run":      0,
    "actions_taken":   0,
}
_state_lock = threading.Lock()

def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)
        _state["last_checkin"] = datetime.now(timezone.utc).isoformat()

# ── File log (fallback when DB unavailable) ───────────────────────────────────

def _log_file(event: str, detail: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}: {detail}\n"
    try:
        (LOGS_DIR / "operations.log").open("a").write(line)
    except Exception:
        print(line, end="")

# ── Memory writer (FIRST: built before any loop logic) ────────────────────────

MEMORY_CAP = 7_500

def _ops_memory_write(entry: str) -> bool:
    """Append a dated entry to ops_memory.md.
    Refuses if file > MEMORY_CAP chars — distillation needed first."""
    try:
        current = MEMORY_FILE.read_text() if MEMORY_FILE.exists() else ""
        if len(current) > MEMORY_CAP:
            _db_log(
                "ops_memory_write_blocked", 2,
                f"File at {len(current)} chars — distillation needed before next write",
                False
            )
            return False
        dated = f"\n### {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n{entry}\n"
        MEMORY_FILE.write_text(current + dated)
        return True
    except Exception as e:
        _log_file("memory_write_error", str(e))
        return False

# ── Tier 1 actions ────────────────────────────────────────────────────────────

def _action_archive_screenshots() -> bool:
    """Archive screenshots older than 30 days not in current/ baselines."""
    cutoff = datetime.now() - timedelta(days=30)
    archived = 0
    try:
        screenshots_dir = BASE_DIR / "docs/screenshots"
        archive_base = screenshots_dir / "_compressed"
        archive_base.mkdir(exist_ok=True)
        for png in screenshots_dir.rglob("*.png"):
            if "current" in str(png):
                continue
            mtime = datetime.fromtimestamp(png.stat().st_mtime)
            if mtime < cutoff:
                dest = archive_base / png.name
                png.rename(dest)
                archived += 1
        msg = f"Archived {archived} screenshots older than 30 days"
        _db_log("archive_screenshots", 1, msg, auto_resolved=True)
        _log_file("archive_screenshots", msg)
        return True
    except Exception as e:
        _db_log("archive_screenshots_error", 1, str(e), False)
        return False

def _action_compress_old_logs() -> bool:
    """Gzip log files older than 60 days."""
    cutoff = datetime.now() - timedelta(days=60)
    compressed = 0
    try:
        for log_file in LOGS_DIR.glob("*.log"):
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff:
                gz_path = log_file.with_suffix(".log.gz")
                with open(log_file, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                log_file.unlink()
                compressed += 1
        msg = f"Compressed {compressed} log files older than 60 days"
        _db_log("compress_logs", 1, msg, auto_resolved=True)
        return True
    except Exception as e:
        _db_log("compress_logs_error", 1, str(e), False)
        return False

def _action_restart_service(label: str) -> bool:
    """Restart a launchd service once, wait 2 min, verify."""
    try:
        subprocess.run(["launchctl", "stop", label], timeout=10)
        time.sleep(2)
        subprocess.run(["launchctl", "start", label], timeout=10)
        time.sleep(120)  # wait 2 min
        # Verify by checking launchctl list
        result = subprocess.run(
            ["launchctl", "list", label], capture_output=True, text=True, timeout=5
        )
        success = result.returncode == 0
        msg = f"Restarted {label}: {'ok' if success else 'still failing'}"
        _db_log("restart_service", 1, msg, auto_resolved=success)
        return success
    except Exception as e:
        _db_log("restart_service_error", 1, f"{label}: {e}", False)
        return False

# ── Service health checks ─────────────────────────────────────────────────────

WATCHED_SERVICES = [
    ("com.user.curator-server",          "http://localhost:8766/"),
    ("com.vanstedum.german-html-server", "http://localhost:8767/"),
    ("com.vanstedum.minimoi-portal",     "http://localhost:5001/"),
]

# Track outage start times
_outage_start: dict = {}

def _check_services() -> list:
    """HTTP-check each watched service. Returns list of (label, ok, latency_ms)."""
    results = []
    threshold = RULES.get("service_down_threshold_minutes", 15)
    for label, url in WATCHED_SERVICES:
        ok, latency = False, None
        try:
            t0 = time.time()
            r  = requests.get(url, timeout=5)
            ok = r.status_code < 500
            latency = int((time.time() - t0) * 1000)
        except Exception:
            pass

        if ok:
            if label in _outage_start:
                del _outage_start[label]
        else:
            if label not in _outage_start:
                _outage_start[label] = datetime.now()
            outage_mins = (datetime.now() - _outage_start[label]).total_seconds() / 60
            if outage_mins >= threshold:
                msg = f"🚨 {label} down >{threshold} min — immediate attention needed"
                _db_escalate(4, msg)
                _send_telegram(f"<b>TIER 4 — Operations</b>\n{msg}")
                _db_log(f"tier4_{label}", 4, f"Service down {outage_mins:.0f} min", False)
            elif outage_mins >= 1:
                _db_escalate(3, f"{label} not responding for {outage_mins:.0f} min",
                             "waiting for threshold")

        results.append((label, ok, latency))
    return results

def _check_disk() -> dict:
    """Check disk usage on the data volume. Apply Tier 1/2/4 based on thresholds."""
    try:
        usage = shutil.disk_usage("/")
        pct   = (usage.used / usage.total) * 100
        free_gb = usage.free / (1024 ** 3)

        if pct > 95:
            msg = f"🚨 Disk at {pct:.1f}% — {free_gb:.1f} GB remaining"
            _db_escalate(4, msg)
            _send_telegram(f"<b>TIER 4 — Operations</b>\n{msg}")
            _db_log("disk_tier4", 4, msg, False)
        elif pct > 85:
            msg = f"Disk at {pct:.1f}% — emergency cleanup needed"
            _db_escalate(2, msg, "monitoring")
            _db_log("disk_tier2", 2, msg, False)
        elif pct > 75:
            _action_archive_screenshots()

        return {"pct": round(pct, 1), "free_gb": round(free_gb, 1)}
    except Exception as e:
        _log_file("disk_check_error", str(e))
        return {"pct": None, "free_gb": None}

# ── DB schema fix: add unique constraint on agent_name if missing ─────────────

def _ensure_agent_state_constraint():
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DO $$ BEGIN
                        ALTER TABLE guild.agent_state
                            ADD CONSTRAINT agent_state_name_unique UNIQUE (agent_name);
                    EXCEPTION WHEN duplicate_table THEN NULL;
                    END $$;
                """)
    except Exception:
        pass

# ── Health loop (every 5 min) ─────────────────────────────────────────────────

_last_daily_summary = None

def _health_loop():
    global _last_daily_summary
    while True:
        try:
            svc_results = _check_services()
            disk        = _check_disk()
            healthy     = sum(1 for _, ok, _ in svc_results if ok)
            total       = len(svc_results)

            with _state_lock:
                _state["checks_run"] += 1
                _state["last_action"] = {
                    "type":      "health_check",
                    "at":        datetime.now(timezone.utc).isoformat(),
                    "services":  f"{healthy}/{total} healthy",
                    "disk_pct":  disk["pct"],
                }
            _db_update_state("running", _state["last_action"])

            # Daily summary to ops_memory.md
            today = datetime.now().date()
            if _last_daily_summary != today:
                _last_daily_summary = today
                svc_names = ", ".join(
                    f"{l.split('.')[-1]} {'✅' if ok else '❌'}"
                    for l, ok, _ in svc_results
                )
                entry = (
                    f"- Services: {svc_names}\n"
                    f"- Disk: {disk['pct']}% used, {disk['free_gb']} GB free\n"
                    f"- Checks run today: {_state['checks_run']}\n"
                    f"- Open escalations: {_db_open_escalations()}"
                )
                _ops_memory_write(entry)

        except Exception as e:
            _log_file("health_loop_error", str(e))

        time.sleep(300)  # 5 minutes

# ── Audit loop (every hour) ───────────────────────────────────────────────────

def _audit_loop():
    while True:
        time.sleep(3600)  # wait 1 hour before first run
        try:
            # Check log file ages
            _action_compress_old_logs()

            # Pattern detection: same service restarted 3+ times in last hour
            try:
                with _db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT action, count(*) AS n
                            FROM guild.ops_maintenance_log
                            WHERE timestamp > NOW() - INTERVAL '1 hour'
                              AND action LIKE 'restart_service%'
                            GROUP BY action HAVING count(*) >= 3
                        """)
                        for row in cur.fetchall():
                            _db_escalate(
                                3, f"Service restarted 3+ times in 1 hour: {row['action']}",
                                "pattern detected"
                            )
            except Exception:
                pass

        except Exception as e:
            _log_file("audit_loop_error", str(e))

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/status")
def status():
    """Primary endpoint — queried by CoS Phase 3 oversight loop."""
    with _state_lock:
        snap = dict(_state)
    snap["open_escalations"] = _db_open_escalations()
    snap["uptime_seconds"]   = int(
        (datetime.now(timezone.utc) -
         datetime.fromisoformat(snap["started_at"])).total_seconds()
    )
    return jsonify(snap)

@app.route("/log")
def log():
    """Last 20 maintenance log entries."""
    rows = _db_last_log(20)
    # Convert datetime objects to ISO strings
    for r in rows:
        if r.get("timestamp"):
            r["timestamp"] = r["timestamp"].isoformat()
    return jsonify(rows)

@app.route("/health")
def health():
    """Quick liveness check — returns 200 if process is alive."""
    return jsonify({"ok": True})

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global RULES

    PORT = int(os.environ.get("PORT", 8768))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [operations] %(levelname)s %(message)s"
    )

    from utils.role import role_label
    _role = role_label()
    print(f"[operations] Starting as {_role}")

    print(f"""
⚙️  Operations Agent starting on port {PORT}...
   /status  — agent state + open escalations
   /log     — last 20 maintenance actions
   /health  — liveness probe
""")

    # 1. Load rules
    RULES = _load_rules()
    print(f"   Rules loaded: {len(RULES)} tiers configured")

    # 2. Ensure DB schema constraint exists
    _ensure_agent_state_constraint()

    # 3. Write initial state to DB
    _db_update_state("starting")
    print("   DB: agent_state row created")

    # 4. Start health loop thread
    t1 = threading.Thread(target=_health_loop, daemon=True, name="health-loop")
    t1.start()
    print("   Thread started: health-loop (5-min interval)")

    # 5. Start audit loop thread
    t2 = threading.Thread(target=_audit_loop, daemon=True, name="audit-loop")
    t2.start()
    print("   Thread started: audit-loop (1-hour interval)")

    # 6. Update state to running
    _set_state(state="running")
    _db_update_state("running")
    print(f"   State: running — first health check in ~5s\n")

    # 7. Serve
    app.run(host="localhost", port=PORT, debug=False)


if __name__ == "__main__":
    main()
