"""The local sample server for review captures: loopback only, no secrets,
sample data, and scene set-up through /__tour_sample/<action>."""
import json

import pytest

from scripts.tools.tour_capture import local_sample as ls


PORT = 18791
HOST = f"127.0.0.1:{PORT}"


class Loopback:
    """The test client, naming the sample by its own loopback Host."""

    def __init__(self, client, host=HOST):
        self.client, self.host = client, host

    def get(self, path, **kw):
        kw.setdefault("base_url", f"http://{self.host}")
        return self.client.get(path, **kw)

    def post(self, path, **kw):
        kw.setdefault("base_url", f"http://{self.host}")
        return self.client.post(path, **kw)


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    built = ls.GuildSample(tmp_path_factory.mktemp("sample"), port=PORT).build()
    yield built
    built.restore()                       # other tests in this session see the process as it was


@pytest.fixture
def client(sample):
    sample.reset({})
    return Loopback(sample.app.test_client())


def test_loopback_requests_are_signed_in_as_the_sample_owner(client):
    for path in ("/guild", "/guild-next/guild/build", "/guild-next/guild/build/queue"):
        assert client.get(path).status_code == 200, path


def test_anything_but_loopback_is_refused(client):
    remote = {"REMOTE_ADDR": "192.168.1.20"}
    assert client.get("/guild-next/guild/build", environ_base=remote).status_code == 403
    assert client.post("/__tour_sample/reset", json={}, environ_base=remote).status_code == 403


def test_serve_binds_loopback_only():
    with pytest.raises(ls.SampleServerError):
        ls.serve(host="0.0.0.0")


def test_no_secret_is_read(sample):
    import core.get_secret as secrets_module
    with pytest.raises(RuntimeError, match="no secrets"):
        secrets_module.get_secret("anything", "at_all")


def test_sample_actions_set_up_a_scene(sample, client):
    assert client.post("/__tour_sample/queue", json={"variant": "quiet"}).status_code == 200
    assert b"#31" not in sample.queue.read_bytes()
    assert client.post("/__tour_sample/postit", json={"text": "Check the lock", "author": "mc"}).status_code == 200
    assert client.post("/__tour_sample/continue", json={"item": 12, "label": "#12 Floor API"}).status_code == 200
    assert sample.floor.count("floor_postits") == 1
    assert sample.floor.count("floor_continue") == 1
    assert client.post("/__tour_sample/reset", json={}).status_code == 200
    assert b"#31" in sample.queue.read_bytes() or b"31" in sample.queue.read_bytes()
    assert sample.floor.count("floor_postits") == 0


def test_unknown_actions_and_bad_arguments_are_refused(client):
    assert client.post("/__tour_sample/drop_tables", json={}).status_code == 400
    assert client.post("/__tour_sample/queue", json={"variant": "everything"}).status_code == 400
    assert client.post("/__tour_sample/voice", json={"boot": "maybe"}).status_code == 400
    assert client.post("/__tour_sample/mc", json={"mode": "paid"}).status_code == 400
    assert client.get("/__tour_sample/reset").status_code in (404, 405)   # GET sets nothing up


def test_mc_turns_answer_from_the_scripted_relay_and_off_restores_the_stub(sample, client):
    assert client.post("/__tour_sample/mc", json={"mode": "turns", "script": ["Item **12** ", "is in build."]}).status_code == 200
    assert sample.services.mc_turns is True
    assert sample.services.mc.kind == "openclaw"
    answer = json.loads(sample.relay.post("http://mc-relay.sample:8790/v1/chat/completions", data=b"{}").text)
    assert answer["choices"][0]["message"]["content"] == "Item **12** is in build."
    assert client.post("/__tour_sample/mc", json={"mode": "off"}).status_code == 200
    assert sample.services.mc_turns is False and sample.services.mc.kind != "openclaw"


def test_the_relay_stream_holds_until_released_and_ends_with_usage():
    relay = ls.ScriptedRelay()
    relay.hold_s = 2
    relay.set_script(["one ", "WAIT", "two"], tokens=5)
    stream = relay.post("x/chat/completions", data=b"{}", stream=True)
    relay.gate.set()
    events = [json.loads(line) for line in b"".join(stream.iter_content()).splitlines()]
    assert [e.get("text") for e in events if e["t"] == "delta"] == ["one ", "two"]
    assert events[-1] == {"t": "usage", "prompt_tokens": 900, "completion_tokens": 5}


def test_stop_ends_a_held_stream_as_stopped():
    relay = ls.ScriptedRelay()
    relay.set_script(["partial ", "WAIT", "never"])
    stream = relay.post("x/chat/completions", data=b"{}", stream=True)
    relay.post("x/turns/stop", data=b"{}")
    events = [json.loads(line) for line in b"".join(stream.iter_content()).splitlines()]
    assert events[-1] == {"t": "error", "class": "stopped"}
    assert "never" not in json.dumps(events)


def test_a_down_runtime_reads_unavailable(sample, client):
    assert client.post("/__tour_sample/mc", json={"mode": "down"}).status_code == 200
    assert sample.services.mc.health().state == "unavailable"


def test_loopback_hosts_with_this_port_are_admitted(sample):
    for host in (f"localhost:{PORT}", f"[::1]:{PORT}", HOST):
        assert Loopback(sample.app.test_client(), host).get("/guild-next/guild/build").status_code == 200, host


@pytest.mark.parametrize("host", ["evil.example", f"evil.example:{PORT}", "127.0.0.1", "localhost:5001",
                                  "dev.minimoi.ai", f"127.0.0.1.nip.io:{PORT}"])
def test_a_foreign_or_rebound_host_is_refused(sample, host):
    """DNS rebinding: a page rebound to 127.0.0.1 still sends its own Host."""
    c = Loopback(sample.app.test_client(), host)
    assert c.get("/guild-next/guild/build").status_code == 403
    assert c.post("/__tour_sample/reset", json={}).status_code == 403


@pytest.mark.parametrize("header", [{"Cf-Connecting-Ip": "203.0.113.9"}, {"CF-Ray": "8c1-ORD"},
                                    {"Cdn-Loop": "cloudflare"}, {"X-Forwarded-For": "203.0.113.9"},
                                    {"X-Forwarded-Host": "dev.minimoi.ai"}, {"X-Forwarded-Proto": "https"},
                                    {"Forwarded": "for=203.0.113.9"}, {"X-Real-Ip": "203.0.113.9"}],
                         ids=lambda h: next(iter(h)))
def test_proxied_or_tunnelled_requests_are_refused(client, header):
    assert client.get("/guild-next/guild/build", headers=header).status_code == 403
    assert client.post("/__tour_sample/reset", json={}, headers=header).status_code == 403


@pytest.mark.parametrize("path", ["/", "/dashboard", "/admin/guests", "/account/password", "/guild/build/queue",
                                  "/guild/operate", "/app/curator", "/app/curator/api/interests", "/app/german/",
                                  "/app/portuguese/", "/app/iotconnect/", "/api/anything", "/interests",
                                  "/research", "/capture-auth", "/login"])
def test_only_the_review_pages_are_served(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("port", [5001, 5432, 8766, 8767, 8768, 8769, 8770, 8095, 14000, 18790, 80, 0, 70000])
def test_service_tunnel_and_low_ports_are_refused(port, tmp_path):
    with pytest.raises(ls.SampleServerError):
        ls.check_port(port)
    with pytest.raises(ls.SampleServerError):
        ls.GuildSample(tmp_path, port=port).build()


def test_every_staging_host_port_is_refused():
    import re
    lib = (ls.REPO / "scripts" / "staging" / "lib.sh").read_text()
    staging = [int(p) for p in re.search(r'^STAGING_HOST_PORTS="([^"]+)"', lib, re.M).group(1).split()]
    assert 5001 in staging and set(staging) <= ls.refused_ports()


def test_serve_on_port_5001_is_refused_before_anything_is_built():
    with pytest.raises(ls.SampleServerError, match="5001"):
        ls.serve(5001)


def test_the_real_backends_point_at_a_closed_address(sample):
    import minimoi_portal.config as portal_config
    for key in ("CURATOR_BACKEND", "GERMAN_BACKEND", "PORTUGUESE_BACKEND", "IOTCONNECT_BACKEND",
                "IOTCONNECT_HEALTH_URL"):
        assert getattr(portal_config, key) == ls.CLOSED_BACKEND, key
    assert portal_config.GUILD_OPERATIONS_STATUS_URL is None


def test_the_session_key_is_the_samples_own(monkeypatch, tmp_path):
    """Even with the real key exported, a sample cookie is signed with a random per-process key."""
    import minimoi_portal.config as portal_config
    real = "r" * 48
    monkeypatch.setenv("PORTAL_SECRET_KEY", real)
    monkeypatch.setenv("CAPTURE_AUTH_SECRET", "capture-" + "c" * 32)
    monkeypatch.setattr(portal_config, "SECRET_KEY", real)
    first = ls.GuildSample(tmp_path / "a", port=PORT).build()
    try:
        assert first.app.secret_key not in (real, "dev-only-change-in-production")
        assert len(first.app.secret_key) == 64
        import os
        assert "PORTAL_SECRET_KEY" not in os.environ and "CAPTURE_AUTH_SECRET" not in os.environ
        c = Loopback(first.app.test_client())
        assert c.get("/guild-next/guild/build").status_code == 200
        cookie = c.client.get_cookie("session", domain="127.0.0.1") or c.client.get_cookie("session")
        from itsdangerous import BadSignature
        from flask.sessions import SecureCookieSessionInterface
        import flask
        other = flask.Flask("elsewhere")
        other.secret_key = real
        with pytest.raises(BadSignature):
            SecureCookieSessionInterface().get_signing_serializer(other).loads(cookie.value)
    finally:
        first.restore()
    import os
    assert os.environ["PORTAL_SECRET_KEY"] == real                   # put back for the rest of the process
    second = ls.GuildSample(tmp_path / "b", port=PORT).build()
    try:
        assert second.app.secret_key != first.app.secret_key
    finally:
        second.restore()


def test_the_cos_stand_in_answers_without_a_model_and_voice_follows_the_sample_state(sample):
    cos = ls._stand_in_cos(sample.cos_state).test_client()
    assert cos.post("/ui/send", json={"text": "Plan the week"}).get_json() == {"reply": "Noted: Plan the week"}
    sample.cos_state["voice"] = "fail"
    assert cos.post("/api/realtime-voice/confer/bootstrap", json={}).status_code == 503
    sample.cos_state["voice"] = "ok"
    assert cos.post("/api/realtime-voice/confer/bootstrap", json={}).get_json()["ok"] is True
    adapter = cos.get("/static/realtime-voice/adapters/openai-webrtc-adapter.js")
    assert b"no network, no microphone" in adapter.data


def test_a_failing_runtime_answers_turns_with_its_error_status(sample, client):
    assert client.post("/__tour_sample/mc", json={"mode": "turns", "fail": 502}).status_code == 200
    assert sample.relay.post("x/chat/completions", data=b"{}").status_code == 502
    assert client.post("/__tour_sample/mc", json={"mode": "turns", "fail": 200}).status_code == 400


def test_restore_puts_the_process_back(tmp_path):
    import os
    import core.get_secret as secrets_module
    import minimoi_portal.config as portal_config
    before = (secrets_module.get_secret, os.environ.get("MINIMOI_GUILD_NEXT"), portal_config.CURATOR_BACKEND)
    built = ls.GuildSample(tmp_path, port=PORT).build()
    assert os.environ["MINIMOI_GUILD_NEXT"] == "1"
    built.restore()
    assert (secrets_module.get_secret, os.environ.get("MINIMOI_GUILD_NEXT"), portal_config.CURATOR_BACKEND) == before


# ── The production Guild pages (legacy_guild), read only, sample data ────────

@pytest.fixture(scope="module")
def legacy(tmp_path_factory, sample):
    sample.restore()                      # one sample per process at a time
    built = ls.GuildSample(tmp_path_factory.mktemp("legacy"), port=PORT, legacy_guild=True).build()
    yield built
    built.restore()


LEGACY_PAGES = ["/guild/build", "/guild/build/queue", "/guild/build/roadmap", "/guild/docs", "/guild/docs/decisions",
                "/guild/operate", "/guild/improve", "/guild/experiment/", "/guild/rooms-preview/", "/guild/career",
                "/guild/users"]


def test_legacy_pages_are_404_unless_switched_on(client):
    for path in LEGACY_PAGES:
        assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("path", LEGACY_PAGES)
def test_legacy_pages_render_with_the_sample_owner(legacy, path):
    assert Loopback(legacy.app.test_client()).get(path).status_code == 200


def test_legacy_pages_show_sample_people_never_the_checkouts_auth_files(legacy):
    import json as _json
    import minimoi_portal.auth as portal_auth
    c = Loopback(legacy.app.test_client())
    operate = c.get("/guild/operate").get_data(as_text=True)
    users = c.get("/guild/users").get_data(as_text=True)
    assert "guest01@example.com" in operate and "Sample Requester" in operate
    assert "owner@example.com" in users
    assert portal_auth.AUTH_DIR == legacy.workdir / "auth"
    real_dir = ls.REPO / "minimoi_portal" / "auth"
    for name in ("pending.json", "email_verifications.json"):
        path = real_dir / name
        if path.exists():
            for email in _json.dumps(_json.loads(path.read_text() or "{}")).split('"'):
                if "@" in email and "example.com" not in email:
                    assert email not in operate and email not in users


def test_legacy_operate_makes_no_outbound_call(legacy):
    """The page asks localhost:8768 for status; the sample's portal module answers unreachable."""
    import requests
    body = Loopback(legacy.app.test_client()).get("/guild/operate").get_data(as_text=True)
    assert "unreachable" in body.lower()
    with pytest.raises(requests.ConnectionError):
        ls._NoNetwork().get("http://localhost:8768/status")


@pytest.mark.parametrize("path", ["/guild/guests/guest_sample01/revoke", "/guild/guests/guest_sample01/extend",
                                  "/guild/guest-requests/1/status", "/guild/build/items/12/status",
                                  "/guild/career/positions/add"])
def test_legacy_writes_are_refused(legacy, path):
    before = (legacy.workdir / "auth" / "guests.json").read_text()
    assert Loopback(legacy.app.test_client()).post(path, data={"status": "approved"}).status_code == 403
    assert (legacy.workdir / "auth" / "guests.json").read_text() == before


def test_legacy_pages_keep_every_other_rule(legacy):
    assert Loopback(legacy.app.test_client(), "evil.example").get("/guild/operate").status_code == 403
    assert Loopback(legacy.app.test_client()).get("/guild/operate", headers={"CF-Ray": "x"}).status_code == 403
    assert Loopback(legacy.app.test_client()).get("/admin/guests").status_code == 404
    import minimoi_portal.config as portal_config
    assert portal_config.CURATOR_BACKEND == ls.CLOSED_BACKEND


def test_restore_puts_back_the_auth_sources(tmp_path):
    import minimoi_portal.auth as portal_auth
    import minimoi_portal.domain_auth as portal_domain_auth
    before = (portal_auth.AUTH_DIR, portal_domain_auth.list_users_with_access)
    built = ls.GuildSample(tmp_path, port=PORT, legacy_guild=True).build()
    assert portal_auth.AUTH_DIR != before[0]
    built.restore()
    assert (portal_auth.AUTH_DIR, portal_domain_auth.list_users_with_access) == before
