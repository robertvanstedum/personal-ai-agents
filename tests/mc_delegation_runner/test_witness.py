"""S1-H, the harness thread's option (a) for T8 (spec v0.5 R5.6-a, E8, R2.4-b): the host's witness tells Robert once
when its authority stops answering past the contact timeout, and once when it answers again, through a notifier that
never depends on the authority's store, with its once-per-key memory in a file of its own. A whole-Mac sleep, where
the authority sleeps with the runtime, says nothing (T6m). No Keychain read, no network: the Telegram adapter is
built and not exercised here."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_host_runtime as H  # noqa: E402
from core.mc_delegation_runner import host as host_mod, witness as W  # noqa: E402

FakeAuthority, make_rt = H.FakeAuthority, H.make_rt
mirror, _reap = H.mirror, H._reap                          # the host runtime tests' fixtures, shared

T0 = datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _witness(tmp_path):
    return W.Witness("mac-test-1", "dev-authority", W.FileNotifier(tmp_path / "notify.jsonl"),
                     W.Memory(tmp_path / "artifacts" / "witness" / "told.jsonl"), 45)


def _keys(tmp_path):
    return [x["key"] for x in _lines(tmp_path / "notify.jsonl")]


def test_an_outage_is_told_once_per_key_and_its_end_once(tmp_path):
    w = _witness(tmp_path)
    w.observe(30, T0)                                         # inside the contact timeout: nothing
    assert _lines(tmp_path / "notify.jsonl") == []
    w.observe(50, T0)                                         # past it: one notice
    w.observe(65, T0 + timedelta(seconds=15))                 # the same outage: nothing more
    (told,) = _lines(tmp_path / "notify.jsonl")
    assert told["key"] == "authority-unreachable:dev-authority:2026-10-29T12:59:10+00:00"
    assert told["source"] == "witness" and "mac-test-1" in told["text"] and "Next:" in told["text"]
    w.observe(None, T0 + timedelta(seconds=60))               # the authority answers again: one back notice
    w.observe(None, T0 + timedelta(seconds=75))
    keys = _keys(tmp_path)
    assert keys == [told["key"], "authority-back:dev-authority:2026-10-29T12:59:10+00:00"]
    memory = [json.loads(x)["key"] for x in (tmp_path / "artifacts" / "witness" / "told.jsonl").read_text()
              .splitlines()]
    assert memory == keys                                     # recorded after each send, fsynced, in its own file


# The S1-H review's SF2 case: the outage begins at T0-50 s, is told at T0, and the runtime restarts at T0+15 s. The
# restarted runtime knows only its own calls: its first fails at T0+20 s, so it measures its own outage from there
# (the earlier test fed it the old outage's 80 s, which a real restarted runtime cannot know).
def test_a_restart_during_an_outage_never_tells_it_twice_and_tells_its_end_once(tmp_path):
    first = _witness(tmp_path)
    first.observe(50, T0)
    (unreachable,) = _keys(tmp_path)
    again = _witness(tmp_path)                                # restarted: its memory is the file
    again.observe(0, T0 + timedelta(seconds=20))
    again.observe(50, T0 + timedelta(seconds=70))             # past its own timeout, measured from its own failure
    again.observe(65, T0 + timedelta(seconds=85))
    assert _keys(tmp_path) == [unreachable]                   # the same outage: never a second notice
    again.observe(None, T0 + timedelta(seconds=100))          # back: told once, under the first notice's since
    again.observe(None, T0 + timedelta(seconds=115))
    assert _keys(tmp_path) == [unreachable, "authority-back:dev-authority:2026-10-29T12:59:10+00:00"]
    third = _witness(tmp_path)                                # a later restart finds nothing open
    assert third.outage is None
    third.observe(None, T0 + timedelta(seconds=130))
    assert len(_keys(tmp_path)) == 2


def test_a_restart_during_an_outage_that_ends_before_its_own_timeout_still_tells_the_end(tmp_path):
    first = _witness(tmp_path)
    first.observe(50, T0)
    again = _witness(tmp_path)                                # restarted at T0+15 s, the authority still down
    again.observe(5, T0 + timedelta(seconds=25))              # its own outage: inside its timeout
    again.observe(None, T0 + timedelta(seconds=35))           # the authority is back
    assert [k.split(":")[0] for k in _keys(tmp_path)] == ["authority-unreachable", "authority-back"]
    assert _keys(tmp_path)[1] == "authority-back:dev-authority:2026-10-29T12:59:10+00:00"


def test_a_restarted_witness_takes_up_only_its_own_endpoints_open_outage(tmp_path):
    memory = W.Memory(tmp_path / "artifacts" / "witness" / "told.jsonl")
    memory.record("authority-unreachable:other-authority:2026-10-29T12:00:00+00:00", T0.isoformat())
    memory.record("authority-unreachable:dev-authority:2026-10-29T12:10:00+00:00", T0.isoformat())
    memory.record("authority-back:dev-authority:2026-10-29T12:10:00+00:00", T0.isoformat())
    assert _witness(tmp_path).outage is None                  # this endpoint's last outage was told back


def test_a_failed_send_is_tried_again_and_never_recorded_as_told(tmp_path):
    class Flaky(W.FileNotifier):
        fail = 1

        def send(self, key, text, at):
            if self.fail:
                self.fail -= 1
                raise OSError("down")
            super().send(key, text, at)
    w = W.Witness("mac-test-1", "dev-authority", Flaky(tmp_path / "notify.jsonl"),
                  W.Memory(tmp_path / "told.jsonl"), 45)
    w.observe(50, T0)
    assert (_lines(tmp_path / "notify.jsonl"), w.memory.told, w.errors) == ([], set(), 1)
    w.observe(65, T0 + timedelta(seconds=15))
    assert len(_lines(tmp_path / "notify.jsonl")) == 1 and len(w.memory.told) == 1


def test_n1_a_send_that_keeps_failing_is_shown_written_and_logged_never_dropped(tmp_path, mirror, capsys):
    """S1-H review N1: a notifier that always fails (a locked Keychain, a wrong chat id) was counted in memory only, so
    nobody saw it. Now each failure is in host-state.json (and an unknowns line while a notice waits), in doctor's
    problems, written once per key and code to the witness's own failures.jsonl and logged once to stderr; never the
    error's message (here, a Keychain reference). Sent once the notifier works again."""
    from core.mc_delegation_runner import cli, launch

    class Locked(W.FileNotifier):
        def send(self, key, text, at):
            raise launch.LaunchRefused("route_key_unavailable", ref="keychain:telegram/bot_token")
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, notifier={"kind": "file", "path": str(tmp_path / "notify.jsonl")})
    rt.witness.notifier = Locked(tmp_path / "notify.jsonl")
    clock = _clocked(rt)
    assert rt.contact() is True
    rt.observe_authority()
    fake.down = True
    step = rt.values.contact_interval_s
    for _ in range(int(rt.values.contact_timeout_s / step) + 4):
        assert rt.contact() is False
        clock["mono"] += step
        clock["wall"] += timedelta(seconds=step)
        rt.observe_authority()
    rt.write_state(force=True)
    state = json.loads((tmp_path / "artifacts" / "host-state.json").read_text())
    shown = state["hosts"][0]["witness"]
    assert shown["undelivered"].startswith("authority-unreachable:dev-authority:") and shown["failures"] >= 2
    assert shown["last_failure"]["code"] == "route_key_unavailable"
    assert [u for u in state["unknowns"] if u.startswith("witness: notice authority-unreachable:")]
    written = _lines(tmp_path / "artifacts" / "witness" / "failures.jsonl")
    assert [(x["witness"], x["code"], x["key"]) for x in written] == \
        [("send_failed", "route_key_unavailable", shown["undelivered"])]          # once per key and code
    err = capsys.readouterr().err
    assert err.count('"send_failed"') == 1 and "keychain:" not in err
    assert cli.main(["doctor", "--host-config", str(tmp_path / "host.yaml")]) == 1
    assert "witness_send_failing:route_key_unavailable" in json.loads(capsys.readouterr().out)["problems"]
    assert _lines(tmp_path / "notify.jsonl") == []
    rt.witness.notifier = W.FileNotifier(tmp_path / "notify.jsonl")      # it works again: told, once
    clock["mono"] += step
    rt.observe_authority()
    assert [x["key"].split(":")[0] for x in _lines(tmp_path / "notify.jsonl")] == ["authority-unreachable"]
    assert rt.witness.status()["undelivered"] is None
    assert W.failure_code(OSError("a message that could hold a value")) == "witness_send_failed:OSError"
    assert W.failure_code(RuntimeError("witness_send_refused")) == "witness_send_refused"


def _clocked(rt, start=1000.0):
    """The runtime on a clock the test moves: monotonic time (which does not run while a Mac sleeps) and wall time."""
    clock = {"mono": start, "wall": T0}
    rt._mono = lambda: clock["mono"]
    rt._now = lambda: clock["wall"]
    return clock


def test_t8_the_runtime_tells_an_authority_outage_once_and_its_return_once(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, notifier={"kind": "file", "path": str(tmp_path / "notify.jsonl")})
    clock = _clocked(rt)
    assert rt.contact() is True
    rt.observe_authority()
    fake.down = True                                           # the authority's store stops
    step = rt.values.contact_interval_s
    for _ in range(int(rt.values.contact_timeout_s / step) + 3):   # contacts past the timeout, all failing
        assert rt.contact() is False
        clock["mono"] += step
        clock["wall"] += timedelta(seconds=step)
        rt.observe_authority()
    fake.down = False
    assert rt.contact() is True
    rt.observe_authority()
    keys = [x["key"] for x in _lines(tmp_path / "notify.jsonl")]
    assert [k.split(":")[0] for k in keys] == ["authority-unreachable", "authority-back"]
    assert keys[0].split(":", 2)[2] == keys[1].split(":", 2)[2]   # one outage, one since


def test_sf3_a_door_that_answers_with_a_redirect_is_an_outage_the_witness_tells(tmp_path, mirror):
    """S1-H review SF3: a redirect from the door's address (a proxy's login page in front of it, at gate D) was read
    as an authority answer: the host counted itself in contact and its witness stayed silent. Now the host is
    offline from the first such call, and past the contact timeout the witness tells it once."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import urllib.request
    from core.mc_delegation_dispatcher import door

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "https://login.example.test/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = door.HttpDispatcherV2(door.DOORS[1], "h" * 48, tmp_path / "artifacts")
        own = client._open                                     # the client's own handler chain

        def retarget(req, timeout):
            return own(urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{door.PATH}", data=req.data,
                                              method=req.get_method(), headers=dict(req.header_items())),
                       timeout=timeout)
        client._open = retarget
        rt = make_rt(tmp_path, mirror[0], client, notifier={"kind": "file", "path": str(tmp_path / "notify.jsonl")})
        clock = _clocked(rt)
        step = rt.values.contact_interval_s
        for _ in range(int(rt.values.contact_timeout_s / step) + 2):
            assert rt.contact() is False                       # never "in contact"
            assert rt.offline()
            clock["mono"] += step
            clock["wall"] += timedelta(seconds=step)
            rt.observe_authority()
        assert [x["key"].split(":")[0] for x in _lines(tmp_path / "notify.jsonl")] == ["authority-unreachable"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_t6m_a_whole_mac_sleep_raises_no_witness_notice(tmp_path, mirror):
    """On the Mac-only gate the authority sleeps with the runtime (R2.4-b). Monotonic time does not run in sleep, so
    neither a wake whose first call succeeds nor one that began failing just before the lid closed reads as an
    outage; a store still waking for a few seconds after the wake stays inside the timeout."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, notifier={"kind": "file", "path": str(tmp_path / "notify.jsonl")})
    clock = _clocked(rt)
    assert rt.contact() is True
    rt.observe_authority()
    clock["wall"] += timedelta(minutes=20)                     # asleep 20 minutes: wall time runs, monotonic doesn't
    rt.on_wake({"gap_s": 1200.0, "wall_minus_mono_s": 1200.0, "hint": None})
    assert rt.contact() is True
    rt.observe_authority()
    fake.down = True                                           # a call fails just before the next sleep
    assert rt.contact() is False
    rt.observe_authority()
    clock["wall"] += timedelta(minutes=10)                     # 10 minutes asleep
    rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})
    clock["mono"] += rt.values.contact_timeout_s - 1           # the store wakes just inside the contact timeout
    rt.observe_authority()
    fake.down = False
    assert rt.contact() is True
    rt.observe_authority()
    assert _lines(tmp_path / "notify.jsonl") == []
    assert not (tmp_path / "artifacts" / "witness" / "told.jsonl").exists()


def test_a_notifier_needs_the_witness_role_and_a_file_one_is_dev_only(tmp_path, mirror):
    from test_host_runtime import host_doc
    doc = host_doc(tmp_path, mirror[0])
    assert host_mod.parse({**doc, "notifier": {"kind": "file", "path": "/tmp/n.jsonl"}}).notifier.kind == "file"
    tg = {"kind": "telegram", "token_ref": "keychain:telegram/bot_token", "chat_ref": "keychain:telegram/chat_id"}
    assert host_mod.parse({**doc, "notifier": tg}).notifier.token_ref == "keychain:telegram/bot_token"
    for bad, code in (({**doc, "roles": ["authority", "worker"], "notifier": tg}, "host_notifier_without_witness"),
                      ({**doc, "notifier": {**tg, "token_ref": "env:TELEGRAM_BOT_TOKEN"}}, "host_notifier_invalid"),
                      ({**doc, "notifier": {"kind": "file", "path": "rel/n.jsonl"}}, "host_notifier_invalid")):
        with pytest.raises(host_mod.HostRefused, match=code):
            host_mod.parse(bad)
    assert W.build(host_mod.parse(doc), 45, tmp_path) is None   # no notifier: this host tells nobody
