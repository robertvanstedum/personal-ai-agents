#!/usr/bin/env python3
"""Guarded, additive Dev Vault service install. Does not recreate portal or CoS.

Run from a private reviewed package directory containing code/ and manifest.json.
prepare builds a local image and pins the current container inventory. apply
checks that inventory, captures/indexes, starts authenticated Ask, probes it,
then installs the five-minute systemd cycle. rollback removes only this install's timer
and container; it retains evidence and credentials. Consumer configuration is
written as private snippets for the shared portal/CoS rollout, never printed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path("/opt/minimoi-staging")
OP = ROOT / "operators/vault-dev-20261105"
SERVICE = "minimoi-dev-vault"
CRON = Path("/etc/cron.d/minimoi-dev-vault")
UNITS = Path("/etc/systemd/system")
TIMER = "minimoi-dev-vault-feed.timer"
UNIT = "minimoi-dev-vault-feed.service"


def schedule_files(plan):
    cycle = OP / "code/scripts/vault/dev_cycle.py"
    return {
        UNITS / UNIT: ("[Unit]\nDescription=Dev Vault capture and verification\nAfter=docker.service\n"
          "[Service]\nType=oneshot\nUser=root\nUMask=0077\nTimeoutStartSec=infinity\n"
          f"ExecStart=/usr/bin/python3 {cycle} --image {plan['image']} --principal {plan['principal']}\n"),
        UNITS / TIMER: ("[Unit]\nDescription=Dev Vault five-minute capture timer\n[Timer]\n"
          "OnActiveSec=300\nOnUnitInactiveSec=300\nAccuracySec=5\n"
          f"Unit={UNIT}\n[Install]\nWantedBy=timers.target\n")}


def remove_schedule(files):
    for path, content in files.items():
        if path.exists() and path.read_text() != content:
            raise RuntimeError("schedule_cleanup_uncertain")
    if (UNITS / TIMER).exists():
        command("systemctl", "disable", "--now", TIMER)
    for path in files:
        path.unlink(missing_ok=True)
    command("systemctl", "daemon-reload")


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temp.chmod(0o600)
    os.replace(temp, path)


def inventory():
    names = command("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    result = {}
    for name in names:
        value = json.loads(command("docker", "inspect", name))[0]
        result[name] = {"id": value["Id"], "image": value["Image"], "running": value["State"]["Running"]}
    return result


def fence():
    manifest = json.loads((OP / "manifest.json").read_text())
    actual = set()
    for path in (OP / "code").rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("invalid_package_path")
        if path.is_file():
            actual.add(path.relative_to(OP / "code").as_posix())
    if actual != set(manifest["files"]):
        raise ValueError("package_inventory_changed")
    for name, expected in manifest["files"].items():
        path = OP / "code" / name
        if not path.resolve().is_relative_to((OP / "code").resolve()) or path.is_symlink():
            raise ValueError("invalid_package_path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("package_changed")
    return hashlib.sha256((OP / "manifest.json").read_bytes()).hexdigest()


def prepare(principal):
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,128}", principal):
        raise ValueError("invalid_principal")
    if (OP / "plan.json").exists() or CRON.exists() or any((UNITS / name).exists() for name in (UNIT, TIMER)):
        raise ValueError("existing_install")
    digest = fence()
    before = inventory()
    if SERVICE in before:
        raise ValueError("existing_service")
    base = before["minimoi-portal"]["image"]
    # Dockerfile FROM interprets a raw sha256 image ID as a registry name.
    # Give the already-local immutable image a unique tag and verify it first.
    base_tag = "minimoi-staging/vault-local-base:" + base.split(":", 1)[1]
    command("docker", "tag", base, base_tag)
    if command("docker", "image", "inspect", base_tag, "--format", "{{.Id}}") != base:
        raise ValueError("base_image_mismatch")
    (OP / "Dockerfile").write_text(f"FROM {base_tag}\nCOPY code/ /app/\n")
    subprocess.run(["docker", "build", "--network=none", "--pull=false", "-t", "minimoi-staging/vault-dev:" + digest[:12],
                    str(OP)], check=True, stdout=subprocess.DEVNULL)
    image = command("docker", "image", "inspect", "minimoi-staging/vault-dev:" + digest[:12], "--format", "{{.Id}}")
    # Import all new runtime code without network or a writable host mount.
    command("docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python", image,
            "-B", "-c", "import scripts.memory.dev_feed,scripts.memory.dev_ask,core.memory_shelf.cos")
    write(OP / "plan.json", {"manifest_sha256": digest, "image": image, "before": before, "principal": principal})
    print(json.dumps({"state": "prepared", "manifest_sha256": digest, "image": image}))


def apply():
    plan = json.loads((OP / "plan.json").read_text())
    if fence() != plan["manifest_sha256"] or inventory() != plan["before"] or CRON.exists() or any((UNITS / name).exists() for name in (UNIT, TIMER)):
        raise ValueError("deployment_drift")
    image, data = plan["image"], ROOT / "data"
    secrets = OP / "consumer-config"
    secrets.mkdir(mode=0o700, exist_ok=True)
    ask = data / "memory-ask"
    ask.mkdir(mode=0o700, exist_ok=True)
    # Tokens stay on Dev, generated once, never in output or model context.
    create = r'''from pathlib import Path
from core.memory_ask.identity import add_agent,load,token_hash
registry=Path('/ask/agents.json')
if registry.exists():
 grants=load(registry)
 assert set(grants)=={'cos','mc'}
 for name in ('cos','mc'):
  env=dict(line.split('=',1) for line in (Path('/consumer')/(name+'.env')).read_text().splitlines())
  grant,digest=grants[name]
  assert token_hash(env['MINIMOI_VAULT_ASK_TOKEN'])==digest and not grant.revoked
  assert grant.scopes==('robert',) and grant.tiers==('curated','all')
 raise SystemExit(0)
assert not any(Path('/consumer').iterdir())
for name in ('cos','mc'):
 token=add_agent(registry,name,tiers=('curated','all'),scopes=('robert',),entry_point='chat')
 text='MINIMOI_VAULT_ASK_URL=http://minimoi-dev-vault:8771\nMINIMOI_VAULT_ASK_INTERNAL_HOSTS=minimoi-dev-vault\nMINIMOI_VAULT_ASK_INCLUDE_RAW=1\nMINIMOI_VAULT_ASK_TOKEN='+token+'\n'
 if name=='cos': text+='MINIMOI_VAULT_ASK_CONVERSATIONS=cosc-,owner,guild-\n'
 path=Path('/consumer')/(name+'.env');path.write_text(text);path.chmod(0o600)
'''
    owned_schedule = {}
    try:
        command("docker", "run", "--rm", "--network", "none", "--read-only", "--entrypoint", "python",
                "-v", f"{ask}:/ask:rw", "-v", f"{secrets}:/consumer:rw", image, "-B", "-c", create)
        cycle = OP / "code/scripts/vault/dev_cycle.py"
        # The cycle bounds and cleans up its own subprocesses. Do not kill its
        # supervisor before its finally block can terminate an owned worker.
        subprocess.run([sys.executable, str(cycle), "--image", image, "--principal", plan["principal"]], check=True)
        cmd = ["docker", "run", "-d", "--name", SERVICE, "--restart", "unless-stopped", "--read-only",
               "--network", "minimoi-staging_default", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--memory", "512m", "--cpus", "1", "--pids-limit", "64", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
               "--label", "minimoi.dev-vault-install=" + plan["manifest_sha256"], "--entrypoint", "python"]
        for name, mode in (("memory", "ro"), ("memory-index", "ro"), ("memory-ask", "rw"), ("jobs", "ro")):
            source = data / ("vault-jobs" if name == "jobs" else name)
            cmd += ["-v", f"{source}:/vault/{name}:{mode}"]
        cmd += [image, "-B", "/app/scripts/memory/dev_ask.py"]
        command(*cmd)
        probe = "import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:8771/v1/health',timeout=3).status==200"
        for attempt in range(20):
            result = subprocess.run(["docker", "exec", SERVICE, "python", "-c", probe], capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise ValueError("service_not_ready")
        command("docker", "run", "--rm", "--network", "minimoi-staging_default", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "python",
                "-v", f"{secrets}:/consumer:ro", "-v", f"{data / 'memory-index'}:/index:ro", image,
                "-B", "/app/scripts/memory/dev_probe.py")
        after = inventory()
        if {k: v for k, v in after.items() if k != SERVICE} != plan["before"]:
            raise ValueError("unrelated_container_changed")
        for path, content in schedule_files(plan).items():
            with path.open("x") as handle:
                owned_schedule[path] = content
                handle.write(content)
            path.chmod(0o644)
        command("systemctl", "daemon-reload")
        command("systemctl", "enable", "--now", TIMER)
        if command("systemctl", "is-active", TIMER) != "active":
            raise RuntimeError("timer_not_active")
        write(OP / "result.json", {"state": "service_and_schedule_ready", "image": image,
              "manifest_sha256": plan["manifest_sha256"], "consumer_wiring": "pending_shared_rollout",
              "owner_chat_live_acceptance": "not_run", "container_id": after[SERVICE]["id"]})
        print('{"state":"service_and_schedule_ready","consumer_wiring":"pending"}')
    except BaseException:
        if owned_schedule:
            remove_schedule(owned_schedule)
        if SERVICE in inventory():
            own = json.loads(command("docker", "inspect", SERVICE))[0]
            if own["Config"].get("Labels", {}).get("minimoi.dev-vault-install") != plan["manifest_sha256"]:
                raise RuntimeError("service_cleanup_uncertain")
            command("docker", "rm", "-f", SERVICE)
            if SERVICE in inventory():
                raise RuntimeError("service_cleanup_failed")
        write(OP / "result.json", {"state": "apply_failed", "consumer_wiring": "not_applied"})
        raise


def rollback():
    plan = json.loads((OP / "plan.json").read_text())
    result = json.loads((OP / "result.json").read_text())
    current = inventory().get(SERVICE)
    if current and current["id"] != result.get("container_id"):
        raise ValueError("service_drift")
    remove_schedule(schedule_files(plan))
    with (OP / "cycle.lock").open("a") as cycle_lock:
        fcntl.flock(cycle_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if current:
            command("docker", "rm", "-f", SERVICE)
    write(OP / "result.json", {"state": "rolled_back", "evidence_retained": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "rollback"))
    parser.add_argument("--principal")
    args = parser.parse_args()
    os.umask(0o077)
    if Path.cwd() != OP or not ROOT.is_dir():
        raise SystemExit("dev_operator_directory_required")
    with (ROOT / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "prepare":
            prepare(args.principal or "")
        elif args.action == "apply":
            apply()
        else:
            rollback()
