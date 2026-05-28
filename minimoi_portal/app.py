"""
minimoi_portal/app.py — mini-moi portal.

Public landing page + authenticated access to Curator and Mein Deutsch.
Runs on port 5000. Cloudflare Tunnel routes minimoi.ai → here.

Run: cd ~/Projects/personal-ai-agents && venv/bin/python3 minimoi_portal/app.py
     PORTAL_PORT=5000 venv/bin/python3 minimoi_portal/app.py
"""

import json
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
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

# Lazy imports
from minimoi_portal import auth as _auth  # noqa: E402
from minimoi_portal import proxy as _proxy  # noqa: E402


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
        if not user or user.get("tier") != "owner":
            return redirect(url_for("dashboard"))
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


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@_require_login
def dashboard():
    user = _current_user()
    return render_template("dashboard.html", user=user)


# ── Curator proxy (owner + family only) ───────────────────────────────────────

@app.route("/app/curator")
@app.route("/app/curator/")
@_require_login
def curator_root():
    user = _current_user()
    if user["tier"] == "guest":
        return redirect(url_for("guest_briefing"))
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, "/", "/app/curator")


@app.route("/app/curator/<path:path>", methods=["GET", "POST", "DELETE", "PATCH"])
@_require_login
def curator_proxy(path):
    user = _current_user()
    if user["tier"] == "guest":
        return redirect(url_for("guest_briefing"))
    return _proxy.proxy_to(_cfg.CURATOR_BACKEND, path, "/app/curator")


# ── Mein Deutsch proxy (owner + family full; guest: lesen only) ───────────────

@app.route("/app/german")
@app.route("/app/german/")
@_require_login
def german_root():
    return _proxy.proxy_to(_cfg.GERMAN_BACKEND, "/", "/app/german")


@app.route("/app/german/<path:path>", methods=["GET", "POST"])
@_require_login
def german_proxy(path):
    user = _current_user()
    # Guests: only allowed landing (/) and /lesen + its API
    if user["tier"] == "guest":
        allowed = ("lesen", "api/lesen-category", "api/lesen-refresh",
                   "api/lesen-action", "api/translate", "api/save-phrase")
        if not any(path.startswith(p) for p in allowed):
            return redirect(url_for("german_root"))
    return _proxy.proxy_to(_cfg.GERMAN_BACKEND, path, "/app/german")


# ── Guest briefing view ───────────────────────────────────────────────────────

@app.route("/guest/briefing")
@_require_login
def guest_briefing():
    """Stripped-down today's briefing for guest users."""
    user = _current_user()

    # Load today's briefing from curator_latest.json
    latest_file = REPO_DIR / "curator_latest.json"
    articles = []
    briefing_date = None

    if latest_file.exists():
        try:
            data = json.loads(latest_file.read_text())
            raw = data if isinstance(data, list) else data.get("articles", [])
            # Strip sensitive/personal fields from guest view
            for a in raw[:20]:
                articles.append({
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

    return render_template(
        "guest_briefing.html",
        user=user,
        articles=articles,
        briefing_date=briefing_date,
    )


# ── Admin: guest management (owner only) ──────────────────────────────────────

@app.route("/admin/guests")
@_require_owner
def admin_guests():
    guests = _auth.list_guests()
    return render_template("admin_guests.html", user=_current_user(), guests=guests)


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
    app.run(host="localhost", port=port, debug=debug)


if __name__ == "__main__":
    main()
