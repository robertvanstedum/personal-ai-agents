"""
Exercises the platform's local-Ollama independence path (see ARCHITECTURE.md
§ Independence): the system should be able to fall back to a fully local
model at zero marginal cost. This test queries a real local Ollama instance
running gemma3:1b — it's a deliberate part of the suite, not dead code.

Skips (does not fail) when Ollama isn't installed or no server is reachable,
which is the expected state on CI runners and most contributor machines.
"""

import pytest

ollama = pytest.importorskip("ollama", reason="ollama not installed — local LLM path not testable here")


def _ollama_server_available() -> bool:
    try:
        ollama.list()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_server_available(), reason="no local Ollama server reachable")
def test_local_llm_query():
    models = ollama.list()
    model_names = [m.get("name", m.get("model", "unknown")) for m in models["models"]]
    assert model_names, "expected at least one local model to be pulled"

    response = ollama.chat(
        model="gemma3:1b",
        messages=[{
            "role": "user",
            "content": "Reply with exactly one word: acknowledged.",
        }],
    )

    content = response["message"]["content"]
    assert content, "expected a non-empty response from the local model"
