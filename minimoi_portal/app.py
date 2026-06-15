"""
minimoi_portal/app.py — mini-moi portal.

Public landing page + authenticated access to Curator and Mein Deutsch.
Runs on port 5001. Cloudflare Tunnel routes minimoi.ai → here.

Run: cd ~/Projects/personal-ai-agents && venv/bin/python3 minimoi_portal/app.py
     PORTAL_PORT=5001 venv/bin/python3 minimoi_portal/app.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import requests as _requests

# Ensure repo root is on sys.path so `minimoi_portal` resolves as a package
sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_cors import CORS

# ── App setup ─────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
REPO_DIR = BASE_DIR.parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)

# Jinja2 globals — timezone-aware so it subtracts cleanly from psycopg2 timestamps
from datetime import timezone as _tz
import re as _re
app.jinja_env.globals['now'] = lambda: datetime.now(_tz.utc)

def _clean_spec_title(spec_file: str) -> str:
    """Convert a spec filename to a human-readable title.
    e.g. 'build_spec_cos_build_discipline_2026-06-12.md' → 'CoS Build Discipline'
    """
    if not spec_file:
        return ""
    name = spec_file
    # Strip directory prefix if present
    name = name.rsplit("/", 1)[-1]
    # Strip extension
    name = _re.sub(r'\.\w+$', '', name)
    # Strip date suffix (e.g. _2026-06-12 or -2026-06-12)
    name = _re.sub(r'[-_]\d{4}-\d{2}-\d{2}.*$', '', name)
    # Strip common doc-type prefixes
    for prefix in ("build_spec_", "spec_", "handoff_", "design_", "feature_", "plan_"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    # Replace underscores/hyphens with spaces, title-case
    name = name.replace("_", " ").replace("-", " ")
    # Title-case with known abbreviation handling
    words = name.split()
    result = []
    caps = {"cos", "ai", "db", "api", "llm", "tpm", "ui", "ux", "poc"}
    for w in words:
        result.append(w.upper() if w.lower() in caps else w.capitalize())
    return " ".join(result)

app.jinja_env.filters['clean_spec_title'] = _clean_spec_title

# Load config
from minimoi_portal import config as _cfg  # noqa: E402

app.secret_key = _cfg.SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=_cfg.SESSION_LIFETIME_DAYS)
# Only send session cookie when session actually changes (login/logout).
# Without this, Flask re-sends Set-Cookie on EVERY response including images,
# which prevents browsers from caching proxied static assets.
app.config["SESSION_REFRESH_EACH_REQUEST"] = False
# Secure cookie flags — required for mobile browsers on HTTPS.
# SESSION_COOKIE_SECURE=True: only send cookie over HTTPS (not HTTP).
# SESSION_COOKIE_SAMESITE="Lax": allow cross-site navigation (normal browser links).
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Lazy imports
from minimoi_portal import auth as _auth          # noqa: E402
from minimoi_portal import proxy as _proxy        # noqa: E402
from minimoi_portal import guest_data as _gdata   # noqa: E402


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _current_user() -> dict | None:
    return session.get("user")


def _require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _current_user():
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _require_owner(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if user.get("tier") != "owner":
            # Logged in but wrong tier — send to login so they can switch accounts
            return redirect(url_for("login", next=request.path, notice="owner_required"))
        return f(*args, **kwargs)
    return decorated


# ── Public routes ─────────────────────────────────────────────────────────────

@app.route("/capture-auth")
def capture_auth():
    """Localhost-only session bootstrap for capture_snapshot.py.
    Only accessible from 127.0.0.1 — never reachable via Cloudflare tunnel."""
    if request.remote_addr != "127.0.0.1":
        return "Forbidden", 403
    session["user"] = {"username": "robert", "tier": "owner", "display_name": "Robert"}
    return "ok", 200


@app.route("/preview/")
@app.route("/preview/<path:subpath>")
def preview(subpath=""):
    """Serve static preview snapshots — public, no auth required."""
    from flask import send_file as _send_file
    preview_dir = BASE_DIR / "static" / "preview"
    target = preview_dir / (subpath or "index.html")
    if target.is_file():
        return _send_file(str(target))
    # Try appending .html
    if not subpath.endswith(".html"):
        alt = preview_dir / f"{subpath}.html"
        if alt.is_file():
            return _send_file(str(alt))
    return "Preview page not found — run tools/capture_snapshot.py to generate snapshots.", 404


@app.route("/contact")
def contact():
    """Public contact / guest access request page."""
    return render_template("contact.html", user=_current_user())


@app.route("/")
def landing():
    return render_template("landing.html", user=_current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    if _current_user():
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, error = _auth.authenticate(username, password)
        if user:
            session.permanent = True
            session["user"] = {
                "username": user["username"],
                "display_name": user.get("display_name", user["username"]),
                "tier": user["tier"],
            }
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Self-service guest registration.
    Creates a pending account and notifies Robert via Telegram.
    """
    if _current_user():
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email        = request.form.get("email", "").strip().lower()
        password     = request.form.get("password", "")

        if not display_name:
            error = "Please enter your name."
        elif not email or "@" not in email:
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            pending = _auth.create_pending(display_name, email, password)
            _notify_telegram_new_request(display_name, email)
            return render_template("register.html", pending=True)

    return render_template("register.html", error=error)


def _notify_telegram_new_request(display_name: str, email: str) -> None:
    """Fire a Telegram message to Robert when a new guest requests access."""
    try:
        import keyring
        token   = keyring.get_password("telegram", "bot_token")
        chat_id = 8379221702
        text = (
            f"🔔 <b>New guest access request</b>\n\n"
            f"<b>Name:</b> {display_name}\n"
            f"<b>Email:</b> {email}\n\n"
            f"Approve or reject at:\n"
            f"https://minimoi.ai/admin/guests"
        )
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️  Telegram notification failed: {e}")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@_require_login
def dashboard():
    user = _current_user()
    return render_template("dashboard.html", user=user)


# ── Curator proxy (owner + family only; guests get deep dive HTML only) ───────

@app.route("/app/curator")
@app.route("/app/curator/")
@_require_login
def curator_root():
    user = _current_user()
    if user["tier"] == "guest":
        return redirect(url_for("guest_briefing"))
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "/", "/app/curator", user=user)


@app.route("/app/curator/<path:path>", methods=["GET", "POST", "DELETE", "PATCH"])
@_require_login
def curator_proxy(path):
    user = _current_user()
    if user["tier"] == "guest":
        # Allow static assets and deep dive HTML — guests need these to render pages
        if path.startswith(("interests/", "static/")):
            return _proxy.proxy_to(_cfg.CURATOR_BACKEND, path, "/app/curator", user=user)
        return redirect(url_for("guest_briefing"))
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, path, "/app/curator", user=user)


# ── Top-level Curator static-file passthroughs ────────────────────────────────
# Several links inside Curator pages are built via JS template literals or
# direct hrefs that land at the portal top-level (not under /app/curator/).
# These catch-alls forward them to the Curator backend transparently.

@app.route("/interests/<path:path>")
@_require_login
def interests_passthrough(path):
    user = _current_user()
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "interests/" + path, "/app/curator", user=user)

# /research/* and /api/research/* — linked from JS-generated HTML in the
# briefing (Topics strip hrefs, Leanings links) which the proxy can't rewrite.
@app.route("/research", methods=["GET", "POST"])
@app.route("/research/", methods=["GET", "POST"])
@app.route("/research/<path:path>", methods=["GET", "POST", "DELETE", "PATCH"])
@_require_login
def research_passthrough(path=""):
    user = _current_user()
    sub = ("research/" + path) if path else "research"
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, sub, "/app/curator", user=user)

# /deepdive and /api/* — JS builds these as variable URLs so the proxy
# regex can't rewrite them; forward them straight to the Curator backend.
# proxy_to() already appends request.query_string automatically.
@app.route("/deepdive", methods=["GET"])
@_require_login
def deepdive_passthrough():
    user = _current_user()
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "deepdive", "/app/curator", user=user)

@app.route("/api/<path:path>", methods=["GET", "POST", "DELETE", "PATCH"])
@_require_login
def api_passthrough(path):
    user = _current_user()
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "api/" + path, "/app/curator", user=user)

# curator_priorities.html, curator_library.html, curator_intelligence.html,
# curator_index.html — linked directly from within Curator pages
_CURATOR_TOP_LEVEL = {
    "curator_priorities.html",
    "curator_library.html",
    "curator_intelligence.html",
    "curator_briefing.html",
    "curator_index.html",
}

@app.route("/<path:filename>")
@_require_login
def curator_static_passthrough(filename):
    if filename in _CURATOR_TOP_LEVEL:
        user = _current_user()
        return _proxy.proxy_to(_cfg.CURATOR_BACKEND, filename, "/app/curator", user=user)
    from flask import abort
    abort(404)


# ── Mein Deutsch proxy (owner + family full; guest: lesen only, no admin) ────

@app.route("/app/german")
@app.route("/app/german/")
@_require_login
def german_root():
    user = _current_user()
    return _proxy.proxy_to(_cfg.GERMAN_BACKEND, "/", "/app/german", user=user)


@app.route("/app/german/<path:path>", methods=["GET", "POST"])
@_require_login
def german_proxy(path):
    user = _current_user()
    if user["tier"] == "guest":
        # Block owner-only sections — show a friendly restricted page
        _GERMAN_OWNER_ONLY = {
            "admin": "Admin",
        }
        for prefix, label in _GERMAN_OWNER_ONLY.items():
            if path.startswith(prefix):
                return render_template("guest_restricted.html", section=label)
    return _proxy.proxy_to(_cfg.GERMAN_BACKEND, path, "/app/german", user=user)


# ── Guest briefing view ───────────────────────────────────────────────────────

@app.route("/guest/briefing")
@_require_login
def guest_briefing():
    """Daily briefing for guest users — with interaction buttons."""
    user = _current_user()

    latest_file = REPO_DIR / "curator_latest.json"
    articles = []
    briefing_date = None

    if latest_file.exists():
        try:
            data = json.loads(latest_file.read_text())
            raw = data if isinstance(data, list) else data.get("articles", [])
            for a in raw[:20]:
                articles.append({
                    "hash_id":  a.get("hash_id", ""),
                    "rank":     a.get("rank", ""),
                    "title":    a.get("title", ""),
                    "url":      a.get("url", a.get("link", "")),
                    "source":   a.get("source", ""),
                    "category": a.get("category", ""),
                    "summary":  a.get("summary", ""),
                })
            briefing_date = data.get("date") if isinstance(data, dict) else None
        except Exception:
            pass

    # Load this guest's existing feedback so buttons show correct state
    user_feedback = _gdata.get_user_feedback(user["username"])

    return render_template(
        "guest_briefing.html",
        user=user,
        articles=articles,
        briefing_date=briefing_date,
        user_feedback=user_feedback,
    )


# ── Guest interaction API ─────────────────────────────────────────────────────

@app.route("/guest/feedback", methods=["POST"])
@_require_login
def guest_feedback():
    """Like, dislike, or save an article. Returns updated state."""
    user = _current_user()
    body = request.get_json(silent=True) or {}
    hash_id = body.get("hash_id", "").strip()
    action  = body.get("action", "").strip()

    if not hash_id or action not in ("like", "dislike", "save"):
        return jsonify({"error": "Invalid request"}), 400

    state = _gdata.record_feedback(user["username"], hash_id, action)
    return jsonify(state)


@app.route("/guest/comment", methods=["POST"])
@_require_login
def guest_comment():
    """Add a comment on an article."""
    user = _current_user()
    body = request.get_json(silent=True) or {}
    hash_id = body.get("hash_id", "").strip()
    text    = body.get("text", "").strip()

    if not hash_id or not text:
        return jsonify({"error": "Invalid request"}), 400

    comment = _gdata.add_comment(user["username"], user["display_name"], hash_id, text)
    return jsonify({"success": True, "comment": comment})


@app.route("/guest/deep-dive", methods=["POST"])
@_require_login
def guest_deep_dive():
    """
    Trigger a fresh deep dive for an article.
    Calls the Curator backend's /deepdive endpoint.
    Takes ~30-60s — the browser waits (threaded Flask handles concurrent requests).
    Returns {success, html_path} on completion.
    """
    user = _current_user()
    body = request.get_json(silent=True) or {}
    hash_id = body.get("hash_id", "").strip()
    title   = body.get("title", "").strip()
    summary = body.get("summary", "").strip()

    if not hash_id:
        return jsonify({"success": False, "message": "Missing article ID"}), 400

    # Build interest string from article metadata
    interest = f"Deep dive on: {title}"
    if summary:
        interest += f". Context: {summary[:300]}"

    try:
        resp = _requests.get(
            f"{_cfg.CURATOR_BACKEND}/deepdive",
            params={"hash_id": hash_id, "interest": interest},
            timeout=90,
        )
        result = resp.json()
        if result.get("success") and result.get("html_path"):
            return jsonify({"success": True, "html_path": result["html_path"]})
        return jsonify({
            "success": False,
            "message": result.get("message", "Deep dive did not complete"),
        })
    except _requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "Timed out — try again"}), 504
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ── Admin: guest management (owner only) ──────────────────────────────────────

@app.route("/admin/guests")
@_require_owner
def admin_guests():
    guests  = _auth.list_guests()
    pending = _auth.load_pending()
    return render_template("admin_guests.html", user=_current_user(),
                           guests=guests, pending=pending)


@app.route("/admin/guests/approve/<token>", methods=["POST"])
@_require_owner
def admin_guests_approve(token):
    guest = _auth.approve_pending(token)
    if guest:
        _notify_telegram_approved(guest)
        _send_approval_email(guest)
    return redirect(url_for("admin_guests"))


def _notify_telegram_approved(guest: dict) -> None:
    """Fire a Telegram reminder to Robert when he approves a guest."""
    try:
        import keyring
        token   = keyring.get_password("telegram", "bot_token")
        chat_id = 8379221702
        name    = guest.get("display_name", "Guest")
        email   = guest.get("email", "(no email)")
        text = (
            f"✅ <b>Guest approved: {name}</b>\n\n"
            f"<b>Email:</b> {email}\n\n"
            f"Approval email sent — they can now sign in at minimoi.ai"
        )
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️  Telegram approval notification failed: {e}")


def _send_approval_email(guest: dict) -> None:
    """Send an approval email to the guest from robert.vanstedum@gmail.com."""
    import smtplib
    import keyring
    from email.mime.text import MIMEText

    to_email = guest.get("email", "")
    if not to_email:
        return

    name = guest.get("display_name", "there")

    try:
        app_password = keyring.get_password("gmail", "app_password")
        if not app_password:
            print("⚠️  Gmail app password not found in Keychain")
            return

        body = f"""Hi {name},

Your access to mini-moi has been approved! You can sign in now at:

  https://minimoi.ai/login

Use the email address and password you chose when you registered.

Welcome,
Robert
"""
        msg = MIMEText(body)
        msg["Subject"] = "You're in — mini-moi access approved"
        msg["From"]    = "robert.vanstedum@gmail.com"
        msg["To"]      = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login("robert.vanstedum@gmail.com", app_password)
            smtp.send_message(msg)

        print(f"✅ Approval email sent to {to_email}")
    except Exception as e:
        print(f"⚠️  Approval email failed: {e}")


@app.route("/admin/guests/reject/<token>", methods=["POST"])
@_require_owner
def admin_guests_reject(token):
    _auth.reject_pending(token)
    return redirect(url_for("admin_guests"))


@app.route("/admin/guests/create", methods=["POST"])
@_require_owner
def admin_guests_create():
    display_name = request.form.get("display_name", "Guest").strip()
    password     = request.form.get("password", "").strip()
    expires_days = int(request.form.get("expires_days", 7))

    if not password:
        return redirect(url_for("admin_guests"))

    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()
    guest = _auth.create_guest(display_name, expires_at, password)
    return render_template(
        "admin_guests.html",
        user=_current_user(),
        guests=_auth.list_guests(),
        new_guest=guest,
        new_password=password,
    )


@app.route("/admin/guests/revoke/<username>", methods=["POST"])
@_require_owner
def admin_guests_revoke(username):
    _auth.revoke_guest(username)
    return redirect(url_for("admin_guests"))


# ── Guild domain (owner-only) ─────────────────────────────────────────────────

@app.template_filter('score_color')
def score_color(score):
    """Color-code opportunity fit scores using the Curator palette."""
    try:
        s = float(score)
        if s >= 8.0: return '#8b5e2a'
        if s >= 6.0: return '#b8860b'
    except (TypeError, ValueError):
        pass
    return '#9e9080'


def _guild_db_query(sql, params=None):
    """Run a SELECT against the personal_agents DB. Returns list of dicts."""
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(
        "postgresql://minimoi:simple123@localhost:5432/personal_agents",
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        rows = list(cur.fetchall())
    conn.close()
    return rows


def _guild_db_execute(sql, params=None):
    """Run an INSERT/UPDATE/DELETE against the personal_agents DB."""
    import psycopg2
    conn = psycopg2.connect(
        "postgresql://minimoi:simple123@localhost:5432/personal_agents"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
    conn.commit()
    conn.close()


@app.route("/guild")
@app.route("/guild/")
@_require_owner
def guild_landing():
    import requests as _req
    from datetime import datetime, timezone, timedelta
    import json as _json
    from pathlib import Path as _Path

    # ── 1. Operations status ──────────────────────────────────────────────────
    ops = {}
    try:
        r = _req.get("http://localhost:8768/status", timeout=2)
        ops = r.json()
    except Exception:
        ops = {"state": "unreachable", "error": True}

    # ── 2. Career ─────────────────────────────────────────────────────────────
    career = {"counts": {}, "interviews": [], "recent_closed": [], "stale": [],
              "days_left": None, "urgency_note": "", "error": False}
    try:
        for row in _guild_db_query(
            "SELECT status, COUNT(*) as cnt FROM pipeline.items GROUP BY status"
        ):
            career["counts"][row["status"]] = int(row["cnt"])
        career["interviews"] = _guild_db_query(
            "SELECT title, company FROM pipeline.items WHERE status='interview'"
            " ORDER BY created_at DESC"
        )
        career["recent_closed"] = _guild_db_query(
            "SELECT title, company, close_reason, fit_score, closed_at"
            " FROM pipeline.items"
            " WHERE status='closed' AND closed_at > NOW() - INTERVAL '5 days'"
            " ORDER BY closed_at DESC"
        )
        career["stale"] = _guild_db_query(
            "SELECT title, company, created_at FROM pipeline.items"
            " WHERE status='applied' AND created_at < NOW() - INTERVAL '7 days'"
            " ORDER BY created_at ASC"
        )
    except Exception:
        career["error"] = True

    # Deadline from cos_context.json
    try:
        _ctx = _json.loads(
            (_Path(__file__).parent.parent / "domains/guild/config/cos_context.json").read_text()
        )
        _cf = _ctx.get("career_focus", {})
        _dl = _cf.get("deadline", "")
        career["urgency_note"] = _cf.get("urgency_note", "")
        if _dl:
            _dl_dt = datetime.strptime(_dl, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            career["days_left"] = (_dl_dt - datetime.now(timezone.utc)).days
    except Exception:
        pass

    # ── 3. Build ──────────────────────────────────────────────────────────────
    build = {"counts": {}, "blocked": [], "spec_ready": [], "agenda": [], "error": False}
    try:
        for row in _guild_db_query(
            "SELECT status, COUNT(*) as cnt FROM guild.design_log GROUP BY status"
        ):
            build["counts"][row["status"]] = int(row["cnt"])
        build["blocked"] = _guild_db_query(
            "SELECT spec_title, blocked_reason FROM guild.design_log WHERE status='blocked'"
        )
        build["spec_ready"] = _guild_db_query(
            "SELECT spec_title FROM guild.design_log WHERE status='spec_ready' ORDER BY id DESC"
        )
        build["agenda"] = _guild_db_query(
            "SELECT domain, description, confidence FROM guild.cos_agenda"
            " WHERE status='pending' ORDER BY confidence DESC NULLS LAST LIMIT 5"
        )
    except Exception:
        build["error"] = True

    # ── 4. Ahead ──────────────────────────────────────────────────────────────
    # Compute next fire times for each loop (America/Chicago ≈ UTC-5/UTC-6)
    _now = datetime.now(timezone.utc)
    _today = _now.date()

    def _next_daily(hour_utc):
        """Next occurrence of a daily UTC hour."""
        candidate = _now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= _now:
            candidate += timedelta(days=1)
        return candidate

    def _next_weekday(weekday, hour_utc):
        """Next occurrence of a UTC weekday (0=Mon…6=Sun) at given hour."""
        days_ahead = (weekday - _today.weekday()) % 7
        candidate = (_now + timedelta(days=days_ahead)).replace(
            hour=hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= _now:
            candidate += timedelta(weeks=1)
        return candidate

    def _next_dom(day, hour_utc):
        """Next occurrence of a day-of-month at given UTC hour."""
        from calendar import monthrange
        y, m, d = _today.year, _today.month, day
        if d < _today.day or (d == _today.day and _now.hour >= hour_utc):
            m += 1
            if m > 12:
                m, y = 1, y + 1
        import datetime as _dt
        # clamp to month length
        d = min(d, monthrange(y, m)[1])
        candidate = datetime(y, m, d, hour_utc, 0, 0, tzinfo=timezone.utc)
        return candidate

    _loop_a_next = _next_daily(11)   # 06:00 CDT = 11:00 UTC
    _loop_b_next = _next_weekday(6, 14)  # Sunday 09:00 CDT = 14 UTC
    _loop_c_next = _next_weekday(6, 15)  # Sunday 10:00 CDT = 15 UTC
    _loop_d1     = _next_dom(1,  13)     # 1st  08:00 CDT = 13 UTC
    _loop_d15    = _next_dom(15, 13)     # 15th 08:00 CDT = 13 UTC
    _loop_d_next = min(_loop_d1, _loop_d15)

    def _fmt_next(dt):
        delta = dt - _now
        days = delta.days
        if days == 0:
            h = delta.seconds // 3600
            return f"in {h}h" if h else "< 1h"
        if days == 1:
            return f"tomorrow {dt.strftime('%-I:%M %p')} CT"
        return dt.strftime(f"%-d %b {dt.strftime('%-I:%M %p')} CT")

    ahead = {
        "loop_a": {"schedule": "Daily 06:00 + 18:00 CT", "next": _fmt_next(_loop_a_next)},
        "loop_b": {"schedule": "Sunday 09:00 CT", "next": _fmt_next(_loop_b_next)},
        "loop_c": {"schedule": "Sunday 10:00 CT", "next": _fmt_next(_loop_c_next)},
        "loop_d": {"schedule": "1st + 15th 08:00 CT", "next": _fmt_next(_loop_d_next)},
        "career_deadline": career.get("days_left"),
        "urgency_note": career.get("urgency_note", ""),
    }

    return render_template("guild/guild_landing.html",
        ops=ops,
        career=career,
        build=build,
        ahead=ahead,
        user=_current_user()
    )


@app.route("/guild/career")
@app.route("/guild/career/positions")
@_require_owner
def guild_career():
    status_filter   = request.args.get('status', 'all')
    geo_filter      = request.args.get('geo', 'all')
    type_filter     = request.args.get('type', 'all')
    priority_filter = request.args.get('priority', 'all')

    conditions, params = [], []
    if status_filter != 'all':
        conditions.append("status = %s")
        params.append(status_filter)
    if type_filter != 'all':
        conditions.append("opportunity_type = %s")
        params.append(type_filter)
    if geo_filter != 'all':
        conditions.append("LOWER(COALESCE(geo,'')) LIKE %s")
        params.append(f'%{geo_filter.lower()}%')
    if priority_filter == 'starred':
        conditions.append("priority = TRUE")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = (
        "SELECT id, title, company, geo, url, opportunity_type, "
        "fit_score, fit_narrative, warm_lead, warm_lead_contacts, "
        "cos_notes, status, priority, close_reason, closed_at, source, created_at "
        "FROM pipeline.items "
        f"{where_clause} "
        "ORDER BY priority DESC NULLS LAST, fit_score DESC NULLS LAST, created_at DESC"
    )
    try:
        opportunities = _guild_db_query(sql, params or None)
    except Exception:
        opportunities = []

    # Read targeting criteria from cos_context.json (read-only, no fallback needed)
    try:
        import json as _json
        _ctx = _json.loads(
            (__import__('pathlib').Path(__file__).parent.parent /
             "domains/guild/config/cos_context.json").read_text()
        )
        targeting = _ctx.get("career_focus", {})
    except Exception:
        targeting = {}

    return render_template(
        "guild/career_focus.html",
        opportunities=opportunities,
        total=len(opportunities),
        status_filter=status_filter,
        geo_filter=geo_filter,
        type_filter=type_filter,
        priority_filter=priority_filter,
        deadline="Aug 1, 2026",
        targeting=targeting,
    )


@app.route("/guild/career/active")
@_require_owner
def guild_career_active():
    sql = (
        "SELECT id, title, company, geo, url, opportunity_type, "
        "fit_score, fit_narrative, warm_lead, warm_lead_contacts, "
        "cos_notes, status, priority, close_reason, closed_at, created_at "
        "FROM pipeline.items "
        "WHERE (priority = TRUE AND status = 'applied') "
        "OR status = 'reviewing' "
        "OR status = 'interview' "
        "OR (status = 'closed' AND closed_at > NOW() - INTERVAL '5 days') "
        "ORDER BY priority DESC NULLS LAST, fit_score DESC NULLS LAST, created_at DESC"
    )
    try:
        positions = _guild_db_query(sql)
    except Exception:
        positions = []
    return render_template("guild/career_active.html", positions=positions, deadline="Aug 1, 2026")


@app.route("/guild/career/positions/<int:opp_id>/status", methods=["POST"])
@_require_owner
def update_position_status(opp_id):
    new_status   = request.form.get("status", "").strip()
    close_reason = request.form.get("close_reason", "").strip() or None
    referrer     = request.form.get("_referrer", "positions")
    valid = {"suggested", "reviewing", "applied", "interview", "closed", "rejected"}
    if new_status in valid:
        try:
            if new_status == "closed":
                _guild_db_execute(
                    "UPDATE pipeline.items SET status=%s, close_reason=%s, closed_at=NOW() WHERE id=%s",
                    (new_status, close_reason, opp_id)
                )
                if close_reason == "accepted":
                    try:
                        import requests as _req
                        _req.post("http://localhost:8769/event",
                                  json={"type": "search_complete"}, timeout=2)
                    except Exception:
                        pass
            else:
                _guild_db_execute(
                    "UPDATE pipeline.items SET status=%s WHERE id=%s",
                    (new_status, opp_id)
                )
        except Exception:
            pass
    dest = "guild_career_active" if referrer == "active" else "guild_career"
    return redirect(url_for(dest))


@app.route("/guild/career/positions/<int:opp_id>/priority", methods=["POST"])
@_require_owner
def toggle_position_priority(opp_id):
    referrer = request.form.get("_referrer", "positions")
    try:
        _guild_db_execute(
            "UPDATE pipeline.items SET priority = NOT COALESCE(priority, FALSE) WHERE id=%s",
            (opp_id,)
        )
    except Exception:
        pass
    dest = "guild_career_active" if referrer == "active" else "guild_career"
    return redirect(url_for(dest))


@app.route("/guild/career/positions/add", methods=["POST"])
@_require_owner
def add_position():
    title            = request.form.get("title", "").strip()
    company          = request.form.get("company", "").strip() or None
    geo              = request.form.get("geo", "").strip() or None
    opportunity_type = request.form.get("opportunity_type", "employment").strip()
    url              = request.form.get("url", "").strip() or None
    status           = request.form.get("status", "applied").strip()
    priority         = request.form.get("priority") == "1"

    valid_status = {"suggested", "reviewing", "applied", "interview", "closed", "rejected"}
    valid_type   = {"employment", "contract", "advisory"}
    if not title:
        return redirect(url_for("guild_career"))
    if status not in valid_status:
        status = "applied"
    if opportunity_type not in valid_type:
        opportunity_type = "employment"

    try:
        _guild_db_execute(
            "INSERT INTO pipeline.items "
            "(title, company, geo, opportunity_type, url, status, priority, source, context, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual', 'career', 'robert')",
            (title, company, geo, opportunity_type, url, status, priority)
        )
    except Exception:
        pass
    return redirect(url_for("guild_career"))


# ── Build Clarity ─────────────────────────────────────────────────────────────

@app.route("/guild/build")
@_require_owner
def guild_build():
    status_filter = request.args.get('status', 'all')
    where = "WHERE dl.status = %s" if status_filter != 'all' else ""
    params = [status_filter] if status_filter != 'all' else []
    query = f"""
        SELECT dl.*,
               t.reason AS incomplete_reason
        FROM guild.design_log dl
        LEFT JOIN LATERAL (
            SELECT reason FROM guild.design_log_transitions
            WHERE design_log_id = dl.id AND to_status = 'incomplete'
            ORDER BY created_at DESC LIMIT 1
        ) t ON true
        {where}
        ORDER BY dl.last_transition_at DESC NULLS LAST
    """
    try:
        items = _guild_db_query(query, params or None)
    except Exception:
        items = []
    return render_template("guild/build_log.html", items=items,
                           status_filter=status_filter, user=_current_user())


@app.route("/guild/build/queue")
@_require_owner
def guild_build_queue():
    sql = """
        SELECT dl.*,
               t.reason AS incomplete_reason
        FROM guild.design_log dl
        LEFT JOIN LATERAL (
            SELECT reason FROM guild.design_log_transitions
            WHERE design_log_id = dl.id AND to_status = 'incomplete'
            ORDER BY created_at DESC LIMIT 1
        ) t ON true
        WHERE dl.status IN ('spec_ready','in_build','blocked','incomplete')
           OR (dl.status = 'done'
               AND dl.last_transition_at > NOW() - INTERVAL '3 days')
        ORDER BY
          CASE dl.status
            WHEN 'blocked'    THEN 1
            WHEN 'in_build'   THEN 2
            WHEN 'spec_ready' THEN 3
            WHEN 'incomplete' THEN 3
            WHEN 'done'       THEN 4
            ELSE 5
          END,
          dl.last_transition_at DESC NULLS LAST
    """
    try:
        items = _guild_db_query(sql)
    except Exception:
        items = []
    return render_template("guild/build_queue.html", items=items,
                           user=_current_user())


@app.route("/guild/build/spec/<path:filename>")
@_require_owner
def guild_build_spec(filename):
    import markdown as _md
    from pathlib import Path
    import re
    if not re.match(r'^[\w\-\.]+\.md$', filename):
        return "Not found", 404
    spec_path = Path(__file__).parent.parent / "_working" / filename
    if not spec_path.exists():
        return render_template("guild/spec_detail.html",
                               content="<p><em>Spec file not found.</em></p>",
                               filename=filename, user=_current_user())
    raw = spec_path.read_text()
    content = _md.markdown(raw, extensions=["fenced_code", "tables"])
    return render_template("guild/spec_detail.html", content=content,
                           filename=filename, user=_current_user())


@app.route("/guild/build/spec/<path:filename>/raw")
@_require_owner
def guild_build_spec_raw(filename):
    from pathlib import Path
    from flask import Response
    import re
    if not re.match(r'^[\w\-\.]+\.md$', filename):
        return "Not found", 404
    spec_path = Path(__file__).parent.parent / "_working" / filename
    if not spec_path.exists():
        return "Not found", 404
    raw = spec_path.read_text()
    return Response(
        raw,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.route("/guild/build/roadmap")
@_require_owner
def guild_build_roadmap():
    import markdown as _md
    from pathlib import Path
    roadmap_path = Path(__file__).parent.parent / "_working/ROADMAP_2026-06-12.md"
    try:
        raw = roadmap_path.read_text()
        content = _md.markdown(raw, extensions=["fenced_code", "tables"])
    except Exception:
        content = "<p><em>Roadmap file not found.</em></p>"
    return render_template("guild/build_roadmap.html", content=content,
                           user=_current_user())


@app.route("/guild/build/items/<int:item_id>/status", methods=["POST"])
@_require_owner
def update_build_status(item_id):
    new_status = request.form.get('status')
    note = request.form.get('note') or None
    valid = {'spec_ready', 'in_build', 'blocked', 'done', 'deferred', 'incomplete'}
    if new_status not in valid:
        return redirect(url_for('guild_build_queue'))

    try:
        rows = _guild_db_query(
            "SELECT status FROM guild.design_log WHERE id = %s", (item_id,)
        )
        current = rows[0]['status'] if rows else None

        _guild_db_execute(
            "UPDATE guild.design_log SET status=%s, last_transition_at=NOW(), "
            "blocked_reason=%s WHERE id=%s",
            (new_status, note if new_status == 'blocked' else None, item_id)
        )
        if current is not None:
            _guild_db_execute(
                "INSERT INTO guild.design_log_transitions "
                "(design_log_id, from_status, to_status, triggered_by, reason) "
                "VALUES (%s,%s,%s,'robert',%s)",
                (item_id, current, new_status, note)
            )

        # On done/deferred, ask Design/Dev to move the file out of _working/.
        # Non-blocking and non-fatal — if the agent is down or file is already gone, ignore.
        if new_status in ('done', 'deferred'):
            spec_rows = _guild_db_query(
                "SELECT spec_file FROM guild.design_log WHERE id = %s", (item_id,)
            )
            spec_file = spec_rows[0]['spec_file'] if spec_rows else None
            if spec_file:
                try:
                    _requests.post(
                        'http://localhost:8770/archive-spec',
                        json={'spec_file': spec_file},
                        timeout=2,
                    )
                except Exception:
                    pass

    except Exception:
        pass

    return redirect(request.referrer or url_for('guild_build'))


@app.route("/guild/build/items/<int:item_id>/edit", methods=["POST"])
@_require_owner
def edit_build_item(item_id):
    """Update spec_title, summary, github_issue — the human-editable metadata fields."""
    spec_title   = request.form.get('spec_title',   '').strip() or None
    summary      = request.form.get('summary',      '').strip() or None
    github_issue = request.form.get('github_issue', '').strip() or None
    try:
        _guild_db_execute(
            "UPDATE guild.design_log SET spec_title=%s, summary=%s, github_issue=%s "
            "WHERE id=%s",
            (spec_title, summary, github_issue, item_id)
        )
    except Exception:
        pass
    return redirect(request.referrer or url_for('guild_build'))


@app.route("/guild/build/items/<int:item_id>/history")
@_require_owner
def build_item_history(item_id):
    """Return transition history for a design_log item as JSON."""
    try:
        rows = _guild_db_query(
            "SELECT from_status, to_status, triggered_by, reason, created_at "
            "FROM guild.design_log_transitions "
            "WHERE design_log_id = %s ORDER BY created_at ASC",
            (item_id,)
        )
        history = []
        for r in rows:
            history.append({
                "from":    r['from_status'] or '—',
                "to":      r['to_status'],
                "by":      r['triggered_by'] or '—',
                "reason":  r['reason'] or '',
                "at":      r['created_at'].strftime('%b %d %H:%M') if r['created_at'] else '—',
            })
        return jsonify(history)
    except Exception as e:
        return jsonify([])


@app.route("/guild/build/items/new", methods=["POST"])
@_require_owner
def new_build_item():
    """Manually seed a design_log row — for specs Design/Dev missed or hand-entered work."""
    spec_title   = request.form.get('spec_title',   '').strip()
    spec_file    = request.form.get('spec_file',    '').strip() or None
    summary      = request.form.get('summary',      '').strip() or None
    github_issue = request.form.get('github_issue', '').strip() or None
    status       = request.form.get('status', 'spec_ready')
    valid_status = {'spec_ready', 'in_build', 'blocked', 'incomplete', 'deferred'}
    if not spec_title:
        return redirect(url_for('guild_build'))
    if status not in valid_status:
        status = 'spec_ready'
    try:
        rows = _guild_db_query(
            "INSERT INTO guild.design_log "
            "(event_type, doc_type, spec_title, spec_file, summary, github_issue, "
            " agent_source, status, last_transition_at) "
            "VALUES ('manual_entry','spec',%s,%s,%s,%s,'robert',%s,NOW()) RETURNING id",
            (spec_title, spec_file, summary, github_issue, status)
        )
        new_id = rows[0]['id'] if rows else None
        if new_id:
            _guild_db_execute(
                "INSERT INTO guild.design_log_transitions "
                "(design_log_id, from_status, to_status, triggered_by, reason) "
                "VALUES (%s, NULL, %s, 'robert', 'manually seeded from /guild/build')",
                (new_id, status)
            )
    except Exception:
        pass
    return redirect(url_for('guild_build'))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = _cfg.PORT
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="localhost", port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
