"""Production must deploy the image set built for the triggering commit."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi"
APP_SERVICES = (
    "curator",
    "german",
    "portuguese",
    "portal",
    "system-bot",
    "cos-bot",
    "cos-scheduler",
)


def test_production_app_images_share_one_configurable_commit_tag():
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    for service in APP_SERVICES:
        match = re.search(
            rf"(?ms)^  {re.escape(service)}:\n"
            rf".*?^    image:\s*(\S+)\s*$",
            compose,
        )
        assert match is not None
        image = match.group(1)
        assert image.startswith(f"{REGISTRY}/")
        assert image.endswith(":${MINIMOI_IMAGE_TAG:-latest}")


def test_scoped_deploy_exports_built_tag_before_pull_and_up():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts/operations/deploy_scoped_release.sh").read_text()

    assert "deploy_scoped_release.sh ${{ needs.build-push.outputs.image_tag }}" in workflow
    assert script.index('export MINIMOI_IMAGE_TAG="$IMAGE_TAG"') < script.index(
        '"${COMPOSE[@]}" pull'
    ) < script.index('"${COMPOSE[@]}" up -d --no-deps')


def test_main_workflow_waits_for_compose_sync_before_deploying():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    sync_step = workflow[
        workflow.index("- name: Push docker-compose.prod.yml to EC2") :
        workflow.index("- name: Deploy on EC2")
    ]

    assert "--query 'Command.CommandId'" in sync_step
    assert "aws ssm get-command-invocation" in sync_step
    assert 'if [ "$STATUS" != "Success" ]' in sync_step
    assert "brief wait is sufficient" not in sync_step


def test_remote_deploy_stops_on_failure_and_manages_unused_images():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    deploy_step = workflow[workflow.index("- name: Deploy on EC2") :]
    script = (ROOT / "scripts/operations/deploy_scoped_release.sh").read_text()

    assert '"set -e",' in deploy_step
    assert "docker image prune -af" in script
    assert script.index("docker image prune -af") > script.index("HEALTH_URLS")
    assert "docker inspect --format='{{.Config.Image}}'" in script
    assert "docker inspect --format='{{.State.Running}}'" in script


def test_remote_deploy_allows_slow_image_pulls_before_timing_out():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    deploy_step = workflow[workflow.index("- name: Deploy on EC2") :]

    assert "--timeout-seconds 1800" in deploy_step
    assert 'executionTimeout=["1800"]' in deploy_step
    assert "for i in $(seq 1 180)" in deploy_step
    assert "Deploy timed out after 30 minutes" in deploy_step


def test_cos_images_can_import_shared_core_package():
    for path in ("docker/Dockerfile.cos", "docker/Dockerfile.cos-scheduler"):
        dockerfile = (ROOT / path).read_text(encoding="utf-8")
        assert "ENV PYTHONPATH=/app" in dockerfile


def test_remote_deploy_requires_cos_health():
    script = (ROOT / "scripts/operations/deploy_scoped_release.sh").read_text()

    assert '[cos-scheduler]="http://localhost:8769/health"' in script
