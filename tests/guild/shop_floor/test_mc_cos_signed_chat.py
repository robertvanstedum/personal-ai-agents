"""F3 (partnership v1.1 B0-2): Master Craftsman's calls to CoS /chat are signed service:mc over the exact bytes requests
sends, so CoS (default deny on writes) accepts them; without the portal's key nothing is signed and CoS refuses."""
from dataclasses import replace

import requests

from core import portal_assertion as PA
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from test_mc_cos_backend import REQ, Resp


def test_mc_chat_is_signed_over_the_bytes_requests_sends(tmp_path, monkeypatch):
    key = tmp_path / "cos-owner.key"
    key.write_text("kid1:" + "k" * 40 + "\n")
    monkeypatch.setenv("PORTAL_COS_OWNER_KEY_FILE", str(key))
    monkeypatch.setenv("PORTAL_COS_OWNER_ENV", "dev")
    seen = []

    def post(url, *, json, timeout, headers=None):
        seen.append((url, json, headers))
        return Resp(200, {"reply": "ok"})
    backend = ChiefOfStaffBackend("http://cos-scheduler:8769", http_post=post)
    assert backend.turn(replace(REQ, text="What next?")).status == "answered"
    url, body, headers = seen[0]
    sent = requests.Request("POST", url, json=body).prepare().body
    spent = set()
    subject = PA.verify(headers["X-Portal-Owner-Assertion"], PA.load_key(str(key)), audience="cos-owner", env="dev",
                        method="POST", target="/chat", body=sent, spend=lambda n, e: not (n in spent or spent.add(n)))
    assert subject == "service:mc"


def test_without_the_key_mc_sends_nothing_signed(monkeypatch):
    monkeypatch.delenv("PORTAL_COS_OWNER_KEY_FILE", raising=False)
    seen = []

    def post(url, **kw):
        seen.append(kw)
        return Resp(200, {"reply": "ok"})
    ChiefOfStaffBackend("http://cos-scheduler:8769", http_post=post).turn(replace(REQ, text="hi"))
    assert "headers" not in seen[0]
