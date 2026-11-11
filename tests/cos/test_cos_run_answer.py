"""CoS keeps her answer, not the narration between tool steps (Dev, 11 Nov 2026). The plugin reports a run's final step;
the listener keeps it for that exact run; the reply keeps it only when the stream ends with it."""
from domains.cos.backends.openclaw_backend import answer_text
from tests.cos.test_cos_tool_listener import RUN, live, rig, sealed  # noqa: F401  (fixture)


def test_the_final_step_is_kept_for_its_own_run_only(rig):
    client, bindings, _ = rig
    live(bindings)
    r = client.post("/internal/cos/v1/answer", json={"text": "The answer."}, headers=sealed())
    assert r.status_code == 200 and r.json["status"] == "kept"
    assert bindings.answer_for_run(RUN.format(1)) == "The answer."
    assert bindings.answer_for_run(RUN.format(2)) is None


def test_a_bad_or_unsealed_report_is_refused(rig):
    client, bindings, _ = rig
    live(bindings)
    for body in ({"text": ""}, {"text": "x", "extra": 1}, {"text": 5}, {"text": "x" * 40_001}):
        assert client.post("/internal/cos/v1/answer", json=body, headers=sealed()).status_code == 400
    assert client.post("/internal/cos/v1/answer", json={"text": "x"}, headers={"Authorization": "Bearer nope"}).status_code in (401, 403)
    assert bindings.answer_for_run(RUN.format(1)) is None


def test_the_reply_keeps_the_final_step_only_when_the_stream_ends_with_it():
    streamed = "The helper returned a result. I'll review it first.CoS reviewed the result: three items."
    assert answer_text(streamed, "CoS reviewed the result: three items.") == "CoS reviewed the result: three items."
    assert answer_text(streamed, "Something the page never saw.") == streamed
    assert answer_text(streamed, None) == streamed
    assert answer_text("Just the answer.", "Just the answer.") == "Just the answer."


def test_the_reply_waits_for_the_report_only_on_a_host_that_records_run_ends(monkeypatch):
    from domains.cos.backends import openclaw_backend as ob
    reports = iter([(None, False, True), (None, False, True), ("Final.", False, True)])

    class Binder:
        def run_report(self, run_id):
            return next(reports)
    backend = ob.OpenClawBackend.__new__(ob.OpenClawBackend)
    backend._binder = Binder()
    monkeypatch.setattr(ob.time, "sleep", lambda s: None)
    assert backend._run_answer(RUN.format(1)) == "Final."

    class NoReceipts:
        calls = 0
        def run_report(self, run_id):
            NoReceipts.calls += 1
            return (None, False, False)
    backend._binder = NoReceipts()
    assert backend._run_answer(RUN.format(1)) is None and NoReceipts.calls == 1      # no waiting where nothing reports

    class Ended:
        def run_report(self, run_id):
            return (None, True, True)
    backend._binder = Ended()
    assert backend._run_answer(RUN.format(1)) is None                                 # the run ended with no answer report
