"""Local host for the Chunk A page. Keys stay in the Keychain. Nothing is deployed.

    python3 core/realtime_voice/bench/serve.py

The page is http://127.0.0.1:8766/. Provider use is billed. The process stops
if either voice key is missing. It does not write the keys anywhere.
"""
import os
import subprocess

from flask import Flask, send_from_directory

from core.realtime_voice.bootstrap import create_bootstrap_blueprint
from core.realtime_voice.consult import ConsultLedger, create_consult_blueprint, stub_backend

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.dirname(os.path.abspath(__file__))
PORT = 8766


def keychain(service, account):
    done = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if done.returncode != 0 or not done.stdout.strip():
        raise SystemExit(f"Chunk A page stopped: no Keychain item {service}/{account}. Nothing was started.")
    return done.stdout.strip()


def persona(name):
    if name != "bench":
        return None
    return {
        "name": "Bench",
        "prompt_txt": "You are the bench voice. English by default. A real German sentence is answered in German.",
        "scenes": {},
        "voices": {},
        "tools": [{
            "type": "function", "name": "consult",
            "description": "Ask the stub.",
            "parameters": {"type": "object", "properties": {}},
        }],
    }


def create_app():
    app = Flask(__name__)
    app.register_blueprint(create_bootstrap_blueprint(
        domain="bench", locale="en-US", get_persona=persona, is_production=lambda: False,
    ))
    ledger = ConsultLedger()
    app.register_blueprint(create_consult_blueprint(
        backend=stub_backend, ledger=ledger,
        csrf_ok=lambda: True,
        owner_id=lambda: "local",
        session_owner=lambda session_id: "local" if session_id else None,
    ))

    @app.route("/")
    def page():
        return send_from_directory(BENCH, "a_page.html")

    @app.route("/page.js")
    def record():
        return send_from_directory(BENCH, "page.js")

    @app.route("/static/realtime-voice/<path:filename>")
    def voice_static(filename):
        return send_from_directory(os.path.join(ROOT, "static"), filename)

    return app


def main():
    os.environ.setdefault("OPENAI_API_KEY", keychain("OPENAI_API_KEY", "OPENAI_API_KEY"))
    os.environ.setdefault("XAI_API_KEY", keychain("XAI_API_KEY", "XAI_API_KEY"))
    create_app().run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
