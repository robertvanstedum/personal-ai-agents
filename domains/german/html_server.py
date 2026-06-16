"""
html_server.py — German language HTML interface.

Both telegram_bot.py and html_server.py import from german_domain.
Run: venv/bin/python3 html_server.py
     PORT=8767 venv/bin/python3 html_server.py
"""

import json
import os
import secrets
from pathlib import Path
from flask import Flask, render_template, redirect, request, jsonify, session
from flask_cors import CORS
from german_domain import (
    GERMAN_DIR,
    DEFAULT_USER,
    get_lesen_pool,
    refresh_lesen_feed,
    lesen_action,
    translate_phrase,
    save_lesen_phrase,
    get_tagebuch_prompts,
    correct_writing,
    save_writing_entry,
    save_note,
    get_phrasebook_entries,
    update_phrase_status,
    get_personas,
    get_drill_pool,
    save_drill_result,
    analyse_session,
    get_gesprache_sessions,
    persona_to_slug,
    get_persona_memory,
    close_round,
    extend_round,
    assemble_session_prompt,
    build_session_brief,
    build_tutor_brief,
    get_last_human_session_suggestion,
    ROBERT_CHAT_ID,
)

BASE_DIR  = Path(__file__).parent           # domains/german/
REPO_ROOT = BASE_DIR.parent.parent          # repo root

# ── Tutor / Whereby config ────────────────────────────────────────────────────
# Guest URL — safe to show in UI and share with conversation partner
WHEREBY_ROOM_URL  = os.environ.get("WHEREBY_ROOM_URL", "https://whereby.com/roberts-german")
# Host URL — includes roomKey JWT; never rendered in templates or client-side JS.
# Accessed only server-side via /api/whereby-join redirect.
WHEREBY_HOST_URL  = os.environ.get("WHEREBY_HOST_URL", "")
# Where tutor brief tokens are persisted (readable/writable by html_server.py)
PORTAL_AUTH_DIR   = REPO_ROOT / "minimoi_portal" / "auth"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400  # cache static files for 1 day

# ── Persistent Flask secret key (needed for session during OAuth flow) ────────
_secret_key_file = REPO_ROOT / ".flask_secret"
if _secret_key_file.exists():
    app.secret_key = _secret_key_file.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    _secret_key_file.write_text(app.secret_key)

# ── Google Drive OAuth config ─────────────────────────────────────────────────
GOOGLE_SCOPES        = ["https://www.googleapis.com/auth/drive.readonly"]
GOOGLE_REDIRECT_URI  = "http://localhost:8767/api/google/callback"


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("german_landing.html", active="landing")


@app.route("/lesen")
def lesen():
    return render_template("german_lesen.html", active="lesen")


@app.route("/schreiben")
def schreiben():
    sessions_file = GERMAN_DIR / "config" / "writing_sessions.json"
    sessions = []
    if sessions_file.exists():
        try:
            data = json.loads(sessions_file.read_text())
            sessions = list(reversed(data.get("entries", [])[-10:]))
        except Exception:
            pass
    prompts = get_tagebuch_prompts()
    return render_template("german_schreiben.html", active="schreiben",
                           sessions=sessions, tagebuch_prompts=prompts)


@app.route("/gesprache")
def gesprache():
    personas = get_personas()
    for p in personas:
        slug = persona_to_slug(p["name"])
        p["slug"] = slug
        p["memory"] = get_persona_memory(DEFAULT_USER, slug)
    sessions = get_gesprache_sessions(limit=5)
    return render_template("german_gesprache.html", active="gesprache",
                           personas=personas, sessions=sessions,
                           whereby_room_url=WHEREBY_ROOM_URL,
                           whereby_host_available=bool(WHEREBY_HOST_URL))


@app.route("/ueben")
def ueben_redirect():
    return redirect("/gesprache", code=301)


@app.route("/woerter")
def woerter():
    entries = get_phrasebook_entries()
    drill_pool = get_drill_pool()
    return render_template("german_woerter.html", active="woerter",
                           entries=entries, drill_pool=drill_pool)


@app.route("/bibliothek")
def bibliothek():
    return redirect("/woerter", code=301)


@app.route("/admin")
def admin():
    personas = get_personas()
    persona_rounds = []
    for p in personas:
        slug = persona_to_slug(p["name"])
        mem = get_persona_memory(DEFAULT_USER, slug)
        persona_rounds.append({
            "name": p["name"],
            "emoji": p.get("emoji", ""),
            "slug": slug,
            "current_round": mem.get("current_round", 1),
            "round_default": mem.get("round_default", 5),
            "sessions_this_round": mem.get("active_memory", {}).get("sessions_this_round", 0),
            "ready_to_archive": mem.get("ready_to_archive", False),
        })
    return render_template("german_admin.html", active="admin", persona_rounds=persona_rounds)


@app.route("/archiv")
def archiv():
    sessions_file = GERMAN_DIR / "config" / "writing_sessions.json"
    writing_sessions = []
    if sessions_file.exists():
        try:
            data = json.loads(sessions_file.read_text())
            writing_sessions = list(reversed(data.get("entries", [])))[:50]
        except Exception:
            pass
    gesprache_sessions = get_gesprache_sessions(limit=50)
    return render_template("german_archiv.html", active="archiv",
                           gesprache_sessions=gesprache_sessions,
                           writing_sessions=writing_sessions)


# ── Lesen API ─────────────────────────────────────────────────────────────────

@app.route("/api/lesen-category")
def api_lesen_category():
    category = request.args.get("category") or None
    articles = get_lesen_pool(category=category)
    return jsonify({"articles": articles})


@app.route("/api/lesen-refresh", methods=["POST"])
def api_lesen_refresh():
    result = refresh_lesen_feed()
    return jsonify(result)


@app.route("/api/lesen-action", methods=["POST"])
def api_lesen_action():
    body = request.get_json(force=True)
    article_id = body.get("article_id", "")
    action = body.get("action", "")
    if not article_id or action not in ("pos", "neg", "pin", "unpin"):
        return jsonify({"ok": False, "error": "invalid params"}), 400
    lesen_action(article_id, action)
    return jsonify({"ok": True})


@app.route("/api/translate", methods=["POST"])
def api_translate():
    body = request.get_json(force=True)
    phrase = body.get("phrase", "").strip()
    if not phrase:
        return jsonify({"translation": "", "cached": False})
    translation, cached, timing = translate_phrase(phrase)
    return jsonify({"translation": translation, "cached": cached, "timing": timing})


@app.route("/api/save-phrase", methods=["POST"])
def api_save_phrase():
    body = request.get_json(force=True)
    german = body.get("german", "").strip()
    english = body.get("english", "").strip()
    context_sentence = body.get("context_sentence", "")
    article_title = body.get("article_title", "")
    if not german:
        return jsonify({"ok": False, "error": "german required"}), 400
    entry = save_lesen_phrase(german, english, context_sentence, article_title)
    return jsonify({"ok": True, "id": entry["id"]})


# ── Schreiben API ─────────────────────────────────────────────────────────────

@app.route("/api/write-correct", methods=["POST"])
def api_write_correct():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    context = body.get("context", "")
    if not text:
        return jsonify({"corrected": "", "notes": []}), 400
    result = correct_writing(text, context)
    return jsonify(result)


@app.route("/api/write-save", methods=["POST"])
def api_write_save():
    body = request.get_json(force=True)
    entry = save_writing_entry(
        mode=body.get("mode", "tagebuch"),
        text_original=body.get("text_original", ""),
        text_corrected=body.get("text_corrected", ""),
        notes=body.get("notes", []),
        context_title=body.get("context_title", ""),
    )
    return jsonify({"ok": True, "id": entry["id"]})


@app.route("/api/note-save", methods=["POST"])
def api_note_save():
    body = request.get_json(force=True)
    note = save_note(
        article_id=body.get("article_id", ""),
        article_title=body.get("article_title", ""),
        original=body.get("original", ""),
        corrected=body.get("corrected", ""),
        rewritten=body.get("rewritten", ""),
    )
    return jsonify({"success": True, "note_id": note["note_id"]})


# ── Üben API ─────────────────────────────────────────────────────────────────

@app.route("/api/drill-result", methods=["POST"])
def api_drill_result():
    body = request.get_json(force=True)
    phrase_id = body.get("phrase_id", "")
    result = body.get("result", "")
    if not phrase_id or result not in ("correct", "wrong", "skip"):
        return jsonify({"ok": False, "error": "invalid params"}), 400
    entry = save_drill_result(phrase_id, result)
    return jsonify({"ok": bool(entry), "entry": entry})


# ── Wörter API ───────────────────────────────────────────────────────────────

@app.route("/api/phrase-update", methods=["POST"])
def api_phrase_update():
    body = request.get_json(force=True)
    phrase_id = body.get("id", "")
    new_status = body.get("status", "")
    if not phrase_id or not new_status:
        return jsonify({"ok": False, "error": "id and status required"}), 400
    ok = update_phrase_status(phrase_id, new_status)
    return jsonify({"ok": ok})


@app.route("/api/anki-export")
def api_anki_export():
    source = request.args.get("source") or None
    status = request.args.get("status") or None
    entries = get_phrasebook_entries(source=source, status=status)
    lines = ["German\tEnglish\tContext"]
    for e in entries:
        german = e.get("german", "").replace("\t", " ")
        english = e.get("english", "").replace("\t", " ")
        context = e.get("source_sentence", "").replace("\t", " ")[:200]
        lines.append(f"{german}\t{english}\t{context}")
    tsv = "\n".join(lines)
    from flask import Response
    return Response(
        tsv,
        mimetype="text/tab-separated-values",
        headers={"Content-Disposition": "attachment; filename=mein-deutsch-anki.tsv"}
    )


# ── Gespräche API ─────────────────────────────────────────────────────────────

@app.route("/api/analyse-transcript", methods=["POST"])
def api_analyse_transcript():
    body = request.get_json(force=True)
    transcript = body.get("transcript", "").strip()
    persona_name = body.get("persona_name", "").strip()
    scene = body.get("scene", "").strip()
    if not transcript:
        return jsonify({"ok": False, "error": "transcript required"}), 400
    result = analyse_session(transcript, persona_name, scene)
    return jsonify({"ok": True, **result})


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file"}), 400
    audio_file = request.files["audio"]
    try:
        import keyring as _kr, time as _t
        from openai import OpenAI as _OAI
        api_key = _kr.get_password("openai", "api_key")
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        client = _OAI(api_key=api_key)
        audio_file.stream.seek(0)
        _t0 = _t.time()
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=("session.webm", audio_file.stream, "audio/webm"),
            language="de",
            response_format="text",
        )
        print(f"[TIMING] transcribe_ms={int((_t.time()-_t0)*1000)}", flush=True)
        return jsonify({"transcript": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/review", methods=["POST"])
def api_review():
    body = request.get_json(force=True)
    transcript  = body.get("transcript", "").strip()
    model       = body.get("model", "grok").strip()
    persona_name = body.get("persona_name", "").strip()
    scene       = body.get("scene", "").strip()
    if not transcript:
        return jsonify({"ok": False, "error": "transcript required"}), 400
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from providers.review_router import run_review, ProviderError
        result = run_review(transcript, persona_name, scene, model)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "model": model}), 502


@app.route("/api/gesprache/ai-turn", methods=["POST"])
def gesprache_ai_turn():
    data      = request.get_json(force=True)
    history   = data.get("history", [])
    persona   = data.get("persona", "").strip()
    scene     = data.get("scene", "").strip()
    model     = data.get("model", "grok").strip()
    user_turn = data.get("user_turn", "")
    if not persona:
        return jsonify({"ok": False, "error": "persona required"}), 400
    try:
        import sys, os, time as _t
        sys.path.insert(0, os.path.dirname(__file__))
        from providers.review_router import run_chat_turn, ProviderError
        _t0 = _t.time()
        response = run_chat_turn(history, persona, scene, user_turn, model)
        print(f"[TIMING] ai_turn_ms={int((_t.time()-_t0)*1000)} model={model}", flush=True)
        return jsonify({"ok": True, "response": response, "model": model})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "model": model}), 502


@app.route("/api/gesprache/speak", methods=["POST"])
def gesprache_speak():
    data  = request.get_json(force=True)
    text  = data.get("text", "").strip()
    voice = data.get("voice", "nova")   # nova/shimmer = female, onyx/echo = male
    if not text:
        return jsonify({"error": "text required"}), 400
    if voice not in ("alloy", "echo", "fable", "nova", "onyx", "shimmer"):
        voice = "nova"
    try:
        import keyring as _kr
        from openai import OpenAI as _OAI
        from flask import Response as _Resp
        api_key = _kr.get_password("openai", "api_key")
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        client = _OAI(api_key=api_key, timeout=12.0)
        import time as _t
        _t0 = _t.time()
        resp = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
        )
        print(f"[TIMING] tts_ms={int((_t.time()-_t0)*1000)} chars={len(text)}", flush=True)
        return _Resp(resp.content, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/round-action", methods=["POST"])
def api_round_action():
    body = request.get_json(force=True)
    persona_slug = body.get("persona_slug", "").strip()
    action = body.get("action", "").strip()
    if not persona_slug or action not in ("close", "extend"):
        return jsonify({"ok": False, "error": "invalid params"}), 400
    if action == "close":
        close_round(DEFAULT_USER, persona_slug)
    elif action == "extend":
        extend_round(DEFAULT_USER, persona_slug)
    return jsonify({"ok": True})


@app.route("/api/gesprache-sessions")
def api_gesprache_sessions():
    limit = min(int(request.args.get("limit", 5)), 20)
    sessions = get_gesprache_sessions(limit=limit)
    return jsonify(sessions)


@app.route("/api/persona-prompt")
def api_persona_prompt():
    persona_slug = request.args.get("persona", "")
    scene = request.args.get("scene", "")

    personas = get_personas()
    persona = next((p for p in personas if persona_to_slug(p["name"]) == persona_slug), None)
    if not persona:
        return jsonify({"error": "persona not found"}), 404

    memory = get_persona_memory(DEFAULT_USER, persona_slug)
    prompt = assemble_session_prompt(persona, scene, memory)
    brief = build_session_brief(persona, scene, memory)

    return jsonify({"prompt": prompt, "session_brief": brief})


# ── Send Grok Voice prompt to Telegram ───────────────────────────────────────

@app.route("/api/send-to-telegram", methods=["POST"])
def api_send_to_telegram():
    """Assemble the full Grok Voice system prompt and send it to Robert's
    Telegram chat via the bot. On mobile, Robert can copy the message and
    paste it directly into Grok Voice — no clipboard transfer needed."""
    import keyring
    import requests as _requests

    data = request.json or {}
    persona_name = data.get("persona_name", "")
    scene_key    = data.get("scene_key", "")

    personas = get_personas()
    persona  = next((p for p in personas if p["name"] == persona_name), None)
    if not persona:
        return jsonify({"ok": False, "error": "persona not found"}), 404

    memory = get_persona_memory(DEFAULT_USER, persona_to_slug(persona_name))
    prompt = assemble_session_prompt(persona, scene_key, memory)

    # Header line so it's easy to identify in the chat
    scene_label = scene_key.replace("_", " ").title() if scene_key else ""
    header = f"🎙 Grok Voice — {persona_name}"
    if scene_label:
        header += f" / {scene_label}"
    message = f"{header}\n\n{prompt}"

    # Telegram message limit is 4096 chars
    if len(message) > 4096:
        message = message[:4090] + "\n…"

    # rvsopenbot (bot_token) is the right choice here — it sends but does not poll,
    # so it has no message handler that could misinterpret the system prompt as a command.
    # minimoi_cmd_bot (polling_bot_token) is deliberately excluded: its message handler
    # would receive the prompt and attempt to match it against German session triggers.
    bot_token = keyring.get_password("telegram", "bot_token")
    if not bot_token:
        return jsonify({"ok": False, "error": "bot token not configured"}), 500

    resp = _requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": ROBERT_CHAT_ID, "text": message},
        timeout=10,
    )

    if resp.ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": resp.text}), 502


# ── Whereby host join — redirect only, host URL never exposed to client ───────

@app.route("/api/whereby-join")
def whereby_join():
    """Server-side redirect to Whereby host URL. Keeps the roomKey JWT off the client."""
    from flask import redirect as _redir
    if not WHEREBY_HOST_URL:
        return jsonify({"error": "Kein Raum konfiguriert."}), 404
    return _redir(WHEREBY_HOST_URL)


# ── Tutor brief — token generation (owner call from UI) ──────────────────────

@app.route("/api/tutor-brief/suggestion")
def api_tutor_brief_suggestion():
    """Return a pre-fill suggestion for the session-notes textarea.
    Pulled from the most recent human_session file — next_focus or top error.
    Returns {"suggestion": ""} when no human session exists yet.
    """
    suggestion = get_last_human_session_suggestion()
    return jsonify({"suggestion": suggestion})


@app.route("/api/tutor-brief/generate", methods=["POST"])
def api_tutor_brief_generate():
    """Generate a 48h shareable token for the pre-session tutor brief.
    Accepts optional JSON body: {"session_notes": "..."}.
    Returns token, expires, and brief_text for inline preview.
    """
    import time

    token   = secrets.token_urlsafe(16)
    expires = int(time.time()) + 48 * 3600

    # Robert's typed intent — optional; empty string is valid
    body         = request.get_json(silent=True) or {}
    session_notes = body.get("session_notes", "")

    brief = build_tutor_brief(session_notes)

    PORTAL_AUTH_DIR.mkdir(parents=True, exist_ok=True)
    briefs_file = PORTAL_AUTH_DIR / "tutor_briefs.json"
    briefs = {}
    if briefs_file.exists():
        try:
            briefs = json.loads(briefs_file.read_text())
        except Exception:
            briefs = {}

    # Prune expired tokens
    now = int(time.time())
    briefs = {k: v for k, v in briefs.items() if v.get("expires", 0) > now}

    briefs[token] = {"expires": expires, "brief": brief, "created": now}
    briefs_file.write_text(json.dumps(briefs, indent=2, ensure_ascii=False))

    return jsonify({"token": token, "expires": expires, "brief_text": brief})


# ── Tutor brief — public read-only view (no login required) ──────────────────

@app.route("/tutor-brief/<token>")
def tutor_brief(token):
    """Public shareable pre-session brief for the tutor. Token expires in 48h."""
    import time

    briefs_file = PORTAL_AUTH_DIR / "tutor_briefs.json"
    if not briefs_file.exists():
        return "Brief nicht gefunden.", 404

    try:
        briefs = json.loads(briefs_file.read_text())
    except Exception:
        return "Brief nicht gefunden.", 404

    entry = briefs.get(token)
    if not entry:
        return "Brief nicht gefunden.", 404
    if entry.get("expires", 0) < int(time.time()):
        return "Dieser Brief ist abgelaufen.", 410

    brief = entry["brief"]
    return render_template("tutor_brief.html", brief=brief)


# ── Google Drive OAuth ───────────────────────────────────────────────────────

def _google_client_config():
    import keyring
    client_id     = keyring.get_password("google_oauth", "client_id")
    client_secret = keyring.get_password("google_oauth", "client_secret")
    if not client_id or not client_secret:
        return None
    return {
        "web": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def _get_google_credentials():
    """Return valid Credentials or None if not authorised."""
    import keyring
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest

    client_id     = keyring.get_password("google_oauth", "client_id")
    client_secret = keyring.get_password("google_oauth", "client_secret")
    refresh_token = keyring.get_password("google_oauth", "refresh_token")

    if not all([client_id, client_secret, refresh_token]):
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )
    creds.refresh(GRequest())
    return creds


@app.route("/api/google/status")
def api_google_status():
    import keyring
    client_id     = keyring.get_password("google_oauth", "client_id")
    refresh_token = keyring.get_password("google_oauth", "refresh_token")
    return jsonify({
        "configured": bool(client_id),
        "authed":     bool(refresh_token),
    })


@app.route("/api/google/auth")
def api_google_auth():
    from google_auth_oauthlib.flow import Flow
    cfg = _google_client_config()
    if not cfg:
        return (
            "Google OAuth credentials not configured. "
            "Store client_id and client_secret in keyring under service 'google_oauth'."
        ), 503

    flow = Flow.from_client_config(cfg, scopes=GOOGLE_SCOPES,
                                   redirect_uri=GOOGLE_REDIRECT_URI)
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.route("/api/google/callback")
def api_google_callback():
    import keyring
    from google_auth_oauthlib.flow import Flow

    cfg = _google_client_config()
    flow = Flow.from_client_config(
        cfg, scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
        state=session.get("google_oauth_state"),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    keyring.set_password("google_oauth", "refresh_token", creds.refresh_token)
    return redirect("/gesprache?tab=konversation&google_auth=ok")


@app.route("/api/google/latest-transcript")
def api_google_latest_transcript():
    from googleapiclient.discovery import build

    creds = _get_google_credentials()
    if not creds:
        return jsonify({"error": "not_authed", "auth_url": "/api/google/auth"}), 401

    service = build("drive", "v3", credentials=creds)

    # Search for the most recently modified Google Doc with "transcript" in the name
    results = service.files().list(
        q=(
            "(name contains 'transcript' or name contains 'Transcript') "
            "and mimeType='application/vnd.google-apps.document' "
            "and trashed=false"
        ),
        orderBy="modifiedTime desc",
        pageSize=5,
        fields="files(id, name, modifiedTime)",
    ).execute()

    files = results.get("files", [])
    if not files:
        return jsonify({"error": "No transcript files found in Drive."}), 404

    # Export the newest as plain text
    file_id   = files[0]["id"]
    file_name = files[0]["name"]
    content   = service.files().export(fileId=file_id, mimeType="text/plain").execute()
    text = content.decode("utf-8") if isinstance(content, bytes) else str(content)

    return jsonify({"ok": True, "text": text, "file_name": file_name})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8767))
    app.run(host="localhost", port=port, debug=True)


if __name__ == "__main__":
    main()
