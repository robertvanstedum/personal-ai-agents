"""Browser check for #268: a failed voice start returns Confer's voice button
to its start state. Opt-in (Playwright + Chrome):

    venv/bin/python3 -m pytest tests/cos/browser_checks_confer_voice.py -q -p no:cacheprovider

A stand-in cos-scheduler on a loopback port renders the real cos_ui.html and
serves the real voice static files. Its voice bootstrap answers an error, so
no provider, no microphone and no model is ever reached.
"""
from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, send_from_directory
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cos():
    boots = []
    app = Flask("cos_voice_stand_in", template_folder=str(REPO / "domains/cos/templates"),
                static_folder=str(REPO / "domains/cos/static"))

    @app.route("/ui/<tab>")
    def ui(tab):
        return render_template("cos_ui.html", initial_tab=tab)

    @app.route("/static/realtime-voice/<path:name>")
    def voice_static(name):
        return send_from_directory(REPO / "core/realtime_voice/static", name)

    @app.route("/status")
    def status():
        return jsonify({"backend_label": "stand-in", "model_label": "none"})

    @app.route("/ui/api/<what>")
    def lists(what):
        return jsonify([])

    @app.route("/api/realtime-voice/confer/capabilities")
    def capabilities():
        return jsonify({"ok": True, "default_provider": "openai",
                        "providers": [{"provider": "openai", "label": "OpenAI Voice"}]})

    @app.route("/api/realtime-voice/confer/bootstrap", methods=["POST"])
    def bootstrap():
        boots.append(1)
        return jsonify({"ok": False, "error": "voice is unavailable right now"}), 503

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{port}", "boots": boots}
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def test_a_failed_voice_start_returns_the_button_to_start(browser, cos):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{cos['url']}/ui/confer")
    button = page.locator("#btn-voice")
    expect(button).to_be_enabled()
    for attempt in (1, 2):                    # the second tap starts again; it does not try to stop a ghost session
        button.click()
        expect(page.locator("#voice-transcript")).to_contain_text("Voice error: voice is unavailable right now")
        expect(button).to_have_text("🎤")
        expect(button).to_have_attribute("aria-label", "Start voice conversation")
        expect(button).not_to_have_class("recording")
        expect(page.locator("#voice-status")).to_have_text("Voice did not start")
        expect(page.locator("#voice-provider-select")).to_be_enabled()
        assert len(cos["boots"]) == attempt
    assert not errors, errors
    ctx.close()
