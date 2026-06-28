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

from get_secret import get_secret

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

@app.after_request
def _no_cache_html(response):
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

# Sentry — init only if DSN is configured; silent no-op otherwise
def _init_sentry():
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        dsn = os.environ.get('SENTRY_DSN')
        if not dsn:
            try:
                dsn = get_secret('SENTRY_DSN')
            except Exception:
                return
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            environment=os.environ.get('FLASK_ENV', 'production'),
        )
    except ImportError:
        pass
_init_sentry()

# Jinja2 globals — timezone-aware so it subtracts cleanly from psycopg2 timestamps
from datetime import timezone as _tz
import re as _re
app.jinja_env.globals['now'] = lambda: datetime.now(_tz.utc)
app.jinja_env.globals['parse_dt'] = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None

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
from minimoi_portal import domain_auth as _dauth  # noqa: E402


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


def requires_domain(domain: str):
    """Decorator: requires login + access to the named domain.
    Owners bypass the domain check. Domain users must have a row in auth.domain_access."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = _current_user()
            if not user:
                return redirect(url_for("login", next=request.path))
            if user.get("tier") == "owner":
                return f(*args, **kwargs)
            auth_id = user.get("auth_id")
            if not auth_id:
                return render_template("access_denied.html", user=user), 403
            try:
                if not _dauth.has_domain_access(auth_id, domain):
                    return render_template("access_denied.html", user=user), 403
            except Exception:
                return render_template("access_denied.html", user=user), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


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


@app.route("/login/<token>")
def login_by_token(token):
    """One-time login link for domain users (daughters). Token is 48h, single-use."""
    if _current_user():
        return redirect(url_for("dashboard"))
    try:
        user = _dauth.consume_login_token(token)
    except Exception:
        user = None
    if not user:
        return render_template("login.html",
                               error="This link has expired or has already been used. "
                                     "Contact Robert to get a new one."), 400
    session.permanent = True
    session["user"] = {
        "username": user["email"],
        "display_name": user["name"],
        "tier": "guest",
        "auth_id": user["id"],
    }
    return redirect(url_for("profile_password"))


@app.route("/request-access", methods=["GET", "POST"])
def request_access():
    """Domain-specific access request form (hidden domain field)."""
    if _current_user():
        return redirect(url_for("dashboard"))

    domain = request.args.get("domain", "portuguese")
    error = None

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email        = request.form.get("email", "").strip().lower()
        reason       = request.form.get("reason", "").strip()
        domain       = request.form.get("domain", "portuguese")

        if not display_name:
            error = "Please enter your name."
        elif not email or "@" not in email:
            error = "Please enter a valid email address."
        else:
            _log_guest_request(display_name, email, reason, domain=domain)
            _notify_telegram_new_request(display_name, email, domain=domain)
            return render_template("request_access.html", submitted=True, domain=domain)

    return render_template("request_access.html", error=error, domain=domain)


@app.route("/profile/password", methods=["GET", "POST"])
@_require_login
def profile_password():
    """Set or change password after first login via token."""
    user = _current_user()
    auth_id = user.get("auth_id")
    if not auth_id:
        return redirect(url_for("dashboard"))

    error = None
    success = False

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            try:
                _dauth.set_password(auth_id, password)
                success = True
            except Exception:
                error = "Could not save password — please try again."

    return render_template("profile_password.html", user=user, error=error, success=success)


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
            reason = request.form.get("reason", "").strip()
            _log_guest_request(display_name, email, reason)
            _notify_telegram_new_request(display_name, email)
            return render_template("register.html", pending=True)

    return render_template("register.html", error=error)


def _notify_telegram_new_request(display_name: str, email: str, domain: str = "portuguese") -> None:
    """Fire a Telegram message to Robert when a new guest requests access."""
    try:
        token   = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
        chat_id = 8379221702
        text = (
            f"🔔 <b>Access Request</b>\n\n"
            f"<b>Name:</b> {display_name}\n"
            f"<b>Email:</b> {email}\n"
            f"<b>Domain:</b> {domain.title()}\n\n"
            f"Approve at:\n"
            f"https://minimoi.ai/guild"
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
    auth_id = user.get("auth_id")
    has_portuguese = (
        user.get("tier") == "owner"
        or (auth_id is not None and _dauth.has_domain_access(auth_id, "portuguese"))
    )
    return render_template("dashboard.html", user=user, has_portuguese=has_portuguese)


# ── Curator proxy (owner + family only; guests get deep dive HTML only) ───────

@app.route("/app/curator")
@app.route("/app/curator/")
@_require_login
def curator_root():
    user = _current_user()
    if user["tier"] == "guest":
        if user.get("auth_id"):
            return render_template("access_denied.html", user=user), 403
        return redirect(url_for("guest_briefing"))
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "/", "/app/curator", user=user)


@app.route("/app/curator/<path:path>", methods=["GET", "POST", "DELETE", "PATCH"])
@_require_login
def curator_proxy(path):
    user = _current_user()
    if user["tier"] == "guest":
        if user.get("auth_id"):
            return render_template("access_denied.html", user=user), 403
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
    if user.get("auth_id"):
        return render_template("access_denied.html", user=user), 403
    return _proxy.proxy_to(_cfg.GERMAN_BACKEND, "/", "/app/german", user=user)


@app.route("/app/german/<path:path>", methods=["GET", "POST"])
@_require_login
def german_proxy(path):
    user = _current_user()
    if user.get("auth_id"):
        return render_template("access_denied.html", user=user), 403
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
    """Daily briefing for legacy guest users (non-domain-auth) only."""
    user = _current_user()
    if user.get("auth_id"):
        return render_template("access_denied.html", user=user), 403

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
    if user.get("auth_id"):
        return jsonify({"error": "Forbidden"}), 403
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
    if user.get("auth_id"):
        return jsonify({"error": "Forbidden"}), 403
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
    if user.get("auth_id"):
        return jsonify({"error": "Forbidden"}), 403
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
        token   = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
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
    from email.mime.text import MIMEText

    to_email = guest.get("email", "")
    if not to_email:
        return

    name = guest.get("display_name", "there")

    try:
        app_password = get_secret("GMAIL_APP_PASSWORD", "gmail", "app_password")
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


def _send_login_link_email(email: str, name: str, token: str) -> bool:
    """Send one-time login link via AWS SES. Returns True on success."""
    from utils.role import is_production
    link = f"https://minimoi.ai/login/{token}"
    try:
        import boto3
        ses = boto3.client("ses", region_name="us-east-1")
        ses.send_email(
            Source="noreply@minimoi.ai",
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": "Your mini-moi access is ready"},
                "Body": {
                    "Text": {"Data": (
                        f"Hi {name},\n\n"
                        f"Your access has been approved.\n\n"
                        f"Click here to log in (link expires in 48 hours):\n"
                        f"{link}\n\n"
                        f"You'll be able to set your password after logging in.\n\n"
                        f"mini-moi"
                    )}
                },
            },
        )
        return True
    except Exception as e:
        print(f"⚠️  SES login link email failed: {e}", flush=True)
        if not is_production():
            print(f"🔗 DEV login link: {link}", flush=True)
        return False


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


_DEFAULT_DB_URL = "postgresql://minimoi:simple123@localhost:5432/personal_agents"

# File-first build queue — source of truth for UI; DB is analytics/archive only
_BQ_PATH = Path(__file__).parent.parent / "data" / "guild" / "build_queue.json"


def _load_build_queue() -> list:
    """Load build items from JSON. Returns [] with a warning if file is missing."""
    try:
        return json.loads(_BQ_PATH.read_text())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("build_queue.json missing or invalid: %s", e)
        return []


def _save_build_queue(items: list):
    """Write build items back to JSON."""
    _BQ_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False))


def _guild_db_query(sql, params=None):
    """Run a SELECT against the personal_agents DB. Returns list of dicts."""
    import psycopg2, psycopg2.extras
    db_url = os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        rows = list(cur.fetchall())
    conn.close()
    return rows


def _guild_db_execute(sql, params=None):
    """Run an INSERT/UPDATE/DELETE against the personal_agents DB."""
    import psycopg2
    db_url = os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)
    conn = psycopg2.connect(db_url)
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
    conn.commit()
    conn.close()


def _career_deadline(career_focus: dict) -> str:
    """Format the deadline from cos_context career_focus for display."""
    raw = career_focus.get("deadline", "")
    if not raw:
        return ""
    try:
        from datetime import datetime
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%b %-d, %Y")
    except Exception:
        return raw


def _log_guest_request(name: str, email: str, reason: str, domain: str = "portuguese") -> None:
    """Log a guest access request to guild.guest_requests. Silent on failure."""
    try:
        _guild_db_execute(
            "INSERT INTO guild.guest_requests (name, email, reason, domain) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (email) DO NOTHING",
            [name, email, reason or "", domain]
        )
    except Exception:
        pass


def _get_guest_requests() -> list:
    """Return guest requests sorted: pending first, then actioned, newest first."""
    try:
        return _guild_db_query(
            "SELECT id, name, email, reason, domain, requested_at, status, actioned_at"
            " FROM guild.guest_requests"
            " ORDER BY CASE status WHEN 'requested' THEN 0 ELSE 1 END,"
            " requested_at DESC"
        )
    except Exception:
        return []


def _update_guest_request_status(req_id: int, status: str) -> None:
    _guild_db_execute(
        "UPDATE guild.guest_requests SET status=%s, actioned_at=NOW() WHERE id=%s",
        [status, req_id]
    )


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

    guest_requests = _get_guest_requests()

    return render_template("guild/guild_landing.html",
        ops=ops,
        career=career,
        build=build,
        ahead=ahead,
        guest_requests=guest_requests,
        user=_current_user()
    )


@app.route("/guild/guest-requests/<int:req_id>/status", methods=["POST"])
@_require_owner
def update_guest_request_status(req_id):
    status = request.form.get("status")
    if status not in ("granted", "rejected"):
        return redirect(url_for("guild_landing"))

    if status == "granted":
        rows = _guild_db_query(
            "SELECT name, email, domain FROM guild.guest_requests WHERE id=%s", [req_id]
        )
        if rows:
            req = rows[0]
            try:
                user_id = _dauth.create_user(req["email"], req["name"])
                _dauth.grant_domain_access(user_id, req.get("domain") or "portuguese")
                token = _dauth.create_login_token(user_id)
                ok = _send_login_link_email(req["email"], req["name"], token)
                if not ok:
                    _update_guest_request_status(req_id, "approved_pending_email")
                    return redirect(url_for("guild_landing"))
            except Exception as e:
                print(f"⚠️  Auth provisioning failed for request {req_id}: {e}")

    _update_guest_request_status(req_id, status)
    return redirect(url_for("guild_landing"))


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

    deadline = _career_deadline(targeting)

    return render_template(
        "guild/career_focus.html",
        opportunities=opportunities,
        total=len(opportunities),
        status_filter=status_filter,
        geo_filter=geo_filter,
        type_filter=type_filter,
        priority_filter=priority_filter,
        deadline=deadline,
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

    try:
        import json as _json
        _cf = _json.loads(
            (__import__('pathlib').Path(__file__).parent.parent /
             "domains/guild/config/cos_context.json").read_text()
        ).get("career_focus", {})
    except Exception:
        _cf = {}

    return render_template("guild/career_active.html", positions=positions, deadline=_career_deadline(_cf))


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


@app.route("/guild/career-focus/edit")
@_require_owner
def guild_career_focus_edit():
    ctx_path = REPO_DIR / "domains/guild/config/cos_context.json"
    try:
        ctx = json.loads(ctx_path.read_text())
    except Exception:
        ctx = {}
    cf = ctx.get("career_focus", {})
    return render_template(
        "guild/career_focus_editor.html",
        goal_text=cf.get("goal_text", cf.get("urgency_note", "")),
        deadline=cf.get("deadline", ""),
        deadline_label=cf.get("deadline_label", ""),
        milestones=cf.get("milestones", []),
        user=_current_user(),
    )


@app.route("/guild/career-focus/save", methods=["POST"])
@_require_owner
def guild_career_focus_save():
    ctx_path = REPO_DIR / "domains/guild/config/cos_context.json"
    try:
        ctx = json.loads(ctx_path.read_text())
    except Exception:
        return jsonify({"status": "error", "message": "Could not read config"}), 500

    data = request.get_json(silent=True) or {}
    cf = ctx.setdefault("career_focus", {})

    goal_text = (data.get("goal_text") or "").strip()[:120]
    deadline = (data.get("deadline") or "").strip()
    deadline_label = (data.get("deadline_label") or "").strip()[:60]
    milestones = data.get("milestones", [])

    if not goal_text:
        return jsonify({"status": "error", "message": "Goal text is required"}), 400
    if not deadline:
        return jsonify({"status": "error", "message": "Deadline date is required"}), 400

    # Validate and sanitise milestones
    clean_milestones = []
    for i, m in enumerate(milestones[:20]):
        label = (m.get("label") or "").strip()[:60]
        date = (m.get("date") or "").strip()
        status = m.get("status", "pending") if m.get("status") in ("pending", "completed") else "pending"
        if not label or not date:
            continue
        clean_milestones.append({
            "id": m.get("id") or f"m{i+1}",
            "date": date,
            "label": label,
            "domain": "career",
            "status": status,
        })

    cf["goal_text"] = goal_text
    cf["urgency_note"] = goal_text  # keep in sync for dashboard display
    cf["deadline"] = deadline
    cf["deadline_label"] = deadline_label
    cf["milestones"] = clean_milestones

    try:
        ctx_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok"})


# ── Build Clarity ─────────────────────────────────────────────────────────────

@app.route("/guild/build")
@_require_owner
def guild_build():
    status_filter = request.args.get('status', 'all')
    items = _load_build_queue()
    if status_filter != 'all':
        items = [i for i in items if i.get('status') == status_filter]
    # Sort: most recently transitioned first (items without timestamp go last)
    items.sort(key=lambda i: i.get('last_transition_at') or '', reverse=True)
    return render_template("guild/build_log.html", items=items,
                           status_filter=status_filter, user=_current_user())


@app.route("/guild/build/queue")
@_require_owner
def guild_build_queue():
    _STATUS_RANK = {"blocked": 1, "in_build": 2, "spec_ready": 3,
                    "incomplete": 4, "done": 5}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    all_items = _load_build_queue()
    items = [
        i for i in all_items
        if i.get('status') in ('spec_ready', 'in_build', 'blocked', 'incomplete')
        or (i.get('status') == 'done' and (i.get('last_transition_at') or '') >= cutoff)
    ]
    items.sort(key=lambda i: (
        _STATUS_RANK.get(i.get('status', ''), 9),
        -(hash(i.get('last_transition_at') or ''))  # stable secondary sort
    ))
    return render_template("guild/build_queue.html", items=items,
                           user=_current_user())


@app.route("/guild/build/spec/<path:filename>")
@_require_owner
def guild_build_spec(filename):
    import markdown as _md
    from pathlib import Path
    import re
    if not re.match(r'^[\w\-\. ()]+\.md$', filename):
        return "Not found", 404
    repo_root = Path(__file__).parent.parent
    candidates = [
        repo_root / "docs" / "specs" / filename,
        repo_root / "docs" / filename,
        repo_root / "_working" / filename,
    ]
    spec_path = next((p for p in candidates if p.exists()), None)
    if not spec_path:
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
    if not re.match(r'^[\w\-\. ()]+\.md$', filename):
        return "Not found", 404
    repo_root = Path(__file__).parent.parent
    candidates = [
        repo_root / "docs" / "specs" / filename,
        repo_root / "docs" / filename,
        repo_root / "_working" / filename,
    ]
    spec_path = next((p for p in candidates if p.exists()), None)
    if not spec_path:
        return "Not found", 404
    raw = spec_path.read_text()
    return Response(
        raw,
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.route("/guild/build/items/<int:item_id>/check")
@_require_owner
def guild_build_check(item_id):
    """Re-run completeness check on a spec file and return current failures as JSON."""
    from pathlib import Path
    items = _load_build_queue()
    item = next((i for i in items if i.get('id') == item_id), None)
    if not item:
        return jsonify({"error": "not found"}), 404
    spec_file = item.get("spec_file")
    if not spec_file:
        return jsonify({"error": "no spec file attached to this entry"}), 400
    repo_root = Path(__file__).parent.parent
    candidates = [
        repo_root / "docs" / "specs" / spec_file,
        repo_root / "docs" / spec_file,
        repo_root / "_working" / spec_file,
    ]
    spec_path = next((p for p in candidates if p.exists()), None)
    if not spec_path:
        return jsonify({"error": f"{spec_file} not found", "failures": [f"file missing: {spec_file}"]})
    content = spec_path.read_text()
    _lower = content.lower()
    failures = []
    if "## definition of done" not in _lower:
        failures.append("missing ## Definition of Done section")
    if "## commit" not in _lower:
        failures.append("missing ## Commit section")
    current_status = "spec_ready" if not failures else "incomplete"
    json_status = item.get("status")
    return jsonify({
        "spec_file": spec_file,
        "current_status": current_status,
        "db_status": json_status,
        "stale": json_status != current_status,
        "failures": failures,
    })


@app.route("/guild/build/roadmap")
@_require_owner
def guild_build_roadmap():
    import markdown as _md
    import subprocess
    import re as _re
    roadmap_path = Path(__file__).parent.parent / "docs/ROADMAP.md"

    # Git last-modified date
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "docs/ROADMAP.md"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent)
        )
        last_modified = result.stdout.strip()[:10] if result.stdout.strip() else None
    except Exception:
        last_modified = None
    if not last_modified:
        try:
            from datetime import datetime as _dt
            last_modified = _dt.fromtimestamp(roadmap_path.stat().st_mtime).strftime('%Y-%m-%d')
        except Exception:
            last_modified = None

    try:
        raw = roadmap_path.read_text()
        content = _md.markdown(raw, extensions=["fenced_code", "tables"])
    except Exception:
        content = "<p><em>Roadmap file not found.</em></p>"

    # Status badges + row classes — process <tr> blocks to add row-level classes
    _STATUS = {
        'target':      ('status-target',      'target'),
        'queued':      ('status-queued',       'queued'),
        'in_progress': ('status-in-progress',  'in progress'),
        'done':        ('status-done',         'done'),
    }

    def _process_row(m):
        row_html = m.group(0)
        row_classes = []
        if _re.search(r'<td><strong>[^<]+</strong></td>', row_html):
            row_classes.append('row-parent')
        elif _re.search(r'<td>·', row_html):
            row_classes.append('row-child')
        for val, (cls, label) in _STATUS.items():
            if f'<td>{val}</td>' in row_html:
                row_html = row_html.replace(
                    f'<td>{val}</td>',
                    f'<td><span class="status-badge {cls}">{label}</span></td>'
                )
                row_classes.append(f'row-{val}')
                break
        if row_classes:
            row_html = row_html.replace('<tr>', f'<tr class="{" ".join(row_classes)}">', 1)
        return row_html

    content = _re.sub(r'<tr>.*?</tr>', _process_row, content, flags=_re.DOTALL)

    # Source doc links — link cells that exactly match a docs/ filename
    _docs_files = {f.name for f in (Path(__file__).parent.parent / "docs").iterdir()
                   if f.is_file() and f.suffix == '.md'}
    _gh_base = "https://github.com/robertvanstedum/personal-ai-agents/blob/main/docs/"

    def _linkify(m):
        cell = m.group(1).strip()
        if cell in _docs_files:
            return f'<td><a href="{_gh_base}{cell}" target="_blank">{cell}</a></td>'
        return m.group(0)

    content = _re.sub(r'<td>([^<]+)</td>', _linkify, content)

    # Domain cards — wrap domain ## sections in a card div (skip "How this works")
    def _wrap_domain(m):
        heading = m.group(1)
        if 'How this works' in heading:
            return heading
        return f'</div><div class="roadmap-domain">{heading}'
    content = _re.sub(r'(<h2>[^<]+</h2>)', _wrap_domain, content)
    # Replace FIRST </div> prefix (before first real domain card) cleanly
    content = content.replace('</div><div class="roadmap-domain">', '<div class="roadmap-domain">', 1)
    content += '</div>'  # close last card

    # Section h3 styling — discussion first (contains "agreed"), then agreed
    content = _re.sub(
        r'<h3>([^<]*[Dd]iscussion[^<]*)</h3>',
        r'<h3 class="h3-discussion">\1</h3>',
        content
    )
    content = _re.sub(
        r'<h3>([^<]*[Aa]greed[^<]*)</h3>',
        r'<h3 class="h3-agreed">\1</h3>',
        content
    )

    return render_template("guild/build_roadmap.html", content=content,
                           last_modified=last_modified, user=_current_user())


@app.route("/guild/build/items/<int:item_id>/status", methods=["POST"])
@_require_owner
def update_build_status(item_id):
    new_status = request.form.get('status')
    note = request.form.get('note') or None
    valid = {'design', 'spec_ready', 'in_build', 'blocked', 'done', 'deferred', 'incomplete'}
    if new_status not in valid:
        return redirect(url_for('guild_build_queue'))

    # ── 1. Update JSON (source of truth) ──────────────────────────────────────
    items = _load_build_queue()
    item = next((i for i in items if i.get('id') == item_id), None)
    current_status = item.get('status') if item else None
    if item:
        item['status'] = new_status
        item['last_transition_at'] = datetime.now(timezone.utc).isoformat()
        item['blocked_reason'] = (note if new_status == 'blocked' else None)
        _save_build_queue(items)

    # ── 2. DB audit log (write-only, non-blocking) ────────────────────────────
    try:
        if current_status is not None:
            _guild_db_execute(
                "INSERT INTO guild.design_log_transitions "
                "(design_log_id, from_status, to_status, triggered_by, reason) "
                "VALUES (%s,%s,%s,'robert',%s)",
                (item_id, current_status, new_status, note)
            )
    except Exception:
        pass

    # ── 3. On done/deferred, ask dev_agent to archive the spec file ───────────
    if new_status in ('done', 'deferred') and item and item.get('spec_file'):
        try:
            _requests.post(
                'http://localhost:8771/archive-spec',
                json={'spec_file': item['spec_file']},
                timeout=2,
            )
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
    items = _load_build_queue()
    item = next((i for i in items if i.get('id') == item_id), None)
    if item:
        if spec_title is not None:
            item['spec_title'] = spec_title
        if summary is not None:
            item['summary'] = summary
        if github_issue is not None:
            item['github_issue'] = github_issue
        _save_build_queue(items)
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
    valid_status = {'design', 'spec_ready', 'in_build', 'blocked', 'incomplete', 'deferred'}
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


# ── Guild Docs ────────────────────────────────────────────────────────────────

_DOCS_DIR = Path(__file__).parent.parent / "docs"
_DR_DIR   = _DOCS_DIR / "decision-records"
_REPO_DIR = Path(__file__).parent.parent

def _docs_git_date(rel_path: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%ci", rel_path],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent)
        )
        return r.stdout.strip()[:10] if r.stdout.strip() else None
    except Exception:
        return None

def _docs_read_meta(path: Path) -> dict:
    """Return {title, subtitle} from the first two lines of a markdown file."""
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return {"title": path.stem, "subtitle": ""}
    title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), path.stem)
    subtitle = next((l.lstrip("*").rstrip("*").strip() for l in lines[1:6]
                     if l.strip() and not l.startswith("#")), "")
    return {"title": title, "subtitle": subtitle}

_DOCS_CORE = {
    "ROADMAP.md", "SERVICES.md", "WORKSPACE-SETUP.md",
    "LLM_REGISTRY.md", "GERMAN.md", "GUILD.md", "DB_SCHEMA.md",
}

def _docs_group_files(files):
    order = ["Core", "Curator", "German", "Guild", "Infrastructure", "Process", "Portfolio", "Releases"]
    groups = {k: [] for k in order}
    for f in files:
        n = f.name
        parent = f.parent.name
        if n in _DOCS_CORE:
            groups["Core"].append(f)
        elif parent == "releases" or "_RELEASE" in n:
            groups["Releases"].append(f)
        elif parent in ("portfolio", "test-reports") or n.startswith(("CASE_STUDY", "CASE-STUDY", "AI_TOOLS", "CURATOR_ENHANCEMENT", "phase3c")):
            groups["Portfolio"].append(f)
        elif n.startswith(("GERMAN", "GESPRACHE", "LANGUAGE_CASE")):
            groups["German"].append(f)
        elif n.startswith(("GUILD", "COS_", "OPS_")):
            groups["Guild"].append(f)
        elif n.startswith(("CURATOR", "curator", "BUILD_WS", "PLAN_WS", "PLAN_Cost", "FEATURE_", "INTELLIGENCE_", "DESIGN_SESSION_INTEL", "NEXT_PHASE")):
            groups["Curator"].append(f)
        elif n.startswith(("DECISION_RECORD", "DESIGN_SESSION_PROMPT", "HANDOFF", "DESIGN_UI", "OPENCLAW")):
            groups["Process"].append(f)
        elif n.startswith(("AWS_", "CODE_", "LEARNING_", "PLAN_")) or parent in ("poc", "design"):
            groups["Infrastructure"].append(f)
        else:
            groups["Infrastructure"].append(f)
    return {k: v for k, v in groups.items() if v}

def _dr_parse_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text()
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    title = next((l.lstrip("# ").strip() for l in text[end+3:].splitlines()
                  if l.startswith("# ")), path.stem)
    fm["title"] = title
    return fm


@app.route("/guild/docs")
@_require_owner
def guild_docs():
    try:
        _skip = _DR_DIR.resolve()
        files = sorted(
            [
                f for f in _DOCS_DIR.rglob("*.md")
                if f.is_file()
                and not str(f.resolve()).startswith(str(_skip))
                and f.name != "README.md"
            ],
            key=lambda f: f.name
        )
    except Exception:
        files = []
    groups = _docs_group_files(files)
    entries = {}

    # Prepend repo README to Core
    readme = _REPO_DIR / "README.md"
    readme_meta = _docs_read_meta(readme) if readme.exists() else {"title": "Project README"}
    entries["Core"] = [{
        "filename": "_readme",
        "title": readme_meta["title"] or "Project README",
        "date": _docs_git_date("README.md"),
    }]

    for group, gfiles in groups.items():
        if group not in entries:
            entries[group] = []
        for f in gfiles:
            meta = _docs_read_meta(f)
            rel = str(f.relative_to(_DOCS_DIR))
            entries[group].append({
                "filename": rel,
                "title": meta["title"],
                "date": _docs_git_date(f"docs/{rel}"),
            })
    dr_count = len([f for f in _DR_DIR.iterdir()
                    if f.is_file() and f.suffix == ".md" and f.name != "README.md"]) \
               if _DR_DIR.exists() else 0
    return render_template("guild/docs_list.html", entries=entries,
                           dr_count=dr_count, user=_current_user())


@app.route("/guild/docs/decisions")
@_require_owner
def guild_docs_decisions():
    drs = []
    if _DR_DIR.exists():
        for f in sorted(_DR_DIR.iterdir(), reverse=True):
            if not f.is_file() or f.suffix != ".md" or f.name == "README.md":
                continue
            fm = _dr_parse_frontmatter(f)
            date = f.name[3:13] if f.name.startswith("dr_") and len(f.name) > 13 else None
            drs.append({
                "filename": f"decision-records/{f.name}",
                "title": fm.get("title", f.stem),
                "subtitle": fm.get("subtitle", ""),
                "date": date,
                "domain": fm.get("domain", ""),
                "status": fm.get("status", "active"),
                "dr_type": fm.get("dr-type", "design"),
                "lora_candidate": fm.get("lora-candidate", "no") == "yes",
            })
    return render_template("guild/docs_decisions.html", records=drs, user=_current_user())


@app.route("/guild/docs/_readme")
@_require_owner
def guild_docs_readme():
    import markdown as _md
    target = _REPO_DIR / "README.md"
    if not target.exists():
        return "Not found", 404
    raw = target.read_text()
    content = _md.markdown(raw, extensions=["fenced_code", "tables"])
    date = _docs_git_date("README.md")
    gh_url = "https://github.com/robertvanstedum/personal-ai-agents/blob/main/README.md"
    return render_template("guild/docs_reader.html", content=content,
                           filename="README.md", date=date, gh_url=gh_url,
                           user=_current_user())


@app.route("/guild/docs/<path:filename>")
@_require_owner
def guild_docs_reader(filename):
    import markdown as _md
    # Path traversal guard: resolved path must be inside docs/
    try:
        target = (_DOCS_DIR / filename).resolve()
        if not str(target).startswith(str(_DOCS_DIR.resolve())):
            return "Not found", 404
        if not target.exists() or target.suffix != ".md":
            return "Not found", 404
    except Exception:
        return "Not found", 404

    raw = target.read_text()
    content = _md.markdown(raw, extensions=["fenced_code", "tables"])
    date = _docs_git_date(f"docs/{filename}")
    gh_url = f"https://github.com/robertvanstedum/personal-ai-agents/blob/main/docs/{filename}"
    return render_template("guild/docs_reader.html", content=content,
                           filename=filename, date=date, gh_url=gh_url,
                           user=_current_user())


@app.route("/app/portuguese")
@app.route("/app/portuguese/")
@requires_domain("portuguese")
def portuguese_root():
    return _proxy.proxy_to(_cfg.PORTUGUESE_BACKEND, "/", "/app/portuguese",
                           user=_current_user())


@app.route("/app/portuguese/<path:path>", methods=["GET", "POST"])
@requires_domain("portuguese")
def portuguese_proxy(path):
    user = _current_user()
    if path.startswith("admin") and user.get("tier") != "owner":
        return render_template("access_denied.html", user=user), 403
    return _proxy.proxy_to(_cfg.PORTUGUESE_BACKEND, path, "/app/portuguese",
                           user=user)


@app.route("/guild/users")
@_require_owner
def guild_users():
    """Admin: list all domain users with access."""
    try:
        users = _dauth.list_users_with_access()
    except Exception:
        users = []
    return render_template("guild/users.html", users=users, user=_current_user())


@app.route("/health")
def health():
    return {"status": "ok", "service": "portal"}

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = _cfg.PORT
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
