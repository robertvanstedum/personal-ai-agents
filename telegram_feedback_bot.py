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

# Track processed callbacks to prevent duplicates
processed_callbacks = set()  # stores "message_id:action:rank"

def get_telegram_token():
    """Get Telegram bot token from keychain or env"""
    try:
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    
    return os.environ.get('TELEGRAM_BOT_TOKEN')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
    query = update.callback_query
    
    # Parse callback data: "action:rank" (e.g., "like:3")
    try:
        action, rank = query.data.split(':')
    except ValueError:
        await query.answer("❌ Invalid button data", show_alert=True)
        return
    
    # Check if already processed (prevent double-clicks)
    callback_id = f"{query.message.message_id}:{action}:{rank}"
    if callback_id in processed_callbacks:
        await query.answer("Already recorded!", show_alert=True)
        return
    
    # Mark as being processed
    processed_callbacks.add(callback_id)
    
    # Immediate acknowledgment to user
    await query.answer(f"⏳ Recording {action}...")
    
    print(f"\n📥 Telegram feedback: {action} for article #{rank}")
    
    # Call curator_feedback.py
    result = record_feedback(action, rank)
    
    # Update the message to show feedback was recorded
    if result['success']:
        # Edit message to add confirmation
        original_text = query.message.text
        confirmation = f"\n\n✅ {action.capitalize()}d!"
        
        # Remove old confirmation if exists
        if "✅" in original_text:
            original_text = original_text.split('\n✅')[0]
        
        await query.edit_message_text(
            text=original_text + confirmation,
            reply_markup=query.message.reply_markup
        )
        print(f"✅ Feedback recorded successfully")
    else:
        await query.answer(f"❌ Error: {result['message']}", show_alert=True)
        print(f"❌ Error: {result['message']}")

def record_feedback(action, rank):
    """Call curator_feedback.py to record feedback"""
    try:
        # Build command
        if action == 'like':
            cmd = ['python', 'curator_feedback.py', 'like', rank]
        elif action == 'dislike':
            cmd = ['python', 'curator_feedback.py', 'dislike', rank]
        elif action == 'save':
            cmd = ['python', 'curator_feedback.py', 'save', rank]
        else:
            return {'success': False, 'message': f'Unknown action: {action}'}
        
        # Use venv python
        venv_python = Path(__file__).parent / 'venv' / 'bin' / 'python'
        if venv_python.exists():
            cmd[0] = str(venv_python)
        
        # Execute with auto-response
        reason = f"{action}d from Telegram"
        result = subprocess.run(
            cmd,
            input=reason.encode(),
            capture_output=True,
            cwd=Path(__file__).parent,
            timeout=30
        )
        
        if result.returncode == 0:
            return {'success': True, 'message': f'Article #{rank} {action}d'}
        else:
            error = result.stderr.decode()
            return {'success': False, 'message': error[:100]}
    
    except Exception as e:
        return {'success': False, 'message': str(e)}

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
