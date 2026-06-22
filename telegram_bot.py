#!/usr/bin/env python3
"""
telegram_bot.py - Unified Telegram bot

Handles:
- Sending daily briefing (called by run_curator_cron.sh)
- Listening for Like/Dislike/Save button callbacks
- Accepting commands: /run, /status, /briefing
"""

from html import escape
import asyncio
import os
import io
import re
import sys
import json
import threading
import subprocess
from functools import partial
import keyring
import requests
import argparse
from pathlib import Path
from datetime import datetime

# Ensure the moved german domain is on the path before any german_domain import.
# german_domain.py lives at domains/german/ since the 2026-05-30 promotion.
# A stale __pycache__/german_domain.cpython-*.pyc at repo root can shadow this
# if not explicitly resolved first.
_REPO_ROOT = Path(__file__).parent
_GERMAN_DOMAIN_DIR = _REPO_ROOT / "domains" / "german"
if str(_GERMAN_DOMAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_GERMAN_DOMAIN_DIR))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes
from telegram.ext import filters
from telegram.error import NetworkError, TimedOut
from german_domain import (
    # Group A — constants and pure functions
    ROBERT_CHAT_ID, GERMAN_BASE, GERMAN_DIR, VENV_PYTHON,
    _WRITING_RE, _SESSION_RE, _CONJUGATE_RE,
    _DRILL_RE, _DRILL_L2_RE, _DRILL_CTL_RE, _DRILL_AGAIN_RE,
    _DRILL_LIST_RE, _DRILL_MORE_RE, _SKIP_LESSON_RE, _AGAIN_RE,
    _PHRASE_CAPTURE_RE, _PHRASE_PRACTICE_VOICE_RE,
    _PHRASE_LIST_VOICE_RE, _PHRASE_LIST_MORE_VOICE_RE,
    _SPOKEN_NUMBERS, _LLM_PROVIDERS,
    _DE_CONTRACTIONS, _DE_CONTRACTION_RE,
    _DRILL_PERSONS, _PERSONS_DISPLAY, _PERSONS_POOL,
    _resolve_keyword_intent, _parse_spoken_id,
    _all_verb_entries, _lookup_verb,
    _expand_contractions, _normalize_answer, _spell_feedback,
    _item_tag, _drill_prompt, _l2_prompt,
    _start_drill_state, _record_l2_item, _record_l1_person, _finalize_l1_items,
    # Group B — I/O and LLM functions
    _PHRASEBOOK_FILE, _DRILL_STATE_FILE, _DRILL_LIST_STATE_FILE,
    _load_keyword_map_bot, _last_session_persona, _run, _german_agent_mode,
    _phrase_next_id, _load_drill_pool, _save_drill_pool,
    _load_phrasebook, _save_phrasebook,
    _call_llm, _fetch_conjugations, _fetch_phrases,
    _write_drill_anki, _drill_completion_message,
    # Group C — async-free resolvers with callback pattern
    _resolve_verb, _resolve_phrases, _resolve_drill_verb,
)

BASE_DIR = Path(__file__).parent
processed_callbacks = set()

# ─── Voice command patterns ───────────────────────────────────────────────────
VOICE_COMMAND_PATTERNS = [
    (re.compile(r'resend (report|briefing)', re.I),                'resend_briefing', None),
    (re.compile(r'(check services|are services running|service status)', re.I), 'check_services', None),
    (re.compile(r'(run the briefing|run daily|run curator)', re.I),'run_curator',    None),
    (re.compile(r'dry run', re.I),                                 'dry_run',         None),
    (re.compile(r'(reset session|compact session)', re.I),         'reset_session',   None),
    (re.compile(r'(session status|token count|how much is this costing)', re.I), 'session_status', None),
    (re.compile(r'investigate[:\s]+(.+)',         re.I),           'investigate',     1),
    (re.compile(r'add to roadmap[:\s]+(.+)',      re.I),           'add_roadmap',     1),
    (re.compile(r'delete from roadmap[:\s]+(.+)', re.I),           'delete_roadmap',  1),
]

# ─── Token helpers ────────────────────────────────────────────────────────────

def get_token():
    """Outbound sending token (rvsopenbot). Used by send_mode only."""
    try:
        token = keyring.get_password("telegram", "bot_token")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')

def get_polling_token():
    """Inbound polling token (minimoi_cmd_bot). Used by run_bot_mode only.
    Must be a different bot from get_token() — Telegram allows only one
    getUpdates poller per token. Mixing causes silent message loss."""
    try:
        token = keyring.get_password("telegram", "polling_bot_token")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get('TELEGRAM_POLLING_BOT_TOKEN')

def get_chat_id():
    return os.environ.get('TELEGRAM_CHAT_ID')

# ─── Sending ──────────────────────────────────────────────────────────────────

def send_message(token, chat_id, text, parse_mode="HTML", retries=3):
    """Simple fire-and-forget message send with retry on timeout"""
    for attempt in range(1, retries + 1):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True},
                timeout=30
            )
            return
        except requests.exceptions.Timeout:
            print(f"⚠️  send_message timeout (attempt {attempt}/{retries})")
            if attempt == retries:
                print("❌ send_message failed after all retries")
                raise

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
    
    for attempt in range(1, 4):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message.strip(),
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                    "disable_web_page_preview": True,
                },
                timeout=30
            )
            return
        except requests.exceptions.Timeout:
            print(f"⚠️  send_article timeout (attempt {attempt}/3)")
            if attempt == 3:
                print(f"❌ send_article failed after all retries: #{num} {title[:40]}")
                raise

def send_briefing(token, chat_id):
    """Read curator_output.txt and send top articles"""
    output_file = BASE_DIR / 'curator_output.txt'
    
    if not output_file.exists():
        send_message(token, chat_id, "❌ curator_output.txt not found — has the curator run today?")
        return
    
    from datetime import datetime
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    send_message(token, chat_id, f"🧠 <b>Your Morning Briefing</b> — {today_str}\n\nTop curated articles:")
    
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
    try:
        await query.answer(f"⏳ Recording {action}...")
    except Exception as e:
        print(f"⚠️  Could not send callback ack (network issue?): {e}")

    # Parse article data from message text
    article_data = parse_article_from_message(query.message.text, rank)
    result = record_feedback(action, rank, article_data)

    if result['success']:
        original = query.message.text
        if "✅" in original:
            original = original.split('\n✅')[0]
        try:
            await query.edit_message_text(
                text=original + f"\n\n✅ {action.capitalize()}d!",
                reply_markup=query.message.reply_markup
            )
        except Exception as e:
            print(f"⚠️  Could not edit message after feedback (network issue?): {e}")
    else:
        try:
            await query.answer(f"❌ {result['message']}", show_alert=True)
        except Exception as e:
            print(f"⚠️  Could not send error callback: {e}")

def record_feedback(action, rank, article_data):
    """Call repo's curator_feedback.py via venv Python to record feedback"""
    venv_python = BASE_DIR / 'venv' / 'bin' / 'python'
    python_bin = str(venv_python) if venv_python.exists() else 'python3'
    feedback_script = BASE_DIR / 'curator_feedback.py'

    if not feedback_script.exists():
        return {'success': False, 'message': f'curator_feedback.py not found at {feedback_script}'}

    try:
        result = subprocess.run(
            [python_bin, str(feedback_script), action, str(rank),
             '--channel', 'telegram', '--text', f'{action}d from Telegram'],
            capture_output=True,
            cwd=str(BASE_DIR),
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
    if not update.message:
        return
    await update.message.reply_text("⏳ Running curator now, this takes a few minutes...")
    out, err, rc = await _arun(
        [str(BASE_DIR / 'run_curator_cron.sh')],
        timeout=600,
        cwd=str(BASE_DIR),
    )
    if rc == 0:
        await update.message.reply_text("✅ Curator run complete. Sending briefing...")
        token = get_polling_token()
        chat_id = get_chat_id() or str(update.message.chat_id)
        send_briefing(token, chat_id)
    else:
        await update.message.reply_text(f"❌ Curator failed:\n{err[:300]}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show last run info"""
    if not update.message:
        return
    log = BASE_DIR / 'logs' / 'curator_launchd.log'
    if log.exists():
        lines = log.read_text().splitlines()
        last_lines = '\n'.join(lines[-10:])
        await update.message.reply_text(f"📊 Last 10 log lines:\n<pre>{escape(last_lines)}</pre>", parse_mode="HTML")
    else:
        await update.message.reply_text("No log file found yet.")

async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /briefing — resend today's briefing"""
    if not update.message:
        return
    token = get_polling_token()
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

    elif command == 'reset_session':
        # Webhook path is stateless — no session to reset
        send_message(token, chat_id,
            "♻️ Webhook mode is stateless — no session context accumulates here.\n"
            "If OpenClaw's polling session is bloated, send 'reset session' directly to OpenClaw.")

    elif command == 'session_status':
        log = BASE_DIR / 'logs' / 'curator_launchd.log'
        last_run = "unknown"
        if log.exists():
            lines = log.read_text().splitlines()
            for line in reversed(lines):
                if line.strip():
                    last_run = line.strip()[-60:]
                    break
        send_message(token, chat_id,
            f"📊 Webhook mode — sessions don't accumulate cost here.\n"
            f"Last curator run: {last_run}\n"
            f"For OpenClaw session cost, ask OpenClaw directly: 'session status'")

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
    token = get_polling_token()
    chat_id = get_chat_id()

    if not token or not chat_id:
        print("❌ Missing polling token or TELEGRAM_CHAT_ID")
        return

    send_briefing(token, chat_id)

# ─── German domain text handlers ─────────────────────────────────────────────

# ROBERT_CHAT_ID, GERMAN_BASE, GERMAN_DIR, VENV_PYTHON,
# regex patterns (_SESSION_RE, _DRILL_RE, etc.), and _AGAIN_RE
# are imported from german_domain


# _load_keyword_map_bot, _last_session_persona, _run imported from german_domain

KEYWORD_MAP = _load_keyword_map_bot()


async def _arun(cmd, timeout=120, **kwargs):
    """Async-safe subprocess wrapper — runs _run() in a thread pool so the event loop stays free."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_run, cmd, timeout, **kwargs))


async def _handle_german_transcript(update: Update, text: str):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    inbox = GERMAN_DIR / "sessions" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    raw_path = inbox / f"raw_{ts}.txt"
    raw_path.write_text(text)

    await update.message.reply_text("📥 Transcript saved. Running pipeline…")

    out, err, rc = await _arun(
        [str(VENV_PYTHON), "parse_transcript.py", "--stdin", "--base-dir", "data/"],
        input=text, cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(
            f"❌ parse_transcript.py failed (exit {rc}):\n{err[:400]}\nFile: {raw_path}"
        )
        return

    reviewer_out, reviewer_err, rc = await _arun(
        [str(VENV_PYTHON), "reviewer.py", "--latest", "--base-dir", "data/"],
        cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(
            f"❌ reviewer.py failed (exit {rc}):\n{reviewer_err[:400]}\nFile: {raw_path}"
        )
        return
    if reviewer_out and reviewer_out.strip():
        await update.message.reply_text(f"<pre>{escape(reviewer_out.strip())}</pre>", parse_mode="HTML")

    out, err, rc = await _arun(
        [str(VENV_PYTHON), "status.py", "--base-dir", "data/"],
        cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(f"❌ status.py failed (exit {rc}):\n{err[:400]}")
        return

    if not out or out.strip().startswith("{"):
        out = "⚠️ Pipeline completed but status output was empty or unexpected.\nRun !german status to check."
    await update.message.reply_text(f"<pre>{escape(out)}</pre>", parse_mode="HTML")


# _german_agent_mode imported from german_domain


async def _handle_german_command(update: Update, text: str):
    parts = text.strip().split()
    cmd = parts[1].lower() if len(parts) > 1 else ""

    # Respect agent_mode — openclaw mode means this bot only handles the pipeline
    if _german_agent_mode() == "openclaw" and cmd in ("session", "drill", "writing"):
        await update.message.reply_text(
            "ℹ️ agent_mode is openclaw — send session commands to @minimoi_agent_bot."
        )
        return

    if cmd == "status":
        out, err, rc = await _arun(
            [str(VENV_PYTHON), "status.py", "--base-dir", "data/"],
            cwd=str(GERMAN_BASE)
        )
        reply = out if rc == 0 else f"❌ status.py failed:\n{err[:400]}"
        await update.message.reply_text(f"<pre>{escape(reply)}</pre>", parse_mode="HTML")

    elif cmd == "progress":
        p = GERMAN_DIR / "progress.json"
        if not p.exists():
            await update.message.reply_text("No progress.json yet — run a session first.")
            return
        d = json.loads(p.read_text())
        counts = d.get("cumulative_error_counts", {})
        top = max(counts.items(), key=lambda x: x[1], default=("none", 0))[0]
        lines = [
            f"Sessions: {d.get('total_sessions', 0)}",
            f"Minutes: {d.get('total_minutes', 0)}",
            f"Anki cards: {d.get('anki_cards_generated', 0)}",
            f"Personas: {', '.join(d.get('personas_practiced', [])) or 'none'}",
            f"Top error: {top}",
        ]
        await update.message.reply_text("\n".join(lines))

    elif cmd == "session":
        out, err, rc = await _arun(
            [str(VENV_PYTHON), "get_german_session.py",
             "--base-dir", "data/", "--dropbox", "--send"],
            timeout=300, cwd=str(GERMAN_BASE)
        )
        if rc != 0:
            await update.message.reply_text(f"❌ get_german_session.py failed:\n{err[:400]}")

    elif cmd == "drill":
        # Usage: !german drill [persona] [scenario] [N]
        # e.g.  !german drill Stefan ubahn 3
        # N defaults to 3 if omitted
        drill_parts = parts[2:]  # everything after "drill"
        drill_n = 3
        if drill_parts:
            try:
                drill_n = int(drill_parts[-1])
                drill_parts = drill_parts[:-1]
            except ValueError:
                pass
        out, err, rc = await _arun(
            [str(VENV_PYTHON), "get_german_session.py",
             "--base-dir", "data/",
             "--drill", str(drill_n),
             "--dropbox", "--send"],
            timeout=300, cwd=str(GERMAN_BASE)
        )
        if rc != 0:
            await update.message.reply_text(f"❌ get_german_session.py (drill) failed:\n{err[:400]}")
        else:
            await update.message.reply_text(
                f"✅ {drill_n} drill prompts written to Dropbox. Session 1 sent to Telegram."
            )

    elif cmd == "today":
        out, err, rc = await _arun(
            [str(VENV_PYTHON), "reviewer.py", "--latest", "--base-dir", "data/"],
            cwd=str(GERMAN_BASE)
        )
        reply = out if rc == 0 else f"❌ reviewer.py failed:\n{err[:400]}"
        await update.message.reply_text(f"<pre>{escape(reply)}</pre>", parse_mode="HTML")

    elif cmd == "next":
        await update.message.reply_text(
            "ℹ️ --next-only not yet implemented. Use !german status to see the next lesson."
        )

    elif cmd == "persona":
        name = " ".join(parts[2:]) if len(parts) > 2 else ""
        if not name:
            await update.message.reply_text("Usage: !german persona [name]")
            return
        cfg_path = GERMAN_DIR / "config" / "domain.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["active_persona"] = name
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        await update.message.reply_text(f"✅ active_persona set to: {name}")

    elif cmd == "writing":
        out, err, rc = await _arun(
            [str(VENV_PYTHON), "get_german_session.py",
             "--base-dir", "data/", "--dropbox", "--send", "--writing"],
            timeout=300, cwd=str(GERMAN_BASE)
        )
        if rc != 0:
            await update.message.reply_text(f"❌ get_german_session.py failed:\n{err[:400]}")

    elif cmd == "watcher" and len(parts) > 2 and parts[2].lower() == "start":
        import subprocess as _sp
        script = str(GERMAN_BASE / "watch_transcripts.py")
        _sp.Popen(
            [str(VENV_PYTHON), script],
            cwd=str(GERMAN_BASE),
            start_new_session=True,
        )
        await update.message.reply_text("✅ Watcher started.")

    elif cmd == "watcher" and len(parts) > 2 and parts[2].lower() == "stop":
        out, err, rc = await _arun(["pkill", "-f", "watch_transcripts.py"])
        if rc == 0:
            await update.message.reply_text("✅ Watcher stopped.")
        else:
            await update.message.reply_text("ℹ️ Watcher was not running (or pkill failed).")

    elif cmd == "resetphrases":
        verb = args.strip().lower() if args else ""
        if not verb:
            await update.message.reply_text("Usage: !german resetphrases [verb]")
            return
        pool = _load_drill_pool()
        found = False
        for section in ("core", "on_demand"):
            for entry in pool.get(section, {}).get("verbs", []):
                if isinstance(entry, dict) and entry.get("verb", "").lower() == verb:
                    entry.pop("phrases", None)
                    found = True
        if found:
            _save_drill_pool(pool)
            await update.message.reply_text(
                f"✅ Phrases cleared for '{verb}'. Next drill will regenerate."
            )
        else:
            await update.message.reply_text(f"⚠️ Verb '{verb}' not found in pool.")

    elif cmd == "anki":
        anki_dir = GERMAN_DIR / "anki"
        csvs = sorted(anki_dir.glob("*.csv")) if anki_dir.exists() else []
        if csvs:
            await update.message.reply_text(f"Latest Anki CSV: {csvs[-1]}")
        else:
            await update.message.reply_text("No Anki CSV files yet.")

    elif cmd == "debug":
        lines = []
        inbox = GERMAN_DIR / "sessions" / "inbox"
        inboxfiles = sorted(inbox.glob("*.txt")) if inbox.exists() else []
        if inboxfiles:
            lines.append(f"Last inbox file: {inboxfiles[-1].name}")
        sessions_dir = GERMAN_DIR / "sessions"
        sessions = sorted(sessions_dir.glob("*.json")) if sessions_dir.exists() else []
        if sessions:
            data = json.loads(sessions[-1].read_text())
            lines.append(f"Last session: {sessions[-1].name}")
            lines.append(f"  persona: {data.get('persona')}")
            lines.append(f"  scenario: {data.get('scenario')}")
            lines.append(f"  reviewed: {data.get('reviewer_output') is not None}")
        await update.message.reply_text(
            "\n".join(lines) if lines else "Nothing in sessions/ or inbox/ yet."
        )

    elif cmd == "addphrase":
        # Format: !german addphrase verb|german_phrase|english_phrase
        # Or without verb: !german addphrase german_phrase|english_phrase
        raw = " ".join(parts[2:])
        pipe_parts = [p.strip() for p in raw.split("|")]
        if len(pipe_parts) == 3:
            verb, german, english = pipe_parts
        elif len(pipe_parts) == 2:
            verb, german, english = None, pipe_parts[0], pipe_parts[1]
        else:
            await update.message.reply_text(
                "Usage: !german addphrase [verb|]german|english\n"
                "Example: !german addphrase schauen|Nein danke, ich schaue nur.|No thanks, I'm just looking."
            )
            return

        pool_path = GERMAN_DIR / "config" / "drill_pool.json"
        pool = json.loads(pool_path.read_text())

        if verb:
            for v in pool["core"]["verbs"]:
                if v["verb"].lower() == verb.lower():
                    v["phrases"].append({"english": english, "german": german})
                    pool_path.write_text(json.dumps(pool, indent=2, ensure_ascii=False))
                    await update.message.reply_text(
                        f"✅ Phrase added to '{verb}':\n🇩🇪 {german}\n🇺🇸 {english}"
                    )
                    return

        # Verb not found in core or not given — save to session_fed.pending
        entry = {
            "phrase": german,
            "english": english,
            "promoted_date": datetime.now().date().isoformat(),
        }
        if verb:
            entry["verb_hint"] = verb
        pool["session_fed"]["pending"].append(entry)
        pool_path.write_text(json.dumps(pool, indent=2, ensure_ascii=False))
        label = f" (verb '{verb}' not in core list)" if verb else ""
        await update.message.reply_text(
            f"✅ Phrase saved to pending{label}:\n🇩🇪 {german}\n🇺🇸 {english}"
        )

    else:
        await update.message.reply_text(
            "German commands:\n"
            "  !german session       — get today's session prompt\n"
            "  !german writing       — get today's writing session prompt\n"
            "  !german drill [N]     — generate N drill sessions\n"
            "  !german status        — current progress summary\n"
            "  !german anki          — import Anki cards\n"
            "  !german addphrase [verb|]de|en — add a phrase to the drill pool\n"
            "  !german watcher start — start Dropbox transcript watcher\n"
            "  !german watcher stop  — stop watcher"
        )


async def _start_repeat_session(update, persona_name: str, scenario: str) -> None:
    """Repeat last session persona/scenario — rotation index does not advance."""
    extra = ["--persona", persona_name, "--repeat"]
    if scenario:
        extra += ["--scenario", scenario]
    out, err, rc = await _arun(
        [str(VENV_PYTHON), "get_german_session.py",
         "--base-dir", "data/", "--dropbox", "--send"] + extra,
        timeout=300, cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(f"❌ get_german_session.py failed:\n{err[:400]}")


async def _start_keyword_session(update, persona_name: str, scenario: str) -> None:
    """Run get_german_session.py with persona/scenario override from keyword intent."""
    extra = ["--persona", persona_name]
    if scenario:
        extra += ["--scenario", scenario]
    out, err, rc = await _arun(
        [str(VENV_PYTHON), "get_german_session.py",
         "--base-dir", "data/", "--dropbox", "--send"] + extra,
        timeout=300, cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(f"❌ get_german_session.py failed:\n{err[:400]}")


async def _handle_skip_lesson(update) -> None:
    """Skip the current lesson and advance the rotation to the next persona/scenario."""
    out, err, rc = await _arun(
        [str(VENV_PYTHON), "get_german_session.py",
         "--base-dir", "data/", "--skip"],
        timeout=60, cwd=str(GERMAN_BASE)
    )
    if rc != 0:
        await update.message.reply_text(f"❌ Skip failed:\n{err[:400]}")
    else:
        confirmation = out.strip() or "⏭ Lesson skipped. Say 'next session' to start the next one."
        await update.message.reply_text(confirmation)


# _load_drill_pool, _save_drill_pool, _PHRASEBOOK_FILE,
# _load_phrasebook, _save_phrasebook, _phrase_next_id,
# _call_llm, _fetch_conjugations imported from german_domain

_phrase_practice: dict = {}     # chat_id → {"phrase_id": str}; in-memory, survives restarts fine
_phrase_save_pending: dict = {}  # chat_id → {"german": str, "english": str}; awaiting yes/no confirm
_phrase_capture_mode: dict = {}  # chat_id → {"retries": int}; waiting for second voice note
_phrase_list_offset: dict = {}   # chat_id → int; pagination offset for phrase list
_last_list_type: dict = {}       # chat_id → 'phrase' | 'verb'; which list was shown last (for bare "more" routing)


# _resolve_verb, _resolve_phrases imported from german_domain (Group C)


# _DE_CONTRACTIONS, _DE_CONTRACTION_RE, _expand_contractions,
# _normalize_answer, _spell_feedback imported from german_domain


async def _handle_conjugate(update, verb: str) -> None:
    """Level 0 conjugate flashcard — any verb, looks up via Claude if needed."""
    async def _cb(msg): await update.message.reply_text(msg)
    entry = _resolve_verb(verb.lower(), progress_cb=_cb)
    if not entry:
        return
    c = entry.get("conjugations", {})
    msg = (
        f"{entry['verb']} ({entry.get('english', '?')}) — present tense:\n\n"
        f"ich {c.get('ich','___')}    wir {c.get('wir','___')}\n"
        f"du {c.get('du','___')}     ihr {c.get('ihr','___')}\n"
        f"er {c.get('er','___')}     sie/Sie {c.get('sie','___')}"
    )
    await update.message.reply_text(msg)


# ─── Drill engine (Level 1 + Level 2) ───────────────────────────────────────

_active_drills: dict = {}  # chat_id → drill state; persisted to disk across restarts
_last_drills: dict = {}   # chat_id → {verb, level, english} snapshot after completion
_drill_list_state: dict = {}  # chat_id → {verbs: list, offset: int} for paginated listing

# _DRILL_STATE_FILE, _DRILL_LIST_STATE_FILE imported from german_domain


def _save_drill_list_state() -> None:
    try:
        with open(_DRILL_LIST_STATE_FILE, "w") as f:
            import json as _json
            _json.dump({str(k): v for k, v in _drill_list_state.items()}, f)
    except Exception as e:
        print(f"⚠️  Could not save drill list state: {e}")


def _load_drill_list_state() -> None:
    if not _DRILL_LIST_STATE_FILE.exists():
        return
    try:
        import json as _json
        with open(_DRILL_LIST_STATE_FILE) as f:
            data = _json.load(f)
        _drill_list_state.update({int(k): v for k, v in data.items()})
    except Exception as e:
        print(f"⚠️  Could not restore drill list state: {e}")


def _save_drill_state() -> None:
    try:
        with open(_DRILL_STATE_FILE, "w") as f:
            import json as _json
            _json.dump({str(k): v for k, v in _active_drills.items()}, f)
    except Exception as e:
        print(f"⚠️  Could not save drill state: {e}")


def _load_drill_state() -> None:
    if not _DRILL_STATE_FILE.exists():
        return
    try:
        import json as _json
        with open(_DRILL_STATE_FILE) as f:
            data = _json.load(f)
        before = set(_active_drills)
        _active_drills.update({int(k): v for k, v in data.items()})
        new_keys = set(_active_drills) - before
        if new_keys:
            print(f"♻️  Restored {len(new_keys)} drill(s) from disk: {new_keys}")
    except Exception as e:
        print(f"⚠️  Could not restore drill state: {e}")

# _DRILL_PERSONS, _PERSONS_DISPLAY, _PERSONS_POOL, _item_tag,
# _write_drill_anki, _drill_completion_message,
# _drill_prompt, _l2_prompt, _start_drill_state, _record_l2_item,
# _record_l1_person, _finalize_l1_items imported from german_domain


# _resolve_drill_verb imported from german_domain (Group C)


async def _handle_drill(update, target: str) -> None:
    """Route to Level 1 or Level 2 drill based on trigger text."""
    target_lower = target.lower()
    if _DRILL_L2_RE.search(target_lower):
        await _handle_drill_l2_start(update, target_lower)
    else:
        await _handle_drill_l1_start(update, target_lower)


async def _handle_drill_l1_start(update, target_lower: str) -> None:
    """Start a Level 1 conjugation drill — any verb, random if none specified."""
    async def _cb(msg): await update.message.reply_text(msg)
    entry = _resolve_drill_verb(target_lower, progress_cb=_cb)
    if not entry:
        await update.message.reply_text("No verbs in drill pool yet.")
        return
    _drill_list_state.pop(update.message.chat_id, None)
    _save_drill_list_state()
    state = _start_drill_state(entry)
    state["level"] = 1
    _active_drills[update.message.chat_id] = state
    _save_drill_state()
    await update.message.reply_text(
        f"Conjugation drill. Type 'end drill' to stop.\n\n" + _drill_prompt(state)
    )


async def _handle_drill_l2_start(update, target_lower: str) -> None:
    """Start a Level 2 translation drill — any verb, random if none specified."""
    async def _cb(msg): await update.message.reply_text(msg)
    entry = _resolve_drill_verb(target_lower, progress_cb=_cb)
    if not entry:
        await update.message.reply_text("No verbs in drill pool yet.")
        return
    _drill_list_state.pop(update.message.chat_id, None)
    _save_drill_list_state()
    phrases = _resolve_phrases(entry, progress_cb=_cb)
    if not phrases:
        return
    import random
    queue = random.sample(range(len(phrases)), len(phrases))
    state = {
        "level": 2,
        "verb": entry["verb"],
        "english": entry.get("english", ""),
        "phrases": phrases,
        "queue": queue,
        "pos": 0,
        "score": 0,
        "total": 0,
        "wrong_count": 0,
        "retry": False,
        "items": [],
        "hint_used_current": False,
    }
    _active_drills[update.message.chat_id] = state
    _save_drill_state()
    await update.message.reply_text(
        f"Translation drill: {entry['verb']} ({entry.get('english','')}). Type 'end drill' to stop.\n\n"
        + _l2_prompt(state)
    )


async def _handle_drill_answer(update, text: str) -> None:
    """Route to L1 or L2 answer handler based on active drill level."""
    chat_id = update.message.chat_id
    state = _active_drills.get(chat_id)
    if not state:
        return
    if state.get("level", 1) == 2:
        await _handle_drill_l2_answer(update, text, chat_id, state)
    else:
        await _handle_drill_l1_answer(update, text, chat_id, state)
    _save_drill_state()


async def _handle_drill_l2_answer(update, text: str, chat_id: int, state: dict) -> None:
    """Check a Level 2 translation answer."""
    idx = state["queue"][state["pos"]]
    phrase = state["phrases"][idx]
    expected_raw = phrase["german"]
    expected = _normalize_answer(expected_raw)
    answer = _normalize_answer(text)

    if answer in ("hint", "skip"):
        if answer == "hint":
            words = expected_raw.split()
            hint = " ".join(w[:2] + "…" for w in words)
            state["hint_used_current"] = True
            await update.message.reply_text(f"💡 {hint}")
            return
        # skip — treat as friction
        _record_l2_item(state, phrase, state.get("wrong_count", 0), auto_revealed=True)
        state["total"] += 1
        state["wrong_count"] = 0
        state["pos"] += 1
        if state["pos"] >= len(state["queue"]):
            _last_drills[chat_id] = {"verb": state["verb"], "level": 2, "english": state.get("english", "")}
            msg = _drill_completion_message(state, f"→ {expected_raw}")
            del _active_drills[chat_id]
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(f"→ {expected_raw}\n\n" + _l2_prompt(state))
        return

    if answer == expected:
        wc = state.get("wrong_count", 0)
        state["score"] += 1
        state["total"] += 1
        state["wrong_count"] = 0
        state["retry"] = False
        _record_l2_item(state, phrase, wc, auto_revealed=False)
        state["pos"] += 1
        if state["pos"] >= len(state["queue"]):
            _last_drills[chat_id] = {"verb": state["verb"], "level": 2, "english": state.get("english", "")}
            msg = _drill_completion_message(state, f"✅ {expected_raw}")
            del _active_drills[chat_id]
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(f"✅ {expected_raw}\n\n" + _l2_prompt(state))
        return

    # Wrong answer
    if not state["retry"]:
        state["total"] += 1
        state["retry"] = True
    state["wrong_count"] = state.get("wrong_count", 0) + 1

    if state["wrong_count"] >= 3:
        _record_l2_item(state, phrase, state["wrong_count"], auto_revealed=True)
        state["wrong_count"] = 0
        state["retry"] = False
        state["pos"] += 1
        if state["pos"] >= len(state["queue"]):
            _last_drills[chat_id] = {"verb": state["verb"], "level": 2, "english": state.get("english", "")}
            msg = _drill_completion_message(state, f"→ {expected_raw}  (auto-revealed)")
            del _active_drills[chat_id]
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(f"→ {expected_raw}  (auto-revealed)\n\n" + _l2_prompt(state))
        return

    from difflib import SequenceMatcher
    similarity = SequenceMatcher(None, answer, expected).ratio()
    spell_note = _spell_feedback(answer, expected=expected)

    if spell_note:
        await update.message.reply_text(f"{spell_note}\n\nTry again:  (hint / skip)")
    elif similarity >= 0.7:
        await update.message.reply_text("Almost — check spelling.  (hint / skip)")
    else:
        await update.message.reply_text(f"❌ Try again.  (hint / skip)")


async def _handle_drill_l1_answer(update, text: str, chat_id: int, state: dict) -> None:
    """Check a Level 1 conjugation answer; 'hint' or 'skip' are escape hatches."""

    person = state["current"]
    expected = state["conjugations"].get(person, "").strip().lower()
    answer = text.strip().lower()
    # Accept "ich nehme" as "nehme" — strip leading pronoun if user included it
    _pronouns = {"ich", "du", "er", "sie", "es", "wir", "ihr", "sie/sie"}
    parts = answer.split(None, 1)
    if len(parts) == 2 and parts[0] in _pronouns:
        answer = parts[1]

    if answer == "hint":
        hint = expected[:2] + "…" if len(expected) > 2 else expected
        state["hint_used_current"] = True
        await update.message.reply_text(f"💡 {person} {hint}")
        return

    if answer == "skip":
        _record_l1_person(state, state.get("wrong_count", 0), auto_revealed=True)
        state["total"] += 1
        state["retry"] = False
        state["wrong_count"] = 0
        state["pos"] += 1
        if state["pos"] >= len(state["queue"]):
            _finalize_l1_items(state)
            _last_drills[chat_id] = {"verb": state["verb"], "level": 1, "english": state.get("english", "")}
            msg = _drill_completion_message(state, f"→ {person} {expected}")
            del _active_drills[chat_id]
            await update.message.reply_text(msg)
        else:
            state["current"] = state["queue"][state["pos"]]
            await update.message.reply_text(f"→ {person} {expected}\n\n" + _drill_prompt(state))
        return

    def _advance(reveal_prefix: str) -> str:
        state["pos"] += 1
        if state["pos"] >= len(state["queue"]):
            _finalize_l1_items(state)
            _last_drills[chat_id] = {"verb": state["verb"], "level": 1, "english": state.get("english", "")}
            msg = _drill_completion_message(state, reveal_prefix)
            del _active_drills[chat_id]
            return msg
        else:
            state["current"] = state["queue"][state["pos"]]
            return f"{reveal_prefix}\n\n" + _drill_prompt(state)

    if answer == expected:
        wc = state.get("wrong_count", 0)
        state["score"] += 1
        state["total"] += 1
        state["retry"] = False
        state["wrong_count"] = 0
        _record_l1_person(state, wc, auto_revealed=False)
        await update.message.reply_text(_advance(f"✅ {person} {expected}"))
    else:
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, answer, expected).ratio()
        is_close = similarity >= 0.7

        state.setdefault("wrong_count", 0)
        if not state["retry"]:
            state["total"] += 1
            state["retry"] = True
        state["wrong_count"] = state.get("wrong_count", 0) + 1

        if state["wrong_count"] >= 3:
            _record_l1_person(state, state["wrong_count"], auto_revealed=True)
            state["retry"] = False
            state["wrong_count"] = 0
            await update.message.reply_text(_advance(f"→ {person} {expected}  (auto-revealed)"))
        elif is_close:
            await update.message.reply_text(f"Almost — check spelling.  {person} ___?")
        else:
            await update.message.reply_text(f"❌ {person} ___?  (hint / skip)")


async def _restart_last_drill(update) -> None:
    """Restart the most recently completed drill for this chat."""
    chat_id = update.message.chat_id
    snap = _last_drills.get(chat_id)
    if not snap:
        await update.message.reply_text("No recent drill to repeat.")
        return
    if snap["level"] == 2:
        await _handle_drill_l2_start(update, snap["verb"])
    else:
        await _handle_drill_l1_start(update, snap["verb"])


def _drill_list_page(verbs: list, offset: int, page_size: int = 10) -> str:
    """Format one page of the verb list."""
    page = verbs[offset:offset + page_size]
    total = len(verbs)
    lines = [f"Verb pool ({total} total):\n"]
    for i, v in enumerate(page, start=offset + 1):
        priority = v.get("priority", "")
        tag = " ★" if priority == "HIGH" else ""
        lines.append(f"  {i}. {v['verb']} — {v.get('english', '')}{tag}")
    remaining = total - (offset + len(page))
    if remaining > 0:
        lines.append(f"\n(say 'more' for next {min(remaining, page_size)}, or type a number or verb to drill)")
    else:
        lines.append("\n(type a number or verb to drill)")
    return "\n".join(lines)


async def _handle_drill_list(update) -> None:
    chat_id = update.message.chat_id
    pool = _load_drill_pool()
    verbs = _all_verb_entries(pool)
    if not verbs:
        await update.message.reply_text("No verbs in drill pool yet.")
        return
    _drill_list_state[chat_id] = {"verbs": verbs, "offset": 10}
    _save_drill_list_state()
    _last_list_type[chat_id] = 'verb'
    await update.message.reply_text(_drill_list_page(verbs, 0))


async def _handle_drill_list_more(update) -> None:
    chat_id = update.message.chat_id
    ls = _drill_list_state.get(chat_id)
    if not ls:
        # State was lost (bot restart) — restart list from beginning
        await _handle_drill_list(update)
        return
    offset = ls["offset"]
    verbs = ls["verbs"]
    if offset >= len(verbs):
        await update.message.reply_text("That's the full list.")
        del _drill_list_state[chat_id]
        _save_drill_list_state()
        return
    ls["offset"] = offset + 10
    _save_drill_list_state()
    await update.message.reply_text(_drill_list_page(verbs, offset))


_LIST_SELECT_RE = re.compile(
    r'^(?:drill\s+)?(?:(?:phrases?|l2|translate|level\s*2)\s+)?(?P<sel>\d+|[a-zäöüß]+)(?:\s+(?:phrases?|l2|translate|level\s*2))?$',
    re.I,
)
_LIST_SELECT_L2_RE = re.compile(r'\b(?:phrases?|l2|translate|level\s*2)\b', re.I)
# Words that look like bare verb names but are drill control words — never treat as list selections
_LIST_SELECT_EXCLUDED = {"hint", "skip", "more", "next", "end", "stop", "done", "quit", "enough", "again"}


async def _handle_drill_list_select(update, text: str) -> None:
    """Handle numeric index or bare verb name as selection from a displayed drill list."""
    chat_id = update.message.chat_id
    ls = _drill_list_state.get(chat_id)
    if not ls:
        return
    verbs = ls["verbs"]
    m = _LIST_SELECT_RE.match(text.strip())
    if not m:
        return
    sel = m.group("sel")
    if sel.lower() in _LIST_SELECT_EXCLUDED:
        return
    is_l2 = bool(_LIST_SELECT_L2_RE.search(text))
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(verbs):
            entry = verbs[idx]
        else:
            await update.message.reply_text(f"No verb #{sel} — list has {len(verbs)}.")
            return
    else:
        entry = next((v for v in verbs if v["verb"].lower() == sel.lower()), None)
        if not entry:
            return
    del _drill_list_state[chat_id]
    _save_drill_list_state()
    if is_l2:
        await _handle_drill_l2_start(update, entry["verb"].lower())
    else:
        await _handle_drill_l1_start(update, entry["verb"].lower())


async def _handle_drill_control(update, word: str) -> None:
    """Handle end-drill words — write Anki friction cards then exit."""
    chat_id = update.message.chat_id
    state = _active_drills.get(chat_id)
    if not state:
        await update.message.reply_text("No active drill.")
        return
    if state.get("level", 1) == 1:
        _finalize_l1_items(state)
    score, total = state["score"], state["total"]
    written = _write_drill_anki(state)
    counts = {"drill-clean": 0, "drill-reinforced": 0, "needs-practice": 0}
    for it in state.get("items", []):
        counts[it["result"]] = counts.get(it["result"], 0) + 1
    _last_drills[chat_id] = {"verb": state["verb"], "level": state.get("level", 1), "english": state.get("english", "")}
    del _active_drills[chat_id]
    _save_drill_state()
    lines = [f"Drill ended — {score}/{total} correct so far."]
    if any(counts.values()):
        lines.append(f"  ✅ Clean: {counts['drill-clean']}  📝 Reinforced: {counts['drill-reinforced']}  ⚠️ Needs practice: {counts['needs-practice']}")
    if written:
        lines.append(f"  {written} card(s) added to vienna_deck.csv — reimport Anki to sync to phone.")
    lines.append("(say 'again' to repeat)")
    await update.message.reply_text("\n".join(lines))


_PHRASE_SUBS = {"list", "practice", "drill", "help", "more", "add", "capture"}


async def _handle_phrase_command(update: Update, text: str) -> None:
    rest = text[len("!phrase"):].strip()
    parts = rest.split(None, 1)
    sub = parts[0].lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""

    if sub in _PHRASE_SUBS:
        if sub == "list":
            _phrase_list_offset[update.message.chat_id] = 0
            await _handle_phrase_list(update, args)
        elif sub == "more":
            await _handle_phrase_list_more(update)
        elif sub == "practice":
            await _handle_phrase_practice(update, args)
        elif sub == "drill":
            await _handle_phrase_to_drill(update, args)
        elif sub in {"add", "capture"}:
            if "|" in args:
                await _handle_phrase_save(update, args)
            elif args:
                _phrase_capture_mode[update.message.chat_id] = {"mode": "text", "retries": 0}
                await _handle_phrase_capture_input(update, args)
            else:
                _phrase_capture_mode[update.message.chat_id] = {"mode": "text", "retries": 0}
                await update.message.reply_text("Ready — type the German phrase.")
        else:
            await update.message.reply_text(
                "phrase commands:\n"
                "  phrase add german | english  — save a phrase\n"
                "  phrase list                  — show recent phrases\n"
                "  phrase more                  — next page\n"
                "  phrase practice [id]         — spelling check\n"
                "  phrase drill [id]            — promote to verb drill"
            )
    else:
        await _handle_phrase_save(update, rest)


async def _handle_phrase_save(update: Update, raw: str) -> None:
    pipe_parts = [p.strip() for p in raw.split("|")]
    if len(pipe_parts) != 2 or not pipe_parts[0] or not pipe_parts[1]:
        await update.message.reply_text(
            "Usage: phrase add german | english\n"
            "Example: phrase add Nein danke, ich schaue nur. | No thanks, I'm just looking."
        )
        return
    german_submitted, english = pipe_parts

    # Ask LLM to fix spelling only — do not change dialect or meaning
    correction_prompt = (
        "Fix any spelling mistakes in this German phrase. "
        "Return ONLY the corrected phrase — no explanation, no quotes. "
        "Do not change dialect, informal vocabulary, or non-standard usage. "
        "If already correct, return it unchanged.\n\n"
        f"Phrase: {german_submitted}"
    )
    corrected = _call_llm(correction_prompt, max_tokens=100)
    german = corrected.strip() if corrected else german_submitted

    # future: show diff if german != german_submitted, e.g. "(corrected: nuur → nur)"
    _phrase_save_pending[update.message.chat_id] = {"german": german, "english": english}
    await update.message.reply_text(
        f"Did you mean:\n{german}\n{english}\n\nReply yes to save or no to cancel."
    )


async def _handle_phrase_save_confirm(update: Update, text: str) -> None:
    chat_id = update.message.chat_id
    pending = _phrase_save_pending.pop(chat_id, None)
    if not pending:
        return

    word = text.strip().lower()
    if word in {"yes", "ja", "já", "jo", "y", "yep", "correct", "save", "genau", "stimmt", "gut", "passt"}:
        today = datetime.now().date().isoformat()
        book = _load_phrasebook()
        pid = _phrase_next_id(book["phrases"], today)
        book["phrases"].append({
            "id": pid,
            "german": pending["german"],
            "english": pending["english"],
            "scene": "",
            "added": today,
            "status": "library",
            "verb_hint": "",
            "practice_count": 0,
            "last_practiced": None,
        })
        _save_phrasebook(book)
        short_id = pid.rsplit("_", 1)[-1]
        await update.message.reply_text(
            f"Saved (#{short_id})\n{pending['german']}\n{pending['english']}"
        )
    elif word in {"no", "nein", "n", "cancel", "nope"}:
        await update.message.reply_text("Cancelled — phrase not saved.")
    else:
        # Unknown reply — re-ask once, put state back
        _phrase_save_pending[chat_id] = pending
        await update.message.reply_text("Reply yes to save or no to cancel.")


async def _handle_phrase_list(update: Update, args: str, offset: int = 0) -> None:
    PAGE = 10
    try:
        n = int(args.strip()) if args.strip() and args.strip().isdigit() else PAGE
    except ValueError:
        n = PAGE
    book = _load_phrasebook()
    phrases = book.get("phrases", [])
    if not phrases:
        await update.message.reply_text(
            "No phrases saved yet. Use !phrase german | english to capture one."
        )
        return
    # newest first
    ordered = phrases[::-1]
    page = ordered[offset:offset + n]
    if not page:
        await update.message.reply_text("No more phrases.")
        return
    lines = []
    for p in page:
        short_id = p["id"].rsplit("_", 1)[-1]
        count = p.get("practice_count", 0)
        status = p.get("status", "library")
        lines.append(
            f"#{short_id} {p['german']}\n"
            f"     {p['english']} [{status}, {count} practice{'s' if count != 1 else ''}]"
        )
    remaining = len(ordered) - offset - len(page)
    if remaining > 0:
        lines.append(f"— {remaining} more. Say 'phrase more' or type !phrase more —")
    lines.append("— Say 'phrase practice' or type !phrase practice [id] to drill one —")
    _phrase_list_offset[update.message.chat_id] = offset + len(page)
    _last_list_type[update.message.chat_id] = 'phrase'
    await update.message.reply_text("\n\n".join(lines))


async def _handle_phrase_list_more(update: Update) -> None:
    offset = _phrase_list_offset.get(update.message.chat_id, 0)
    await _handle_phrase_list(update, "", offset=offset)


async def _handle_phrase_practice(update: Update, args: str) -> None:
    chat_id = update.message.chat_id
    book = _load_phrasebook()
    phrases = book.get("phrases", [])
    if not phrases:
        await update.message.reply_text("No phrases in library yet.")
        return
    if args.strip():
        target = args.strip()
        phrase = next(
            (p for p in phrases if p["id"] == target or p["id"].endswith(f"_{target}")),
            None,
        )
        if not phrase:
            await update.message.reply_text(
                f"Phrase '{target}' not found. Use !phrase list to see ids."
            )
            return
    else:
        library = [p for p in phrases if p.get("status") == "library"]
        if not library:
            await update.message.reply_text(
                "No library phrases to practice. All promoted to drill or archived."
            )
            return
        phrase = min(library, key=lambda p: (p.get("practice_count", 0), -phrases.index(p)))
    _phrase_practice[chat_id] = {"phrase_id": phrase["id"]}
    await update.message.reply_text(
        f"You learned this one in the wild — type it from memory:\n"
        f"{phrase['german']}\n"
        f"{phrase['english']}\n\n"
        f"Type the German spelling:"
    )


async def _handle_phrase_practice_answer(update: Update, text: str) -> None:
    from difflib import SequenceMatcher
    import re as _re
    chat_id = update.message.chat_id
    state = _phrase_practice.pop(chat_id, None)
    if not state:
        return
    book = _load_phrasebook()
    phrase = next((p for p in book["phrases"] if p["id"] == state["phrase_id"]), None)
    if not phrase:
        await update.message.reply_text("Phrase not found — it may have been removed.")
        return

    normalized_user = _normalize_answer(text)
    normalized_expected = _normalize_answer(phrase["german"])
    ratio = SequenceMatcher(None, normalized_user, normalized_expected).ratio()

    AGAIN = "\n\nSay 'phrase practice' or !phrase practice to try another."
    if ratio >= 0.92:
        # Exact or very close — count as correct
        phrase["practice_count"] = phrase.get("practice_count", 0) + 1
        phrase["last_practiced"] = datetime.now().date().isoformat()
        _save_phrasebook(book)
        await update.message.reply_text(f"✅ Correct!{AGAIN}")
    elif ratio >= 0.78:
        # Near-match — accept, show exact form
        phrase["practice_count"] = phrase.get("practice_count", 0) + 1
        phrase["last_practiced"] = datetime.now().date().isoformat()
        _save_phrasebook(book)
        await update.message.reply_text(
            f"✅ Close enough!\nExact: {phrase['german']}{AGAIN}"
        )
    else:
        # Wrong — don't count, prompt to try again
        await update.message.reply_text(
            f"❌ Try again.\nExpected something close to: {phrase['german'][:40]}…{AGAIN}"
        )


async def _handle_phrase_to_drill(update: Update, args: str) -> None:
    target = args.strip()
    if not target:
        await update.message.reply_text("Usage: !phrase drill [id]")
        return
    book = _load_phrasebook()
    phrase = next(
        (p for p in book.get("phrases", []) if p["id"] == target or p["id"].endswith(f"_{target}")),
        None,
    )
    if not phrase:
        await update.message.reply_text(
            f"Phrase '{target}' not found. Use !phrase list to see ids."
        )
        return
    pool = _load_drill_pool()
    verb_hint = phrase.get("verb_hint", "")
    if verb_hint:
        for v in pool.get("core", {}).get("verbs", []):
            if v["verb"].lower() == verb_hint.lower():
                new_entry = {"english": phrase["english"], "german": phrase["german"]}
                if new_entry not in v.get("phrases", []):
                    v.setdefault("phrases", []).append(new_entry)
                    _save_drill_pool(pool)
                    phrase["status"] = "drilling"
                    _save_phrasebook(book)
                    await update.message.reply_text(
                        f"Promoted to drill under '{v['verb']}':\n{phrase['german']}"
                    )
                else:
                    await update.message.reply_text(
                        f"That phrase is already in '{v['verb']}' drill."
                    )
                return
    await update.message.reply_text(
        f"Verb '{verb_hint}' not found in core list. Set verb_hint or add manually."
        if verb_hint else
        "No verb_hint set for this phrase. Cannot auto-promote without a target verb."
    )


async def _handle_phrase_capture_trigger(update: Update) -> None:
    """First voice note of two-step capture — put chat into capture mode, prompt for phrase."""
    _phrase_capture_mode[update.message.chat_id] = {"retries": 0}
    await update.message.reply_text("Ready — say the phrase.")


async def _handle_phrase_capture_input(update: Update, text: str) -> None:
    """Second voice note — LLM corrects + translates, then enters confirm flow."""
    chat_id = update.message.chat_id
    state = _phrase_capture_mode.get(chat_id, {})

    prompt = (
        "The following was transcribed from speech. The speaker just heard this phrase from a "
        "native German speaker and is repeating it back.\n\n"
        "Fix any transcription noise or spelling errors in the German. "
        "Then provide a natural English translation.\n"
        "The phrase may be in Austrian German or informal register — preserve this.\n\n"
        "Respond in exactly this format — two lines, nothing else:\n"
        "German: <corrected German>\n"
        "English: <natural English translation>\n\n"
        "If the text does not appear to be a German phrase at all, respond with exactly:\n"
        "NOT_GERMAN\n\n"
        f"Transcription: {text}"
    )

    resp = _call_llm(prompt, max_tokens=80)

    if resp is None:
        # All providers down — use raw transcription, no English
        _phrase_capture_mode.pop(chat_id, None)
        _phrase_save_pending[chat_id] = {"german": text, "english": ""}
        await update.message.reply_text(
            f"Saved what I heard — no translation available.\n"
            f"🇩🇪 {text}\n\n"
            f"Reply yes to save or no to cancel."
        )
        return

    resp = resp.strip()

    if resp == "NOT_GERMAN":
        retries = state.get("retries", 0)
        if retries == 0:
            _phrase_capture_mode[chat_id] = {"retries": 1}
            await update.message.reply_text(
                f"That didn't sound like a German phrase — I got:\n"
                f'"{text}"\n\n'
                f"Say the phrase again, or say \"cancel\" to exit capture mode."
            )
        else:
            _phrase_capture_mode.pop(chat_id, None)
            await update.message.reply_text("Capture cancelled — couldn't recognise a German phrase.")
        return

    # Parse "German: ...\nEnglish: ..." response
    german = ""
    english = ""
    for line in resp.splitlines():
        if line.startswith("German:"):
            german = line[len("German:"):].strip()
        elif line.startswith("English:"):
            english = line[len("English:"):].strip()

    if not german:
        german = text

    _phrase_capture_mode.pop(chat_id, None)
    _phrase_save_pending[chat_id] = {"german": german, "english": english}
    await update.message.reply_text(
        f"Did you mean:\n🇩🇪 {german}\n🇺🇸 {english}\n\nReply yes to save or no to cancel."
    )



async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route plain text messages — German transcript, !german commands, fallback."""
    if not update.message or not update.message.text:
        return
    if update.message.chat_id != ROBERT_CHAT_ID:
        return

    text = update.message.text.strip()

    # !phrase commands always route directly — never intercepted by drill or practice state
    if text.lower().startswith("!phrase"):
        await _handle_phrase_command(update, text)
        return

    # Phrase save confirmation intercepts before drill state
    if update.message.chat_id in _phrase_save_pending:
        await _handle_phrase_save_confirm(update, text)
        return

    # Phrase capture mode
    if update.message.chat_id in _phrase_capture_mode:
        if text.lower().strip() in {"cancel", "abbrechen", "stop"}:
            _phrase_capture_mode.pop(update.message.chat_id, None)
            await update.message.reply_text("Capture cancelled.")
        elif _phrase_capture_mode[update.message.chat_id].get("mode") == "text":
            # Text two-step: this message IS the phrase — route same as voice capture input
            await _handle_phrase_capture_input(update, text)
        # else: voice mode — ignore typed text, waiting for voice note
        return

    # Phrase practice intercepts before drill state
    if update.message.chat_id in _phrase_practice:
        await _handle_phrase_practice_answer(update, text)
        return

    # Text triggers for phrase commands (mirrors voice routing)
    # Guard: 'drill list phrases' should route to the verb pool, not phrase list
    if _PHRASE_LIST_VOICE_RE.search(text) and not _DRILL_LIST_RE.search(text):
        _phrase_list_offset[update.message.chat_id] = 0
        await _handle_phrase_list(update, "")
        return
    if _PHRASE_LIST_MORE_VOICE_RE.search(text):
        await _handle_phrase_list_more(update)
        return
    # Bare "more"/"next" — route to whichever list was shown last (phrase vs verb)
    if _DRILL_MORE_RE.search(text) and _last_list_type.get(update.message.chat_id) == 'phrase':
        await _handle_phrase_list_more(update)
        return
    _tm = _PHRASE_PRACTICE_VOICE_RE.search(text)
    if _tm:
        _tid = _parse_spoken_id(text[_tm.end():].strip())
        await _handle_phrase_practice(update, _tid)
        return
    # Phrase ID shortcut: "#010" or "drill #010" routes to phrase practice regardless of drill state
    _pid_m = re.search(r'#(\d{1,3})\b', text)
    if _pid_m:
        await _handle_phrase_practice(update, f"{int(_pid_m.group(1)):03d}")
        return
    if _PHRASE_CAPTURE_RE.search(text):
        rest = _PHRASE_CAPTURE_RE.sub("", text).strip().lstrip("-—:,").strip()
        if "|" in rest:
            # Full pipe syntax inline — save directly
            await _handle_phrase_save(update, rest)
        elif rest:
            # German provided inline, no English — use LLM to translate (same as voice)
            _phrase_capture_mode[update.message.chat_id] = {"mode": "text", "retries": 0}
            await _handle_phrase_capture_input(update, rest)
        else:
            # Bare trigger — enter text capture mode, prompt for the phrase
            _phrase_capture_mode[update.message.chat_id] = {"mode": "text", "retries": 0}
            await update.message.reply_text("Ready — type the German phrase.")
        return

    # Active drill intercepts all input (except control words, list commands, and new drill starts)
    # Lazy reload from disk in case bot restarted after state was saved but before this message arrived.
    if update.message.chat_id not in _active_drills:
        _load_drill_state()
    if update.message.chat_id in _active_drills:
        if _DRILL_CTL_RE.search(text):
            await _handle_drill_control(update, text)
        elif _SESSION_RE.search(text):
            # Session request exits the drill and starts a new session
            _active_drills.pop(update.message.chat_id, None)
            _save_drill_state()
            intent = _resolve_keyword_intent(text, KEYWORD_MAP)
            if intent:
                persona_name, scenario = intent
                await _start_keyword_session(update, persona_name, scenario)
            else:
                await _handle_german_command(update, "!german session")
        elif _DRILL_LIST_RE.search(text):
            await _handle_drill_list(update)
        elif _DRILL_MORE_RE.search(text):
            await _handle_drill_list_more(update)
        elif _drill_list_state.get(update.message.chat_id) and _LIST_SELECT_RE.match(text.strip()):
            await _handle_drill_list_select(update, text)
        elif _DRILL_RE.search(text):
            await _handle_drill(update, text)
        elif _SKIP_LESSON_RE.search(text):
            await _handle_skip_lesson(update)
        else:
            await _handle_drill_answer(update, text)
        return

    if text.startswith("---SESSION---") or text.lower().startswith("\u2014session\u2014"):
        await _handle_german_transcript(update, text)
    elif text.lower().startswith("!german"):
        await _handle_german_command(update, text)
    elif _WRITING_RE.search(text):
        await _handle_german_command(update, "!german writing")
    elif _SESSION_RE.search(text):
        # Keyword in same message takes priority over generic session trigger
        intent = _resolve_keyword_intent(text, KEYWORD_MAP)
        if intent:
            persona_name, scenario = intent
            await _start_keyword_session(update, persona_name, scenario)
        else:
            await _handle_german_command(update, "!german session")
    elif _DRILL_LIST_RE.search(text):
        await _handle_drill_list(update)
    elif _DRILL_MORE_RE.search(text):
        await _handle_drill_list_more(update)
    elif _drill_list_state.get(update.message.chat_id) and _LIST_SELECT_RE.match(text.strip()):
        await _handle_drill_list_select(update, text)
    elif _DRILL_AGAIN_RE.search(text) and update.message.chat_id in _last_drills:
        await _restart_last_drill(update)
    elif _SKIP_LESSON_RE.search(text):
        await _handle_skip_lesson(update)
    elif _AGAIN_RE.search(text):
        persona = _last_session_persona()
        if persona:
            scenario = (KEYWORD_MAP.get(persona, {}).get("default_scenario", "") or "")
            await _start_repeat_session(update, persona, scenario)
        else:
            await update.message.reply_text("⚠️ No previous session found to repeat.")
    elif _CONJUGATE_RE.search(text):
        verb = _CONJUGATE_RE.search(text).group(1)
        await _handle_conjugate(update, verb)
    elif _DRILL_RE.search(text):
        await _handle_drill(update, text)
    elif _DRILL_CTL_RE.search(text):
        await _handle_drill_control(update, text)
    else:
        # Auto-detect phrase practice: if text fuzzy-matches a phrasebook entry, score it directly
        _book = _load_phrasebook()
        _phrases = _book.get("phrases", [])
        if _phrases:
            from difflib import SequenceMatcher
            import re as _re2
            def _pnorm(s): return _re2.sub(r"[.,!?;:\-–]", "", s.lower()).strip()
            _best = max(_phrases, key=lambda p: SequenceMatcher(None, _pnorm(text), _pnorm(p["german"])).ratio())
            if SequenceMatcher(None, _pnorm(text), _pnorm(_best["german"])).ratio() >= 0.70:
                _phrase_practice[update.message.chat_id] = {"phrase_id": _best["id"]}
                await _handle_phrase_practice_answer(update, text)
                return

        intent = _resolve_keyword_intent(text, KEYWORD_MAP)
        if intent:
            persona_name, scenario = intent
            await _start_keyword_session(update, persona_name, scenario)
        elif KEYWORD_MAP:
            personas_list = ", ".join(KEYWORD_MAP.keys())
            await update.message.reply_text(
                f"Not sure what you mean. Try:\n"
                f"  • 'german session' — next scheduled lesson\n"
                f"  • 'german café session' — jump to a specific persona\n"
                f"Available: {personas_list}"
            )
        else:
            await update.message.reply_text(
                "Got it — I hear you. Send !german status to test the pipeline."
            )


async def handle_voice_polling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages in polling mode: transcribe → echo → route."""
    if not update.message or not update.message.voice:
        return
    if update.message.chat_id != ROBERT_CHAT_ID:
        return

    voice = update.message.voice
    if voice.duration > 120:
        await update.message.reply_text("⚠️ Voice note too long (>2 min). Ignored.")
        return

    try:
        tg_file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await tg_file.download_as_bytearray())
        transcription = transcribe_voice(audio_bytes)
    except Exception as e:
        await update.message.reply_text(f"❌ Transcription failed: {e}")
        return

    await update.message.reply_text(f'🎙 "{transcription}"')

    text = transcription.strip()

    # Send early acknowledgment for commands that run slow subprocesses (session generation takes 30–120s)
    if _SESSION_RE.search(text) or _WRITING_RE.search(text) or text.lower().startswith("!german session"):
        await update.message.reply_text("⏳ Building your session — hang on…")

    # Phrase save confirmation intercept (voice yes/no counts too)
    if update.message.chat_id in _phrase_save_pending and not text.lower().startswith("!phrase"):
        await _handle_phrase_save_confirm(update, text)
        return

    # Voice phrase capture — trigger detection (ends any active drill first)
    if _PHRASE_CAPTURE_RE.search(text) and update.message.chat_id not in _phrase_capture_mode:
        if update.message.chat_id in _active_drills:
            _active_drills.pop(update.message.chat_id, None)
            _save_drill_state()
        await _handle_phrase_capture_trigger(update)
        return

    # Voice phrase capture — second note is the phrase itself
    if update.message.chat_id in _phrase_capture_mode:
        if text.lower().strip() in {"cancel", "abbrechen", "stop"}:
            _phrase_capture_mode.pop(update.message.chat_id, None)
            await update.message.reply_text("Capture cancelled.")
        else:
            await _handle_phrase_capture_input(update, text)
        return

    # Voice triggers for phrase list / more / practice
    if _PHRASE_LIST_VOICE_RE.search(text):
        _phrase_list_offset[update.message.chat_id] = 0
        await _handle_phrase_list(update, "")
        return
    if _PHRASE_LIST_MORE_VOICE_RE.search(text):
        await _handle_phrase_list_more(update)
        return
    # Bare "more"/"next" — route to phrase list continuation if phrase was shown last
    if _DRILL_MORE_RE.search(text) and _last_list_type.get(update.message.chat_id) == 'phrase':
        await _handle_phrase_list_more(update)
        return
    _pm = _PHRASE_PRACTICE_VOICE_RE.search(text)
    if _pm:
        _id_str = _parse_spoken_id(text[_pm.end():].strip())
        await _handle_phrase_practice(update, _id_str)
        return
    # Phrase ID shortcut: "#010" (unlikely via voice but handle anyway)
    _vpid_m = re.search(r'#(\d{1,3})\b', text)
    if _vpid_m:
        await _handle_phrase_practice(update, f"{int(_vpid_m.group(1)):03d}")
        return

    # Phrase practice intercept (voice answers count too)
    if update.message.chat_id in _phrase_practice and not text.lower().startswith("!phrase"):
        await _handle_phrase_practice_answer(update, text)
        return

    # Auto-detect phrase practice from free voice (mirrors text handler logic)
    _vbook = _load_phrasebook()
    _vphrases = _vbook.get("phrases", [])
    if _vphrases and update.message.chat_id not in _active_drills:
        from difflib import SequenceMatcher
        import re as _re3
        def _vpnorm(s): return _re3.sub(r"[.,!?;:\-–]", "", s.lower()).strip()
        _vbest = max(_vphrases, key=lambda p: SequenceMatcher(None, _vpnorm(text), _vpnorm(p["german"])).ratio())
        if SequenceMatcher(None, _vpnorm(text), _vpnorm(_vbest["german"])).ratio() >= 0.70:
            _phrase_practice[update.message.chat_id] = {"phrase_id": _vbest["id"]}
            await _handle_phrase_practice_answer(update, text)
            return

    # Active drill intercepts voice answers too; lazy reload handles bot-restart state loss.
    if update.message.chat_id not in _active_drills:
        _load_drill_state()
    if update.message.chat_id in _active_drills:
        if _DRILL_CTL_RE.search(text):
            await _handle_drill_control(update, text)
        elif _SESSION_RE.search(text):
            # Session request exits the drill and starts a new session
            _active_drills.pop(update.message.chat_id, None)
            _save_drill_state()
            intent = _resolve_keyword_intent(text, KEYWORD_MAP)
            if intent:
                persona_name, scenario = intent
                await _start_keyword_session(update, persona_name, scenario)
            else:
                await _handle_german_command(update, "!german session")
        elif _DRILL_LIST_RE.search(text):
            await _handle_drill_list(update)
        elif _DRILL_MORE_RE.search(text):
            await _handle_drill_list_more(update)
        elif _drill_list_state.get(update.message.chat_id) and _LIST_SELECT_RE.match(text.strip()):
            await _handle_drill_list_select(update, text)
        elif _DRILL_RE.search(text):
            await _handle_drill(update, text)
        elif _SKIP_LESSON_RE.search(text):
            await _handle_skip_lesson(update)
        else:
            await _handle_drill_answer(update, text)
        return

    if text.startswith("---SESSION---") or text.lower().startswith("—session—"):
        await _handle_german_transcript(update, text)
    elif text.lower().startswith("!german"):
        await _handle_german_command(update, text)
    elif _WRITING_RE.search(text):
        await _handle_german_command(update, "!german writing")
    elif _SESSION_RE.search(text):
        # Keyword in same message takes priority over generic session trigger
        intent = _resolve_keyword_intent(text, KEYWORD_MAP)
        if intent:
            persona_name, scenario = intent
            await _start_keyword_session(update, persona_name, scenario)
        else:
            await _handle_german_command(update, "!german session")
    elif _DRILL_LIST_RE.search(text):
        await _handle_drill_list(update)
    elif _DRILL_MORE_RE.search(text):
        await _handle_drill_list_more(update)
    elif _drill_list_state.get(update.message.chat_id) and _LIST_SELECT_RE.match(text.strip()):
        await _handle_drill_list_select(update, text)
    elif _DRILL_AGAIN_RE.search(text) and update.message.chat_id in _last_drills:
        await _restart_last_drill(update)
    elif _SKIP_LESSON_RE.search(text):
        await _handle_skip_lesson(update)
    elif _AGAIN_RE.search(text):
        persona = _last_session_persona()
        if persona:
            scenario = (KEYWORD_MAP.get(persona, {}).get("default_scenario", "") or "")
            await _start_repeat_session(update, persona, scenario)
        else:
            log_voice_note(transcription)
    elif _CONJUGATE_RE.search(text):
        verb = _CONJUGATE_RE.search(text).group(1)
        await _handle_conjugate(update, verb)
    elif _DRILL_RE.search(text):
        await _handle_drill(update, text)
    elif _DRILL_CTL_RE.search(text):
        await _handle_drill_control(update, text)
    else:
        intent = _resolve_keyword_intent(text, KEYWORD_MAP)
        if intent:
            persona_name, scenario = intent
            await _start_keyword_session(update, persona_name, scenario)
        else:
            log_voice_note(transcription)


MAX_UPLOAD_BYTES = 50 * 1024  # 50KB

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle .txt file uploads — read content, enforce 50KB limit, feed transcript pipeline."""
    if not update.message or not update.message.document:
        return
    if update.message.chat_id != ROBERT_CHAT_ID:
        return

    doc = update.message.document
    if doc.file_size and doc.file_size > MAX_UPLOAD_BYTES:
        await update.message.reply_text(
            "⚠️ File too large (max 50KB). Paste the text directly instead."
        )
        return

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        raw_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        await update.message.reply_text(f"❌ Could not download file: {e}")
        return

    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        await update.message.reply_text(
            "⚠️ File too large (max 50KB). Paste the text directly instead."
        )
        return

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        await update.message.reply_text("⚠️ File must be UTF-8 encoded plain text.")
        return

    await _handle_german_transcript(update, text)


def register_german_handlers(app, include_text: bool = True) -> None:
    """Register the German-domain message handlers on a python-telegram-bot Application.

    Shared by run_bot_mode() (legacy minimoi_cmd_bot on the Mac) and by
    telegram_system_bot.py (minimoi_system_bot on AWS) so both bots expose
    identical German behaviour: natural-language + !german commands, voice
    practice, phrasebook capture/practice, drills, sessions, and the transcript
    pipeline.

    include_text=False lets a caller install its own TEXT handler — the system
    bot intercepts !ops first, then delegates the remainder to handle_text_message.
    Restores persisted drill state so an in-progress drill survives a restart.
    """
    if include_text:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_polling))
    app.add_handler(MessageHandler(filters.Document.TXT, handle_document))
    _load_drill_state()
    _load_drill_list_state()


def run_bot_mode():
    """Run persistent bot for button callbacks and commands"""
    token = get_polling_token()

    if not token:
        print("❌ No polling token found (keyring 'telegram/polling_bot_token' or TELEGRAM_POLLING_BOT_TOKEN)")
        return

    # Startup conflict check: fail fast if another process is already polling this token
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"timeout": 0},
            timeout=5,
        )
        if resp.status_code == 409:
            print(
                "❌ FATAL: Another process is already polling this bot token.\n"
                "Only one poller is allowed per token (see ARCHITECTURE.md).\n"
                "Check for other running telegram_bot.py or OpenClaw processes.\n"
                "Exiting.",
                file=sys.stderr,
            )
            sys.exit(1)
    except requests.RequestException as e:
        print(f"⚠️  Startup conflict check failed (network error): {e}", file=sys.stderr)

    print("🤖 Unified Telegram bot starting...")
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=30.0,   # wait up to 30s for DNS + TCP (default is 5s)
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    app = Application.builder().token(token).request(request).get_updates_request(request).build()
    
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    register_german_handlers(app)

    async def error_handler(update, context):
        """Suppress noisy network errors — log one line instead of full traceback."""
        err = context.error
        if isinstance(err, (NetworkError, TimedOut)):
            print(f"⚠️  Network error (will retry): {err.__class__.__name__}: {err}")
        else:
            print(f"❌ Bot error: {err.__class__.__name__}: {err}")

    app.add_error_handler(error_handler)

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
                    # Thread voice handling — Whisper can take 10-30s, must return 200 fast
                    threading.Thread(
                        target=handle_voice_message,
                        args=(msg, token),
                        daemon=True
                    ).start()
                elif 'text' in msg and msg['text'].startswith('/'):
                    handle_webhook_command(msg, token)
                elif 'text' in msg:
                    handle_webhook_text_message(msg, token)
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

def handle_webhook_text_message(message, token):
    """Route plain text — dispatch transcript or generic ack."""
    chat_id = str(message['chat']['id'])
    text = message.get('text', '')
    if text.startswith('---SESSION---'):
        threading.Thread(
            target=_handle_webhook_german_transcript,
            args=(text, chat_id, token),
            daemon=True
        ).start()
    else:
        send_message(token, chat_id,
            "✅ Got it. Commands: /status /run /briefing\n"
            "Or send a voice note to run curator commands.")


def _handle_webhook_german_transcript(text, chat_id, token):
    """Pass full message text to parse_transcript.py via subprocess (webhook mode)."""
    import tempfile
    send_message(token, chat_id, "⏳ Parsing German session transcript...")
    german_dir = BASE_DIR / 'domains' / 'german'
    sessions_dir = german_dir / 'data' / 'sessions'
    sessions_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                     delete=False, encoding='utf-8') as f:
        f.write(text)
        tmp = f.name
    try:
        result = subprocess.run(
            ['python3', str(german_dir / 'parse_transcript.py'),
             '--input', tmp,
             '--base-dir', str(german_dir / 'data')],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            send_message(token, chat_id, f"✅ {result.stdout.strip()}")
        else:
            send_message(token, chat_id,
                f"❌ Transcript parse failed:\n{result.stderr.strip()[:300]}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def handle_webhook_command(message, token):
    """Process commands from webhook"""
    chat_id = message['chat']['id']
    text = message['text']
    
    if text == '/briefing':
        send_briefing(token, str(chat_id))
    elif text == '/reset':
        send_message(token, str(chat_id),
            "♻️ Webhook mode is stateless — no session to reset here.\n"
            "Send 'reset session' to OpenClaw in Telegram to compact its polling session.")
    elif text == '/status':
        log = BASE_DIR / 'logs' / 'curator_launchd.log'
        if log.exists():
            lines = [l for l in log.read_text().splitlines() if l.strip()]
            last_run = lines[-1][-80:] if lines else "no entries yet"
            last_lines = '\n'.join(lines[-5:])
            send_message(token, str(chat_id),
                f"📊 Status\n"
                f"Mode: webhook (stateless — no session cost)\n"
                f"Last run: {last_run}\n\n"
                f"<pre>{escape(last_lines)}</pre>", parse_mode="HTML")
        else:
            send_message(token, str(chat_id), "📊 Webhook mode active. No curator log yet.")
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
    """Send answerCallbackQuery to Telegram with retry on network error"""
    for attempt in range(1, 3):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                json={"callback_query_id": query_id, "text": text, "show_alert": alert},
                timeout=5
            )
            return
        except Exception as e:
            print(f"⚠️  answer_callback attempt {attempt}/2 failed: {e}")
            if attempt < 2:
                import time; time.sleep(2)
    print("❌ answer_callback failed after 2 attempts — feedback still recorded")

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
