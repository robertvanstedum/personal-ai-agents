"""B4 connection step 1: the task client never resends blindly; an unknown outcome is settled by receipt lookup."""
import json
import os
from types import SimpleNamespace

import pytest
import requests

from services.partnership_tasks.client import TaskClient, TaskServiceUnavailable, command, read_token
from services.partnership_tasks.http_api import create_app
from tests.partnership.synth import service


class Session:
    def __init__(self, app):
        self.client = app.test_client()
        self.trust_env = False
        self.fail = None            # "before" or "after": lose the next /v1/commands call
        self.sent = []

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        path = url.split("http://tasks:8772", 1)[1]
        if path == "/v1/commands" and self.fail == "before":
            self.fail = None
            raise requests.ConnectionError("lost before")
        r = self.client.open(path, method=method, headers=headers, data=data)
        if path == "/v1/commands":
            self.sent.append(json.loads(data)["command_id"])
            if self.fail == "after":
                self.fail = None
                raise requests.ReadTimeout("lost after")
        return SimpleNamespace(status_code=r.status_code, json=lambda: json.loads(r.data or b"{}"))


def source(cid, sid):
    return command("source_event.record", "source_event", sid,
                   {"source": {"store": "records", "id": "evt-" + sid, "position": 1}, "content": "Hello",
                    "correction_of": None}, command_id=cid, environment="synthetic", scope="personal")


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    s = Session(create_app(svc))
    return svc, s, TaskClient("http://tasks:8772", "tok-cos", session=s)


def test_accepted_and_replayed(rig):
    svc, s, c = rig
    first = c.submit(source("cmd-c-1", "SE-cli-0001"))
    assert first["outcome"] == "accepted"
    assert c.submit(source("cmd-c-1", "SE-cli-0001"))["replayed"] is True
    assert c.get("source_event", "SE-cli-0001")["source_event_id"] == "SE-cli-0001"


def test_a_reply_lost_after_commit_is_settled_by_receipt_not_resent(rig):
    svc, s, c = rig
    s.fail = "after"
    result = c.submit(source("cmd-c-2", "SE-cli-0002"))
    assert result["outcome"] == "accepted" and s.sent == ["cmd-c-2"]          # sent once, settled by lookup


def test_a_send_lost_before_arrival_says_absent_and_does_not_resend(rig):
    svc, s, c = rig
    s.fail = "before"
    with pytest.raises(TaskServiceUnavailable) as err:
        c.submit(source("cmd-c-3", "SE-cli-0003"))
    assert err.value.answer == "absent" and s.sent == []
    assert c.submit(source("cmd-c-3", "SE-cli-0003"))["outcome"] == "accepted"      # the caller may send it again


def test_unknown_token_is_refused_and_reads_are_scoped(rig, tmp_path):
    svc, s, _ = rig
    bad = TaskClient("http://tasks:8772", "tok-nobody", session=s)
    assert bad.submit(source("cmd-c-4", "SE-cli-0004"))["code"] == "http_401"
    peer = TaskClient("http://tasks:8772", "tok-peer", session=s)
    TaskClient("http://tasks:8772", "tok-cos", session=s).submit(source("cmd-c-5", "SE-cli-0005"))
    assert peer.get("source_event", "SE-cli-0005") is None


def test_token_file_must_be_private(tmp_path):
    f = tmp_path / "tok"
    f.write_text("secret-token\n")
    os.chmod(f, 0o644)
    with pytest.raises(PermissionError):
        read_token(f)
    os.chmod(f, 0o600)
    assert read_token(f) == "secret-token"
