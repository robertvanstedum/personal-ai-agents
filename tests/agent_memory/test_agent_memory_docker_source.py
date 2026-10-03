"""DockerArchiveSource against a fake Docker socket (v0.4 §3.3, §6): read-only,
one request, fixed codes, no Docker needed."""
import os
import socketserver
import tempfile
import threading
import time

import pytest

from agent_memory_helpers import NOW, make_cfg, make_tar
from core.agent_memory.errors import COPY_INCOMPLETE, DOCKER_TIMEOUT, DOCKER_UNREACHABLE, CopierError
from core.agent_memory.run import run_source
from core.agent_memory.sources import MAX_ARCHIVE_BYTES, DockerArchiveSource


class FakeDocker:
    """A unix-socket HTTP server that records every request line and serves a canned reply."""

    def __init__(self, status=200, body=b"", delay=0.0, cut=None, declared=None):
        self.requests, self.status, self.body, self.delay, self.cut, self.declared = [], status, body, delay, cut, declared
        self.dir = tempfile.mkdtemp(prefix="am-", dir="/tmp")
        self.path = os.path.join(self.dir, "d.sock")
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline().decode("latin-1").strip()
                while self.rfile.readline().strip():
                    pass
                outer.requests.append(line)
                time.sleep(outer.delay)
                body = outer.body if outer.cut is None else outer.body[:outer.cut]
                length = outer.declared if outer.declared is not None else len(outer.body)
                self.wfile.write(f"HTTP/1.1 {outer.status} X\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n".encode() + body)

        self.server = socketserver.ThreadingUnixStreamServer(self.path, Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


@pytest.fixture
def fake():
    made = []
    def make(**kw):
        d = FakeDocker(**kw); made.append(d); return d
    yield make
    [d.close() for d in made]


FILES = {"MEMORY.md": b"# m\nhello\n", "memory/a.md": b"a\n"}


def source(d, **kw):
    return DockerArchiveSource("minimoi-cos-agent-a", "/home/node/.openclaw/workspace-cos-agent-a", socket_path=d.path, **kw)


def test_reads_the_workspace_with_exactly_one_read_only_request(fake):
    d = fake(body=make_tar(FILES))
    scan = source(d).read()
    assert scan.complete and set(scan.files) == set(FILES)
    assert len(d.requests) == 1 and d.requests[0].startswith("GET /containers/minimoi-cos-agent-a/archive?path=")
    assert "exec" not in d.requests[0].lower()


@pytest.mark.parametrize("status", [404, 500, 403])
def test_a_non_200_answer_is_docker_unreachable(fake, status):
    with pytest.raises(CopierError) as err:
        source(fake(status=status))._fetch()
    assert err.value.code == DOCKER_UNREACHABLE


def test_no_socket_is_docker_unreachable(tmp_path):
    with pytest.raises(CopierError) as err:
        DockerArchiveSource("c", "/w", socket_path=str(tmp_path / "missing.sock"))._fetch()
    assert err.value.code == DOCKER_UNREACHABLE


def test_a_hanging_daemon_is_docker_timeout_within_the_deadline(fake):
    d = fake(body=make_tar(FILES), delay=3)
    started = time.monotonic()
    with pytest.raises(CopierError) as err:
        source(d, timeout_s=0.5)._fetch()
    assert err.value.code == DOCKER_TIMEOUT and time.monotonic() - started < 2


def test_a_cut_off_body_is_incomplete_and_publishes_nothing(fake, tmp_path):
    blob = make_tar(FILES)
    d = fake(body=blob, cut=len(blob) // 2, declared=len(blob))
    with pytest.raises(CopierError) as err:
        source(d)._fetch()
    assert err.value.code == COPY_INCOMPLETE
    result = run_source(source(d), make_cfg(), tmp_path / "data", NOW)
    assert not result.ok and result.code == COPY_INCOMPLETE
    assert not (tmp_path / "data" / "cos-agent-a" / "current").exists()          # a partial copy is never a deletion


def test_an_oversized_body_is_refused(fake):
    d = fake(body=b"x" * 10, declared=MAX_ARCHIVE_BYTES + 1)
    with pytest.raises(CopierError) as err:
        source(d)._fetch()
    assert err.value.code == COPY_INCOMPLETE


def test_end_to_end_copy_through_the_fake_socket(fake, tmp_path):
    d = fake(body=make_tar({**FILES, ".env": b"SECRET=1", "sessions/s.md": b"no"}))
    result = run_source(source(d), make_cfg(), tmp_path / "data", NOW)
    assert result.ok and result.counts["files"] == 2
    assert (tmp_path / "data" / "cos-agent-a" / "current" / "MEMORY.md").read_bytes() == FILES["MEMORY.md"]
