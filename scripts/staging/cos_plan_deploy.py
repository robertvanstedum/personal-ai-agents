#!/usr/bin/env python3
"""Dev only: CoS onto the ChatGPT plan, OpenAI routes, default A. ``dry-run`` changes nothing live. ``apply`` and ``rollback``
run detached, under the deploy lock, through guarded_swap: workers resume only after a verified result.

Sign-in is separate (operators/cos-device-login-20261105). ``apply`` refuses until CoS has a healthy plan profile. B and C are
offered, and Claude considered, only after their own proof; this offers A alone, as the default. MC's lane and host-read scope
are untouched. CoS has no admission gate like MC's: wait_idle sees no live turn first, and a message arriving during the
swap fails rather than queues."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json

AGENT_SVC, SCHED_SVC = "cos-agent-a", "cos-scheduler"
AGENT, SCHED = "minimoi-cos-agent-a", "minimoi-cos-scheduler"
AGENTS = ("cos-agent-a", "cos-meeting", "cos-closed")
PLAN_MODEL, PLAN_ROUTES = "openai/gpt-6-astra", "openai/gpt-6-sol,openai/gpt-6-luna"
ROUTES_MOUNT, ROUTES_DIR = "/run/minimoi-model-routes", "/opt/minimoi-staging/config/model-routes"
ROUTES_FILE = ROUTES_MOUNT + "/catalogue.json"
A, GATEWAY = "plan-openai-strong", "gateway-cos-role"
AGENT_ENV = ("COS_RUNTIME_MODEL_ACCESS", "COS_PLAN_MODEL", "COS_PLAN_ROUTES", "COS_PLAN_AUTH_PROFILE")
SCHED_ENV = ("MINIMOI_MODEL_ROUTES_FILE",)


def agent_env(profile: str) -> dict:
    return {"COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": PLAN_MODEL, "COS_PLAN_ROUTES": PLAN_ROUTES,
            "COS_PLAN_AUTH_PROFILE": profile}


def overlaid(doc: dict, images: dict, profile: str) -> dict:
    """The compose override with CoS's images, plan settings and catalogue mount. Nothing else."""
    out = copy.deepcopy(doc)
    services = out["services"]
    agent, sched = services.setdefault(AGENT_SVC, {}), services.setdefault(SCHED_SVC, {})
    agent.update(image=images["agent"], pull_policy="never")
    agent["environment"] = {**(agent.get("environment") or {}), **agent_env(profile)}
    sched.update(image=images["sched"], pull_policy="never")
    sched["environment"] = {**(sched.get("environment") or {}), "MINIMOI_MODEL_ROUTES_FILE": ROUTES_FILE}
    sched["volumes"] = [*(sched.get("volumes") or []), f"{ROUTES_DIR}:{ROUTES_MOUNT}:ro"]
    return out


def check_render(before: dict, after: dict, images: dict, profile: str) -> None:
    """What compose would actually run differs from today by exactly the plan, on exactly two services."""
    b, a = copy.deepcopy(before["services"]), copy.deepcopy(after["services"])
    assert set(b) == set(a), "the services would change"
    for name in set(b) - {AGENT_SVC, SCHED_SVC}:
        assert a[name] == b[name], "an unrelated service would change: " + name
    for name, key, env in ((AGENT_SVC, "agent", agent_env(profile)), (SCHED_SVC, "sched", {"MINIMOI_MODEL_ROUTES_FILE": ROUTES_FILE})):
        new, old = a[name], b[name]
        assert new.pop("image") == images[key], "image: " + name
        old.pop("image")
        new.pop("pull_policy", None), old.pop("pull_policy", None)
        for k, v in env.items():
            assert new["environment"].pop(k) == v, "environment: " + k
        if name == SCHED_SVC:
            added = [v for v in new["volumes"] if v.get("target") == ROUTES_MOUNT]
            assert len(added) == 1 and added[0].get("read_only") is True, "catalogue mount"
            new["volumes"] = [v for v in new["volumes"] if v.get("target") != ROUTES_MOUNT]
        assert new == old, "changed beyond the plan: " + name


def plan_catalogue(catalogue: dict) -> dict:
    """The host's catalogue with CoS's default on A, offered because this deploy proves it. B, C and Claude stay unoffered."""
    out = copy.deepcopy(catalogue)
    for route in out["routes"]:
        if route["route_id"] == A:
            route["offered"]["cos"] = "available"
        elif route["route_id"] == GATEWAY:
            route["offered"]["cos"] = "No API route while CoS runs on the plan"
    out["roles"]["cos-conversation"]["route"] = A
    return out


def check_runtime(pinned: dict, live: dict) -> None:
    """The runtime's config: subscription only, no fallback, every agent's own identity and tools unchanged."""
    assert not (live.get("models") or {}).get("providers"), "an API provider remains"
    assert all(p.get("mode") == "oauth" for p in (live.get("auth") or {}).get("profiles", {}).values()) and live["auth"]["profiles"], "plan profile"
    allow = [PLAN_MODEL, *PLAN_ROUTES.split(",")]
    assert live["agents"]["defaults"].get("thinkingDefault") == "high", "effort"
    assert set(live["agents"]["entries"]) == set(pinned["agents"]["entries"]) == set(AGENTS), "agents"
    for name, entry in [("defaults", live["agents"]["defaults"]), *live["agents"]["entries"].items()]:
        assert entry["model"] == {"primary": PLAN_MODEL, "fallbacks": []} and entry["modelPolicy"]["allow"] == allow, "model: " + name
    for name, entry in pinned["agents"]["entries"].items():
        for key in ("name", "workspace", "tools", "memorySearch", "agentDir"):
            assert live["agents"]["entries"][name].get(key) == entry.get(key), f"{name}.{key} changed"


class Deploy:
    """The order of things. ``host`` does the work, so every failure can be tested without a host."""

    def __init__(self, host, guarded_swap):
        self.host, self.guarded_swap = host, guarded_swap

    def dry_run(self) -> dict:
        h = self.host
        h.check_baseline()
        h.build_images()
        profile = h.signed_in_profile()
        h.check_new_images(profile or "openai:dry-run")
        h.check_render(profile or "openai:dry-run")
        return {"would_apply": profile is not None, "signed_in": profile is not None, "changed": "nothing live; two local images built"}

    def apply(self):
        h = self.host
        h.check_baseline()
        profile = h.signed_in_profile()
        if profile is None:
            raise RuntimeError("CoS has no healthy plan profile; sign in first. Nothing changed.")
        h.build_images()
        h.check_new_images(profile)
        h.check_render(profile)
        h.share_auth(profile)                       # auth only: no model reads it until the swap
        h.save_before()

        def forward(_):
            h.write_new(profile)
            h.recreate()
            h.verify_new()
        return self._swap(forward)

    def rollback(self):
        self.host.check_saved()
        def no_second_try(_):                       # a rollback that failed is not restored: workers must stay stopped
            raise RuntimeError("rollback failed; recovery required")
        return self._swap(lambda _: self.host.restore_verified(), restore=no_second_try)

    def _swap(self, apply, restore=None):
        h = self.host

        @contextmanager
        def gate():                                 # the deploy lock is held by the caller; CoS has nothing more to hold
            yield "held"
        return self.guarded_swap(stop_workers=h.stop_workers, start_workers=h.start_workers, wait_idle=h.wait_idle, gate=gate,
                                 gate_alive=lambda _: True, apply=apply, restore=restore or (lambda _: h.restore_verified()), record=h.record)


class RealHost:                                     # pragma: no cover  (exercised by the dry-run on Dev)
    def __init__(self):
        import pathlib
        import sys
        self.R = pathlib.Path("/opt/minimoi-staging")
        self.OP = self.R / "operators/cos-plan-20261105"
        sys.path.insert(0, str(self.R / "operators/mc-route-roles-20261105"))
        import op as O
        self.O, self.PF, self.CAT = O, O.PF, self.R / "config/model-routes/catalogue.json"
        self.meta = json.loads((self.OP / "payload/meta.json").read_text())
        self.old = {"agent": O.inspect(AGENT)["Config"]["Image"], "sched": O.inspect(SCHED)["Config"]["Image"]}
        self.images = {"agent": "minimoi-staging/cos-agent-a:plan-20261105", "sched": "minimoi-staging/cos-scheduler:plan-20261105"}

    def _run(self, *args, **kw):
        import subprocess
        p = subprocess.run(args, capture_output=True, text=True, timeout=kw.pop("timeout", 900), **kw)
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
        """Every file this replaces must be exactly the reviewed base, so the overlay undoes nothing live."""
        import hashlib
        import subprocess
        assert self.old["agent"].endswith(":partnership-fd6ca08a") or self.old["agent"] == self.images["agent"], "unexpected CoS agent image"
        assert self.old["sched"].endswith(":partnership-ec5579ff") or self.old["sched"] == self.images["sched"], "unexpected scheduler image"
        assert self.old != self.images, "already deployed"
        for name, m in self.meta.items():
            if m["base"]:
                got = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", self.old[m["k"]], m["dest"]], capture_output=True).stdout
                assert hashlib.sha256(got).hexdigest() == m["base"], "live file differs from the reviewed base: " + name
        assert not (self.OP / "recovery-required.json").exists(), "an earlier run needs recovery"

    def build_images(self):
        import shutil
        d = self.OP / "ctx"
        shutil.rmtree(d, ignore_errors=True)
        for k in self.images:
            (d / k / "ctx").mkdir(parents=True)
        for name, m in self.meta.items():
            x = d / m["k"] / "ctx" / m["dest"].lstrip("/")
            x.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.OP / "payload" / name, x)
        for k, tag in self.images.items():
            (d / k / "Dockerfile").write_text(f"FROM {self.old[k]}\nCOPY ctx/ /\n")
            self._run("docker", "build", "--pull=false", "--network", "none", "-t", tag, str(d / k))
        shutil.rmtree(d)

    def signed_in_profile(self):
        """The healthy plan profile on CoS's main agent, or None. Names and states only."""
        out = self._run("docker", "exec", AGENT, "node", "/app/openclaw.mjs", "models", "status", "--agent", "cos-agent-a", "--json")
        good = [p for p in json.loads(out)["auth"]["oauth"]["profiles"] if p.get("provider") == "openai" and p.get("type") == "oauth" and p.get("status") == "ok"]
        return good[0]["profileId"] if len(good) == 1 else None

    def share_auth(self, profile):
        """OpenClaw's own helper, as MC used it: the same owner's profile, inside this runtime only. Then every agent must see it."""
        script = (self.R / "operators/mc-chatgpt-plan-20261105/share-auth.mjs").read_text().replace("'mc-chat'", "'cos-agent-a'")
        self._run("docker", "exec", "-i", AGENT, "node", "--input-type=module", input=script)
        for agent in AGENTS:
            got = json.loads(self._run("docker", "exec", AGENT, "node", "/app/openclaw.mjs", "models", "status", "--agent", agent, "--json"))
            assert any(p.get("profileId") == profile and p.get("status") == "ok" for p in got["auth"]["oauth"]["profiles"]), "no plan profile on " + agent

    def check_new_images(self, profile):
        """The runtime's own validator and the scheduler's own loader, before anything live is touched."""
        env = [x for k, v in agent_env(profile).items() for x in ("-e", f"{k}={v}")]
        self._run("docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", *env, "--entrypoint", "sh", self.images["agent"], "-c",
                  "cp /opt/minimoi/cos-agent-a/openclaw.json /tmp/c.json && node /opt/minimoi/cos-agent-a/plan-access.cjs /tmp/c.json "
                  # The runtime's startup migration stamps this on every start; the pinned file alone fails the strict validator.
                  "&& node -e \"const f=require('fs'),c=JSON.parse(f.readFileSync('/tmp/c.json'));c.agents.ownership='explicit';f.writeFileSync('/tmp/c.json',JSON.stringify(c))\" "
                  "&& OPENCLAW_CONFIG_PATH=/tmp/c.json node /app/openclaw.mjs config validate --json")
        tmp = self.OP / "catalogue.check.json"
        tmp.write_text(json.dumps(plan_catalogue(json.loads(self.CAT.read_text()))))
        tmp.chmod(0o644)
        try:
            self._run("docker", "run", "--rm", "--network", "none", "-v", f"{tmp}:/c.json:ro", "--entrypoint", "python", self.images["sched"], "-c",
                      "import core.model_routes as m, domains.cos.model_route;c=m.load_catalogue('/c.json');assert m.snapshot(c,'cos-conversation')['route_id']=='" + A + "'")
        finally:
            tmp.unlink(missing_ok=True)

    def _render(self, pf_doc=None):
        script = (self.R / "bin/compose.sh").read_text()
        assert str(self.PF) in script
        if pf_doc is None:
            return json.loads(self._run(str(self.R / "bin/compose.sh"), "config", "--format", "json"))
        pf, alt = self.OP / "pf.check.private.yml", self.OP / "compose.check.private.sh"
        pf.write_text(json.dumps(pf_doc, indent=2))
        alt.write_text(script.replace(str(self.PF), str(pf)))
        alt.chmod(0o700)
        try:
            return json.loads(self._run("bash", str(alt), "config", "--format", "json"))
        finally:
            pf.unlink(missing_ok=True)
            alt.unlink(missing_ok=True)

    def check_render(self, profile):
        check_render(self._render(), self._render(overlaid(json.loads(self.PF.read_text()), self.images, profile)), self.images, profile)

    def save_before(self):
        import shutil
        shutil.copy2(self.PF, self.OP / "before.compose.private.yml")
        shutil.copy2(self.CAT, self.OP / "before.catalogue.json")
        (self.OP / "before.contract.json").write_text(json.dumps({"images": {n: self.O.inspect(n)["Image"] for n in (AGENT, SCHED)},
                                                                   "agent": self.O.contract(AGENT), "sched": self.O.contract(SCHED)}))

    def check_saved(self):
        for name in ("before.compose.private.yml", "before.catalogue.json", "before.contract.json"):
            assert (self.OP / name).exists(), "nothing was applied: no " + name

    def stop_workers(self):
        self._run("docker", "stop", "--time", "120", "minimoi-guildhall-cos")

    def start_workers(self):
        self._run("docker", "start", "minimoi-guildhall-cos")

    def wait_idle(self):
        import time
        code = ("import os,sqlite3,time;d=sqlite3.connect('file:'+os.environ['COS_TOOL_BINDINGS_FILE']+'?mode=ro',uri=True);"
                "print(d.execute('select count(*) from bindings where revoked is null and expires>?',(time.time(),)).fetchone()[0])")
        end = time.monotonic() + 300
        while time.monotonic() < end:
            if self._run("docker", "exec", SCHED, "python", "-c", code).strip() == "0":
                return
            time.sleep(5)
        raise RuntimeError("CoS still has a live turn; nothing changed")

    def write_new(self, profile):
        import os
        self.PF.write_text(json.dumps(overlaid(json.loads(self.PF.read_text()), self.images, profile), indent=2))
        tmp = self.CAT.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan_catalogue(json.loads(self.CAT.read_text())), indent=2) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.CAT)

    def recreate(self):
        self.O.portal_compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", AGENT_SVC)
        self.O.wait_healthy(AGENT, None, 660)       # the gateway's owner lease can take minutes to clear
        self.O.portal_compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", SCHED_SVC)
        self.O.wait_healthy(SCHED, None, 180)

    def verify_new(self):
        saved = json.loads((self.OP / "before.contract.json").read_text())
        for name, key, env, mount in ((AGENT, "agent", AGENT_ENV, None), (SCHED, "sched", SCHED_ENV, ROUTES_MOUNT)):
            assert self.O.inspect(name)["Config"]["Image"] == self.images[key], "image: " + name
            assert json.loads(json.dumps(self.O.contract(name, env, mount))) == json.loads(json.dumps(saved[key])), "contract changed: " + name
        pinned = json.loads(self._run("docker", "exec", AGENT, "cat", "/opt/minimoi/cos-agent-a/openclaw.json"))
        check_runtime(pinned, json.loads(self._run("docker", "exec", AGENT, "cat", "/home/node/.openclaw/openclaw.json")))
        assert self.signed_in_profile(), "plan profile unhealthy"
        self._run("docker", "exec", SCHED, "python", "-c", "import urllib.request as u;assert u.urlopen('http://127.0.0.1:8769/health',timeout=20).status==200")

    def restore_verified(self):
        import shutil
        shutil.copy2(self.OP / "before.compose.private.yml", self.PF)
        shutil.copy2(self.OP / "before.catalogue.json", self.CAT)
        self.recreate()
        saved = json.loads((self.OP / "before.contract.json").read_text())
        for name, key in ((AGENT, "agent"), (SCHED, "sched")):
            assert self.O.inspect(name)["Image"] == saved["images"][name], "restored image differs: " + name
            assert json.loads(json.dumps(self.O.contract(name))) == json.loads(json.dumps(saved[key])), "restored contract differs: " + name


if __name__ == "__main__":                          # pragma: no cover
    import fcntl
    import sys
    from guarded_swap import guarded_swap
    host = RealHost()
    with (host.R / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy = Deploy(host, guarded_swap)
        print("RESULT", {"dry-run": deploy.dry_run, "apply": deploy.apply, "rollback": deploy.rollback}[sys.argv[1]](), flush=True)
