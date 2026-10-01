"""Rooms worker (ROOMS_R1.md §3.8): a sibling container beside Records.

Claims Master Craftsman's meeting turns from Records with its work-scoped
credential, answers each through the MC relay, and posts the reply as MC with
MC's own membership-scoped credential. It has no portal code, import or URL,
so Rooms keeps answering while the portal is down.

Rules it keeps:
- journal before inference; the generated content is saved once and delivered
  under a fresh envelope at every delivery (same idempotency key);
- no relay request without a fresh dispatch-admission answer from Records;
- a stop request from Records stops the relay turn; late output is journaled,
  never posted;
- receipt first on recovery; a lookup or journal failure never means "nothing
  happened", so it never triggers inference.

Logs carry ids and outcomes only: no message text, no credential.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.rooms_worker import adapter
from services.rooms_worker.clients import Records, Relay, Unavailable, read_secret
from services.rooms_worker.journal import RoomBridgeError, TurnJournal, peek

DISPATCH_FRESH_S = 5
BUSY_RETRY_S = 5
HEARTBEAT_S = 1
HOSTED_EVERY_S = 10


def log(**fields):
    fields.setdefault("at", datetime.now(timezone.utc).isoformat())
    print(json.dumps(fields, sort_keys=True), flush=True)


def journal_key(turn_id):
    return "rooms:" + turn_id


def fingerprint(turn):
    return hashlib.sha256(f"{turn['room']}:{turn['id']}:{turn['trigger_seq']}:{turn['attempt']}".encode()).hexdigest()


class Worker:
    def __init__(self, records_work, records_mc, relay, journal, teammate="mc", alive_path=None, clock=time,
                 agent_id="mc-agent", runtime="OpenClaw"):
        self.work = records_work
        self.mc = records_mc
        self.relay = relay
        self.journal = journal
        self.teammate = teammate
        self.alive_path = Path(alive_path) if alive_path else None
        self.clock = clock
        self._last_hosted = 0.0
        # Rooms R2 (ROOMS_R2.md §3.2): runtime identity per teammate; the
        # turns this worker is delivering right now, so threaded housekeeping
        # never reconciles or delivers the same turn concurrently.
        self.agent_id, self.runtime = agent_id, runtime
        self._inflight = set()
        self._lock = threading.Lock()
        self._housekeeper = None
        self.on_committed = None          # optional callback(turn) after Records accepted a reply

    def start_housekeeping(self, every=None):
        """Housekeeping on its own thread (invites, reconciliation, reach), so a
        long turn does not stale the teammate's reach. Idempotent."""
        if self._housekeeper:
            return
        stop = threading.Event()

        def loop():
            while not stop.wait(every or HOSTED_EVERY_S):
                try:
                    self.housekeeping()
                except Exception as error:           # never print payloads or secrets
                    log(event="housekeeping_error", kind=type(error).__name__)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        self._housekeeper = (thread, stop)

    def stop_housekeeping(self):
        if self._housekeeper:
            self._housekeeper[1].set()
            self._housekeeper[0].join(timeout=5)
            self._housekeeper = None

    # ── one pass of the loop ────────────────────────────────────────────────
    def run_once(self):
        self.touch()
        if not self._housekeeper and self.clock.time() - self._last_hosted >= HOSTED_EVERY_S:
            self._last_hosted = self.clock.time()
            self.housekeeping()
        status, data = self.work.call("POST", "/turns/claim", {"addressees": [self.teammate]}, key=str(uuid4()))
        if status != 200 or not data.get("turn"):
            return False
        turn = data["turn"]
        with self._lock:
            self._inflight.add(turn["id"])
        try:
            self.answer(turn)
        finally:
            with self._lock:
                self._inflight.discard(turn["id"])
        return True

    def housekeeping(self):
        status, hosted = self.work.call("GET", "/hosted-teammates")
        if status != 200:
            log(event="hosted_unavailable", status=status)
            return
        for invite in hosted.get("pending_invites", []):
            if invite["principal"] == self.teammate and invite.get("proven"):
                code, _ = self.mc.call("POST", f"/rooms/{invite['room']}/rsvp", {"state": "accepted"},
                                       key=f"rsvp-accept:{invite['room']}:{self.teammate}")
                log(event="rsvp_accept", room=invite["room"], status=code)
        if hasattr(self.relay, "reconcile_proofs"):
            self.relay.reconcile_proofs(self.mc)       # proofs Records accepted before a crash (B2-02)
        for item in hosted.get("uncertain_turns", []):
            # Only this worker's own teammate (R2-03), and never a turn it is delivering now.
            if item.get("principal", self.teammate) != self.teammate:
                continue
            with self._lock:
                if item["turn_id"] in self._inflight:
                    continue
                self._inflight.add(item["turn_id"])
            try:
                self.recover(item)
            finally:
                with self._lock:
                    self._inflight.discard(item["turn_id"])
        if self.relay.ready():
            self.work.call("POST", f"/hosted-teammates/{self.teammate}/reachable",
                           {"readyz_at": datetime.now(timezone.utc).isoformat()}, key=str(uuid4()))
        elif getattr(self.relay, "unready_reason", None):
            self.work.call("POST", f"/hosted-teammates/{self.teammate}/reachable",
                           {"readyz_at": datetime.now(timezone.utc).isoformat(), "unready": self.relay.unready_reason},
                           key=str(uuid4()))

    def touch(self):
        if self.alive_path:
            self.alive_path.parent.mkdir(parents=True, exist_ok=True)
            self.alive_path.write_text(datetime.now(timezone.utc).isoformat())

    # ── a claimed turn ──────────────────────────────────────────────────────
    def answer(self, turn):
        key = journal_key(turn["id"])
        try:
            saved = self.journal.reserve(key, fingerprint(turn), {"room": turn["room"], "turn_id": turn["id"]})
        except RoomBridgeError:
            # Started before and unresolved: never infer again for this turn.
            self.fail(turn, turn["claim_id"], "uncertain", "journal_started")
            return
        if saved is None:
            saved = self.infer(turn)
            if saved is None:
                return
        self.deliver(turn, turn["claim_id"], saved)

    def admitted(self, turn, first):
        """(ok, state, asked_at). asked_at is a monotonic time taken BEFORE the
        request, so the freshness window is measured conservatively (review F4)."""
        path = f"/turns/{turn['id']}/start" if first else f"/turns/{turn['id']}/heartbeat"
        body = {"claim_id": turn["claim_id"]} if first else {"claim_id": turn["claim_id"], "intent": "dispatch"}
        asked_at = time.monotonic()
        try:
            status, data = self.work.call("POST", path, body, key=str(uuid4()))
        except Unavailable:
            return False, "records_unavailable", asked_at
        if status == 200 and data.get("dispatch") is True:
            return True, None, asked_at
        return False, data.get("state") or f"http_{status}", asked_at

    def infer(self, turn):
        """Run the relay turn; on success journal the generated content and return it."""
        first = True
        while True:
            ok, state, asked_at = self.admitted(turn, first)
            if not ok:
                if state == "cancel_requested":
                    self.ack(turn, turn["claim_id"], late_output=False)
                log(event="dispatch_refused", turn=turn["id"], state=state)
                return None
            first = False
            if time.monotonic() - asked_at >= DISPATCH_FRESH_S:
                continue                     # the answer is too old to send on: ask again
            messages = adapter.messages(turn)
            if time.monotonic() - asked_at >= DISPATCH_FRESH_S:
                continue
            correlation = uuid4().hex
            watcher = Watcher(self, turn, correlation)
            watcher.start()
            if time.monotonic() - asked_at >= DISPATCH_FRESH_S:
                watcher.stop()               # the send boundary: still fresh, or ask again (review F4)
                continue
            try:
                result = self.relay.stream(messages, adapter.user_key(turn["room"], turn["id"]), correlation, turn=turn,
                                           admit=lambda: self.spawn_admission(turn))
            finally:
                watcher.stop()
            log(event="relay_turn", turn=turn["id"], correlation=correlation, outcome=result["outcome"],
                detail=result["detail"], chars=len(result["text"]))
            if result["outcome"] == "not_admitted":
                # A runner asked for a fresh admission at its spawn boundary and did
                # not get one; no process started (ROOMS_R2 B2-01).
                if result.get("state") == "cancel_requested":
                    self.ack(turn, turn["claim_id"], late_output=False)
                    return None
                if result.get("state") == "stale":
                    continue
                log(event="dispatch_refused", turn=turn["id"], state=result.get("state"))
                return None
            if watcher.cancelled:
                if result["text"]:
                    self.journal.generated(journal_key(turn["id"]),
                                           {"late_output_discarded": True, "chars": len(result["text"])})
                self.ack(turn, turn["claim_id"], late_output=bool(result["text"]))
                return None
            if result["outcome"] == "busy":
                if self.clock.time() > self._deadline(turn):
                    self.fail(turn, turn["claim_id"], "failed", "relay_busy")
                    return None
                self.clock.sleep(BUSY_RETRY_S)
                continue
            if result["outcome"] == "done" and result["text"].strip():
                payload = adapter.reply_payload(turn, result["text"], correlation, result["usage"],
                                                agent_id=self.agent_id, runtime=self.runtime)
                self.journal.generated(journal_key(turn["id"]), payload)
                return payload
            if result["outcome"] == "done":
                self.fail(turn, turn["claim_id"], "failed", "empty_reply")      # MC answered nothing
            elif result["outcome"] == "refused":
                # Refused before any inference (relay 4xx; a runner's pre-spawn check).
                self.fail(turn, turn["claim_id"], "failed", result.get("reason") or "relay_refused")
            else:
                # A transport or stream failure after sending: MC may have
                # worked on it. Never claim it failed (review F7).
                self.fail(turn, turn["claim_id"], "uncertain", "relay_error" if result["outcome"] == "error" else "relay_stopped")
            return None

    def spawn_admission(self, turn):
        """(granted, state): a fresh dispatch admission at a runner's spawn
        boundary, inside the turn's absolute deadline."""
        if time.time() >= self._deadline(turn):
            return False, "turn_expired"
        ok, state, asked_at = self.admitted(turn, first=False)
        if not ok:
            return False, state
        if time.monotonic() - asked_at >= DISPATCH_FRESH_S:
            return False, "stale"
        return True, None

    @staticmethod
    def _deadline(turn):
        try:
            return datetime.fromisoformat(turn["expires"]).timestamp()
        except (KeyError, ValueError):
            return time.time()

    def deliver(self, turn, claim_id, saved):
        if saved.get("late_output_discarded"):
            self.fail(turn, claim_id, "failed", "late_output_discarded")
            return
        body = {**saved, **adapter.envelope(turn, claim_id)}
        try:
            status, data = self.mc.call("POST", f"/rooms/{turn['room']}/events", body, key=turn["response_key"])
        except Unavailable:
            log(event="post_unconfirmed", turn=turn["id"])      # the lease will expire; recovery is receipt-first
            return
        if status in (200, 201):
            log(event="committed", turn=turn["id"], record=data.get("result", {}).get("id"))
            if self.on_committed:
                try:
                    self.on_committed(turn)
                except Exception as error:
                    log(event="on_committed_error", kind=type(error).__name__)
            return
        log(event="post_refused", turn=turn["id"], status=status)
        if status == 409:
            # Fenced: the meeting moved on (pause, stop, removal). Acknowledge a stop request.
            _, hb = self.work.call("POST", f"/turns/{turn['id']}/heartbeat", {"claim_id": claim_id}, key=str(uuid4()))
            if (hb or {}).get("state") == "cancel_requested":
                self.ack(turn, claim_id, late_output=True)

    def recover(self, item):
        """Receipt-first recovery of an uncertain turn (ROOMS_R1.md §3.7)."""
        try:
            state, saved = peek(self.journal, journal_key(item["turn_id"]))
        except Exception:
            log(event="journal_unreadable", turn=item["turn_id"])   # uncertainty, never "nothing"
            return
        if state != "generated" or not saved:
            if item.get("disposition") in ("unresolved_started", "confirmed_absent"):
                return                       # already reported
            # Tell Records what the journal shows, so Robert's choices are honest:
            # started = MC may have answered; nothing = no inference happened.
            self.work.call("POST", f"/turns/{item['turn_id']}/reconciled",
                           {"prior_claim_id": item["prior_claim_id"], "finding": "started" if state == "started" else "nothing"},
                           key=str(uuid4()))
            log(event="reconciled", turn=item["turn_id"], finding=state or "nothing")
            return
        status, data = self.work.call("POST", f"/turns/{item['turn_id']}/recover",
                                      {"prior_claim_id": item["prior_claim_id"]}, key=str(uuid4()))
        if status != 200 or data.get("state") != "recovering":
            log(event="recover", turn=item["turn_id"], state=(data or {}).get("state"), wait=(data or {}).get("wait"))
            return
        turn = data["turn"]
        self.deliver(turn, turn["claim_id"], saved)

    def fail(self, turn, claim_id, outcome, reason):
        self.work.call("POST", f"/turns/{turn['id']}/fail", {"claim_id": claim_id, "outcome": outcome, "reason": reason},
                       key=str(uuid4()))
        log(event="turn_failed", turn=turn["id"], outcome=outcome, reason=reason)

    def ack(self, turn, claim_id, late_output):
        self.work.call("POST", f"/turns/{turn['id']}/cancel-ack", {"claim_id": claim_id, "late_output": late_output},
                       key=str(uuid4()))
        log(event="stop_acknowledged", turn=turn["id"], late_output=late_output)


class Watcher(threading.Thread):
    """Keeps the lease alive while a relay request is open and serves stop
    requests at once (ROOMS_R1.md §3.8). A plain heartbeat never authorizes."""

    def __init__(self, worker, turn, correlation):
        super().__init__(daemon=True)
        self.worker, self.turn, self.correlation = worker, turn, correlation
        self._halt = threading.Event()      # not "_stop": Thread.join() calls Thread._stop() (review F1)
        self.cancelled = False

    def run(self):
        while not self._halt.wait(HEARTBEAT_S):
            try:
                status, data = self.worker.work.call("POST", f"/turns/{self.turn['id']}/heartbeat",
                                                     {"claim_id": self.turn["claim_id"]}, key=str(uuid4()))
            except Unavailable:
                continue
            state = (data or {}).get("state")
            if state == "cancel_requested" or state in ("cancelled", "uncertain", "abandoned", "expired"):
                self.cancelled = True
                self.worker.relay.stop(self.correlation)
                return

    def stop(self):
        self._halt.set()
        self.join(timeout=5)


def main():
    env = os.environ
    journal_dir = Path(env.get("ROOMS_JOURNAL_DIR", "/journal"))
    journal_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(journal_dir / "worker.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)        # one worker per journal
    records_url = env.get("RECORDS_URL", "http://minimoi-records:18880")
    relay_url = env.get("MC_RELAY_URL", "http://mc-relay:8790")
    secrets = Path(env.get("ROOMS_SECRETS_DIR", "/run/secrets/rooms"))
    worker = Worker(Records(records_url, read_secret(secrets / "rooms-worker.token")),
                    Records(records_url, read_secret(secrets / "mc.token")),
                    Relay(relay_url, read_secret(secrets / "mc-relay.token")),
                    TurnJournal(journal_dir / "turns"), teammate=env.get("ROOMS_TEAMMATE", "mc"),
                    alive_path="/tmp/rooms-worker/alive")
    log(event="started", records=records_url, relay=relay_url, teammate=worker.teammate)
    worker.start_housekeeping()
    while True:
        try:
            busy = worker.run_once()
        except Unavailable as error:
            log(event="records_unavailable", detail=str(error))
            busy = False
        except Exception as error:      # keep serving; never print payloads or secrets
            log(event="loop_error", kind=type(error).__name__)
            busy = False
        if not busy:
            time.sleep(1)


if __name__ == "__main__":
    main()
