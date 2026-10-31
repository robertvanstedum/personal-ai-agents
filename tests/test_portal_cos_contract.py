"""The portal's /app/cos proxy keeps the contract production's Confer page script already speaks.

The CSRF guard on /app/cos writes is paired with Confer's page script (it sends the token). Production still runs the earlier script,
which sends none, so on a production origin the guard must stay off and text and voice chat must reach cos-scheduler exactly as before.
A staging origin has the paired script, so there the guard stays on (a write without the token is refused, a write with it is
forwarded). MINIMOI_COS_WRITE_GUARD overrides either way."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "guild" / "shop_floor"))
from floor_helpers import load_portal  # noqa: E402,F401  (pytest fixture)

TEXT = {"text": "synthetic test", "channel": "html_text", "conversation_id": "owner", "request_id": "compat-test"}


def _portal(load_portal, monkeypatch, base_url):
    portal = load_portal(base_url=base_url, next_flag=None)
    forwarded = []
    monkeypatch.setattr(portal.module._proxy, "proxy_to", lambda *a, **kw: forwarded.append((a, kw)) or ("ok", 200))
    return portal, forwarded


def test_production_text_chat_without_a_token_reaches_cos_as_before(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch, "https://minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    forwarded.clear()
    response = client.post("/app/cos/ui/send", json=TEXT)                      # the earlier page script: JSON, no X-CSRF-Token
    assert response.status_code == 200 and len(forwarded) == 1
    assert forwarded[0][0][1] == "ui/send"


def test_production_voice_upload_without_a_token_reaches_cos_as_before(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch, "https://minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    forwarded.clear()
    response = client.post("/app/cos/ui/transcribe", data={"audio": (io.BytesIO(b"RIFF....WAVE"), "clip.wav")},
                           content_type="multipart/form-data")
    assert response.status_code == 200 and len(forwarded) == 1 and forwarded[0][0][1] == "ui/transcribe"


def test_production_pages_always_carry_the_token_meta_and_writes_without_it_still_reach_cos(load_portal, monkeypatch):
    """Spec 165 v0.3 §6.7 (A2): Confer always renders the CSRF meta tag, because her direct voice's writes are always
    checked. This replaces the earlier pin that production pages carry none (the HTML is no longer byte-identical: one meta
    tag). Behaviour is unchanged: with the legacy guard off, the earlier page script's tokenless writes still reach
    cos-scheduler (the two tests above), and nothing else reads the tag."""
    portal, forwarded = _portal(load_portal, monkeypatch, "https://minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    assert forwarded and forwarded[-1][1].get("head_meta", {}).get("minimoi-csrf-token")
    forwarded.clear()
    assert client.post("/app/cos/ui/send", json=TEXT).status_code == 200 and len(forwarded) == 1


def test_a_staging_origin_keeps_the_guard(load_portal, monkeypatch):
    portal, forwarded = _portal(load_portal, monkeypatch, "https://dev.minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    meta = forwarded[-1][1].get("head_meta")
    assert meta and meta.get("minimoi-csrf-token")
    forwarded.clear()
    refused = client.post("/app/cos/ui/send", json=TEXT)
    assert refused.status_code == 403 and forwarded == []                       # never reaches cos-scheduler


def test_the_guard_can_be_switched_on_for_production_when_the_paired_script_ships(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_COS_WRITE_GUARD", "on")
    portal, forwarded = _portal(load_portal, monkeypatch, "https://minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    forwarded.clear()
    assert client.post("/app/cos/ui/send", json=TEXT).status_code == 403 and forwarded == []


def test_the_guard_can_be_switched_off_on_staging(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_COS_WRITE_GUARD", "off")
    portal, forwarded = _portal(load_portal, monkeypatch, "https://dev.minimoi.ai")
    client = portal.owner()
    client.get("/app/cos/ui/confer")
    forwarded.clear()
    assert client.post("/app/cos/ui/send", json=TEXT).status_code == 200 and len(forwarded) == 1


def test_managed_voice_always_requires_owner_csrf_even_with_legacy_guard_off(load_portal, monkeypatch):
    monkeypatch.setenv("MINIMOI_COS_WRITE_GUARD", "off")
    monkeypatch.setenv("MINIMOI_COS_MANAGED_REALTIME", "1")
    portal, forwarded = _portal(load_portal, monkeypatch, "https://minimoi.ai")
    client=portal.owner();client.get('/app/cos/ui/confer')
    token=forwarded[-1][1]['head_meta']['minimoi-csrf-token'];forwarded.clear()
    path='/app/cos/api/realtime-voice/managed/session'
    assert client.post(path,json={},headers={'X-Minimoi-Voice-Write-Verified':'1'}).status_code==403
    assert forwarded==[]
    assert client.post(path,json={},headers={'X-CSRF-Token':token}).status_code==200
    assert forwarded[-1][1]['verified_cos_voice_write'] is True
    forwarded.clear()
    assert client.post(path,json={},headers={'X-CSRF-Token':token,'Origin':'https://evil.example'}).status_code==403
    assert forwarded==[]
