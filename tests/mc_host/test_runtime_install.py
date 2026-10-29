"""S1-H: the installer (scripts/mc_host/install_delegation_runner.sh, runtime_install.py and the systemd templates).

* Production's invocation is today's, byte for byte: the script as it stood before S1-H (``BEFORE``) and the script now
  are each run, unchanged, in a sandbox, and their plists, bundles, Keychain and launchctl calls and output compared.
  The sandbox's PATH holds only the tools the script uses, and ``security`` and ``launchctl`` are stand-ins that record
  their arguments (never a value) and change nothing: no Keychain item, LaunchAgent or network is touched.
* A version 2 install is rendered from a host.yaml template: the bundle with core/minimoi_actions and its digest, the
  interpreter pinned with its venv, host.yaml checked by the bundle's own rules, the launchd plist or the systemd
  units, and nothing loaded. Production's host configuration renders, and is never loaded by this build."""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_runner import host as host_mod  # noqa: E402

BEFORE = "f2502d60"            # claude/harness-s1 at S1-H's start: install_delegation_runner.sh as production has it
SCRIPT = ROOT / "scripts" / "mc_host" / "install_delegation_runner.sh"
HELPER = ROOT / "scripts" / "mc_host" / "runtime_install.py"
TOOLS = ("git", "tar", "install", "cut", "tr", "shasum", "mkdir", "rm", "mv", "cat", "id", "dirname", "openssl",
         "head")
SECURITY = r'''#!/bin/bash
# security, in a sandbox: records each call with -w's value masked when one is given; a store of one file.
args=(); mask=0
for a in "$@"; do if [[ $mask == 1 ]]; then args+=("<value>"); mask=0; else args+=("$a"); fi
  if [[ "$a" == -w && "$1" == add-generic-password ]]; then mask=1; fi; done
echo "security ${args[*]}" >> "$SANDBOX_CALLS"
case "$1" in
  add-generic-password) : > "$SANDBOX_STORE" ;;
  delete-generic-password) rm -f "$SANDBOX_STORE" ;;
  find-generic-password)
    [[ -f "$SANDBOX_STORE" ]] || exit 44
    for a in "$@"; do [[ "$a" == -w ]] && printf 'sandbox-runner-token-0123456789abcdef0123456789abcdef\n'; done ;;
esac
exit 0
'''
LAUNCHCTL = '''#!/bin/bash
echo "launchctl $*" >> "$SANDBOX_CALLS"
exit 0
'''


def _git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _tree(root):
    """Every file under root with its SHA-256 (the bundle compared whole, not only its digest)."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _sandbox(tmp):
    """A repository whose origin/main holds the bundle's code, a PATH of the needed tools and the two stand-ins."""
    origin, repo, bin_ = tmp / "origin.git", tmp / "repo", tmp / "bin"
    bin_.mkdir()
    for name in TOOLS:
        found = shutil.which(name)
        if found is None:
            pytest.skip(f"{name} is not installed")
        (bin_ / name).symlink_to(found)
    (bin_ / "security").write_text(SECURITY)
    (bin_ / "launchctl").write_text(LAUNCHCTL)
    for n in ("security", "launchctl"):
        (bin_ / n).chmod(0o755)
    archive = _git("archive", "HEAD", "core/__init__.py", "core/mc_delegation_runner", "core/mc_delegation_dispatcher").stdout
    repo.mkdir()
    subprocess.run(["tar", "-x", "-C", str(repo)], input=archive, check=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.email=t@x.test", "-c", "user.name=t", "add", "."],
                 ["-c", "user.email=t@x.test", "-c", "user.name=t", "commit", "-qm", "bundle"]):
        _git(*args, cwd=repo)
    _git("init", "-q", "--bare", str(origin))
    _git("remote", "add", "origin", str(origin), cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    (repo / "scripts" / "mc_host").mkdir(parents=True)
    return repo, bin_


def _run_prod(tmp, repo, bin_, script_text, out):
    """The production invocation (no arguments), as Robert runs it, in the sandbox; its effects moved to ``out``."""
    home, calls, store = tmp / "home", tmp / "calls.log", tmp / "keychain"
    for p in (home, calls, store):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    (home / "minimoi-runtime" / "runner-prod" / "mirrors" / "personal-ai-agents.git").mkdir(parents=True)  # no clone
    script = repo / "scripts" / "mc_host" / "install_delegation_runner.sh"
    script.write_text(script_text)
    env = {"HOME": str(home), "PATH": str(bin_), "SANDBOX_CALLS": str(calls), "SANDBOX_STORE": str(store),
           "LANG": "C"}
    for name in ("launchctl", "security"):            # the stand-ins, or nothing runs at all
        found = subprocess.run(["/bin/bash", "-c", f"command -v {name}"], env=env, capture_output=True, text=True)
        assert found.stdout.strip() == str(bin_ / name), found
    proc = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    shutil.move(str(home), str(out / "home"))
    shutil.move(str(calls), str(out / "calls.log"))
    (out / "stdout").write_text(proc.stdout)
    return out


def test_productions_invocation_is_todays_byte_for_byte(tmp_path):
    try:
        before = _git("show", f"{BEFORE}:scripts/mc_host/install_delegation_runner.sh").stdout.decode()
    except subprocess.CalledProcessError:
        pytest.skip(f"{BEFORE} is not in this clone")
    now = SCRIPT.read_text()
    assert before != now                                             # the script did change (the version 2 path)
    repo, bin_ = _sandbox(tmp_path)
    runs = {}
    for name, text in (("before", before), ("now", now)):
        out = tmp_path / name
        out.mkdir()
        runs[name] = _run_prod(tmp_path, repo, bin_, text, out)
    label = "ai.minimoi.delegation-runner.prod"
    plists = {k: (v / "home" / "Library" / "LaunchAgents" / f"{label}.plist").read_bytes() for k, v in runs.items()}
    assert plists["before"] == plists["now"]
    bundles = {k: _tree(v / "home" / "minimoi-runtime" / "runner-prod" / "bundle") for k, v in runs.items()}
    assert bundles["before"] == bundles["now"] and "run_runner.py" in bundles["now"]
    assert not any(p.startswith("core/minimoi_actions") for p in bundles["now"])     # production's bundle unchanged
    calls = {k: (v / "calls.log").read_text() for k, v in runs.items()}
    assert calls["before"] == calls["now"]
    assert "launchctl bootstrap gui/" in calls["now"] and "<value>" in calls["now"]   # values never recorded
    assert (runs["before"] / "stdout").read_text() == (runs["now"] / "stdout").read_text()
    digests = {k: host_mod.tree_digest(v / "home" / "minimoi-runtime" / "runner-prod" / "bundle")[0]
               for k, v in runs.items()}
    assert digests["before"] == digests["now"]
    print("prod-identity:", json.dumps({"plist_sha256": hashlib.sha256(plists["now"]).hexdigest(),
                                        "bundle_digest": digests["now"], "files": len(bundles["now"]),
                                        "calls": calls["now"].count("\n")}))


# ── version 2 renders ───────────────────────────────────────────────────────────────────────────────────────────
def _template(tmp, **over):
    values = json.loads((ROOT / "tests" / "mc_delegation_dispatcher" / "authority.test_values.json").read_text())
    (tmp / "authority.json").write_text(json.dumps(values))
    (tmp / "dev.dsn").write_text("dbname=never_used")
    os.chmod(tmp / "dev.dsn", 0o600)
    doc = {"schema": "minimoi.host.v1", "host_id": "mac-test-1", "environment": "dev", "principal": "robert",
           "roles": ["authority", "worker", "witness"], "expected_availability": "intermittent",
           "unreachable_grace_s": 900, "service_manager": "launchd",
           "authority": {"endpoint_id": "dev-authority", "dsn_file": str(tmp / "dev.dsn"),
                         "values": str(tmp / "authority.json")},
           "capabilities": ["repo:personal-ai-agents", "scope:build", "actor:fakework", "host:mac-local"],
           "concurrency": 1, "artifact_root": str(tmp / "home" / "artifacts"), "workspace_root": str(tmp / "home" / "w"),
           "mirrors": {"personal-ai-agents": str(tmp / "mirror.git")}, "stop_timeout_s": 20, **over}
    path = tmp / "host.template.yaml"
    path.write_text(json.dumps(doc))
    return path


def _render(tmp, template, *extra):
    proc = subprocess.run(["/bin/bash", str(SCRIPT), "--host-config", str(template), "--python", sys.executable,
                           "--ref", "HEAD", "--base", str(tmp / "base"), "--home", str(tmp / "home"), *extra],
                          capture_output=True, text=True, timeout=180, env={**os.environ,
                                                                             "MINIMOI_INSTALL_PYTHON": sys.executable})
    return proc


def test_gate_m_renders_the_bundle_the_pin_and_the_plist_and_loads_nothing(tmp_path):
    proc = _render(tmp_path, _template(tmp_path), "--path-prefix", str(tmp_path / "tripwire"))
    assert proc.returncode == 0, proc.stderr
    assert "NOT loaded" in proc.stdout
    base = tmp_path / "base"
    record = json.loads((base / "install.json").read_text())
    bundle = base / "bundle"
    assert (bundle / "core" / "minimoi_actions" / "grants.py").is_file() and (bundle / "run_runner.py").is_file()
    assert record["bundle_digest"] == host_mod.tree_digest(bundle)[0]           # P0's method, the bundle's own code
    assert record["commit"] == _git("rev-parse", "HEAD").stdout.decode().strip() and record["loaded"] is False
    installed = host_mod.load(base / "host.yaml")
    assert installed.bundle_root == bundle and installed.interpreter.prefix == Path(sys.prefix)
    assert installed.interpreter.packages == record["interpreter"]["packages"]
    plist = plistlib.loads((base / "service" / "ai.minimoi.runtime.dev.plist").read_bytes())
    assert plist["Label"] == "ai.minimoi.runtime.dev" and plist["KeepAlive"] is True and plist["ThrottleInterval"] == 30
    assert plist["ProgramArguments"] == [sys.executable, "-I", str(bundle / "run_runner.py"), "run", "--host-config",
                                         str(base / "host.yaml")]
    assert plist["ExitTimeOut"] > installed.stop_timeout_s                       # G4 finishes before launchd kills
    assert plist["EnvironmentVariables"]["HOME"] == str(tmp_path / "home")
    assert plist["EnvironmentVariables"]["PATH"].split(":")[0] == str(tmp_path / "tripwire")
    assert plist["StandardErrorPath"] == plist["StandardOutPath"]                 # stderr kept
    assert not (tmp_path / "home" / "Library").exists()


def test_a_staging_host_with_a_door_gets_both_units_in_upgrade_order(tmp_path):
    door = {"listen": "0.0.0.0:8443", "tls_cert": "/etc/minimoi/tls/fullchain.pem",
            "tls_key": "/etc/minimoi/tls/privkey.pem", "dsn_file": str(tmp_path / "dev.dsn"),
            "credentials": "/etc/minimoi/door-credentials.json"}
    template = _template(tmp_path, host_id="staging-1", expected_availability="always_on", unreachable_grace_s=600,
                         service_manager="systemd", door=door,
                         capabilities=["repo:personal-ai-agents", "scope:build", "actor:fakework", "host:always-on"])
    proc = _render(tmp_path, template, "--user", "minimoi")
    assert proc.returncode == 0, proc.stderr
    units = tmp_path / "base" / "service"
    rt = (units / "minimoi-runtime-dev.service").read_text()
    au = (units / "minimoi-authority-dev.service").read_text()
    bundle = tmp_path / "base" / "bundle"
    assert f"ExecStart={sys.executable} -I {bundle}/run_runner.py run --host-config" in rt
    assert f"ExecStart={sys.executable} -I {bundle}/run_runner.py authority --host-config" in au
    for text in (rt, au):
        assert "KillMode=mixed" in text and "Restart=always" in text and "User=minimoi" in text
        assert "TimeoutStopSec=30" in text and "@" not in text.replace("multi-user", "")
        assert "TOKEN" not in text and "PASSWORD" not in text
    assert "After=network-online.target minimoi-authority-dev.service" in rt          # the authority first
    assert "NOT installed" in proc.stdout and "systemctl" in proc.stdout


def test_productions_host_configuration_renders_and_is_never_loaded_by_this_build(tmp_path):
    template = _template(tmp_path, host_id="prod-ec2-1", environment="prod", expected_availability="always_on",
                         unreachable_grace_s=1800, roles=["worker"],
                         authority={"endpoint_id": "prod-authority", "url": "https://minimoi.ai/internal/mc/v1/"
                                    "delegation-runner", "credential_ref": "keychain:minimoi_runtime/prod",
                                    "values": str(tmp_path / "authority.json")})
    prod_values = json.loads((tmp_path / "authority.json").read_text())
    prod_values.update(environment="prod", routes={}, judgment=None)
    prod_values.pop("judgment")
    (tmp_path / "authority.json").write_text(json.dumps(prod_values))
    proc = _render(tmp_path, template)
    assert proc.returncode == 0, proc.stderr
    plist = plistlib.loads((tmp_path / "base" / "service" / "ai.minimoi.runtime.prod.plist").read_bytes())
    assert plist["Label"] == "ai.minimoi.runtime.prod"                           # never the version 1 runner's label
    refused = _render(tmp_path, template, "--load")
    assert refused.returncode != 0 and "load_prod_not_in_this_build" in refused.stderr
    refused = _render(tmp_path, template, "--label", "ai.minimoi.delegation-runner.prod")
    assert refused.returncode != 0 and "label_invalid" in refused.stderr


def test_a_template_the_bundle_refuses_installs_nothing_further(tmp_path):
    proc = _render(tmp_path, _template(tmp_path, concurrency=2))
    assert proc.returncode != 0 and "host_concurrency_unsupported" in proc.stderr
    assert not (tmp_path / "base" / "host.yaml").exists() and not (tmp_path / "base" / "service").exists()
