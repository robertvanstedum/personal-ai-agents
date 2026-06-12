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
    return render_template("guild/guild_landing.html", user=_current_user())


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

    # Count board-qualifying positions for contextual link
    board_sql = (
        "SELECT COUNT(*) AS cnt FROM pipeline.items "
        "WHERE (priority = TRUE AND status = 'applied') "
        "OR status = 'reviewing' "
        "OR status = 'interview' "
        "OR (status = 'closed' AND closed_at > NOW() - INTERVAL '5 days')"
    )
    try:
        board_count = int(_guild_db_query(board_sql)[0]['cnt'])
    except Exception:
        board_count = 0

    return render_template(
        "guild/career_focus.html",
        opportunities=opportunities,
        total=len(opportunities),
        status_filter=status_filter,
        geo_filter=geo_filter,
        type_filter=type_filter,
        priority_filter=priority_filter,
        board_count=board_count,
        deadline="Aug 1, 2026",
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

    return render_template(
        "guild/career_active.html",
        positions=positions,
        deadline="Aug 1, 2026",
    )


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
    if referrer == "active":
        return redirect(url_for("guild_career_active"))
    return redirect(url_for("guild_career"))


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
    if referrer == "active":
        return redirect(url_for("guild_career_active"))
    return redirect(url_for("guild_career"))


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = _cfg.PORT
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="localhost", port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
