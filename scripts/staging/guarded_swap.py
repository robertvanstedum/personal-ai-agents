"""The safety order of a Dev deploy that replaces MC's runtime, kept apart from what it replaces so it can be tested.

Workers resume only after a verified result: the new state, or the old state fully restored and checked. Anything less
leaves them stopped and writes a recovery-required receipt, because dispatching against an unverified runtime is worse
than waiting. The caller's ``apply`` and ``restore`` must raise unless their result is verified.
"""
from __future__ import annotations

from enum import Enum
import sys


class Outcome(str, Enum):
    APPLIED = "applied"
    RESTORED = "restored"
    NOT_STARTED = "not_started"                     # refused before anything live changed
    RECOVERY_REQUIRED = "recovery_required"


def guarded_swap(*, stop_workers, start_workers, wait_idle, gate, gate_alive, apply, restore, record) -> Outcome:
    """Run one swap. Re-raises the failure that stopped it. Whether anything could be recorded (a full disk, say) never
    changes what happens next: a restore still runs, and workers still stay stopped."""
    outcome, failure = Outcome.NOT_STARTED, None
    given_record = record

    def record(name, detail):
        try:
            given_record(name, detail)
        except BaseException as exc:                # last place left to say it, which may be on the same full disk
            try:
                print(f"guarded_swap: could not record {name} {detail}: {type(exc).__name__}", file=sys.stderr, flush=True)
            except BaseException:
                pass

    try:
        stop_workers()                              # a partial stop is still only stopped workers: nothing live changed yet
        wait_idle()
        with gate() as held:
            outcome = Outcome.RECOVERY_REQUIRED     # from here live state may change: only a verified result lifts this
            try:
                apply(held)
                if not gate_alive(held):
                    raise RuntimeError("admission gate lost during apply")
                outcome = Outcome.APPLIED
            except BaseException as exc:
                failure = exc
                record("apply_failed", {"exception": type(exc).__name__})
                if not gate_alive(held):
                    record("restore_skipped", {"reason": "admission gate lost"})
                else:
                    try:
                        restore(held)
                        if not gate_alive(held):
                            raise RuntimeError("admission gate lost during restore")
                        outcome = Outcome.RESTORED
                    except BaseException as restore_exc:
                        record("restore_failed", {"exception": type(restore_exc).__name__})
    except BaseException as exc:
        if failure is None:
            failure = exc
        if outcome in (Outcome.APPLIED, Outcome.RESTORED):      # the gate failed to close after a good result: not verified
            outcome = Outcome.RECOVERY_REQUIRED
            record("gate_close_failed", {"exception": type(exc).__name__})
    if outcome is Outcome.RECOVERY_REQUIRED:
        record("recovery_required", {"workers": "left stopped"})
    else:
        try:
            start_workers()
        except BaseException as exc:
            record("workers_not_resumed", {"exception": type(exc).__name__})
            failure = failure or exc
    record("outcome", {"outcome": outcome.value})
    if failure is not None:
        raise failure
    return outcome
