"""``effect-sink`` (host runtime spec v0.5 §9.3; S1-H): the acceptance target for consequential effects, as an HTTP
service over the same file and rules as ``fakework.FileSink`` (core/mc_delegation_runner/fakework.py), so its answers
are the file form's by construction:

    POST /effects {operation_id, item_id, generation, lease_until, digest}
        -> {"answer": committed | duplicate | operation_reused | lease_expired}
           one ``committed`` per operation id; the same digest again is ``duplicate``, another ``operation_reused``;
           a send at or after its receipt lease is ``lease_expired`` (R4.5-b: the target deduplicates by operation id
           and refuses a send whose lease has expired)
    GET /effects/<operation_id> -> {"operation_id", "answer": committed | absent}, or ``unsupported`` with --no-lookup

It keeps ``effects.jsonl`` (every send, with its answer and time). On gate M the runtime and fakework share the Mac's
file directly (the file form); this service serves the same file, for the controller's readback and for gate D, where
a host on another machine reaches the sink over HTTP (``effect-sink.service`` on staging).

    python -m harness_acceptance.effect_sink --dir DIR [--port N] [--no-lookup] [--port-file PATH]

Binds 127.0.0.1 only. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mc_delegation_runner.fakework import FileSink  # noqa: E402

_OP = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FIELDS = {"operation_id", "item_id", "generation", "lease_until", "digest"}
BODY_MAX = 4096


class SinkServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, folder, *, lookup=True):
        self.sink = FileSink(folder, lookup=lookup)
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = "effect-sink/1"
    sys_version = ""
    timeout = 10

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, doc):
        data = json.dumps(doc, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/effects":
            return self._send(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", ""))
            body = json.loads(self.rfile.read(length)) if 0 < length <= BODY_MAX else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if type(body) is not dict or set(body) != _FIELDS or type(body["operation_id"]) is not str \
                or not _OP.match(body["operation_id"]) or type(body["generation"]) is not int \
                or type(body["digest"]) is not str or type(body["item_id"]) is not str:
            return self._send(400, {"error": "effect_invalid"})
        answer = self.server.sink.post(body["operation_id"], body["item_id"], body["generation"], body["lease_until"],
                                       body["digest"])
        self._send(200, {"answer": answer})

    def do_GET(self):
        op = self.path[len("/effects/"):] if self.path.startswith("/effects/") else ""
        if not _OP.match(op):
            return self._send(404, {"error": "not_found"})
        self._send(200, {"operation_id": op, "answer": self.server.sink.get(op)})


def serve(folder, *, port=0, lookup=True):
    """A running sink in this process (the controller's own use): (server, thread)."""
    srv = SinkServer(("127.0.0.1", port), folder, lookup=lookup)
    t = threading.Thread(target=srv.serve_forever, name="effect-sink", daemon=True)
    t.start()
    return srv, t


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="effect-sink")
    p.add_argument("--dir", required=True, type=Path)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-lookup", action="store_true")
    p.add_argument("--port-file", type=Path)
    a = p.parse_args(argv)
    srv = SinkServer(("127.0.0.1", a.port), a.dir, lookup=not a.no_lookup)
    if a.port_file:
        a.port_file.write_text(str(srv.server_address[1]))
    print(json.dumps({"state": "serving", "port": srv.server_address[1], "lookup": not a.no_lookup}), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
