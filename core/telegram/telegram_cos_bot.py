#!/usr/bin/env python3
"""
telegram_cos_bot.py — Chief of Staff Telegram polling handler.

Polls minimoi_cos_bot token (from SSM on EC2).
Calls _chat() from chief_of_staff directly — no separate cos-agent container needed.
Handles text and voice messages. Voice → OpenAI Whisper transcription → _chat().
"""
import functools
import logging
import os
import platform
import re
import socket
import sys
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (Application, ApplicationHandlerStop, CommandHandler, ContextTypes,
                          MessageHandler, TypeHandler, filters)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)
# httpx logs each request URL at INFO, and Telegram's URLs carry the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Ensure repo root is on path so chief_of_staff imports resolve correctly
_REPO_ROOT = Path(__file__).parent.parent.parent
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


def _chat(text: str, *, channel: str = "telegram_text") -> str:
    from domains.cos.chief_of_staff import _chat as cos_chat
    return cos_chat(text, channel=channel)


def _transcribe_voice(audio_path: str) -> str:
    """Transcribe a voice file via OpenAI Whisper API. Returns transcript text."""
    from openai import OpenAI
    from core.get_secret import get_secret
    try:
        api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
    except Exception as e:
        raise RuntimeError(f"OpenAI API key not found for Whisper transcription: {e}")
    client = OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )
    # response_format="text" returns a plain string directly
    return (result if isinstance(result, str) else getattr(result, "text", "")).strip()


# ── Owner-only access ─────────────────────────────────────────────────────────
# Telegram lets anyone who finds a bot message it, and has no allow-list of its
# own, so this bot checks every update itself: only the configured owner, in
# the owner's private chat, reaches CoS, the model or memory. A missing or
# malformed owner setting refuses everything (fail closed).

OWNER_KEY = "owner_chat_id"
_OWNER_ID = re.compile(r"[1-9][0-9]{0,19}")


def owner_chat_id(raw: str | None = None) -> int | None:
    """The owner's private chat id (TELEGRAM_CHAT_ID, then keyring), or None."""
    if raw is None:
        from utils.telegram import get_chat_id
        raw = get_chat_id()
    raw = (raw or "").strip()
    return int(raw) if _OWNER_ID.fullmatch(raw) else None


def _refusal(update, owner_id: int | None) -> str | None:
    """Why an update is refused, or None for the owner. Never the sender's id or text."""
    if owner_id is None:
        return "owner_not_configured"
    if getattr(update, "message", None) is None:
        return "not_a_message"          # edited, business, channel, callback and member updates are never served
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if user is None or chat is None:
        return "no_sender"
    if getattr(user, "is_bot", False):
        return "sender_is_bot"
    if getattr(chat, "type", None) != "private":
        return "not_private_chat"
    if user.id != owner_id or chat.id != owner_id:
        return "not_owner"
    return None


def is_owner_update(update, owner_id: int | None) -> bool:
    return _refusal(update, owner_id) is None


async def _gate(update, context) -> None:
    """Runs before every handler: stops each update that is not the owner's."""
    reason = _refusal(update, context.bot_data.get(OWNER_KEY))
    if reason is not None:
        log.warning("refused telegram update (%s)", reason)
        raise ApplicationHandlerStop


def _owner_only(handler):
    """The same check inside each handler, in case one is ever registered without the gate."""
    @functools.wraps(handler)
    async def wrapped(update, context):
        if not is_owner_update(update, context.bot_data.get(OWNER_KEY)):
            return None
        return await handler(update, context)
    return wrapped


@_owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Chief of Staff — {_identity()}\n\nSend any message to chat with CoS.\n/status — agent status"
    )


@_owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from domains.cos.chief_of_staff import _state, _state_lock, _read_memory
    with _state_lock:
        snap = dict(_state)
    mem = _read_memory()
    await update.message.reply_text(
        f"<b>CoS status</b> — {_identity()}\n"
        f"Memory: {len(mem)} chars",
        parse_mode="HTML",
    )


@_owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = _chat(text, channel="telegram_text")
    except Exception as e:
        log.exception("_chat error")
        reply = f"CoS error: {e}"

    await update.message.reply_text(reply)


@_owner_only
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice note via Whisper API, then route to CoS chat."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    voice = update.message.voice
    tmp_path = None
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        transcript = _transcribe_voice(tmp_path)
        if not transcript:
            await update.message.reply_text("Voice note received but transcription was empty.")
            return

        log.info("Voice note transcribed (%ds, %d chars)", voice.duration, len(transcript))

        reply = _chat(transcript, channel="telegram_voice")
    except Exception as e:
        log.exception("voice handler error")
        reply = f"CoS error processing voice note: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    await update.message.reply_text(reply)


def main():
    token = _get_token()
    if not token:
        log.error("No CoS bot token — set TELEGRAM_COS_BOT_TOKEN or SSM /minimoi/production/telegram_cos_bot_token")
        sys.exit(1)

    owner_id = owner_chat_id()
    if owner_id is None:
        log.error("No owner chat id — set TELEGRAM_CHAT_ID to the owner's private chat id. "
                  "Refusing to start: this bot answers only its owner.")
        sys.exit(1)

    log.info(f"CoS bot starting on {_identity()}")
    app = build_application(token, owner_id)
    log.info("Polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=[Update.MESSAGE])


def build_application(token: str, owner_id: int) -> Application:
    """The bot with its owner gate registered ahead of every handler."""
    app = Application.builder().token(token).build()
    app.bot_data[OWNER_KEY] = owner_id
    app.add_handler(TypeHandler(Update, _gate), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    return app


if __name__ == "__main__":
    main()
