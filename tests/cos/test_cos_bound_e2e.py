"""F2 step 2 end to end: the real cos-bound plugin (node) calls the real listener over HTTP."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from werkzeug.serving import make_server
import threading

from domains.cos.binding import BindingStore
from domains.cos.store.writer import StoreWriter
from domains.cos.tool_listener import create_app

RUN = "chatcmpl_00000001-0000-0000-0000-000000000000"


def test_plugin_to_listener_to_store(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    bindings = BindingStore(tmp_path / "b" / "bindings.sqlite3", "dev")
    bindings.start_epoch("e1")
    writer = StoreWriter(tmp_path / "store", "dev")
    app = create_app(bindings=bindings, writer=writer, token_sha256=hashlib.sha256(b"t" * 40).hexdigest(), wait_s=0)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bindings.register_instance("a" * 32)   # what the plugin's announcement does; asserted again below
        assert bindings.record("t1", principal="robert", origin="owner", scope="owner", audience="owner",
                               agent_entry="cos-agent-a", session_user="cos-confer:" + "0" * 32, lifetime_s=600,
                               owner_request_id="req-1", supersession_key="dev:owner")
        assert bindings.attach_run("t1", RUN)
        endpoint = f"http://127.0.0.1:{server.server_port}/internal/cos/v1"
        out = subprocess.run([node, str(Path(__file__).with_name("cos_bound_e2e.mjs")), endpoint, RUN],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        result = json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        server.shutdown()
    assert result["good"]["status"] == "done", result
    assert result["forged"]["code"] == "run_identity_missing"
    rows = writer.docs("cos.in_flight")
    assert [r["title"] for r in rows] == ["Deck for Robert"]
    trace = bindings.trace("t1")
    assert trace["calls"][0]["receipt_id"]
    assert [(n["tool"], n["target"]) for n in trace["native_calls"]] == [("exec", "soffice --convert-to pdf deck.pptx")]
