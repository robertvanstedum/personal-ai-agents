#!/usr/bin/env python3
"""
telegram_cos_bot.py — Chief of Staff Telegram polling handler.

Polls minimoi_cos_bot token (from SSM on EC2).
Calls _chat() from chief_of_staff directly — no separate cos-agent container needed.
"""
import logging
import os
import platform
import socket
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Ensure repo root is on path so chief_of_staff imports resolve correctly
_REPO_ROOT = Path(__file__).parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _get_token() -> str:
    from utils.telegram import get_cos_token
    return get_cos_token()


def _identity() -> str:
    host = socket.gethostname()
    system = platform.system()
    if system == "Linux" and "ip-" in host:
        return f"EC2 ({host})"
    return f"{host} ({system}/{platform.machine()})"


def _chat(text: str) -> str:
    from domains.guild.agents.chief_of_staff import _chat as cos_chat
    return cos_chat(text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Chief of Staff — {_identity()}\n\nSend any message to chat with CoS.\n/status — agent status"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from domains.guild.agents.chief_of_staff import _state, _state_lock, _read_memory
    with _state_lock:
        snap = dict(_state)
    mem = _read_memory()
    await update.message.reply_text(
        f"<b>CoS status</b> — {_identity()}\n"
        f"Memory: {len(mem)} chars",
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = _chat(text)
    except Exception as e:
        log.exception("_chat error")
        reply = f"CoS error: {e}"

    await update.message.reply_text(reply)


def main():
    token = _get_token()
    if not token:
        log.error("No CoS bot token — set TELEGRAM_COS_BOT_TOKEN or SSM /minimoi/production/telegram_cos_bot_token")
        sys.exit(1)

    log.info(f"CoS bot starting on {_identity()}")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
