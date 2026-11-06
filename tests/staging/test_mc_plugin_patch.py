"""MC's plugin and portal patch: the order, the gate, and what may change. Synthetic: no Docker, no host."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import importlib.util
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


gs, op = _load("guarded_swap"), _load("mc_plugin_patch")


class Boom(Exception):
    pass


class Host:
    def __init__(self, lose_gate_in=None, **fail):
        self.fail, self.calls, self.records, self.alive, self.lose = fail, [], {}, True, lose_gate_in

    def __getattr__(self, name):
        def step(*args):
            self.calls.append(name)
            if self.lose == name:
                self.alive = False
            if self.fail.get(name):
                raise Boom(name)
        return step

    @contextmanager
    def gate(self):
        self.calls.append("gate_open")
        yield "held"
        self.calls.append("gate_close")

    def gate_alive(self, _):
        return self.alive

    def record(self, name, detail):
        self.records[name] = detail


def patch(host):
    return op.Patch(host, gs.guarded_swap)


def test_a_dry_run_moves_nothing():
    host = Host()
    patch(host).dry_run()
    assert not {"stop_workers", "stop_mc", "write_new", "recreate", "start_workers", "save_before", "gate_open"} & set(host.calls)


@pytest.mark.parametrize("step", ["check_baseline", "build_images", "check_new_images", "check_render", "save_before"])
def test_a_failed_check_stops_before_anything_moves(step):
    host = Host(**{step: True})
    with pytest.raises(Boom):
        patch(host).apply()
    assert not {"stop_workers", "stop_mc", "write_new"} & set(host.calls)


def test_a_good_apply_holds_the_gate_and_verifies_before_workers_resume():
    host = Host()
    assert patch(host).apply() is gs.Outcome.APPLIED
    c = host.calls
    assert c.index("stop_workers") < c.index("gate_open") < c.index("stop_mc") < c.index("write_new") < c.index("recreate") < c.index("verify_new")
    assert c.index("verify_new") < c.index("gate_close") < c.index("start_workers")


@pytest.mark.parametrize("step", ["stop_mc", "write_new", "recreate", "verify_new"])
def test_a_failed_apply_restores_then_resumes(step):
    host = Host(**{step: True})
    with pytest.raises(Boom, match=step):
        patch(host).apply()
    assert host.calls.index("restore_verified") < host.calls.index("start_workers") and host.records["outcome"] == {"outcome": "restored"}


def test_a_failed_restore_leaves_workers_stopped():
    host = Host(verify_new=True, restore_verified=True)
    with pytest.raises(Boom):
        patch(host).apply()
    assert "start_workers" not in host.calls and "recovery_required" in host.records


def test_a_lost_gate_never_restores_without_it_and_leaves_workers_stopped():
    host = Host(lose_gate_in="recreate", recreate=True)
    with pytest.raises(Boom):
        patch(host).apply()
    assert "restore_verified" not in host.calls and "start_workers" not in host.calls


def _rendered():
    return {"services": {"portal": {"image": "p:old", "environment": {"B": "2"}}, "other": {"image": "o"}}}


def test_the_render_check_accepts_only_the_services_image():
    after = copy.deepcopy(_rendered())
    after["services"]["portal"]["image"] = "p:new"
    op.only_image(_rendered(), after, "portal", "p:new")
    for mutate in (lambda d: d["services"]["other"].update(image="x"), lambda d: d["services"]["portal"]["environment"].update(B="3"),
                   lambda d: d["services"]["portal"].update(image="p:wrong"), lambda d: d["services"].update(extra={})):
        wrong = copy.deepcopy(after)
        mutate(wrong)
        with pytest.raises(AssertionError):
            op.only_image(_rendered(), wrong, "portal", "p:new")


def _policy():
    return {"agents": {"entries": {"mc-chat": {"tools": {"deny": ["group:runtime"], "fs": {"workspaceOnly": True}}}, "mc-agent": {"tools": {"deny": []}}}},
            "auth": {"profiles": {"p": {"mode": "oauth"}}}}


def test_the_policy_check_refuses_any_loosening():
    op.check_policy(_policy())
    for mutate in (lambda c: c["agents"]["entries"]["mc-chat"]["tools"].update(deny=[]),
                   lambda c: c["agents"]["entries"]["mc-chat"]["tools"]["fs"].update(workspaceOnly=False),
                   lambda c: c["auth"]["profiles"]["p"].update(mode="api_key"),
                   lambda c: c.update(models={"providers": {"x": {}}})):
        wrong = _policy()
        mutate(wrong)
        with pytest.raises(AssertionError):
            op.check_policy(wrong)


def test_a_tag_that_moved_is_refused():
    op.check_tag_is_running("t:old", "sha256:aaa", "sha256:aaa")
    for tag_id, running in (("sha256:bbb", "sha256:aaa"), ("", "")):
        with pytest.raises(AssertionError, match="no longer names"):
            op.check_tag_is_running("t:old", tag_id, running)


def test_a_payload_that_is_not_what_was_reviewed_is_refused():
    import hashlib
    meta = {"a.mjs": {"after": hashlib.sha256(b"reviewed").hexdigest()}}
    op.check_payload(meta, lambda _: b"reviewed")
    with pytest.raises(AssertionError, match="differs from what was reviewed"):
        op.check_payload(meta, lambda _: b"swapped")
    with pytest.raises(AssertionError, match="empty"):
        op.check_payload({}, lambda _: b"")


def test_a_restored_runtime_config_must_equal_the_saved_one():
    saved = {"agents": {"x": 1}, "auth": {"p": 1}, "meta": {"lastTouchedAt": "a"}}
    op.same_runtime(saved, {**saved, "meta": {"lastTouchedAt": "b"}})
    for live in ({**saved, "agents": {"x": 2}}, {"agents": {"x": 1}, "meta": {}}, {**saved, "extra": 1}):
        with pytest.raises(AssertionError, match="differs from the saved"):
            op.same_runtime(saved, live)

