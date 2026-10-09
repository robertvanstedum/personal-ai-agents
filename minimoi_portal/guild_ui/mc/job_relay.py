"""The portal's client for Master Craftsman JOBS on the relay (relay v2 job protocol; overnight build, step 3).

A job is not a chat turn: it is started once, runs on the relay for minutes, and is read back by polling, so a page
reload or a portal restart never loses it and nothing is ever replayed. Three calls, all with the relay's caller token
(the one the chat path already uses):

  POST {url}/jobs                  start. Body {job_id, agent, task, inbox, deadline_s}.
        200 {"accepted": true,  "state": "running"}                      started
        200 {"accepted": false, "duplicate": true, "state": ...}         this job id is already known: NOT run again
        429 {"error": "busy"}                                            definitely not started
        other 4xx                                                        refused, definitely not started
        5xx, a timeout or a broken connection                            AMBIGUOUS: it may have started
  GET  {url}/jobs/<job_id>         status. 200 {state: running|completed|failed|stopped|unknown, elapsed_s,
        heartbeat_age_s, result: {text}, audit: {tools: [{name, target, ok}], truncated},
        writes: [{path, exists, bytes, sha256, verified}], error_class}; 404 = the relay has never heard of it.
  POST {url}/jobs/<job_id>/stop    200 {"stop": "confirmed" | "requested" | "not_running"}; 404 = unknown job.

Nothing here retries a start. The answer's fields are cleaned by ``clean_status`` before anyone uses them: the relay's
words are data, bounded and stripped of control characters, never trusted to be well formed."""
from __future__ import annotations

import re
from urllib.parse import quote

CONNECT_TIMEOUT_S = 5
START_TIMEOUT_S = 15
STATUS_TIMEOUT_S = 10
STOP_TIMEOUT_S = 10
RESULT_MAX_CHARS = 64 * 1024
AUDIT_TOOLS_MAX = 200
WRITES_MAX = 50
STATES = ("running", "completed", "failed", "stopped", "unknown")
FLAGS = ("touches_restricted_area", "write_outside_allowed_area")        # the only audit flags the relay may raise
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value, limit: int) -> str:
    return _CTRL.sub("", str(value if value is not None else ""))[:limit]


def _num(value, default=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value if value >= 0 else default


def clean_status(data) -> dict | None:
    """The relay's status answer reduced to what the portal may keep, or None when it is not a status at all."""
    if not isinstance(data, dict) or data.get("state") not in STATES:
        return None
    result = data.get("result")
    text = result.get("text") if isinstance(result, dict) else None
    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    tools = []
    for t in (audit.get("tools") or [])[:AUDIT_TOOLS_MAX] if isinstance(audit.get("tools"), list) else []:
        if isinstance(t, dict):
            tool = {"name": _text(t.get("name"), 60), "target": _text(t.get("target"), 300), "ok": bool(t.get("ok", True))}
            if t.get("flag") in FLAGS:
                tool["flag"] = t["flag"]
            tools.append(tool)
    writes = []
    for w in (data.get("writes") or [])[:WRITES_MAX] if isinstance(data.get("writes"), list) else []:
        if isinstance(w, dict):
            writes.append({"path": _text(w.get("path"), 300), "exists": bool(w.get("exists")), "bytes": _num(w.get("bytes")),
                           "sha256": _text(w.get("sha256"), 64) if re.fullmatch(r"[0-9a-f]{64}", str(w.get("sha256") or "")) else "",
                           "verified": bool(w.get("verified"))})
    return {"state": data["state"], "elapsed_s": _num(data.get("elapsed_s"), 0), "heartbeat_age_s": _num(data.get("heartbeat_age_s")),
            "result_text": _text(text, RESULT_MAX_CHARS + 1) if isinstance(text, str) else None,
            "tools": tools, "tools_truncated": bool(audit.get("truncated")) or (isinstance(audit.get("tools"), list) and len(audit["tools"]) > AUDIT_TOOLS_MAX),
            "audit_available": audit.get("available") is not False,
            "writes": writes, "error_class": _text(data.get("error_class"), 40) or None}


class JobRelay:
    def __init__(self, url, token, *, http_get=None, http_post=None):
        self.url = (url or "").rstrip("/")
        self._token = token or ""
        if http_get is None or http_post is None:
            import requests
            http_get = http_get or requests.get
            http_post = http_post or requests.post
        self._get, self._post = http_get, http_post

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        return cls(environ.get("MC_RUNTIME_URL"), environ.get("MC_RUNTIME_TOKEN"), http_get=http_get, http_post=http_post)

    @property
    def connected(self) -> bool:
        return bool(self.url and self._token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    @staticmethod
    def _json(resp):
        try:
            return resp.json()
        except Exception:
            return None

    def start(self, spec: dict) -> tuple[str, dict]:
        """('accepted' | 'duplicate' | 'busy' | 'refused' | 'ambiguous', detail). One attempt, never retried."""
        import json
        if not self.connected:
            return "refused", {"reason": "not_connected"}
        try:
            resp = self._post(f"{self.url}/jobs", data=json.dumps(spec), headers=self._headers(), timeout=(CONNECT_TIMEOUT_S, START_TIMEOUT_S))
        except Exception as exc:
            return "ambiguous", {"reason": type(exc).__name__}
        status = getattr(resp, "status_code", 0)
        body = self._json(resp) or {}
        if status == 200 and isinstance(body, dict):
            if body.get("accepted") is True:
                return "accepted", {}
            if body.get("duplicate") is True:
                return "duplicate", {"state": body.get("state")}
        if status == 429:
            return "busy", {}
        if 400 <= status < 500:
            return "refused", {"reason": f"http_{status}", "error": _text(body.get("error") if isinstance(body, dict) else "", 60)}
        return "ambiguous", {"reason": f"http_{status}"}

    def status(self, job_id: str) -> tuple[str, dict | None]:
        """('ok', cleaned status) | ('not_found', None) | ('unreachable', None)."""
        try:
            resp = self._get(f"{self.url}/jobs/{quote(job_id, safe='')}", headers=self._headers(), timeout=(CONNECT_TIMEOUT_S, STATUS_TIMEOUT_S))
        except Exception:
            return "unreachable", None
        code = getattr(resp, "status_code", 0)
        if code == 404:
            return "not_found", None
        if code != 200:
            return "unreachable", None
        cleaned = clean_status(self._json(resp))
        return ("ok", cleaned) if cleaned is not None else ("unreachable", None)

    def stop(self, job_id: str) -> str:
        """'confirmed' | 'requested' | 'not_running' | 'not_found' | 'unreachable'."""
        try:
            resp = self._post(f"{self.url}/jobs/{quote(job_id, safe='')}/stop", data="{}", headers=self._headers(), timeout=(CONNECT_TIMEOUT_S, STOP_TIMEOUT_S))
        except Exception:
            return "unreachable"
        code = getattr(resp, "status_code", 0)
        if code == 404:
            return "not_found"
        body = self._json(resp)
        if code == 200 and isinstance(body, dict) and body.get("stop") in ("confirmed", "requested", "not_running"):
            return body["stop"]
        return "unreachable"
