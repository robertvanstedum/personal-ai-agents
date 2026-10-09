"""The conversation lane's agent is configurable (MC_RUNTIME_CHAT_MODEL): unset, chat goes to mc-agent exactly as before."""
from __future__ import annotations

import json

import pytest

from minimoi_portal.guild_ui.mc.openclaw import CHAT_MODELS, MODEL, OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.backend import TurnRequest

REQ = TurnRequest(conversation_id="c-0123456789ab", text="hello", note_request_id="n-1", correlation_id="a" * 32)


def _sent(adapter):
    return json.loads(adapter._payload(REQ))["model"], json.loads(adapter._payload(REQ, stream=True))["model"]


def test_unset_chat_goes_to_mc_agent_exactly_as_before():
    assert _sent(OpenClawMasterCraftsman("http://r/v1", "t" * 32, http_get=lambda *a, **k: None, http_post=lambda *a, **k: None)) == (MODEL, MODEL) == ("openclaw/mc-agent",) * 2


@pytest.mark.parametrize("model", CHAT_MODELS)
def test_each_allowed_chat_agent_is_what_both_paths_send(model):
    adapter = OpenClawMasterCraftsman("http://r/v1", "t" * 32, http_get=lambda *a, **k: None, http_post=lambda *a, **k: None, chat_model=model)
    assert _sent(adapter) == (model, model)


@pytest.mark.parametrize("bad", ["openclaw/main", "openclaw/default", "gpt-5", "mc-chat", "openclaw/mc-chat "])
def test_any_other_value_refuses_to_build(bad):
    with pytest.raises(ValueError):
        OpenClawMasterCraftsman("http://r/v1", "t" * 32, chat_model=bad)


def test_from_env_reads_the_setting_and_ignores_blank():
    env = {"MC_RUNTIME_URL": "http://r/v1", "MC_RUNTIME_TOKEN": "t" * 32}
    assert OpenClawMasterCraftsman.from_env({**env, "MC_RUNTIME_CHAT_MODEL": "openclaw/mc-chat"}, http_get=lambda *a, **k: None, http_post=lambda *a, **k: None).chat_model == "openclaw/mc-chat"
    assert OpenClawMasterCraftsman.from_env({**env, "MC_RUNTIME_CHAT_MODEL": "  "}, http_get=lambda *a, **k: None, http_post=lambda *a, **k: None).chat_model == MODEL
    assert OpenClawMasterCraftsman.from_env(env, http_get=lambda *a, **k: None, http_post=lambda *a, **k: None).chat_model == MODEL
