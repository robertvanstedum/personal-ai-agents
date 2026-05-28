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
        # Guests can see all German pages except Admin
        if path.startswith("admin"):
            return redirect(url_for("german_root"))
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = _cfg.PORT
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="localhost", port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
