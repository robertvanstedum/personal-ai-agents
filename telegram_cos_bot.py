#!/usr/bin/env python3
"""
telegram_cos_bot.py — Chief of Staff Telegram polling handler.

Polls minimoi_cos_bot token (from SSM on EC2).
Forwards messages to the CoS agent /chat endpoint and replies with the response.
The CoS agent runs as a separate container (cos-agent) on the same Docker network.
"""
import logging
import os
import platform
import socket
import sys

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

COS_AGENT_URL = os.environ.get("COS_AGENT_URL", "http://cos-agent:8769")


def _get_token() -> str:
    from utils.telegram import get_cos_token
    return get_cos_token()


def _get_chat_id() -> str:
    from utils.telegram import get_chat_id
    return get_chat_id() or os.environ.get("TELEGRAM_CHAT_ID", "")


def _identity() -> str:
    host = socket.gethostname()
    system = platform.system()
    if system == "Linux" and "ip-" in host:
        return f"EC2 ({host})"
    return f"{host} ({system}/{platform.machine()})"


def _chat(text: str) -> str:
    try:
        r = requests.post(
            f"{COS_AGENT_URL}/chat",
            json={"text": text},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("reply", "(no reply)")
    except Exception as e:
        return f"CoS agent unreachable: {e}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    identity = _identity()
    try:
        r = requests.get(f"{COS_AGENT_URL}/health", timeout=4)
        agent_status = "online" if r.ok else "degraded"
    except Exception:
        agent_status = "unreachable"
    await update.message.reply_text(
        f"Chief of Staff — {identity}\nAgent: {agent_status}\n\nSend any message to chat with CoS."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        r = requests.get(f"{COS_AGENT_URL}/status", timeout=5)
        data = r.json()
        uptime = data.get("uptime_seconds", 0)
        chats = data.get("chats_today", 0)
        mem = data.get("memory_chars", 0)
        reply = (
            f"<b>CoS status</b> — {_identity()}\n"
            f"Uptime: {uptime // 3600}h {(uptime % 3600) // 60}m\n"
            f"Chats today: {chats}\n"
            f"Memory: {mem} chars"
        )
    except Exception as e:
        reply = f"CoS agent unreachable: {e}"
    await update.message.reply_text(reply, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    # Stream a typing indicator while CoS thinks
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    reply = _chat(text)
    await update.message.reply_text(reply)


def main():
    token = _get_token()
    if not token:
        log.error("No CoS bot token found — set TELEGRAM_COS_BOT_TOKEN or configure SSM /minimoi/production/telegram_cos_bot_token")
        sys.exit(1)

    log.info(f"CoS bot starting on {_identity()} → {COS_AGENT_URL}")

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
