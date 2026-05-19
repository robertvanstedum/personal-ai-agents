"""
html_server.py — German language HTML interface.

Both telegram_bot.py and html_server.py import from german_domain.
Run: venv/bin/python3 html_server.py
     PORT=8765 venv/bin/python3 html_server.py
"""

import os
from pathlib import Path
from flask import Flask, render_template, redirect
from flask_cors import CORS
from german_domain import GERMAN_DIR

BASE_DIR = Path(__file__).parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
CORS(app)


@app.route("/")
def index():
    return redirect("/lesen")


@app.route("/lesen")
def lesen():
    return render_template("german_lesen.html", active="lesen")


@app.route("/schreiben")
def schreiben():
    return render_template("german_schreiben.html", active="schreiben")


@app.route("/ueben")
def ueben():
    return render_template("german_ueben.html", active="ueben")


@app.route("/bibliothek")
def bibliothek():
    return render_template("german_bibliothek.html", active="bibliothek")


@app.route("/admin")
def admin():
    return render_template("german_admin.html", active="admin")


@app.route("/archiv")
def archiv():
    return render_template("german_archiv.html", active="archiv")


def main():
    port = int(os.environ.get("PORT", 8767))
    app.run(host="localhost", port=port, debug=True)


if __name__ == "__main__":
    main()
