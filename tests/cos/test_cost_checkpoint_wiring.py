"""Structural checks keep COS cost observability deployable and read-only."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_cos_image_and_dev_mount_include_shared_gateway_reporting_module():
    dockerfile = (ROOT / "docker/Dockerfile.cos").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "COPY services/model_gateway/ services/model_gateway/" in dockerfile
    assert "./services/model_gateway:/app/services/model_gateway:ro" in compose


def test_cos_schedules_read_only_cost_checkpoint_and_exposes_status():
    source = (ROOT / "domains/cos/chief_of_staff.py").read_text()

    assert '"loop_i": {"name": "model_gateway_cost_check"' in source
    assert 'lambda: _run_loop("loop_i", _run_model_gateway_cost_checkpoint)' in source
    assert '@app.route("/costs/model-gateway")' in source
    assert "cannot mutate budgets, routes, or infrastructure" in source
