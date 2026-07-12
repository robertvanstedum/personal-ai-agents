#!/usr/bin/env python3
"""
telegram_cos_bot.py — Chief of Staff Telegram polling handler.

Polls minimoi_cos_bot token (from SSM on EC2).
Calls _chat() from chief_of_staff directly — no separate cos-agent container needed.
Handles text and voice messages. Voice → OpenAI Whisper transcription → _chat().
"""
import logging
import os
import platform
import socket
import sys
import tempfile
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
    from domains.cos.chief_of_staff import _chat as cos_chat
    return cos_chat(text)


def _transcribe_voice(audio_path: str) -> str:
    """Transcribe a voice file via OpenAI Whisper API. Returns transcript text."""
    from openai import OpenAI
    from get_secret import get_secret
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Chief of Staff — {_identity()}\n\nSend any message to chat with CoS.\n/status — agent status"
    )


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

        log.info("Voice note transcribed (%ds): %s", voice.duration, transcript[:80])

        # Route through CoS — prefix lets the memory judgment and system prompt
        # know this came from a voice note (relevant for action-item extraction)
        reply = _chat(f"[Voice note] {transcript}")
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

    log.info(f"CoS bot starting on {_identity()}")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
