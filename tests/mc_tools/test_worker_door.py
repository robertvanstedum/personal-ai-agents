"""The Mac agent worker's way in from outside the host (plan 2c): one path, passed unchanged to the tool listener, bounded,
rate limited; and the worker's own endpoint rule (loopback, or HTTPS to exactly the MiniMoi hosts)."""
import importlib.util
import io
import sys
from pathlib import Path

import pytest
from flask import Flask

from minimoi_portal.guild_ui.mc_tools_wiring import WORKER_PATH, attach_worker_door

ROOT = Path(__file__).resolve().parents[2]


class Opener:
    def __init__(self, status=200, body=b'{"ok":true}'):
        self.sent, self.status, self.body = [], status, body

    def open(self, req, timeout):
        self.sent.append((req.full_url, req.get_method(), dict(req.header_items()), req.data, timeout))
        status, body = self.status, self.body

        class R(io.BytesIO):
            def __enter__(s): return s
            def __exit__(s, *a): return False
        r = R(body)
        r.status = status
        return r


def door(**kw):
    app, opener = Flask("t"), Opener(**kw)
    attach_worker_door(app, 5011, opener=opener, clock=lambda: 0.0, per_minute=3)
    return app.test_client(), opener


def test_only_the_worker_path_is_passed_to_the_listener_with_the_callers_credential():
    client, opener = door()
    r = client.post(WORKER_PATH, data=b'{"op":"poll"}', headers={"Authorization": "Bearer " + "w" * 40},
                    content_type="application/json")
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    url, method, headers, data, timeout = opener.sent[0]
    assert url == "http://127.0.0.1:5011" + WORKER_PATH and method == "POST" and data == b'{"op":"poll"}'
    assert headers["Authorization"] == "Bearer " + "w" * 40 and timeout == 10
    assert client.get(WORKER_PATH).status_code == 405
    assert client.post("/internal/mc/v1/tools/minimoi_describe_house", data=b"{}", content_type="application/json").status_code == 404


@pytest.mark.parametrize("kw", [dict(content_type="text/plain"), dict(headers={}), dict(data=b"x" * 16385)])
def test_malformed_unauthenticated_or_oversized_requests_never_reach_the_listener(kw):
    client, opener = door()
    args = dict(data=b"{}", headers={"Authorization": "Bearer " + "w" * 40}, content_type="application/json")
    args.update(kw)
    assert client.post(WORKER_PATH, **args).status_code == 400
    assert opener.sent == []


def test_the_listeners_answer_passes_through_and_guessing_is_rate_limited():
    client, opener = door(status=401, body=b'{"error":"agent_worker_unauthorized"}')
    hdr = {"Authorization": "Bearer " + "x" * 40}
    codes = [client.post(WORKER_PATH, data=b"{}", headers=hdr, content_type="application/json").status_code
             for _ in range(4)]
    assert codes == [401, 401, 401, 429] and len(opener.sent) == 3


def _endpoint():
    spec = importlib.util.spec_from_file_location("agent_worker", ROOT / "scripts/mc_host/agent_worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("agent_worker", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url", ["https://minimoi.ai" + WORKER_PATH, "https://dev.minimoi.ai" + WORKER_PATH,
                                 "http://127.0.0.1:5011" + WORKER_PATH])
def test_the_worker_pulls_from_loopback_or_exactly_the_minimoi_hosts(url):
    assert _endpoint().Endpoint(url, "t" * 40).url == url


@pytest.mark.parametrize("url", ["http://minimoi.ai" + WORKER_PATH, "https://minimoi.ai:8443" + WORKER_PATH,
                                 "https://evil.example" + WORKER_PATH, "https://minimoi.ai/internal/mc/v1/tools",
                                 "https://user:pw@minimoi.ai" + WORKER_PATH, "https://minimoi.ai" + WORKER_PATH + "?x=1"])
def test_any_other_worker_endpoint_is_refused(url):
    m = _endpoint()
    with pytest.raises(m.Refused):
        m.Endpoint(url, "t" * 40)
