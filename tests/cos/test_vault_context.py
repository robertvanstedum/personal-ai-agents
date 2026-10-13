from types import SimpleNamespace

import pytest

from domains.cos.vault_context import UNAVAILABLE, call_with_vault


class Backend:
    def call_backend(self, prompt, context, policy):
        self.input = (prompt, context.copy(), policy)
        context["runtime_route"] = {"served_model": "actual-model"}
        return "The agent's answer."


CONFIG = {"MINIMOI_VAULT_ASK_URL": "https://ask.example", "MINIMOI_VAULT_ASK_TOKEN": "private-token"}
HIT = {"file": "decision.md", "lines": [1, 3], "passage": "Earlier decision", "speaker": "human", "provenance": "original"}


@pytest.mark.parametrize("environ,record_mode", [({}, "on_record"), (CONFIG, "off_record")])
def test_disabled_and_private_turns_do_not_query_or_change_the_prompt(environ, record_mode):
    backend = Backend()
    context = {"system_prompt": "Standing instructions", "record_mode": record_mode}

    def forbidden(_, **kwargs):
        pytest.fail("retrieval must not run")

    assert call_with_vault(backend, "Owner's words", context, {}, retrieve=forbidden, environ=environ) == "The agent's answer."
    assert backend.input[0] == "Owner's words"
    assert backend.input[1]["system_prompt"] == "Standing instructions"


def test_evidence_and_retained_citations_reach_the_same_agent_without_replacing_owner_text():
    backend = Backend()
    context = {"system_prompt": "Standing instructions"}
    built = SimpleNamespace(mark_synthesis=lambda text: text + " [checked retained ranges]", report={"passages_prepared": 1})
    result = SimpleNamespace(prompt_block="BEGIN SOURCE DATA\nEarlier decision\nEND SOURCE DATA", built=built,
                             report={"hook_status": "ok"})
    seen = []
    original_call = backend.call_backend
    def source_answer(*args):
        original_call(*args)
        return "[VAULT_SYNTHESIS]The earlier decision was recorded.[/VAULT_SYNTHESIS]"
    backend.call_backend = source_answer
    reply = call_with_vault(backend, "Why did we decide?", context, {},
                           retrieve=lambda query, **kwargs: (seen.append(query), result)[1], environ=CONFIG)
    assert seen == ["Why did we decide?"]
    assert backend.input[0] == "Why did we decide?"
    assert "Standing instructions" in backend.input[1]["system_prompt"]
    assert "Earlier decision" in backend.input[1]["system_prompt"]
    assert context["runtime_route"]["served_model"] == "actual-model"
    assert reply.endswith("[checked retained ranges]")


def test_broken_retrieval_keeps_the_answer_and_explains_missing_evidence_without_secrets():
    backend = Backend()
    context = {"system_prompt": "Standing instructions"}

    def broken(_, **kwargs):
        raise ValueError("secret private-token URL or query")

    assert call_with_vault(backend, "Owner's words", context, {}, retrieve=broken, environ=CONFIG) == "The agent's answer."
    assert UNAVAILABLE in backend.input[1]["system_prompt"]
    assert "private-token" not in repr(context)


def test_unavailable_result_is_explained_even_if_consumer_returns_no_block():
    backend = Backend()
    context = {}
    result = SimpleNamespace(prompt_block=None, built=None, report={"hook_status": "disabled", "code": "bad_config"})
    call_with_vault(backend, "History?", context, {}, retrieve=lambda _, **kwargs: result, environ=CONFIG)
    assert UNAVAILABLE in backend.input[1]["system_prompt"]


def test_backend_failure_is_preserved_and_not_misreported_as_retrieval_failure():
    class BrokenBackend:
        def call_backend(self, *args):
            raise RuntimeError("agent failed")

    with pytest.raises(RuntimeError, match="agent failed"):
        call_with_vault(BrokenBackend(), "question", {}, {}, environ={})


@pytest.mark.parametrize("payload", [
    {"error": "unavailable"},
    {"keyword": [], "meaning": [], "adjacent": {"none": "not_retained"}},
    {"keyword": [HIT], "meaning": []},
])
def test_real_renderer_does_not_mark_an_unrelated_agent_answer(payload):
    renderer = pytest.importorskip("core.memory_ask.context")
    built = renderer.build(payload, max_chars=6000, max_bytes=12000)
    result = SimpleNamespace(prompt_block=built.block, built=built, report=built.report)
    answer = call_with_vault(Backend(), "What is the current status?", {}, {},
                            retrieve=lambda _, **kwargs: result, environ=CONFIG)
    assert answer == "The agent's answer."


def test_real_renderer_checks_only_the_vault_synthesis_ranges():
    renderer = pytest.importorskip("core.memory_ask.context")
    built = renderer.build({"keyword": [HIT], "meaning": []})
    assert built.report["passages_prepared"] == 1
    result = SimpleNamespace(prompt_block=built.block, built=built, report=built.report)
    backend = SimpleNamespace(call_backend=lambda *args: (
        "The current tool reports everything healthy.\n"
        "[VAULT_SYNTHESIS]The earlier decision was made (decision.md:2). "
        "A different decision was made (decision.md:99).[/VAULT_SYNTHESIS]"))
    answer = call_with_vault(backend, "question", {}, {}, retrieve=lambda _, **kwargs: result, environ=CONFIG)
    assert answer.startswith("The current tool reports everything healthy.\n")
    assert answer.count("[unsupported:") == 1
    assert "citation is not a passage" in answer
    assert "[VAULT_SYNTHESIS]" not in answer
