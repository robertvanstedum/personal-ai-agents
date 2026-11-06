"""scripts/operations/guildhall_prod.sh against fakes of docker, docker-compose, aws, the deployment check and the release.

Every run is under a temporary sandbox. The fake deployment check and release keep the real rule: a portal wired to Records
with no binding is refused, so a wrong stop order fails here the way it would on the host.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/operations/guildhall_prod.sh"
REGISTRY = "332704997792.dkr.ecr.us-east-1.amazonaws.com"
PORTAL_TAGGED = f"{REGISTRY}/minimoi/portal:abc1234"
PORTAL_DIGEST = f"{REGISTRY}/minimoi/portal@sha256:{'a' * 64}"
WORKER_DIGEST = f"{REGISTRY}/minimoi/cos-scheduler@sha256:{'b' * 64}"
NETWORK = "minimoi-records-production-1"
PORTAL_SECRET = "PORTALENVSECRETVALUE"
TOKEN_PREFIX = "TOKENSECRETVALUE"

SETUP_TOKENS = ["cos-guildhall/cos-service.token", "guildhall-mc/mc-service.token"]
BOUND_TOKENS = ["guildhall-cos/rooms-worker-cos.token", "guildhall-cos/cos.token", "guildhall-cos/cos-service.token",
                "guildhall-cos/discussion-gateway.token", "guildhall-mc/rooms-worker.token", "guildhall-mc/mc.token",
                "guildhall-mc/mc-relay.token", "guildhall-mc/discussion-gateway.token",
                "cos-guildhall/meeting-observe.token", "mc-rooms-work/work.token"]

FAKE_COMMON = r'''
import json, os, sys, stat
STATE = os.environ["FAKE_STATE"]
SANDBOX = os.environ["GUILDHALL_PROD_SANDBOX"]
BINDING = SANDBOX + "/etc/minimoi/records-production.json"
LAYER = SANDBOX + "/opt/minimoi/docker-compose.guildhall-production-main.yml"
def load():
    return json.load(open(STATE + "/state.json"))
def save(value):
    json.dump(value, open(STATE + "/state.json", "w"))
def record(tool, argv):
    with open(STATE + "/calls.jsonl", "a") as stream:
        stream.write(json.dumps({"tool": tool, "argv": argv, "binding": os.path.exists(BINDING),
                                 "layer": os.path.exists(LAYER), "env": sorted(k for k in os.environ if k.startswith("MINIMOI_"))}) + "\n")
def wired(row):
    return any(n.startswith("minimoi-records-production-") for n in row["NetworkSettings"]["Networks"])
def container(name, image, networks=("minimoi_default",), env=(), health=None):
    state = {"Running": True}
    if health:
        state["Health"] = {"Status": health}
    return {"Name": "/" + name, "State": state, "Config": {"Image": image, "Env": list(env)}, "Mounts": [],
            "HostConfig": {"PortBindings": {}, "PublishAllPorts": False},
            "NetworkSettings": {"Networks": {n: {} for n in networks}, "Ports": {}}}
def join(state, network, name):
    if network in state["networks"]:
        state["networks"][network]["Containers"]["id-" + name] = {"Name": name}
'''

FAKE_DOCKER = FAKE_COMMON + r'''
argv = sys.argv[1:]
record("docker", argv)
state = load()
if argv[0] == "ps":
    if state.get("daemon_down"):
        sys.exit(1)
    print("\n".join(state["containers"]))
elif argv[0] == "inspect":
    row = state["containers"].get(argv[1])
    if row is None:
        sys.exit(1)
    print(json.dumps([row]))
elif argv[:2] == ["image", "inspect"]:
    row = state["images"].get(argv[2])
    if row is None:
        sys.exit(1)
    print(json.dumps([row]))
elif argv[:2] == ["network", "inspect"]:
    row = state["networks"].get(argv[2])
    if row is None:
        sys.exit(1)
    print(json.dumps([row]))
elif argv[:2] == ["network", "create"]:
    state["networks"][argv[-1]] = {"Name": argv[-1], "Internal": "--internal" in argv, "Containers": {}}
    save(state)
elif argv[0] == "logs":
    print(state["logs"].get(argv[1], ""))
elif argv[0] == "run":
    if "services.records_prepare" in argv:
        if state.get("prepare_fails"):
            sys.exit(1)
        source = [a for a in argv if "dst=/records-state" in a][0].split("src=")[1].split(",")[0]
        target = source + "/records"
        os.mkdir(target, 0o700)
        for name in ("COMPLETE", "production-runtime.json", "records.sqlite3", "owner-key.txt", "session-key.txt"):
            descriptor = os.open(target + "/" + name, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(descriptor, b"x")
            os.close(descriptor)
elif argv[0] == "stop":
    pass
elif argv[0] == "rm":
    name = argv[-1]
    state["containers"].pop(name)
    for network in state["networks"].values():
        network["Containers"].pop("id-" + name, None)
    save(state)
elif argv[0] == "login":
    sys.stdin.read()
'''

FAKE_COMPOSE = FAKE_COMMON + r'''
argv = sys.argv[1:]
record("docker-compose", argv)
state = load()
project = argv[argv.index("--project-name") + 1]
if project == "minimoi-records-production":
    state["containers"]["minimoi-records"] = container("minimoi-records", "portal-digest", (NETWORK := os.environ["MINIMOI_RECORDS_NETWORK"],), health="healthy")
    join(state, NETWORK, "minimoi-records")
elif project == "minimoi-guildhall-production":
    for name in ("minimoi-guildhall-cos", "minimoi-guildhall-mc"):
        state["containers"][name] = container(name, "worker", ("minimoi-records-production-1", "minimoi_default"))
        join(state, "minimoi-records-production-1", name)
        if not state.get("workers_silent"):
            state["logs"][name] = '{"event": "started", "profile": "production_internal"}'
save(state)
'''

FAKE_AWS = FAKE_COMMON + r'''
record("aws", sys.argv[1:])
print("ecr-password-not-a-real-one")
'''

FAKE_DEPLOYMENT = FAKE_COMMON + r'''
args = sys.argv[1:]
record("deployment", args)
state = load()
portal = state["containers"].get("minimoi-portal")
records = state["containers"].get("minimoi-records")
if not os.path.exists(BINDING):
    if records is not None or (portal is not None and wired(portal)):
        sys.exit(1)
    if args[0] == "--backend-plan":
        sys.exit(1)
    sys.exit(0)
if args[0] == "--portal-env":
    if records is None or records["State"].get("Health", {}).get("Status") != "healthy":
        sys.exit(1)
    print("MINIMOI_RECORDS_NETWORK=minimoi-records-production-1")
    sys.exit(0)
references = {"MINIMOI_RECORDS_IMAGE": "image", "MINIMOI_RECORDS_STATE": "state", "MINIMOI_RECORDS_PORTAL_KEY": "key",
              "MINIMOI_RECORDS_NETWORK": "minimoi-records-production-1"}
argv = {"preflight": ["docker", "run", "--rm", "--pull=never", "--network=none", "image", "python", "-m", "services.records_runtime"],
        "start": ["docker-compose", "--project-name", "minimoi-records-production", "up", "-d", "--pull", "never", "minimoi-records"],
        "status": ["docker-compose", "ps"]}[args[1]]
print(json.dumps({"plan_only": True, "activated": False, "argv": argv, "environment_references": references}))
'''

FAKE_RELEASE = FAKE_COMMON + r'''
import subprocess
args = sys.argv[1:]
record("release", args)
root = SANDBOX + "/opt/minimoi"
code = subprocess.run([sys.executable, root + "/scripts/rooms/deployment.py", "--portal-env"], stdout=subprocess.DEVNULL).returncode
if code or load().get("release_fails"):
    sys.exit(1)
state = load()
networks = ["minimoi_default"]
if os.path.exists(BINDING):
    networks.append("minimoi-records-production-1")
env = ["MINIMOI_RECORDS_PRODUCTION=1"] if os.path.exists(BINDING) else []
layered = ["MINIMOI_MC_ROOMS_PRODUCTION=1"] if os.path.exists(LAYER) else []
state["containers"]["minimoi-portal"] = container("minimoi-portal", state["portal_image"], networks, env + layered + ["SECRET=" + state["portal_secret"]])
state["containers"]["minimoi-cos-scheduler"] = container("minimoi-cos-scheduler", state["scheduler_image"], networks,
    ["MINIMOI_GUILDHALL_COS_PRODUCTION=1"] if os.path.exists(LAYER) else [])
if os.path.exists(BINDING):
    join(state, "minimoi-records-production-1", "minimoi-portal")
    join(state, "minimoi-records-production-1", "minimoi-cos-scheduler")
save(state)
'''


class Host:
    def __init__(self, tmp_path):
        self.sandbox = tmp_path / "sandbox"
        self.fake = tmp_path / "fake"
        self.bin = self.fake / "bin"
        self.root = self.sandbox / "opt/minimoi"
        self.etc = self.sandbox / "etc/minimoi"
        self.binding = self.etc / "records-production.json"
        self.keys = self.root / "secrets/records-production"
        self.state_dir = self.root / "state/records-production/records"
        self.secrets = self.root / "secrets/1-2"
        self.receipt = self.root / "operators/beta/backup/receipt.json"
        self.bin.mkdir(parents=True)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "GUILDHALL_PROD_SANDBOX": str(self.sandbox),
                    "FAKE_STATE": str(self.fake), "HOME": str(tmp_path)}
        python = sys.executable
        for name, body in (("docker", FAKE_DOCKER), ("docker-compose", FAKE_COMPOSE), ("aws", FAKE_AWS)):
            self._executable(self.bin / name, f"#!{python}\n{body}")
        self._executable(self.bin / "id", "#!/bin/sh\necho 0\n")
        self._executable(self.bin / "fake_release.py", f"#!{python}\n{FAKE_RELEASE}")
        self.write("scripts/rooms/deployment.py", FAKE_DEPLOYMENT)
        self.write("scripts/rooms/production_plan.py", "")
        self.write("scripts/deploy_scoped_release.sh",
                   f"# reads docker-compose.minimoi-1-2-production.yml\nexec {python} {self.bin}/fake_release.py \"$@\"\n")
        for name in ("docker-compose.minimoi-1-2-production.yml", "docker-compose.records-production.yml",
                     "docker-compose.records-production-portal.yml", "docker-compose.guildhall-production.yml",
                     "guildhall-kit/docker-compose.guildhall-production-main.yml"):
            self.write(name, "services: {}\n")
        self.write(".env", "KEEP=this\n", mode=0o640)
        for folder in ("secrets/1-2", "state/1-2"):
            (self.root / folder).mkdir(parents=True, mode=0o700)
        for name in SETUP_TOKENS:
            self.token(name, f"{TOKEN_PREFIX}-{name}")
        self.receipt.parent.mkdir(parents=True, mode=0o700)
        self.receipt.write_text("{}")
        self.receipt.chmod(0o600)
        networks = {"minimoi_default": {"Name": "minimoi_default", "Internal": False, "Containers": {}}}
        scheduler = f"{REGISTRY}/minimoi/cos-scheduler:abc1234"
        self.save({
            "portal_image": PORTAL_TAGGED, "scheduler_image": scheduler, "portal_secret": PORTAL_SECRET,
            "containers": {"minimoi-portal": self.container("minimoi-portal", PORTAL_TAGGED, env=["SECRET=" + PORTAL_SECRET]),
                           "minimoi-cos-scheduler": self.container("minimoi-cos-scheduler", scheduler)},
            "images": {PORTAL_TAGGED: {"RepoDigests": [PORTAL_DIGEST]}},
            "networks": networks, "logs": {},
        })
        (self.fake / "calls.jsonl").write_text("")

    @staticmethod
    def container(name, image, networks=("minimoi_default",), env=()):
        return {"Name": "/" + name, "State": {"Running": True}, "Config": {"Image": image, "Env": list(env)}, "Mounts": [],
                "HostConfig": {"PortBindings": {}, "PublishAllPorts": False},
                "NetworkSettings": {"Networks": {n: {} for n in networks}, "Ports": {}}}

    @staticmethod
    def _executable(path, text):
        path.write_text(text)
        path.chmod(0o755)

    def write(self, relative, text, mode=0o644):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)

    def token(self, name, value):
        path = self.secrets / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(value + "\n")
        path.chmod(0o600)

    def bound_tokens(self):
        for name in BOUND_TOKENS:
            self.token(name, f"{TOKEN_PREFIX}-{name}")
        self.token("guildhall-cos/cos-service.token", f"{TOKEN_PREFIX}-cos-guildhall/cos-service.token")

    def load(self):
        return json.loads((self.fake / "state.json").read_text())

    def save(self, value):
        (self.fake / "state.json").write_text(json.dumps(value))

    def update(self, **values):
        self.save({**self.load(), **values})

    def run(self, *args, **extra):
        env = {**self.env, **extra}
        return subprocess.run(["/bin/bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)

    def start(self, **extra):
        return self.run("start", **{"GUILDHALL_PROD_APPROVED": "1", "MINIMOI_ROOMS_WORKER_IMAGE": WORKER_DIGEST,
                                    "MINIMOI_GUILDHALL_BACKUP_RECEIPT": str(self.receipt), **extra})

    def check(self, **extra):
        return self.run("check", **{"MINIMOI_ROOMS_WORKER_IMAGE": WORKER_DIGEST,
                                    "MINIMOI_GUILDHALL_BACKUP_RECEIPT": str(self.receipt), **extra})

    def calls(self):
        return [json.loads(line) for line in (self.fake / "calls.jsonl").read_text().splitlines()]

    def clear_calls(self):
        (self.fake / "calls.jsonl").write_text("")

    def snapshot(self):
        result = {}
        for folder, names, files in os.walk(self.sandbox):
            for path in [folder, *(os.path.join(folder, n) for n in names + files)]:
                info = os.lstat(path)
                body = open(path, "rb").read() if stat.S_ISREG(info.st_mode) else b""
                result[path] = (info.st_mode, info.st_mtime_ns, hashlib.sha256(body).hexdigest())
        return result

    def up(self):
        waiting = self.start()
        assert waiting.returncode == 3, waiting.stdout + waiting.stderr
        self.bound_tokens()
        result = self.start()
        assert result.returncode == 0, result.stdout + result.stderr
        return result


def position(calls, predicate):
    for index, call in enumerate(calls):
        if predicate(call):
            return index
    raise AssertionError("no such call")


def docker_call(calls, *words):
    return position(calls, lambda c: c["tool"] == "docker" and all(w in c["argv"] for w in words))


@pytest.fixture
def host(tmp_path):
    tmp_path.chmod(0o700)
    return Host(tmp_path)


READ_ONLY_DOCKER = {"ps", "inspect", "logs", "image", "network"}


def assert_read_only(host, before, state_before):
    assert host.snapshot() == before
    assert host.load() == state_before
    for call in host.calls():
        assert call["tool"] in {"docker", "deployment"}, call
        if call["tool"] == "docker":
            assert call["argv"][0] in READ_ONLY_DOCKER, call
            if call["argv"][0] in {"image", "network"}:
                assert call["argv"][1] == "inspect", call
        else:
            assert call["argv"][0] == "--portal-env" or call["argv"] == ["--backend-plan", "status"], call


def test_check_is_the_default_and_changes_nothing(host):
    before, state = host.snapshot(), host.load()
    result = host.run(MINIMOI_ROOMS_WORKER_IMAGE=WORKER_DIGEST, MINIMOI_GUILDHALL_BACKUP_RECEIPT=str(host.receipt))
    assert result.returncode == 0, result.stdout
    assert "start may run" in result.stdout
    assert_read_only(host, before, state)


def test_check_changes_nothing_on_a_running_guildhall(host):
    host.up()
    host.clear_calls()
    before, state = host.snapshot(), host.load()
    result = host.check()
    assert result.returncode == 0, result.stdout
    assert "Guildhall is up" in result.stdout
    assert_read_only(host, before, state)


def test_status_changes_nothing_and_says_up(host):
    host.up()
    host.clear_calls()
    before, state = host.snapshot(), host.load()
    result = host.run("status")
    assert result.returncode == 0, result.stdout
    assert_read_only(host, before, state)


def test_status_is_not_up_on_a_fresh_host(host):
    assert host.run("status").returncode == 1


def test_refuses_without_the_1_2_layer(host):
    (host.root / "docker-compose.minimoi-1-2-production.yml").unlink()
    result = host.check()
    assert result.returncode == 1 and "1.2 layer missing" in result.stdout
    before = host.snapshot()
    assert host.start().returncode == 1
    assert host.snapshot() == before


def test_refuses_when_the_release_script_would_strip_the_1_2_layer(host):
    host.write("scripts/deploy_scoped_release.sh", "exit 0\n")
    result = host.check()
    assert result.returncode == 1 and "does not read the 1.2 layer" in result.stdout


@pytest.mark.parametrize("arrange", ["wired_portal", "records_container", "state_left_behind"])
def test_refuses_when_the_binding_is_missing(host, arrange):
    state = host.load()
    if arrange == "wired_portal":
        state["containers"]["minimoi-portal"]["NetworkSettings"]["Networks"][NETWORK] = {}
    elif arrange == "records_container":
        state["containers"]["minimoi-records"] = Host.container("minimoi-records", PORTAL_DIGEST)
    else:
        host.state_dir.mkdir(parents=True)
    host.save(state)
    result = host.check()
    assert result.returncode == 1 and "binding missing" in result.stdout
    before = host.snapshot()
    refused = host.start()
    assert refused.returncode == 1 and host.snapshot() == before
    assert not host.binding.exists()


def test_a_fresh_host_is_the_one_case_with_no_binding_that_may_start(host):
    assert host.check().returncode == 0
    assert not host.binding.exists()


def test_start_needs_approval_and_changes_nothing_without_it(host):
    before = host.snapshot()
    result = host.run("start", MINIMOI_ROOMS_WORKER_IMAGE=WORKER_DIGEST, MINIMOI_GUILDHALL_BACKUP_RECEIPT=str(host.receipt))
    assert result.returncode == 2 and "APPROVED" in result.stderr
    assert host.snapshot() == before


def test_stop_needs_approval(host):
    host.up()
    before, state = host.snapshot(), host.load()
    assert host.run("stop").returncode == 2
    assert host.snapshot() == before and host.load() == state


def test_refuses_unless_root(host):
    Host._executable(host.bin / "id", "#!/bin/sh\necho 1000\n")
    assert host.run("check").returncode == 2


def test_refuses_an_unknown_command_and_a_relative_sandbox(host):
    assert host.run("deploy").returncode == 2
    assert host.run("check", GUILDHALL_PROD_SANDBOX="relative").returncode == 2


def test_first_start_stops_where_records_must_issue_the_tokens(host):
    result = host.start()
    assert result.returncode == 3, result.stdout + result.stderr
    for name in BOUND_TOKENS:
        assert f"secrets/1-2/{name}" in result.stdout
    calls = host.calls()
    order = [docker_call(calls, "services.records_prepare"), docker_call(calls, "create", "--internal", NETWORK),
             position(calls, lambda c: c["binding"]),
             position(calls, lambda c: c["tool"] == "docker-compose" and "minimoi-records-production" in c["argv"])]
    assert order == sorted(order)
    assert not [c for c in calls if c["tool"] == "release"]
    assert not (host.root / "docker-compose.guildhall-production-main.yml").exists()
    assert "minimoi-guildhall-cos" not in host.load()["containers"]


def test_keys_are_private_and_outside_the_state(host):
    host.start()
    assert stat.S_IMODE(host.keys.stat().st_mode) == 0o700
    for name in ("owner.key", "session.key", "portal-bridge.key", "prepare-inputs.json"):
        assert stat.S_IMODE((host.keys / name).stat().st_mode) == 0o600
    assert host.state_dir not in host.keys.parents and host.keys not in host.state_dir.parents


def test_the_binding_is_what_the_real_deployment_check_reads(host):
    host.start()
    spec = importlib.util.spec_from_file_location("real_deployment", ROOT / "scripts/rooms/deployment.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    inputs = real.read(host.binding, trusted_uid=os.getuid())
    assert inputs == {"image": PORTAL_DIGEST, "state": str(host.state_dir), "portal_key": str(host.keys / "portal-bridge.key"),
                      "network": NETWORK, "backup_receipt": str(host.receipt)}
    assert stat.S_IMODE(host.etc.stat().st_mode) == 0o700
    assert stat.S_IMODE(host.binding.stat().st_mode) == 0o600
    assert real.plan_module().IMAGE.fullmatch(inputs["image"])


def test_the_keys_and_inputs_are_what_the_real_prepare_and_plan_accept(host, tmp_path, monkeypatch):
    from services import records_prepare as prepare
    from services.records_runtime import MANIFEST
    host.start()
    inputs = json.loads((host.keys / "prepare-inputs.json").read_text())
    assert set(inputs) == prepare.FIELDS
    # The compose file's --data-dir, which prepare's manifest must equal.
    service = yaml.safe_load((ROOT / "docker-compose.records-production.yml").read_text())["services"]["minimoi-records"]
    assert inputs["runtime_data_dir"] == service["command"][service["command"].index("--data-dir") + 1]
    target = tmp_path / "real-state-parent"
    target.mkdir(mode=0o700)
    real = {**inputs, "destination": str(target / "records"), "runtime_data_dir": str(target / "records")}
    for key, name in (("owner", "owner.key"), ("session", "session.key"), ("portal", "portal-bridge.key")):
        assert inputs[key + "_key_file"] == "/records-keys/" + name
        real[key + "_key_file"] = str(host.keys / name)
    monkeypatch.setattr(prepare.os, "fstatvfs", lambda fd: SimpleNamespace(f_bavail=1 << 40, f_frsize=1))
    assert prepare.prepare(real)["prepared"] is True
    assert (target / "records" / MANIFEST).is_file()
    spec = importlib.util.spec_from_file_location("real_plan", ROOT / "scripts/rooms/production_plan.py")
    plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan)
    result = plan.plan({"image": PORTAL_DIGEST, "state": str(target / "records"), "portal_key": str(host.keys / "portal-bridge.key"),
                        "network": NETWORK, "backup_receipt": str(host.receipt)})
    assert result["activated"] is False


def test_start_resumes_in_order_and_ends_verified(host):
    result = host.up()
    assert "Guildhall is up" in result.stdout
    calls = host.calls()
    release = position(calls, lambda c: c["tool"] == "release")
    workers = position(calls, lambda c: c["tool"] == "docker-compose" and "minimoi-guildhall-production" in c["argv"])
    assert calls[release]["argv"] == ["abc1234", "cos-scheduler", "portal"]
    assert calls[release]["layer"] is True and calls[release]["binding"] is True
    assert release < workers
    argv = calls[workers]["argv"]
    assert argv[argv.index("--env-file") + 1] == str(host.root / "guildhall.env")
    assert argv[argv.index("--profile") + 1] == "guildhall-production"
    # Own project only: nothing merges it into the main one.
    assert "docker-compose.prod.yml" not in " ".join(argv)
    assert host.run("status").returncode == 0


def test_own_env_file_is_private_and_exact(host):
    host.up()
    path = host.root / "guildhall.env"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert values == {"MINIMOI_ROOMS_WORKER_IMAGE": WORKER_DIGEST,
                      "MINIMOI_GUILDHALL_COS_SECRETS": str(host.secrets / "guildhall-cos"),
                      "MINIMOI_GUILDHALL_COS_JOURNAL": str(host.root / "state/1-2/guildhall-cos-journal"),
                      "MINIMOI_GUILDHALL_MC_SECRETS": str(host.secrets / "guildhall-mc"),
                      "MINIMOI_GUILDHALL_MC_JOURNAL": str(host.root / "state/1-2/guildhall-mc-journal"),
                      "MINIMOI_RECORDS_NETWORK": NETWORK}


def test_main_env_gets_references_and_a_hash_and_keeps_the_rest(host):
    host.up()
    path = host.root / ".env"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert values["KEEP"] == "this"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    token = f"{TOKEN_PREFIX}-guildhall-mc/mc-service.token".encode()
    assert values["MINIMOI_MC_ROOMS_TOKEN_SHA256"] == hashlib.sha256(token).hexdigest()
    assert values["MINIMOI_COS_GUILDHALL_SECRETS"] == str(host.secrets / "cos-guildhall")
    assert TOKEN_PREFIX not in path.read_text()


def test_no_value_is_ever_printed_or_passed_on_a_command_line(host):
    outputs = [host.check(), host.start()]
    host.bound_tokens()
    outputs += [host.start(), host.check(), host.run("status"), host.run("stop", GUILDHALL_PROD_APPROVED="1"),
                host.check(), host.run("status")]
    values = [PORTAL_SECRET, TOKEN_PREFIX, "ecr-password-not-a-real-one"]
    for name in ("owner.key", "session.key", "portal-bridge.key"):
        raw = (host.keys / name).read_text().strip()
        values += [raw, raw.rpartition(":")[2]]
    values.append(hashlib.sha256(f"{TOKEN_PREFIX}-guildhall-mc/mc-service.token".encode()).hexdigest())
    text = "".join(o.stdout + o.stderr for o in outputs) + json.dumps(host.calls())
    for value in values:
        assert value and value not in text
    # And no shell trace, even when the caller asks for one.
    traced = subprocess.run(["/bin/bash", "-x", str(SCRIPT), "check"], env=host.env, capture_output=True, text=True, timeout=120)
    assert not any(value in traced.stdout + traced.stderr for value in values)
    assert "+ " not in traced.stderr.split("\n", 2)[-1]


def test_a_failing_release_does_not_print_its_log(host):
    host.start()
    host.bound_tokens()
    host.update(release_fails=True)
    result = host.start()
    assert result.returncode == 5 and "half done" in result.stderr
    assert TOKEN_PREFIX not in result.stdout + result.stderr


@pytest.mark.parametrize("value", ["/home/ec2-user/minimoi-staging/receipt.json", "/opt/minimoi-staging/receipt.json",
                                   "_working/receipt.json", "/missing/receipt.json"])
def test_refuses_a_dev_backup_receipt(host, value):
    result = host.check(MINIMOI_GUILDHALL_BACKUP_RECEIPT=value)
    assert result.returncode == 1 and "backup receipt" in result.stdout
    before = host.snapshot()
    assert host.start(MINIMOI_GUILDHALL_BACKUP_RECEIPT=value).returncode == 1
    assert host.snapshot() == before


@pytest.mark.parametrize("value", ["minimoi-staging/rooms-worker:abc1234", f"{REGISTRY}/minimoi/cos-scheduler:rooms-worker-abc1234",
                                   f"{REGISTRY}/minimoi/portal@sha256:{'b' * 64}", "rooms-worker@sha256:" + "b" * 64, ""])
def test_refuses_a_dev_or_mutable_worker_image(host, value):
    result = host.check(MINIMOI_ROOMS_WORKER_IMAGE=value)
    assert result.returncode == 1 and "MINIMOI_ROOMS_WORKER_IMAGE" in result.stdout


@pytest.mark.parametrize("variable,value", [
    ("MINIMOI_GUILDHALL_COS_SECRETS", "/home/ec2-user/minimoi-staging/secrets/guildhall-cos"),
    ("MINIMOI_GUILDHALL_MC_JOURNAL", "/opt/minimoi/_working/journal"),
    ("MINIMOI_ROOMS_WORKER_IMAGE", "minimoi-staging/rooms-worker:abc1234"),
    ("MINIMOI_RECORDS_NETWORK", "minimoi-staging-records-net"),
])
def test_refuses_a_dev_value_in_its_own_env_file(host, variable, value):
    host.up()
    path = host.root / "guildhall.env"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    values[variable] = value
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    result = host.check()
    assert result.returncode == 1 and "guildhall.env" in result.stdout


def test_refuses_an_extra_key_in_its_own_env_file(host):
    host.up()
    path = host.root / "guildhall.env"
    path.write_text(path.read_text() + "MINIMOI_RECORDS_PROFILE=dev\n")
    assert host.check().returncode == 1


@pytest.mark.parametrize("field,value", [("state", "/home/ec2-user/minimoi-staging/records"),
                                         ("portal_key", "/opt/minimoi/_working/portal-bridge.key"),
                                         ("image", "minimoi-staging/records:abc1234")])
def test_refuses_a_dev_value_in_the_binding(host, field, value):
    host.start()
    document = json.loads(host.binding.read_text())
    document["inputs"][field] = value
    host.binding.write_text(json.dumps(document))
    result = host.check()
    assert result.returncode == 1 and "binding is not" in result.stdout


def test_refuses_a_binding_anyone_else_can_read(host):
    host.start()
    host.binding.chmod(0o644)
    assert host.check().returncode == 1


def test_refuses_on_a_dev_host(host):
    (host.sandbox / "home/ec2-user/minimoi-staging").mkdir(parents=True)
    result = host.check()
    assert result.returncode == 2 and "Dev host" in result.stderr


def test_refuses_a_network_that_is_not_internal(host):
    state = host.load()
    state["networks"][NETWORK] = {"Name": NETWORK, "Internal": False, "Containers": {}}
    host.save(state)
    result = host.check()
    assert result.returncode == 1 and "not internal" in result.stdout
    before = host.snapshot()
    assert host.start().returncode == 1
    assert host.snapshot() == before
    assert not host.binding.exists()


def test_refuses_a_network_with_a_member_that_is_not_allowed(host):
    state = host.load()
    state["networks"][NETWORK] = {"Name": NETWORK, "Internal": True, "Containers": {"x": {"Name": "minimoi-curator"}}}
    host.save(state)
    assert host.check().returncode == 1
    assert host.start().returncode == 1


def test_creates_the_network_internal(host):
    host.start()
    assert host.load()["networks"][NETWORK]["Internal"] is True


def test_status_refuses_when_the_network_lost_its_internal_flag(host):
    host.up()
    state = host.load()
    state["networks"][NETWORK]["Internal"] = False
    host.save(state)
    assert host.run("status").returncode == 1


def test_status_refuses_when_a_port_is_published_on_records(host):
    host.up()
    state = host.load()
    state["containers"]["minimoi-records"]["HostConfig"]["PortBindings"] = {"18880/tcp": [{"HostPort": "18880"}]}
    host.save(state)
    assert host.run("status").returncode == 1
    assert host.check().returncode == 1


def test_start_is_not_verified_unless_both_workers_log_the_profile(host):
    host.start()
    host.bound_tokens()
    host.update(workers_silent=True)
    result = host.start()
    assert result.returncode == 6
    assert "minimoi-guildhall-cos" in host.load()["containers"]     # Reports; undoes nothing by itself.
    status = host.run("status")
    assert status.returncode == 1 and "production_internal" in status.stdout


def test_refuses_the_main_layer_placed_before_records(host):
    host.write("docker-compose.guildhall-production-main.yml", "services: {}\n")
    result = host.check()
    assert result.returncode == 1 and "placed but Records is not healthy" in result.stdout


def test_refuses_two_different_cos_service_tokens(host):
    host.start()
    host.bound_tokens()
    host.token("guildhall-cos/cos-service.token", "something else")
    assert host.start().returncode == 1
    assert not (host.root / "docker-compose.guildhall-production-main.yml").exists()


def test_preparation_is_never_retried(host):
    host.update(prepare_fails=True)
    assert host.start().returncode == 1
    assert not host.binding.exists()
    host.update(prepare_fails=False)
    host.state_dir.mkdir(mode=0o700)
    (host.state_dir / "INCOMPLETE").write_text("{}")
    result = host.check()
    assert result.returncode == 1 and "incomplete" in result.stdout
    assert host.start().returncode == 1


def test_stop_order_is_the_one_the_binding_check_allows(host):
    host.up()
    host.clear_calls()
    result = host.run("stop", GUILDHALL_PROD_APPROVED="1")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = host.calls()
    cos = docker_call(calls, "rm", "minimoi-guildhall-cos")
    mc = docker_call(calls, "rm", "minimoi-guildhall-mc")
    records = docker_call(calls, "rm", "minimoi-records")
    portal = docker_call(calls, "rm", "-f", "minimoi-portal")
    release = position(calls, lambda c: c["tool"] == "release")
    assert max(cos, mc) < records < portal < release
    assert calls[release]["argv"] == ["abc1234", "portal", "cos-scheduler"]
    # The layer goes after the workers and before Records; the binding after Records and before the portal.
    assert calls[cos]["layer"] is True and calls[mc]["layer"] is True and calls[records]["layer"] is False
    assert calls[records]["binding"] is True and calls[portal]["binding"] is False
    assert calls[release]["binding"] is False and calls[release]["layer"] is False


def test_stop_leaves_an_unwired_portal_and_keeps_everything_else(host):
    host.up()
    keys = {p.name: p.read_bytes() for p in host.keys.iterdir()}
    assert host.run("stop", GUILDHALL_PROD_APPROVED="1").returncode == 0
    state = host.load()
    assert set(state["containers"]) == {"minimoi-portal", "minimoi-cos-scheduler"}
    assert NETWORK not in state["containers"]["minimoi-portal"]["NetworkSettings"]["Networks"]
    assert NETWORK in state["networks"]
    assert {p.name: p.read_bytes() for p in host.keys.iterdir()} == keys
    assert (host.state_dir / "COMPLETE").is_file() and (host.root / "guildhall.env").is_file()
    assert not host.binding.exists()
    assert not (host.root / "docker-compose.guildhall-production-main.yml").exists()
    assert (host.root / "guildhall-kit/docker-compose.guildhall-production-main.yml").is_file()
    assert host.check().returncode == 0


def test_stop_says_when_the_release_failed_and_the_portal_may_be_down(host):
    host.up()
    host.update(release_fails=True)
    result = host.run("stop", GUILDHALL_PROD_APPROVED="1")
    assert result.returncode == 5 and "portal may be down" in result.stderr and "abc1234" in result.stderr


def test_stop_without_a_portal_needs_the_tag_and_changes_nothing_first(host):
    host.up()
    state = host.load()
    del state["containers"]["minimoi-portal"]
    host.save(state)
    before, state = host.snapshot(), host.load()
    assert host.run("stop", GUILDHALL_PROD_APPROVED="1").returncode == 1
    assert host.snapshot() == before and host.load() == state
    done = host.run("stop", GUILDHALL_PROD_APPROVED="1", GUILDHALL_RELEASE_TAG="abc1234")
    assert done.returncode == 0, done.stdout + done.stderr


def test_stop_with_nothing_there_leaves_the_portal_alone(host):
    result = host.run("stop", GUILDHALL_PROD_APPROVED="1")
    assert result.returncode == 0 and "nothing to stop" in result.stdout
    assert [c for c in host.calls() if c["tool"] == "docker" and c["argv"][0] == "rm"] == []
    assert [c for c in host.calls() if c["tool"] == "release"] == []


def test_stop_before_the_portal_was_wired_never_touches_it(host):
    host.start()     # Records up, waiting for tokens; the portal is not wired yet.
    host.clear_calls()
    assert host.run("stop", GUILDHALL_PROD_APPROVED="1").returncode == 0
    assert [c for c in host.calls() if c["tool"] == "release"] == []
    assert "minimoi-portal" in host.load()["containers"]


def test_start_after_a_stop_restores_from_what_was_kept(host):
    host.up()
    keys = (host.keys / "owner.key").read_bytes()
    assert host.run("stop", GUILDHALL_PROD_APPROVED="1").returncode == 0
    host.clear_calls()
    result = host.run("start", GUILDHALL_PROD_APPROVED="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (host.keys / "owner.key").read_bytes() == keys
    assert host.binding.exists() and not (host.etc / "records-production.stopped.json").exists()
    assert not [c for c in host.calls() if "services.records_prepare" in c["argv"]]


def test_after_a_reboot_start_brings_records_back_and_check_says_so(host):
    host.up()
    state = host.load()
    state["containers"]["minimoi-records"]["State"] = {"Running": False}
    host.save(state)
    assert host.run("status").returncode == 1
    result = host.check()
    assert result.returncode == 0 and "Records is stopped" in result.stdout


def test_real_deployment_check_still_refuses_a_wired_portal_with_no_binding():
    spec = importlib.util.spec_from_file_location("real_deployment", ROOT / "scripts/rooms/deployment.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    wired = {"Config": {"Env": []}, "Mounts": [], "NetworkSettings": {"Networks": {NETWORK: {}}}}
    with pytest.raises(real.Refused):
        real.admit(None, rows={"minimoi-portal": wired})
    assert real.admit(None, rows={"minimoi-portal": {"Config": {"Env": []}, "Mounts": [], "NetworkSettings": {"Networks": {}}}}) is None


def _secrets(tmp_path, names):
    folder = tmp_path / "secrets"
    folder.mkdir()
    for name in names:
        (folder / name).write_text("synthetic-credential-value\n")
    return folder


class _Stop(BaseException):
    pass


@pytest.mark.parametrize("module_name", ["worker", "cos"])
def test_both_workers_log_their_profile_at_start(tmp_path, monkeypatch, capsys, module_name):
    from services.rooms_worker import cos, worker
    environment = {"ROOMS_RECORDS_PROFILE": "production_internal", "ROOMS_RECORDS_PRODUCTION": "1",
                   "RECORDS_URL": "http://minimoi-records:18880", "ROOMS_JOURNAL_DIR": str(tmp_path / "journal")}
    secrets = _secrets(tmp_path, ["mc-relay.token", "mc-service.token"])
    environment.update(ROOMS_SECRETS_DIR=str(secrets))
    if module_name == "worker":
        environment.update(MC_RELAY_TOKEN_FILE=str(secrets / "mc-relay.token"), ROOMS_MC_PORTAL="1")
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    class FakeWorker:
        teammate = "t"

        def __init__(self, *args, **kwargs):
            pass

        def start_housekeeping(self):
            pass

        def run_once(self):
            raise _Stop()

    monkeypatch.setattr(worker, "Worker", FakeWorker)
    with pytest.raises(_Stop):
        (worker if module_name == "worker" else cos).main()
    line = [json.loads(l) for l in capsys.readouterr().out.splitlines() if '"started"' in l][0]
    assert line["profile"] == "production_internal"


def test_the_marker_the_script_looks_for_is_what_the_log_writes(capsys):
    from services.rooms_worker.worker import log
    log(event="started", profile="production_internal")
    assert '"profile": "production_internal"' in capsys.readouterr().out
    assert '"profile": "production_internal"' in SCRIPT.read_text()


def _workflow():
    return yaml.safe_load((ROOT / ".github/workflows/rooms-worker-image.yml").read_text())


def test_worker_image_workflow_never_runs_on_a_push_or_touches_the_host():
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert set(workflow["jobs"]) == {"build-push"}
    text = (ROOT / ".github/workflows/rooms-worker-image.yml").read_text()
    for word in ("ssm", "deploy_scoped", "EC2_INSTANCE_ID", ":latest"):
        assert word not in text


def test_worker_image_workflow_produces_a_digest_for_the_script():
    text = (ROOT / ".github/workflows/rooms-worker-image.yml").read_text()
    assert "docker/Dockerfile.rooms-worker" in text and "linux/amd64" in text
    assert "MINIMOI_ROOMS_WORKER_IMAGE=$image" in text
    assert "imageDigest" in text and "merge-base --is-ancestor" in text
    env = _workflow()["env"]
    assert env["REPOSITORY"] == "minimoi/cos-scheduler"
    assert f"{env['REGISTRY']}/{env['REPOSITORY']}@sha256:" == WORKER_DIGEST.split("@")[0] + "@sha256:"


def test_worker_image_workflow_tests_before_it_pushes():
    steps = _workflow()["jobs"]["build-push"]["steps"]
    names = [step.get("name", "") for step in steps]
    assert names.index("Build the worker image") < next(i for i, n in enumerate(names) if n.startswith("The image starts")) < names.index("Push the tested image")


def test_what_the_dockerfile_copies_is_enough_for_both_workers(tmp_path):
    (tmp_path / "services").mkdir()
    shutil.copy(ROOT / "services/__init__.py", tmp_path / "services/__init__.py")
    shutil.copytree(ROOT / "services/rooms_worker", tmp_path / "services/rooms_worker", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    code = ("import importlib\n"
            "for name in ('cos','worker','discussion','portal_mc','transport_profile'):\n"
            " importlib.import_module('services.rooms_worker.'+name)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ["PATH"]},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_the_expected_token_files_are_what_the_setup_script_tracks():
    spec = importlib.util.spec_from_file_location("real_setup", ROOT / "scripts/operations/minimoi_1_2_prod_setup.py")
    setup = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = setup
    spec.loader.exec_module(setup)
    for name in BOUND_TOKENS:
        assert f"secrets/1-2/{name}" in setup.BY_OPERATOR
    for name in SETUP_TOKENS:
        assert f"secrets/1-2/{name}" in setup.GENERATED
    text = SCRIPT.read_text()
    for name in BOUND_TOKENS + SETUP_TOKENS:
        assert name in text


def test_the_new_files_alone_never_restart_a_service():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/classify_release.py")], cwd=ROOT, capture_output=True, text=True,
                            input=".github/workflows/rooms-worker-image.yml\nscripts/operations/guildhall_prod.sh\nservices/rooms_worker/cos.py\n", timeout=60)
    assert json.loads(result.stdout)["services"] == ""


def test_the_script_is_valid_bash():
    assert subprocess.run(["/bin/bash", "-n", str(SCRIPT)]).returncode == 0
