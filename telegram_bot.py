#!/usr/bin/env python3
"""
telegram_bot.py - Unified Telegram bot

Handles:
- Sending daily briefing (called by run_curator_cron.sh)
- Listening for Like/Dislike/Save button callbacks
- Accepting commands: /run, /status, /briefing
"""

import os
import json
import subprocess
import keyring
import requests
import argparse
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).parent
processed_callbacks = set()

# ─── Token helpers ────────────────────────────────────────────────────────────

def get_token():
    try:
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')

def get_chat_id():
    return os.environ.get('TELEGRAM_CHAT_ID')

# ─── Sending ──────────────────────────────────────────────────────────────────

def send_message(token, chat_id, text, parse_mode="HTML"):
    """Simple fire-and-forget message send"""
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True},
        timeout=10
    )

def send_article(token, chat_id, num, title, url, source, category, score):
    """Send one article with interactive buttons"""
    message = (
        f"<b>#{num}</b> • {category.upper()} • {source}\n\n"
        f"<b>{title}</b>\n\n"
        f"<a href='{url}'>🔗 Read article</a>\n\n"
        f"Score: {score}"
    )
    
    keyboard = {
        "inline_keyboard": [[
            {"text": "👍 Like", "callback_data": f"like:{num}"},
            {"text": "👎 Dislike", "callback_data": f"dislike:{num}"},
            {"text": "🔖 Save", "callback_data": f"save:{num}"},
        ]]
    }
    
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message.strip(),
            "parse_mode": "HTML",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
        timeout=10
    )

def send_briefing(token, chat_id):
    """Read curator_output.txt and send top articles"""
    output_file = BASE_DIR / 'curator_output.txt'
    
    if not output_file.exists():
        send_message(token, chat_id, "❌ curator_output.txt not found — has the curator run today?")
        return
    
    send_message(token, chat_id, "🧠 <b>Your Morning Briefing</b>\n\nTop curated articles:")
    
    # Parse curator_output.txt
    # Adjust this parsing to match your actual file format
    articles = parse_curator_output(output_file)
    
    for a in articles[:10]:
        send_article(token, chat_id, a['num'], a['title'], a['url'], a['source'], a['category'], a['score'])
    
    send_message(token, chat_id, "✅ Briefing complete. Tap buttons to give feedback.")
    print(f"✅ Sent {len(articles[:10])} articles to Telegram")

def parse_curator_output(path):
    """
    Parse curator_output.txt into list of article dicts.
    Format:
    #N [Source] 🏷️  category (model)
       Title
       URL
       Published: ...
       Scores: X/10 (raw: X, final: X)
       snippet...
    """
    import re
    
    articles = []
    content = path.read_text()
    
    # Split on article markers (#1, #2, etc.)
    sections = re.split(r'\n#(\d+) ', content)
    
    for i in range(1, len(sections), 2):
        num = sections[i]
        article_text = sections[i+1]
        
        lines = article_text.strip().split('\n')
        if len(lines) < 4:
            continue
        
        # Parse header line: [Source] 🏷️  category (model)
        header = lines[0]
        source_match = re.search(r'\[(.*?)\]', header)
        category_match = re.search(r'🏷️\s+(\w+)', header)
        
        source = source_match.group(1) if source_match else "Unknown"
        category = category_match.group(1) if category_match else "other"
        
        # Parse title (line 1, indented)
        title = lines[1].strip()
        
        # Parse URL (line 2)
        url = lines[2].strip()
        
        # Parse score (line 4 usually)
        score = "?"
        for line in lines:
            if 'Scores:' in line:
                score_match = re.search(r'Scores:\s+([\d.]+)/10', line)
                if score_match:
                    score = score_match.group(1)
                break
        
        articles.append({
            'num': num,
            'title': title,
            'url': url,
            'source': source,
            'category': category,
            'score': score
        })
    
    return articles

# ─── Feedback ─────────────────────────────────────────────────────────────────

def parse_article_from_message(message_text, rank):
    """Extract article metadata from Telegram message text"""
    import re
    
    lines = message_text.split('\n')
    
    # Line 0: #N • CATEGORY • Source
    header = lines[0] if lines else ""
    category_match = re.search(r'• ([A-Z]+) •', header)
    source_match = re.search(r'• ([^•]+)$', header)
    
    category = category_match.group(1).lower() if category_match else "other"
    source = source_match.group(1).strip() if source_match else "Unknown"
    
    # Line 2: Title (bold in HTML)
    title = re.sub(r'<[^>]+>', '', lines[2]) if len(lines) > 2 else "Unknown"
    
    # Line 4: URL in anchor tag
    url = "unknown"
    for line in lines:
        if 'href=' in line:
            url_match = re.search(r"href='([^']+)'", line)
            if url_match:
                url = url_match.group(1)
                break
    
    return {
        'id': f'telegram-{rank}',
        'title': title,
        'link': url,
        'source': source,
        'category': category
    }

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Like/Dislike/Save button presses"""
    query = update.callback_query
    
    try:
        action, rank = query.data.split(':')
    except ValueError:
        await query.answer("❌ Invalid button data", show_alert=True)
        return
    
    callback_id = f"{query.message.message_id}:{action}:{rank}"
    if callback_id in processed_callbacks:
        await query.answer("Already recorded!", show_alert=True)
        return
    
    processed_callbacks.add(callback_id)
    await query.answer(f"⏳ Recording {action}...")
    
    # Parse article data from message text
    article_data = parse_article_from_message(query.message.text, rank)
    result = record_feedback(action, rank, article_data)
    
    if result['success']:
        original = query.message.text
        if "✅" in original:
            original = original.split('\n✅')[0]
        await query.edit_message_text(
            text=original + f"\n\n✅ {action.capitalize()}d!",
            reply_markup=query.message.reply_markup
        )
    else:
        await query.answer(f"❌ {result['message']}", show_alert=True)

def record_feedback(action, rank, article_data):
    """Call curator_feedback.py in workspace with article data"""
    workspace = Path.home() / '.openclaw' / 'workspace'
    feedback_script = workspace / 'curator_feedback.py'
    
    if not feedback_script.exists():
        return {'success': False, 'message': f'curator_feedback.py not found at {feedback_script}'}
    
    # Prepare JSON payload with article data
    payload = {
        'article': article_data,
        'your_words': f'{action}d from Telegram'
    }
    
    try:
        result = subprocess.run(
            ['python3', str(feedback_script), action, str(rank), '--channel', 'telegram'],
            input=json.dumps(payload).encode(),
            capture_output=True,
            cwd=workspace,
            timeout=30
        )
        if result.returncode == 0:
            return {'success': True, 'message': f'Article #{rank} {action}d'}
        return {'success': False, 'message': result.stderr.decode()[:100]}
    except Exception as e:
        return {'success': False, 'message': str(e)}

# ─── Commands ─────────────────────────────────────────────────────────────────

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /run — trigger the curator manually"""
    await update.message.reply_text("⏳ Running curator now, this takes a few minutes...")
    
    result = subprocess.run(
        [str(BASE_DIR / 'run_curator_cron.sh')],
        capture_output=True,
        cwd=BASE_DIR,
        timeout=600
    )
    
    if result.returncode == 0:
        await update.message.reply_text("✅ Curator run complete. Sending briefing...")
        token = get_token()
        chat_id = get_chat_id() or str(update.message.chat_id)
        send_briefing(token, chat_id)
    else:
        await update.message.reply_text(f"❌ Curator failed:\n{result.stderr.decode()[:300]}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show last run info"""
    log = BASE_DIR / 'logs' / 'curator_launchd.log'
    if log.exists():
        lines = log.read_text().splitlines()
        last_lines = '\n'.join(lines[-10:])
        await update.message.reply_text(f"📊 Last 10 log lines:\n<pre>{last_lines}</pre>", parse_mode="HTML")
    else:
        await update.message.reply_text("No log file found yet.")

async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /briefing — resend today's briefing"""
    token = get_token()
    chat_id = get_chat_id() or str(update.message.chat_id)
    send_briefing(token, chat_id)

# ─── Entry points ─────────────────────────────────────────────────────────────

def run_send_mode():
    """Called by cron/launchd: just send the briefing and exit"""
    token = get_token()
    chat_id = get_chat_id()
    
    if not token or not chat_id:
        print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return
    
    send_briefing(token, chat_id)

def run_bot_mode():
    """Run persistent bot for button callbacks and commands"""
    token = get_token()
    
    if not token:
        print("❌ No Telegram token found")
        return
    
    print("🤖 Unified Telegram bot starting...")
    app = Application.builder().token(token).build()
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    
    print("✅ Listening for callbacks and commands...")
    app.run_polling()

def get_webhook_secret():
    """Get webhook secret token from keyring (set at registration time)."""
    try:
        return keyring.get_password('telegram', 'webhook_secret')
    except Exception:
        return None


def run_webhook_mode():
    """Run Flask webhook server on localhost:8444"""
    from flask import Flask, request, jsonify

    token = get_token()
    if not token:
        print("❌ No Telegram token found")
        return

    webhook_secret = get_webhook_secret()
    if webhook_secret:
        print("🔒 Webhook secret validation enabled")
    else:
        print("⚠️  No webhook secret found — requests will not be validated")

    app = Flask(__name__)

    @app.route('/webhook', methods=['POST'])
    def webhook():
        """Handle incoming webhook updates from Telegram"""
        # Validate secret token if one is configured
        if webhook_secret:
            incoming = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            if incoming != webhook_secret:
                print(f"⚠️  Rejected webhook request with bad secret")
                return jsonify({'error': 'Unauthorized'}), 403

        try:
            update_json = request.get_json()

            # Handle callback_query (button presses)
            if 'callback_query' in update_json:
                handle_webhook_callback(update_json['callback_query'], token)
                return jsonify({'ok': True})

            # Handle commands (future)
            if 'message' in update_json:
                msg = update_json['message']
                if 'text' in msg and msg['text'].startswith('/'):
                    handle_webhook_command(msg, token)
                return jsonify({'ok': True})

            return jsonify({'ok': True})
        except Exception as e:
            print(f"❌ Webhook error: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/health', methods=['GET'])
    def health():
        """Health check endpoint"""
        return jsonify({'status': 'ok', 'mode': 'webhook'})
    
    print("🌐 Starting webhook server on http://localhost:8444")
    print("📡 Waiting for Telegram updates...")
    app.run(host='0.0.0.0', port=8444, debug=False)

def handle_webhook_callback(callback_query, token):
    """Process button callback from webhook"""
    query_id = callback_query['id']
    data = callback_query['data']
    message = callback_query['message']
    
    try:
        action, rank = data.split(':')
    except ValueError:
        answer_callback(token, query_id, "❌ Invalid button data", alert=True)
        return
    
    callback_id = f"{message['message_id']}:{action}:{rank}"
    if callback_id in processed_callbacks:
        answer_callback(token, query_id, "Already recorded!", alert=True)
        return
    
    processed_callbacks.add(callback_id)
    answer_callback(token, query_id, f"⏳ Recording {action}...")
    
    # Parse article data from message text
    article_data = parse_article_from_message(message['text'], rank)
    result = record_feedback(action, rank, article_data)
    
    if result['success']:
        original = message['text']
        if "✅" in original:
            original = original.split('\n✅')[0]
        
        edit_message(token, message['chat']['id'], message['message_id'], 
                    original + f"\n\n✅ {action.capitalize()}d!")
    else:
        answer_callback(token, query_id, f"❌ {result['message']}", alert=True)

def handle_webhook_command(message, token):
    """Process commands from webhook"""
    chat_id = message['chat']['id']
    text = message['text']
    
    if text == '/briefing':
        send_briefing(token, str(chat_id))
    elif text == '/status':
        log = BASE_DIR / 'logs' / 'curator_launchd.log'
        if log.exists():
            lines = log.read_text().splitlines()
            last_lines = '\n'.join(lines[-10:])
            send_message(token, str(chat_id), f"📊 Last 10 log lines:\n<pre>{last_lines}</pre>", parse_mode="HTML")
        else:
            send_message(token, str(chat_id), "No log file found yet.")
    elif text == '/run':
        send_message(token, str(chat_id), "⏳ Running curator now, this takes a few minutes...")
        result = subprocess.run(
            [str(BASE_DIR / 'run_curator_cron.sh')],
            capture_output=True,
            cwd=BASE_DIR,
            timeout=600
        )
        if result.returncode == 0:
            send_message(token, str(chat_id), "✅ Curator run complete. Sending briefing...")
            send_briefing(token, str(chat_id))
        else:
            send_message(token, str(chat_id), f"❌ Curator failed:\n{result.stderr.decode()[:300]}")

def answer_callback(token, query_id, text, alert=False):
    """Send answerCallbackQuery to Telegram"""
    requests.post(
        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
        json={"callback_query_id": query_id, "text": text, "show_alert": alert},
        timeout=5
    )

def edit_message(token, chat_id, message_id, text):
    """Edit an existing message"""
    requests.post(
        f"https://api.telegram.org/bot{token}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=5
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--send', action='store_true', help='Send briefing and exit (for cron/launchd)')
    parser.add_argument('--webhook', action='store_true', help='Run webhook server on port 8444')
    args = parser.parse_args()
    
    if args.send:
        run_send_mode()
    elif args.webhook:
        run_webhook_mode()
    else:
        run_bot_mode()
