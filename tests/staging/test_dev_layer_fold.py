"""Dev's layer fold: side layers into one reviewed override per wrapper. Synthetic: temp dirs, fake wrappers and layers shaped
like Dev's, and a stub for `docker compose config` with its own merge. No Docker, no host, no AWS."""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/staging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


F = _load("dev_layer_fold")
SECRET = "SECRETVALUE"
sha = lambda b: hashlib.sha256(b).hexdigest()


# ── A stub `docker compose config`, merging independently of the tool ──

class _Tag:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


class _L(yaml.SafeLoader):
    pass


for _t in ("!override", "!reset"):
    _L.add_constructor(_t, lambda loader, node, t=_t: _Tag(t, loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode)
                                                           else loader.construct_mapping(node, deep=True)))


def _files(text, root):
    text = text.replace("$R", str(root))
    return [m.group(1) for m in re.finditer(r'(?<!\[ )-f\s+"?([^\s"]+)"?', text)]


def _target(v):
    return v["target"] if isinstance(v, dict) else v.split(":")[1]


def _norm_vol(v):
    if isinstance(v, dict):
        return v
    parts = v.split(":")
    return {"type": "bind", "source": parts[0], "target": parts[1], **({"read_only": True} if parts[2:] == ["ro"] else {})}


def fake_render(root):
    def render(script_text, stdin=None):
        out = {"name": "minimoi-staging", "services": {}, "networks": {}}
        for path in _files(script_text, root):
            text = stdin if path == "/dev/stdin" else Path(path).read_text()
            try:
                doc = json.loads(text)
            except ValueError:
                doc = yaml.load(text, Loader=_L) or {}
            for net, cfg in (doc.get("networks") or {}).items():
                out["networks"][net] = cfg
            for name, cfg in (doc.get("services") or {}).items():
                svc = out["services"].setdefault(name, {})
                for key, value in cfg.items():
                    if key == "environment":
                        env = value if isinstance(value, dict) else dict(e.split("=", 1) for e in value)
                        svc.setdefault("environment", {}).update({k: str(v) for k, v in env.items()})
                    elif key == "volumes":
                        if value == []:                 # compose omits an empty list
                            continue
                        if isinstance(value, _Tag):
                            svc["volumes"] = [_norm_vol(v) for v in value.value]
                        else:
                            have = {_target(v): v for v in svc.get("volumes", [])}
                            for v in value:
                                have[_target(v)] = _norm_vol(v)
                            svc["volumes"] = list(have.values())
                    else:
                        svc[key] = value
        for svc in out["services"].values():
            if "volumes" in svc:
                svc["volumes"] = sorted(svc["volumes"], key=lambda v: v["target"])
        return out
    return render


# ── A Dev-shaped staging root ──

def _w(path: Path, doc, fmt="json"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) if fmt == "json" else doc)


INC1_YML = """\
networks:
  partnership:
    name: minimoi-partnership
services:
  portal:
    image: "minimoi-staging/portal:partnership-fd6ca08a"
    pull_policy: never
    environment:
      PARTNERSHIP_ENVIRONMENT: dev
      PORTAL_PARTNERSHIP_OWNER_KEY_FILE: /run/secrets/partnership/SECRETVALUE-owner
    networks: [default, partnership]
    volumes:
      - /srv/partnership:/run/secrets/partnership:ro
  cos-agent-a:
    image: "minimoi-staging/cos-agent-a:partnership-fd6ca08a"
    pull_policy: never
    volumes: !override
      - /srv/openclaw:/home/node/.openclaw
      - /srv/cos-key:/run/cos-key:ro
  partnership-tasks:
    image: "minimoi-staging/cos-scheduler:partnership-fd6ca08a"
    pull_policy: never
    container_name: minimoi-partnership-tasks
    command: ["python", "-m", "services.partnership_tasks"]
    restart: unless-stopped
    environment:
      PARTNERSHIP_ENVIRONMENT: dev
      PARTNERSHIP_OWNER_KEY_FILE: /run/secrets/partnership/SECRETVALUE-key
    volumes:
      - /srv/partnership-data:/data
"""


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "staging"
    ops = root / "operators"
    _w(root / "config/base.yml", {"services": {
        "portal": {"image": "minimoi-staging/portal:base", "container_name": "minimoi-portal", "environment": {"MINIMOI_ENV": "dev"}},
        "cos-scheduler": {"image": "minimoi-staging/cos-scheduler:base", "environment": {"COS_BACKGROUND_JOBS": "0"}},
        "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:base", "volumes": ["/srv/base:/minimoi/base"]},
        "model-gateway": {"image": "minimoi-staging/cos-scheduler:model-gateway-base"}}})
    _w(root / "config/harness.yml", {"services": {"supervisor": {"image": "minimoi-staging/supervisor:base"}}})
    _w(root / "config/mc-base.yml", {"services": {
        "mc-agent": {"image": "minimoi-staging/mc-agent:base", "environment": {"MC_ENV": "dev"}},
        "mc-relay": {"image": "minimoi-staging/mc-relay:base"}}})
    layers = {
        "minimoi12-20261031-v3/compose.sh.images.json": {"services": {
            "cos-scheduler": {"image": "minimoi-staging/cos-scheduler:minimoi12-8481940d", "environment": {"COS_STORE_ENVIRONMENT": "dev"}},
            "portal": {"image": "minimoi-staging/portal:minimoi12-8481940d", "environment": {"MINIMOI_MC_EVERYWHERE": "1"}}}},
        "minimoi12-20261031-v6/compose.sh.images.json": {"services": {"portal": {"image": "minimoi-staging/portal:minimoi12-79b30639"}}},
        "cos-actions-20261102/compose.sh.images.json": {"services": {
            "cos-scheduler": {"image": "minimoi-staging/cos-scheduler:minimoi12-cf2ba9d9",
                              "environment": {"COS_ACTIONS_TOKEN_FILE": "/run/secrets/cos-actions-token", "COS_STORE_DIR": "/var/lib/minimoi/cos-actions"},
                              "volumes": ["/srv/cos-actions:/var/lib/minimoi/cos-actions", f"/srv/{SECRET}-token:/run/secrets/cos-actions-token:ro"]},
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:minimoi12-cf2ba9d9", "environment": {"COS_ACTIONS_TOKEN_FILE": "/run/secrets/cos-actions-token"},
                            "volumes": [f"/srv/{SECRET}-token:/run/secrets/cos-actions-token:ro"]}}},
        "cos-board-20261102/compose.sh.images.json": {"services": {
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:minimoi12-29d7d59a", "volumes": ["/srv/board.json:/run/secrets/cos-board-profile.json:ro"]}}},
        "partnership-inc1-20261105/compose.partnership.yml": INC1_YML,
        "partnership-inc2-20261105/compose.partnership2.yml": {"services": {
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:plan-20261105d", "pull_policy": "never",
                            "environment": {"COS_RUNTIME_MODEL_ACCESS": "plan", "COS_PLAN_MODEL": "openai/x"}}}},
        "beta-dev-20261106/compose.beta.yml": {"services": {
            "portal": {"image": "minimoi-staging/portal:beta-20261110g", "pull_policy": "never"},
            "cos-scheduler": {"image": "minimoi-staging/cos-scheduler:beta-20261110g", "pull_policy": "never"},
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:beta-20261110f", "pull_policy": "never"},
            "partnership-tasks": {"image": "minimoi-staging/partnership-tasks:beta-20261108n", "pull_policy": "never"}}},
        "beta-dev-routes-20261106/compose.routes.yml": {"services": {
            "cos-agent-a": {"environment": {"COS_METERED_ROUTES": "three-routes", "COS_FAILOVER_ROUTES": "f", "COS_GATEWAY_KEY_CAPPED": "1"}}}},
        "work-modes-depth-v5-20261111/compose.sh.images.json": {"services": {
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:depth-a8255964", "volumes": [],
                            "environment": {"MINIMOI_DEPTH_PROOF": "1", "MINIMOI_RUNTIME_PROOF_MODULE": "/opt/x.cjs"}},
            "portal": {"image": "minimoi-staging/portal:depth-a8255964", "volumes": []}}},
        "work-modes-depth-v7-20261111/compose.sh.images.json": {"services": {
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:depth-c2df23bb", "volumes": [], "environment": {"COS_METERED_ROUTES": "four-routes"}}}},
        "career-sync-mounts-20261111/overlay.staging.json": {"services": {
            "cos-scheduler": {"environment": {"COS_WORK_SOURCE_ROOTS_FILE": "/minimoi/private/roots.json"}, "volumes": ["/srv/pro:/minimoi/private/professional:ro"]},
            "cos-agent-a": {"image": "minimoi-staging/cos-agent-a:career-06d77956", "volumes": ["/srv/pro:/minimoi/private/professional:ro"]}}},
        "data-gaps-507-510-20261112/overlay.staging.json": {"services": {
            "model-gateway": {"image": "minimoi-staging/cos-scheduler:model-gateway-gaps-507"},
            "cos-scheduler": {"image": "minimoi-staging/cos-scheduler:gaps-507-510"}}},
        # mc
        "partnership-inc2-20261105/mc.partnership2.yml": {"services": {
            "mc-agent": {"image": "minimoi-staging/mc-agent:route-roles-20261105g", "pull_policy": "never",
                         "environment": {"MC_MODEL_GATEWAY_KEY": f"sk-{SECRET}-inc2", "MC_PLAN_MODEL": "openai/x"}},
            "mc-relay": {"image": "minimoi-staging/mc-relay:route-roles-20261105e", "pull_policy": "never",
                         "environment": {"MC_RELAY_ROUTES": "inc2-routes", "MC_RELAY_CHAT_DB": "/db"}}}},
        "beta-dev-20261106/mc.beta.yml": {"services": {
            "mc-agent": {"image": "minimoi-staging/mc-agent:beta-20261110f", "pull_policy": "never"},
            "mc-relay": {"image": "minimoi-staging/mc-relay:beta-20261107b", "pull_policy": "never"}}},
        "beta-dev-routes-20261106/mc.routes.yml": {"services": {
            "mc-agent": {"environment": {"MC_MODEL_GATEWAY_KEY": f"sk-{SECRET}-routes", "MC_METERED_ROUTES": "three-routes"}},
            "mc-relay": {"environment": {"MC_RELAY_ROUTES": "routes-routes", "MC_RELAY_CHAT_DB": "/db"}}}},
        "work-modes-depth-v5-20261111/mc.sh.images.json": {"services": {
            "mc-agent": {"image": "minimoi-staging/mc-agent:depth-a8255964", "volumes": [], "environment": {"MINIMOI_DEPTH_PROOF": "1"}},
            "mc-relay": {"image": "minimoi-staging/mc-relay:depth-a8255964", "volumes": [], "environment": {"MC_RELAY_DEPTH_PROOFS": "/x"}}}},
        "work-modes-depth-v7-20261111/mc.sh.images.json": {"services": {
            "mc-agent": {"image": "minimoi-staging/mc-agent:depth-c2df23bb", "volumes": [], "environment": {"MC_METERED_ROUTES": "four-routes"}},
            "mc-relay": {"image": "minimoi-staging/mc-relay:depth-c2df23bb", "volumes": [], "environment": {"MC_RELAY_ROUTES": "four-routes-relay"}}}},
        "work-modes-notices-v1-20261111/mc.sh.images.json": {"services": {}},
        "mc-persona-0131-20261111b/overlay.mc.json": {"services": {"mc-agent": {"image": "minimoi-staging/mc-agent:fix-d437e81c"}}},
    }
    for ref, doc in layers.items():
        _w(ops / ref, doc, "yml" if isinstance(doc, str) else "json")
    o = f"{root}/operators"
    compose_refs = ["minimoi12-20261031-v3/compose.sh.images.json", "minimoi12-20261031-v6/compose.sh.images.json", "cos-actions-20261102/compose.sh.images.json",
                    "cos-board-20261102/compose.sh.images.json", "partnership-inc1-20261105/compose.partnership.yml", "partnership-inc2-20261105/compose.partnership2.yml",
                    "beta-dev-20261106/compose.beta.yml", "beta-dev-routes-20261106/compose.routes.yml", "work-modes-depth-v5-20261111/compose.sh.images.json",
                    "work-modes-depth-v7-20261111/compose.sh.images.json", "career-sync-mounts-20261111/overlay.staging.json", "data-gaps-507-510-20261112/overlay.staging.json"]
    mc_refs = ["partnership-inc2-20261105/mc.partnership2.yml", "beta-dev-20261106/mc.beta.yml", "beta-dev-routes-20261106/mc.routes.yml",
               "work-modes-depth-v5-20261111/mc.sh.images.json", "work-modes-depth-v7-20261111/mc.sh.images.json", "work-modes-notices-v1-20261111/mc.sh.images.json",
               "mc-persona-0131-20261111b/overlay.mc.json"]
    compose = (f"#!/bin/bash\n# compose.sh, Dev-shaped\nset -euo pipefail\nR={root}; REL={root}/release\nHARNESS=()\n"
               f"[ -f \"$R/config/harness.yml\" ] && HARNESS=(-f \"$R/config/harness.yml\")\n"
               f"exec docker compose -p minimoi-staging \\\n  --env-file \"$R/.env\" \\\n  -f \"$R/config/base.yml\" \\\n"
               f"  ${{HARNESS[@]+\"${{HARNESS[@]}}\"}} " + " ".join(f"-f {o}/{r}" for r in compose_refs) + ' "$@"\n')
    mc = (f"#!/bin/bash\nset -euo pipefail\nR={root}\nexec env MC_IMAGE_TAG=x docker compose -p minimoi-mc --env-file $R/mc/mc.env \\\n"
          f"  -f $R/config/mc-base.yml " + " ".join(f"-f {o}/{r}" for r in mc_refs) + ' "$@"\n')
    (root / "bin").mkdir(parents=True)
    for name, text in (("compose.sh", compose), ("mc.sh", mc)):
        (root / "bin" / name).write_text(text)
        os.chmod(root / "bin" / name, 0o700)
    running = {"minimoi-portal": "minimoi-staging/portal:depth-a8255964"}       # a side image, as on Dev now
    host = F.Host(root=root, date="20261112", render=fake_render(root), clock=lambda: 1_794_500_000.0, running=running.get)
    return host


DEFAULT = dict(F.DEFAULT_DECISIONS)


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in root.rglob("*") if p.is_file()}


# ── Classification ──

def test_wrapper_files_skip_file_tests_and_env_files(rig):
    files = F.wrapper_files(rig.wrapper("compose").read_text())
    assert files[0] == "$R/config/base.yml"                     # not the [ -f ... ] test, not --env-file
    assert not any("harness" in f for f in files[:1])


def test_plan_classifies_every_layer(rig):
    plan = rig.plan(DEFAULT)
    rows = {r["layer"]: r for r in plan["rows"]}
    assert rows["minimoi12-20261031-v6/compose.sh.images.json"]["kind"] == "image-only"
    assert rows["minimoi12-20261031-v6/compose.sh.images.json"]["image_superseded_by"] == {"portal": "beta-dev-20261106/compose.beta.yml"}
    assert rows["work-modes-notices-v1-20261111/mc.sh.images.json"]["kind"] == "empty"
    assert rows["mc-persona-0131-20261111b/overlay.mc.json"]["kind"] == "image-only"
    inc1 = rows["partnership-inc1-20261105/compose.partnership.yml"]
    assert inc1["kind"] == "settings" and inc1["groups"] == [9]
    assert "cos-agent-a:volumes:!override" in inc1["settings"] and "top:networks" in inc1["settings"]
    assert "partnership-tasks:key:container_name" in inc1["settings"]
    assert rows["work-modes-depth-v5-20261111/compose.sh.images.json"]["groups"] == [10]   # volumes: [] is no setting
    assert rows["data-gaps-507-510-20261112/overlay.staging.json"]["image_superseded_by"] == {
        "cos-scheduler": "beta-dev-20261106/compose.beta.yml", "model-gateway": "decision: --gateway-image"}
    assert not plan["unknown"] and not plan["unsuperseded"]
    assert plan["counts"] == {"settings": 11, "image-only": 3, "empty": 1}
    F.check_plan(plan)
    assert plan["base"]["compose"] == ["$R/config/base.yml"]


def test_reviewed_overrides_must_be_there_in_order(rig):
    path = rig.wrapper("mc")
    path.write_text(path.read_text().replace(f" -f {rig.R}/operators/beta-dev-routes-20261106/mc.routes.yml", ""))
    with pytest.raises(F.Refused, match="reviewed beta and routes"):
        rig.plan(DEFAULT)


def test_an_image_nothing_supersedes_is_refused(rig):
    path = rig.OPS / "minimoi12-20261031-v6/compose.sh.images.json"
    path.write_text(json.dumps({"services": {"curator": {"image": "minimoi-staging/curator:old"}}}))
    plan = rig.plan(DEFAULT)
    with pytest.raises(F.Refused, match="nothing supersedes"):
        F.check_plan(plan)


# ── Refusal on an unknown setting ──

def test_unknown_setting_refuses_and_changes_nothing(rig, capsys):
    path = rig.OPS / "career-sync-mounts-20261111/overlay.staging.json"
    doc = json.loads(path.read_text())
    doc["services"]["cos-scheduler"]["environment"]["COS_SURPRISE"] = f"{SECRET}-surprise"
    path.write_text(json.dumps(doc))
    plan = rig.plan(DEFAULT)
    assert plan["unknown"] == ["career-sync-mounts-20261111/overlay.staging.json: cos-scheduler:env:COS_SURPRISE"]
    before = _snapshot(rig.R)
    with pytest.raises(F.Refused, match="does not classify"):
        rig.fold(DEFAULT, "keep")
    assert _snapshot(rig.R) == before
    assert SECRET not in capsys.readouterr().out


def test_a_known_name_in_another_groups_layer_is_still_unknown(rig):
    path = rig.OPS / "minimoi12-20261031-v6/compose.sh.images.json"
    path.write_text(json.dumps({"services": {"portal": {"image": "p:x", "environment": {"MINIMOI_MC_EVERYWHERE": "1"}}}}))
    assert rig.plan(DEFAULT)["unknown"] == ["minimoi12-20261031-v6/compose.sh.images.json: portal:env:MINIMOI_MC_EVERYWHERE"]


def test_fold_needs_a_deliberate_gateway_pick(rig):
    with pytest.raises(F.Refused, match="gateway-image"):
        rig.fold(DEFAULT, None)


# ── The fold and its proof ──

def test_fold_rewrites_wrappers_and_proves_the_render(rig):
    before = {w: fake_render(rig.R)(rig.wrapper(w).read_text()) for w in F.WRAPPERS}
    result = rig.fold(DEFAULT, "keep")
    assert result["status"] == "folded"
    ops = f"{rig.R}/operators"
    compose = rig.wrapper("compose").read_text()
    assert compose.endswith(f'-f {ops}/beta-dev-20261106/compose.beta.yml -f {ops}/beta-dev-routes-20261106/compose.routes.yml '
                            f'-f {ops}/dev-fold-20261112/compose.fold.yml "$@"\n')
    assert '[ -f "$R/config/harness.yml" ]' in compose and '-f "$R/config/base.yml"' in compose
    assert F.classify_wrapper(compose, "compose", str(rig.R))["side"] == []
    # the update tool's own wrapper checks
    for w, ov in (("compose", "beta-dev-20261106/compose.beta.yml"), ("mc", "beta-dev-20261106/mc.beta.yml")):
        assert rig.wrapper(w).read_text().count(f"-f {ops}/{ov} ") == 1
    after = {w: fake_render(rig.R)(rig.wrapper(w).read_text()) for w in F.WRAPPERS}
    for w in F.WRAPPERS:
        for name, svc in after[w]["services"].items():
            b = dict(before[w]["services"][name])
            a = dict(svc)
            for k in ("image", "pull_policy"):
                a.pop(k, None), b.pop(k, None)
            assert a == b, name
    assert after["compose"]["services"]["cos-agent-a"]["image"] == "minimoi-staging/cos-agent-a:beta-20261110f"
    assert after["mc"]["services"]["mc-agent"]["image"] == "minimoi-staging/mc-agent:beta-20261110f"
    assert after["compose"]["services"]["model-gateway"]["image"] == "minimoi-staging/cos-scheduler:model-gateway-gaps-507"
    assert after["compose"]["services"]["cos-agent-a"]["environment"]["COS_METERED_ROUTES"] == "four-routes"
    assert after["mc"]["services"]["mc-relay"]["environment"]["MC_RELAY_ROUTES"] == "four-routes-relay"


def test_fold_file_carries_kept_settings_only(rig):
    rig.fold(DEFAULT, "keep")
    compose = F.load_layer((rig.FOLD / "compose.fold.yml").read_text())
    mc = F.load_layer((rig.FOLD / "mc.fold.yml").read_text())
    agent = compose["services"]["cos-agent-a"]
    assert isinstance(agent["volumes"], F.Tagged) and agent["volumes"].tag == "!override"
    targets = [F.volume_target(v) for v in agent["volumes"].value]
    assert targets == ["/home/node/.openclaw", "/run/cos-key", "/minimoi/private/professional"]     # cos-actions and cos-board mounts were replaced by inc1
    assert "image" not in agent and "pull_policy" not in agent
    assert compose["services"]["model-gateway"] == {"image": "minimoi-staging/cos-scheduler:model-gateway-gaps-507"}
    assert compose["networks"] == {"partnership": {"name": "minimoi-partnership"}}
    assert compose["services"]["partnership-tasks"]["container_name"] == "minimoi-partnership-tasks"
    # inc2's MC values come before routes, which wins today: they stay out of a fold that sits after routes
    assert "MC_MODEL_GATEWAY_KEY" not in mc["services"]["mc-agent"]["environment"]
    assert mc["services"]["mc-relay"]["environment"]["MC_RELAY_ROUTES"] == "four-routes-relay"
    assert mc["services"]["mc-agent"]["environment"]["MC_PLAN_MODEL"] == "openai/x"
    for name in ("compose.fold.yml", "mc.fold.yml"):
        assert stat.S_IMODE((rig.FOLD / name).stat().st_mode) == 0o600


def test_dropped_md_records_every_layer_by_name(rig):
    rig.fold(DEFAULT, "keep")
    text = (rig.FOLD / "DROPPED.md").read_text()
    for ref in ("minimoi12-20261031-v6/compose.sh.images.json", "mc-persona-0131-20261111b/overlay.mc.json", "work-modes-notices-v1-20261111/mc.sh.images.json",
                "partnership-inc1-20261105/compose.partnership.yml"):
        assert f"## {ref}" in text
    assert "`cos-agent-a:volume:/run/secrets/cos-board-profile.json`: replaced" in text
    assert "`mc-agent:env:MC_MODEL_GATEWAY_KEY`: shadowed" in text
    assert "`cos-agent-a:env:COS_METERED_ROUTES`: carried" in text
    assert "superseded by `beta-dev-20261106/compose.beta.yml`" in text


def test_metered_routes_decision_drops_depth_v7(rig):
    decisions = {**DEFAULT, "metered": "routes"}
    rig.fold(decisions, "keep")
    after = fake_render(rig.R)(rig.wrapper("compose").read_text())
    assert after["services"]["cos-agent-a"]["environment"]["COS_METERED_ROUTES"] == "three-routes"
    assert "`cos-agent-a:env:COS_METERED_ROUTES`: dropped (decision)" in (rig.FOLD / "DROPPED.md").read_text()


def test_gateway_base_lets_only_the_gateway_image_move(rig):
    rig.fold(DEFAULT, "base")
    assert fake_render(rig.R)(rig.wrapper("compose").read_text())["services"]["model-gateway"]["image"] == "minimoi-staging/cos-scheduler:model-gateway-base"


def test_a_render_that_moves_a_value_refuses_before_any_wrapper_changes(rig, capsys):
    real = fake_render(rig.R)

    def drifting(text, stdin=None):
        out = real(text, stdin)
        if stdin is not None:
            out["services"]["cos-scheduler"]["environment"]["COS_STORE_ENVIRONMENT"] = f"{SECRET}-moved"
        return out
    rig._render_fn = drifting
    before = _snapshot(rig.R)
    with pytest.raises(F.Refused, match=r"cos-scheduler would change beyond its image, at: /environment/COS_STORE_ENVIRONMENT"):
        rig.fold(DEFAULT, "keep")
    assert _snapshot(rig.R) == before and not rig.FOLD.exists()          # refused before anything was written
    assert not (rig.FOLD / "compose.fold.yml").exists() and not (rig.FOLD / "mc.fold.yml").exists()
    assert SECRET not in capsys.readouterr().out


def test_a_live_render_that_fails_restores_the_wrappers(rig):
    real = fake_render(rig.R)

    def late(text, stdin=None):
        out = real(text, stdin)
        if stdin is None and "compose.fold.yml" in text:
            out["services"]["portal"]["environment"]["MINIMOI_ENV"] = "moved"
        return out
    rig._render_fn = late
    originals = {w: rig.wrapper(w).read_bytes() for w in F.WRAPPERS}
    with pytest.raises(F.Refused):
        rig.fold(DEFAULT, "keep")
    assert {w: rig.wrapper(w).read_bytes() for w in F.WRAPPERS} == originals
    assert json.loads((rig.FOLD / "state.private.json").read_text())["status"] == "restored_after_failure"
    assert not (rig.FOLD / "compose.fold.yml").exists()


def test_check_equivalent_names_fields_never_values():
    before = {"services": {"a": {"image": "x", "environment": {"K": f"{SECRET}-1"}}}}
    after = {"services": {"a": {"image": "y", "environment": {"K": f"{SECRET}-2"}}}}
    with pytest.raises(F.Refused) as exc:
        F.check_equivalent(before, after, {"a": "y"})
    assert "/environment/K" in str(exc.value) and SECRET not in str(exc.value)
    with pytest.raises(F.Refused, match="beta override's image"):
        F.check_equivalent(before, {"services": {"a": {"image": "z", "environment": {"K": f"{SECRET}-1"}}}}, {"a": "y"})
    with pytest.raises(F.Refused, match="nothing decided"):
        F.check_equivalent(before, {"services": {"a": {"image": "z", "environment": {"K": f"{SECRET}-1"}}}}, {})
    assert F.check_equivalent(before, after, {"a": "y"}, dropped=["a:env:K"])["images"] == {"a": "beta override"}


# ── Values never printed ──

def test_values_never_printed_or_recorded(rig, capsys):
    rig.plan(DEFAULT)
    rig.fold(DEFAULT, "keep")
    rig.rollback()
    out = capsys.readouterr().out
    assert SECRET not in out and "four-routes" not in out and "openai/x" not in out
    for name in ("plan.json", "journal.jsonl", "state.private.json"):
        assert SECRET not in (rig.FOLD / name).read_text(), name


def test_dropped_md_and_plan_json_hold_no_values(rig):
    rig.fold(DEFAULT, "keep")
    for name in ("DROPPED.md", "plan.json"):
        text = (rig.FOLD / name).read_text()
        assert SECRET not in text and "four-routes" not in text and "/srv/" not in text, name


# ── Backup and rollback ──

def test_backup_is_private_and_rollback_restores_exactly(rig):
    originals = {w: (rig.wrapper(w).read_bytes(), stat.S_IMODE(rig.wrapper(w).stat().st_mode)) for w in F.WRAPPERS}
    layers_before = {k: v for k, v in _snapshot(rig.R).items() if k.startswith("operators/") and "dev-fold" not in k}
    result = rig.fold(DEFAULT, "keep")
    backup = Path(result["backup"])
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    for p in backup.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    assert (backup / "wrapper.compose.private").read_bytes() == originals["compose"][0]
    assert {w: stat.S_IMODE(rig.wrapper(w).stat().st_mode) for w in F.WRAPPERS} == {w: m for w, (_, m) in originals.items()}
    rig.rollback()
    assert {w: (rig.wrapper(w).read_bytes(), stat.S_IMODE(rig.wrapper(w).stat().st_mode)) for w in F.WRAPPERS} == originals
    assert not (rig.FOLD / "compose.fold.yml").exists() and not (rig.FOLD / "mc.fold.yml").exists()
    assert {k: v for k, v in _snapshot(rig.R).items() if k.startswith("operators/") and "dev-fold" not in k} == layers_before   # no layer file touched
    assert json.loads((rig.FOLD / "state.private.json").read_text())["status"] == "rolled_back"


def test_rollback_refuses_when_a_wrapper_changed_since(rig):
    rig.fold(DEFAULT, "keep")
    path = rig.wrapper("mc")
    path.write_text(path.read_text() + "# hand edit\n")
    with pytest.raises(F.Refused, match="changed since the fold"):
        rig.rollback()


def test_rollback_without_a_fold_refuses(rig):
    with pytest.raises(F.Refused, match="nothing to roll back"):
        rig.rollback()


# ── Idempotence ──

def test_second_fold_is_a_no_op(rig, capsys):
    rig.fold(DEFAULT, "keep")
    snap = _snapshot(rig.R)
    assert rig.fold(DEFAULT, "keep") == {"status": "already_folded"}
    assert _snapshot(rig.R) == snap
    assert "already folded" in capsys.readouterr().out


def test_fold_after_rollback_works_again(rig):
    rig.fold(DEFAULT, "keep")
    rig.rollback()
    assert rig.fold(DEFAULT, "keep")["status"] == "folded"
    assert rig.rollback()["status"] == "rolled_back"
    with pytest.raises(F.Refused, match="not folded"):
        rig.rollback()


def test_a_new_side_layer_beside_a_fold_refuses(rig):
    rig.fold(DEFAULT, "keep")
    _w(rig.OPS / "cos-page-20261111/overlay.json", {"services": {"cos-scheduler": {"image": "minimoi-staging/cos-scheduler:page"}}})
    path = rig.wrapper("compose")
    path.write_text(path.read_text().replace(' "$@"', f' -f {rig.OPS}/cos-page-20261111/overlay.json "$@"'))
    with pytest.raises(F.Refused, match="earlier fold"):
        rig.fold(DEFAULT, "keep")


# ── Guards ──

def test_refuses_production_root_and_paths(tmp_path):
    with pytest.raises(F.Refused, match="production"):
        F.Host(root="/opt/minimoi")
    with pytest.raises(F.Refused, match="production"):
        F.refuse_production_text("-f /opt/minimoi/docker-compose.yml", "x")
    F.refuse_production_text("REL=/opt/minimoi-staging-src/release", "x")


def test_recovery_or_update_in_flight_refuses(rig):
    (rig.OPS / "beta-dev-20261106/recovery-required.json").write_text("{}")
    with pytest.raises(F.Refused, match="recovery"):
        rig.fold(DEFAULT, "keep")
    (rig.OPS / "beta-dev-20261106/recovery-required.json").unlink()
    (rig.OPS / "beta-dev-20261106/update-in-flight.json").write_text("{}")
    with pytest.raises(F.Refused, match="in flight"):
        rig.fold(DEFAULT, "keep")


def test_host_script_carries_this_exact_file(tmp_path):
    script = F.host_script(["plan", "--depth-proof", "on"])
    source = (ROOT / "scripts/staging/dev_layer_fold.py").read_bytes()
    blob = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64 -d", script).group(1)
    assert gzip.decompress(base64.b64decode(blob)) == source
    assert sha(source) in script and script.rstrip().endswith("plan --depth-proof on")
    with pytest.raises(F.Refused):
        F.host_script(["plan", "$(rm -rf /)"])


def test_keep_list_matches_the_catalog_groups():
    assert [g["id"] for g in F.GROUPS] == list(range(1, 15))
    assert all(g["layers"] and g["settings"] for g in F.GROUPS)


def test_plan_prove_renders_the_candidate_without_writing(rig, capsys):
    snap = _snapshot(rig.R)
    plan = rig.plan(DEFAULT, prove_gateway="keep")
    assert plan["proofs"]["compose"]["images"]["cos-agent-a"] == "beta override"
    assert plan["proofs"]["compose"]["images"]["model-gateway"] == "unchanged"
    assert plan["proofs"]["running_not_as_compose_after_fold"] == {"minimoi-portal": {
        "wrapper": "compose", "service": "portal", "running": "minimoi-staging/portal:depth-a8255964", "compose_after_fold": "minimoi-staging/portal:beta-20261110g"}}
    assert _snapshot(rig.R) == snap and not rig.FOLD.exists()
    assert SECRET not in capsys.readouterr().out
