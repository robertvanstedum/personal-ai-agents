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
    db_url = os.environ.get("DATABASE_URL") or get_secret("DATABASE_URL") or \
        "postgresql://postgres:simple123@localhost:5432/personal_agents"
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
    return render_template("portuguese_escrita.html", active="escrita",
                           show_toggle=True)


@app.route("/palavras")
def palavras():
    return render_template("portuguese_palavras.html", active="palavras",
                           show_toggle=True)


@app.route("/arquivo")
def arquivo():
    return render_template("portuguese_arquivo.html", active="arquivo",
                           show_toggle=True)


@app.route("/admin")
def admin():
    return render_template("portuguese_admin.html", active="admin",
                           show_toggle=True)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "portuguese"})


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


# ── API: Sessions list ────────────────────────────────────────────────────────

@app.route("/api/pt/sessions")
def api_pt_sessions():
    limit = min(int(request.args.get("limit", 5)), 20)
    sessions = _get_sessions(_request_user_id(), limit=limit)
    return jsonify(sessions)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8770))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
