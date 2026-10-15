"""Curator container-only state is preserved, seeded and mounted (#305)."""

import hashlib
import importlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "operations"
MAP = OPS / "curator_state.map"
HOST_PREFIX = "${MINIMOI_ROOT:-/opt/minimoi}/"


def _map_rows():
    rows = []
    for line in MAP.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            rows.append(tuple(line.split()))
    return rows


def _curator_service():
    return yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())["services"]["curator"]


def test_state_map_matches_production_compose_binds_exactly():
    binds = set()
    for volume in _curator_service()["volumes"]:
        assert volume.startswith(HOST_PREFIX), volume
        source, target = volume[len(HOST_PREFIX):].split(":")[:2]
        binds.add((source, target))
    mapped = {(host, target) for kind, _old, host, target in _map_rows() if kind in ("dir", "file")}
    assert binds == mapped


def test_moved_state_env_points_into_the_mounted_curator_folder():
    env = dict(item.split("=", 1) for item in _curator_service()["environment"])
    moved = {target for kind, _old, _host, target in _map_rows() if kind == "moved"}
    assert {env["CURATOR_SIGNAL_STORE_PATH"], env["CURATOR_X_PULL_STATE_PATH"]} == moved
    assert all(path.startswith("/app/data/curator/") for path in moved)


def test_every_formerly_unmounted_path_is_covered_and_topics_is_not_mounted():
    preserve = (OPS / "curator_preserve.sh").read_text()
    seeded = {old for kind, old, _host, _target in _map_rows() if old != "-"}
    for old in seeded:
        assert f"  {old}\n" in preserve, old
    targets = [target for *_rest, target in _map_rows()]
    assert not any("research-intelligence/topics" in target for target in targets)


def test_deploy_preserves_and_seeds_before_up_and_checks_after_health():
    script = (OPS / "deploy_scoped_release.sh").read_text()
    up = script.index('"${COMPOSE[@]}" up -d')
    assert script.index("preserve_curator_state\nfi") < up
    assert script.index("curator_preserve.sh\" snapshot --keep-stopped") < up
    assert script.index("curator_seed_state.sh") < up
    assert script.index("curator_bind_preflight.sh") < up
    assert script.index("check_curator_mounts.sh\"\nfi") > script.index("HEALTH_URLS")
    assert "trap restart_stopped_on_exit EXIT" in script
    assert script.index("CURATOR_STOPPED=1") < script.index("snapshot --keep-stopped)")


def test_workflow_ships_the_curator_kit_before_deploying():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    for name in ("curator_preserve.sh", "curator_seed_state.sh", "curator_bind_preflight.sh",
                 "check_curator_mounts.sh", "curator_state.map"):
        assert name in workflow.split("CURATOR_STATE_KIT=")[1].split("\n")[0]
    assert "tar -xzf - --no-same-owner -C /opt/minimoi/scripts" in workflow


def test_legacy_paths_stay_the_default_and_env_moves_them(monkeypatch, tmp_path):
    from domains.curator import signal_store
    from scripts.x import paths

    monkeypatch.delenv("CURATOR_SIGNAL_STORE_PATH", raising=False)
    monkeypatch.delenv("CURATOR_X_PULL_STATE_PATH", raising=False)
    assert importlib.reload(signal_store).SIGNAL_STORE_PATH == ROOT / "signal_store.jsonl"
    assert importlib.reload(paths).STATE_FILE == ROOT / "x_pull_state.json"

    monkeypatch.setenv("CURATOR_SIGNAL_STORE_PATH", str(tmp_path / "signal_store.jsonl"))
    monkeypatch.setenv("CURATOR_X_PULL_STATE_PATH", str(tmp_path / "x_pull_state.json"))
    assert importlib.reload(signal_store).SIGNAL_STORE_PATH == tmp_path / "signal_store.jsonl"
    assert importlib.reload(paths).STATE_FILE == tmp_path / "x_pull_state.json"
    signal_store.append_event("test_event", {"n": 1})
    assert (tmp_path / "signal_store.jsonl").read_text().count("\n") == 1

    monkeypatch.delenv("CURATOR_SIGNAL_STORE_PATH")
    monkeypatch.delenv("CURATOR_X_PULL_STATE_PATH")
    importlib.reload(signal_store)
    importlib.reload(paths)


# ---- seed and preflight, run as shell scripts against a fake host ----

SEEDED = {
    "/app/data/annotations/curator/2026-10-15.json": "{\"note\": 1}\n",
    "/app/_NewDomains/research-intelligence/library/session-log.md": "| row |\n",
    "/app/_NewDomains/research-intelligence/agent/config.json": "{}\n",
    "/app/domains/curator/curator_sources.json": "[]\n",
    "/app/signal_store.jsonl": "{\"e\": 1}\n",
    "/app/x_pull_state.json": "{\"last_pull_at\": null}\n",
}
EXISTING = ["data/curator/", "data/curator_archive/", "data/curator_history.json",
            "data/curator_costs.json", "data/interests/", "data/research-intelligence/"]


def _snapshot(tmp_path, files=SEEDED):
    snap = tmp_path / "snap"
    for rel, body in files.items():
        path = snap / "tree" / rel.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    lines = []
    for path in sorted((snap / "tree").rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  ./{path.relative_to(snap / 'tree')}")
    (snap / "FILES.sha256").write_text("\n".join(lines) + "\n")
    (snap / "SNAPSHOT").write_text("container_id=test\n")
    return snap


def _host(tmp_path):
    host = tmp_path / "opt"
    for rel in EXISTING:
        path = host / rel
        if rel.endswith("/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("existing\n")
    return host


def _run(script, *args, host):
    env = {**os.environ, "MINIMOI_ROOT": str(host), "CURATOR_CONTAINER": "curator-test-no-such-container",
           "CURATOR_STATE_MAP": str(MAP)}
    return subprocess.run(["bash", str(OPS / script), *args], env=env, capture_output=True, text=True)


def test_seed_copies_missing_state_then_preflight_passes(tmp_path):
    host, snap = _host(tmp_path), _snapshot(tmp_path)
    assert _run("curator_bind_preflight.sh", host=host).returncode == 1
    result = _run("curator_seed_state.sh", str(snap), host=host)
    assert result.returncode == 0, result.stderr
    assert (host / "data/annotations/curator/2026-10-15.json").read_text() == "{\"note\": 1}\n"
    assert (host / "data/curator/signal_store.jsonl").read_text() == "{\"e\": 1}\n"
    assert (host / "data/research-config/config.json").is_file()
    assert (host / "data/curator_history.json").read_text() == "existing\n"
    assert _run("curator_bind_preflight.sh", host=host).returncode == 0
    # Running it again changes nothing and still passes.
    again = _run("curator_seed_state.sh", str(snap), host=host)
    assert again.returncode == 0 and "0 new" in again.stdout


def test_seed_refuses_differing_host_state_and_changes_nothing(tmp_path):
    host, snap = _host(tmp_path), _snapshot(tmp_path)
    (host / "data/curator/signal_store.jsonl").write_text("{\"e\": 0}\n")
    result = _run("curator_seed_state.sh", str(snap), host=host)
    assert result.returncode == 1 and "differs" in result.stderr
    assert (host / "data/curator/signal_store.jsonl").read_text() == "{\"e\": 0}\n"
    assert not (host / "data/annotations").exists()


def test_seed_refuses_wrong_type_and_a_tampered_snapshot(tmp_path):
    host, snap = _host(tmp_path), _snapshot(tmp_path)
    (host / "data/research-config/config.json").mkdir(parents=True)
    assert "expected a file" in _run("curator_seed_state.sh", str(snap), host=host).stderr

    host2 = _host(tmp_path / "second")
    (snap / "tree/app/signal_store.jsonl").write_text("changed\n")
    result = _run("curator_seed_state.sh", str(snap), host=host2)
    assert result.returncode == 1 and "manifest" in result.stderr


def test_seed_refuses_a_bind_with_nothing_to_mount_but_allows_moved_files_to_start_empty(tmp_path):
    host = _host(tmp_path)
    without_annotations = {k: v for k, v in SEEDED.items() if "annotations" not in k}
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path, without_annotations)), host=host)
    assert result.returncode == 1 and "nothing to mount" in result.stderr

    host2 = _host(tmp_path / "second")
    without_moved = {k: v for k, v in SEEDED.items() if k not in ("/app/signal_store.jsonl", "/app/x_pull_state.json")}
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path / "second", without_moved)), host=host2)
    assert result.returncode == 0, result.stderr
    assert not (host2 / "data/curator/signal_store.jsonl").exists()


def test_preflight_rejects_a_file_bind_that_became_a_directory(tmp_path):
    host = _host(tmp_path)
    _run("curator_seed_state.sh", str(_snapshot(tmp_path)), host=host)
    (host / "data/curator_costs.json").unlink()
    (host / "data/curator_costs.json").mkdir()
    result = _run("curator_bind_preflight.sh", host=host)
    assert result.returncode == 1 and "curator_costs.json should be a regular file" in result.stderr
