#!/usr/bin/env python3
"""Dev only: replace one reviewed file in CoS's agent image, on the plan deploy's guarded path. Nothing else moves: the scheduler,
catalogue, sign-in and plan settings stay. ``dry-run`` changes nothing live. Workers resume only after a verified result."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess

from cos_plan_deploy import AGENT, AGENT_ENV, AGENT_SVC, Deploy, RealHost, SCHED, check_runtime
from mc_plugin_patch import check_payload, check_tag_is_running

OLD, NEW = "minimoi-staging/cos-agent-a:plan-20261105c", "minimoi-staging/cos-agent-a:plan-20261105d"


def only_agent_image(before: dict, after: dict, image: str) -> None:
    """What compose would run differs by the agent's image, and by nothing else on any service."""
    b, a = copy.deepcopy(before["services"]), copy.deepcopy(after["services"])
    assert a[AGENT_SVC].pop("image") == image, "agent image"
    b[AGENT_SVC].pop("image")
    assert a == b, "changed beyond the agent's image"


class PatchHost(RealHost):                          # pragma: no cover  (exercised by the dry-run on Dev)
    def __init__(self):
        super().__init__()
        self.OP = self.R / "operators/cos-plan-20261105/patch-d"
        self.meta = json.loads((self.OP / "payload/meta.json").read_text())
        self.old = {"agent": OLD}
        self.images = {"agent": NEW}

    def check_baseline(self):
        live = self.O.inspect(AGENT)
        assert live["Config"]["Image"] == OLD, "unexpected live agent image"
        check_tag_is_running(OLD, self._image_id(OLD), live["Image"])
        check_payload(self.meta, lambda name: (self.OP / "payload" / name).read_bytes())
        assert self.O.inspect(SCHED)["Config"]["Image"].endswith(":plan-20261105"), "unexpected live scheduler image"
        assert not (self.OP / "recovery-required.json").exists(), "an earlier run needs recovery"
        for name, m in self.meta.items():
            got = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", OLD, m["dest"]], capture_output=True).stdout
            assert hashlib.sha256(got).hexdigest() == m["base"], "live file differs from the reviewed base: " + name

    def _image_id(self, tag):
        return json.loads(self._run("docker", "image", "inspect", tag))[0]["Id"]

    def build_images(self):
        check_payload(self.meta, lambda name: (self.OP / "payload" / name).read_bytes())    # again, right before it ships
        super().build_images()
        self.new_id = self._image_id(NEW)
        (self.OP / "new-image.json").write_text(json.dumps({"agent": self.new_id}))

    def check_new_images(self, profile):
        for name, m in self.meta.items():                   # what is inside the built image, not just what went in
            got = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", self.new_id, m["dest"]], capture_output=True).stdout
            assert hashlib.sha256(got).hexdigest() == m["after"], "built image holds a different file: " + name
        env = [x for e in self.O.inspect(AGENT)["Config"]["Env"] if e.split("=")[0] in AGENT_ENV for x in ("-e", e)]
        self._run("docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", *env, "--entrypoint", "sh", NEW, "-c",
                  "cp /opt/minimoi/cos-agent-a/openclaw.json /tmp/c.json && node /opt/minimoi/cos-agent-a/plan-access.cjs /tmp/c.json "
                  "&& node -e \"const f=require('fs'),c=JSON.parse(f.readFileSync('/tmp/c.json'));c.agents.ownership='explicit';f.writeFileSync('/tmp/c.json',JSON.stringify(c))\" "
                  "&& OPENCLAW_CONFIG_PATH=/tmp/c.json node /app/openclaw.mjs config validate --json")

    def _patched(self):
        doc = json.loads(self.PF.read_text())
        assert doc["services"][AGENT_SVC]["image"] == OLD, "agent image in the override"
        doc["services"][AGENT_SVC]["image"] = NEW
        return doc

    def check_render(self, profile):
        only_agent_image(self._render(), self._render(self._patched()), NEW)

    def share_auth(self, profile):
        """Already shared by the plan deploy, and left alone. Every agent must still see it."""
        for agent in ("cos-agent-a", "cos-meeting", "cos-closed"):
            got = json.loads(self._run("docker", "exec", AGENT, "node", "/app/openclaw.mjs", "models", "status", "--agent", agent, "--json"))
            assert any(p.get("profileId") == profile and p.get("status") == "ok" for p in got["auth"]["oauth"]["profiles"]), "no plan profile on " + agent

    def save_before(self):
        import shutil
        shutil.copy2(self.PF, self.OP / "before.compose.private.yml")
        (self.OP / "before.contract.json").write_text(json.dumps({"image": self.O.inspect(AGENT)["Image"], "agent": self.O.contract(AGENT),
                                                                   "sched_id": self.O.inspect(SCHED)["Id"]}))

    def check_saved(self):
        for name in ("before.compose.private.yml", "before.contract.json"):
            assert (self.OP / name).exists(), "nothing was applied: no " + name

    def write_new(self, profile):
        self.PF.write_text(json.dumps(self._patched(), indent=2))

    def _up_agent(self):
        self.O.portal_compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", AGENT_SVC)
        self.O.wait_healthy(AGENT, None, 660)

    def recreate(self):
        self._up_agent()

    def _same(self, image_check):
        saved = json.loads((self.OP / "before.contract.json").read_text())
        image_check(saved)
        assert json.loads(json.dumps(self.O.contract(AGENT))) == json.loads(json.dumps(saved["agent"])), "agent contract changed"
        assert self.O.inspect(SCHED)["Id"] == saved["sched_id"], "the scheduler was touched"

    def verify_new(self):
        def image(_):
            live = self.O.inspect(AGENT)
            assert live["Config"]["Image"] == NEW and live["Image"] == self.new_id, "agent image"
        self._same(image)
        pinned = json.loads(self._run("docker", "exec", AGENT, "cat", "/opt/minimoi/cos-agent-a/openclaw.json"))
        check_runtime(pinned, json.loads(self._run("docker", "exec", AGENT, "cat", "/home/node/.openclaw/openclaw.json")))
        assert self.signed_in_profile(), "plan profile unhealthy"

    def restore_verified(self):
        import shutil
        shutil.copy2(self.OP / "before.compose.private.yml", self.PF)
        self._up_agent()

        def image(saved):
            assert self.O.inspect(AGENT)["Image"] == saved["image"], "restored image differs"
        self._same(image)


if __name__ == "__main__":                          # pragma: no cover
    import fcntl
    import sys
    from guarded_swap import guarded_swap
    host = PatchHost()
    with (host.R / ".renewal-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy = Deploy(host, guarded_swap)
        print("RESULT", {"dry-run": deploy.dry_run, "apply": deploy.apply, "rollback": deploy.rollback}[sys.argv[1]](), flush=True)
