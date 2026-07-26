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


def test_main_workflow_exports_built_tag_before_pull_and_up():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    export = (
        '"export MINIMOI_IMAGE_TAG='
        "${{ needs.build-push.outputs.image_tag }}"
        '",'
    )
    pull = '"docker-compose -f /opt/minimoi/docker-compose.prod.yml pull",'
    up = (
        '"docker-compose -f /opt/minimoi/docker-compose.prod.yml '
        'up -d --remove-orphans",'
    )

    assert export in workflow
    assert workflow.index(export) < workflow.index(pull) < workflow.index(up)
