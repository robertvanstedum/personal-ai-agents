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
)

BASE_DIR = Path(__file__).parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("german_landing.html", active="landing")


@app.route("/lesen")
def lesen():
    articles = get_lesen_pool()
    return render_template("german_lesen.html", active="lesen", articles=articles)


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


@app.route("/ueben")
def ueben():
    return render_template("german_ueben.html", active="ueben")


@app.route("/woerter")
def woerter():
    entries = get_phrasebook_entries()
    return render_template("german_woerter.html", active="woerter", entries=entries)


@app.route("/bibliothek")
def bibliothek():
    return redirect("/woerter", code=301)


@app.route("/admin")
def admin():
    return render_template("german_admin.html", active="admin")


@app.route("/archiv")
def archiv():
    return render_template("german_archiv.html", active="archiv")


# ── Lesen API ─────────────────────────────────────────────────────────────────

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8767))
    app.run(host="localhost", port=port, debug=True)


if __name__ == "__main__":
    main()
