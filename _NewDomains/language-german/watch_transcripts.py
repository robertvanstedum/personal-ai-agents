#!/usr/bin/env python3
"""
watch_transcripts.py — Poll Dropbox inbox for German session transcripts and run pipeline.

Usage:
  cd _NewDomains/language-german
  python3 watch_transcripts.py

Stop:
  pkill -f watch_transcripts

Check running:
  pgrep -fl watch_transcripts
"""
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent.parent

# ─── Config ──────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    cfg_path = HERE / "language/german/config/sync_config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))

CFG = _load_config()

BASE_PATH      = Path(CFG["base_path"]).expanduser()
INBOX          = BASE_PATH / CFG["transcripts_inbox_dir"]
PROCESSED      = BASE_PATH / CFG["transcripts_processed_dir"]
PROMPTS        = BASE_PATH / CFG["prompts_dir"]
LOGS           = BASE_PATH / "logs"
LOG_FILE       = LOGS / "watcher.log"
POLL_INTERVAL  = CFG["poll_interval_seconds"]
EXTENSIONS     = set(CFG["supported_extensions"])
PIPELINE_BASE  = CFG["pipeline_base_dir"]  # relative to project root
VENV_PYTHON    = PROJECT_ROOT / "venv/bin/python3"

# ─── Logging ─────────────────────────────────────────────────────────────────

def _setup_logging():
    LOGS.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stdout)],
    )

import logging.handlers  # import here so the function above can reference it

# ─── Telegram ────────────────────────────────────────────────────────────────

def _tg_send(text: str) -> None:
    try:
        import keyring, requests as req
        token   = keyring.get_password("telegram", "polling_bot_token")
        chat_id = keyring.get_password("telegram", "chat_id")
        if not token or not chat_id:
            logging.warning("Telegram credentials missing — skipping notification")
            return
        req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception as e:
        logging.warning(f"Telegram send failed: {e}")

def _tg_enabled(key: str) -> bool:
    return CFG.get(key, True)

# ─── File stability ───────────────────────────────────────────────────────────

def _is_stable(path: Path, checks: int = 3, interval: float = 1.0) -> bool:
    sizes = []
    for _ in range(checks):
        try:
            sizes.append(path.stat().st_size)
        except FileNotFoundError:
            return False
        time.sleep(interval)
    return len(set(sizes)) == 1 and sizes[0] > 0

# ─── Pipeline ─────────────────────────────────────────────────────────────────

def _run(cmd: list, cwd: Path) -> tuple[str, str, int]:
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(cwd)
    )
    return result.stdout, result.stderr, result.returncode


def _process(path: Path) -> None:
    name = path.name
    logging.info(f"Processing: {name}")
    if _tg_enabled("send_telegram_on_start"):
        _tg_send(f"📥 Transcript received — parsing {name}...")

    cwd = HERE

    # Step 1 — parse
    out, err, rc = _run(
        [str(VENV_PYTHON), "parse_transcript.py",
         "--input", str(path), "--base-dir", "language/german/"],
        cwd=cwd,
    )
    if rc != 0:
        _fail(name, "parse_transcript.py", err or out)
        return
    logging.info(f"  parse OK: {out.strip()}")

    # Step 2 — review
    out, err, rc = _run(
        [str(VENV_PYTHON), "reviewer.py", "--latest", "--base-dir", "language/german/"],
        cwd=cwd,
    )
    if rc != 0:
        _fail(name, "reviewer.py", err or out)
        return
    logging.info(f"  review OK")

    # Step 3 — Anki import (graceful degradation)
    out, err, rc = _run([str(VENV_PYTHON), "import_cards.py"], cwd=cwd)
    if rc != 0:
        logging.warning(f"  import_cards.py unavailable (AnkiConnect?): {err.strip()}")
    else:
        logging.info(f"  anki OK: {out.strip()}")

    # Step 4 — check drill mode (read latest session JSON)
    sessions_dir = HERE / "language/german/sessions"
    session_files = sorted(sessions_dir.glob("*.json"))
    drill_mode = False
    drill_session = 0
    drill_total = 1
    if session_files:
        try:
            sess = json.loads(session_files[-1].read_text(encoding="utf-8"))
            drill_mode    = sess.get("drill_mode", False)
            drill_session = sess.get("drill_session", 0)
            drill_total   = sess.get("drill_total", 1)
        except Exception:
            pass

    is_final_drill = drill_mode and drill_session >= drill_total

    # Step 5 — next session prompt (skip if mid-drill)
    if not drill_mode or is_final_drill:
        out, err, rc = _run(
            [str(VENV_PYTHON), "get_german_session.py",
             "--base-dir", "language/german/", "--dropbox", "--send"],
            cwd=cwd,
        )
        if rc != 0:
            _fail(name, "get_german_session.py", err or out)
            return
        logging.info(f"  session prompt written to Dropbox + sent to Telegram")
    else:
        logging.info(f"  drill session {drill_session}/{drill_total} — skipping prompt rotation")

    # Step 6 — move to processed
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = PROCESSED / f"{ts}_{name}"
    shutil.move(str(path), str(dest))
    logging.info(f"  moved to processed: {dest.name}")

    if _tg_enabled("send_telegram_on_success") and (not drill_mode or is_final_drill):
        _tg_send(f"✅ Pipeline complete. Next prompt in Dropbox.")
    elif drill_mode and not is_final_drill:
        _tg_send(f"✅ Drill session {drill_session}/{drill_total} processed. Keep going!")


def _fail(name: str, step: str, error: str) -> None:
    msg = f"❌ Pipeline failed at {step}: {name}\n{error[:400]}"
    logging.error(msg)
    if _tg_enabled("send_telegram_on_error"):
        _tg_send(msg)

# ─── Main loop ────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    for d in [INBOX, PROCESSED, PROMPTS, LOGS]:
        d.mkdir(parents=True, exist_ok=True)


def main() -> None:
    _setup_logging()
    _ensure_dirs()

    logging.info("German watcher starting...")
    if _tg_enabled("send_telegram_on_start"):
        _tg_send(f"🟢 German watcher active — polling ~/Dropbox/German_Sessions every {POLL_INTERVAL}s")

    seen: set[str] = set()

    while True:
        try:
            candidates = [
                INBOX / f for f in os.listdir(INBOX)
                if Path(f).suffix in EXTENSIONS and f not in seen
            ]
            for path in sorted(candidates):
                seen.add(path.name)
                if not _is_stable(path):
                    logging.info(f"Skipping unstable file: {path.name}")
                    seen.discard(path.name)  # retry next poll
                    continue
                try:
                    _process(path)
                except Exception as e:
                    _fail(path.name, "unexpected error", str(e))
        except Exception as e:
            logging.error(f"Poll loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
