"""Keep one conversation on one warm xAI cache (#553).

xAI caches a prompt prefix per server, and keeps a conversation on the server that holds its prefix only when each
request carries the same ``x-grok-conv-id`` header (Chat Completions; docs.x.ai, "Maximizing cache hits"). Without it,
8 of MC's last 11 turns started cold in production (#553 research, 13 Nov).

OpenClaw sends a stable per-session ``prompt_cache_key`` in the request body once the gateway provider's model entry sets
``compat.supportsPromptCacheKey: true`` (MC's container model access policy, and CoS's config). LiteLLM 1.106.6
cannot pass that on to xAI by itself:

* its xAI adapter does not list ``prompt_cache_key`` as a supported parameter, so ``drop_params: true`` drops it;
* ``x-pass-*`` header stripping exists only on its pass-through endpoints, not /v1/chat/completions;
* model-group header forwarding forwards the client's own ``x-`` headers, and OpenClaw's headers for a custom provider
  are static, never per conversation.

So this hook, after the router has picked a deployment and only when that deployment is xAI, turns the request's
``prompt_cache_key`` into ``x-grok-conv-id``. The value sent is a digest, not OpenClaw's session id: the same key gives
the same id, and nothing internal leaves the gateway. Any other provider is untouched (OpenAI keeps ``prompt_cache_key``,
which its adapter supports). A request without a key is untouched. The hook never raises into LiteLLM.
"""
from __future__ import annotations

import hashlib
import logging

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:                              # tests without LiteLLM installed
    class CustomLogger:                          # type: ignore[no-redef]
        pass

_log = logging.getLogger("minimoi.cache_affinity")
if not _log.handlers:                            # LiteLLM leaves the root logger at WARNING; these lines are INFO
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False
HEADER = "x-grok-conv-id"
# xAI calls seen by this gateway process, with and without a conversation id (counts only: never a key or an id). The
# log line per xAI call lets Dev count cold starts per conversation before and after (#553 measurement plan).
COUNTS = {"with": 0, "without": 0}
PREFIX = "minimoi-"
MAX_KEY = 1024


def _is_xai(kwargs: dict) -> bool:
    params = kwargs.get("litellm_params") if isinstance(kwargs.get("litellm_params"), dict) else {}
    provider = kwargs.get("custom_llm_provider") or params.get("custom_llm_provider")
    if isinstance(provider, str) and provider:
        return provider == "xai"
    model = kwargs.get("model")
    return isinstance(model, str) and model.startswith("xai/")


def conversation_id(cache_key: object) -> str | None:
    """The header value for one cache key: a stable digest, or None for no usable key."""
    if not isinstance(cache_key, str) or not cache_key.strip() or len(cache_key) > MAX_KEY:
        return None
    return PREFIX + hashlib.sha256(cache_key.strip().encode("utf-8")).hexdigest()[:32]


def with_conversation_id(kwargs: dict) -> dict | None:
    """The deployment call's kwargs with ``x-grok-conv-id`` added, or None when nothing changes."""
    if not isinstance(kwargs, dict) or not _is_xai(kwargs):
        return None
    conv = conversation_id(kwargs.get("prompt_cache_key"))
    if conv is None:
        return None
    for name in ("extra_headers", "headers"):
        existing = kwargs.get(name)
        if isinstance(existing, dict) and any(isinstance(k, str) and k.lower() == HEADER for k in existing):
            return None                          # a caller's own id wins
    out = dict(kwargs)
    extra = out.get("extra_headers")
    out["extra_headers"] = {**(extra if isinstance(extra, dict) else {}), HEADER: conv}
    return out


def _count(kwargs, out) -> None:
    if not isinstance(kwargs, dict) or not _is_xai(kwargs):
        return
    sent = out is not None or any(isinstance(h, dict) and any(isinstance(k, str) and k.lower() == HEADER for k in h)
                                  for h in (kwargs.get("extra_headers"), kwargs.get("headers")))
    COUNTS["with" if sent else "without"] += 1
    _log.info("xai call %s x-grok-conv-id (with=%d without=%d)", "with" if sent else "without", COUNTS["with"], COUNTS["without"])


class CacheAffinity(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        try:
            out = with_conversation_id(kwargs)
            _count(kwargs, out)
            return out
        except Exception:                        # never fail a call over a cache hint
            _log.debug("cache affinity skipped", exc_info=True)
            return None


cache_affinity = CacheAffinity()
