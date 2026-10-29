"""``fakework/1``: the deterministic test worker (host runtime spec v0.5 §6.5, §9.3), the only adapter acceptance runs.

It is started through the runner's ordinary child path (barrier, process group, stage files, allowlisted
environment), with no provider SDK and no provider environment. It opens nothing but the runtime's local channel (a
private folder of request and answer files beside the task's artifacts) and the effect sink. Its program is the item's
brief:

    fakework/1
    checkpoint <name> [hold_s=<n>] [paths=<p>,<p>]   write fakework/<name>.txt, report the checkpoint, hold n s
    effect <key> grant=<uuid>                        prepare, claim and dispatch through the runtime, post to the
                                                     sink, report the outcome (an effect already done is never sent)
    hang_s <n>                                       stall n s (no progress, no channel)
    crash: self                                      exit 70
    report_usage <amount>|unknown                    report usage in the route's unit (capped at --cap)
    stale_effect_on_wake <key> grant=<uuid> [wait_s=<n>] [gap_s=<n>]
                                                     hold a receipt lease; on noticing its own wake gap (a pause of
                                                     more than gap_s), dispatch with that old lease
    findings [<word>]                                a reviewer's output, recorded as a findings artifact
    resume_session <id>                              no-op: fakework keeps no sessions (the runtime refuses the
                                                     do-not-resume ids before any spawn, R6.4)
    result ok                                        exit 0
    spawn ...                                        refused before any spawn: the census is S1-F's

``--from <checkpoint>`` restarts after that checkpoint (a resume or a retry). Exit codes: 0 done, 64 program refused,
65 cap reached, 66 channel lost, 69 effect refused, 70 crash, 75 stopped at a checkpoint on the runtime's request.

``FileSink`` is the effect sink's file form (§9.3's rules: one ``committed`` per operation id, ``duplicate`` for the
same digest again, ``operation_reused`` for another, ``lease_expired`` for a send after its receipt lease; readback, or
``unsupported`` with lookup off). The HTTP effect-sink service is S1-H's.

Stdlib only; Python 3.9-compatible. Run as a file: ``python -I fakework.py ...``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROGRAM = "fakework/1"
OPERATION = "fakework.sink_post"            # the sink's effect capability (catalogue row, test environments only)
EXIT = {"done": 0, "refused": 64, "cap": 65, "channel": 66, "effect": 69, "crash": 70, "stopped": 75}
_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/@+-]{1,256}$")
_WORD = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,99}$")
_UNSUPPORTED = ("spawn",)                   # the census (S1-F)
POLL_S = 0.02


class ProgramRefused(ValueError):
    pass


def _int(value, lo, hi, code):
    if not re.match(r"^[0-9]{1,7}$", value or "") or not lo <= int(value) <= hi:
        raise ProgramRefused(code)
    return int(value)


def _opts(parts, allowed, code):
    out = {}
    for p in parts:
        k, sep, v = p.partition("=")
        if not sep or k not in allowed or k in out:
            raise ProgramRefused(code)
        out[k] = v
    return out


def parse_program(text) -> list:
    """The steps of a ``fakework/1`` program, strictly; ProgramRefused with a fixed code otherwise."""
    if type(text) is not str:
        raise ProgramRefused("fakework_program_invalid")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if not lines or lines[0] != PROGRAM:
        raise ProgramRefused("fakework_program_invalid")
    steps, names = [], set()
    for ln in lines[1:]:
        word, *rest = ln.split()
        if word in _UNSUPPORTED:
            raise ProgramRefused(f"fakework_step_unsupported:{word}")
        if word == "checkpoint":
            if not rest or not _NAME.match(rest[0]) or rest[0] in names:
                raise ProgramRefused("fakework_checkpoint_invalid")
            o = _opts(rest[1:], ("hold_s", "paths"), "fakework_checkpoint_invalid")
            paths = o["paths"].split(",") if "paths" in o else [f"fakework/{rest[0]}.txt"]
            if not all(_PATH.match(p) for p in paths) or len(paths) > 50:
                raise ProgramRefused("fakework_checkpoint_invalid")
            names.add(rest[0])
            steps.append(("checkpoint", {"name": rest[0], "hold_s": _int(o.get("hold_s", "0"), 0, 86400,
                                                                         "fakework_checkpoint_invalid"),
                                         "paths": paths}))
        elif word in ("effect", "stale_effect_on_wake"):
            if not rest or not _NAME.match(rest[0]):
                raise ProgramRefused("fakework_effect_invalid")
            allowed = ("grant",) if word == "effect" else ("grant", "wait_s", "gap_s")
            o = _opts(rest[1:], allowed, "fakework_effect_invalid")
            if not _UUID.match(o.get("grant", "")):
                raise ProgramRefused("fakework_effect_invalid")
            step = {"key": rest[0], "grant": o["grant"]}
            if word == "stale_effect_on_wake":
                step.update(wait_s=_int(o.get("wait_s", "60"), 0, 86400, "fakework_effect_invalid"),
                            gap_s=_int(o.get("gap_s", "30"), 1, 86400, "fakework_effect_invalid"))
            steps.append((word, step))
        elif word == "hang_s":
            if len(rest) != 1:
                raise ProgramRefused("fakework_hang_invalid")
            steps.append(("hang_s", {"seconds": _int(rest[0], 0, 86400, "fakework_hang_invalid")}))
        elif word == "crash:":
            if rest != ["self"]:
                raise ProgramRefused("fakework_crash_invalid")
            steps.append(("crash", {}))
        elif word == "report_usage":
            if len(rest) != 1 or not (rest[0] == "unknown" or re.match(r"^[0-9]{1,6}(\.[0-9]{1,4})?$", rest[0])):
                raise ProgramRefused("fakework_usage_invalid")
            steps.append(("report_usage", {"amount": None if rest[0] == "unknown" else float(rest[0])}))
        elif word == "findings":
            if len(rest) > 1 or (rest and not _WORD.match(rest[0])):
                raise ProgramRefused("fakework_findings_invalid")
            steps.append(("findings", {"word": rest[0] if rest else "none"}))
        elif word == "resume_session":
            if len(rest) != 1 or not _SESSION.match(rest[0]):
                raise ProgramRefused("fakework_resume_invalid")
            steps.append(("resume_session", {"id": rest[0].lower()}))
        elif word == "result":
            if rest != ["ok"]:
                raise ProgramRefused("fakework_result_invalid")
            steps.append(("result", {}))
        else:
            raise ProgramRefused("fakework_step_unknown")
    if any(k == "result" for k, _ in steps[:-1]):
        raise ProgramRefused("fakework_result_not_last")
    return steps


def sessions(text) -> list:
    """The session ids a program would resume (the runtime checks them against do_not_resume before any spawn)."""
    return [s["id"] for k, s in parse_program(text) if k == "resume_session"]


def effect_request(key) -> dict:
    """The exact request an ``effect <key>`` makes: what the effect grant's argument digest names."""
    return {"operation": OPERATION, "operation_version": 1, "arguments": {"op": key}}


def effect_digest(key) -> str:
    return hashlib.sha256(json.dumps({"op": key}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _write_atomic(path: Path, doc: dict):
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ── the runtime's local channel: one request file, one answer file, numbered ─────────────────────────────────────────
class Channel:
    """The runner's side. ``requests()`` yields (seq, doc) not yet answered, in order; ``answer`` writes the reply."""
    REQ = re.compile(r"^req-([0-9]{6})\.json$")

    def __init__(self, path):
        self.path = Path(path)

    def open(self):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)

    def requests(self) -> list:
        out = []
        try:
            names = sorted(os.listdir(self.path))
        except FileNotFoundError:
            return out
        for name in names:
            m = self.REQ.match(name)
            if m and not (self.path / f"ack-{m.group(1)}.json").exists():
                try:
                    doc = json.loads((self.path / name).read_text())
                except (OSError, ValueError):
                    doc = None
                out.append((int(m.group(1)), doc))
        return out

    def answer(self, seq: int, doc: dict):
        _write_atomic(self.path / f"ack-{seq:06d}.json", doc)

    def request_stop(self):
        """G4 and the offline hold: ask the worker to stop before its next step (it exits 75)."""
        if self.path.exists():
            _write_atomic(self.path / "stop", {"stop": True})


class _Client:
    def __init__(self, path):
        self.path, self.seq, self.gap = Path(path), 0, 0.0

    def stopped(self) -> bool:
        return (self.path / "stop").exists()

    def ask(self, doc: dict) -> dict:
        self.seq += 1
        last = time.time()
        _write_atomic(self.path / f"req-{self.seq:06d}.json", doc)
        ack = self.path / f"ack-{self.seq:06d}.json"
        self.gap = 0.0                # the longest pause of this process, anywhere, while it waited for the answer
        while not ack.exists():
            if self.stopped():
                _exit("stopped", "stopped at the runtime's request")
            time.sleep(POLL_S)
            now = time.time()
            self.gap, last = max(self.gap, now - last), now
        self.gap = max(self.gap, time.time() - last)
        try:
            return json.loads(ack.read_text())
        except (OSError, ValueError):
            _exit("channel", "channel answer unreadable")


def _exit(kind, line):
    print(f"fakework: {line}", flush=True)
    sys.exit(EXIT[kind])


def _wait(client, seconds, *, gap_s=None) -> float:
    """Sleep ``seconds`` in small steps, stopping early on the runtime's stop request. With ``gap_s``, return as soon
    as this process notices its own wake: more than ``gap_s`` of wall-clock time between two consecutive looks at the
    clock, wherever in the loop the pause fell (a sleep, or a stop by anyone); 0 otherwise."""
    end = time.monotonic() + seconds
    last = time.time()
    while True:
        if client.stopped():
            _exit("stopped", "stopped at the runtime's request")
        now = time.time()
        if gap_s is not None and now - last > gap_s:
            return now - last
        last = now
        if time.monotonic() >= end:
            return 0.0
        time.sleep(POLL_S)


def _send(sink, ack, key) -> str:
    return sink.post(ack["operation_id"], ack["item_id"], ack["generation"], ack["lease_until"], effect_digest(key))


def run(steps, *, channel, sink, cap, unit, start_after=None) -> int:
    client = _Client(channel)
    if start_after is not None:
        names = [s["name"] for k, s in steps if k == "checkpoint"]
        if start_after not in names:
            _exit("refused", "fakework_from_unknown")
        cut = next(i for i, (k, s) in enumerate(steps) if k == "checkpoint" and s["name"] == start_after)
        steps = steps[cut + 1:]
        print(f"fakework: restarting after checkpoint {start_after}", flush=True)
    for kind, s in steps:
        if client.stopped():
            _exit("stopped", "stopped at the runtime's request")
        if kind == "checkpoint":
            for p in s["paths"]:
                target = Path.cwd() / p
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"{s['name']}\n")
            ack = client.ask({"kind": "checkpoint", "name": s["name"], "paths": s["paths"]})
            print(f"checkpoint {s['name']}: {ack.get('state')}", flush=True)
            if s["hold_s"]:
                _wait(client, s["hold_s"])
        elif kind == "effect":
            ack = client.ask({"kind": "effect_begin", "key": s["key"], "grant": s["grant"]})
            if ack.get("state") == "done":
                print(f"effect {s['key']}: already {ack.get('receipt_state')}, not sent again", flush=True)
                continue
            if ack.get("state") != "ok":
                _exit("effect", f"effect {s['key']} refused: {ack.get('code')}")
            answer = _send(sink, ack, s["key"])
            end = client.ask({"kind": "effect_end", "key": s["key"], "answer": answer})
            print(f"effect {s['key']}: {answer} ({end.get('state')})", flush=True)
            if answer not in ("committed", "duplicate"):
                _exit("effect", f"effect {s['key']} not done: {answer}")    # a definite failure, never a success
        elif kind == "stale_effect_on_wake":
            ack = client.ask({"kind": "effect_hold", "key": s["key"], "grant": s["grant"]})
            if ack.get("state") != "held":
                print(f"stale {s['key']}: not held ({ack.get('state')} {ack.get('code')})", flush=True)
                continue
            # The lease is held from the moment the runtime claimed it: a pause while the answer was on its way counts.
            gap = client.gap if client.gap > s["gap_s"] else _wait(client, s["wait_s"], gap_s=s["gap_s"])
            if not gap:
                print(f"stale {s['key']}: no wake observed", flush=True)
                continue
            late = client.ask({"kind": "effect_dispatch_held", "key": s["key"]})
            if late.get("state") != "ok":
                print(f"stale {s['key']}: refused {late.get('code')} after a {int(gap)} s gap", flush=True)
                continue
            answer = _send(sink, late, s["key"])
            client.ask({"kind": "effect_end", "key": s["key"], "answer": answer})
            print(f"stale {s['key']}: {answer}", flush=True)
        elif kind == "hang_s":
            time.sleep(s["seconds"])
        elif kind == "crash":
            _exit("crash", "crash: self")
        elif kind == "report_usage":
            amount = s["amount"]
            over = amount is not None and amount > cap
            client.ask({"kind": "usage", "amount": cap if over else amount})
            print(f"usage {amount if amount is not None else 'unknown'} {unit}", flush=True)
            if over:
                _exit("cap", f"cap reached: {cap} {unit}")
        elif kind == "findings":
            ack = client.ask({"kind": "findings", "text": f"findings {s['word']}\n"})
            print(f"findings: {ack.get('sha256')}", flush=True)
        elif kind == "resume_session":
            print(f"resume_session {s['id']}: fakework keeps no sessions", flush=True)
        elif kind == "result":
            print("result ok", flush=True)
            return EXIT["done"]
    return EXIT["done"]


class FileSink:
    """The effect sink as a locked file (§9.3; R4.5-b: the target deduplicates by operation id and refuses a send
    after its receipt lease)."""

    def __init__(self, path, lookup=True):
        self.path, self.lookup = Path(path), lookup

    def _locked(self):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path / "effects.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def lines(self) -> list:
        f = self.path / "effects.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def post(self, operation_id, item_id, generation, lease_until, digest, now=None) -> str:
        fd = self._locked()
        try:
            done = [x for x in self.lines() if x["operation_id"] == operation_id and x["answer"] == "committed"]
            now = now or _now()
            try:
                expired = lease_until is None or now >= datetime.fromisoformat(str(lease_until).replace("Z", "+00:00"))
            except ValueError:
                expired = True
            if done:
                answer = "duplicate" if done[0]["digest"] == digest else "operation_reused"
            elif expired:
                answer = "lease_expired"
            else:
                answer = "committed"
            line = {"operation_id": operation_id, "item_id": item_id, "generation": generation,
                    "lease_until": lease_until, "digest": digest, "answer": answer, "at": now.isoformat()}
            with open(self.path / "effects.jsonl", "a") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return answer
        finally:
            os.close(fd)

    def get(self, operation_id) -> str:
        """Readback: 'committed', 'absent', or 'unsupported' when lookup is off (``--no-lookup``)."""
        if not self.lookup:
            return "unsupported"
        fd = self._locked()
        try:
            return "committed" if any(x["operation_id"] == operation_id and x["answer"] == "committed"
                                      for x in self.lines()) else "absent"
        finally:
            os.close(fd)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="fakework")
    p.add_argument("--program", required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--sink")
    p.add_argument("--no-lookup", action="store_true")
    p.add_argument("--cap", type=float, required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--from", dest="start_after")
    a = p.parse_args(argv)
    try:
        steps = parse_program(a.program)
    except ProgramRefused as exc:
        _exit("refused", str(exc))
    sink = FileSink(a.sink, lookup=not a.no_lookup) if a.sink else None
    if sink is None and any(k in ("effect", "stale_effect_on_wake") for k, _ in steps):
        _exit("refused", "fakework_sink_missing")
    return run(steps, channel=a.channel, sink=sink, cap=a.cap, unit=a.unit, start_after=a.start_after)


if __name__ == "__main__":
    sys.exit(main())
