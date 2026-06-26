"""
html_server.py — Portuguese language domain.

Spec 2: shell + pages.
Spec 3: Conversas — voice sessions, port of Mein Deutsch Gespräche.
Run: PORT=8770 venv/bin/python3 html_server.py
"""

import json
import os
import secrets
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask import Response as FlaskResponse
from flask_cors import CORS

BASE_DIR  = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent.parent

sys.path.insert(0, str(REPO_ROOT))
from get_secret import get_secret

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400


def _init_sentry():
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        dsn = os.environ.get("SENTRY_DSN")
        if not dsn:
            try:
                dsn = get_secret("SENTRY_DSN")
            except Exception:
                return
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get("FLASK_ENV", "production"),
        )
    except ImportError:
        pass


_init_sentry()


def _ensure_writing_sessions_notes():
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE portuguese.writing_sessions"
                " ADD COLUMN IF NOT EXISTS notes JSONB DEFAULT '[]'"
            )
    except Exception as e:
        print(f"[portuguese] migration notes column: {e}", flush=True)


_ensure_writing_sessions_notes()

_flask_secret_env = os.environ.get("FLASK_SECRET")
if _flask_secret_env:
    app.secret_key = _flask_secret_env
else:
    _secret_key_file = REPO_ROOT / ".flask_secret"
    if _secret_key_file.exists():
        app.secret_key = _secret_key_file.read_text().strip()
    else:
        app.secret_key = secrets.token_hex(32)
        _secret_key_file.write_text(app.secret_key)


# ── Personas ──────────────────────────────────────────────────────────────────

_PERSONAS_JSON = BASE_DIR / "data" / "personas.json"


def _load_personas() -> list:
    try:
        return json.loads(_PERSONAS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def _name_to_slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("-", "_")


def _persona_voice(persona: dict) -> str:
    name = persona.get("name", "").lower()
    if name in ("carlos", "lucas") or "masc" in persona.get("style", ""):
        return "onyx"
    return "nova"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_conn():
    import psycopg2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        try:
            db_url = get_secret("DATABASE_URL")
        except Exception:
            db_url = None
    db_url = db_url or "postgresql://postgres:simple123@localhost:5432/personal_agents"
    return psycopg2.connect(db_url)


def _save_session(user_id, date_str, persona, scenario, source,
                  raw_transcript, reviewer_output, model, duration_min):
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO portuguese.sessions
                   (user_id, date, persona, scenario, source,
                    raw_transcript, reviewer_output, model, duration_estimate_min)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (user_id, date_str, persona, scenario, source,
                 raw_transcript,
                 json.dumps(reviewer_output, ensure_ascii=False) if reviewer_output else None,
                 model, duration_min),
            )
            return cur.fetchone()[0]
    except Exception as e:
        print(f"[portuguese] session save error: {e}", flush=True)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_sessions(user_id, limit=5) -> list:
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            if user_id:
                cur.execute(
                    """SELECT id, date, persona, scenario, source, created_at
                       FROM portuguese.sessions
                       WHERE user_id = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    """SELECT id, date, persona, scenario, source, created_at
                       FROM portuguese.sessions
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
            rows = cur.fetchall()
        return [
            {
                "id":       r[0],
                "date":     str(r[1]),
                "persona":  r[2],
                "scenario": r[3],
                "source":   r[4],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] sessions fetch error: {e}", flush=True)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _request_user_id() -> int | None:
    hdr = request.headers.get("X-Minimoi-Auth-Id")
    if hdr:
        try:
            return int(hdr)
        except ValueError:
            pass
    return None


# ── Translation ───────────────────────────────────────────────────────────────

_deepl_client: object | None = None


def _get_deepl_client():
    global _deepl_client
    if _deepl_client is not None:
        return _deepl_client
    try:
        import deepl as _deepl
        api_key = get_secret("DEEPL_API_KEY", "deepl", "api_key")
        if not api_key:
            return None
        _deepl_client = _deepl.Translator(api_key)
        return _deepl_client
    except Exception:
        return None


def _translate_phrase(text: str) -> tuple[str, str]:
    """Returns (translation, source). Source: 'cache', 'deepl', or 'claude'."""
    key = text.lower().strip()
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT english FROM portuguese.translation_cache WHERE portuguese = %s", (key,)
            )
            row = cur.fetchone()
        if row:
            return row[0], "cache"
    except Exception:
        pass

    translation = ""
    source = ""
    try:
        translator = _get_deepl_client()
        if translator:
            result = translator.translate_text(text, source_lang="PT", target_lang="EN-US")
            translation = result.text
            source = "deepl"
    except Exception:
        global _deepl_client
        _deepl_client = None

    if not translation:
        try:
            import anthropic
            api_key = get_secret("ANTHROPIC_API_KEY", "anthropic", "api_key")
            if api_key:
                client = anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=100,
                    system="Translate the Portuguese word or phrase to English. Return only the translation, nothing else.",
                    messages=[{"role": "user", "content": text}],
                )
                translation = resp.content[0].text.strip()
                source = "claude"
        except Exception:
            pass

    if translation:
        try:
            conn = _db_conn()
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO portuguese.translation_cache (portuguese, english)"
                    " VALUES (%s, %s) ON CONFLICT (portuguese) DO NOTHING",
                    (key, translation),
                )
        except Exception:
            pass

    return translation, source


# ── Grammar correction ────────────────────────────────────────────────────────

_CORRECTION_SYSTEM = """Você é um professor de português brasileiro.
Corrija o texto do estudante e retorne SOMENTE um JSON válido:
{"corrected": "texto corrigido", "translation": "English translation", "notes": ["nota curta 1", "nota curta 2"]}
Máximo 3 notas. Seja específico e encorajador. Notes em português."""


def _run_correction(text: str) -> dict:
    try:
        import anthropic
        api_key = get_secret("ANTHROPIC_API_KEY", "anthropic", "api_key")
        if not api_key:
            return {"corrected": text, "translation": "", "notes": []}
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=_CORRECTION_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        return {
            "corrected":    parsed.get("corrected", text),
            "translation":  parsed.get("translation", ""),
            "notes":        parsed.get("notes", []),
        }
    except Exception as e:
        print(f"[portuguese] correction error: {e}", flush=True)
        return {"corrected": text, "translation": "", "notes": []}


# ── Vocabulary helpers ────────────────────────────────────────────────────────

def _get_vocabulary(user_id, source=None, status=None, limit=100) -> list:
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            clauses = []
            params: list = []
            if user_id is not None:
                clauses.append("user_id = %s")
                params.append(user_id)
            if source:
                clauses.append("source = %s")
                params.append(source)
            if status:
                clauses.append("status = %s")
                params.append(status)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur.execute(
                f"SELECT id, portuguese, english, source, source_sentence, status, added_at"
                f" FROM portuguese.vocabulary {where}"
                f" ORDER BY added_at DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "portuguese": r[1], "english": r[2],
                "source": r[3], "source_sentence": r[4],
                "status": r[5] or "library",
                "added": str(r[6])[:10] if r[6] else "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] vocabulary fetch error: {e}", flush=True)
        return []


def _get_writing_sessions(user_id, limit=10) -> list:
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, mode, original_text, corrected_text, created_at,"
                    " COALESCE(notes, '[]'::jsonb)"
                    " FROM portuguese.writing_sessions"
                    " WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, mode, original_text, corrected_text, created_at,"
                    " COALESCE(notes, '[]'::jsonb)"
                    " FROM portuguese.writing_sessions"
                    " ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "mode": r[1] or "diario",
                "text_original": r[2] or "", "text_corrected": r[3] or "",
                "timestamp": str(r[4]) if r[4] else "",
                "notes": r[5] if isinstance(r[5], list) else [],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] writing sessions fetch error: {e}", flush=True)
        return []


def _get_leitura_notes(user_id, limit=20) -> list:
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT id, article_title, original, corrected, created_at"
                    " FROM portuguese.leitura_notes"
                    " WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, article_title, original, corrected, created_at"
                    " FROM portuguese.leitura_notes"
                    " ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
        return [
            {
                "id": r[0], "article_title": r[1] or "",
                "original": r[2] or "", "corrected": r[3] or "",
                "date": str(r[4])[:10] if r[4] else "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] leitura notes fetch error: {e}", flush=True)
        return []


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("portuguese_landing.html", active="landing",
                           show_toggle=True)


@app.route("/leitura")
def leitura():
    return render_template("portuguese_leitura.html", active="leitura",
                           show_toggle=True)


@app.route("/conversas")
def conversas():
    personas = _load_personas()
    for p in personas:
        p["slug"] = _name_to_slug(p["name"])
    sessions = _get_sessions(_request_user_id(), limit=5)
    return render_template(
        "portuguese_conversas.html",
        active="conversas",
        show_toggle=True,
        personas=personas,
        sessions=sessions,
    )


@app.route("/escrita")
def escrita():
    user_id = _request_user_id()
    writing_sessions = _get_writing_sessions(user_id, limit=5)
    return render_template("portuguese_escrita.html", active="escrita",
                           show_toggle=True, writing_sessions=writing_sessions)


@app.route("/palavras")
def palavras():
    user_id = _request_user_id()
    entries = _get_vocabulary(user_id, limit=100)
    drill_pool = _get_vocabulary(user_id, status="practice", limit=50)
    return render_template("portuguese_palavras.html", active="palavras",
                           show_toggle=True, entries=entries, drill_pool=drill_pool)


@app.route("/arquivo")
def arquivo():
    user_id = _request_user_id()
    conversas_sessions = _get_sessions(user_id, limit=10)
    writing_sessions = _get_writing_sessions(user_id, limit=10)
    leitura_notes = _get_leitura_notes(user_id, limit=20)
    return render_template("portuguese_arquivo.html", active="arquivo",
                           show_toggle=True,
                           conversas_sessions=conversas_sessions,
                           writing_sessions=writing_sessions,
                           leitura_notes=leitura_notes)


@app.route("/admin")
def admin():
    return render_template("portuguese_admin.html", active="admin",
                           show_toggle=True)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "portuguese"})


# ── API: Leitura stubs ───────────────────────────────────────────────────────

@app.route("/api/pt/leitura-category")
def api_pt_leitura_category():
    category = request.args.get("category", "").strip()
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, source, url, full_text, excerpt, date_fetched"
                " FROM portuguese.articles"
                " WHERE category = %s AND is_active = TRUE"
                " ORDER BY date_fetched DESC, id DESC LIMIT 30",
                (category,),
            )
            rows = cur.fetchall()
        articles = [
            {
                "id": r[0], "title": r[1], "source": r[2] or "",
                "url": r[3] or "#",
                "summary": r[4] or r[5] or "",
                "date_fetched": str(r[6]) if r[6] else "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] leitura-category error: {e}", flush=True)
        articles = []
    return jsonify({"articles": articles, "category": category})


@app.route("/api/pt/leitura-action", methods=["POST"])
def api_pt_leitura_action():
    return jsonify({"ok": True})


@app.route("/api/pt/leitura-refresh", methods=["POST"])
def api_pt_leitura_refresh():
    return jsonify({"ok": True})


@app.route("/api/pt/translate", methods=["POST"])
def api_pt_translate():
    import time as _t
    body = request.get_json(force=True)
    phrase = (body.get("phrase") or body.get("text") or "").strip()
    if not phrase:
        return jsonify({"translation": "", "timing": {}})
    t0 = _t.time()
    translation, source = _translate_phrase(phrase)
    ms = int((_t.time() - t0) * 1000)
    print(f"[TIMING] pt_translate_ms={ms} source={source}", flush=True)
    return jsonify({"translation": translation, "timing": {"total_ms": ms, "source": source}})


@app.route("/api/pt/save-phrase", methods=["POST"])
def api_pt_save_phrase():
    body = request.get_json(force=True)
    pt   = (body.get("portuguese") or "").strip()
    en   = (body.get("english") or body.get("translation") or "").strip()
    ctx  = (body.get("context_sentence") or "").strip()
    src  = (body.get("source") or "leitura").strip()
    user_id = _request_user_id()
    if not pt:
        return jsonify({"ok": False, "error": "portuguese required"}), 400
    if not en:
        en, _ = _translate_phrase(pt)
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portuguese.vocabulary"
                " (user_id, portuguese, english, source, source_sentence)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id, portuguese) DO UPDATE SET english = EXCLUDED.english",
                (user_id, pt, en or None, src, ctx or None),
            )
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[portuguese] save-phrase error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pt/note-save", methods=["POST"])
def api_pt_note_save():
    body = request.get_json(force=True)
    user_id       = _request_user_id()
    article_id    = body.get("article_id") or None
    article_title = (body.get("article_title") or "").strip()
    original      = (body.get("original") or body.get("rewritten") or "").strip()
    corrected     = (body.get("corrected") or "").strip()
    if not original:
        return jsonify({"ok": False, "error": "original required"}), 400
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portuguese.leitura_notes"
                " (user_id, article_id, article_title, original, corrected)"
                " VALUES (%s, %s, %s, %s, %s)",
                (user_id, article_id or None, article_title or None, original, corrected or None),
            )
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[portuguese] note-save error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pt/leitura/correct", methods=["POST"])
def api_pt_leitura_correct():
    body = request.get_json(force=True)
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"corrected": "", "translation": "", "notes": []})
    result = _run_correction(text)
    return jsonify(result)


@app.route("/api/pt/escrita/correct", methods=["POST"])
def api_pt_escrita_correct():
    body = request.get_json(force=True)
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"corrected": "", "notes": []})
    result = _run_correction(text)
    return jsonify({"corrected": result["corrected"], "notes": result["notes"]})


@app.route("/api/pt/escrita/save", methods=["POST"])
def api_pt_escrita_save():
    body     = request.get_json(force=True)
    text     = (body.get("text") or "").strip()
    mode     = (body.get("mode") or "diario").strip()
    user_id  = _request_user_id()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    corrected_text = (body.get("corrected_text") or None)
    notes = body.get("notes") or []

    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portuguese.writing_sessions"
                " (user_id, mode, original_text, corrected_text, notes)"
                " VALUES (%s, %s, %s, %s, %s)",
                (user_id, mode, text, corrected_text, json.dumps(notes)),
            )
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[portuguese] escrita save error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pt/palavras-status", methods=["POST"])
def api_pt_palavras_status():
    body    = request.get_json(force=True)
    word_id = body.get("id")
    status  = (body.get("status") or "").strip()
    user_id = _request_user_id()
    if not word_id or not status:
        return jsonify({"ok": False, "error": "id and status required"}), 400
    valid = {"library", "practice", "mastered"}
    if status not in valid:
        return jsonify({"ok": False, "error": f"status must be one of {valid}"}), 400
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "UPDATE portuguese.vocabulary SET status = %s WHERE id = %s AND user_id = %s",
                    (status, word_id, user_id),
                )
            else:
                cur.execute(
                    "UPDATE portuguese.vocabulary SET status = %s WHERE id = %s",
                    (status, word_id),
                )
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[portuguese] palavras-status error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ── API: Transcribe ───────────────────────────────────────────────────────────

@app.route("/api/pt/transcribe", methods=["POST"])
def api_pt_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file"}), 400
    audio_file = request.files["audio"]
    try:
        import time as _t
        from openai import OpenAI as _OAI
        api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        client = _OAI(api_key=api_key)
        audio_file.stream.seek(0)
        _t0 = _t.time()
        fname = audio_file.filename or "sessao.webm"
        content_type = audio_file.content_type or "audio/webm"
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=(fname, audio_file.stream, content_type),
            language="pt",
            response_format="text",
        )
        print(f"[TIMING] pt_transcribe_ms={int((_t.time()-_t0)*1000)}", flush=True)
        return jsonify({"transcript": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Review (transcript analysis) ────────────────────────────────────────

@app.route("/api/pt/review", methods=["POST"])
def api_pt_review():
    body         = request.get_json(force=True)
    transcript   = body.get("transcript", "").strip()
    model        = body.get("model", "claude").strip()
    persona_name = body.get("persona_name", "").strip()
    scene        = body.get("scene", "").strip()
    source       = body.get("source", "ki_sessao").strip()
    if not transcript:
        return jsonify({"ok": False, "error": "transcript required"}), 400
    try:
        from review_router import run_review, ProviderError
        result = run_review(transcript, persona_name, scene, model)

        # Save to postgres.portuguese.sessions
        import datetime as _dt
        date_str = _dt.datetime.now().strftime("%Y-%m-%d")
        duration_min = max(1, len(transcript) // 300)
        _save_session(
            user_id=_request_user_id(),
            date_str=date_str,
            persona=persona_name,
            scenario=scene,
            source=source,
            raw_transcript=transcript,
            reviewer_output=result.get("feedback", {}),
            model=model,
            duration_min=duration_min,
        )

        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "model": model}), 502


# ── API: KI-Sessão chat turn ──────────────────────────────────────────────────

@app.route("/api/pt/chat", methods=["POST"])
def api_pt_chat():
    data      = request.get_json(force=True)
    history   = data.get("history", [])
    persona   = data.get("persona", "").strip()
    scene     = data.get("scene", "").strip()
    model     = data.get("model", "claude").strip()
    user_turn = data.get("user_turn", "")
    if not persona:
        return jsonify({"ok": False, "error": "persona required"}), 400
    try:
        import time as _t
        from review_router import run_chat_turn, ProviderError
        personas = _load_personas()
        for p in personas:
            p["slug"] = _name_to_slug(p["name"])
        _t0 = _t.time()
        response = run_chat_turn(history, persona, scene, user_turn, model, personas)
        print(f"[TIMING] pt_ai_turn_ms={int((_t.time()-_t0)*1000)} model={model}", flush=True)
        return jsonify({"ok": True, "response": response, "model": model})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "model": model}), 502


# ── API: TTS speak ────────────────────────────────────────────────────────────

@app.route("/api/pt/speak", methods=["POST"])
def api_pt_speak():
    data  = request.get_json(force=True)
    text  = data.get("text", "").strip()
    voice = data.get("voice", "nova")
    if not text:
        return jsonify({"error": "text required"}), 400
    if voice not in ("alloy", "echo", "fable", "nova", "onyx", "shimmer"):
        voice = "nova"
    try:
        from openai import OpenAI as _OAI
        api_key = get_secret("OPENAI_API_KEY", "openai", "api_key")
        if not api_key:
            return jsonify({"error": "OpenAI API key not configured"}), 500
        client = _OAI(api_key=api_key, timeout=12.0)
        import time as _t
        _t0 = _t.time()
        _first = [True]

        def _stream():
            with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=voice,
                input=text,
            ) as r:
                for chunk in r.iter_bytes(chunk_size=4096):
                    if _first[0]:
                        print(f"[TIMING] pt_tts_ttfb_ms={int((_t.time()-_t0)*1000)}", flush=True)
                        _first[0] = False
                    yield chunk

        return FlaskResponse(_stream(), mimetype="audio/mpeg", direct_passthrough=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Persona prompt ───────────────────────────────────────────────────────

@app.route("/api/pt/persona-prompt")
def api_pt_persona_prompt():
    persona_slug = request.args.get("persona", "")
    scene        = request.args.get("scene", "")

    personas = _load_personas()
    persona = next((p for p in personas if _name_to_slug(p["name"]) == persona_slug), None)
    if not persona:
        return jsonify({"error": "persona not found"}), 404

    # Build the full system prompt from the .txt file
    personas_dir = BASE_DIR / "personas"
    matches = list(personas_dir.glob(f"{persona_slug}*.txt"))
    if matches:
        persona_txt = matches[0].read_text(encoding="utf-8").strip()
    else:
        persona_txt = persona.get("description", f"Você é {persona.get('name', persona_slug)}.")

    scene_text = persona.get("speaking_prompts", {}).get(scene, "")
    parts = [persona_txt]
    if scene_text:
        parts.append(f"Cenário:\n{scene_text}")
    full_prompt = "\n\n".join(parts)

    return jsonify({"prompt": full_prompt, "session_brief": scene_text})


# ── API: Admin — article management ──────────────────────────────────────────

@app.route("/api/pt/admin/article", methods=["POST"])
def api_pt_admin_article():
    body      = request.get_json(force=True)
    url       = (body.get("url") or "").strip()
    title     = (body.get("title") or "").strip()
    full_text = (body.get("full_text") or body.get("text") or "").strip()
    excerpt   = (body.get("excerpt") or full_text[:300] if full_text else "").strip()
    source    = (body.get("source") or "").strip()
    category  = (body.get("category") or "").strip()
    user_id   = _request_user_id()
    if not url or not title or not category:
        return jsonify({"ok": False, "error": "url, title, category required"}), 400
    valid_cats = {"cotidiano", "cultura", "noticias", "rio"}
    if category not in valid_cats:
        return jsonify({"ok": False, "error": f"category must be one of {valid_cats}"}), 400
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO portuguese.articles"
                " (url, title, excerpt, full_text, source, category, added_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (url) DO UPDATE SET"
                "   title = EXCLUDED.title,"
                "   full_text = EXCLUDED.full_text,"
                "   excerpt = EXCLUDED.excerpt,"
                "   is_active = TRUE"
                " RETURNING id",
                (url, title, excerpt or None, full_text or None, source or None, category, user_id),
            )
            article_id = cur.fetchone()[0]
        return jsonify({"ok": True, "id": article_id})
    except Exception as e:
        print(f"[portuguese] admin/article error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ── API: Sessions list ────────────────────────────────────────────────────────

@app.route("/api/pt/sessions")
def api_pt_sessions():
    limit = min(int(request.args.get("limit", 5)), 20)
    sessions = _get_sessions(_request_user_id(), limit=limit)
    return jsonify(sessions)


# ── API: Send session feedback to Telegram ────────────────────────────────────

_ROBERT_CHAT_ID = 8379221702

@app.route("/api/pt/send-telegram", methods=["POST"])
def api_pt_send_telegram():
    import requests as _req
    data = request.get_json(force=True)
    feedback = data.get("feedback", {})
    persona  = data.get("persona", "")
    scene    = data.get("scene", "")

    summary   = feedback.get("overall_summary", "")
    errors    = feedback.get("errors", [])
    strengths = feedback.get("strengths", [])
    next_focus = feedback.get("next_focus", "")

    lines = [f"🇧🇷 Português — {persona}", f"_{scene}_", ""]
    lines.append(f"*Resumo:* {summary}")
    if errors:
        lines.append("\n*Erros:*")
        for e in errors[:4]:
            lines.append(f"• {e.get('original','')} → {e.get('correction','')} ({e.get('explanation','')})")
    if strengths:
        lines.append("\n*Pontos fortes:*")
        for s in strengths[:3]:
            lines.append(f"✓ {s}")
    if next_focus:
        lines.append(f"\n*Próxima sessão:* {next_focus}")

    message = "\n".join(lines)
    if len(message) > 4096:
        message = message[:4090] + "\n…"

    try:
        bot_token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    resp = _req.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": _ROBERT_CHAT_ID, "text": message, "parse_mode": "Markdown"},
        timeout=10,
    )
    if resp.ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": resp.text}), 502


# ── API: Anki export ─────────────────────────────────────────────────────────

@app.route("/api/pt/anki-export")
def api_pt_anki_export():
    user_id = _request_user_id()
    status = request.args.get("status") or None
    entries = _get_vocabulary(user_id, status=status, limit=10000)
    lines = ["Português\tInglês\tContexto"]
    for e in entries:
        pt  = e.get("portuguese", "").replace("\t", " ")
        en  = e.get("english", "").replace("\t", " ")
        ctx = (e.get("source_sentence") or "").replace("\t", " ")[:200]
        lines.append(f"{pt}\t{en}\t{ctx}")
    from flask import Response as _Resp
    return _Resp(
        "\n".join(lines),
        mimetype="text/tab-separated-values",
        headers={"Content-Disposition": "attachment; filename=meu-portugues-anki.tsv"},
    )


# ── API: Drill result ─────────────────────────────────────────────────────────

@app.route("/api/pt/drill-result", methods=["POST"])
def api_pt_drill_result():
    body = request.get_json(force=True)
    phrase_id = body.get("phrase_id", "")
    result = body.get("result", "")
    if not phrase_id or result not in ("correct", "wrong", "skip"):
        return jsonify({"ok": False, "error": "invalid params"}), 400
    return jsonify({"ok": True})


# ── API: Send persona prompt to Telegram ──────────────────────────────────────

@app.route("/api/pt/send-prompt-telegram", methods=["POST"])
def api_pt_send_prompt_telegram():
    import requests as _req
    body = request.get_json(force=True)
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "prompt required"}), 400
    try:
        bot_token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    msg = f"📝 *Prompt PT*\n\n{prompt[:3500]}"
    resp = _req.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": _ROBERT_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )
    return jsonify({"ok": resp.ok})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8770))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
