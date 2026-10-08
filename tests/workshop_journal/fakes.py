"""Synthetic teammates for the Unit 3 scenarios. They only append to the journal, as a real adapter would through the helper.

Nothing here talks to a model, a network or a real checkout; the owner resolver cannot resolve anything outside a folder that
carries the synthetic marker (v0.7 Unit 3, Grok G3)."""
from __future__ import annotations

import os
from pathlib import Path

from conftest import new_id

SYNTHETIC_MARKER = ".synthetic-owner-root"


class NotASyntheticRoot(Exception):
    pass


class SyntheticOwnerResolver:
    """Stands in for the real owner control: true only for the owner's own decision carrying a synthetic authority reference,
    and only for a journal whose root holds the synthetic marker. It refuses to be built anywhere else."""

    def __init__(self, journal_root: str):
        marker = Path(journal_root) / SYNTHETIC_MARKER
        if not marker.is_file():
            raise NotASyntheticRoot("the synthetic owner resolver only works inside a synthetic root")
        self.root = str(journal_root)

    def __call__(self, ev: dict) -> bool:
        ref = ev.get("authority_ref") or {}
        return (ev.get("actor") == "robert" and ref.get("type") == "owner-control" and str(ref.get("ref", "")).startswith("synthetic:")
                and (Path(self.root) / SYNTHETIC_MARKER).is_file())


def make_synthetic_root(root: str) -> str:
    Path(root, SYNTHETIC_MARKER).write_text("synthetic data only\n")
    return root


class Teammate:
    def __init__(self, name: str, journal, topic: str = "scenario"):
        self.name, self.j, self.topic = name, journal, topic

    def _send(self, kind: str, payload: dict, text: str, *, item: str | None = None, ok: bool = True, **over):
        env = {"actor": self.name, "kind": kind, "item": item or f"topic:{self.topic}", "topic": self.topic, "text": text,
               "payload": payload, "recipients": over.pop("recipients", []), **over}
        result = self.j.append(env)
        if ok:
            assert result.ok, result.to_json()
        return result

    def request(self, to: list[str], action: str, text: str, **over):
        payload = {"action": action, "expected_result": over.pop("expected", "A numbered result.")}
        for k in ("due_at", "resource"):
            if k in over:
                payload[k] = over.pop(k)
        return self._send("request", payload, text, recipients=to, **over)

    def receive(self, request_id: str, **over):
        return self._send("receipt", {"request_id": request_id, "recipient": self.name}, f"{self.name} picked up the request.", **over)

    def claim(self, request_id: str, resource: str, generation: int, **over):
        return self._send("claim", {"request_id": request_id, "resource": resource, "generation": generation, "claimant": self.name},
                          f"{self.name} claims {resource}.", **over)

    def release(self, claim_id: str, generation: int, stopped: bool, reason: str = "finished", **over):
        return self._send("release", {"claim_id": claim_id, "generation": generation, "stopped": stopped, "reason": reason},
                          f"{self.name} releases the claim.", **over)

    def progress(self, text: str, *, ok: bool = True, **payload):
        return self._send("progress", {"action": payload.pop("action", "working"), **payload}, text, ok=ok)

    def result(self, request_id: str, outcome: str = "completed", limitations: list | None = None, text: str = "Result.", *,
               ok: bool = True, **extra):
        refs = extra.pop("refs", None)
        payload = {"request_id": request_id, "recipient": self.name, "outcome": outcome, "limitations": limitations or [], **extra}
        return self._send("result", payload, text, ok=ok, **({"refs": refs} if refs else {}))

    def needs_you(self, text: str, **over):
        return self._send("needs_you", {"reason_code": "owner_choice", "requested_action": "Decide.", "incident_id": new_id()}, text,
                          recipients=["robert"], **over)

    def propose(self, text: str, **over):
        return self._send("decision", {"record_event_kind": "proposed", "resolves": [], "reason": text}, text, **over)

    def next(self, next_actor: str, action: str, **over):
        return self._send("next", {"next_actor": next_actor, "action": action}, f"Next: {next_actor} {action}", **over)

    def blocked(self, text: str, **over):
        return self._send("blocked", {"reason_code": "waiting_on_input"}, text, **over)

    def decide_as_owner(self, resolves: list[str], *, ref: str = "synthetic:one", kind: str = "approved-direct", **over):
        return self.j.append({"actor": "robert", "kind": "decision", "item": f"topic:{self.topic}", "topic": self.topic,
                              "text": "Owner decision.", "payload": {"record_event_kind": kind, "resolves": resolves, "reason": "owner chose"},
                              "authority_ref": {"type": "owner-control", "ref": ref}, **over})
