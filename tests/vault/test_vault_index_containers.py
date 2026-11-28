"""Index-step containers never outlive their step (#660, the production seed of 26-28 Nov). A timeout kills only the ``docker run`` client;
the container kept embedding and kept the seed index locked, so the next slice found ``index_busy`` and two clients shared one CPU embedder.
Every step's container is now named, removed by name when its step times out, and a seed slice first removes any leftover. Docker is
simulated by an injected ``run`` that keeps a set of containers."""
from __future__ import annotations

import subprocess

import pytest

from core.memory_backup import backup as B
from core.memory_sync import apply as A

IMAGE = "sha256:" + "a" * 64


class FakeDocker:
    """``docker run`` adds a container (and times out when ``hang``); ``rm -f`` removes it unless ``stuck``; ``ps -a`` lists them."""

    def __init__(self, containers=(), hang=False, stuck=()):
        self.containers, self.hang, self.stuck, self.calls = set(containers), hang, set(stuck), []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        verb = argv[1]
        if verb == "run":
            name = argv[argv.index("--name") + 1]
            self.containers.add(name)
            if self.hang:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"))      # the client is killed; the container keeps running
            self.containers.discard(name)                                      # --rm
            return subprocess.CompletedProcess(argv, 0, stdout='{"protocol": 1, "code": "budget_spent", "embedded_chunks": 40}\n', stderr="")
        if verb == "rm":
            if argv[-1] not in self.stuck:
                self.containers.discard(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if verb == "ps":
            return subprocess.CompletedProcess(argv, 0, stdout="".join(f"{n}\n" for n in sorted(self.containers)), stderr="")
        raise AssertionError(argv)

    def verbs(self):
        return [a[1] for a, _ in self.calls]


def runner(docker):
    return A.make_index_runner(f"docker:{IMAGE}", run=docker, embedder_url="http://vault-embedder:11434", network="minimoi-vault-embed")


def test_a_timed_out_seed_slice_removes_its_container(tmp_path):
    docker = FakeDocker(hang=True)
    with pytest.raises(B.BackupError) as e:
        runner(docker).seed(tmp_path / "shelf", tmp_path / "seed" / "memory.sqlite", 420, None)
    assert e.value.code == "index_runner_timeout"
    run_argv, run_kw = next(c for c in docker.calls if c[0][1] == "run")
    assert run_argv[run_argv.index("--name") + 1] == A.SEED_CONTAINER and run_kw["timeout"] == 420 + A.SEED_TIMEOUT_GRACE_S
    assert [a for a, _ in docker.calls if a[1] == "rm"][-1] == ["docker", "rm", "-f", A.SEED_CONTAINER]
    assert docker.containers == set()                       # nothing left to hold the seed index's lock or the embedder


def test_a_timed_out_apply_index_step_removes_its_own_container(tmp_path):
    build = tmp_path / "scratch" / "build"
    build.mkdir(parents=True)
    docker = FakeDocker(hang=True)
    with pytest.raises(B.BackupError) as e:
        runner(docker).index(build, ["01M55RJ7RTGFC980AN2Y4XMZBC"], meaning=True)
    assert e.value.code == "index_runner_timeout"
    name = docker.calls[0][0][docker.calls[0][0].index("--name") + 1]
    assert name.startswith(A.INDEX_CONTAINER_PREFIX) and ["docker", "rm", "-f", name] in [a for a, _ in docker.calls]
    assert docker.containers == set() and not (build.parent / "only.json").exists()


def test_a_container_that_will_not_go_is_reported_as_an_orphan(tmp_path):
    docker = FakeDocker(hang=True, stuck={A.SEED_CONTAINER})
    with pytest.raises(B.BackupError) as e:
        runner(docker).seed(tmp_path / "shelf", tmp_path / "seed" / "memory.sqlite", 420, None)
    assert e.value.code == "index_runner_orphaned"


def test_a_seed_slice_first_removes_leftover_index_containers_and_nothing_else(tmp_path):
    leftovers = {A.SEED_CONTAINER, A.INDEX_CONTAINER_PREFIX + "0123456789ab"}
    docker = FakeDocker(containers=leftovers | {"minimoi-portal", "minimoi-vault-embedder"})
    out = runner(docker).seed(tmp_path / "shelf", tmp_path / "seed" / "memory.sqlite", 420, None)
    assert out["code"] == "budget_spent" and out["leftover_containers_removed"] == 2
    removed = {a[-1] for a, _ in docker.calls if a[1] == "rm"}
    assert removed == leftovers                              # the portal and the embedder are never touched
    assert docker.containers == {"minimoi-portal", "minimoi-vault-embedder"}
    assert docker.verbs().index("run") > max(i for i, v in enumerate(docker.verbs()) if v == "rm")   # cleared before the slice starts


def test_a_seed_slice_never_starts_beside_an_orphan_it_cannot_remove(tmp_path):
    docker = FakeDocker(containers={A.SEED_CONTAINER}, stuck={A.SEED_CONTAINER})
    with pytest.raises(B.BackupError) as e:
        runner(docker).seed(tmp_path / "shelf", tmp_path / "seed" / "memory.sqlite", 420, None)
    assert e.value.code == "index_runner_orphaned" and "run" not in docker.verbs()


def test_a_clean_seed_slice_reports_no_leftovers(tmp_path):
    docker = FakeDocker()
    out = runner(docker).seed(tmp_path / "shelf", tmp_path / "seed" / "memory.sqlite", 420, None)
    assert out == {"protocol": 1, "code": "budget_spent", "embedded_chunks": 40} and docker.containers == set()
