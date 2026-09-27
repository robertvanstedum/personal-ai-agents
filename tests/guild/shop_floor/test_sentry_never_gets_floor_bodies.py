"""Review B1c #5: the portal sends errors to Sentry when a DSN is configured,
and Sentry's Flask integration attaches request bodies. A Shop floor API body
(a note or post-it, before its payment details are scrubbed) must never be
attached. No DSN or secret is read here: a stand-in sentry_sdk records the
init call."""
from __future__ import annotations

import sys
import types

from minimoi_portal.guild_mounts import sentry_before_send

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)


def _event(url):
    return {"request": {"url": url, "method": "POST", "data": {"text": "Visa 4242 secret note"},
                        "query_string": "x=1", "cookies": {"session": "s"},
                        "headers": {"Cookie": "session=s", "X-CSRF-Token": "t", "User-Agent": "ua"}}}


def test_guild_api_bodies_are_dropped():
    for url in ("https://dev.minimoi.ai/guild-next/api/v1/notes", "http://localhost:5001/guild/api/v1/postits",
                "/guild-next/api/v1/postits/3/bin"):
        request = sentry_before_send(_event(url), None)["request"]
        assert "data" not in request and "query_string" not in request and "cookies" not in request
        assert request["headers"] == {"User-Agent": "ua"}


def test_other_portal_events_pass_unchanged():
    for url in ("https://dev.minimoi.ai/guild/build/items/3/status", "https://minimoi.ai/api/curator/x"):
        assert sentry_before_send(_event(url), None) == _event(url)
    assert sentry_before_send({"message": "no request"}, None) == {"message": "no request"}


def test_the_portal_installs_the_hook(load_portal, monkeypatch):
    calls = []
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kw: calls.append(kw)
    integrations = types.ModuleType("sentry_sdk.integrations")
    flask_mod = types.ModuleType("sentry_sdk.integrations.flask")
    flask_mod.FlaskIntegration = lambda: "flask-integration"
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", integrations)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.flask", flask_mod)
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.invalid/1")   # a stand-in, never contacted
    portal = load_portal()
    assert calls, "the portal did not initialise Sentry with a DSN present"
    assert calls[-1]["before_send"] is sentry_before_send
    assert calls[-1]["before_send_transaction"] is sentry_before_send
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
