"""Continuation transport: exactly one logical turn, no retry without proof."""
import json
from types import SimpleNamespace

import pytest

from core.model_routes.depth_control import MARKER
from domains.cos.backends import openclaw_backend as cos
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.backend import TurnRequest
from minimoi_portal.guild_ui.mc.stream import Delta, Finish, RunStarted, Terminal, Failure

NONCE = "c" * 32
RUN = "chatcmpl_11111111-1111-4111-8111-111111111111"
DEEP_RUN = "chatcmpl_22222222-2222-4222-8222-222222222222"
DEEP = {"route_id": "api-xai-strong-deep", "provider": "minimoi-gateway", "model": "minimoi-xai-strong-deep"}
ROUTE = {"route_id": "api-xai-strong", "depth": {"kind": "quick", "allow_continuation": True, "continuation_route": DEEP}}


def signal():
    return MARKER + json.dumps({"kind": "request_depth", "nonce": NONCE, "reason": "Compare risks"})


def proof(**changes):
    return {"instance": "a" * 32, "run": RUN, "session": "agent:mc-chat:openai-user:fixture", "agent": "mc-chat",
            "success": True, "model_calls": 1, "tool_calls": 0, "signature": "A" * 86 + "==", **changes}


@pytest.mark.parametrize("receipt,prepared,expected", [(proof(), True, 2), (None, True, 1),
    (proof(tool_calls=1), True, 1), (proof(model_calls=2), True, 1), (proof(), False, 1)])
def test_mc_continues_only_with_exact_unused_receipt(monkeypatch, receipt, prepared, expected):
    import minimoi_portal.guild_ui.mc.openclaw as mc
    monkeypatch.setattr(mc, "uuid4", lambda: SimpleNamespace(hex=NONCE))
    backend = OpenClawMasterCraftsman("http://relay/v1", "synthetic-token")
    posts, transitions = [], []
    def stream(payload, headers, cancel):
        posts.append(json.loads(payload))
        if len(posts) == 1:
            yield RunStarted(RUN)
            yield Delta(signal())
            yield Finish("stop")
            if receipt:
                yield Terminal(receipt)
        else:
            assert transitions == [RUN] # durable preparation before any second send
            yield RunStarted(DEEP_RUN)
            yield Delta("Considered answer")
            yield Finish("stop")
    monkeypatch.setattr(backend, "_stream", stream)
    def prepare(run, nonce, evidence):
        transitions.append(run)
        assert nonce == NONCE
        return prepared
    req = TurnRequest("same-conversation", "Question", "same-note", route=ROUTE, prepare_depth=prepare)
    events = list(backend.stream_turn(req))
    assert len(posts) == expected
    assert all(MARKER not in e.text for e in events if isinstance(e, Delta))
    if expected == 2:
        assert [e.text for e in events if isinstance(e, Delta)] == ["Considered answer"]
        assert posts[0]["user"] == posts[1]["user"]
        assert [e for e in events if isinstance(e, RunStarted)][1].replaces == RUN
    else:
        assert any(isinstance(e, Failure) for e in events)


@pytest.mark.parametrize("prepared", [True, False])
def test_cos_continuation_keeps_binding_until_deep_finishes(monkeypatch, prepared):
    monkeypatch.setattr(cos, "uuid4", lambda: SimpleNamespace(hex=NONCE))
    calls = []
    class Binder:
        def begin(self, **kw): calls.append("begin"); return "turn"
        def attach(self, turn, run): calls.append("attach-quick"); return True
        def prepare_depth(self, turn, run, nonce): calls.append("prepare"); return prepared
        def attach_depth(self, turn, run): calls.append("attach-deep"); return True
        def end(self, turn): calls.append("end")
    backend = cos.OpenClawBackend(lambda x: True, lambda *a: {}, gateway_url="http://fixture/v1",
                                  gateway_token="synthetic", binder=Binder())
    posts = []
    def post(**kw):
        posts.append(kw)
        deep = len(posts) == 2
        if deep:
            assert calls[-1] == "prepare"
            assert kw["model"] == "minimoi-gateway/minimoi-xai-strong-deep"
        kw["on_run"](DEEP_RUN if deep else RUN)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Considered answer" if deep else signal()}}]}
    monkeypatch.setattr(backend, "_post_turn", post)
    token = cos.turn_route.set({"route": ROUTE})
    try:
        if prepared:
            result = backend._call_backend_payload("Question", {}, {})
            assert result["choices"][0]["message"]["content"] == "Considered answer"
            assert posts[0]["conversation_id"] == posts[1]["conversation_id"]
            assert calls == ["begin", "attach-quick", "prepare", "attach-deep", "end"]
        else:
            with pytest.raises(cos.AgentRuntimeError):
                backend._call_backend_payload("Question", {}, {})
            assert len(posts) == 1 and calls[-1] == "end"
    finally:
        cos.turn_route.reset(token)
