#!/usr/bin/env python3
"""
telegram_bot.py - Unified Telegram bot

Handles:
- Sending daily briefing (called by run_curator_cron.sh)
- Listening for Like/Dislike/Save button callbacks
- Accepting commands: /run, /status, /briefing
"""

import os
import io
import re
import json
import threading
import subprocess
import keyring
import requests
import argparse
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BASE_DIR = Path(__file__).parent
processed_callbacks = set()

# ─── Voice command patterns ───────────────────────────────────────────────────
VOICE_COMMAND_PATTERNS = [
    (re.compile(r'resend (report|briefing)', re.I),                'resend_briefing', None),
    (re.compile(r'(check services|are services running|service status)', re.I), 'check_services', None),
    (re.compile(r'(run the briefing|run daily|run curator)', re.I),'run_curator',    None),
    (re.compile(r'dry run', re.I),                                 'dry_run',         None),
    (re.compile(r'investigate[:\s]+(.+)',         re.I),           'investigate',     1),
    (re.compile(r'add to roadmap[:\s]+(.+)',      re.I),           'add_roadmap',     1),
    (re.compile(r'delete from roadmap[:\s]+(.+)', re.I),           'delete_roadmap',  1),
]

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
    Current format:
    #N [Source] 🏷️  category (model)
       ID: xxxxx
       Title
       https://...
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

        lines = [l.strip() for l in article_text.strip().split('\n')]
        if len(lines) < 3:
            continue

        # Parse header line: [Source] 🏷️  category (model)
        header = lines[0]
        source_match = re.search(r'\[(.*?)\]', header)
        category_match = re.search(r'🏷️\s+(\w+)', header)

        source = source_match.group(1) if source_match else "Unknown"
        category = category_match.group(1) if category_match else "other"

        # Find URL by pattern (robust against added/removed lines above it)
        url = "unknown"
        for line in lines[1:]:
            if line.startswith('http://') or line.startswith('https://'):
                url = line
                break

        # Find title: first non-empty line that isn't the ID, a URL, Published, or Scores
        title = "Unknown"
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('ID:'):
                continue
            if stripped.startswith('http://') or stripped.startswith('https://'):
                continue
            if stripped.startswith('Published:') or stripped.startswith('Scores:'):
                continue
            title = stripped
            break

        # Parse score
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

# ─── Voice notes ──────────────────────────────────────────────────────────────

def get_openai_key():
    try:
        key = keyring.get_password("openai", "api_key")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get('OPENAI_API_KEY')

def get_telegram_file_bytes(file_id, token):
    """Download a file from Telegram by file_id, return raw bytes."""
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id}, timeout=10
    )
    file_path = r.json()["result"]["file_path"]
    r2 = requests.get(
        f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=30
    )
    return r2.content

def transcribe_voice(audio_bytes):
    """Send audio bytes to Whisper, return transcription string."""
    from openai import OpenAI
    key = get_openai_key()
    if not key:
        raise ValueError("No OpenAI API key found in keychain (openai/api_key) or OPENAI_API_KEY env")
    client = OpenAI(api_key=key)
    audio_io = io.BytesIO(audio_bytes)
    audio_io.name = "voice.ogg"
    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_io)
    return transcript.text.strip()

def classify_voice(text):
    """Pattern-match transcription against known commands.
    Returns (command_name, args) or (None, None) for capture mode."""
    for pattern, command, arg_group in VOICE_COMMAND_PATTERNS:
        m = pattern.search(text)
        if m:
            args = m.group(arg_group).strip() if arg_group else None
            return command, args
    return None, None

def log_voice_note(transcription, tag=None):
    """Append transcription to VOICE_NOTES.md (append-only, never overwrites)."""
    notes_file = BASE_DIR / 'VOICE_NOTES.md'
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    prefix = f"[{tag}] " if tag else ""
    entry = f"\n## {timestamp}\n{prefix}{transcription}\n"
    with open(notes_file, 'a') as f:
        f.write(entry)

def execute_voice_command(command, args, chat_id, token):
    """Execute a recognised voice command and reply via Telegram."""

    if command == 'resend_briefing':
        send_message(token, chat_id, "📨 Resending today's briefing...")
        send_briefing(token, chat_id)

    elif command == 'check_services':
        jobs = ['com.vanstedum.curator', 'ai.openclaw.gateway']
        lines = []
        for job in jobs:
            r = subprocess.run(['launchctl', 'list', job], capture_output=True)
            if r.returncode == 0:
                out = r.stdout.decode().strip()
                pid = out.split('\t')[0] if '\t' in out else '?'
                lines.append(f"✅ {job}  (PID {pid})")
            else:
                lines.append(f"❌ {job} — not loaded")
        send_message(token, chat_id, "📊 Service status:\n" + "\n".join(lines))

    elif command == 'run_curator':
        send_message(token, chat_id, "⏳ Running curator — this takes a few minutes...")
        def _run():
            r = subprocess.run(
                [str(BASE_DIR / 'run_curator_cron.sh')],
                capture_output=True, cwd=BASE_DIR, timeout=600
            )
            if r.returncode == 0:
                send_message(token, chat_id, "✅ Curator run complete. Sending briefing...")
                send_briefing(token, chat_id)
            else:
                send_message(token, chat_id, f"❌ Curator failed:\n{r.stderr.decode()[:300]}")
        threading.Thread(target=_run, daemon=True).start()

    elif command == 'dry_run':
        send_message(token, chat_id, "🧪 Running dry run — no writes, preview only...")
        def _dry():
            subprocess.run(
                ['python', 'curator_rss_v2.py', '--dry-run'],
                capture_output=True, cwd=BASE_DIR, timeout=300
            )
            preview = BASE_DIR / 'curator_preview.txt'
            if preview.exists():
                lines = preview.read_text().splitlines()
                snippet = '\n'.join(lines[:30])
                send_message(token, chat_id,
                    f"🧪 Dry run preview (first 30 lines):\n<pre>{snippet}</pre>")
            else:
                send_message(token, chat_id, "🧪 Dry run complete — no preview file found.")
        threading.Thread(target=_dry, daemon=True).start()

    elif command == 'add_roadmap':
        roadmap = BASE_DIR / 'PROJECT_ROADMAP.md'
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        entry = f"\n### Voice-added {timestamp}\n- {args}\n"
        with open(roadmap, 'a') as f:
            f.write(entry)
        send_message(token, chat_id, f"📋 Added to roadmap:\n{args}")

    elif command == 'delete_roadmap':
        # Spec: flag for review, do not delete silently
        roadmap = BASE_DIR / 'PROJECT_ROADMAP.md'
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        flag = f"\n### ⚠️ Voice delete request {timestamp}\n- FLAG FOR REMOVAL: {args}\n"
        with open(roadmap, 'a') as f:
            f.write(flag)
        send_message(token, chat_id,
            f"⚠️ Flagged for removal — review PROJECT_ROADMAP.md to confirm:\n{args}")

    elif command == 'investigate':
        # Log the request — OpenClaw handles investigations
        log_voice_note(f"Investigate: {args}", tag="INVESTIGATE REQUEST")
        send_message(token, chat_id,
            f"🔍 Investigation request logged: {args}\n"
            f"Tell OpenClaw directly to begin now if you want immediate results.")

def handle_voice_message(message, token):
    """Transcribe voice, echo back, classify as command or capture, act/log."""
    chat_id = str(message['chat']['id'])
    voice = message['voice']
    file_id = voice['file_id']
    duration = voice.get('duration', 0)

    # Transcribe
    try:
        audio_bytes = get_telegram_file_bytes(file_id, token)
        transcription = transcribe_voice(audio_bytes)
    except Exception as e:
        send_message(token, chat_id, f"❌ Transcription failed: {e}")
        return

    # Always echo back first
    send_message(token, chat_id, f'🎙️ I heard: "{transcription}"')

    if duration < 3:
        send_message(token, chat_id,
            "⚠️ Short note logged — double-check it made sense.")

    # Classify: command or capture
    command, args = classify_voice(transcription)

    if command is None:
        log_voice_note(transcription)
        send_message(token, chat_id, "📝 Noted.")
    else:
        execute_voice_command(command, args, chat_id, token)


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

            if 'message' in update_json:
                msg = update_json['message']
                if 'voice' in msg:
                    handle_voice_message(msg, token)
                elif 'text' in msg and msg['text'].startswith('/'):
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
