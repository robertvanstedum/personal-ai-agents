"""A fake relay that speaks the v2 JOB protocol (mc/job_relay.py) and can be made to misbehave in every way a real one
might: refuse, be busy, answer a start ambiguously (received or lost), go away, lose a job, ignore a stop. It records
every call so a test can prove nothing was started twice. No network, no model."""
from __future__ import annotations

import json
import re


class Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


JOB_URL = re.compile(r"/jobs/(j-[0-9a-f]{16})(/stop)?$")


class FakeJobRelay:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.starts: list[dict] = []            # every start request body that reached the relay
        self.stops: list[str] = []
        self.calls: list[tuple] = []
        self.tombstones: set[str] = set()
        self.start_mode = "ok"                  # ok | busy | refuse | ambiguous_received | ambiguous_lost | down | server_error
        self.status_mode = "ok"                 # ok | down | garbage
        self.stop_mode = "confirmed"            # confirmed | requested | unreachable | not_running
        self.audit_available = True
        self.on_start = None                    # a hook called with the body just before the relay answers (to inspect state)

    # ── transport ──
    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append(("GET", url))
        if self.status_mode == "down" or self.start_mode == "down":
            raise ConnectionError("relay unreachable")
        m = JOB_URL.search(url)
        if not m or m.group(2):
            return Resp(404, {"error": "not_found"})
        job = self.jobs.get(m.group(1))
        if job is None:
            return Resp(404, {"error": "not_found"})
        if self.status_mode == "garbage":
            return Resp(200, {"state": "weird"})
        return Resp(200, self._status(job))

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        self.calls.append(("POST", url))
        if self.start_mode == "down":
            raise ConnectionError("relay unreachable")
        if url.endswith("/jobs"):
            return self._start(json.loads(data))
        m = JOB_URL.search(url)
        if m and m.group(2):
            return self._stop(m.group(1))
        return Resp(404, {"error": "not_found"})

    # ── behaviour ──
    def _start(self, body):
        if self.on_start:
            self.on_start(body)
        jid = body["job_id"]
        mode = self.start_mode
        if mode == "busy":
            return Resp(429, {"error": "busy"})
        if mode == "refuse":
            return Resp(422, {"error": "bad_agent"})
        if mode == "server_error":
            raise TimeoutError("read timed out")
        if mode == "http500":
            self.starts.append(body)                            # it may well have started: a 500 says nothing either way
            self.jobs[jid] = {"state": "running", "elapsed_s": 0, "heartbeat_age_s": 1, "result": None, "tools": [], "writes": [], "error_class": None}
            return Resp(500, {"error": "internal"})
        if jid in self.tombstones:
            return Resp(409, {"error": "stopped_before_start"})
        if mode == "ambiguous_lost":
            raise TimeoutError("read timed out")                # the request never arrived
        if jid in self.jobs:
            return Resp(200, {"accepted": False, "duplicate": True, "state": self.jobs[jid]["state"]})
        self.starts.append(body)
        self.jobs[jid] = {"state": "running", "elapsed_s": 0, "heartbeat_age_s": 1, "result": None, "tools": [], "writes": [], "error_class": None}
        if mode == "ambiguous_received":
            raise TimeoutError("read timed out")                # it arrived and runs, but the answer was lost
        return Resp(200, {"accepted": True, "job_id": jid, "state": "running"})

    def _stop(self, jid):
        self.stops.append(jid)
        if self.stop_mode == "unreachable":
            raise ConnectionError("relay unreachable")
        job = self.jobs.get(jid)
        if job is None:
            self.tombstones.add(jid)                            # a stop for a job it never heard of: a later start is refused
            return Resp(404, {"error": "not_found"})
        if job["state"] != "running" or self.stop_mode == "not_running":
            return Resp(200, {"stop": "not_running"})
        if self.stop_mode == "requested":
            return Resp(200, {"stop": "requested"})
        job["state"] = "stopped"
        return Resp(200, {"stop": "confirmed"})

    def _status(self, job):
        return {"state": job["state"], "elapsed_s": job["elapsed_s"], "heartbeat_age_s": job["heartbeat_age_s"],
                "result": ({"text": job["result"]} if job["result"] is not None else None),
                "audit": {"tools": job["tools"], "truncated": False, "available": self.audit_available}, "writes": job["writes"], "error_class": job["error_class"]}

    # ── test controls ──
    def progress(self, jid, elapsed_s, tools=None):
        self.jobs[jid]["elapsed_s"] = elapsed_s
        if tools is not None:
            self.jobs[jid]["tools"] = tools

    def finish(self, jid, text, *, tools=None, writes=None):
        j = self.jobs[jid]
        j.update(state="completed", result=text, tools=tools if tools is not None else j["tools"], writes=writes or [])

    def fail(self, jid, error_class="agent_error"):
        self.jobs[jid].update(state="failed", error_class=error_class)

    def forget(self, jid):
        self.jobs.pop(jid, None)
