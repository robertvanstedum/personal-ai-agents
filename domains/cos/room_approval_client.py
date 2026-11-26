"""cos-scheduler's side of a CoS room approval (Lane G): send the Records-admitted approval to the portal, which alone
can act as Robert in the task service, and return its decision. Signed with the CoS owner-assertion key (audience
rooms-approve-from-cos), the same key cos_send_to_mc uses; the key never enters an agent container."""
from __future__ import annotations

import json

import requests

from core import portal_assertion as PA

PATH = "/internal/mc/v1/rooms/cos-approve"
AUDIENCE = "rooms-approve-from-cos"


class RoomApprovals:
    def __init__(self, base_url: str, key_file: str, env: str, *, session=None):
        if base_url.rstrip("/") not in ("http://portal:5011", "http://minimoi-portal:5011"):
            raise ValueError("fixed_portal_required")
        self.base, self.key_file, self.env = base_url.rstrip("/"), key_file, env
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False

    def __call__(self, room: str, turn_id: str, approval: dict) -> dict:
        body = {"room": room, "turn_id": turn_id,
                "approval": {k: approval.get(k) for k in ("code", "kind", "partner", "source_ref", "payload_digest")}}
        raw = json.dumps(body, sort_keys=True).encode()
        code = approval.get("code")
        try:
            header = PA.sign(PA.load_key(self.key_file), audience=AUDIENCE, subject="service:cos", method="POST",
                             target=PATH, body=raw, env=self.env)
            r = self.session.post(self.base + PATH, data=raw, timeout=(3, 60), allow_redirects=False,
                                  headers={"Content-Type": "application/json", "X-CoS-Assertion": header})
            data = r.json()
            if r.status_code == 200 and data.get("code") == code and data.get("state") in (
                    "confirmed", "cancelled", "failed", "refused") and isinstance(data.get("receipt"), str):
                return {"state": data["state"], "receipt": data["receipt"]}
        except (requests.RequestException, ValueError, OSError):
            pass
        return {"state": "failed", "receipt": f"I could not confirm what happened to {code}. Do not answer again yet; "
                                              "the task view shows whether it was confirmed."}


def from_environ(environ):
    url, key, env = (environ.get("COS_ROOM_APPROVE_URL") or "").strip(), environ.get("COS_OWNER_ASSERTION_KEY_FILE"), \
        environ.get("COS_OWNER_ASSERTION_ENV")
    if environ.get("MINIMOI_COS_ROOM_APPROVALS") != "1" or not (url and key and env):
        return None
    return RoomApprovals(url, key, env)
