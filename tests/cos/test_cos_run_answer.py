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
