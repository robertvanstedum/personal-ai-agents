"""
html_server.py — Portuguese language domain shell.

Shell build (Spec 2) — stub pages only. Content added in later specs.
Run: venv/bin/python3 html_server.py
     PORT=8770 venv/bin/python3 html_server.py
"""

import os
import secrets
from pathlib import Path
from flask import Flask, render_template, jsonify
from flask_cors import CORS

BASE_DIR  = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent.parent

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
                import sys
                sys.path.insert(0, str(REPO_ROOT))
                from get_secret import get_secret
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
    return render_template("portuguese_conversas.html", active="conversas",
                           show_toggle=True)


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    port = int(os.environ.get("PORT", 8770))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
