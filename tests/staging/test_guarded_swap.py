"""Workers resume only after a verified apply or restore. Synthetic: no Docker, no host."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("guarded_swap", Path(__file__).resolve().parents[2] / "scripts/staging/guarded_swap.py")
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)


class Boom(Exception):
    pass


class Rig:
    def __init__(self, **fail):
        self.fail, self.calls, self.records, self.alive = fail, [], {}, True
        self.lose_gate_in = fail.pop("lose_gate_in", None)

    def step(self, name):
        def run(*_):
            self.calls.append(name)
            if self.lose_gate_in == name:
                self.alive = False
            if self.fail.get(name):
                raise Boom(name)
        return run

    @contextmanager
    def gate(self):
        self.calls.append("gate_open")
        yield "held"
        self.calls.append("gate_close")
        if self.fail.get("gate_close"):
            raise Boom("gate_close")

    def go(self, record=None):
        return gs.guarded_swap(stop_workers=self.step("stop"), start_workers=self.step("start"), wait_idle=self.step("idle"),
                               gate=self.gate, gate_alive=lambda _: self.alive, apply=self.step("apply"), restore=self.step("restore"),
                               record=record or (lambda name, detail: self.records.__setitem__(name, detail)))


def test_a_verified_apply_resumes_workers_and_never_restores():
    rig = Rig()
    assert rig.go() is gs.Outcome.APPLIED
    assert rig.calls == ["stop", "idle", "gate_open", "apply", "gate_close", "start"] and "recovery_required" not in rig.records


def test_a_failed_apply_with_a_verified_restore_resumes_workers_and_still_reports_the_failure():
    rig = Rig(apply=True)
    with pytest.raises(Boom, match="apply"):
        rig.go()
    assert "restore" in rig.calls and rig.calls[-1] == "start" and rig.records["outcome"] == {"outcome": "restored"}


def test_a_failed_restore_leaves_workers_stopped_with_a_receipt():
    rig = Rig(apply=True, restore=True)
    with pytest.raises(Boom, match="apply"):
        rig.go()
    assert "start" not in rig.calls and rig.records["recovery_required"] == {"workers": "left stopped"}
    assert rig.records["restore_failed"] == {"exception": "Boom"}


def test_a_gate_lost_during_apply_does_not_restore_without_it_and_leaves_workers_stopped():
    rig = Rig(apply=True, lose_gate_in="apply")
    with pytest.raises(Boom):
        rig.go()
    assert "restore" not in rig.calls and "start" not in rig.calls and rig.records["restore_skipped"] == {"reason": "admission gate lost"}


def test_a_gate_lost_after_an_apply_that_raised_nothing_is_not_success():
    rig = Rig(lose_gate_in="apply")
    with pytest.raises(RuntimeError, match="gate lost"):
        rig.go()
    assert "start" not in rig.calls and "recovery_required" in rig.records


def test_a_gate_lost_during_restore_leaves_workers_stopped():
    rig = Rig(apply=True, lose_gate_in="restore")
    with pytest.raises(Boom):
        rig.go()
    assert "start" not in rig.calls and rig.records["restore_failed"]["exception"] == "RuntimeError"


def test_a_gate_that_fails_to_close_leaves_workers_stopped():
    rig = Rig(gate_close=True)
    with pytest.raises(Boom, match="gate_close"):
        rig.go()
    assert "start" not in rig.calls and "gate_close_failed" in rig.records


@pytest.mark.parametrize("step", ["stop", "idle"])
def test_a_refusal_before_anything_changed_resumes_workers(step):
    rig = Rig(**{step: True})
    with pytest.raises(Boom):
        rig.go()
    assert "apply" not in rig.calls and rig.calls[-1] == "start" and rig.records["outcome"] == {"outcome": "not_started"}


def test_workers_that_will_not_resume_are_reported():
    rig = Rig(start=True)
    with pytest.raises(Boom, match="start"):
        rig.go()
    assert rig.records["workers_not_resumed"] == {"exception": "Boom"}


def _full_disk(name, detail):
    raise OSError(28, "No space left on device")


def test_when_nothing_can_be_recorded_a_failed_apply_still_restores_before_workers_resume(capsys):
    rig = Rig(apply=True)
    with pytest.raises(Boom, match="apply"):
        rig.go(_full_disk)
    assert rig.calls.index("restore") < rig.calls.index("start")
    assert "could not record apply_failed" in capsys.readouterr().err


def test_when_nothing_can_be_recorded_a_failed_restore_still_leaves_workers_stopped(capsys):
    rig = Rig(apply=True, restore=True)
    with pytest.raises(Boom, match="apply"):
        rig.go(_full_disk)
    assert "start" not in rig.calls and "could not record recovery_required" in capsys.readouterr().err


def test_when_nothing_can_be_recorded_a_good_apply_still_succeeds():
    rig = Rig()
    assert rig.go(_full_disk) is gs.Outcome.APPLIED and rig.calls[-1] == "start"
