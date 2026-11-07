"""Dev's fast path: a release-to-release update that rebuilds only what changed, FROM what runs, and restores the release that was
running when anything fails. Synthetic: no Docker, no host, no AWS. Built on test_beta_dev_deploy's pretend Docker."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tarfile
import time

import pytest

import test_beta_dev_deploy as base

op, pay, gs = base.op, base.pay, base.gs
sha = base.sha
ROOT = base.ROOT
NEXT = "beta-next"
H, I = "H" * 40, "I" * 40


# ── A second commit, I, after the one Dev runs (H) ──

class NextGit(base._FakeGit):
    """At I, the changed paths hold newer bytes; everything else is H's. Every version since the oldest is a committed one."""

    def __init__(self, changed, tree=None, deleted=()):
        super().__init__(changed, deleted)
        self.tree = tree if tree is not None else {"minimoi_portal/other.py": "2" * 64}

    def rev(self, ref):
        return {"head": H, "next": I}.get(ref, "B" * 40)

    def blob(self, commit, path):
        if commit == I and path in self.changed:
            return b"newer " + path.encode()
        return super().blob(H if commit == I else commit, path)

    def versions(self, since, commit, path):
        return [H, commit]

    def tree_hashes(self, commit, prefixes):
        return {p: h for p, h in self.tree.items() if any(p.startswith(x.rstrip("/")) for x in prefixes)}


def next_meta(changed, release=NEXT, tree=None):
    return pay.build_meta(NextGit(changed, tree), "next", base.BASES, from_ref="head", release=release)


# ── The pretend Docker, made faithful where an update leans on it ──

class Docker(base.FakeDocker):
    def __init__(self, root, meta):
        super().__init__(root, meta)
        self.probe_ok, self.health_on_up = True, None

    def build(self, args):
        out = super().build(args)
        tag = args[args.index("-t") + 1]
        if out.returncode == 0:                     # an image inherits its base's ENV lines, as Docker's do
            first = (Path(args[-1]) / "Dockerfile").read_text().splitlines()[0].split()[1]
            self.images[tag]["added"] = [*self.images[first].get("added", []), *self.images[tag].get("added", [])]
            self.images[self.images[tag]["Id"]] = self.images[tag]
        return out

    def compose(self, wrapper_path, args):
        out = super().compose(wrapper_path, args)
        if self.health_on_up and out.returncode == 0 and "up" in args and "--no-start" not in args:
            name = self.render("mc" if wrapper_path.endswith("mc.sh") else "compose")["services"][args[-1]]["container_name"]
            if name in self.health_on_up:
                self.containers[name]["State"]["Health"] = {"Status": "starting"}
        return out

    def __call__(self, args, **kw):
        args = [str(a) for a in args]
        if args[:2] == ["docker", "run"] and "-c" in args and op.LAYOUT_HASHES in args:
            image = self.images[args[args.index("--entrypoint") + 2]]
            return base._Result(json.dumps({p: image["files"].get(p) for p in json.loads(kw["input"])}) + "\n")
        if args[:2] == ["docker", "exec"] and args[3:4] == ["probe"]:
            return base._Result("", 0 if self.probe_ok else 1)
        return super().__call__(args, **kw)


class Op(base.FakeOp):
    def contract(self, name):
        c = self.docker.containers[name]
        return {"env": sorted(c["Config"]["Env"]), "host": c["HostConfig"], "mounts": c["Mounts"]}


def _write_release(OP, meta, blobs, name):
    tar = pay.make_tar(meta, blobs, {"beta_dev_deploy.py": b"#", "guarded_swap.py": b"#"})
    import shutil
    shutil.rmtree(OP / name, ignore_errors=True)           # a later send replaces it, as the host's unpack does
    (OP / name).mkdir()
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as t:
        t.extractall(OP / name, filter="data")
    (OP / f"{name}.tar.gz").write_bytes(tar)
    (OP / f"{name}.sha256").write_text(sha(tar) + "\n")


@pytest.fixture
def dev(tmp_path, monkeypatch):
    """Dev after a verified apply of release H (op.RELEASE). ``dev.send(changed)`` lands an update to I in release-next/."""
    root = tmp_path
    meta, blobs = pay.build_meta(base._FakeGit(base.ALL_CHANGED), "head", base.BASES)
    OP = root / "operators" / op.OPERATOR_NAME
    OP.mkdir(parents=True)
    _write_release(OP, meta, blobs, "release")
    gateway = next(iter(meta["targets"]["model-gateway"]["files"]))
    for rel, text in (("bin/compose.sh", 'exec docker compose -p minimoi-staging -f /r/x.yml "$@"\n'), ("bin/mc.sh", 'exec docker compose -p minimoi-mc -f /r/y.yml "$@"\n'),
                      ("config/litellm.staging.yaml", "old " + gateway),
                      ("config/model-routes/catalogue.json", json.dumps({"roles": {"cos-conversation": {"route": op.A}, "mc-conversation": {"route": op.A}}})),
                      ("config/staging-aws.yml", "services: {}\n"), ("operators/inc/compose.partnership2.yml", "{}\n"), ("operators/inc/mc.partnership2.yml", "{}\n"),
                      ("mc-release/mc.yml", "{}\n"), ("mc-state/openclaw.json", json.dumps(base._policy()))):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    docker = Docker(root, meta)
    # a compose service the apply never touched: it runs its baseline image throughout
    docker._add("minimoi-staging/supervisor:running", "sha256:base-supervisor", {}, user="")
    docker._container("minimoi-supervisor", "minimoi-staging/supervisor:running", "sha256:base-supervisor", "compose", "supervisor", [],
                      compose_file=str(root / "config/staging-aws.yml"))
    # what the commit holds beyond any payload, in every image that lays it out
    for image in list(docker.images.values()):
        image["files"].setdefault("/app/minimoi_portal/other.py", "2" * 64)
    monkeypatch.setattr(op.subprocess, "run", docker)
    monkeypatch.setattr(op.time, "sleep", lambda *_: None)
    monkeypatch.setattr(op.shutil, "disk_usage", lambda _: type("U", (), {"free": 10 * 1024 ** 3}))
    O = Op(docker, root)
    first = op.RealHost(root=root, op_module=O)

    def api(method, path, body=None):
        docker.log.append(("api", method, path.split("?")[0]))
        if "/rename" in path:
            old, new = path.split("/")[2], path.split("name=")[1]
            docker.containers[new] = docker.containers.pop(old)
            docker.containers[new]["Name"] = "/" + new
        elif path.startswith("/containers/create"):
            name = path.split("name=")[1]
            docker.containers[name] = {"Id": "c2-" + name, "Name": "/" + name, "Image": body["Image"], "Mounts": [], "State": {"Running": False},
                                       "Config": {k: v for k, v in body.items() if k not in ("HostConfig", "NetworkingConfig")},
                                       "HostConfig": body["HostConfig"], "NetworkSettings": {"Networks": {"n": {"Aliases": []}}}}
        return {}
    first._api = api
    baseline = base._frozen(type("R", (), {"docker": docker, "root": root}))
    assert op.Deploy(first, gs.guarded_swap).apply() is gs.Outcome.APPLIED
    docker.log.clear()

    class Dev:
        pass
    d = Dev()
    d.root, d.OP, d.docker, d.O, d.api, d.baseline, d.meta = root, OP, docker, O, api, baseline, meta

    def send(changed, release=NEXT, tree=None, from_ref="head"):
        m, b = pay.build_meta(NextGit(changed, tree), "next", base.BASES, from_ref=from_ref, release=release)
        _write_release(OP, m, b, "release-next")
        return m

    def host():
        h = op.RealHost(root=root, op_module=O, update=True)
        h._api = api
        return h

    def fresh():
        h = op.RealHost(root=root, op_module=O)
        h._api = api
        return h
    d.send, d.host, d.fresh = send, host, fresh
    d.running = lambda: {n: c["Image"] for n, c in docker.containers.items()}
    d.ups = lambda: [e[1] for e in docker.log if e[0] == "up"]
    d.stops = lambda: [n for e in docker.log if e[0] == "stop" for n in e[1]]
    return d


def _override(d, wrapper):
    return json.loads((d.OP / ("compose.beta.yml" if wrapper == "compose" else "mc.beta.yml")).read_text())


def _files(d):
    return {p: (d.root / p).read_bytes() for p in ("bin/compose.sh", "bin/mc.sh", "config/litellm.staging.yaml", "config/model-routes/catalogue.json",
                                                   "mc-state/openclaw.json")} | {n: (d.OP / n).read_bytes() for n in ("compose.beta.yml", "mc.beta.yml")}


# ── The fast path ──

def test_a_css_change_rebuilds_and_restarts_the_portal_alone_from_the_image_it_runs(dev):
    dev.send(["minimoi_portal/static/site.css"])
    before, files = dev.running(), _files(dev)
    h = dev.host()
    assert op.Deploy(h, gs.guarded_swap).update() is gs.Outcome.APPLIED
    assert dev.ups() == ["portal"] and "minimoi-mc-agent" not in dev.stops()
    after = dev.running()
    assert after["minimoi-portal"] != before["minimoi-portal"]
    assert {n: i for n, i in after.items() if n != "minimoi-portal"} == {n: i for n, i in before.items() if n != "minimoi-portal"}
    new = dev.docker.images[after["minimoi-portal"]]
    assert new["RootFS"]["Layers"][:-1] == dev.docker.images[before["minimoi-portal"]]["RootFS"]["Layers"]       # FROM the running release's image
    assert new["files"]["/app/minimoi_portal/static/site.css"] == sha(b"newer minimoi_portal/static/site.css")
    # only this operator's compose override moved, and only the portal's line in it
    assert _override(dev, "compose")["services"]["portal"] == {"image": f"minimoi-staging/portal:{NEXT}", "pull_policy": "never"}
    old = json.loads(files["compose.beta.yml"])["services"]
    assert {s: v for s, v in _override(dev, "compose")["services"].items() if s != "portal"} == {s: v for s, v in old.items() if s != "portal"}
    now = _files(dev)
    assert all(now[p] == files[p] for p in files if p != "compose.beta.yml")
    # the next release is now the running one; the one before is kept; the record says so
    assert json.loads((dev.OP / "release/meta.json").read_text())["release"] == NEXT
    assert json.loads((dev.OP / "release.prev/meta.json").read_text())["release"] == op.RELEASE
    assert not (dev.OP / "release-next").exists() and not (dev.OP / op.UPDATE_IN_FLIGHT).exists()
    assert json.loads((dev.OP / "result.json").read_text())["status"] == "updated_verified"
    assert json.loads((dev.root / op.DELIVERY_DIR / "DEV.json").read_text())["release"] == NEXT


def test_the_cos_to_mc_switch_is_not_named_twice_on_an_overlay_that_already_holds_it(dev):
    dev.send(["minimoi_portal/app.py"])
    h = dev.host()
    op.Deploy(h, gs.guarded_swap).update()
    env = dev.docker.containers["minimoi-portal"]["Config"]["Env"]
    assert env.count("MINIMOI_COS_TO_MC=1") == 1
    assert op.dockerfile_for(op.TARGETS["portal"], "sha256:x", "", ["MINIMOI_COS_TO_MC=1"]).count("ENV ") == 0
    assert op.dockerfile_for(op.TARGETS["portal"], "sha256:x", "", []).count("ENV MINIMOI_COS_TO_MC=1") == 1


def test_everything_is_built_and_checked_before_a_worker_stops(dev):
    dev.send(["minimoi_portal/app.py"])
    op.Deploy(dev.host(), gs.guarded_swap).update()
    log = dev.docker.log
    last_build = max(i for i, e in enumerate(log) if e[0] == "build")
    first_stop = min(i for i, e in enumerate(log) if e[0] == "stop")
    assert last_build < first_stop < log.index(("gate", "open")) < log.index(("up", "portal")) < log.index(("gate", "closed"))
    closed = log.index(("gate", "closed"))
    assert any(e[0] == "start" for e in log[closed:]) and not any(e[0] == "start" for e in log[:closed])


def test_an_mc_persona_change_restarts_mc_cleanly_and_ships_its_whole_persona(dev):
    persona = op.PERSONA
    tree = {persona + "AGENTS.md": "a" * 64, persona + "manifest.json": "b" * 64, persona + "RUNTIME.openclaw.md": "c" * 64}
    m = dev.send([persona + "AGENTS.md"], tree=tree)
    mc = m["targets"]["mc-agent"]["files"]
    assert set(mc) == set(tree)                    # the build empties persona/: the overlay carries all of it, not only what changed
    assert set(m["targets"]) == {"mc-agent", "portal"}          # the portal reads the persona too
    op.Deploy(dev.host(), gs.guarded_swap).update()
    assert dev.ups() == ["portal", "mc-agent"] and dev.stops().count("minimoi-mc-agent") == 1
    assert dev.docker.log.index(("stop", ["minimoi-mc-agent"])) < dev.docker.log.index(("up", "mc-agent"))   # a clean stop first
    assert _override(dev, "mc")["services"]["mc-agent"]["image"] == f"minimoi-staging/mc-agent:{NEXT}"
    assert _override(dev, "mc")["services"]["mc-relay"]["image"] == f"minimoi-staging/mc-relay:{op.RELEASE}"


def test_mc_ready_by_its_own_healthcheck_is_accepted_while_docker_still_says_starting(dev):
    dev.send(["minimoi_portal/app.py"])
    dev.docker.containers["minimoi-portal"]["Config"]["Healthcheck"] = {"Test": ["CMD", "probe"]}
    dev.docker.health_on_up = {"minimoi-portal"}
    h = dev.host()
    op.Deploy(h, gs.guarded_swap).update()
    assert dev.docker.containers["minimoi-portal"]["State"]["Health"]["Status"] == "starting" and "minimoi-portal" in h.ready_by_probe


# ── Failure, and the restore to the release that was running ──

def _assert_on_running_release(dev, before, files):
    assert dev.running() == before
    assert _files(dev) == files
    assert json.loads((dev.OP / "release/meta.json").read_text())["release"] == op.RELEASE
    assert not (dev.OP / op.UPDATE_IN_FLIGHT).exists() and not (dev.OP / "recovery-required.json").exists()
    assert sorted(w for w in op.WORKERS if dev.docker.containers.get(w, {}).get("State", {}).get("Running")) == \
        sorted(set(op.WORKERS) - {"minimoi-cos-board", "minimoi-records"})


def test_a_failed_verify_restores_the_running_release_not_the_baseline(dev):
    dev.send(["minimoi_portal/app.py"])
    before, files = dev.running(), _files(dev)
    dev.docker.routes["/app/cos/work-cards"] = 404
    with pytest.raises(AssertionError, match="route missing"):
        op.Deploy(dev.host(), gs.guarded_swap).update()
    _assert_on_running_release(dev, before, files)
    assert dev.ups() == ["portal", "portal"]                    # the update's, then the restore's: nothing else moved
    assert json.loads((dev.OP / "outcome.json").read_text())["outcome"] == "restored"
    assert (dev.OP / "release-next").exists()                   # kept for a look; a later send replaces it


def test_a_running_file_that_differs_after_the_swap_restores(dev):
    dev.send(["minimoi_portal/app.py"])
    before, files = dev.running(), _files(dev)
    dev.docker.bad_running_file = ("minimoi-portal",)
    with pytest.raises(AssertionError, match="holds a different file"):
        op.Deploy(dev.host(), gs.guarded_swap).update()
    dev.docker.bad_running_file = None
    _assert_on_running_release(dev, before, files)


def test_mc_never_ready_restores_mc_to_the_running_release(dev):
    persona = op.PERSONA
    dev.send([persona + "AGENTS.md"], tree={persona + "AGENTS.md": "a" * 64})
    before, files = dev.running(), _files(dev)
    calls = {"n": 0}
    real = dev.O.wait_healthy

    def first_mc_never_ready(name, gate, limit):
        if name == "minimoi-mc-agent":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("minimoi-mc-agent did not become ready in time")
        return real(name, gate, limit)
    dev.O.wait_healthy = first_mc_never_ready
    with pytest.raises(RuntimeError, match="ready in time"):
        op.Deploy(dev.host(), gs.guarded_swap).update()
    _assert_on_running_release(dev, before, files)
    assert json.loads((dev.root / "mc-state/openclaw.json").read_text()) == base._policy()


def test_a_failed_restore_leaves_workers_stopped_and_requires_recovery(dev):
    dev.send(["minimoi_portal/app.py"])
    dev.docker.routes["/app/cos/work-cards"] = 404
    seen = {"portal": 0}

    def second_portal_up_fails(service):
        if service == "portal":
            seen["portal"] += 1
            return seen["portal"] == 2
        return False
    dev.docker.fail_up = second_portal_up_fails
    with pytest.raises(AssertionError, match="route missing"):
        op.Deploy(dev.host(), gs.guarded_swap).update()
    assert (dev.OP / "recovery-required.json").exists()
    assert not [w for w in op.WORKERS if dev.docker.containers.get(w, {}).get("State", {}).get("Running")]
    dev.docker.fail_up = None
    dev.send(["minimoi_portal/app.py"], release="beta-next-2")
    with pytest.raises(AssertionError, match="needs recovery"):
        op.Deploy(dev.host(), gs.guarded_swap).update_dry_run()


def test_a_worker_that_fails_after_the_swap_puts_the_running_release_back(dev):
    dev.send(["services/rooms_worker/worker.py"])
    before, files = dev.running(), _files(dev)
    h = dev.host()
    real = h._heartbeat
    seen = {"n": 0}

    def first_heartbeat_fails(name):
        seen["n"] += 1
        if seen["n"] == 1:
            raise RuntimeError(name + " did not publish a fresh worker heartbeat")
        return real(name)
    h._heartbeat = first_heartbeat_fails
    with pytest.raises(RuntimeError, match="heartbeat"):
        op.Deploy(h, gs.guarded_swap).update()
    assert dev.running() == before and _files(dev) == files
    assert not [n for n in dev.docker.containers if "-prev-" in n]       # the kept containers are back under their own names
    assert json.loads((dev.OP / "release/meta.json").read_text())["release"] == op.RELEASE


def test_a_standalone_worker_update_keeps_the_running_container_until_verified_then_drops_it(dev):
    dev.send(["services/rooms_worker/worker.py"])
    before = dev.running()
    op.Deploy(dev.host(), gs.guarded_swap).update()
    assert dev.running()["minimoi-guildhall-mc"] != before["minimoi-guildhall-mc"]
    assert not [n for n in dev.docker.containers if "-prev-" in n]
    assert f"minimoi-guildhall-mc-before-{op.RELEASE}" in dev.docker.containers         # the baseline's own backup stays


# ── Refused before anything moves ──

def _refused_untouched(dev, match, exc=(op.Refused, AssertionError)):
    before, files = dev.running(), _files(dev)
    with pytest.raises(exc, match=match):
        op.Deploy(dev.host(), gs.guarded_swap).update()
    assert dev.running() == before and _files(dev) == files
    assert not [e for e in dev.docker.log if e[0] in ("up", "stop", "start", "api")]
    assert not (dev.OP / op.UPDATE_IN_FLIGHT).exists()


def test_an_update_not_built_from_what_dev_runs_is_refused(dev):
    dev.send(["minimoi_portal/app.py"], from_ref="other")
    _refused_untouched(dev, "was not built from it")


def test_an_update_that_reuses_the_running_release_name_is_refused(dev):
    dev.send(["minimoi_portal/app.py"], release=op.RELEASE)
    _refused_untouched(dev, "name of its own")


def test_a_next_tag_that_already_exists_is_refused(dev):
    dev.send(["minimoi_portal/app.py"])
    dev.docker.images[f"minimoi-staging/portal:{NEXT}"] = dev.docker.images[dev.running()["minimoi-portal"]]
    _refused_untouched(dev, "tag already exists")


def test_an_unfinished_earlier_update_is_refused(dev):
    dev.send(["minimoi_portal/app.py"])
    (dev.OP / op.UPDATE_IN_FLIGHT).write_text("{}")
    with pytest.raises(AssertionError, match="did not finish"):
        op.Deploy(dev.host(), gs.guarded_swap).update()


def test_an_update_with_nothing_applied_is_refused(dev):
    op.Deploy(dev.fresh(), gs.guarded_swap).rollback()
    dev.docker.log.clear()
    dev.send(["minimoi_portal/app.py"])
    with pytest.raises(AssertionError, match="nothing applied"):
        op.Deploy(dev.host(), gs.guarded_swap).update()


def test_a_tampered_next_payload_is_refused(dev):
    dev.send(["minimoi_portal/app.py"])
    next(p for p in (dev.OP / "release-next/payload").rglob("app.py")).write_bytes(b"tampered")
    _refused_untouched(dev, "differs from what was reviewed")


def test_an_image_whose_other_files_are_not_the_commits_is_refused(dev):
    dev.send(["minimoi_portal/app.py"], tree={"minimoi_portal/other.py": "9" * 64})
    _refused_untouched(dev, "files beyond this update differ")
    assert not [e for e in dev.docker.log if e[0] == "stop"]


def test_a_hand_edited_running_file_is_refused(dev):
    dev.send(["minimoi_portal/app.py"])
    dev.docker.images[dev.running()["minimoi-portal"]]["files"]["/app/minimoi_portal/app.py"] = "f" * 64
    _refused_untouched(dev, "no commit explains")


def test_a_standalone_worker_the_release_never_touched_goes_the_full_way(dev):
    # As if the apply had never touched Guildhall's MC worker: its baseline backup does not exist, so an update cannot make one.
    state = dev.OP / "before/state.private.json"
    saved = json.loads(state.read_text())
    saved["specs"].pop("guildhall-mc")
    state.write_text(json.dumps(saved))
    dev.send(["services/rooms_worker/worker.py"])
    _refused_untouched(dev, "never touched")


# ── Routes stay on when they can ──

def _routes_on(dev):
    R = dev.root / "operators" / op.ROUTES_OPERATOR
    (R / "before").mkdir(parents=True)
    (R / "compose.routes.yml").write_text(json.dumps({"services": {}}))
    renders = {w: dev.docker.render(w, [str(dev.OP / ("compose.beta.yml" if w == "compose" else "mc.beta.yml"))]) for w in ("compose", "mc")}
    images = {n: dev.docker.containers[n]["Image"] for n in op.ROUTED}
    (R / "before/state.private.json").write_text(json.dumps({"renders": renders, "images": images, "where": {}}))
    return R


def test_with_routes_on_an_update_that_touches_what_they_verified_is_refused(dev):
    _routes_on(dev)
    dev.send(["docker/mc-agent-container/start.sh"])
    _refused_untouched(dev, "routes are on")


def test_with_routes_on_a_portal_update_runs_and_moves_the_routes_saved_images_on(dev):
    R = _routes_on(dev)
    dev.send(["minimoi_portal/app.py"])
    op.Deploy(dev.host(), gs.guarded_swap).update()
    state = json.loads((R / "before/state.private.json").read_text())
    assert state["renders"]["compose"]["services"]["portal"]["image"] == f"minimoi-staging/portal:{NEXT}"
    assert state["images"]["minimoi-mc-agent"] == dev.running()["minimoi-mc-agent"]       # unchanged: MC did not move
    assert (R / f"before/state.private.before-{NEXT}.json").exists()


def test_the_routes_rebase_moves_only_images():
    state = {"renders": {"compose": {"services": {"portal": {"image": "a", "environment": {"X": "1"}}, "german": {"image": "g"}}}},
             "images": {"minimoi-mc-agent": "sha256:old"}, "contracts": {"minimoi-mc-agent": {"env": ["K=v"]}}}
    new = op.rebase_routes_state(state, {"compose": {"portal": "b"}, "mc": {"mc-agent": "m"}}, {"minimoi-mc-agent": "sha256:new", "minimoi-portal": "sha256:p"})
    assert new["renders"]["compose"]["services"]["portal"] == {"image": "b", "environment": {"X": "1"}, "pull_policy": "never"}
    assert new["renders"]["compose"]["services"]["german"] == {"image": "g"}
    assert new["images"] == {"minimoi-mc-agent": "sha256:new"} and new["contracts"] == state["contracts"]
    assert state["images"]["minimoi-mc-agent"] == "sha256:old"           # the input is not changed


def test_routes_sensitive_files_are_found_by_path_and_prefix():
    targets = {"mc-agent": {"container": "minimoi-mc-agent", "files": {"a": {"dest": op.MC_DIR + "start.sh"}, "b": {"dest": op.MC_DIR + "persona/AGENTS.md"}}},
               "portal": {"container": "minimoi-portal", "files": {"c": {"dest": "/app/core/model_routes/__init__.py"}, "d": {"dest": "/app/minimoi_portal/app.py"}}}}
    assert op.routes_sensitive_hits(targets) == ["minimoi-mc-agent: " + op.MC_DIR + "start.sh", "minimoi-portal: /app/core/model_routes/__init__.py"]


# ── A baseline rollback still works after updates ──

def test_after_an_update_a_rollback_still_returns_dev_to_its_pre_beta_baseline(dev):
    dev.send(["minimoi_portal/app.py"])
    op.Deploy(dev.host(), gs.guarded_swap).update()
    assert op.Deploy(dev.fresh(), gs.guarded_swap).rollback() is gs.Outcome.APPLIED
    now = base._frozen(type("R", (), {"docker": dev.docker, "root": dev.root}))
    assert now["images"] == dev.baseline["images"] and now["tags"] == dev.baseline["tags"]
    assert now["files"] == dev.baseline["files"]


def test_after_updates_and_a_rollback_the_update_left_in_release_is_never_applied_to_the_baseline(dev):
    dev.send(["minimoi_portal/app.py"])
    op.Deploy(dev.host(), gs.guarded_swap).update()
    op.Deploy(dev.fresh(), gs.guarded_swap).rollback()
    dev.docker.log.clear()
    for step in ("dry_run", "apply"):
        with pytest.raises(AssertionError, match="holds an update"):
            getattr(op.Deploy(dev.fresh(), gs.guarded_swap), step)()
    assert not [e for e in dev.docker.log if e[0] in ("build", "up", "stop")]


def test_an_update_that_first_touches_a_target_extends_the_baseline_a_rollback_uses(dev):
    # The apply never touched the supervisor, so it runs its baseline image; the update is the first to rebuild it.
    assert "supervisor" not in json.loads((dev.OP / "before/state.private.json").read_text()).get("specs", {})
    dev.send(["services/supervisor/main.py"])
    op.Deploy(dev.host(), gs.guarded_swap).update()
    assert dev.running()["minimoi-supervisor"] != "sha256:base-supervisor"
    saved = json.loads((dev.OP / "before/state.private.json").read_text())
    assert saved["specs"]["supervisor"]["container"] == "minimoi-supervisor" and saved["images"]["supervisor"] == "sha256:base-supervisor"
    assert set(saved["specs"]) >= set(dev.meta["targets"])           # the first apply's targets stay in it
    op.Deploy(dev.fresh(), gs.guarded_swap).rollback()
    assert dev.running()["minimoi-supervisor"] == "sha256:base-supervisor"
    assert dev.running()["minimoi-portal"] == dev.baseline["images"]["minimoi-portal"]


def test_an_update_dry_run_builds_and_checks_and_changes_nothing_live(dev, capsys):
    dev.send(["minimoi_portal/app.py"])
    before, files = dev.running(), _files(dev)
    plan = op.Deploy(dev.host(), gs.guarded_swap).update_dry_run()
    assert plan["mode"] == "update" and plan["release"] == NEXT and [r["container"] for r in plan["recreate_in_order"]] == ["minimoi-portal"]
    assert dev.running() == before and _files(dev) == files
    assert not [e for e in dev.docker.log if e[0] in ("up", "stop", "start", "api")] and [e for e in dev.docker.log if e[0] == "build"]
    assert "WILL RECREATE minimoi-portal" in capsys.readouterr().out and not (dev.OP / op.UPDATE_IN_FLIGHT).exists()


# ── Whether routes come off for an update ──

rp = base._load("beta_dev_routes_payload")
SAME = {"beta_dev_routes.py": "1" * 64, "mc_keys.sh": "2" * 64}


def _host(applied=True, recovery=False, **sha_over):
    shas = {**SAME, **sha_over}
    out = f"APPLIED {'yes' if applied else 'no'}\n" + ("RECOVERY yes\n" if recovery else "") + "".join(f"SHA {n} {h}\n" for n, h in shas.items() if h)
    return rp.parse_check(out)


def test_routes_stay_on_when_devs_routes_operator_is_this_commits():
    assert rp.routes_decision(SAME, _host())[0] == "skip"


def test_routes_cycle_when_their_own_logic_differs_or_cannot_be_read():
    assert rp.routes_decision(SAME, _host(**{"mc_keys.sh": "9" * 64}))[0] == "cycle"
    assert rp.routes_decision(SAME, _host(**{"beta_dev_routes.py": ""}))[0] == "cycle"


def test_routes_off_or_needing_recovery_are_said_so():
    assert rp.routes_decision(SAME, _host(applied=False))[0] == "off"
    assert rp.routes_decision(SAME, _host(recovery=True))[0] == "stop"


def test_the_routes_check_is_one_bounded_read_of_devs_folder():
    s = rp.check_script()
    assert "while" not in s and "sleep" not in s and rp.HOST_DIR in s and "release/operator/beta_dev_routes.py" in s
    assert not __import__("re").search(r"/opt/minimoi(?![-\w])", s)


# ── The Mac side ──

def test_an_update_payload_carries_only_what_changed_since_dev_and_says_where_it_came_from():
    meta, blobs = next_meta(["minimoi_portal/static/site.css"])
    assert meta["mode"] == "update" and meta["from"] == {"commit": H} and meta["release"] == NEXT
    assert set(meta["targets"]) == {"portal"} and set(blobs) == {"minimoi_portal/static/site.css"}
    f = meta["targets"]["portal"]["files"]["minimoi_portal/static/site.css"]
    assert f["after"] == sha(b"newer minimoi_portal/static/site.css") and f["base"] == sha(b"new minimoi_portal/static/site.css")
    lay = meta["targets"]["portal"]["layout_digest"]
    assert lay["count"] == 1 and lay["sha256"] == op.layout_digest({"/app/minimoi_portal/other.py": "2" * 64})
    assert "layout_files" not in meta["targets"]["portal"] and meta["tree"] == ["/app/minimoi_portal/other.py"]


def test_an_update_needs_a_release_name():
    with pytest.raises(AssertionError, match="release name"):
        pay.build_meta(NextGit(["minimoi_portal/app.py"]), "next", base.BASES, from_ref="head")


def test_layout_paths_and_digest_agree_on_both_sides():
    tree = ["/app/core/a.py", "/app/minimoi_portal/x.py", "/app/minimoi_portal/y.py"]
    assert op.layout_paths(tree, ("minimoi_portal/",), {"minimoi_portal/x.py"}) == ["/app/minimoi_portal/y.py"]
    assert op.layout_paths(tree, (), set()) == []
    assert op.layout_digest({"/a": "1"}) != op.layout_digest({"/a": None}) == op.layout_digest({"/a": "-"})


def test_a_small_update_goes_in_one_ssm_call_to_release_next_and_never_replaces_the_running_release():
    meta, blobs = next_meta(["minimoi_portal/static/site.css"])
    tar = pay.make_tar(meta, blobs, {"beta_dev_deploy.py": (ROOT / "scripts/staging/beta_dev_deploy.py").read_bytes(),
                                     "guarded_swap.py": (ROOT / "scripts/staging/guarded_swap.py").read_bytes()})
    assert pay.landing_for(tar) == "release-next"
    scripts = pay.chunk_scripts(tar, sha(tar), landing=pay.landing_for(tar))
    assert len(scripts) == 1 and pay.envelope_size(scripts[0]) < pay.SSM_LIMIT
    one = scripts[0]
    assert one.index("sha256sum -c - >/dev/null") < one.index('release-next.tar.gz.new" | sha256sum -c -') < one.index("tar -xzf")
    assert '[ -e "$D/compose.beta.yml" ] ||' in one and op.UPDATE_IN_FLIGHT in one
    assert 'rm -rf "$D/release-next"' in one and 'rm -rf "$D/release"\n' not in one and '"$D/release.sha256"' not in one


def test_the_unpack_rides_with_the_last_part_and_every_byte_still_arrives(tmp_path):
    tar = os.urandom(130_000)
    scripts = pay.chunk_scripts(tar, sha(tar), landing="release-next")
    assert len(scripts) == 3 and all(pay.envelope_size(s) < pay.SSM_LIMIT for s in scripts)
    assert "tar -xzf" in scripts[-1] and "tar -xzf" not in scripts[0]


def test_a_full_release_still_lands_only_where_nothing_is_applied():
    meta, blobs = pay.build_meta(base._FakeGit(base.CHANGED), "head", base.BASES)
    tar = pay.make_tar(meta, blobs, {"beta_dev_deploy.py": b"#", "guarded_swap.py": b"#"})
    assert pay.landing_for(tar) == "release"
    last = pay.chunk_scripts(tar, sha(tar))[-1]
    assert '[ ! -e "$D/compose.beta.yml" ] || { echo \'already applied: roll back first\'; exit 1; }' in last


def test_an_update_runs_the_operator_it_was_sent_with():
    s = pay.start_script("update")
    assert '"$D/release-next/operator/beta_dev_deploy.py" update' in s and "[ -f release-next.sha256 ]" in s
    assert '"$D/release/operator/beta_dev_deploy.py" apply' in pay.start_script("apply")
    with pytest.raises(AssertionError):
        pay.start_script("update; rm -rf /")


# ── Readiness ──

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _ready(states, probes, limit=60, gate=lambda: True):
    clock, states, probes = Clock(), iter(states), iter(probes)
    last = {}

    def inspect():
        last["s"] = next(states, last.get("s"))
        return {"State": last["s"]}
    return op.wait_ready("mc", inspect=inspect, probe=lambda: next(probes, False), gate_ok=gate, limit=limit, sleep=clock.sleep, clock=clock), clock.t


RUN = {"Running": True, "Health": {"Status": "starting"}}


def test_ready_as_soon_as_its_own_check_passes_twice():
    who, waited = _ready([RUN] * 10, [False, True, True])
    assert who == "probe" and waited == 2 * op.READY_INTERVAL


def test_one_pass_then_a_failure_starts_counting_again():
    who, waited = _ready([RUN] * 10, [True, False, True, True])
    assert who == "probe" and waited == 3 * op.READY_INTERVAL


def test_docker_saying_healthy_is_enough():
    assert _ready([{"Running": True, "Health": {"Status": "healthy"}}], [])[0] == "docker"


def test_unhealthy_a_lost_gate_and_the_time_limit_each_stop_the_wait():
    with pytest.raises(RuntimeError, match="unhealthy"):
        _ready([{"Running": True, "Health": {"Status": "unhealthy"}}], [True, True])
    with pytest.raises(AssertionError, match="gate"):
        _ready([RUN] * 5, [False] * 5, gate=lambda: False)
    with pytest.raises(RuntimeError, match="in time"):
        _ready([RUN] * 100, [False] * 100, limit=30)


def test_a_restarting_container_is_never_ready_by_probe():
    with pytest.raises(RuntimeError, match="in time"):
        _ready([{"Running": True, "Restarting": True}] * 100, [True] * 100, limit=30)


def test_the_healthcheck_runs_as_docker_would_run_it():
    assert op.healthcheck_argv(["CMD", "node", "-e", "x"]) == ["node", "-e", "x"]
    assert op.healthcheck_argv(["CMD-SHELL", "curl -f x"]) == ["sh", "-c", "curl -f x"]
    assert op.healthcheck_argv(["NONE"]) is None and op.healthcheck_argv(None) is None


# ── MC's clean stop (docker/mc-agent-container/start.sh) ──

def _gateway_loop(tmp_path, fake_gateway):
    """start.sh from its gateway loop to the end, with a fake OpenClaw. The steps before it are tested elsewhere."""
    text = (ROOT / "docker/mc-agent-container/start.sh").read_text()
    loop = text[text.index("START_ATTEMPTS="):]
    gw = tmp_path / "openclaw.sh"
    gw.write_text(fake_gateway)
    script = tmp_path / "loop.sh"
    script.write_text("set -eu\nlog() { echo \"start: $*\" >&2; }\nOPENCLAW=\"sh " + str(gw) + "\"\n" + loop)
    return script


SHELLS = [s for s in ("sh", "dash") if __import__("shutil").which(s)]        # MC's image runs Debian's dash


@pytest.mark.parametrize("shell", SHELLS)
def test_a_stop_reaches_the_gateway_waits_for_it_and_never_retries(tmp_path, shell):
    marks = tmp_path / "marks"
    script = _gateway_loop(tmp_path, f"""trap 'sleep 1; echo released >> {marks}; exit 0' TERM
echo started >> {marks}
while :; do sleep 0.1; done
""")
    p = subprocess.Popen([shell, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    for _ in range(50):
        if marks.exists():
            break
        time.sleep(0.1)
    time.sleep(0.3)
    os.killpg(p.pid, signal.SIGTERM)                # docker stop's TERM, to the whole group, as tini -g delivers it
    out, err = p.communicate(timeout=20)
    assert p.returncode == 0, err
    assert marks.read_text().split() == ["started", "released"]     # one start, one TERM, and the gateway finished its own shutdown
    assert "stopped: the gateway shut down on its own" in err and "attempt 2" not in err


@pytest.mark.parametrize("shell", SHELLS)
def test_a_gateway_that_exits_early_on_its_own_is_still_retried(tmp_path, shell):
    marks = tmp_path / "marks"
    script = _gateway_loop(tmp_path, f"echo run >> {marks}\nexit 1\n")
    env = {**os.environ, "MC_START_ATTEMPTS": "2"}
    text = script.read_text().replace("sleep 30", "sleep 0")
    script.write_text(text)
    p = subprocess.run([shell, str(script)], capture_output=True, text=True, env=env, timeout=20)
    assert p.returncode == 1 and marks.read_text().split() == ["run", "run"]


@pytest.mark.skipif(not all(__import__("shutil").which(t) for t in ("sha256sum", "base64", "tar")), reason="needs sha256sum, base64 and tar")
@pytest.mark.parametrize("applied,in_flight,ok", [(True, False, True), (False, False, False), (True, True, False)])
def test_the_one_call_transfer_really_unpacks_beside_the_running_release(tmp_path, applied, in_flight, ok):
    meta, blobs = next_meta(["minimoi_portal/static/site.css"])
    tar = pay.make_tar(meta, blobs, {"beta_dev_deploy.py": b"#", "guarded_swap.py": b"#"})
    (script,) = pay.chunk_scripts(tar, sha(tar), landing="release-next")
    D = tmp_path / "op"
    (D / "release").mkdir(parents=True)
    (D / "release/meta.json").write_text("running")
    if applied:
        (D / "compose.beta.yml").write_text("{}")
    if in_flight:
        (D / op.UPDATE_IN_FLIGHT).write_text("{}")
    local = script.replace(f"D={pay.HOST_DIR}\n", f"D={D}\n")
    assert local.count(f"D={D}\n") == 1
    p = subprocess.run(["sh", "-c", local], capture_output=True, text=True, timeout=60)
    assert (p.returncode == 0) is ok, p.stdout + p.stderr
    assert (D / "release/meta.json").read_text() == "running"                   # the running release is never touched
    if ok:
        assert json.loads((D / "release-next/meta.json").read_text())["release"] == NEXT
        assert (D / "release-next.sha256").read_text().strip() == sha(tar) and (D / "release-next.tar.gz").read_bytes() == tar
        assert not (D / "incoming-next").exists()
    else:
        assert not (D / "release-next").exists()
