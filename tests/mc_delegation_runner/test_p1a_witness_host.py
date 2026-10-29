"""Host runtime P1-a on the host side: a production host runs the test worker (and the acceptance sink) only when its
host.yaml and its authority's file both say so; the witnesses check the sweeper's age and the other hosts' contact
from the snapshot (R5.6-a), tell once per key through their own notifier and file memory, and send to Telegram
directly by reference (the Keychain on the Mac, Parameter Store on EC2), each text bounded to one message; the
stand-alone EC2 witness and the delivery probe. No Keychain, SSM, Telegram or network: every adapter is a fake."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_host_runtime as H  # noqa: E402
from core.mc_delegation_runner import cli, host as host_mod, launch, witness as W  # noqa: E402
from core.mc_delegation_dispatcher.store import Authority  # noqa: E402

mirror, _reap = H.mirror, H._reap
PROD_FILE = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
TEMPLATE = ROOT / "scripts" / "mc_host" / "host.mac-prod-1.json"
T0 = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
STATE_URL = "https://minimoi.ai/internal/mc/v1/runtime/state"


def prod_doc(tmp_path, mirror_path, **over):
    doc = H.host_doc(tmp_path, mirror_path)
    doc.update(environment="prod", host_id="mac-prod-1", roles=["worker", "witness"],
               authority={"endpoint_id": "prod-authority", "url": "https://minimoi.ai/internal/mc/v1/delegation-runner",
                          "credential_ref": "keychain:minimoi_runtime/prod", "values": str(PROD_FILE)},
               unreachable_grace_s=28800)
    doc.pop("effect_sink")
    doc.update(over)
    return doc


# ── the production host's opt-in ──
def test_a_production_host_takes_the_test_worker_and_sink_only_by_its_own_word(tmp_path, mirror):
    sink = {"path": str(tmp_path / "effects.jsonl"), "lookup": False}
    with pytest.raises(host_mod.HostRefused, match="host_test_instrument_in_production"):
        host_mod.parse(prod_doc(tmp_path, mirror[0], effect_sink=sink))                    # S1's rule by default
    h = host_mod.parse(prod_doc(tmp_path, mirror[0], effect_sink=sink, test_routes_in_production=True))
    assert h.test_routes_in_production and h.effect_sink.lookup is False
    exe = sys.executable
    sha = launch.pin("sha256:" + "a" * 64)
    for over in ({"tools": {"fakework": {"path": exe, "sha256": sha}}}, {"test_credentials": str(tmp_path / "s")},
                 {"route_keys": {"fakework.api": "keychain:a/b"}}):
        with pytest.raises(host_mod.HostRefused, match="host_test_instrument_in_production"):
            host_mod.parse(prod_doc(tmp_path, mirror[0], test_routes_in_production=True, **over))   # never fakecli
    with pytest.raises(host_mod.HostRefused, match="host_test_routes_invalid"):
        host_mod.parse(prod_doc(tmp_path, mirror[0], test_routes_in_production="yes"))


def test_the_runner_starts_fakework_in_production_only_on_the_fakework_route_and_with_the_hosts_word(tmp_path, mirror):
    env = SimpleNamespace(actor="fakework", scope="build", brief="fakework/1\nresult ok")

    def contract(route, **over):
        h = host_mod.parse(prod_doc(tmp_path, mirror[0], **over))
        rt = cli.runtime_for(h, Authority.load(PROD_FILE), object(), digest="sha256:" + "0" * 64)
        return rt._contract(env, {"route": route})
    with pytest.raises(launch.LaunchRefused, match="test_route_in_production"):
        contract("fakework")                                                               # the host did not say so
    assert contract("fakework", test_routes_in_production=True)["tool"] == "fakework"
    for route in ("fakework.plan", "fakework.subscription"):
        with pytest.raises(launch.LaunchRefused, match="test_route_in_production"):
            contract(route, test_routes_in_production=True)                                # only the test route itself


def test_the_witness_read_is_the_doors_own_read_path_by_reference(tmp_path, mirror):
    ok = {"url": STATE_URL, "credential_ref": "keychain:minimoi_runtime/prod_reader"}
    assert host_mod.parse(prod_doc(tmp_path, mirror[0], witness_read=ok)).witness_read.url == STATE_URL
    for bad, code in (({**ok, "url": "https://evil.example/internal/mc/v1/runtime/state"}, "host_witness_read_invalid"),
                      ({**ok, "url": "https://dev.minimoi.ai/internal/mc/v1/runtime/state"},
                       "host_authority_environment_mismatch"),
                      ({**ok, "credential_ref": "env:READ"}, "host_witness_read_invalid"),
                      ({**ok, "extra": 1}, "host_witness_read_invalid")):
        with pytest.raises(host_mod.HostRefused, match=code):
            host_mod.parse(prod_doc(tmp_path, mirror[0], witness_read=bad))
    with pytest.raises(host_mod.HostRefused, match="host_witness_read_without_witness"):
        host_mod.parse(prod_doc(tmp_path, mirror[0], roles=["worker"], witness_read=ok))


def test_the_production_template_parses_as_the_installer_fills_it():
    doc = json.loads(TEMPLATE.read_text())
    doc.update(interpreter={"path": "/usr/bin/python3", "version": "3.9"}, bundle_root="/tmp/bundle")
    h = host_mod.parse(doc)
    assert (h.host_id, h.environment, h.roles, h.expected_availability, h.unreachable_grace_s) == \
        ("mac-prod-1", "prod", ("worker", "witness"), "intermittent", 28800)
    assert h.authority.transport == "door" and h.authority.url == "https://minimoi.ai/internal/mc/v1/delegation-runner"
    assert h.actors() == ("fakework",) and h.test_routes_in_production and h.effect_sink.lookup is False
    assert h.notifier.kind == "telegram" and h.witness_read.url == STATE_URL
    assert host_mod.required_python(h.roles, h.authority.transport) == (3, 9)      # the worker runs on /usr/bin/python3
    text = TEMPLATE.read_text()
    assert "token" not in text.lower().replace("token_ref", "").replace("bot_token", "")   # references only


# ── the snapshot checks ──
class Notes:
    def __init__(self):
        self.lines = []

    def send(self, key, text, at):
        self.lines.append((key, text))


def _witness(tmp_path, notifier=None, **kw):
    return W.Witness("mac-prod-1", "prod-authority", notifier or Notes(), W.Memory(tmp_path / "w" / "told.jsonl"), 90,
                     **kw)


def _doc(*, sweeper_age=5.0, view_age=0.0, hosts=(), as_of=T0):
    return {"schema": "minimoi.runtime.state.v1", "as_of": as_of.isoformat(), "view_age_s": view_age,
            "authority": {"sweeper_age_s": sweeper_age}, "hosts": list(hosts)}


def test_a_stale_sweeper_is_told_once_and_its_return_once(tmp_path):
    w = _witness(tmp_path)
    c = W.SnapshotChecks(None, gap_threshold_s=60, every_s=30)
    c.evaluate(w, _doc(sweeper_age=30), T0)
    assert w.notifier.lines == []
    c.evaluate(w, _doc(sweeper_age=75), T0)
    c.evaluate(w, _doc(sweeper_age=105, as_of=T0 + timedelta(seconds=30)), T0 + timedelta(seconds=30))
    c.evaluate(w, _doc(sweeper_age=20, view_age=50), T0 + timedelta(seconds=60))     # an old file: stale still
    assert [k for k, _ in w.notifier.lines] == ["sweeper-stale:prod-authority:2026-10-29T14:58:45+00:00"]
    c.evaluate(w, _doc(sweeper_age=4, as_of=T0 + timedelta(seconds=90)), T0 + timedelta(seconds=90))
    assert [k for k, _ in w.notifier.lines][-1] == "sweeper-back:prod-authority:2026-10-29T14:58:45+00:00"
    assert len(w.notifier.lines) == 2


def test_an_always_on_hosts_silence_is_told_once_and_the_macs_never(tmp_path):
    w = _witness(tmp_path)
    c = W.SnapshotChecks(None, gap_threshold_s=60, every_s=30)
    hosts = [{"host_id": "prod-ec2-1", "availability": "always_on", "reachability": "unreachable",
              "contact_age_s": 120.0},
             {"host_id": "mac-other", "availability": "intermittent", "reachability": "unreachable",
              "contact_age_s": 9000.0},
             {"host_id": "mac-prod-1", "availability": "always_on", "reachability": "unreachable",
              "contact_age_s": 500.0}]                                                    # itself: never
    c.evaluate(w, _doc(hosts=hosts), T0)
    c.evaluate(w, _doc(hosts=hosts), T0 + timedelta(seconds=30))
    assert [k for k, _ in w.notifier.lines] == ["host-silent:prod-ec2-1:2026-10-29T14:58:00+00:00"]
    back = [dict(hosts[0], reachability="reachable", contact_age_s=3.0)]
    c.evaluate(w, _doc(hosts=back), T0 + timedelta(seconds=60))
    assert [k for k, _ in w.notifier.lines][-1] == "host-back:prod-ec2-1:2026-10-29T14:58:00+00:00"


def test_the_host_witness_reads_the_snapshot_only_while_in_contact_and_at_most_every_interval(tmp_path):
    reads = []
    clock = [0.0]
    c = W.SnapshotChecks(lambda: reads.append(1) or _doc(), gap_threshold_s=60, every_s=30, mono=lambda: clock[0])
    w = _witness(tmp_path, snapshot=c)
    w.observe(None, T0)
    w.observe(None, T0)
    clock[0] = 31.0
    w.observe(None, T0)
    w.observe(120, T0)                                                                   # offline: the first check's
    assert len(reads) == 2
    failing = W.SnapshotChecks(lambda: (_ for _ in ()).throw(OSError("down")), gap_threshold_s=60, every_s=30)
    w2 = _witness(tmp_path / "2", snapshot=failing)
    w2.observe(None, T0)
    assert w2.last_failure["code"] == "witness_read_failed:OSError" and w2.notifier.lines == []   # shown, not told


# ── the stand-alone witness (EC2) ──
def test_the_standalone_witness_tells_an_unanswering_authority_once_and_its_return(tmp_path):
    state = {"ok": False}
    clock = [0.0]
    times = iter(T0 + timedelta(seconds=30 * i) for i in range(100))

    def read():
        if not state["ok"]:
            raise ConnectionRefusedError()
        return _doc()
    w = _witness(tmp_path)
    run = W.Standalone(w, read, W.SnapshotChecks(None, gap_threshold_s=60, every_s=30), mono=lambda: clock[0],
                       now=lambda: next(times), health=tmp_path / "alive")
    for t in (0, 30, 60, 90, 120):
        clock[0] = t
        run.tick()
    assert [k for k, _ in w.notifier.lines] == ["authority-unreachable:prod-authority:2026-10-29T15:00:00+00:00"]
    state["ok"] = True
    run.tick()
    assert [k for k, _ in w.notifier.lines][-1] == "authority-back:prod-authority:2026-10-29T15:00:00+00:00"
    assert (tmp_path / "alive").exists()


def test_the_standalone_witness_configuration_is_references_only_and_complete(tmp_path):
    env = {"MINIMOI_WITNESS_HOST_ID": "prod-ec2-1", "MINIMOI_WITNESS_ENDPOINT_ID": "prod-authority",
           "MINIMOI_WITNESS_READ_URL": "http://portal:5001/internal/mc/v1/runtime/state",
           "MINIMOI_WITNESS_READ_TOKEN_REF": "ssm:/minimoi/production/runtime_witness_read_token",
           "MINIMOI_WITNESS_TELEGRAM_TOKEN_REF": "ssm:/minimoi/production/telegram_witness_bot_token",
           "MINIMOI_WITNESS_TELEGRAM_CHAT_REF": "ssm:/minimoi/production/telegram_witness_chat_id",
           "MINIMOI_WITNESS_MEMORY_DIR": str(tmp_path / "mem"), "MINIMOI_WITNESS_AUTHORITY_FILE": str(PROD_FILE)}
    cfg = W.serve_config(env)
    assert (cfg["contact_interval_s"], cfg["contact_timeout_s"], cfg["gap_threshold_s"]) == (30, 90, 60)
    with pytest.raises(W.WitnessRefused, match="witness_config_missing"):
        W.serve_config({k: v for k, v in env.items() if k != "MINIMOI_WITNESS_TELEGRAM_CHAT_REF"})
    for k, v in (("MINIMOI_WITNESS_READ_URL", "http://evil:5001/internal/mc/v1/runtime/state"),
                 ("MINIMOI_WITNESS_TELEGRAM_TOKEN_REF", "123456:abcdef"),
                 ("MINIMOI_WITNESS_HOST_ID", "a/b")):
        with pytest.raises(W.WitnessRefused, match="witness_config_invalid"):
            W.serve_config({**env, k: v})
    assert W.serve({}) == 2                                                    # unconfigured: refuses, sends nothing


# ── Telegram, direct, by reference ──
class Store:
    def __init__(self, values):
        self.values, self.reads = values, []

    def read(self, ref):
        self.reads.append(ref)
        return self.values[ref]


class Opener:
    def __init__(self, answer=b'{"ok": true}'):
        self.answer, self.requests = answer, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        import io
        return _Resp(self.answer)


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self, n):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_telegram_is_sent_direct_by_reference_and_bounded_to_one_message():
    ssm = Store({"ssm:/minimoi/production/t": "123456:" + "A" * 35, "ssm:/minimoi/production/c": "8379221702"})
    opener = Opener()
    n = W.TelegramNotifier("ssm:/minimoi/production/t", "ssm:/minimoi/production/c", stores={"ssm": ssm},
                           opener=opener)
    n.send("k", "x" * 5000, "at")
    body = json.loads(opener.requests[0].data)
    assert body["chat_id"] == 8379221702 and len(body["text"]) <= W.MESSAGE_CHARS and "[cut:" in body["text"]
    assert ssm.reads == ["ssm:/minimoi/production/t", "ssm:/minimoi/production/c"]     # read at each send
    with pytest.raises(RuntimeError, match="^witness_send_refused$"):
        W.TelegramNotifier("ssm:/minimoi/production/t", "ssm:/minimoi/production/c", stores={"ssm": ssm},
                           opener=Opener(b'{"ok": false}')).send("k", "t", "at")
    assert W.bounded("short") == "short"


def test_parameter_store_is_read_by_name_with_decryption_and_fails_by_code_only():
    calls = []

    class Client:
        def get_parameter(self, Name, WithDecryption):
            calls.append((Name, WithDecryption))
            if Name.endswith("missing"):
                raise KeyError("ParameterNotFound with the value in it")
            return {"Parameter": {"Value": " v \n"}}
    s = W.SsmCredentialStore(Client())
    assert s.read("ssm:/minimoi/production/x") == "v" and calls == [("/minimoi/production/x", True)]
    with pytest.raises(W.WitnessRefused) as exc:
        s.read("ssm:/minimoi/production/missing")
    assert exc.value.code == "witness_ssm_unavailable:KeyError" and "value" not in str(exc.value)
    with pytest.raises(W.WitnessRefused, match="witness_ref_invalid"):
        s.read("keychain:a/b")
    assert set(W.credential_stores("ssm:/a", "keychain:a/b")) == {"ssm", "keychain"}
    with pytest.raises(W.WitnessRefused, match="witness_ref_invalid"):
        W.credential_stores("env:X")


def test_the_delivery_probe_goes_through_the_installed_notifier_and_remembers_nothing(tmp_path, mirror, capsys,
                                                                                     monkeypatch):
    notes = Notes()
    doc = prod_doc(tmp_path, mirror[0], notifier={"kind": "telegram", "token_ref": "keychain:minimoi_witness/bot_token",
                                                  "chat_ref": "keychain:minimoi_witness/chat_id"})
    (tmp_path / "prod-host.yaml").write_text(json.dumps(doc))
    monkeypatch.setattr(W, "notifier_of", lambda host: notes if host.host_id == "mac-prod-1" else None)
    assert cli.main(["witness-probe", "--host-config", str(tmp_path / "prod-host.yaml")]) == 0       # the Mac's
    (key, text), = notes.lines
    assert key.startswith("witness-probe:mac-prod-1:") and "Nothing is wrong" in text
    assert not (Path(doc["artifact_root"]) / "witness" / "told.jsonl").exists()
    assert W.probe({}) == 1 and json.loads(capsys.readouterr().out.splitlines()[-1]) == {
        "state": "failed", "code": "witness_config_missing"}                                       # EC2's, unconfigured
    monkeypatch.undo()
    real = W.notifier_of(host_mod.load(tmp_path / "prod-host.yaml"))
    assert isinstance(real, W.TelegramNotifier) and real.token_ref == "keychain:minimoi_witness/bot_token"


# ── the Mac's production bundle on Python 3.9 ──
PY39 = Path("/usr/bin/python3")


@pytest.mark.skipif(not PY39.exists(), reason="no /usr/bin/python3")
def test_the_production_workers_new_code_runs_on_the_macs_python_3_9(tmp_path):
    version = subprocess.run([str(PY39), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True).stdout.strip()
    if version != "3.9":
        pytest.skip(f"/usr/bin/python3 is {version}")
    code = (
        "import sys, json, datetime as dt\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from core.mc_delegation_runner import host, witness as W\n"
        "from core.mc_delegation_dispatcher.store import Authority\n"
        f"doc = json.load(open({str(TEMPLATE)!r}))\n"
        "doc.update(interpreter={'path': '/usr/bin/python3', 'version': '3.9'}, bundle_root='/tmp/b')\n"
        "h = host.parse(doc)\n"
        "assert h.test_routes_in_production and h.witness_read is not None\n"
        f"a = Authority.load({str(PROD_FILE)!r})\n"
        "assert a.speaks_v2 and a.hosts.contact_timeout_s == 90\n"
        "class N:\n"
        "    lines = []\n"
        "    def send(self, k, t, at): self.lines.append(k)\n"
        f"w = W.Witness('mac-prod-1', 'prod-authority', N(), W.Memory({str(tmp_path / 'told.jsonl')!r}), 90)\n"
        "now = dt.datetime(2026, 10, 29, 15, tzinfo=dt.timezone.utc)\n"
        "W.SnapshotChecks(None, gap_threshold_s=60, every_s=30).evaluate(w, {'schema': 'minimoi.runtime.state.v1',\n"
        "    'as_of': now.isoformat(), 'view_age_s': 0, 'authority': {'sweeper_age_s': 99}, 'hosts': []}, now)\n"
        "assert N.lines == ['sweeper-stale:prod-authority:2026-10-29T14:58:21+00:00'], N.lines\n"
        "assert len(W.bounded('x' * 9000)) <= W.MESSAGE_CHARS\n"
        "print('ok')\n")
    out = subprocess.run([str(PY39), "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
