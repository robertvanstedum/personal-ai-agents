#!/usr/bin/env python3
"""
telegram_feedback_bot.py - Telegram bot that handles feedback button callbacks

Usage:
    python telegram_feedback_bot.py
    
Runs continuously, listening for button clicks in Telegram briefings.
"""

import os
import json
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
import keyring

# Support the documented direct-script entrypoint as well as package imports.
import sys
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from core.telegram.owner_policy import owner_only
from utils.telegram import get_chat_id

from core.telegram.feedback_handlers import polling_feedback
from core.telegram.feedback_state import FeedbackTracker
from core.telegram.feedback_writer import write_feedback

processed_callbacks = FeedbackTracker()

def get_telegram_token():
    """Get Telegram bot token from keychain or env"""
    try:
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    
    return os.environ.get('TELEGRAM_BOT_TOKEN')

@owner_only(lambda: get_chat_id())
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await polling_feedback(update.callback_query, processed_callbacks,
                           lambda action, rank, url: record_feedback(action, rank, expected_url=url))


def record_feedback(action, rank, *, expected_url=None):
    """Compatibility adapter; old unbound calls refuse without writing."""
    return write_feedback(action, int(rank), expected_url, root=_REPO_ROOT)


def main():
    """Start the Telegram bot"""
    token = get_telegram_token()
    
    if not token:
        print("❌ No Telegram bot token found!")
        print("Set it in keychain: keyring set telegram bot_token")
        print("Or set TELEGRAM_BOT_TOKEN environment variable")
        return
    
    print("""
🤖 Telegram Feedback Bot Starting...

📱 Bot is listening for button clicks
👍 Users can tap Like/Dislike/Save on articles
💾 Feedback will be recorded automatically

Press Ctrl+C to stop
""")
    
    # Create application
    app = Application.builder().token(token).build()
    
    # Add callback handler for button presses
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    print("✅ Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
