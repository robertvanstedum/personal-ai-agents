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
    mapped = {(host, target) for kind, _old, host, target in _map_rows() if kind in ("dir", "appdir", "file")}
    assert binds == mapped


def test_moved_state_env_points_into_the_mounted_curator_folder():
    env = dict(item.split("=", 1) for item in _curator_service()["environment"])
    moved = {target for kind, _old, _host, target in _map_rows() if kind == "moved"}
    assert {env["CURATOR_SIGNAL_STORE_PATH"], env["CURATOR_X_PULL_STATE_PATH"],
            env["CURATOR_OUTPUT_PATH"]} == moved
    assert all(path.startswith("/app/data/curator/") for path in moved)


def test_every_formerly_unmounted_path_is_covered_and_topics_is_not_mounted():
    preserve = (OPS / "curator_preserve.sh").read_text()
    seeded = {old for kind, old, _host, _target in _map_rows() if old != "-"}
    for old in seeded:
        assert f"  {old}\n" in preserve, old
    targets = [target for *_rest, target in _map_rows()]
    assert not any(target in ("/app/_NewDomains/research-intelligence/topics", "/app/research-state/topics") for target in targets)


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
    kit_step = workflow.index("- name: Push Curator preservation kit to EC2")
    assert workflow.index("- name: Push docker-compose.prod.yml to EC2") < kit_step
    assert kit_step < workflow.index("- name: Deploy on EC2")
    # The kit travels in its own SSM command, not the near-limit compose sync.
    compose_step = workflow[workflow.index("- name: Push docker-compose.prod.yml to EC2"):kit_step]
    assert "CURATOR_STATE_KIT" not in compose_step


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
    "/app/curator_cache/abc12.json": "{\"hash_id\": \"abc12\"}\n",
    "/app/curator_output.txt": "1. Article\n",
}
EXISTING = ["data/curator/", "data/curator_archive/", "data/curator_history.json",
            "data/curator_costs.json", "data/interests/", "data/research-intelligence/"]


def _snapshot(tmp_path, files=SEEDED, paths=None):
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
    if paths is None:
        seeded = [old for kind, old, _h, _t in _map_rows() if old != "-"]
        held = lambda old: any(k == old or k.startswith(old + "/") for k in files)
        paths = [("copied " if held(old) else "absent ") + old for old in seeded]
        paths.append("absent /app/_NewDomains/research-intelligence/topics")
    (snap / "PATHS").write_text("\n".join(paths) + "\n")
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


# A stand-in for the docker CLI, so failure paths run in CI without a daemon.
FAKE_DOCKER = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG"
cmd="$1"; shift
case "$cmd" in
  ps)
    [[ -z "${FAKE_PS_FAIL:-}" ]] || { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
    printf '%s\n' ${FAKE_CONTAINERS:-} ;;
  inspect)
    fmt=""
    while [[ $# -gt 1 ]]; do
      case "$1" in --format=*) fmt="${1#--format=}" ;; --format) shift; fmt="$1" ;; esac; shift
    done
    case "$fmt" in
      "") echo "[]" ;;
      *println*.Destination*) printf '%s\n' /app/data/curator ;;
      *.Mounts*) echo "  bind /h/data/curator -> /app/data/curator rw=true" ;;
      *State.Running*) echo true ;;
      *) echo fake ;;
    esac ;;
  diff) cat "$FAKE_DIFF" ;;
  top) echo "PID COMMAND" ;;
  stop|start) ;;
  cp)
    path="${1#*:}"
    if [[ "$path" == "${FAKE_CP_IO_FAIL:-none}" ]]; then
      printf 'partial-bytes'; echo "write /dev/stdout: no space left on device" >&2; exit 1
    elif [[ -e "$FAKE_FS$path" ]]; then
      tar -C "$FAKE_FS$(dirname "$path")" -cf - "$(basename "$path")"
    else
      echo "Error response from daemon: Could not find the file $path in container x" >&2; exit 1
    fi ;;
  *) echo "fake docker: unexpected $cmd" >&2; exit 9 ;;
esac
"""


def _fake_docker(tmp_path, **env):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "docker").chmod(0o755)
    diff = tmp_path / "docker-diff.txt"
    diff.write_text(env.pop("diff", ""))
    return {"PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_LOG": str(tmp_path / "docker.log"),
            "FAKE_DIFF": str(diff), **env}


def _run(script, *args, host, docker=None):
    env = {**os.environ, "MINIMOI_ROOT": str(host), "CURATOR_CONTAINER": "minimoi-curator",
           "CURATOR_STATE_MAP": str(MAP), **(docker or _fake_docker(host.parent))}
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


def test_seed_refuses_snapshot_records_that_do_not_match_its_tree(tmp_path):
    host = _host(tmp_path)
    seeded = [old for kind, old, _h, _t in _map_rows() if old != "-"]
    lying = [f"copied {old}" for old in seeded] + ["absent /app/_NewDomains/research-intelligence/topics"]
    without_log = {k: v for k, v in SEEDED.items() if k != "/app/signal_store.jsonl"}
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path, without_log, lying)), host=host)
    assert result.returncode == 1 and "says it copied /app/signal_store.jsonl" in result.stderr
    assert not (host / "data/annotations").exists()

    host2 = _host(tmp_path / "second")
    unaccounted = [line for line in lying if "x_pull_state" not in line]
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path / "second", SEEDED, unaccounted)), host=host2)
    assert result.returncode == 1 and "does not account for /app/x_pull_state.json" in result.stderr


@pytest.mark.parametrize("topics", ["/app/_NewDomains/research-intelligence/topics", "/app/research-state/topics"])
def test_seed_refuses_while_topics_exists_in_the_running_container(tmp_path, topics):
    host = _host(tmp_path)
    files = {**SEEDED, topics + "/t/s.md": "session\n"}
    paths = [f"copied {old}" for kind, old, _h, _t in _map_rows() if old != "-"]
    paths.append("copied " + topics)
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path, files, paths)), host=host)
    assert result.returncode == 1 and "topics exists in the running container" in result.stderr
    assert not (host / "data/annotations").exists()


def test_seed_fails_closed_when_docker_cannot_list_containers(tmp_path):
    host = _host(tmp_path)
    docker = _fake_docker(tmp_path, FAKE_PS_FAIL="1")
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path)), host=host, docker=docker)
    assert result.returncode == 1 and "could not list Docker containers" in result.stderr
    assert not (host / "data/annotations").exists()


# ---- curator_preserve.sh failure injection (fake docker) ----

def _container_fs(tmp_path, extra=None):
    fs = tmp_path / "container"
    for rel, body in {**SEEDED, **(extra or {})}.items():
        path = fs / rel.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return fs


def _preserve(tmp_path, *, diff="", **env):
    root = tmp_path / "preserve"
    docker = _fake_docker(tmp_path, diff=diff, FAKE_CONTAINERS="minimoi-curator",
                          FAKE_FS=str(_container_fs(tmp_path, env.pop("extra", None))), **env)
    run_env = {**os.environ, **docker, "CURATOR_CONTAINER": "minimoi-curator", "CURATOR_PRESERVE_ROOT": str(root),
               "CURATOR_COMPOSE_FILE": str(tmp_path / "none.yml")}
    result = subprocess.run(["bash", str(OPS / "curator_preserve.sh"), "snapshot"], env=run_env,
                            capture_output=True, text=True)
    log = Path(docker["FAKE_LOG"]).read_text() if Path(docker["FAKE_LOG"]).exists() else ""
    return result, root, log


def test_a_copy_error_on_existing_state_fails_closed_and_keeps_evidence(tmp_path):
    result, root, log = _preserve(tmp_path, FAKE_CP_IO_FAIL="/app/signal_store.jsonl",
                                  diff="A /app/signal_store.jsonl\n")
    assert result.returncode != 0 and "could not copy /app/signal_store.jsonl" in result.stderr
    assert "no space left" in result.stderr
    sets = list(root.iterdir())
    assert len(sets) == 1 and sets[0].name.endswith(".partial")          # nothing published
    kept = sets[0] / "archives"
    assert (kept / "app__signal_store.jsonl.tar.partial").read_text() == "partial-bytes"
    assert (kept / "app__signal_store.jsonl.cp-error").is_file()
    assert "stop --time 30 minimoi-curator" in log and "start minimoi-curator" in log


def test_not_found_contradicted_by_the_writable_layer_fails_closed(tmp_path):
    # Docker says the cursor is missing, but the layer shows it was written.
    shutil = __import__("shutil")
    result, root, log = _preserve(tmp_path, diff="A /app/x_pull_state.json\n")
    assert result.returncode == 0, result.stderr        # present in the fake fs: copied
    shutil.rmtree(root)
    (tmp_path / "container" / "app" / "x_pull_state.json").unlink()
    result = subprocess.run(["bash", str(OPS / "curator_preserve.sh"), "snapshot"],
                            env={**os.environ, **_fake_docker(tmp_path, diff="A /app/x_pull_state.json\n",
                                                              FAKE_CONTAINERS="minimoi-curator",
                                                              FAKE_FS=str(tmp_path / "container")),
                                 "CURATOR_PRESERVE_ROOT": str(root), "CURATOR_COMPOSE_FILE": str(tmp_path / "none.yml")},
                            capture_output=True, text=True)
    assert result.returncode != 0 and "could not copy /app/x_pull_state.json" in result.stderr
    assert all(p.name.endswith(".partial") for p in root.iterdir())


def test_paths_with_spaces_are_kept_whole_and_true_absence_is_recorded(tmp_path):
    odd = "/app/data/odd dir/note one.json"
    result, root, _log = _preserve(tmp_path, extra={odd: "kept\n"},
                                   diff=f"C /app\nA /app/data/odd dir\nA {odd}\n")
    assert result.returncode == 0, result.stderr
    (snap,) = [p for p in root.iterdir() if not p.name.endswith(".partial")]
    assert (snap / "tree" / odd.lstrip("/")).read_text() == "kept\n"
    paths = (snap / "PATHS").read_text().splitlines()
    assert f"copied {odd}" in paths
    assert "absent /app/_NewDomains/research-intelligence/topics" in paths
    assert "copied /app/signal_store.jsonl" in paths


def test_preserve_fails_closed_when_docker_cannot_list_containers(tmp_path):
    result, root, log = _preserve(tmp_path, FAKE_PS_FAIL="1")
    assert result.returncode != 0 and "could not list Docker containers" in result.stderr
    assert "nothing to preserve" not in result.stdout and "stop" not in log


def test_deploy_proves_curator_absence_from_a_listing_not_a_failed_inspect():
    script = (OPS / "deploy_scoped_release.sh").read_text()
    body = script[script.index("preserve_curator_state() {"):]
    assert 'docker inspect "$CURATOR_CONTAINER" >/dev/null 2>&1' not in script
    assert "if curator_container_exists; then" in body
    assert "docker ps -a --format '{{.Names}}'" in script


def test_legacy_local_to_production_data_sync_refuses_to_run(tmp_path):
    script = ROOT / "scripts" / "sync_data.sh"
    result = subprocess.run(["bash", str(script), "203.0.113.1"], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode != 0
    assert "retired" in result.stderr
    assert "rsync" not in script.read_text()
    assert "sync_data.sh" not in (ROOT / "scripts" / "bootstrap_ec2.sh").read_text()


def test_seed_creates_the_article_folder_empty_only_when_the_snapshot_proves_it_absent(tmp_path):
    host = _host(tmp_path)
    no_articles = {k: v for k, v in SEEDED.items() if not k.startswith("/app/curator_cache/")}
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path, no_articles)), host=host)
    assert result.returncode == 0, result.stderr
    assert (host / "data/curator_cache").is_dir()
    assert list((host / "data/curator_cache").iterdir()) == []
    assert _run("curator_bind_preflight.sh", host=host).returncode == 0

    # With articles in the snapshot they are copied, never replaced by an empty folder.
    host2 = _host(tmp_path / "second")
    result = _run("curator_seed_state.sh", str(_snapshot(tmp_path / "second")), host=host2)
    assert result.returncode == 0, result.stderr
    assert (host2 / "data/curator_cache/abc12.json").read_text() == "{\"hash_id\": \"abc12\"}\n"
    assert (host2 / "data/curator/curator_output.txt").read_text() == "1. Article\n"


def test_preflight_rejects_an_article_folder_that_is_a_file(tmp_path):
    host = _host(tmp_path)
    _run("curator_seed_state.sh", str(_snapshot(tmp_path)), host=host)
    import shutil
    shutil.rmtree(host / "data/curator_cache")
    (host / "data/curator_cache").write_text("not a folder\n")
    result = _run("curator_bind_preflight.sh", host=host)
    assert result.returncode == 1 and "curator_cache should be a directory" in result.stderr


def test_every_reader_of_the_briefing_text_honours_the_moved_path():
    files = subprocess.run(["git", "grep", "-l", "curator_output.txt", "--", "*.py", "*.sh",
                            ":!docs/archive", ":!tests", ":!scripts/operations"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert files
    for rel in files:
        if rel == "domains/curator/curator_server.py":
            continue  # mentions the file only in a docstring
        assert "CURATOR_OUTPUT_PATH" in (ROOT / rel).read_text(), rel


def test_feedback_reads_the_briefing_text_from_the_env_path(monkeypatch, tmp_path):
    from domains.curator import curator_feedback

    monkeypatch.delenv("CURATOR_OUTPUT_PATH", raising=False)
    assert importlib.reload(curator_feedback).CURATOR_OUTPUT == ROOT / "curator_output.txt"
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(tmp_path / "curator_output.txt"))
    assert importlib.reload(curator_feedback).CURATOR_OUTPUT == tmp_path / "curator_output.txt"
    monkeypatch.delenv("CURATOR_OUTPUT_PATH")
    importlib.reload(curator_feedback)


def test_images_never_carry_curator_runtime_state_that_moved_to_host_mounts():
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
               if line.strip() and not line.startswith("#")}
    for kind, old, _host, _target in _map_rows():
        if kind not in ("moved", "appdir"):
            continue
        rel = old.removeprefix("/app/")
        assert "/" not in rel, old  # build-context root patterns only
        assert (rel + "/" if kind == "appdir" else rel) in ignored, old
    for name in ("curator_output.txt", "signal_store.jsonl", "x_pull_state.json"):
        assert name in ignored


def test_relocated_state_mounts_keep_canonical_host_sources_without_nested_data():
    service = _curator_service()
    env = dict(item.split("=", 1) for item in service["environment"])
    assert env["CURATOR_RESEARCH_STATE_DIR"] == "/app/research-state"
    targets = {host: target for kind, _old, host, target in _map_rows() if kind in ("dir", "appdir", "file")}
    assert targets["data/research-intelligence"] == "/app/data/research-intelligence"
    assert targets["data/research-library"] == "/app/research-state/library"
    assert targets["data/research-config/config.json"] == "/app/research-state/agent/config.json"
    assert all(not target.startswith("/app/data/research-intelligence/") for target in targets.values())
    rows = {host: old for _kind, old, host, _target in _map_rows()}
    assert rows["data/research-library"] == "/app/_NewDomains/research-intelligence/library"
    assert rows["data/research-config/config.json"] == "/app/_NewDomains/research-intelligence/agent/config.json"
