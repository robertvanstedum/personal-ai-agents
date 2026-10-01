"""Test doubles for the Rooms worker: an HTTP session over Records' Flask test
client, a fake clock, a scripted MC relay, and a background worker thread.
No network, no model, no spend."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

BACKEND = "http://minimoi-records:18880"


class Session:
    """requests.Session shape over Records' Flask test client."""

    def __init__(self, app):
        self.client = app.test_client(use_cookies=False)
        self.trust_env = False
        self.fail_next = None          # "before" or "after": simulate a lost write

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        parts = urlsplit(url)
        assert parts.netloc == "minimoi-records:18880" and "Origin" not in (headers or {})
        if self.fail_next == "before":
            self.fail_next = None
            import requests
            raise requests.ConnectionError("lost")
        r = self.client.open(parts.path + (f"?{parts.query}" if parts.query else ""), method=method,
                             headers=dict(headers or {}), data=data, base_url=BACKEND)
        if self.fail_next == "after":
            self.fail_next = None
            import requests
            raise requests.ConnectionError("response lost")
        return SimpleNamespace(status_code=r.status_code, json=lambda: json.loads(r.data or b"{}"))


class FakeClock:
    def __init__(self):
        self.now = time.time()
        self.slept = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


class FakeRelay:
    """Scripted outcomes; each script item is a dict or a callable(relay, messages) -> dict."""

    def __init__(self, script=(), ready=True):
        self.script = list(script)
        self.calls = []
        self.stops = []
        self._stopped = threading.Event()
        self.is_ready = ready
        self.during = None

    def ready(self):
        return self.is_ready

    def stop(self, correlation):
        self.stops.append(correlation)
        self._stopped.set()
        return 200

    def stream(self, messages, user, correlation, on_open=None):
        self.calls.append({"messages": messages, "user": user, "correlation": correlation})
        if self.during:
            self.during(self)
        item = self.script.pop(0) if self.script else {"outcome": "done", "text": "A short useful point.", "usage": None}
        if callable(item):
            item = item(self, messages)
        return {"detail": item.get("outcome"), "usage": None, **item}

    def wait_for_stop(self, timeout=5):
        return self._stopped.wait(timeout)


def start_worker(app, out_dir, relay, journal_dir, period=0.2):
    """Run the real worker loop in a thread against a test Records app."""
    from services.rooms_worker.clients import Records
    from services.rooms_worker.journal import TurnJournal
    from services.rooms_worker.worker import Worker
    tokens = {n: (out_dir / f"{n}.token").read_text().strip() for n in ("mc", "rooms-worker")}
    w = Worker(Records(BACKEND, tokens["rooms-worker"], session=Session(app)),
               Records(BACKEND, tokens["mc"], session=Session(app)), relay, TurnJournal(journal_dir))
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            w._last_hosted = 0                     # check invites every pass in tests
            try:
                busy = w.run_once()
            except Exception:                      # keep the test worker alive like the real one
                busy = False
            if not busy:
                stop.wait(period)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return SimpleNamespace(worker=w, stop=lambda: (stop.set(), thread.join(timeout=10)))
