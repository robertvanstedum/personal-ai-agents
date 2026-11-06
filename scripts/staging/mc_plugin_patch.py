#!/usr/bin/env python3
"""Dev only: replace reviewed files in MC's runtime and portal images, same settings. ``dry-run`` changes nothing live. Workers
resume only after a verified result. Holds MC's admission gate across the swap. Never touches a sign-in, the Mac listeners,
CoS, or the host catalogue. A recreated MC can wait minutes on its gateway's owner lease; that is waited out, never edited."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import subprocess

OLD = {"mc": "minimoi-staging/mc-agent:route-roles-20261105e", "portal": "minimoi-staging/portal:route-roles-20261105f"}
NEW = {"mc": "minimoi-staging/mc-agent:route-roles-20261105g", "portal": "minimoi-staging/portal:route-roles-20261105g"}
MC_SVC, PORTAL_SVC = "mc-agent", "portal"


def only_image(before: dict, after: dict, service: str, image: str) -> None:
    """What compose would run differs by this service's image, and by nothing else on any service."""
    b, a = copy.deepcopy(before["services"]), copy.deepcopy(after["services"])
    assert a[service].pop("image") == image, "image: " + service
    b[service].pop("image")
    assert a == b, "changed beyond the image of " + service


def check_policy(live: dict) -> None:
    """The runtime policy this patch must leave exactly as reviewed."""
    chat = live["agents"]["entries"]["mc-chat"]["tools"]
    assert "group:runtime" in chat["deny"] and chat["fs"]["workspaceOnly"] is True, "conversation limits"
    assert "group:runtime" not in live["agents"]["entries"]["mc-agent"]["tools"]["deny"], "job agent"
    assert all(p.get("mode") == "oauth" for p in live["auth"]["profiles"].values()), "an API profile appeared"
    assert not (live.get("models") or {}).get("providers"), "an API provider appeared"


class Patch:
    """The order of things. ``host`` does the work, so every failure can be tested without a host."""

    def __init__(self, host, guarded_swap):
        self.host, self.guarded_swap = host, guarded_swap

    def dry_run(self) -> dict:
        h = self.host
        h.check_baseline()
        h.build_images()
        h.check_new_images()
        h.check_render()
        return {"would_apply": True, "changed": "nothing live; two local images built"}

    def apply(self):
        h = self.host
        h.check_baseline()
        h.build_images()
        h.check_new_images()
        h.check_render()
        h.save_before()

        def forward(gate):
            h.stop_mc()
            h.write_new()
            h.recreate(gate)
            h.verify_new()
        return self.guarded_swap(stop_workers=h.stop_workers, start_workers=h.start_workers, wait_idle=h.wait_idle, gate=h.gate,
                                 gate_alive=h.gate_alive, apply=forward, restore=h.restore_verified, record=h.record)


class RealHost:                                     # pragma: no cover  (exercised by the dry-run on Dev)
    def __init__(self):
        import pathlib
        import sys
        self.R = pathlib.Path("/opt/minimoi-staging")
        self.OP = self.R / "operators/mc-details-20261105"
        sys.path.insert(0, str(self.R / "operators/mc-route-roles-20261105"))
        import op as O
        self.O, self.MF, self.PF = O, O.MC_FINAL, O.PF
        self.meta = json.loads((self.OP / "payload/meta.json").read_text())
        self.names = {"mc": O.MC, "portal": O.PORTAL}

    def _run(self, *args, **kw):
        p = subprocess.run(args, capture_output=True, text=True, timeout=900, **kw)
        if p.returncode:
            (self.OP / "last-command.private.log").write_text(" ".join(map(str, args[:6])) + "\n" + p.stdout + "\n" + p.stderr)
            raise RuntimeError(f"command failed (log kept privately): {args[0]} {args[1] if len(args) > 1 else ''}")
        return p.stdout

    def record(self, name, detail):
        import time
        with (self.OP / "journal.jsonl").open("a") as f:
            f.write(json.dumps({"at": time.time(), "event": name, **detail}) + "\n")
        if name == "recovery_required":
            (self.OP / "recovery-required.json").write_text(json.dumps({"at": time.time(), **detail}))

    def check_baseline(self):
        for key, name in self.names.items():
            assert self.O.inspect(name)["Config"]["Image"] == OLD[key], "unexpected live image: " + key
        assert not (self.OP / "recovery-required.json").exists(), "an earlier run needs recovery"
        for name, m in self.meta.items():
            got = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", OLD[m["k"]], m["dest"]], capture_output=True).stdout
            assert hashlib.sha256(got).hexdigest() == m["base"], "live file differs from the reviewed base: " + name

    def build_images(self):
        import shutil
        d = self.OP / "ctx"
        shutil.rmtree(d, ignore_errors=True)
        for key in OLD:
            (d / key / "ctx").mkdir(parents=True)
        for name, m in self.meta.items():
            x = d / m["k"] / "ctx" / m["dest"].lstrip("/")
            x.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.OP / "payload" / name, x)
        for key, tag in NEW.items():
            (d / key / "Dockerfile").write_text(f"FROM {OLD[key]}\nCOPY ctx/ /\n")
            self._run("docker", "build", "--pull=false", "--network", "none", "-t", tag, str(d / key))
        shutil.rmtree(d)

    def check_new_images(self):
        self._run("docker", "run", "--rm", "--network", "none", "--entrypoint", "node", NEW["mc"], "--check", "/opt/minimoi/mc-agent/plugins/minimoi-tools/index.mjs")
        self._run("docker", "run", "--rm", "--network", "none", "--entrypoint", "python", NEW["portal"], "-c",
                  "from core.mc_tools.service import ASK_FOLLOW_UP;assert 'minimoi_agent_status' in ASK_FOLLOW_UP")

    def _docs(self):
        mf, pf = json.loads(self.MF.read_text()), json.loads(self.PF.read_text())
        assert mf["services"][MC_SVC]["image"] == OLD["mc"] and pf["services"][PORTAL_SVC]["image"] == OLD["portal"], "override images"
        mf["services"][MC_SVC]["image"], pf["services"][PORTAL_SVC]["image"] = NEW["mc"], NEW["portal"]
        return mf, pf

    def check_render(self):
        mf, pf = self._docs()
        self._check_one(self.O.mc_compose, self.MF, mf, MC_SVC, "mc")
        script = (self.R / "bin/compose.sh").read_text()
        assert str(self.PF) in script
        tmp, alt = self.OP / "pf.check.private.yml", self.OP / "compose.check.private.sh"
        tmp.write_text(json.dumps(pf, indent=2))
        alt.write_text(script.replace(str(self.PF), str(tmp)))
        alt.chmod(0o700)
        try:
            before = json.loads(self.O.portal_compose("config", "--format", "json"))
            after = json.loads(self._run("bash", str(alt), "config", "--format", "json"))
        finally:
            tmp.unlink(missing_ok=True)
            alt.unlink(missing_ok=True)
        only_image(before, after, PORTAL_SVC, NEW["portal"])

    def _check_one(self, compose, path, doc, service, key):
        before = json.loads(compose("config", "--format", "json"))
        tmp = self.OP / "mc.check.private.yml"
        tmp.write_text(json.dumps(doc, indent=2))
        files, self.O.MC_FILES = self.O.MC_FILES, [tmp if f == path else f for f in self.O.MC_FILES]
        try:
            after = json.loads(compose("config", "--format", "json"))
        finally:
            self.O.MC_FILES = files
            tmp.unlink(missing_ok=True)
        only_image(before, after, service, NEW[key])

    def save_before(self):
        import shutil
        shutil.copy2(self.MF, self.OP / "before.mc.private.yml")
        shutil.copy2(self.PF, self.OP / "before.portal.private.yml")
        state = next(v["Source"] for v in self.O.inspect(self.O.MC)["Mounts"] if v["Destination"] == "/home/node/.openclaw")
        self.cfg = self.R.__class__(state) / "openclaw.json"
        shutil.copy2(self.cfg, self.OP / "before.runtime.private.json")
        self.saved = {"images": {n: self.O.inspect(n)["Image"] for n in self.names.values()},
                      "contracts": {n: json.loads(json.dumps(self.O.contract(n))) for n in [*self.names.values(), self.O.RELAY]},
                      "others": self._others()}

    def _others(self):
        watched = [*self.names.values(), self.O.RELAY, *self.O.WORKERS]
        return {k: v for k, v in self.O.snapshot().items() if k not in watched}

    def stop_workers(self):
        self._run("docker", "stop", "--time", "120", *self.O.WORKERS)

    def start_workers(self):
        self._run("docker", "start", *self.O.WORKERS)

    def wait_idle(self):
        self.O.idle()

    @contextmanager
    def gate(self):
        with self.O.admission_gate() as held:
            yield held

    def gate_alive(self, held):
        return self.O.gate_alive(held)

    def stop_mc(self):
        self._run("docker", "stop", "--time", "120", self.O.MC)

    def write_new(self):
        mf, pf = self._docs()
        self.MF.write_text(json.dumps(mf, indent=2))
        self.PF.write_text(json.dumps(pf, indent=2))

    def recreate(self, gate):
        self.O.mc_compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", MC_SVC)
        self.O.wait_healthy(self.O.MC, gate, 660)
        self.O.portal_compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", PORTAL_SVC)
        self.O.wait_healthy(self.O.PORTAL, gate, 180)

    def _same_contracts(self):
        for name, want in self.saved["contracts"].items():
            assert json.loads(json.dumps(self.O.contract(name))) == want, "contract changed: " + name
        assert self._others() == self.saved["others"], "an unrelated container changed"

    def verify_new(self):
        for key, name in self.names.items():
            assert self.O.inspect(name)["Config"]["Image"] == NEW[key], "image: " + key
        self._same_contracts()
        check_policy(json.loads(self.cfg.read_text()))

    def restore_verified(self, gate):
        import os
        import shutil
        subprocess.run(["docker", "stop", "--time", "30", self.O.MC], capture_output=True, timeout=120)
        shutil.copy2(self.OP / "before.mc.private.yml", self.MF)
        shutil.copy2(self.OP / "before.portal.private.yml", self.PF)
        stat, tmp = self.cfg.stat(), self.cfg.with_suffix(".restore")
        shutil.copy2(self.OP / "before.runtime.private.json", tmp)
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, self.cfg)
        self.recreate(gate)
        for name, image in self.saved["images"].items():
            assert self.O.inspect(name)["Image"] == image, "restored image differs: " + name
        self._same_contracts()


if __name__ == "__main__":                          # pragma: no cover
    import fcntl
    import sys
    from guarded_swap import guarded_swap
    host = RealHost()
    with (host.R / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        patch = Patch(host, guarded_swap)
        print("RESULT", {"dry-run": patch.dry_run, "apply": patch.apply}[sys.argv[1]](), flush=True)
