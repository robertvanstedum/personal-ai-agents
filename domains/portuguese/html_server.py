"""
html_server.py — Portuguese language domain.

Spec 2: shell + pages.
Spec 3: Conversas — voice sessions, port of Mein Deutsch Gespräche.
Run: PORT=8770 venv/bin/python3 html_server.py
"""

SESSIONS_PER_ROUND = 5

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
from core.identity import resolve_user_id

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


import datetime as _dt

_PT_DATA_DIR = BASE_DIR / "data"

_PT_UNIVERSAL_HEADER = """\
=== SESSION INSTRUCTIONS — READ BEFORE STARTING ===

You are playing a character in a Brazilian Portuguese language practice session. These rules override everything else. Follow them exactly.

0. VOICE AND GENDER: Play the character exactly as described below — including gender. Never switch. Non-negotiable.

1. SCENARIO AND MEDIUM: Follow the scenario setup exactly. If it says you are at a padaria, stay at the padaria. Never change the setting mid-session.

2. NO NAME PREFIX: Do not announce your name before each turn.
   Wrong: "Maria: Olá!"
   Correct: "Olá!"

3. LANGUAGE: Always respond in Brazilian Portuguese. Never switch to English unless I say "English please."

4. CORRECTIONS: If I make a grammatical error, gently use the correct form naturally. Do not break character.

5. START TRIGGER: Do not begin until I say "Start today's session", "Start session", or "Vamos começar." Wait in silence — do not acknowledge or ask.

6. STAY IN CHARACTER: Do not comment on the exercise or your role. You are the character.

=== CHARACTER AND SCENARIO BELOW ===""".strip()

_PT_UNIVERSAL_FOOTER = """\
=== HOW TO END THIS SESSION ===

PREFERRED: Stop voice mode yourself first, then type "End session. Give me the transcript."
This prevents the transcript from being read aloud.

VOICE TRIGGER: If Robert says "end session" or "encerrar sessão" while in voice mode —
1. Stop speaking immediately. Do not say anything else.
2. Exit voice mode silently.
3. Output the transcript block below in text only. Do not read it aloud.

Output ONLY this block — nothing before or after, no commentary:

---SESSION---
Date: [today's date as YYYY-MM-DD]
Persona: [character name]
Scenario: [scenario_label]
Duration: [number only — e.g. 12]
Mode: voice

[Character name]: [their exact words]
Robert: [your exact words]
[continue alternating turns in order...]
---END---

Every turn in order, no skips. Use --- not em-dashes. Duration is a number only. Nothing before ---SESSION---. Nothing after ---END---.""".strip()


def _writing_sessions_path(user_id) -> Path:
    key = str(user_id) if user_id is not None else "anonymous"
    return _PT_DATA_DIR / "writing_sessions" / f"user_{key}.json"


def _conversas_sessions_path(user_id) -> Path:
    key = str(user_id) if user_id is not None else "anonymous"
    return _PT_DATA_DIR / "conversas_sessions" / f"user_{key}.json"


def _pt_save_writing_entry(user_id, mode: str, text_original: str,
                            text_corrected: str = "", notes: list = None) -> dict:
    path = _writing_sessions_path(user_id)
    data = {"entries": []}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            pass
    entry = {
        "id": f"pt_ws_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "user_id": user_id,
        "created_at": _dt.datetime.now().isoformat(),
        "mode": mode,
        "text_original": text_original,
        "text_corrected": text_corrected,
        "notes": notes or [],
        "timestamp": _dt.datetime.now().isoformat(),
    }
    data.setdefault("entries", []).append(entry)
    data["entries"] = data["entries"][-50:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return entry


def _pt_get_writing_sessions(user_id, limit: int = 10) -> list:
    entries = []
    # Per-user file (current)
    path = _writing_sessions_path(user_id)
    if path.exists():
        try:
            entries = json.loads(path.read_text()).get("entries", [])
        except Exception:
            pass
    # Legacy flat file (pre-multiuser migration — merge to avoid data loss)
    legacy = _PT_DATA_DIR / "writing_sessions.json"
    if legacy.exists():
        try:
            legacy_entries = json.loads(legacy.read_text()).get("entries", [])
            seen = {e.get("id") for e in entries}
            entries += [e for e in legacy_entries if e.get("id") not in seen]
        except Exception:
            pass
    entries.sort(key=lambda e: e.get("created_at") or e.get("timestamp") or "", reverse=True)
    return entries[:limit]


def _pt_save_conversas_session(user_id, session_data: dict) -> None:
    path = _conversas_sessions_path(user_id)
    data = {"sessions": []}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            pass
    data.setdefault("sessions", []).append(session_data)
    data["sessions"] = data["sessions"][-100:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _pt_get_conversas_sessions(user_id, limit: int = 10) -> list:
    sessions = []
    # Per-user JSON file (current)
    path = _conversas_sessions_path(user_id)
    if path.exists():
        try:
            sessions = json.loads(path.read_text()).get("sessions", [])
        except Exception:
            pass
    # Fail closed: no resolved user_id means no proven identity, so skip
    # the Postgres legacy-session merge entirely rather than surface the
    # ownerless (user_id IS NULL) bucket to an unauthenticated request.
    if user_id is None:
        return sessions
    # Postgres historical sessions (pre-JSON migration — merge to avoid data loss)
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, date, persona, scenario, source, created_at
                   FROM portuguese.sessions
                   WHERE user_id IS NULL OR user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit),
            )
            rows = cur.fetchall()
        seen_ids = {s.get("id") for s in sessions}
        for r in rows:
            pg_id = f"pg_{r[0]}"
            if pg_id not in seen_ids:
                sessions.append({
                    "id": pg_id,
                    "date": str(r[1]),
                    "persona": r[2],
                    "scenario": r[3],
                    "source": r[4],
                    "created_at": r[5].isoformat() if r[5] else "",
                })
        conn.close()
    except Exception:
        pass
    sessions.sort(key=lambda s: s.get("created_at") or s.get("date") or "", reverse=True)
    return sessions[:limit]


def _ensure_persona_progress_table():
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portuguese.persona_progress (
                    id               SERIAL PRIMARY KEY,
                    user_id          INTEGER REFERENCES auth.users(id) ON DELETE CASCADE,
                    persona_slug     VARCHAR(100) NOT NULL,
                    current_round    INTEGER DEFAULT 1,
                    sessions_in_round INTEGER DEFAULT 0,
                    sessions_total   INTEGER DEFAULT 0,
                    last_session_at  TIMESTAMP,
                    UNIQUE(user_id, persona_slug)
                )
            """)
    except Exception as e:
        print(f"[portuguese] migration persona_progress: {e}", flush=True)


_ensure_persona_progress_table()

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


# ── Article selection ─────────────────────────────────────────────────────────

_LEITURA_SOURCES_FILE = BASE_DIR / "data" / "leitura_sources.json"


def _load_leitura_selection_config() -> dict:
    try:
        data = json.loads(_LEITURA_SOURCES_FILE.read_text())
        return data.get("article_selection", {})
    except Exception:
        return {}


def _apply_source_cap(articles: list, config: dict) -> list:
    """Cap articles per source per category. Applied at display time, not ingestion."""
    max_per_cat = config.get("max_per_category", 10)
    max_per_src = config.get("max_per_source_per_category", 3)
    min_per_cat = config.get("min_per_category", 3)
    source_counts: dict = {}
    selected = []
    for article in articles:
        src = article.get("source", "")
        if source_counts.get(src, 0) < max_per_src:
            selected.append(article)
            source_counts[src] = source_counts.get(src, 0) + 1
        if len(selected) >= max_per_cat:
            break
    if len(selected) < min_per_cat:
        already = set(id(a) for a in selected)
        for article in articles:
            if id(article) not in already:
                selected.append(article)
                if len(selected) >= min_per_cat:
                    break
    return selected


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
    # Fail closed: no resolved user_id means no proven identity, so no
    # sessions are returned — never fall back to an unfiltered query.
    if not user_id:
        return []
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, date, persona, scenario, source, created_at
                   FROM portuguese.sessions
                   WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit),
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


def _request_user_id():
    """Delegates to the shared resolver — see core/identity.py."""
    return resolve_user_id(request)


def _request_user_tier() -> str:
    return request.headers.get("X-Minimoi-User-Tier", "guest")


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
    # Fail closed: no resolved user_id means no proven identity, so no
    # vocabulary is returned — never fall back to an unfiltered query.
    if user_id is None:
        return []
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            clauses = ["user_id = %s"]
            params: list = [user_id]
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
                "status": r[5] or "biblioteca",
                "added": str(r[6])[:10] if r[6] else "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[portuguese] vocabulary fetch error: {e}", flush=True)
        return []



def _get_leitura_notes(user_id, limit=20) -> list:
    # Fail closed: no resolved user_id means no proven identity, so no
    # notes are returned — never fall back to an unfiltered query.
    if user_id is None:
        return []
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, article_title, original, corrected, created_at"
                " FROM portuguese.leitura_notes"
                " WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
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


def _get_persona_progress(user_id) -> dict:
    """Returns {persona_slug: {round, sessions_in_round, sessions_total, ready}}"""
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            if user_id is not None:
                cur.execute(
                    "SELECT persona_slug, current_round, sessions_in_round, sessions_total"
                    " FROM portuguese.persona_progress WHERE user_id = %s",
                    (user_id,),
                )
            else:
                return {}
            rows = cur.fetchall()
        return {
            r[0]: {
                "round": r[1],
                "sessions_in_round": r[2],
                "sessions_total": r[3],
                "ready": r[2] >= SESSIONS_PER_ROUND,
            }
            for r in rows
        }
    except Exception as e:
        print(f"[portuguese] persona_progress fetch error: {e}", flush=True)
        return {}


def _update_persona_progress(user_id, persona_slug: str):
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO portuguese.persona_progress
                   (user_id, persona_slug, current_round, sessions_in_round, sessions_total, last_session_at)
                   VALUES (%s, %s, 1, 1, 1, NOW())
                   ON CONFLICT (user_id, persona_slug) DO UPDATE SET
                     sessions_in_round = portuguese.persona_progress.sessions_in_round + 1,
                     sessions_total    = portuguese.persona_progress.sessions_total + 1,
                     last_session_at   = NOW()""",
                (user_id, persona_slug),
            )
    except Exception as e:
        print(f"[portuguese] persona_progress update error: {e}", flush=True)


# ── Tips ──────────────────────────────────────────────────────────────────────

_TIPS_FILE = REPO_ROOT / "config" / "curator" / "tips.json"

def _load_tip(slot: str):
    try:
        tips = json.loads(_TIPS_FILE.read_text()) if _TIPS_FILE.exists() else {}
        entry = tips.get(slot, {})
        return entry.get("text") if entry.get("active") else None
    except Exception:
        return None


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("portuguese_landing.html", active="landing",
                           tip=_load_tip("portuguese.landing"))


@app.route("/leitura")
def leitura():
    return render_template("portuguese_leitura.html", active="leitura",
                           tip=_load_tip("portuguese.leitura"))


@app.route("/conversas")
def conversas():
    user_id = _request_user_id()
    personas = _load_personas()
    for p in personas:
        p["slug"] = _name_to_slug(p["name"])
    sessions = _pt_get_conversas_sessions(user_id, limit=5)
    persona_progress = _get_persona_progress(user_id)
    return render_template(
        "portuguese_conversas.html",
        active="conversas",
        personas=personas,
        sessions=sessions,
        persona_progress=persona_progress,
        sessions_per_round=SESSIONS_PER_ROUND,
        tip=_load_tip("portuguese.conversas"),
    )


@app.route("/escrita")
def escrita():
    user_id = _request_user_id()
    writing_sessions = _pt_get_writing_sessions(user_id, limit=5)
    return render_template("portuguese_escrita.html", active="escrita",
                           writing_sessions=writing_sessions,
                           tip=_load_tip("portuguese.escrita"))


@app.route("/palavras")
def palavras():
    user_id = _request_user_id()
    entries = _get_vocabulary(user_id, limit=100)
    drill_pool = (
        _get_vocabulary(user_id, status="praticando", limit=50)
        + _get_vocabulary(user_id, status="pronto_para_testar", limit=50)
    )
    return render_template("portuguese_palavras.html", active="palavras",
                           entries=entries, drill_pool=drill_pool,
                           tip=_load_tip("portuguese.palavras"))


@app.route("/arquivo")
def arquivo():
    user_id = _request_user_id()
    conversas_sessions = _pt_get_conversas_sessions(user_id, limit=10)
    writing_sessions = _pt_get_writing_sessions(user_id, limit=10)
    leitura_notes = _get_leitura_notes(user_id, limit=20)
    return render_template("portuguese_arquivo.html", active="arquivo",
                           conversas_sessions=conversas_sessions,
                           writing_sessions=writing_sessions,
                           leitura_notes=leitura_notes,
                           tip=_load_tip("portuguese.arquivo"))


@app.route("/admin")
def admin():
    return render_template("portuguese_admin.html", active="admin")


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
                " ORDER BY date_fetched DESC, id DESC LIMIT 50",
                (category,),
            )
            rows = cur.fetchall()
        candidates = [
            {
                "id": r[0], "title": r[1], "source": r[2] or "",
                "url": r[3] or "#",
                "summary": r[4] or r[5] or "",
                "date_fetched": str(r[6]) if r[6] else "",
            }
            for r in rows
        ]
        articles = _apply_source_cap(candidates, _load_leitura_selection_config())
    except Exception as e:
        print(f"[portuguese] leitura-category error: {e}", flush=True)
        articles = []
    return jsonify({"articles": articles, "category": category})


@app.route("/api/pt/article/<int:article_id>")
def api_pt_article(article_id):
    _UA = "Mozilla/5.0 (compatible; RSS Reader Bot)"
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT url, full_text, excerpt FROM portuguese.articles WHERE id = %s",
                (article_id,),
            )
            row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        url, full_text, excerpt = row
        if full_text:
            return jsonify({"ok": True, "text": full_text})
        if excerpt:
            return jsonify({"ok": True, "text": excerpt})
        # Scrape on demand
        try:
            from bs4 import BeautifulSoup
            resp = requests.get(url, timeout=8, headers={"User-Agent": _UA})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")
            for tag in soup(["nav", "header", "footer", "script", "style", "aside", "iframe", "figure"]):
                tag.decompose()
            # Try specific article containers first, fall back to all <p>
            article_body = (
                soup.find("article")
                or soup.find(class_=lambda c: c and any(x in c for x in
                    ("article-body", "article-content", "content-text", "materia-content",
                     "news-content", "post-content", "entry-content")))
            )
            target = article_body or soup
            paras = [
                p.get_text(separator=" ", strip=True)
                for p in target.find_all("p")
                if len(p.get_text(strip=True)) > 40
            ]
            text = " ".join(paras)[:3000].strip()
            # Fallback: og:description / meta description (works even on JS-rendered sites)
            if not text:
                for sel in ({"property": "og:description"}, {"name": "description"}):
                    tag = soup.find("meta", attrs=sel)
                    if tag and tag.get("content", "").strip():
                        text = tag["content"].strip()
                        break
        except Exception as scrape_err:
            print(f"[article/{article_id}] scrape failed: {scrape_err}", flush=True)
            text = ""
        if text:
            conn2 = _db_conn()
            with conn2, conn2.cursor() as cur2:
                cur2.execute(
                    "UPDATE portuguese.articles SET full_text = %s WHERE id = %s",
                    (text, article_id),
                )
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        print(f"[article/{article_id}] error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pt/leitura-action", methods=["POST"])
def api_pt_leitura_action():
    return jsonify({"ok": True})


@app.route("/api/pt/leitura-refresh", methods=["POST"])
def api_pt_leitura_refresh():
    try:
        import leitura_rss
        total_new = leitura_rss.run_pipeline()
        return jsonify({"ok": True, "new": total_new})
    except Exception as e:
        print(f"[leitura-refresh] pipeline error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


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
    body  = request.get_json(force=True)
    text  = (body.get("text") or "").strip()
    mode  = (body.get("mode") or "diario").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    try:
        entry = _pt_save_writing_entry(
            user_id=_request_user_id(),
            mode=mode,
            text_original=text,
            text_corrected=(body.get("corrected_text") or ""),
            notes=body.get("notes") or [],
        )
        return jsonify({"ok": True, "id": entry["id"]})
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
    valid = {"biblioteca", "praticando", "pronto_para_testar", "aprendido"}
    if status not in valid:
        return jsonify({"ok": False, "error": "invalid status"}), 400
    # Fail closed: no resolved user_id means no proven identity, so no
    # update is allowed — never fall back to an unscoped write.
    if user_id is None:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE portuguese.vocabulary SET status = %s WHERE id = %s AND user_id = %s",
                (status, word_id, user_id),
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

        date_str = _dt.datetime.now().strftime("%Y-%m-%d")
        duration_min = max(1, len(transcript) // 300)
        uid = _request_user_id()

        # JSON-first: save to per-user file (always)
        _pt_save_conversas_session(uid, {
            "id": f"pt_cs_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "user_id": uid,
            "created_at": _dt.datetime.now().isoformat(),
            "date": date_str,
            "persona": persona_name,
            "scenario": scene,
            "source": source,
            "raw_transcript": transcript,
            "reviewer_output": result.get("feedback", {}),
            "model": model,
            "duration_min": duration_min,
        })

        # Postgres projection: best-effort, non-blocking
        try:
            _save_session(
                user_id=uid,
                date_str=date_str,
                persona=persona_name,
                scenario=scene,
                source=source,
                raw_transcript=transcript,
                reviewer_output=result.get("feedback", {}),
                model=model,
                duration_min=duration_min,
            )
        except Exception as _pg_err:
            print(f"[portuguese] Postgres session projection failed (non-fatal): {_pg_err}", flush=True)

        if uid and persona_name:
            _update_persona_progress(uid, _name_to_slug(persona_name))

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

    persona_name = persona.get("name", "")
    personas_dir = BASE_DIR / "personas"
    matches = list(personas_dir.glob(f"{persona_slug}*.txt"))
    if matches:
        persona_txt = matches[0].read_text(encoding="utf-8").strip()
    else:
        persona_txt = persona.get("description", f"Você é {persona_name}.")

    scene_text = persona.get("speaking_prompts", {}).get(scene, "")

    role_anchor = (
        f"ROLES: You are {persona_name}. "
        f"The learner you are speaking with is Robert. "
        f"Stay in character as {persona_name} for the entire session."
    )

    parts = [_PT_UNIVERSAL_HEADER, role_anchor, persona_txt]
    if scene_text:
        parts.append(f"=== SCENARIO FOR THIS SESSION ===\n\n{scene_text}")
    parts.append(_PT_UNIVERSAL_FOOTER)
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
            # Match date_fetched to the most recent article in this category so
            # manually added articles group with the existing feed in HOJE filter.
            cur.execute(
                "SELECT date_fetched FROM portuguese.articles"
                " WHERE category = %s AND is_active = TRUE AND date_fetched IS NOT NULL"
                " ORDER BY date_fetched DESC LIMIT 1",
                (category,),
            )
            row = cur.fetchone()
            date_fetched = row[0] if row else None
            cur.execute(
                "INSERT INTO portuguese.articles"
                " (url, title, excerpt, full_text, source, category, added_by, date_fetched)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (url) DO UPDATE SET"
                "   title = EXCLUDED.title,"
                "   full_text = EXCLUDED.full_text,"
                "   excerpt = EXCLUDED.excerpt,"
                "   is_active = TRUE"
                " RETURNING id",
                (url, title, excerpt or None, full_text or None, source or None, category, user_id, date_fetched),
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
    sessions = _pt_get_conversas_sessions(_request_user_id(), limit=limit)
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


# ── API: Persona round management ────────────────────────────────────────────

@app.route("/api/pt/persona-progress")
def api_pt_persona_progress():
    user_id = _request_user_id()
    progress = _get_persona_progress(user_id)
    return jsonify(progress)


@app.route("/api/pt/persona-advance", methods=["POST"])
def api_pt_persona_advance():
    data = request.get_json(force=True)
    user_id = _request_user_id()
    persona_slug = (data.get("persona_slug") or "").strip()
    if not persona_slug:
        return jsonify({"ok": False, "error": "persona_slug required"}), 400
    try:
        conn = _db_conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE portuguese.persona_progress
                   SET current_round = current_round + 1,
                       sessions_in_round = 0
                   WHERE user_id = %s AND persona_slug = %s""",
                (user_id, persona_slug),
            )
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[portuguese] persona-advance error: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8770))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
