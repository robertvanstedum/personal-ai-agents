"""The §9.3 instruments the gate M controller uses (host runtime spec v0.5; S1-H). Test instruments only: nothing here
is installed outside the run's own acceptance folder, and nothing here is a provider.

* ``install_tripwire``: provider-tripwire, first on the runtime's PATH: ``claude``, ``codex``, ``grok``, ``openclaw``,
  ``litellm``, each appending one line to ``tripwire.jsonl`` (its name and pid, never its arguments) and exiting 97.
* ``planted_values``: synthetic test values for the seven planted names, made for the run, kept in memory only (LCH-6
  searches every evidence file for them at the end).
* ``Collector``: the snapshot collector, on a separate observer credential, every 10 s, through the read path the
  HTTPS endpoint serves (``snapshot.read_request``), the snapshot built live from the authority's records (gate M has
  no door; the published ``state.json`` stops when the runtime that publishes it stops).
* ``Notifier``: the notifier side, the real supervisor (services/supervisor/main.py) with fake-notifier
  (``notify.jsonl``) and the runtime link, ticked by the controller.
* ``Owner``: the owner test identity, the only path for grants, revocations and decisions in the run; every call is
  logged in the timeline with the notice that preceded it.
* ``ScriptedHost``: a second runner identity (``mac-test-9``) speaking the store as a host does, for the protocol-level
  parts of T9, T10, T11m, T11b, T15 and X5. Its items carry ``repo:scripted``, which the runtime under test lacks.
* ``egress_commands``: the pf rules and counter reads for the provider hosts; they need ``sudo`` and run only in
  Robert's gate M session, on his approval (GATE_M_OWNER_SESSION_PLAN.md).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests" / "mc_delegation_dispatcher", ROOT / "tests" / "mc_delegation_runner"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

class Stop(Exception):
    """A scenario cannot go on (a precondition failed); recorded, never retried."""


class ReconcileFirst(Stop):
    """A claim answered ``withheld`` (Codex F2): the identity has items to reconcile first. Never read as idle."""


TRIPWIRE = ("claude", "codex", "grok", "openclaw", "litellm")
PLANTED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
           "TELEGRAM_BOT_TOKEN", "SSH_AUTH_SOCK")
PROVIDER_HOSTS = ("api.anthropic.com", "api.openai.com", "api.x.ai", "chatgpt.com", "claude.ai", "statsig.anthropic.com")
OWNER_ID = "robert"


def install_tripwire(folder: Path, log: Path) -> Path:
    """Five stand-ins, each logging one line and exiting 97. Returns the folder to put first on PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in TRIPWIRE:
        p = folder / name
        p.write_text(f'#!/bin/sh\nprintf \'{{"tool":"{name}","pid":%d}}\\n\' $$ >> "{log}"\nexit 97\n')
        p.chmod(0o755)
    return folder


def tripwire_lines(log: Path) -> list:
    try:
        return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    except FileNotFoundError:
        return []


def planted_values() -> dict:
    """Synthetic values for the planted names, made for this run only; SSH_AUTH_SOCK's is a path that does not exist."""
    out = {n: "test-planted-" + secrets.token_hex(20) for n in PLANTED}
    out["SSH_AUTH_SOCK"] = "/nonexistent/test-planted-agent-" + secrets.token_hex(8)
    return out


def egress_commands(anchor="minimoi.acceptance") -> dict:
    """The pf anchor that counts (and blocks) traffic to the provider hosts, and how its counters are read. Built
    here, run only in Robert's gate M session with sudo on his approval."""
    table = " ".join(PROVIDER_HOSTS)
    rules = (f'table <providers> {{ {table} }}\n'
             f'block drop out log quick proto {{ tcp udp }} from any to <providers>\n')
    return {"rules": rules,
            "load": [f"echo '{rules.strip()}' | sudo pfctl -a {anchor} -f -", "sudo pfctl -E"],
            "read": [f"sudo pfctl -a {anchor} -vvs rules"],
            "remove": [f"sudo pfctl -a {anchor} -F all", "sudo pfctl -X <the token printed by pfctl -E>"],
            "pass": "every rule's Packets and Bytes counters are 0 at the end of the run"}


# ── the snapshot collector ──────────────────────────────────────────────────────────────────────────────────────
class Collector:
    """state every 10 s on the observer credential (R7.6-a's read path), each snapshot saved under ``snapshots/``.
    ``take(label)`` collects one now (a scenario's own observation); ``since(t)`` the ones taken after t."""

    def __init__(self, store, folder: Path, *, endpoint_id, host_id, every_s=10.0, timeline=None):
        from core.mc_delegation_dispatcher import snapshot
        self.snapshot, self.store, self.folder = snapshot, store, folder
        self.endpoint_id, self.host_id, self.every_s, self.timeline = endpoint_id, host_id, every_s, timeline
        self.token = "observer-" + secrets.token_hex(24)
        self.readers = {"collector": {"sha256": hashlib.sha256(self.token.encode()).hexdigest(), "role": "observer"}}
        self.taken, self._lock, self._stop, self._n = [], threading.Lock(), threading.Event(), 0
        self.scenario = None
        folder.mkdir(parents=True, exist_ok=True)

    def take(self, label="tick"):
        status, doc = self.snapshot.read_request(
            {"Authorization": "Bearer " + self.token}, readers=self.readers,
            provide=lambda: self.store.snapshot(endpoint_id=self.endpoint_id, host_id=self.host_id),
            note_read=self._note_read)
        with self._lock:
            self._n += 1
            entry = {"n": self._n, "at": time.time(), "scenario": self.scenario, "label": label, "status": status,
                     "doc": doc}
            self.taken.append(entry)
        name = f"{self._n:05d}-{self.scenario or 'run'}-{label}.json"
        (self.folder / name).write_text(json.dumps({k: v for k, v in entry.items()}, sort_keys=True, default=str))
        return entry

    def _note_read(self, reader, role):
        try:
            self.store.note_read(reader, role)
        except Exception:
            pass                                        # the authority unreachable: the read itself answers 503

    def since(self, t) -> list:
        with self._lock:
            return [e for e in self.taken if e["at"] >= t]

    def start(self):
        def loop():
            while not self._stop.wait(self.every_s):
                try:
                    self.take()
                except Exception:
                    pass
        threading.Thread(target=loop, name="collector", daemon=True).start()

    def stop(self):
        self._stop.set()


# ── the notifier side: the real supervisor with fake-notifier ──────────────────────────────────────────────────
class _GitHub:
    def open_prs(self):
        return []

    def checks(self, sha):
        return []

    def runs(self):
        return []


class Notifier:
    """services/supervisor/main.py's Supervisor, unchanged, over the run's own database, notifying into
    ``notify.jsonl`` (fake-notifier) and linked to the authority (core/supervision/runtime.py). The controller ticks
    it every ``every_s`` on a thread: decisions out, answers in, and the check-in every ``checkin_s``."""

    def __init__(self, connect, authority_store, path: Path, *, every_s=2.0, checkin_s=15):
        from core.supervision.runtime import RuntimeLink
        from core.supervision.store import Store
        from fake_notifier import OWNER, FakeNotifier
        from services.supervisor import main as supervisor_main
        from services.supervisor.main import Supervisor
        supervisor_main.HEALTH = Path(path).with_name("supervisor.alive")   # its liveness file, in the evidence
        self.bot = FakeNotifier(path)
        self.link = RuntimeLink(authority_store, owner=OWNER_ID, view_bound_s=60)
        self.sup = Supervisor(Store(connect), self.bot, _GitHub(), chat_id=OWNER, user_id=OWNER,
                              checkin_minutes=checkin_s / 60, runtime=self.link)
        self.every_s, self._stop, self._lock, self.errors = every_s, threading.Event(), threading.Lock(), []

    def tick(self):
        """The supervisor's own tick, unchanged (decisions out, answers in, the check-in when due)."""
        with self._lock:
            try:
                self.sup.tick()
            except Exception as exc:
                self.errors.append((time.time(), type(exc).__name__))

    def start(self):
        def loop():
            while not self._stop.wait(self.every_s):
                self.tick()
        threading.Thread(target=loop, name="notifier", daemon=True).start()

    def stop(self):
        self._stop.set()

    def lines(self) -> list:
        return self.bot.lines()


# ── the owner test identity ─────────────────────────────────────────────────────────────────────────────────────
class Owner:
    """The only path for grants, revocations and decisions in the run (§9.3). Each call goes to the timeline with what
    preceded it: the admission is the owner's go; any other call names the notice it answers (OWN-3)."""

    def __init__(self, run):
        self.run = run

    def _log(self, what, **fields):
        self.run.log("owner", what, **fields)

    def _grant(self, conn_grants, definition):
        with self.run.connect() as conn:
            conn_grants.issue(conn, definition)

    def item(self, job, brief, *, amount=0.05, route="fakework", scope="build", actor="fakework",
             repository="personal-ai-agents", ref=None, ttl_s=3600, required_capabilities=None, deadline_s=1800):
        """An exact approval of one item (delegate.start_build), issued with the owner's turn, then admitted."""
        from core.mc_delegation_dispatcher.store import item_subject_digest
        from core.minimoi_actions.grants import GrantDefinition
        ref = ref or self.run.ref
        caps = list(required_capabilities or [])
        subject = item_subject_digest(actor=actor, scope=scope, repository=repository, ref=ref, brief=brief,
                                      route=route, amount=amount, **({"required_capabilities": caps} if caps else {}))
        gid, now = str(uuid.uuid4()), self.run.db_now()
        cap = "delegate.start_build" if scope == "build" else "delegate.start_research"
        self._grant(self.run.grants, GrantDefinition(
            gid, OWNER_ID, "dev", "conversation", (f"owner-turn:{job}",), ("mc",), "policy", cap, 1, (subject,), 1,
            now + timedelta(seconds=ttl_s), proposal_id=str(uuid.uuid4()), approved_operation_id=str(uuid.uuid4()),
            approval_expires_at=now + timedelta(seconds=min(ttl_s, 600))))
        got = self.run.store.enqueue(job_id=job, actor=actor, scope=scope, repository=repository, ref=ref, brief=brief,
                                     cost_cap_usd=1.0, deadline_s=deadline_s, route=route, amount=amount,
                                     grant_id=gid, **({"required_capabilities": caps} if caps else {}))
        self._log("approve_item", job=job, grant_id=gid, route=route, amount=amount, answer=got, go=True)
        return gid

    def effect_grant(self, job, *keys, ttl_s=3600):
        """The exact approval of an item's effects on the sink (fakework.sink_post), one operation per key."""
        from core.mc_delegation_dispatcher.store import EFFECT_POLICY
        from core.mc_delegation_runner import fakework
        from core.minimoi_actions.grants import GrantDefinition
        from core.minimoi_actions.requests import ToolRequest
        gid = str(uuid.uuid4())
        digests = tuple(ToolRequest(fakework.OPERATION, 1, json.dumps({"op": k}).encode()).arguments_sha256 for k in keys)
        self._grant(self.run.grants, GrantDefinition(gid, OWNER_ID, "dev", job, (f"owner-turn:{job}:effects",),
                                                     ("fakework",), EFFECT_POLICY, fakework.OPERATION, 1, digests,
                                                     len(keys), self.run.db_now() + timedelta(seconds=ttl_s),
                                                     work_id=job))
        self._log("approve_effects", job=job, grant_id=gid, keys=list(keys), go=True)
        return gid

    def card(self, doc):
        now = self.run.db_now()
        got = self.run.store.admit_card(doc, grant_id=str(uuid.uuid4()), owner_turn_ids=(f"owner-turn:{doc['card_id']}",),
                                        proposal_id=str(uuid.uuid4()), approved_operation_id=str(uuid.uuid4()),
                                        approval_expires_at=now + timedelta(seconds=300), by=OWNER_ID,
                                        note="the card as approved")
        self._log("approve_card", card_id=doc["card_id"], grant_id=got.get("grant_id"), go=True)
        return got

    def revoke(self, grant_id, *, after_notice=None):
        got = self.run.store.revoke(grant_id, by=OWNER_ID, note="acceptance: revoked mid-run")
        self._log("revoke", grant_id=grant_id, answer=got.get("state"), after_notice=after_notice)
        return got

    def stop(self, job, *, after_notice=None):
        got = self.run.store.stop(job)
        self._log("stop", job=job, answer=got, after_notice=after_notice)
        return got

    def end(self, job, *, after_notice):
        row = self.run.store.by_job(job)
        got = self.run.store.end(str(row["request_id"]), by=OWNER_ID, note="acceptance: ended after its notice")
        self._log("end", job=job, answer=got.get("state"), after_notice=after_notice)
        return got

    def resolve_usage_hold(self, route, *, after_notice):
        got = self.run.store.resolve_usage_hold(route, note="acceptance: the unknown usage is recorded", by=OWNER_ID)
        self._log("resolve_usage_hold", route=route, answer=got, after_notice=after_notice)
        return got

    def merge(self, card_id, *, after_notice):
        got = self.run.store.record_owner_act(card_id, "merge", by=OWNER_ID, note="acceptance: merged after ready")
        self._log("merge", card_id=card_id, answer=got.get("state"), after_notice=after_notice)
        return got

    def authorize_review(self, request_id, *, timeout=90):
        """MC asks to review an item (``propose_review``); Robert's "yes" to that decision's notice, through the real
        Telegram path, authorizes it. -> the owner trigger reserve_judgment verifies: {"kind": "owner",
        "decision_id"} (S1-G SF4). Each trigger is used once."""
        did = self.run.store.propose_review(request_id, by="mc")["decision_id"]
        self.run.log("mc", "asks to review an item (propose_review)", item=request_id, decision_id=did)

        def message():
            rows = self.run.sql("""SELECT d.telegram_message_id, m.message_id FROM guild.supervision_decisions d
                LEFT JOIN guild.supervision_decision_messages m ON m.decision_id = d.id
                WHERE d.request_key = %s""", ("rt-" + did,))
            return rows and (rows[0]["message_id"] or rows[0]["telegram_message_id"])
        mid = self.run.wait(message, timeout, every=1)
        if not mid:
            raise Stop("review_request_never_delivered")
        self.reply(mid, "yes", after_notice=mid)
        done = self.run.wait(lambda: (self.run.store.decisions_status([did])[did] or {}).get("action")
                             == "authorize_review", timeout, every=1)
        if not done:
            raise Stop("review_request_never_authorized")
        return {"kind": "owner", "decision_id": did}

    def reply(self, message_id, text, *, after_notice):
        """Robert's own short reply to one notice message, through the real Telegram receipt path."""
        self.run.notifier.bot.reply(text, message_id)
        self._log("reply", message_id=message_id, text=text, after_notice=after_notice)


# ── a second runner identity ────────────────────────────────────────────────────────────────────────────────────
class ScriptedHost:
    """``mac-test-9``: a runner identity the controller speaks for, through the same store calls a host makes (the
    door's shapes, version 2). It runs nothing; it reports what a scenario needs it to."""

    def __init__(self, run, host_id="mac-test-9", *, roles=("worker", "witness"), repos=("scripted",)):
        self.run, self.host_id, self.runner_id = run, host_id, f"{host_id}:runner"
        self.roles = list(roles)
        self.caps = [f"repo:{r}" for r in repos] + ["scope:build", "scope:research", "actor:fakework",
                                                    "host:mac-local"]

    def facts(self, **over):
        return {"host_id": self.host_id, "bundle_digest": self.run.bundle_digest, "roles": self.roles,
                "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online", **over}

    def beat(self, **over):
        return self.run.store.heartbeat(self.runner_id, "online", self.caps, protocol=2, facts=self.facts(**over))

    def claim(self):
        """One claim as a host makes it after Codex F2: a fresh request id and the inventory of what this identity
        holds. -> the claim (``claimed``), or None (``none``). ``withheld`` raises ReconcileFirst (never read as
        idle); ``refused`` raises Stop; ``claim_superseded`` asks once more under a new id."""
        self.beat()
        held = self.run.held.setdefault(self.runner_id, [])
        for _ in range(2):
            got = self.run.store.claim(self.runner_id, self.caps, protocol=2, claim_id=str(uuid.uuid4()),
                                       held=list(held[-64:]))
            if got["state"] == "claimed":
                env = got["envelope"]
                held.append(f"{env['request_id']}:{env['generation']}")
                return got
            if got["state"] == "none":
                return None
            if got["state"] == "withheld":
                raise ReconcileFirst(f"withheld:{got.get('code')}:{len(got.get('items') or [])}")
            if got["state"] != "claim_superseded":
                raise Stop(f"claim_{got['state']}:{got.get('code')}")
        raise Stop("claim_superseded_twice")

    def keep_alive(self, env, *, every):
        """Renew the item's lease every ``every`` s on a thread, as a host does while it works. -> the Event that
        stops it."""
        stop = threading.Event()

        def loop():
            while not stop.wait(every):
                try:
                    self.run.store.renew(env["request_id"], env["generation"], protocol=2, runner_id=self.runner_id)
                except Exception:
                    pass
        threading.Thread(target=loop, name=f"renew-{self.host_id}", daemon=True).start()
        return stop

    def progress(self, env, name, *, event_id=None, at=None):
        cp = {"name": name, "digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(), "paths": [f"x/{name}"]}
        eid = event_id or str(uuid.uuid4())
        at = at or self.run.db_now().isoformat()
        got = self.run.store.progress(env["request_id"], env["generation"], "executing", protocol=2,
                                      runner_id=self.runner_id, event_id=eid, at=at, checkpoint=cp)
        return got, eid, at

    def result(self, env, *, status="completed", amount=0.0, unit="test_units", route="fakework", detail=None,
               artifacts=(), event_id=None):
        usage = {"amount": amount, "unit": unit, "children_stopped": True}
        return self.run.store.result(env["request_id"], env["generation"], event_id=event_id or str(uuid.uuid4()),
                                     status=status, artifacts=list(artifacts),
                                     detail=detail if detail is not None else {"exit_code": 0}, output_text="done",
                                     protocol=2, runner_id=self.runner_id, at=self.run.db_now().isoformat(),
                                     route=route, usage=usage)

    def effect(self, env, op, key, grant_id, **extra):
        from core.mc_delegation_runner import fakework
        eff = {"op": op, "key": key, "grant_id": grant_id, "request": fakework.effect_request(key), **extra}
        return self.run.store.effect(self.runner_id, env["request_id"], env["generation"], eff, protocol=2)

    def reconcile(self, env, *, child="stopped", checkpoint=None, open_operations=()):
        return self.run.store.reconcile(self.runner_id, env["request_id"], env["generation"],
                                        {"child": child, "checkpoint": checkpoint}, list(open_operations), protocol=2)
