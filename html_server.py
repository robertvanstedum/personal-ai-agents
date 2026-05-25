"""
html_server.py — German language HTML interface.

Both telegram_bot.py and html_server.py import from german_domain.
Run: venv/bin/python3 html_server.py
     PORT=8767 venv/bin/python3 html_server.py
"""

import json
import os
from pathlib import Path
from flask import Flask, render_template, redirect, request, jsonify
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
)

BASE_DIR = Path(__file__).parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400  # cache static files for 1 day


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
                           personas=personas, sessions=sessions)


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8767))
    app.run(host="localhost", port=port, debug=True)


if __name__ == "__main__":
    main()
