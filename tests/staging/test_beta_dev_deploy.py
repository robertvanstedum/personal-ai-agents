"""The combined 1.2 candidate's Dev deploy: order, failure handling, payload proof and refusals. Synthetic: no Docker, no host, no AWS."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/staging/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gs, op, pay = _load("guarded_swap"), _load("beta_dev_deploy"), _load("beta_dev_payload")
sha = lambda b: hashlib.sha256(b).hexdigest()


class Boom(Exception):
    pass


class Host:
    def __init__(self, lose_gate_in=None, **fail):
        self.fail, self.calls, self.records, self.alive, self.lose = fail, [], {}, True, lose_gate_in

    def __getattr__(self, name):
        def step(*args):
            self.calls.append((name, args[0]) if name == "recreate" else name)
            if self.lose == name:
                self.alive = False
            if self.fail.get(name):
                raise Boom(name)
            if name == "plan":
                return {"changed": "nothing live"}
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


LIVE = {"stop_workers", "start_workers", "wait_idle", "gate_open", "write_new", "create_workers", "save_before", "verify_new", "restore_verified"}


def deploy(host):
    return op.Deploy(host, gs.guarded_swap)


def names(host):
    return {c if isinstance(c, str) else c[0] for c in host.calls}


# ── Order ──

def test_a_dry_run_changes_nothing_live_and_builds_before_saying_so():
    host = Host()
    assert deploy(host).dry_run() == {"changed": "nothing live"}
    assert not (LIVE | {"recreate"}) & names(host)
    c = host.calls
    assert c.index("check_baseline") < c.index("check_disk") < c.index("build_images") < c.index("check_new_images") < c.index("clean_build") < c.index("check_render") < c.index("plan")


@pytest.mark.parametrize("step", ["check_baseline", "check_disk", "build_images", "check_new_images", "check_render", "save_before"])
def test_a_build_or_check_failure_swaps_nothing(step):
    host = Host(**{step: True})
    with pytest.raises(Boom, match=step):
        deploy(host).apply()
    assert not {"stop_workers", "write_new", "recreate", "gate_open", "create_workers"} & names(host)


@pytest.mark.parametrize("step", ["build_images", "check_new_images"])
def test_a_failed_build_still_cleans_its_context(step):
    host = Host(**{step: True})
    with pytest.raises(Boom):
        deploy(host).dry_run()
    assert "clean_build" in host.calls


def test_every_overlay_is_built_and_checked_before_a_worker_stops():
    host = Host()
    assert deploy(host).apply() is gs.Outcome.APPLIED
    c = host.calls
    assert c.index("build_images") < c.index("check_new_images") < c.index("save_before") < c.index("stop_workers") < c.index("wait_idle") < c.index("gate_open") < c.index("write_new")


def test_a_good_apply_recreates_low_risk_first_mc_last_and_verifies_before_workers_resume():
    host = Host()
    deploy(host).apply()
    c = host.calls
    tiers = [x[1] for x in c if isinstance(x, tuple)]
    assert tiers == sorted(tiers) == [1, 2, 3, 4, 5, 6]
    by_tier = {t["container"]: t["tier"] for t in op.TARGETS.values()}
    assert by_tier["minimoi-model-gateway"] < by_tier["minimoi-cos-scheduler"] < by_tier["minimoi-cos-agent-a"] < by_tier["minimoi-portal"] < by_tier["minimoi-mc-agent"]
    assert max(tiers) == by_tier["minimoi-mc-agent"]
    assert c.index(("recreate", 6)) < c.index("create_workers") < c.index("verify_new") < c.index("gate_close") < c.index("start_workers") < c.index("verify_workers")
    assert "restore_verified" not in c and host.records["result"] == {"status": "deployed_verified"}


@pytest.mark.parametrize("step", ["write_new", "recreate", "create_workers", "verify_new"])
def test_a_failed_apply_restores_and_proves_then_resumes(step):
    host = Host(**{step: True})
    with pytest.raises(Boom, match=step):
        deploy(host).apply()
    c = host.calls
    assert c.index("restore_verified") < c.index("start_workers") and host.records["outcome"] == {"outcome": "restored"}
    assert "verify_workers" not in c and "result" not in host.records


def test_a_failed_restore_leaves_workers_stopped_with_recovery_required():
    host = Host(verify_new=True, restore_verified=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "start_workers" not in host.calls and host.records["recovery_required"] == {"workers": "left stopped"}


def test_a_lost_gate_never_restores_without_it_and_leaves_workers_stopped():
    host = Host(lose_gate_in="recreate", recreate=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "restore_verified" not in host.calls and "start_workers" not in host.calls


def test_a_worker_that_fails_after_its_start_rolls_everything_back():
    host = Host(verify_workers=True)
    with pytest.raises(Boom, match="verify_workers"):
        deploy(host).apply()
    c = host.calls
    assert c.index("verify_workers") < c.index("check_saved") < c.index("restore_verified") and "workers_failed_verification" in host.records
    assert host.records["result"] == {"status": "rolled_back_verified"}


def test_a_worker_failure_whose_rollback_fails_leaves_workers_stopped():
    host = Host(verify_workers=True, restore_verified=True)
    with pytest.raises(Boom, match="verify_workers"):
        deploy(host).apply()
    assert "recovery_required" in host.records
    assert host.calls[-1] != "start_workers" and host.calls.count("start_workers") == 1      # only the one before the failed verification


def test_rollback_needs_saved_state_proves_itself_and_a_failed_one_leaves_workers_stopped():
    host = Host(check_saved=True)
    with pytest.raises(Boom):
        deploy(host).rollback()
    assert "stop_workers" not in host.calls
    host = Host()
    assert deploy(host).rollback() is gs.Outcome.APPLIED
    c = host.calls
    assert c.index("stop_workers") < c.index("restore_verified") < c.index("start_workers") < c.index("verify_restored_workers")
    host = Host(restore_verified=True)
    with pytest.raises(Boom):
        deploy(host).rollback()
    assert "start_workers" not in host.calls and "recovery_required" in host.records


def test_a_turn_still_live_refuses_with_workers_back_and_nothing_written():
    host = Host(wait_idle=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "write_new" not in host.calls and host.calls[-1] == "start_workers" and host.records["outcome"] == {"outcome": "not_started"}


# ── Refusing production ──

@pytest.mark.parametrize("path", ["/opt/minimoi", "/opt/minimoi/", "/opt/minimoi/.env", "/opt/minimoi/operators/x", "/opt/minimoi-staging/../minimoi/.env"])
def test_production_paths_are_refused(path):
    with pytest.raises(op.Refused, match="production"):
        op.refuse_production(path)


@pytest.mark.parametrize("path", ["/opt/minimoi-staging", "/opt/minimoi-staging/operators/beta-dev-20261106", "/opt/minimoi-staging-src/release"])
def test_dev_paths_are_not_mistaken_for_production(path):
    op.refuse_production(path)


def test_the_host_operator_cannot_be_rooted_in_production():
    with pytest.raises(op.Refused, match="production"):
        op.RealHost(root="/opt/minimoi")


def test_a_wrapper_naming_production_is_refused():
    op.refuse_production_text("R=/opt/minimoi-staging; REL=/opt/minimoi-staging-src/release\n", "compose.sh")
    with pytest.raises(op.Refused):
        op.refuse_production_text("--env-file /opt/minimoi/.env\n", "compose.sh")


def test_a_rendered_mount_or_env_file_from_production_is_refused_for_a_recreated_service():
    good = {"services": {"portal": {"volumes": [{"source": "/opt/minimoi-staging/data"}]}, "other": {"volumes": [{"source": "/opt/minimoi/data"}]}}}
    op.refuse_production_sources(good, ["portal"])
    with pytest.raises(op.Refused):
        op.refuse_production_sources(good, ["other"])
    with pytest.raises(op.Refused):
        op.refuse_production_sources({"services": {"p": {"env_file": ["/opt/minimoi/.env"]}}}, ["p"])


@pytest.mark.parametrize("path", ["/opt/minimoi/operators/beta", "/opt/minimoi-staging/config", "/tmp/x"])
def test_the_payload_will_only_go_to_devs_operator_folder(path):
    with pytest.raises((op.Refused, AssertionError)):
        pay.check_destination(path)
    with pytest.raises((op.Refused, AssertionError)):
        pay.chunk_scripts(b"x", "0" * 64, host_dir=path)
    with pytest.raises((op.Refused, AssertionError)):
        pay.start_script("dry-run", host_dir=path)


def test_nothing_in_the_operator_writes_a_production_value():
    source = (ROOT / "scripts/staging/beta_dev_deploy.py").read_text()
    assert "minimoi/.env" not in source and "PARTNERSHIP_ENVIRONMENT" not in source and "production_internal" not in source
    assert "authority." not in source and "mc-policy" not in source       # neither authority files nor gates are written


# ── Payload proof ──

def test_the_release_tarball_hash_is_checked():
    op.check_release(b"tar", sha(b"tar"))
    for tar, recorded in ((b"other", sha(b"tar")), (b"tar", "")):
        with pytest.raises(AssertionError, match="differs from its recorded"):
            op.check_release(tar, recorded)


def test_a_payload_file_that_is_not_what_was_reviewed_is_refused():
    files = {"a.py": {"after": sha(b"reviewed")}}
    op.check_payload(files, lambda _: b"reviewed")
    with pytest.raises(AssertionError, match="differs from what was reviewed"):
        op.check_payload(files, lambda _: b"swapped")
    with pytest.raises(AssertionError, match="empty"):
        op.check_payload({}, lambda _: b"")


def _m(dest="/app/a.py", after="n", accepted=("o",), base="o", **more):
    return {"dest": dest, "after": after, "accepted": list(accepted), "base": base, **more}


def test_a_hand_edited_file_is_refused_and_all_are_reported_at_once():
    files = {"a.py": _m(), "b.py": _m(dest="/app/b.py"), "c.py": _m(dest="/app/c.py"), "d.py": _m(dest="/app/d.py")}
    live = {"/app/a.py": "hand edit", "/app/b.py": "o", "/app/d.py": "another edit"}
    bad = op.base_mismatches(files, live)
    assert len(bad) == 2 and bad[0].startswith("/app/a.py") and bad[1].startswith("/app/d.py")        # c.py is absent: this image gains it


def test_a_replaced_folder_and_a_compiled_input_are_not_base_checked():
    files = {"p": _m(dest="/opt/x/persona/AGENTS.md", accepted=()), "s": _m(dest="/opt/y/AGENTS.md", accepted=(), generated_input=True)}
    assert op.base_mismatches(files, {"/opt/x/persona/AGENTS.md": "old", "/opt/y/AGENTS.md": "compiled"}, ["/opt/x/persona/"]) == []


def test_a_file_on_another_committed_version_is_allowed_and_said_aloud():
    files = {"a.py": _m(accepted=("o", "older"))}
    assert op.base_mismatches(files, {"/app/a.py": "older"}) == []
    assert op.not_on_declared_base(files, {"/app/a.py": "older"}) == ["/app/a.py"]
    assert op.not_on_declared_base(files, {"/app/a.py": "o"}) == []


def test_a_tag_that_moved_is_refused():
    op.check_tag_is_running("t", "sha256:a", "sha256:a")
    for tag_id, running in (("sha256:b", "sha256:a"), ("", "")):
        with pytest.raises(AssertionError, match="no longer names"):
            op.check_tag_is_running("t", tag_id, running)


def test_an_overlay_must_be_a_child_with_the_same_config():
    base = {"RootFS": {"Layers": ["a", "b"]}, "Config": {"User": "node", "Env": ["X=1"], "Image": "p", "Labels": {"x": "1"}}}
    good = {"RootFS": {"Layers": ["a", "b", "c"]}, "Config": {"User": "node", "Env": ["X=1"], "Image": "q", "Labels": {"x": "2"}}}
    op.check_overlay(base, good)
    with pytest.raises(AssertionError, match="not a child"):
        op.check_overlay(base, {**good, "RootFS": {"Layers": ["z", "c"]}})
    for change in ({"User": "root"}, {"Env": ["X=2"]}):
        with pytest.raises(AssertionError, match="image config"):
            op.check_overlay(base, {**good, "Config": {**good["Config"], **change}})


def test_a_file_that_differs_in_the_image_or_container_is_refused():
    op.check_files({"/a": "1"}, {"/a": "1", "/b": "2"}, "image")
    for got in ({"/a": "2"}, {}):
        with pytest.raises(AssertionError, match="holds a different file"):
            op.check_files({"/a": "1"}, got, "image")


def test_sha256sum_output_is_parsed_and_a_missing_file_is_just_absent():
    h = "a" * 64
    assert op.parse_sums(f"{h}  /app/x.py\nsha256sum: /app/y.py: No such file\n{h} */app/z\n") == {"/app/x.py": h, "/app/z": h}


def test_a_mounted_path_is_not_read_back_and_the_sample_is_fixed():
    assert op.shadowed("/app/data/x.json", ["/app/data"]) and not op.shadowed("/app/database.py", ["/app/data"])
    many = [f"/app/f{i:03}.py" for i in range(100)]
    picked = op.sample(many)
    assert len(picked) == op.SAMPLE_PER_IMAGE and picked == op.sample(reversed(many))
    assert op.sample(["/app/a", "/app/data/b"], ["/app/data"]) == ["/app/a"]


def test_a_compiled_input_is_not_expected_in_the_built_image():
    assert op.want_hashes({"s": _m(dest="/x", generated_input=True), "t": _m(dest="/y", after="h")}) == {"/y": "h"}


# ── Dev's settings stay ──

def _render():
    return {"name": "minimoi-staging", "networks": {"n": {}},
            "services": {"portal": {"image": "p:old", "environment": {"K": "1"}}, "mc": {"image": "m:old", "pull_policy": "never"}, "other": {"image": "o"}}}


def test_the_render_check_accepts_only_the_declared_images():
    after = copy.deepcopy(_render())
    after["services"]["portal"].update(image="p:new", pull_policy="never")
    op.only_images(_render(), after, {"portal": "p:new"})


@pytest.mark.parametrize("mutate", [
    lambda d: d["services"]["other"].update(image="x"),
    lambda d: d["services"]["portal"]["environment"].update(K="2"),
    lambda d: d["services"]["portal"].update(image="p:wrong"),
    lambda d: d["services"]["portal"].update(pull_policy="always"),
    lambda d: d["services"].update(extra={}),
    lambda d: d.update(networks={}),
    lambda d: d["services"]["mc"].update(pull_policy="always"),
])
def test_the_render_check_refuses_anything_more(mutate):
    after = copy.deepcopy(_render())
    after["services"]["portal"].update(image="p:new", pull_policy="never")
    mutate(after)
    with pytest.raises((AssertionError, KeyError)):
        op.only_images(_render(), after, {"portal": "p:new"})


def test_the_override_holds_only_image_and_pull_policy():
    assert op.override_doc({"portal": "p:new"}) == {"services": {"portal": {"image": "p:new", "pull_policy": "never"}}}


def test_the_wrapper_gains_one_last_file_and_gives_it_back_byte_for_byte():
    body = 'exec docker compose -p x -f a.yml ${H[@]+"${H[@]}"} "$@"\n'
    new = op.with_override(body, "/o.yml")
    assert new == 'exec docker compose -p x -f a.yml ${H[@]+"${H[@]}"} -f /o.yml "$@"\n'
    assert op.without_override(new, "/o.yml") == body
    with pytest.raises(AssertionError):
        op.with_override("no args here\n", "/o.yml")


def test_devs_settings_are_named_and_a_changed_one_is_seen():
    env = ["COS_BACKGROUND_JOBS=0", "MINIMOI_MC_TOOLS_ENV=dev", "ROOMS_RECORDS_PROFILE=dev", "HOME=/root", "A=b=c"]
    assert op.dev_values(env) == {"COS_BACKGROUND_JOBS": "0", "MINIMOI_MC_TOOLS_ENV": "dev", "ROOMS_RECORDS_PROFILE": "dev"}
    op.check_required_env("minimoi-cos-scheduler", env)
    op.check_required_env("minimoi-guildhall-cos", env)
    with pytest.raises(AssertionError):
        op.check_required_env("minimoi-cos-scheduler", ["COS_BACKGROUND_JOBS=1"])
    with pytest.raises(AssertionError):
        op.check_required_env("minimoi-guildhall-mc", ["ROOMS_RECORDS_PROFILE=production_internal"])


def test_default_a_is_required_on_both_conversations():
    good = {"roles": {"cos-conversation": {"route": op.A}, "mc-conversation": {"route": op.A}}}
    op.check_catalogue(good)
    wrong = copy.deepcopy(good)
    wrong["roles"]["mc-conversation"]["route"] = "plan-openai-fast"
    with pytest.raises(AssertionError, match="default route"):
        op.check_catalogue(wrong)


def test_a_restored_state_must_equal_the_saved_one():
    op.check_restored({"a": (1, 2)}, {"a": [1, 2]}, "contract")
    with pytest.raises(AssertionError, match="restored contract"):
        op.check_restored({"a": 1}, {"a": 2}, "contract")
    saved = {"agents": {"x": 1}, "meta": {"t": "a"}}
    op.same_runtime(saved, {**saved, "meta": {"t": "b"}})
    with pytest.raises(AssertionError):
        op.same_runtime(saved, {"agents": {"x": 2}, "meta": {}})


def _policy():
    return {"agents": {"entries": {"mc-chat": {"tools": {"deny": ["group:runtime"], "fs": {"workspaceOnly": True}}}, "mc-agent": {"tools": {"deny": []}}}},
            "auth": {"profiles": {"p": {"mode": "oauth"}}}}


def test_the_mc_policy_check_refuses_any_loosening():
    op.check_mc_policy(_policy())
    for mutate in (lambda c: c["agents"]["entries"]["mc-chat"]["tools"].update(deny=[]), lambda c: c["auth"]["profiles"]["p"].update(mode="api_key"),
                   lambda c: c.update(models={"providers": {"x": {}}})):
        wrong = _policy()
        mutate(wrong)
        with pytest.raises(AssertionError):
            op.check_mc_policy(wrong)


# ── Disk ──

def test_under_two_gigabytes_free_is_refused():
    op.check_free(2 * 1024 ** 3, "/var/lib/docker")
    with pytest.raises(op.Refused, match="2 GB"):
        op.check_free(2 * 1024 ** 3 - 1, "/var/lib/docker")


# ── What goes where ──

CHANGED = """contracts/beta-ui/approval_and_needs_v0.1.md
contracts/partnership/v1/command.schema.json
core/mc_tools/tasks_adapter.py
core/realtime_voice/static/realtime-voice-controller.js
core/telegram/telegram_bot.py
docker-compose.minimoi-1-2-production.yml
docker/cos-agent-a/apply-config.sh
docker/cos-agent-a/plan-access.cjs
docker/cos-agent-a/plugins/cos-bound/index.mjs
docker/mc-agent-container/README.md
docker/mc-agent-container/handbook.mjs
docker/mc-agent-container/start.sh
docker/mc-agent/relay_v2.mjs
docs/runbooks/model_tiers.md
domains/cos/chief_of_staff.py
domains/curator/curator_rss_v2.py
domains/german/german_domain.py
domains/portuguese/html_server.py
minimoi_portal/app.py
scripts/ops/mc_container_security_test.py
scripts/staging/cos_plan_deploy.py
scripts/vault/dev_cycle.py
services/mc_operations/handbook/manifest.json
services/mc_operations/handbook/v0.1.3/PROJECT.md
services/mc_operations/personas/mc-chat/releases/0.1.34/AGENTS.md
services/model_gateway/litellm.prod.yaml
services/model_gateway/litellm.staging.yaml
services/partnership_tasks/service.py
services/rooms_worker/worker.py
tests/staging/test_cos_plan_deploy.py""".split()


def where(target):
    return op.files_for(op.TARGETS[target], CHANGED)


def test_tests_docs_and_host_tools_ship_in_no_image():
    for key in op.TARGETS:
        for path in where(key):
            assert not path.startswith(("tests/", "docs/", "scripts/staging/", "scripts/vault/", "docker-compose", "contracts/beta-ui")), key + " " + path
            assert not path.endswith("README.md")


def test_each_image_gets_its_own_paths():
    assert where("portal")["minimoi_portal/app.py"] == "/app/minimoi_portal/app.py"
    assert "domains/german/german_domain.py" not in where("portal") and "core/telegram/telegram_bot.py" not in where("portal")
    shared = {"core/mc_tools/tasks_adapter.py", "core/realtime_voice/static/realtime-voice-controller.js", "core/telegram/telegram_bot.py",
              "contracts/partnership/v1/command.schema.json", "services/partnership_tasks/service.py"}     # the domain images hold the whole app, so the shared code goes too
    assert where("german")["domains/german/german_domain.py"] == "/app/domains/german/german_domain.py"
    assert set(where("german")) == shared | {"domains/german/german_domain.py"}
    assert set(where("portuguese")) == shared | {"domains/portuguese/html_server.py"}
    assert set(where("curator")) == shared | {"domains/curator/curator_rss_v2.py"}
    assert where("mc-relay") == {"docker/mc-agent/relay_v2.mjs": "/opt/minimoi/mc-agent/relay_v2.mjs"}
    assert set(where("partnership-tasks")) == {"contracts/partnership/v1/command.schema.json", "services/partnership_tasks/service.py"} == set(where("partnership-worker"))
    assert set(where("guildhall-cos")) == {"services/rooms_worker/worker.py"} == set(where("guildhall-mc"))
    assert where("model-gateway") == {"services/model_gateway/litellm.staging.yaml": "@config/litellm.staging.yaml"}
    assert "domains/cos/chief_of_staff.py" in where("cos-scheduler") and "core/telegram/telegram_bot.py" not in where("cos-scheduler")


def test_mc_lays_out_its_persona_handbook_and_release_tools():
    mc = where("mc-agent")
    assert mc["docker/mc-agent-container/handbook.mjs"] == op.MC_DIR + "handbook.mjs"
    assert mc["services/mc_operations/personas/mc-chat/releases/0.1.34/AGENTS.md"] == op.MC_DIR + "persona/AGENTS.md"
    assert mc["services/mc_operations/handbook/manifest.json"] == op.MC_DIR + "handbook/manifest.json"
    assert mc["services/mc_operations/handbook/v0.1.3/PROJECT.md"] == op.MC_DIR + "handbook/v0.1.3/PROJECT.md"
    assert mc["scripts/ops/mc_container_security_test.py"] == "/opt/minimoi/mc-release/mc_container_security_test.py"


def test_cos_agent_gets_its_config_plugin_and_handbook_plus_the_source_its_build_needs():
    cos = where("cos-agent-a")
    assert cos["docker/cos-agent-a/apply-config.sh"] == op.COS_DIR + "apply-config.sh"
    assert cos["docker/cos-agent-a/plugins/cos-bound/index.mjs"] == op.PLUGINS + "cos-bound/index.mjs"
    assert cos["services/mc_operations/handbook/v0.1.3/PROJECT.md"] == op.COS_DIR + "handbook/v0.1.3/PROJECT.md"
    assert cos["docker/cos-agent-a/AGENTS.md"] == op.COS_DIR + "AGENTS.md"


def test_an_image_with_nothing_changed_is_skipped():
    for key in ("records", "cos-board", "supervisor"):
        assert where(key) == {}, key
    meta, _ = pay.build_meta(_FakeGit(CHANGED), "head", {"portal": "b", "cos": "b", "domains": "b", "records": "b", "board": "b"})
    assert set(meta["skipped"]) == {"minimoi-records", "minimoi-cos-board", "minimoi-supervisor"}
    assert all(why == "nothing it runs changed" for why in meta["skipped"].values())


def test_an_overlay_builds_on_the_exact_id_and_never_a_tag():
    text = op.dockerfile_for(op.TARGETS["german"], "sha256:abc", "")
    assert text == "FROM sha256:abc\nCOPY ctx/ /\n"
    assert op.dockerfile_for(op.TARGETS["portal"], "sha256:abc", "") == "FROM sha256:abc\nCOPY ctx/ /\nENV MINIMOI_COS_TO_MC=1\n"
    with pytest.raises(AssertionError):
        op.dockerfile_for(op.TARGETS["portal"], "minimoi-staging/portal:route-roles-20261105g", "")


def test_mcs_overlay_replaces_persona_and_handbook_runs_check_6c_and_restores_its_user():
    text = op.dockerfile_for(op.TARGETS["mc-agent"], "sha256:abc", "node")
    lines = text.splitlines()
    assert lines[0] == "FROM sha256:abc" and lines[1] == "USER root" and lines[-1] == "USER node"
    assert lines.index(f"RUN rm -rf {op.MC_DIR}persona {op.MC_DIR}handbook") < lines.index("COPY ctx/ /")
    assert any(".beta-check-6c.sh" in x and "chmod 0755" in x and "rm -f" in x for x in lines)
    assert "minimoi.persona=0.1.34" in text and "minimoi.handbook=0.1.3" in text
    assert "USER" not in op.dockerfile_for(op.TARGETS["mc-agent"], "sha256:abc", "")        # an image with no user set keeps none


def test_cos_overlay_reruns_role_then_handbook_as_its_dockerfile_does():
    lines = op.dockerfile_for(op.TARGETS["cos-agent-a"], "sha256:abc", "node").splitlines()
    role = next(i for i, x in enumerate(lines) if "build-mc-role.mjs" in x)
    handbook = next(i for i, x in enumerate(lines) if "build-mc-handbook.mjs" in x)
    assert lines.index("COPY ctx/ /") < role < handbook < lines.index("USER node")
    dockerfile = (ROOT / "docker/Dockerfile.cos-agent-a").read_text()
    for step in (lines[role], lines[handbook]):
        assert step in dockerfile, step


def test_mc_overlay_matches_its_dockerfile_on_persona_and_handbook():
    dockerfile = (ROOT / "docker/mc-agent-container/Dockerfile").read_text()
    assert "services/mc_operations/personas/mc-chat/releases/0.1.34/ /opt/minimoi/mc-agent/persona/" in dockerfile
    assert "services/mc_operations/handbook/manifest.json /opt/minimoi/mc-agent/handbook/manifest.json" in dockerfile
    assert "services/mc_operations/handbook/v0.1.3/ /opt/minimoi/mc-agent/handbook/v0.1.3/" in dockerfile
    assert "minimoi.persona=0.1.34" in dockerfile and "minimoi.handbook=0.1.3" in dockerfile
    assert op.PERSONA == "services/mc_operations/personas/mc-chat/releases/0.1.34/"


def test_a_build_context_has_exact_modes_and_open_folders(tmp_path):
    payload = tmp_path / "payload"
    (payload / "a").mkdir(parents=True)
    (payload / "a/x.py").write_text("x\n")
    (payload / "a/run.sh").write_text("run\n")
    old = os.umask(0o077)
    try:
        ctx = tmp_path / "ctx"
        (ctx / "ctx").mkdir(parents=True)
        op.write_context(ctx, {"a/x.py": {"dest": "/app/a/x.py", "mode": "644"}, "a/run.sh": {"dest": "/opt/m/run.sh", "mode": "755"}}, payload, {"/opt/m/.check.sh": "check\n"})
    finally:
        os.umask(old)
    assert stat.S_IMODE((ctx / "ctx/app/a/x.py").stat().st_mode) == 0o644 and stat.S_IMODE((ctx / "ctx/opt/m/run.sh").stat().st_mode) == 0o755
    assert (ctx / "ctx/opt/m/.check.sh").read_text() == "check\n"
    assert stat.S_IMODE((ctx / "ctx/app/a").stat().st_mode) == 0o755 and stat.S_IMODE((ctx / "ctx").stat().st_mode) == 0o755


# ── The checks that run inside an image, run here on a folder ──

def _tree(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def _imports(root, *files):
    out = subprocess.run([sys.executable, "-c", op.IMPORT_CHECK, str(root), *[str(root / f) for f in files]], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_import_check_finds_a_module_or_name_the_image_lacks(tmp_path):
    root = _tree(tmp_path, {"core/__init__.py": "", "core/old.py": "def have(): pass\nX = 1\n", "domains/a.py": "\n".join([
        "import os", "import core.old", "from core.old import have, X", "from core.old import missing", "from core.gone import y", "import core.absent",
        "try:\n    from core.optional import z\nexcept ImportError:\n    z = None", "def later():\n    from core.lazy import q", "from .sibling import s"]), "domains/sibling.py": "s = 1\n"})
    assert _imports(root, "domains/a.py") == ["domains/a.py: core.absent", "domains/a.py: core.gone", "domains/a.py: core.lazy", "domains/a.py: core.old.missing"]


def test_the_layout_check_reports_what_differs(tmp_path):
    (tmp_path / "same").write_bytes(b"same")
    (tmp_path / "old").write_bytes(b"old")
    want = {str(tmp_path / "same"): sha(b"same"), str(tmp_path / "old"): sha(b"new"), str(tmp_path / "gone"): sha(b"x")}
    out = subprocess.run([sys.executable, "-c", op.LAYOUT_CHECK], input=json.dumps(want), capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"differs": [str(tmp_path / "old")], "missing": [str(tmp_path / "gone")]}


NODE = shutil.which("node")


def _mc_dir(tmp_path, manifest=None):
    """The MC layout from the repo, with start.sh pointed at the folder (the image's own path is fixed)."""
    d = tmp_path / "mc"
    (d / "handbook").mkdir(parents=True)
    (d / "persona").mkdir()
    for name in ("start.sh", "model-access.cjs", "handbook.mjs", "openclaw.json"):
        shutil.copy(ROOT / "docker/mc-agent-container" / name, d / name)
    shutil.copytree(ROOT / "services/mc_operations/personas/mc-chat/releases/0.1.34", d / "persona", dirs_exist_ok=True)
    shutil.copy(ROOT / "services/mc_operations/handbook/manifest.json", d / "handbook/manifest.json")
    shutil.copytree(ROOT / "services/mc_operations/handbook/v0.1.3", d / "handbook/v0.1.3")
    start = (d / "start.sh").read_text().replace("IMAGE_DIR=/opt/minimoi/mc-agent", f"IMAGE_DIR={d}")
    (d / "start.sh").write_text(start)
    if manifest is not None:
        (d / "handbook/manifest.json").write_text(manifest)
    (tmp_path / "check.sh").write_text(op.CHECK_6C)
    return d


def _clean_env(d):
    # CHECK_6C counts the projection with its `mktemp -d` path in the handbook block. In the image build (Linux) that is
    # /tmp/tmp.XXXXXXXXXX, shorter than the container's /home/node/.openclaw/workspace-mc; macOS ignores TMPDIR and gives a
    # /var/folders/... path long enough to make a release within budget look ~230 characters over. Measure as Linux does.
    shim = d.parent / "bin"
    shim.mkdir(exist_ok=True)
    real = shutil.which("mktemp")
    (shim / "mktemp").write_text(f'#!/bin/sh\n[ "$*" = "-d" ] && exec {real} -d /tmp/tmp.XXXXXXXXXX\nexec {real} "$@"\n')
    (shim / "mktemp").chmod(0o755)
    return {"PATH": f"{shim}:{os.environ['PATH']}", "HOME": os.environ.get("HOME", "/tmp"), "BETA_MC_DIR": str(d)}


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_check_6c_passes_on_the_reviewed_handbook_and_leaves_it_untouched(tmp_path):
    d = _mc_dir(tmp_path)
    before = (d / "handbook/manifest.json").read_bytes()
    out = subprocess.run(["sh", str(tmp_path / "check.sh")], capture_output=True, text=True, env=_clean_env(d))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "check 6c passed" in out.stdout and (d / "handbook/manifest.json").read_bytes() == before


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_a_bad_handbook_fails_the_overlay_build(tmp_path):
    d = _mc_dir(tmp_path)
    (d / "handbook/v0.1.3/PROJECT.md").write_text("tampered\n")
    out = subprocess.run(["sh", str(tmp_path / "check.sh")], capture_output=True, text=True, env=_clean_env(d))
    assert out.returncode != 0


ROLE_DOCS = ROOT / "planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents"


@pytest.mark.skipif(not NODE or not ROLE_DOCS.is_dir(), reason="node or the owner's role files not here")
def test_the_cos_build_steps_compile_the_pinned_handbook_from_the_source_agents_file(tmp_path):
    role = tmp_path / "owner-role"
    role.mkdir()
    for name in ("MASTER_CRAFTSMAN_JOB_DESCRIPTION.md", "MASTER_CRAFTSMAN_PERSONA.md", "MASTER_CRAFTSMAN_WORKING_AGREEMENT_2026-10-12.md"):
        shutil.copy(ROLE_DOCS / name, role / name)
    src = ROOT / "docker/cos-agent-a"
    agents, receipt = tmp_path / "AGENTS.md", tmp_path / "ROLE_LOAD.json"
    shutil.copy(src / "AGENTS.md", agents)
    subprocess.run([NODE, str(src / "build-mc-role.mjs"), str(agents), str(role), str(src / "mc-role-lock.json"), str(agents), str(receipt)], check=True, capture_output=True)
    subprocess.run([NODE, str(src / "build-mc-handbook.mjs"), str(agents), str(ROOT / "services/mc_operations/handbook"), str(agents), str(receipt)], check=True, capture_output=True)
    assert "BEGIN_MINIMOI_MC_HANDBOOK 0.1.3" in agents.read_text() and receipt.is_file()


# ── The payload builder ──

class _FakeGit:
    """Every path at 'base' holds old bytes; at 'head' new ones. docker/cos-agent-a/AGENTS.md is unchanged, as in the real tree."""

    def __init__(self, changed, deleted=()):
        self.changed, self.deleted = list(changed), list(deleted)

    def rev(self, ref):
        return {"head": "H" * 40}.get(ref, "B" * 40)

    def status(self, base, commit):
        return [("M", p) for p in self.changed] + [("D", p) for p in self.deleted]

    def blob(self, commit, path):
        if path == "docker/cos-agent-a/AGENTS.md":
            return b"source"
        return (b"new " + path.encode()) if commit == "H" * 40 else (b"old " + path.encode())

    def mode(self, commit, path):
        return "755" if path.endswith("apply-config.sh") else "644"

    def versions(self, since, commit, path):
        return [commit]

    def tree_hashes(self, commit, prefixes):
        return {"minimoi_portal/app.py": "1" * 64, "minimoi_portal/other.py": "2" * 64, "minimoi_portal/data/x.json": "3" * 64, "minimoi_portal/logo.png": "4" * 64}

    def titles(self, since, commit, limit):
        return [("hhhhhhh", "Guild opens your last chat"), ("ggggggg", "Work cards use plain words")][:limit]

    def subject(self, commit):
        return "Guild opens your last chat"

    def count(self, since, commit):
        return 2

    def branches(self, commit):
        return ["claude/beta-final"]


BASES = {"portal": "b", "cos": "b", "domains": "b", "records": "b", "board": "b"}


def test_the_manifest_names_every_hash_the_host_proves():
    meta, blobs = pay.build_meta(_FakeGit(CHANGED), "head", BASES)
    assert meta["commit"] == "H" * 40
    m = meta["targets"]["portal"]["files"]["minimoi_portal/app.py"]
    assert m["dest"] == "/app/minimoi_portal/app.py" and m["after"] == sha(b"new minimoi_portal/app.py") and m["base"] == sha(b"old minimoi_portal/app.py")
    assert sorted(m["accepted"]) == sorted([sha(b"old minimoi_portal/app.py"), sha(b"new minimoi_portal/app.py")])
    assert blobs["minimoi_portal/app.py"] == b"new minimoi_portal/app.py"
    assert meta["targets"]["cos-agent-a"]["files"]["docker/cos-agent-a/AGENTS.md"]["generated_input"] is True
    assert meta["targets"]["cos-agent-a"]["files"]["docker/cos-agent-a/apply-config.sh"]["mode"] == "755"
    assert set(meta["targets"]["portal"]["layout_files"]) == {"/app/minimoi_portal/other.py"}    # not a payload file, not data, not an image


def test_a_deleted_file_an_image_holds_stops_the_build():
    with pytest.raises(AssertionError, match="deleted"):
        pay.build_meta(_FakeGit(["minimoi_portal/app.py"], deleted=["minimoi_portal/gone.py"]), "head", BASES)


def _tar():
    meta, blobs = pay.build_meta(_FakeGit(CHANGED), "head", BASES)
    return meta, blobs, pay.make_tar(meta, blobs, {"beta_dev_deploy.py": b"operator", "guarded_swap.py": b"swap"})


def test_the_tarball_is_reproducible_and_holds_the_payload_at_its_own_hash():
    meta, blobs, tar = _tar()
    assert tar == _tar()[2]
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as t:
        got = t.getnames()
        assert "meta.json" in got and "operator/beta_dev_deploy.py" in got
        for path, m in meta["targets"]["portal"]["files"].items():
            assert sha(t.extractfile("payload/" + path).read()) == m["after"]
        assert t.getmember("payload/docker/cos-agent-a/apply-config.sh").mode == 0o755


def test_every_chunk_stays_under_ssms_limit_and_the_host_verifies_each_part_and_the_whole():
    tar = os.urandom(500_000)
    digest = sha(tar)
    scripts = pay.chunk_scripts(tar, digest)
    assert len(scripts) == 9                                # nine parts; the last, small, carries the unpack too
    for script in scripts:
        assert pay.envelope_size(script) < pay.SSM_LIMIT
        assert "/opt/minimoi-staging/operators/beta-dev-20261106" in script and not re.search(r"/opt/minimoi(?![-\w])", script)
    rebuilt = b""
    for script in scripts:
        b64 = re.search(r"printf %s '([^']+)'", script).group(1)
        data = base64.b64decode(b64)
        rebuilt += data
        assert sha(data) in script and "sha256sum -c" in script
    assert rebuilt == tar
    last = scripts[-1]
    assert digest in last and "sha256sum -c" in last and last.index("sha256sum -c") < last.index("tar -xzf")
    assert "recovery-required.json" in last and "compose.beta.yml" in last


def test_the_real_ssm_envelope_is_what_was_measured():
    script = pay.chunk_scripts(os.urandom(pay.CHUNK_BYTES), "0" * 64)[0]
    assert len(base64.b64encode(gzip.compress(script.encode()))) + 200 == pay.envelope_size(script) < pay.SSM_LIMIT


def test_an_oversized_script_never_reaches_ssm(tmp_path, monkeypatch):
    fake = tmp_path / "ssm_run.sh"
    fake.write_text(f"IID={pay.STAGING_INSTANCE}\n")
    monkeypatch.setattr(pay, "SSM_RUN", fake)
    monkeypatch.setattr(pay.subprocess, "run", lambda *a, **k: pytest.fail("reached SSM"))
    with pytest.raises(AssertionError, match="over SSM"):
        pay.run_on_host("printf %s '" + base64.b64encode(os.urandom(200_000)).decode() + "'")


def test_the_ssm_wrapper_must_aim_at_dev(tmp_path):
    good, bad = tmp_path / "good.sh", tmp_path / "bad.sh"
    good.write_text(f"IID={pay.STAGING_INSTANCE}\n")
    bad.write_text("IID=i-0production00000000\n")
    pay.check_ssm_wrapper(good)
    with pytest.raises(AssertionError, match="Dev host"):
        pay.check_ssm_wrapper(bad)


def test_host_steps_are_detached_and_the_read_is_one_bounded_pass():
    start = pay.start_script("apply")
    assert "nohup setsid python3" in start and start.rstrip().endswith('log $log"') and "> \"$log\" 2>&1 < /dev/null &" in start
    read = pay.read_script()
    assert "while" not in read and "sleep" not in read and "tail -f" not in read and "status.json" in read
    with pytest.raises(AssertionError):
        pay.start_script("rm -rf /")


def test_the_operator_is_packaged_only_from_the_commit_it_claims(tmp_path):
    class Git:
        def blob(self, commit, path):
            return (ROOT / path).read_bytes()
    assert set(pay.operator_blobs(Git(), "H")) == {"beta_dev_deploy.py", "guarded_swap.py"}

    class Other:
        def blob(self, commit, path):
            return b"something else"
    with pytest.raises(AssertionError, match="differs"):
        pay.operator_blobs(Other(), "H")

    class Absent:
        def blob(self, commit, path):
            return None
    with pytest.raises(AssertionError, match="commit it first"):
        pay.operator_blobs(Absent(), "H")


# ── A real host's file handling, on a folder, with Docker faked ──

class _FakeO:
    PF, MC_FINAL, MC_FILES, MC = "", "", [], "minimoi-mc-agent"


@pytest.fixture
def real(tmp_path):
    op_dir = tmp_path / "operators" / op.OPERATOR_NAME
    (op_dir / "release/payload/services/model_gateway").mkdir(parents=True)
    (op_dir / "release/payload/services/model_gateway/litellm.staging.yaml").write_text("new: 1\n")
    (op_dir / "release/payload/a").mkdir()
    (op_dir / "release/payload/a/x.py").write_text("x\n")
    meta = {"commit": "H", "skipped": {}, "targets": {
        "model-gateway": {**{k: v for k, v in op.TARGETS["model-gateway"].items() if k != "rules"}, "files": {"services/model_gateway/litellm.staging.yaml": {"dest": "@config/x", "after": sha(b"new: 1\n"), "accepted": []}}},
        "portal": {"container": "minimoi-portal", "repo": "portal", "tier": 4, "wait": 1, "files": {"a/x.py": {"dest": "/app/a/x.py", "after": sha(b"x\n"), "accepted": [], "mode": "644"}}},
        "records": {"container": "minimoi-records", "repo": "records", "tier": 1, "files": {}}}}
    (op_dir / "release/meta.json").write_text(json.dumps(meta))
    for rel, text in (("bin/compose.sh", 'exec docker compose -f a "$@"\n'), ("bin/mc.sh", 'exec docker compose -f b "$@"\n'), ("config/litellm.staging.yaml", "old: 1\n"),
                      ("config/model-routes/catalogue.json", "{}\n")):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    host = op.RealHost(root=tmp_path, op_module=_FakeO())
    host.where = {"portal": ("compose", "portal"), "model-gateway": ("compose", "model-gateway")}
    return host


def test_an_image_with_no_files_is_not_a_target(real):
    assert set(real.targets) == {"model-gateway", "portal"}


def test_writing_then_restoring_gives_back_every_byte(real):
    real.gateway_file = real.R / "config/litellm.staging.yaml"
    real.BEFORE.mkdir()
    for wrapper, path in real.WRAPPERS.items():
        shutil.copy2(path, real.BEFORE / f"wrapper.{wrapper}.private")
    real.write_new()
    assert real.gateway_file.read_text() == "new: 1\n"
    assert f'-f {real.OVERRIDES["compose"]} "$@"' in real.COMPOSE.read_text()
    assert json.loads(real.OVERRIDES["compose"].read_text()) == {"services": {"portal": {"image": f"minimoi-staging/portal:{op.RELEASE}", "pull_policy": "never"}}}
    assert json.loads(real.OVERRIDES["mc"].read_text()) == {"services": {}}
    assert op.without_override(real.COMPOSE.read_text(), str(real.OVERRIDES["compose"])) == 'exec docker compose -f a "$@"\n'
    assert stat.S_IMODE(real.OVERRIDES["compose"].stat().st_mode) == 0o600


def test_the_build_context_is_removed_after(real):
    real.CTX.mkdir()
    (real.CTX / "x").write_text("x")
    real.clean_build()
    assert not real.CTX.exists()


def test_a_recorded_recovery_writes_its_receipt(real):
    real.record("recovery_required", {"workers": "left stopped"})
    assert json.loads((real.OP / "recovery-required.json").read_text())["workers"] == "left stopped"
    assert "recovery_required" in (real.OP / "journal.jsonl").read_text()


# ── The whole operator against a pretend Docker: every host step runs, and what it leaves behind is read back ──

ALL_CHANGED = sorted(set(CHANGED) | {"docker/cos-agent-a/plugins/cos-bound/index.mjs"})
PORTAL_ROUTES_OK = {"/app/cos/task-proposals": 302, "/app/cos/work-cards": 302, "tools": 401}


class _Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeDocker:
    """Images, containers and two compose projects in memory. Only what the operator reads and changes."""

    def __init__(self, root, meta):
        self.root, self.images, self.containers, self.fail, self.log = root, {}, {}, {}, []
        self.routes, self.bad_running_file, self.fail_up = dict(PORTAL_ROUTES_OK), None, None
        self.services = {"compose": {}, "mc": {}}
        self.others = {"postgres-ai-agents": {"id": "pg", "image": "pgimg"}}
        for key, t in meta["targets"].items():
            name = t["container"]
            if t.get("kind") == "config":
                self._add("cos-scheduler:model-gateway-x", "sha256:gateway", {})
                self._container(name, "cos-scheduler:model-gateway-x", "sha256:gateway", "compose", "model-gateway", [])
                continue
            files = {m["dest"]: m["base"] for m in t["files"].values() if not m["new"]}
            image_id = "sha256:base-" + key
            tag = f"minimoi-staging/{t['repo']}:" + (t.get("old") or "running")
            self._add(tag, image_id, files, user="node" if t.get("kind") == "node" else "")
            wrapper = "mc" if key in ("mc-relay", "mc-agent") else None if key.startswith("guildhall") else "compose"
            env = [f"{k}={v}" for k, v in op.REQUIRED_ENV.get(name, {}).items()] + (["MINIMOI_ROLE=standby", "MINIMOI_MC_TOOLS_ENV=dev"] if key == "portal" else [])
            mounts = [{"Destination": "/home/node/.openclaw", "Source": str(root / "mc-state")}] if key == "mc-agent" else []
            self._container(name, tag, image_id, wrapper, key, env, mounts, compose_file=str(root / "config/staging-aws.yml"))

    def _add(self, tag, image_id, files, user=""):
        image = {"Id": image_id, "RootFS": {"Layers": [image_id]}, "Config": {"User": user, "Image": ""}, "files": dict(files)}
        self.images[tag] = self.images[image_id] = image

    def _container(self, name, tag, image_id, wrapper, service, env, mounts=(), compose_file=""):
        labels = {"com.docker.compose.project": "minimoi-staging", "com.docker.compose.project.config_files": compose_file} if wrapper else {}
        self.containers[name] = {"Id": "c-" + name, "Name": "/" + name, "Image": image_id, "Mounts": list(mounts), "State": {"Running": True},
                                 "Config": {"Image": tag, "Env": list(env), "User": "", "Labels": labels}, "HostConfig": {"NetworkMode": "n", "RestartPolicy": {"Name": "always"}},
                                 "NetworkSettings": {"Networks": {"n": {"Aliases": []}}}}
        if wrapper:
            self.services[wrapper][service] = {"container_name": name, "image": tag}

    def render(self, wrapper, overrides=()):
        services = copy.deepcopy(self.services[wrapper])
        for path in overrides:
            for service, change in json.loads(Path(path).read_text())["services"].items():
                services[service].update(change)
        return {"name": wrapper, "services": services}

    def _overrides(self, wrapper_path, args):
        found = re.findall(r"-f (\S+)", Path(wrapper_path).read_text())
        extra = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
        return [p for p in [*found, *extra] if p.endswith(".yml") and Path(p).exists() and p.startswith(str(self.root / "operators"))]

    def compose(self, wrapper_path, args):
        wrapper = "mc" if wrapper_path.endswith("mc.sh") else "compose"
        render = self.render(wrapper, self._overrides(wrapper_path, args))
        words = [a for i, a in enumerate(args) if a != "-f" and (i == 0 or args[i - 1] != "-f")]
        if words[0] == "config":
            return _Result(json.dumps(render))
        assert words[0] == "up" and "--no-deps" in words and "--no-build" in words, words
        service = words[-1]
        self.log.append(("up", service))
        if self.fail_up and self.fail_up(service):
            return _Result("", 1, "up failed")
        spec = render["services"][service]
        name, c = spec["container_name"], self.containers[spec["container_name"]]
        image = self.images[spec["image"]]
        if c["Config"]["Image"] != spec["image"] or c["Image"] != image["Id"] or "--force-recreate" in words:
            c["Id"] = c["Id"] + "+"
        c["Config"]["Image"], c["Image"] = spec["image"], image["Id"]
        # As docker does: a container holds its image's ENV lines; the old image brings none back.
        c["Config"]["Env"] = [e for e in c["Config"]["Env"] if e not in c.get("from_image", [])] + image.get("added", [])
        c["from_image"] = image.get("added", [])
        c["State"]["Running"] = "--no-start" not in words
        self.log.append(("recreated", name))
        return _Result("")

    def build(self, args):
        tag = args[args.index("-t") + 1]
        folder = Path(args[-1])
        self.log.append(("build", tag))
        if self.fail.get("build") and self.fail["build"] in tag:
            return _Result("", 1, "build failed")
        first = (folder / "Dockerfile").read_text().splitlines()[0].split()[1]
        base = self.images[first]
        files = dict(base["files"])
        ctx = folder / "ctx"
        assert ctx.is_dir(), "context missing"
        for p in ctx.rglob("*"):
            if p.is_file():
                files["/" + str(p.relative_to(ctx))] = sha(p.read_bytes())
        files.pop(op.MC_DIR + ".beta-check-6c.sh", None)
        new_id = "sha256:new-" + hashlib.sha256(tag.encode()).hexdigest()[:12]
        self.images[tag] = self.images[new_id] = {"Id": new_id, "RootFS": {"Layers": [*base["RootFS"]["Layers"], new_id]}, "Config": copy.deepcopy(base["Config"]), "files": files}
        added = [l[4:] for l in (folder / "Dockerfile").read_text().splitlines() if l.startswith("ENV ")]        # as docker build lays them
        if added:
            config = self.images[tag]["Config"]
            config["Env"] = [*(config.get("Env") or []), *added]
            self.images[tag]["added"] = added
        return _Result("")

    def _sums(self, files, paths):
        got = [f"{files[p]}  {p}" for p in paths if p in files]
        return _Result("\n".join(got) + "\n", 0 if len(got) == len(paths) else 1)

    def __call__(self, args, **kw):
        args = [str(a) for a in args]
        if args[0].endswith((".sh",)):
            return self.compose(args[0], args[1:])
        assert args[0] == "docker", args
        verb = args[1]
        if verb == "inspect":
            c = self.containers.get(args[2])
            return _Result(json.dumps([c])) if c else _Result("", 1)
        if verb == "image":
            image = self.images.get(args[3])
            return _Result(json.dumps([{k: v for k, v in image.items() if k != "files"}])) if image else _Result("", 1)
        if verb == "info":
            return _Result(str(self.root) + "\n")
        if verb == "build":
            return self.build(args)
        if verb == "stop":
            for name in args[4:]:
                if name in self.containers:
                    self.containers[name]["State"]["Running"] = False
            self.log.append(("stop", args[4:]))
            return _Result("")
        if verb == "start":
            for name in args[2:]:
                if name in self.containers:      # a worker this rig's change set skips has no container here
                    self.containers[name]["State"]["Running"] = True
            self.log.append(("start", args[2:]))
            return _Result("")
        if verb == "update":
            return _Result("")
        if verb == "rm":
            del self.containers[args[2]]
            return _Result("")
        if verb == "run":
            at = args.index("--entrypoint")
            entry, image, rest = args[at + 1], self.images[args[at + 2]], args[at + 3:]
            if entry == "sha256sum":
                return self._sums(image["files"], rest)
            if entry == "python" and rest[:2] == ["-c", op.IMPORT_CHECK]:
                return _Result("[]\n")
            if entry == "python" and rest[:2] == ["-c", op.LAYOUT_CHECK]:
                return _Result('{"differs": [], "missing": []}\n')
            return _Result("")
        if verb == "exec":
            name, cmd = args[2], args[3:]
            c = self.containers[name]
            if cmd[0] == "sha256sum":
                files = dict(self.images[c["Image"]]["files"])
                if self.bad_running_file and self.bad_running_file[0] == name:
                    files[next(p for p in cmd[1:] if p in files)] = "e" * 64
                return self._sums(files, cmd[1:])
            if "COS_TOOL_BINDINGS_FILE" in cmd[-1]:
                return _Result("0\n")
            if "task-proposals" in cmd[-1]:
                return _Result(json.dumps(self.routes) + "\n")
            if "config.yaml" in cmd[-1]:
                return _Result(sha((self.root / "config/litellm.staging.yaml").read_bytes()) + "\n")
            return _Result("")
        raise AssertionError(args)


class FakeOp:
    def __init__(self, docker, root):
        self.docker, self.MC = docker, "minimoi-mc-agent"
        self.PF, self.MC_FINAL = root / "operators/inc/compose.partnership2.yml", root / "operators/inc/mc.partnership2.yml"
        self.MC_FILES = [root / "mc-release/mc.yml", self.MC_FINAL]

    def inspect(self, name):
        return copy.deepcopy(self.docker.containers[name])

    def contract(self, name):
        c = self.docker.containers[name]
        return {"env": sorted(c["Config"]["Env"]), "host": c["HostConfig"], "mounts": c["Mounts"]}

    def snapshot(self):
        mine = {n: {"id": c["Id"], "image": c["Image"]} for n, c in self.docker.containers.items() if c["State"]["Running"]}
        return {**mine, **self.docker.others}

    def mc_compose(self, *args):
        return json.dumps(self.docker.render("mc"))

    def idle(self):
        return True

    @contextmanager
    def admission_gate(self):
        self.docker.log.append(("gate", "open"))
        yield "gate"
        self.docker.log.append(("gate", "closed"))

    def gate_alive(self, _):
        return True

    def wait_healthy(self, name, gate, limit):
        assert self.docker.containers[name]["State"]["Running"], name + " is not running"
        c = self.docker.containers[name]
        c["State"].pop("Health", None)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path
    meta, blobs = pay.build_meta(_FakeGit(ALL_CHANGED), "head", BASES)
    tar = pay.make_tar(meta, blobs, {"beta_dev_deploy.py": b"#", "guarded_swap.py": b"#"})
    OP = root / "operators" / op.OPERATOR_NAME
    (OP / "release").mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as t:
        t.extractall(OP / "release", filter="data")
    (OP / "release.tar.gz").write_bytes(tar)
    (OP / "release.sha256").write_text(sha(tar) + "\n")
    gateway = next(iter(meta["targets"]["model-gateway"]["files"]))
    runtime = _policy()
    for rel, text in (("bin/compose.sh", 'exec docker compose -p minimoi-staging -f /r/x.yml "$@"\n'), ("bin/mc.sh", 'exec docker compose -p minimoi-mc -f /r/y.yml "$@"\n'),
                      ("config/litellm.staging.yaml", "old " + gateway), ("config/model-routes/catalogue.json", json.dumps({"roles": {"cos-conversation": {"route": op.A}, "mc-conversation": {"route": op.A}}})),
                      ("config/staging-aws.yml", "services: {}\n"), ("operators/inc/compose.partnership2.yml", "{}\n"), ("operators/inc/mc.partnership2.yml", "{}\n"), ("mc-release/mc.yml", "{}\n"),
                      ("mc-state/openclaw.json", json.dumps(runtime))):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    docker = FakeDocker(root, meta)
    monkeypatch.setattr(op.subprocess, "run", docker)
    monkeypatch.setattr(op.time, "sleep", lambda *_: None)
    monkeypatch.setattr(op.shutil, "disk_usage", lambda _: type("U", (), {"free": 10 * 1024 ** 3}))
    host = op.RealHost(root=root, op_module=FakeOp(docker, root))

    def api(method, path, body=None):
        docker.log.append(("api", method, path.split("?")[0]))
        if "/rename" in path:
            old = path.split("/")[2]
            new = path.split("name=")[1]
            docker.containers[new] = docker.containers.pop(old)
            docker.containers[new]["Name"] = "/" + new
        elif path.startswith("/containers/create"):
            name = path.split("name=")[1]
            docker.containers[name] = {"Id": "c2-" + name, "Name": "/" + name, "Image": body["Image"], "Mounts": [], "State": {"Running": False},
                                       "Config": {k: v for k, v in body.items() if k not in ("HostConfig", "NetworkingConfig")},
                                       "HostConfig": body["HostConfig"], "NetworkSettings": {"Networks": {"n": {"Aliases": []}}}}
        elif path.endswith("/start"):
            docker.containers[path.split("/")[2]]["State"]["Running"] = True
        return {}
    monkeypatch.setattr(host, "_api", api)
    return type("Rig", (), {"host": host, "docker": docker, "root": root, "meta": meta, "OP": OP, "deploy": op.Deploy(host, gs.guarded_swap)})


def _frozen(rig):
    return {"images": {n: c["Image"] for n, c in rig.docker.containers.items()}, "tags": {n: c["Config"]["Image"] for n, c in rig.docker.containers.items()},
            "ids": {n: c["Id"] for n, c in rig.docker.containers.items()},
            "files": {p: (rig.root / p).read_bytes() for p in ("bin/compose.sh", "bin/mc.sh", "config/litellm.staging.yaml", "config/model-routes/catalogue.json", "config/staging-aws.yml",
                                                               "operators/inc/compose.partnership2.yml", "operators/inc/mc.partnership2.yml", "mc-state/openclaw.json")}}


def _workers_running(rig):
    return [w for w in op.WORKERS if w in rig.docker.containers and rig.docker.containers[w]["State"]["Running"]]


def test_end_to_end_a_dry_run_changes_nothing_live_and_names_who_recreates(rig, capsys):
    before = _frozen(rig)
    plan = rig.deploy.dry_run()
    assert _frozen(rig) == before and not rig.host.OVERRIDES["compose"].exists() and not rig.host.CTX.exists()
    assert not [e for e in rig.docker.log if e[0] in ("up", "stop", "start", "api")]
    order = [r["container"] for r in plan["recreate_in_order"]]
    assert order[0] == "minimoi-model-gateway" and order.index("minimoi-cos-scheduler") < order.index("minimoi-cos-agent-a") < order.index("minimoi-portal") < order.index("minimoi-mc-agent")
    assert set(order[-3:]) == set(op.WORKERS) - {"minimoi-cos-board", "minimoi-records"} and "minimoi-records" not in order
    out = capsys.readouterr().out
    assert "WILL RECREATE minimoi-portal" in out and "SKIP minimoi-records: nothing it runs changed" in out
    assert json.loads((rig.OP / "plan.json").read_text())["release"] == op.RELEASE


def test_end_to_end_a_good_apply_runs_every_new_image_and_keeps_the_settings(rig):
    before = _frozen(rig)
    assert rig.deploy.apply() is gs.Outcome.APPLIED
    built = {key: t for key, t in rig.meta["targets"].items() if t.get("kind") != "config"}
    for key, t in built.items():
        c = rig.docker.containers[t["container"]]
        assert c["Image"] == rig.host.new_ids[key] != before["images"][t["container"]] and c["State"]["Running"]
    # each overlay built on the running id
    assert all(rig.docker.images[rig.host.new_ids[k]]["RootFS"]["Layers"][0] == before["images"][t["container"]] for k, t in built.items())
    assert (rig.root / "config/litellm.staging.yaml").read_text() == "new services/model_gateway/litellm.staging.yaml"
    for path in ("config/model-routes/catalogue.json", "config/staging-aws.yml", "operators/inc/compose.partnership2.yml", "operators/inc/mc.partnership2.yml"):
        assert (rig.root / path).read_bytes() == before["files"][path], path
    assert op.without_override((rig.root / "bin/compose.sh").read_text(), str(rig.host.OVERRIDES["compose"])).encode() == before["files"]["bin/compose.sh"]
    assert json.loads(rig.host.OVERRIDES["mc"].read_text())["services"]["mc-agent"] == {"image": f"minimoi-staging/mc-agent:{op.RELEASE}", "pull_policy": "never"}
    assert (rig.OP / "result.json").exists() and not (rig.OP / "recovery-required.json").exists()
    # MC last among the recreates; the gateway first; workers started after the gate closed
    ups = [e[1] for e in rig.docker.log if e[0] == "recreated"]
    assert ups[0] == "minimoi-model-gateway" and ups.index("minimoi-portal") < ups.index("minimoi-mc-relay") < ups.index("minimoi-mc-agent")
    closed = rig.docker.log.index(("gate", "closed"))
    assert any(e[0] == "start" for e in rig.docker.log[closed:]) and not any(e[0] == "start" for e in rig.docker.log[:closed])
    # standalone Guildhall kept its old container, renamed
    assert "minimoi-guildhall-mc-before-" + op.RELEASE in rig.docker.containers


def test_end_to_end_a_failed_build_touches_nothing_and_leaves_no_context(rig):
    rig.docker.fail["build"] = "/mc-agent:"
    before = _frozen(rig)
    with pytest.raises(RuntimeError, match="command failed"):
        rig.deploy.apply()
    assert _frozen(rig) == before and not rig.host.CTX.exists() and not rig.host.OVERRIDES["compose"].exists()
    assert not [e for e in rig.docker.log if e[0] in ("up", "stop", "api")]


def _assert_restored(rig, before):
    now = _frozen(rig)
    assert now["images"] == before["images"] and now["tags"] == before["tags"], "images"
    assert now["files"] == before["files"], "files"
    assert not rig.host.OVERRIDES["compose"].exists() and not rig.host.OVERRIDES["mc"].exists()
    assert sorted(_workers_running(rig)) == sorted(set(op.WORKERS) - {"minimoi-cos-board", "minimoi-records"})


def test_end_to_end_a_failed_verify_restores_and_proves_it(rig):
    before = _frozen(rig)
    rig.docker.routes["/app/cos/work-cards"] = 404
    with pytest.raises(AssertionError, match="route missing"):
        rig.deploy.apply()
    _assert_restored(rig, before)
    assert json.loads((rig.OP / "outcome.json").read_text())["outcome"] == "restored" and not (rig.OP / "recovery-required.json").exists()


def test_end_to_end_a_running_file_that_differs_fails_the_verify_and_restores(rig):
    before = _frozen(rig)
    rig.docker.bad_running_file = ("minimoi-portal",)
    with pytest.raises(AssertionError, match="holds a different file"):
        rig.deploy.apply()
    _assert_restored(rig, before)


def test_end_to_end_a_failed_recreate_part_way_restores_everything(rig):
    before = _frozen(rig)
    seen = {"count": 0}

    def fail_portal_once(service):
        if service == "portal" and seen["count"] == 0:
            seen["count"] = 1
            return True
        return False
    rig.docker.fail_up = fail_portal_once
    with pytest.raises(RuntimeError, match="command failed"):
        rig.deploy.apply()
    _assert_restored(rig, before)


def test_end_to_end_a_failed_restore_leaves_workers_stopped_with_recovery_required(rig):
    rig.docker.routes["/app/cos/work-cards"] = 404
    calls = {"portal": 0}

    def fail_second_portal_up(service):
        if service == "portal":
            calls["portal"] += 1
            return calls["portal"] == 2                 # the apply's own is the first; the restore's is the second
        return False
    rig.docker.fail_up = fail_second_portal_up
    with pytest.raises(AssertionError, match="route missing"):
        rig.deploy.apply()
    assert (rig.OP / "recovery-required.json").exists() and _workers_running(rig) == []
    assert json.loads((rig.OP / "outcome.json").read_text())["outcome"] == "recovery_required"
    with pytest.raises(AssertionError, match="needs recovery"):                  # and nothing runs again until someone has looked
        rig.deploy.dry_run()


def test_end_to_end_a_rollback_restores_and_proves_it(rig):
    before = _frozen(rig)
    rig.deploy.apply()
    assert rig.deploy.rollback() is gs.Outcome.APPLIED
    _assert_restored(rig, before)
    assert json.loads((rig.OP / "result.json").read_text())["status"] == "rolled_back_verified"


def test_end_to_end_a_fresh_process_can_roll_back_from_what_was_saved(rig):
    before = _frozen(rig)
    rig.deploy.apply()
    fresh = op.RealHost(root=rig.root, op_module=rig.host.O)
    fresh._api = rig.host._api
    op.Deploy(fresh, gs.guarded_swap).rollback()
    _assert_restored(rig, before)


def test_end_to_end_rollback_with_nothing_applied_is_refused_and_moves_nothing(rig):
    before = _frozen(rig)
    with pytest.raises(AssertionError, match="nothing was applied"):
        rig.deploy.rollback()
    assert _frozen(rig) == before


def test_end_to_end_a_tampered_payload_or_tarball_is_refused_before_anything(rig):
    target = next((rig.OP / "release/payload").rglob("app.py"))
    good = target.read_bytes()
    target.write_bytes(b"tampered")
    with pytest.raises(AssertionError, match="differs from what was reviewed"):
        rig.deploy.dry_run()
    target.write_bytes(good)
    (rig.OP / "release.tar.gz").write_bytes(b"other")
    with pytest.raises(AssertionError, match="differs from its recorded"):
        rig.deploy.dry_run()
    assert not [e for e in rig.docker.log if e[0] in ("build", "up", "stop")]


def test_end_to_end_a_hand_edited_running_file_is_refused_and_named(rig, capsys):
    rig.docker.images["sha256:base-portal"]["files"]["/app/minimoi_portal/app.py"] = "f" * 64
    with pytest.raises(op.Refused, match="no commit explains"):
        rig.deploy.dry_run()
    assert "BASE minimoi-portal: /app/minimoi_portal/app.py" in capsys.readouterr().out
    assert not [e for e in rig.docker.log if e[0] == "build"]


def test_end_to_end_a_tag_that_moved_is_refused(rig):
    rig.docker.containers["minimoi-german"]["Image"] = "sha256:something-else"
    rig.docker.images["sha256:something-else"] = rig.docker.images["sha256:base-german"]
    with pytest.raises(AssertionError, match="no longer names"):
        rig.deploy.dry_run()


def test_end_to_end_a_moved_dev_setting_is_refused_before_anything(rig):
    env = rig.docker.containers["minimoi-cos-scheduler"]["Config"]["Env"]
    env[env.index("COS_BACKGROUND_JOBS=0")] = "COS_BACKGROUND_JOBS=1"
    with pytest.raises(AssertionError, match="COS_BACKGROUND_JOBS"):
        rig.deploy.dry_run()


def test_end_to_end_a_host_that_says_production_is_refused(rig):
    env = rig.docker.containers["minimoi-portal"]["Config"]["Env"]
    env[env.index("MINIMOI_ROLE=standby")] = "MINIMOI_ROLE=production"
    with pytest.raises(AssertionError, match="production"):
        rig.deploy.dry_run()
    rig.docker.containers["minimoi-portal"]["Config"]["Env"] = ["MINIMOI_ROLE=standby"]
    rig.docker.containers["minimoi-portal"]["Config"]["Labels"]["com.docker.compose.project"] = "minimoi"
    with pytest.raises(AssertionError, match="not Dev's"):
        rig.deploy.dry_run()


def test_end_to_end_low_disk_is_refused(rig, monkeypatch):
    monkeypatch.setattr(op.shutil, "disk_usage", lambda _: type("U", (), {"free": 1024 ** 3}))
    with pytest.raises(op.Refused, match="2 GB"):
        rig.deploy.dry_run()
    assert not [e for e in rig.docker.log if e[0] == "build"]


def test_only_the_exact_reviewed_host_config_gets_past_the_base_check():
    assert len(op.REVIEWED_HOST_CONFIGS) == 1
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in op.REVIEWED_HOST_CONFIGS)
    assert "0" * 64 not in op.REVIEWED_HOST_CONFIGS


def test_language_images_carry_the_photo_loader_their_servers_import():
    for key in ("german", "portuguese"):
        assert op.dest_for(op.TARGETS[key]["rules"], "core/photo_store.py") == "/app/core/photo_store.py"


def test_a_domain_image_carries_the_shared_code_it_runs():
    # 7 Nov 2026: Curator's Dive failed on a stale challenger.py the overlay never touched.
    for key in ("curator", "german", "portuguese"):
        assert op.dest_for(op.TARGETS[key]["rules"], "domains/guild/services/challenger.py") == "/app/domains/guild/services/challenger.py"
        assert op.dest_for(op.TARGETS[key]["rules"], "contracts/beta-ui/x.md") is None


def test_a_new_containers_false_oom_setting_means_the_same_as_an_old_null():
    op.check_restored({"host": {"OomKillDisable": None, "Memory": 1}}, {"host": {"OomKillDisable": False, "Memory": 1}}, "x")


@pytest.mark.parametrize("saved,live", [
    ({"host": {"OomKillDisable": None}}, {"host": {"OomKillDisable": True}}),
    ({"host": {"OomKillDisable": False, "Memory": 1}}, {"host": {"OomKillDisable": False, "Memory": 2}}),
    ({"env": ["A=1"]}, {"env": ["A=2"]}),
])
def test_every_real_config_difference_still_stops_the_restore(saved, live):
    with pytest.raises(AssertionError):
        op.check_restored(saved, live, "x")


def test_a_standalone_containers_first_network_is_found_by_name_or_id():
    nets = {"a-net": {"NetworkID": "111"}, "b-net": {"NetworkID": "222"}}
    assert op.primary_network("b-net", nets) == "b-net"
    assert op.primary_network("222", nets) == "b-net"
    assert op.primary_network("default", {"only": {"NetworkID": "9"}}) == "only"


@pytest.mark.parametrize("mode,nets", [("333", {"a": {"NetworkID": "111"}}), ("default", {"a": {}, "b": {}})])
def test_an_unknown_first_network_is_refused_not_guessed(mode, nets):
    with pytest.raises(op.Refused):
        op.primary_network(mode, nets)


def test_a_config_difference_names_the_field_and_never_its_value():
    with pytest.raises(AssertionError) as caught:
        op.check_restored({"env": ["KEY=secret-a"], "host": {"Memory": 1}}, {"env": ["KEY=secret-b"], "host": {"Memory": 1}}, "x")
    assert "/env" in str(caught.value) and "secret" not in str(caught.value)


def test_standalone_containers_are_made_with_the_servers_own_api_version():
    source = open(op.__file__).read()
    assert '"/v1.' not in source      # an older pinned version changes read-only mounts (6 Nov)


def test_the_deploys_own_backups_are_not_unrelated_containers():
    source = open(op.__file__).read()
    body = source.split("def _others(self):", 1)[1].split("# swap", 1)[0]
    assert "self._backup(s)" in body


def test_mc_overlay_carries_the_plugin_that_marks_tool_starts():
    """Failover's tool evidence lives in this plugin; an overlay without it made every switch stop on doubt, live on Dev."""
    rules = dict(op.TARGETS["mc-agent"]["rules"])
    assert rules["services/mc_operations/plugins/minimoi-tools/"] == op.MC_DIR + "plugins/minimoi-tools/"


def test_cos_to_mc_switch_lives_in_the_overlay_image_so_a_rollback_removes_it():
    from scripts.staging import beta_dev_deploy as deploy
    assert dict(deploy.TARGETS["portal"]["image_env"]) == {"MINIMOI_COS_TO_MC": "1"}
    assert dict(deploy.TARGETS["cos-scheduler"]["image_env"])["MINIMOI_COS_TO_MC"] == "1"
    assert all("image_env" not in spec for key, spec in deploy.TARGETS.items() if key not in ("portal", "cos-scheduler"))


def test_the_overlay_check_admits_exactly_the_named_switch():
    base = {"RootFS": {"Layers": ["a"]}, "Config": {"Env": ["A=1"], "User": "u", "Image": "x", "Labels": {}}}
    good = {"RootFS": {"Layers": ["a", "b"]}, "Config": {"Env": ["A=1", "S=1"], "User": "u", "Image": "y", "Labels": {}}}
    op.check_overlay(base, good, image_env=(("S", "1"),))
    with pytest.raises(AssertionError):
        op.check_overlay(base, good)                                     # not named: refused
    with pytest.raises(AssertionError):
        op.check_overlay(base, good, image_env=(("S", "2"),))            # another value: refused
    with pytest.raises(AssertionError):
        op.check_overlay(base, {**good, "Config": {**good["Config"], "Env": ["A=2", "S=1"]}}, image_env=(("S", "1"),))


def test_with_image_env_is_the_saved_contract_plus_exactly_the_named_switch():
    saved = {"env": ["B=1", "A=1"], "host": {}, "mounts": []}
    assert op.with_image_env(saved, {})["env"] == ["A=1", "B=1"]
    assert op.with_image_env(saved, {"image_env": (("S", "1"),)}) == {"env": ["A=1", "B=1", "S=1"], "host": {}, "mounts": []}


def test_a_scheduled_jobs_container_is_not_an_unrelated_change():
    # 7 Nov 2026: a memory-feed job ending mid-swap failed the apply and its restore.
    assert "dev-memory-feed-045f".startswith(op.SHORT_LIVED)
    assert not "postgres-ai-agents".startswith(op.SHORT_LIVED)
    source = open(op.__file__).read()
    assert "not k.startswith(SHORT_LIVED)" in source.split("def _others(self):", 1)[1].split("# swap", 1)[0]


# ── Dev's delivery record ──

def test_the_payload_says_which_commits_dev_has_that_main_lacks():
    meta, _ = pay.build_meta(_FakeGit(CHANGED), "head", BASES)
    d = meta["delivery"]
    assert d["commit"] == "H" * 40 and d["main"] == "B" * 40 and d["main_ref"] == "origin/main" and d["ahead_of_main"] == 2
    assert d["titles"] == [["hhhhhhh", "Guild opens your last chat"], ["ggggggg", "Work cards use plain words"]]
    assert d["branches"] == ["claude/beta-final"] and d["title"] == "Guild opens your last chat"


def test_the_record_names_release_commit_and_what_is_dev_only():
    meta, _ = pay.build_meta(_FakeGit(CHANGED), "head", BASES)
    text, doc = op.delivery_record(meta, "deployed", 1794072717.0)
    assert f"release `{op.RELEASE}` deployed and verified" in text and "`HHHHHHHH` on `claude/beta-final`" in text
    assert "## On Dev, not yet on main (2 commits, newest first)" in text and "- `hhhhhhh` Guild opens your last chat" in text
    assert "Merged to main means production" in text and "**Still open:** not in this record" in text
    assert doc["status"] == "deployed" and doc["ahead_of_main"] == 2


def test_a_rolled_back_record_never_lists_commits_as_on_dev():
    meta, _ = pay.build_meta(_FakeGit(CHANGED), "head", BASES)
    text, doc = op.delivery_record(meta, "rolled_back", 1794072717.0)
    assert "was rolled back" in text and "nothing listed below is on Dev now" in text and "not yet on main" not in text
    with pytest.raises(AssertionError):
        op.delivery_record(meta, "half done", 1.0)


def test_a_record_from_an_older_payload_still_says_what_it_knows():
    text, _ = op.delivery_record({"release": "r", "commit": "C" * 40}, "deployed", 1.0)
    assert "`CCCCCCCC`" in text and "not yet on main" not in text


def test_only_a_verified_result_writes_the_record_and_a_failed_write_changes_nothing_live():
    host = Host()
    deploy(host).apply()
    assert host.calls[-1] == "write_delivery"
    host = Host(verify_new=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert "write_delivery" not in host.calls
    host = Host(verify_workers=True)
    with pytest.raises(Boom):
        deploy(host).apply()
    assert host.calls.count("write_delivery") == 1          # the rollback's own, never "deployed"
    host = Host(write_delivery=True)
    assert deploy(host).apply() is gs.Outcome.APPLIED
    assert host.records["delivery_record_failed"] == {"status": "deployed", "exception": "Boom"}


def test_end_to_end_the_record_is_written_read_only_beside_a_history(rig):
    rig.deploy.apply()
    d = rig.root / op.DELIVERY_DIR
    text = (d / "DEV.md").read_text()
    assert "deployed and verified" in text and json.loads((d / "DEV.json").read_text())["release"] == op.RELEASE
    assert oct((d / "DEV.md").stat().st_mode & 0o777) == "0o644" and oct(d.stat().st_mode & 0o777) == "0o755"
    rig.deploy.rollback()
    assert "was rolled back" in (d / "DEV.md").read_text()
    assert [json.loads(x)["status"] for x in (d / "history.jsonl").read_text().splitlines()] == ["deployed", "rolled_back"]
