"""Local host for the Chunk A page. Keys stay in the Keychain. Nothing is deployed.

    python3 -m core.realtime_voice.bench.serve

The page is http://127.0.0.1:8790/. Provider use is billed. The process stops
if either voice key is missing. It does not write the keys anywhere.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from flask import Flask, send_from_directory

from core.realtime_voice.bootstrap import create_bootstrap_blueprint
from core.realtime_voice.consult import ConsultLedger, create_consult_blueprint, stub_backend

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.dirname(os.path.abspath(__file__))
PORT = 8790


def keychain(service, account):
    done = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if done.returncode != 0 or not done.stdout.strip():
        raise SystemExit(f"Chunk A page stopped: no Keychain item {service}/{account}. Nothing was started.")
    return done.stdout.strip()


def load_key(env_name, service, account):
    if os.environ.get(env_name):
        return
    os.environ[env_name] = keychain(service, account)


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
    ledger = ConsultLedger()
    app.register_blueprint(create_bootstrap_blueprint(
        domain="bench", locale="en-US", get_persona=persona, is_production=lambda: False,
        local_owner=42, consult_ledger=ledger,
    ))
    app.register_blueprint(create_consult_blueprint(
        backend=stub_backend, ledger=ledger,
        csrf_ok=lambda: True,
        owner_id=lambda: 42,
        session_owner=ledger.owner_of,
    ))
    app.extensions["voice_ledger"] = ledger

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
    load_key("OPENAI_API_KEY", "openai", "api_key")
    load_key("XAI_API_KEY", "xai", "api_key")
    create_app().run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
