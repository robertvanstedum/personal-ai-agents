"""The local sample server for review captures: loopback only, no secrets,
sample data, and scene set-up through /__tour_sample/<action>."""
import json

import pytest

from scripts.tools.tour_capture import local_sample as ls


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    built = ls.GuildSample(tmp_path_factory.mktemp("sample")).build()
    yield built
    built.restore()                       # other tests in this session see the process as it was


@pytest.fixture
def client(sample):
    sample.reset({})
    return sample.app.test_client()


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
    before = (secrets_module.get_secret, os.environ.get("MINIMOI_GUILD_NEXT"))
    built = ls.GuildSample(tmp_path).build()
    assert os.environ["MINIMOI_GUILD_NEXT"] == "1"
    built.restore()
    assert (secrets_module.get_secret, os.environ.get("MINIMOI_GUILD_NEXT")) == before
