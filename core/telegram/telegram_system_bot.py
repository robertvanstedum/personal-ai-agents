#!/usr/bin/env python3
"""
telegram_system_bot.py — EC2 system bot polling handler.

Polls minimoi_system_bot token (from SSM on EC2, keyring on Mac).
Handles inbound commands for curator, ops status, and system health.
Outbound briefings and alerts are sent by curator_rss_v2.py and operations.py
using the same token — this process handles inbound only.
"""
import logging
import os
import platform
import socket
import sys
from pathlib import Path

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Ensure repo root is on path so utils.telegram imports resolve correctly
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_IN_DOCKER = os.path.exists('/.dockerenv')
_CURATOR_URL = "http://curator:8766" if _IN_DOCKER else "http://localhost:8766"
_GERMAN_URL  = "http://german:8767"  if _IN_DOCKER else "http://localhost:8767"
_PORTAL_URL  = "http://portal:5001"  if _IN_DOCKER else "http://localhost:5001"


def _get_token() -> str:
    from utils.telegram import get_system_token
    return get_system_token()


def _identity() -> str:
    host = socket.gethostname()
    role = os.environ.get("MINIMOI_ROLE", "production")
    system = platform.system()
    machine = platform.machine()
    if system == "Linux" and "ip-" in host:
        return f"EC2 ({host})"
    return f"{host} ({system}/{machine}, role={role})"


def _check_service(name: str, url: str) -> str:
    try:
        r = requests.get(url, timeout=4)
        return f"{name}: up ({r.status_code})"
    except Exception as e:
        return f"{name}: unreachable ({e})"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"minimoi system bot — {_identity()}\n\nCommands:\n/status — service health\n!ops — ops summary"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"<b>Status</b> — {_identity()}"]
    lines.append(_check_service("curator", _CURATOR_URL + "/"))
    lines.append(_check_service("german", _GERMAN_URL + "/"))
    lines.append(_check_service("portal", _PORTAL_URL + "/"))
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text.lower().startswith("!ops"):
        lines = [f"<b>Ops</b> — {_identity()}"]
        lines.append(_check_service("curator", _CURATOR_URL + "/"))
        lines.append(_check_service("german", _GERMAN_URL + "/"))
        lines.append(_check_service("portal", _PORTAL_URL + "/"))
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # Everything else is German-domain routing — drills, sessions, phrasebook,
    # natural-language and !german commands. Delegated verbatim to the shared
    # handler so behaviour matches the legacy minimoi_cmd_bot exactly.
    from core.telegram.telegram_bot import handle_text_message
    await handle_text_message(update, context)


def main():
    token = _get_token()
    if not token:
        log.error("No system bot token found — set TELEGRAM_SYSTEM_BOT_TOKEN or configure SSM /minimoi/production/telegram_system_bot_token")
        sys.exit(1)

    log.info(f"System bot starting on {_identity()}")

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    # Combined text handler: !ops first, then German routing.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # German voice notes, .txt transcript uploads, and drill-state restore.
    from core.telegram.telegram_bot import register_german_handlers
    register_german_handlers(app, include_text=False)

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
