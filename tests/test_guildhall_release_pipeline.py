"""Guildhall deploys with everything else (#550): deploy.yml builds the worker image, refreshes the Guildhall stack in the
deploy job, checks MC's own image, records every stack and says which it refreshed. Read from the workflow; no network."""
import importlib.util
import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def deploy_steps():
    return workflow()["jobs"]["deploy"]["steps"]


def step(name):
    return next(s for s in deploy_steps() if s.get("name") == name)


def index(name):
    return [s.get("name") for s in deploy_steps()].index(name)


def test_the_worker_image_builds_in_ci_when_its_files_change():
    job = workflow()["jobs"]["build-worker"]
    assert job["uses"] == "./.github/workflows/rooms-worker-image.yml"
    assert job["with"]["commit"] == "${{ github.sha }}" and job["secrets"] == "inherit"
    assert "needs.classify.outputs.rooms_worker_image == 'true'" in job["if"]
    assert "refs/heads/main" in job["if"] and "pull_request" in job["if"]


def test_the_deploy_waits_for_the_worker_build_but_runs_when_it_was_not_needed():
    job = workflow()["jobs"]["deploy"]
    assert job["needs"] == ["classify", "test", "build-push", "build-worker"]
    condition = job["if"]
    assert condition.startswith("!cancelled()")     # a skipped worker build still deploys; a cancelled run never does
    for need in ("classify", "test", "build-push"):
        assert f"needs.{need}.result == 'success'" in condition
    assert "(needs.build-worker.result == 'success' || needs.build-worker.result == 'skipped')" in condition


def test_one_deploy_at_a_time_still_covers_the_refresh():
    concurrency = workflow()["concurrency"]
    assert concurrency["group"].startswith("production-deploy-") and concurrency["cancel-in-progress"] is False
    # The refresh is a step of the deploy job, not a job or workflow of its own outside that queue.
    assert "Refresh the Guildhall stack" in [s.get("name") for s in deploy_steps()]
    assert not [name for name, job in workflow()["jobs"].items() if "guildhall_prod.sh" in json.dumps(job) and name != "deploy"]


def test_the_refresh_runs_after_the_main_deploy_with_the_scripts_from_this_commit():
    refresh = step("Refresh the Guildhall stack")
    assert refresh["if"] == "needs.classify.outputs.guildhall == 'true'"
    assert index("Install host scripts and check parity on EC2") < index("Push optional Records deployment support to EC2") \
        < index("Deploy on EC2") < index("Verify the portal sees the live Build Queue") < index("Refresh the Guildhall stack")
    run = refresh["run"]
    assert "GUILDHALL_PROD_APPROVED=1" in run and "/opt/minimoi/scripts/guildhall_prod.sh refresh" in run
    assert "MINIMOI_ROOMS_WORKER_IMAGE=" in run and refresh["env"]["WORKER_IMAGE"] == "${{ needs.build-worker.outputs.image }}"
    # The worker digest is checked before it reaches a command, and only an immutable digest passes.
    assert "cos-scheduler@sha256:[a-f0-9]{64}$" in run
    # Only refreshed, current or skipped pass; anything else fails the deploy with a named error.
    assert "refreshed|current|skipped) [ \"$rc\" = 0 ] && exit 0" in run
    for result in ("refused", "rolled_back", "rollback_failed"):
        assert f"{result})" in run
    assert run.rstrip().endswith("exit 1")


def test_the_host_gets_the_refresh_and_the_workers_project_from_this_commit():
    entries = {e["repo"]: e for e in json.loads((ROOT / "config/host_scripts.json").read_text())["entries"]}
    assert entries["scripts/operations/guildhall_prod.sh"]["ship"] is True
    assert entries["scripts/operations/guildhall_prod.sh"]["host"] == "/opt/minimoi/scripts/guildhall_prod.sh"
    assert "docker-compose.guildhall-production.yml" in step("Push optional Records deployment support to EC2")["run"]


def test_a_guildhall_only_release_deploys_but_recreates_no_main_service():
    main = step("Deploy on EC2")
    assert main["if"] == "needs.classify.outputs.services != ''" and main["id"] == "deploy_main"
    build = workflow()["jobs"]["build-push"]
    assert "needs.classify.outputs.release_class != 'documents'" in build["if"]
    docs = workflow()["jobs"]["deploy-docs"]
    assert "needs.classify.outputs.release_class == 'documents'" in docs["if"]


def test_full_fallbacks_name_guildhall():
    run = workflow()["jobs"]["classify"]["steps"][-1]["run"]
    assert run.count('echo "$FULL_STACKS" >> "$GITHUB_OUTPUT"') == 2
    assert "stacks=main guildhall\\nguildhall=true\\nrooms_worker_image=false" in run


def test_mc_agent_is_checked_against_the_tag_production_runs():
    mc = step("Check MC's own image against this release")
    assert mc["if"] == "always()"
    run = mc["run"]
    assert "minimoi-mc-agent" in run and "classify_release.py --mc-agent" in run
    assert "needed=yes" in run and "::warning title=MC agent release needed::" in run and "needed=unknown" in run
    # Read-only: it never builds, tags or deploys MC.
    for word in ("docker build", "git tag", "git push", "deploy_mc_container_ec2", "docker pull"):
        assert word not in run
    assert 'git fetch -q --no-tags --depth=1 origin "refs/tags/$LIVE:refs/tags/$LIVE"' in run


def test_every_stack_is_recorded_and_summarised_whatever_happened():
    record = step("Record every stack in the production delivery record")
    assert record["if"] == "always()" and "write_production_delivery_record.py" in record["run"]
    assert "--mc-agent-live" in record["run"] and "--mc-agent-needed" in record["run"]
    summary = step("Say which stacks this release refreshed")
    assert summary["if"] == "always()" and "scripts/ci/release_summary.py" in summary["run"]
    assert index("Refresh the Guildhall stack") < index("Check MC's own image against this release") \
        < index("Record every stack in the production delivery record") < index("Say which stacks this release refreshed")


def test_an_mc_release_records_its_tag():
    text = (ROOT / ".github/workflows/deploy-mc-agent.yml").read_text()
    assert "write_production_delivery_record.py --mc-agent-released $TAG || echo" in text
    assert text.index("deploy_mc_container_ec2.sh status") < text.index("--mc-agent-released")


def test_the_ssm_runner_takes_a_parameters_file_and_fails_unless_success():
    text = (ROOT / "scripts/ci/ssm_run.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/ci/ssm_run.sh")]).returncode == 0
    assert '--parameters "file://$PARAMETERS"' in text and 'if [[ "$STATUS" != Success ]]' in text


def _summary():
    spec = importlib.util.spec_from_file_location("release_summary", ROOT / "scripts/ci/release_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summary


def test_the_summary_lists_each_stack():
    text = _summary()({"SERVICES": "portal cos-scheduler", "MAIN": "success", "GUILDHALL_NAMED": "true", "GUILDHALL": "refreshed",
                       "GUILDHALL_STEP": "success", "MC_LIVE": "mc-agent-v0.1.32-1", "MC_NEEDED": "yes", "MC_CHANGES": "7"})
    assert "| Main | deployed: portal, cos-scheduler |" in text
    assert "| Guildhall | refreshed and verified |" in text
    assert "**MC agent release needed**: 7 file(s) MC's image holds changed since `mc-agent-v0.1.32-1`" in text


def test_the_summary_is_loud_about_failures_and_quiet_about_what_was_not_touched():
    summary = _summary()
    text = summary({"SERVICES": "", "GUILDHALL_NAMED": "true", "GUILDHALL": "rolled_back", "GUILDHALL_STEP": "failure",
                    "MC_NEEDED": "no", "MC_LIVE": "mc-agent-v0.1.33"})
    assert "not part of this release (no main service changed)" in text and "**FAILED, put back**" in text
    assert "current: production runs `mc-agent-v0.1.33`" in text
    text = summary({"SERVICES": "german", "MAIN": "failure", "GUILDHALL_NAMED": "false"})
    assert "**FAILED** (failure): german" in text and "Guildhall | not part of this release" in text and "**unknown**" in text
    text = summary({"SERVICES": "portal", "MAIN": "success", "GUILDHALL_NAMED": "true", "GUILDHALL_STEP": "failure"})
    assert "**UNCONFIRMED** (failure)" in text
