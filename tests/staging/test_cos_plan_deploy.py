"""The CoS plan deploy's order and checks. Synthetic: no Docker, no host, no sign-in."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/staging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gs, op = _load("guarded_swap"), _load("cos_plan_deploy")
IMAGES = {"agent": "a:new", "sched": "s:new"}
PROFILE = "openai:robert"


class Boom(Exception):
    pass


class Host:
    def __init__(self, signed_in=True, **fail):
        self.signed_in, self.fail, self.calls, self.records = signed_in, fail, [], {}

    def __getattr__(self, name):
        def step(*args):
            self.calls.append(name)
            if self.fail.get(name):
                raise Boom(name)
        return step

    def signed_in_profile(self):
        self.calls.append("signed_in_profile")
        return PROFILE if self.signed_in else None

    def record(self, name, detail):
        self.records[name] = detail


LIVE = {"write_new", "recreate", "verify_new", "restore_verified", "stop_workers", "start_workers", "share_auth", "save_before"}


def deploy(host):
    return op.Deploy(host, gs.guarded_swap)


def test_a_dry_run_touches_nothing_live_and_works_before_sign_in():
    host = Host(signed_in=False)
    assert deploy(host).dry_run()["would_apply"] is False
    assert not LIVE & set(host.calls) and {"check_baseline", "build_images", "check_new_images", "check_render"} <= set(host.calls)


def test_apply_refuses_without_a_sign_in_and_changes_nothing():
    host = Host(signed_in=False)
    with pytest.raises(RuntimeError, match="sign in first"):
        deploy(host).apply()
    assert not LIVE & set(host.calls)


@pytest.mark.parametrize("step", ["check_baseline", "build_images", "check_new_images", "check_render", "share_auth"])
def test_a_failed_check_stops_before_any_worker_or_container_moves(step):
    host = Host(**{step: True})
    with pytest.raises(Boom):
        deploy(host).apply()
    assert not {"stop_workers", "write_new", "recreate"} & set(host.calls)


def test_a_good_apply_verifies_before_workers_resume():
    host = Host()
    assert deploy(host).apply() is gs.Outcome.APPLIED
    assert host.calls.index("share_auth") < host.calls.index("stop_workers") < host.calls.index("write_new")
    assert host.calls.index("verify_new") < host.calls.index("start_workers") and "restore_verified" not in host.calls


@pytest.mark.parametrize("step", ["write_new", "recreate", "verify_new"])
def test_a_failed_apply_restores_then_resumes_and_still_reports_failure(step):
    host = Host(**{step: True})
    with pytest.raises(Boom, match=step):
        deploy(host).apply()
    assert host.calls.index("restore_verified") < host.calls.index("start_workers")
    assert host.records["outcome"] == {"outcome": "restored"}


def test_a_failed_restore_leaves_workers_stopped_with_a_receipt():
    host = Host(verify_new=True, restore_verified=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "start_workers" not in host.calls and "recovery_required" in host.records


def test_a_turn_still_live_refuses_with_nothing_changed_and_workers_back():
    host = Host(wait_idle=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "write_new" not in host.calls and host.calls[-1] == "start_workers"


def test_a_full_disk_during_failure_still_restores_or_stays_stopped(monkeypatch):
    class NoStderr:
        def write(self, *_):
            raise OSError(28, "No space left")
        def flush(self):
            raise OSError(28, "No space left")
    monkeypatch.setattr(gs.sys, "stderr", NoStderr())

    def no_record(self, name, detail):
        raise OSError(28, "No space left")
    monkeypatch.setattr(Host, "record", no_record)
    host = Host(verify_new=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert host.calls.index("restore_verified") < host.calls.index("start_workers")
    host = Host(verify_new=True, restore_verified=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "start_workers" not in host.calls


def test_rollback_needs_saved_state_and_a_failed_one_leaves_workers_stopped():
    host = Host(check_saved=True)
    with pytest.raises(Boom):
        deploy(host).rollback()
    assert "stop_workers" not in host.calls
    host = Host()
    assert deploy(host).rollback() is gs.Outcome.APPLIED and host.calls[-1] == "start_workers"
    host = Host(restore_verified=True)
    with pytest.raises(Boom):
        deploy(host).rollback()
    assert "start_workers" not in host.calls and "recovery_required" in host.records


# ── The pure checks ──

def _rendered():
    agent = {"image": "a:old", "environment": {"X": "1"}, "volumes": []}
    sched = {"image": "s:old", "environment": {"Y": "2"}, "volumes": [{"target": "/data", "source": "v"}]}
    return {"services": {"cos-agent-a": agent, "cos-scheduler": sched, "portal": {"image": "p"}}}


def _after():
    doc = copy.deepcopy(_rendered())
    s = doc["services"]
    s["cos-agent-a"].update(image="a:new", pull_policy="never")
    s["cos-agent-a"]["environment"].update(op.agent_env(PROFILE))
    s["cos-scheduler"].update(image="s:new", pull_policy="never")
    s["cos-scheduler"]["environment"]["MINIMOI_MODEL_ROUTES_FILE"] = op.ROUTES_FILE
    s["cos-scheduler"]["volumes"].append({"target": op.ROUTES_MOUNT, "read_only": True})
    return doc


def test_the_render_check_accepts_exactly_the_plan():
    op.check_render(_rendered(), _after(), IMAGES, PROFILE)


@pytest.mark.parametrize("mutate", [
    lambda d: d["services"]["portal"].update(image="other"),
    lambda d: d["services"].update(extra={}),
    lambda d: d["services"]["cos-agent-a"]["environment"].update(MORE="x"),
    lambda d: d["services"]["cos-agent-a"]["environment"].update(COS_PLAN_MODEL="openai/other"),
    lambda d: d["services"]["cos-scheduler"]["volumes"].append({"target": "/more"}),
    lambda d: d["services"]["cos-scheduler"]["volumes"][-1].update(read_only=False),
    lambda d: d["services"]["cos-scheduler"].update(image="s:wrong"),
])
def test_the_render_check_refuses_anything_more(mutate):
    after = _after()
    mutate(after)
    with pytest.raises((AssertionError, KeyError)):
        op.check_render(_rendered(), after, IMAGES, PROFILE)


def test_the_overlay_adds_only_the_plan_and_leaves_other_services():
    doc = {"services": {"portal": {"image": "p", "environment": {"K": "v"}}}}
    out = op.overlaid(doc, IMAGES, PROFILE)
    assert out["services"]["portal"] == doc["services"]["portal"] and "cos-agent-a" not in doc["services"]
    assert out["services"]["cos-scheduler"]["volumes"] == [f"{op.ROUTES_DIR}:{op.ROUTES_MOUNT}:ro"]


def _catalogue():
    from core.model_routes import load_catalogue
    return json.loads(json.dumps(load_catalogue(ROOT / "core/model_routes/catalogue.json")))


def test_the_catalogue_offers_only_a_and_makes_it_cos_default():
    from core.model_routes import route_of, snapshot
    out = op.plan_catalogue(_catalogue())
    assert snapshot(out, "cos-conversation")["route_id"] == op.A
    offered = [r["route_id"] for r in out["routes"] if r["offered"].get("cos") == "available"]
    assert offered == [op.A] and route_of(out, op.GATEWAY)["offered"]["cos"] != "available"
    assert out["roles"]["mc-conversation"] == _catalogue()["roles"]["mc-conversation"]
    assert [r["offered"].get("mc") for r in out["routes"]] == [r["offered"].get("mc") for r in _catalogue()["routes"]]


def _live():
    pinned = json.loads((ROOT / "docker/cos-agent-a/openclaw.json").read_text())
    live = copy.deepcopy(pinned)
    live.pop("models")
    live["auth"] = {"profiles": {PROFILE: {"provider": "openai", "mode": "oauth"}}}
    allow = [op.PLAN_MODEL, *op.PLAN_ROUTES.split(",")]
    live["agents"]["defaults"].update(thinkingDefault="high")
    for entry in [live["agents"]["defaults"], *live["agents"]["entries"].values()]:
        entry.update(model={"primary": op.PLAN_MODEL, "fallbacks": []}, modelPolicy={"allow": allow})
    return pinned, live


def test_the_runtime_check_accepts_the_plan():
    op.check_runtime(*_live())


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(models={"providers": {"minimoi-gateway": {}}}),
    lambda c: c["auth"]["profiles"][PROFILE].update(mode="api_key"),
    lambda c: c["agents"]["entries"]["cos-agent-a"]["model"].update(fallbacks=["x"]),
    lambda c: c["agents"]["entries"]["cos-meeting"]["modelPolicy"]["allow"].append("minimoi-gateway/minimoi-cos-agent"),
    lambda c: c["agents"]["entries"]["cos-closed"]["tools"].update(alsoAllow=["exec"]),
    lambda c: c["agents"]["defaults"].update(thinkingDefault="low"),
])
def test_the_runtime_check_refuses_anything_else(mutate):
    pinned, live = _live()
    mutate(live)
    with pytest.raises(AssertionError):
        op.check_runtime(pinned, live)


# ── The agent-only patch ──

def test_the_patch_render_check_accepts_only_the_agents_image():
    patch = _load("cos_agent_patch")
    before = _rendered()
    after = copy.deepcopy(before)
    after["services"]["cos-agent-a"]["image"] = "a:new"
    patch.only_agent_image(before, after, "a:new")
    for mutate in (lambda d: d["services"]["cos-scheduler"].update(image="s:other"),
                   lambda d: d["services"]["cos-agent-a"]["environment"].update(X="changed"),
                   lambda d: d["services"]["cos-agent-a"].update(image="a:wrong")):
        wrong = copy.deepcopy(after)
        mutate(wrong)
        with pytest.raises(AssertionError):
            patch.only_agent_image(before, wrong, "a:new")
