"""Browser checks: the real Confer page through the portal, with the portal's
write guard on /app/cos. Opt-in (Playwright + Chrome), like the Shop floor's:

    venv/bin/python3 -m pytest tests/cos/browser_checks_confer.py -q -p no:cacheprovider

The portal runs on a loopback port. cos-scheduler is a stand-in on another
loopback port that renders the real cos_ui.html and serves the real voice
static files, and records what reaches it. There is no model call, no
network, and no microphone: the voice bootstrap answers 503 on purpose, so the
session stops before it would ask for one.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, request, send_from_directory
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parents[2]
OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}
SHOTS = os.environ.get("CONFER_SHOTS")


def _serve(app):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def _stand_in_cos(seen: list):
    app = Flask("cos_stand_in", template_folder=str(REPO / "domains/cos/templates"),
                static_folder=str(REPO / "domains/cos/static"))

    def note():
        seen.append({"method": request.method, "path": request.path,
                     "csrf": request.headers.get("X-CSRF-Token"),
                     "auth_id": request.headers.get("X-Minimoi-Auth-Id"),
                     "ctype": request.mimetype})

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
        note()
        return jsonify({"ok": False, "error": "stand-in stops before the microphone"}), 503

    @app.route("/ui/send", methods=["POST"])
    def send():
        note()
        return jsonify({"reply": f"Noted: {request.get_json()['text']}"})

    @app.route("/ui/bounce", methods=["POST"])
    def bounce():
        note()
        return "", 307, {"Location": "/ui/catch"}

    @app.route("/ui/catch", methods=["POST"])
    def catch():
        note()
        return jsonify({"caught": True})

    @app.route("/ui/transcribe", methods=["POST"])
    def transcribe():
        note()
        return jsonify({"transcript": f"{len(request.files['audio'].read())} bytes"})

    return app


@pytest.fixture(scope="module")
def stack():
    import minimoi_portal.config as cfg
    from minimoi_portal.app import app as portal
    seen: list = []
    cos_srv, cos_url = _serve(_stand_in_cos(seen))
    saved = cfg.COS_BACKEND
    cfg.COS_BACKEND = cos_url
    portal.config["SESSION_COOKIE_SECURE"] = False
    # Signed in by a session cookie the portal itself would issue (no test route on the shared app).
    cookie = portal.session_interface.get_signing_serializer(portal).dumps({"user": dict(OWNER)})
    portal_srv, portal_url = _serve(portal)
    yield {"url": portal_url, "cos_url": cos_url, "seen": seen,
           "cookie": {"name": portal.config.get("SESSION_COOKIE_NAME", "session"), "value": cookie,
                      "url": portal_url}}
    portal_srv.shutdown()
    cos_srv.shutdown()
    cfg.COS_BACKEND = saved


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def _confer(browser, stack):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    ctx.add_cookies([stack["cookie"]])
    page.goto(f"{stack['url']}/app/cos/ui/confer")
    token = page.locator('meta[name="minimoi-csrf-token"]').get_attribute("content")
    assert token and len(token) >= 40
    return ctx, page, token, errors


def test_confer_text_send_and_voice_bootstrap_carry_the_token(browser, stack):
    stack["seen"].clear()
    ctx, page, token, errors = _confer(browser, stack)
    page.fill("#send-input", "Plan the week")
    page.click("#btn-send")
    expect(page.locator("#chat-log")).to_contain_text("Noted: Plan the week")
    expect(page.locator("#btn-voice")).to_be_enabled()
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_contain_text("stand-in stops before the microphone")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/confer-send-and-voice.png")
    sends = [s for s in stack["seen"] if s["path"] == "/ui/send"]
    boots = [s for s in stack["seen"] if s["path"].endswith("/confer/bootstrap")]
    assert len(sends) == 1 and len(boots) == 1
    for s in sends + boots:          # they passed the portal's guard; the token itself stops at the portal
        assert s["csrf"] is None and s["auth_id"] == "1" and s["ctype"] == "application/json"
    assert not errors, errors
    ctx.close()


def _xhr(page, method, url, body_js, headers_js="{}"):
    """A request that bypasses the page's fetch (so it carries no token)."""
    return page.evaluate(f"""() => new Promise((resolve) => {{
        const x = new XMLHttpRequest();
        x.open('{method}', '{url}');
        for (const [k, v] of Object.entries({headers_js})) x.setRequestHeader(k, v);
        x.onload = () => resolve({{status: x.status, body: x.responseText}});
        x.onerror = () => resolve({{status: 0, body: ''}});
        x.send({body_js});
    }})""")


def test_transcribe_upload_carries_the_token_and_without_it_is_refused(browser, stack):
    stack["seen"].clear()
    ctx, page, token, errors = _confer(browser, stack)
    result = page.evaluate("""async () => {
        const form = new FormData();
        form.append('audio', new Blob([new Uint8Array(64)], {type: 'audio/webm'}), 'a.webm');
        const ok = await fetch('/app/cos/ui/transcribe', {method: 'POST', body: form});   // as Confer would
        return {ok: ok.status, okBody: await ok.json()};
    }""")
    bad = _xhr(page, "POST", "/app/cos/ui/transcribe",
               "(() => { const f = new FormData(); f.append('audio', new Blob([new Uint8Array(8)]), 'a.webm'); return f; })()")
    result.update(bad=bad["status"], badBody=json.loads(bad["body"]))
    assert result["ok"] == 200 and result["okBody"] == {"transcript": "64 bytes"}
    assert result["bad"] == 403 and result["badBody"]["error"] == "csrf"
    assert [s["path"] for s in stack["seen"]] == ["/ui/transcribe"]                  # only the good one arrived
    assert stack["seen"][0]["csrf"] is None and stack["seen"][0]["ctype"] == "multipart/form-data"
    assert not errors, errors
    ctx.close()


def test_a_request_without_the_token_cannot_send(browser, stack):
    stack["seen"].clear()
    ctx, page, token, errors = _confer(browser, stack)
    r = _xhr(page, "POST", "/app/cos/ui/send", "JSON.stringify({text: 'no token'})", "{'Content-Type': 'application/json'}")
    assert r["status"] == 403 and stack["seen"] == []
    ctx.close()


def test_the_token_is_never_sent_to_another_origin(browser, stack):
    stack["seen"].clear()
    ctx, page, token, errors = _confer(browser, stack)
    # The stand-in cos-scheduler is another origin (another port). A simple POST
    # reaches it (the page cannot read the answer); it must carry no token.
    page.evaluate(f"""async () => {{ try {{ await fetch('{stack["cos_url"]}/ui/transcribe', {{method: 'POST',
        body: (() => {{ const f = new FormData(); f.append('audio', new Blob([new Uint8Array(4)]), 'a.webm'); return f; }})()
    }}); }} catch (e) {{}} }}""")
    page.wait_for_timeout(300)
    assert [(s["path"], s["csrf"]) for s in stack["seen"]] == [("/ui/transcribe", None)]
    ctx.close()


def test_a_guarded_request_does_not_follow_a_redirect(browser, stack):
    stack["seen"].clear()
    ctx, page, token, errors = _confer(browser, stack)
    kind = page.evaluate("""async () => (await fetch('/app/cos/ui/bounce', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: '{}'})).type""")
    page.wait_for_timeout(300)
    assert kind == "opaqueredirect"
    assert [s["path"] for s in stack["seen"]] == ["/ui/bounce"]                    # /ui/catch was never requested
    ctx.close()
