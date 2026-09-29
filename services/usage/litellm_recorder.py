"""The model gateway's usage recorder: one standard usage record per call, on
EVERY route and outcome (LiteLLM is one emitter; the record is not LiteLLM's).

Loaded by the gateway config (`callbacks: [usage_recorder.usage_recorder]`)
next to, not instead of, receipt_callback (CoS's routing receipts and its
wait_for stay as they are). Staging only until Robert enables production.

* ok      async_log_success_event: tokens and LiteLLM's priced cost.
* error   async_log_failure_event: a provider or routing failure after the
          request reached a deployment (cost null, or LiteLLM's partial cost).
* refused async_post_call_failure_hook, for requests refused before any
          deployment (a key refused, a route not allowed, a budget or rate
          limit). A failure that reached a deployment is recorded once, by
          async_log_failure_event: both carry the same litellm_call_id.

Never blocks and never fails a call: records go to services/usage's
fire-and-forget writer, and every hook swallows its own errors.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_record  # noqa: E402  (stdlib-only; mounted next to this file)

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:                              # tests without LiteLLM installed
    class CustomLogger:                          # type: ignore[no-redef]
        pass

_log = logging.getLogger("minimoi.usage")
_NS = uuid.UUID("6f1b8c1e-2c7a-4f0e-9a51-3b8f0d6c9e21")
REFUSAL_DELAY_S = 2.0        # let a deployment-level failure record first
ALIAS_ACTORS = (("mc-agent-", "mc"), ("cos-agent-", "cos"))
ROUTE_ACTORS = (("minimoi-mc-", "mc"), ("minimoi-cos-", "cos"))


def actor_of(key_alias: str | None, route: str | None) -> str:
    for prefix, actor in ALIAS_ACTORS:
        if key_alias and key_alias.startswith(prefix):
            return actor
    for prefix, actor in ROUTE_ACTORS:
        if route and route.startswith(prefix):
            return actor
    return "unknown"


def _record_id(call_id) -> str:
    return str(uuid.uuid5(_NS, str(call_id))) if call_id else str(uuid.uuid4())


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _alias_from(slp_meta: dict, kwargs: dict) -> str | None:
    alias = (slp_meta or {}).get("user_api_key_alias")
    if not alias:
        meta = ((kwargs.get("litellm_params") or {}).get("metadata") or {})
        alias = meta.get("user_api_key_alias")
    return alias if isinstance(alias, str) and alias else None


def from_logging_event(kwargs: dict, start_time, end_time, *, ok: bool) -> dict:
    slp = kwargs.get("standard_logging_object") or {}
    meta = slp.get("metadata") or {}
    params = kwargs.get("litellm_params") or {}
    model_info = params.get("model_info") or {}
    route = (slp.get("model_group") or (params.get("metadata") or {}).get("model_group")
             or _requested_model(kwargs) or "unknown")
    alias = _alias_from(meta, kwargs)
    usage_obj = meta.get("usage_object") or {}
    cached = None
    details = usage_obj.get("prompt_tokens_details") if isinstance(usage_obj, dict) else None
    if isinstance(details, dict):
        cached = _int(details.get("cached_tokens"))
    cost = _num(slp.get("response_cost"))
    if not ok and not cost:
        cost = None                               # a failure's cost is unknown unless the provider reported one
    try:
        latency = max(0.0, (end_time - start_time).total_seconds() * 1000)
    except Exception:
        latency = None
    err = slp.get("error_information") or {}
    call_type = str(slp.get("call_type") or kwargs.get("call_type") or "")
    deployment = slp.get("model_id") or model_info.get("id")
    code = _status_code(err.get("error_code")) if not ok else 200
    # Refused: the gateway said no before any deployment was chosen (a key,
    # route, model, budget or rate limit). Error: a deployment was tried.
    status = "ok" if ok else ("refused" if not deployment and code in REFUSAL_CODES else "error")
    return {
        "record_id": _record_id(slp.get("litellm_call_id") or kwargs.get("litellm_call_id")),
        "occurred_at": usage_record.now_iso(),
        "env": usage_record.env_name(),
        "emitter": "gateway",
        "actor": actor_of(alias, route),
        "kind": "model",
        "route": str(route)[:200],
        "provider": (slp.get("custom_llm_provider") or params.get("custom_llm_provider") or None),
        "model": (str(slp.get("model"))[:200] if slp.get("model") else None),
        "deployment_id": str(deployment) if deployment else None,
        "fallback_position": _int(model_info.get("fallback_position")),
        "status": status,
        "http_status": _int(code),
        "error_class": (str(err.get("error_class"))[:80] if not ok and err.get("error_class") else None),
        "latency_ms": round(latency, 3) if latency is not None else None,
        "input_tokens": _int(slp.get("prompt_tokens")) if ok else None,
        "output_tokens": _int(slp.get("completion_tokens")) if ok else None,
        "cached_tokens": cached,
        "units": {"responses": 1} if call_type.startswith(("aresponses", "responses")) else None,
        "cost_usd": cost,
        "cost_source": "price_table" if cost is not None else "none",
        "key_ref": alias,
        "correlation_id": None,
    }


REFUSAL_CODES = (400, 401, 403, 404, 429)


def _requested_model(kwargs: dict):
    """The model the caller asked for, when no deployment was chosen."""
    for value in (kwargs.get("model"), ((kwargs.get("litellm_params") or {}).get("proxy_server_request") or {})
                  .get("body", {}).get("model") if isinstance(kwargs.get("litellm_params"), dict) else None):
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return None


def _status_code(value):
    try:
        return int(str(value)[:3]) if value not in (None, "") else None
    except ValueError:
        return None


def from_refusal(request_data: dict, exc: Exception, key) -> dict:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    code = _status_code(status)
    route = (request_data or {}).get("model")
    alias = getattr(key, "key_alias", None) if key is not None else None
    refused = code in REFUSAL_CODES or type(exc).__name__ in ("BudgetExceededError", "ProxyException")
    return {
        "record_id": _record_id((request_data or {}).get("litellm_call_id")),
        "occurred_at": usage_record.now_iso(),
        "env": usage_record.env_name(),
        "emitter": "gateway",
        "actor": actor_of(alias if isinstance(alias, str) else None, route if isinstance(route, str) else None),
        "kind": "model",
        "route": (str(route)[:200] if isinstance(route, str) and route else "unknown"),
        "status": "refused" if refused else "error",
        "http_status": code,
        "error_class": type(exc).__name__[:80],
        "cost_usd": None,
        "cost_source": "none",
        "key_ref": alias if isinstance(alias, str) and alias else None,
    }


class UsageRecorder(CustomLogger):
    def __init__(self):
        super().__init__()
        self._seen = collections.OrderedDict()
        self._tasks: set = set()

    def _mark(self, record_id: str) -> bool:
        """True the first time a record id is seen (a small LRU)."""
        if record_id in self._seen:
            return False
        self._seen[record_id] = True
        while len(self._seen) > 2048:
            self._seen.popitem(last=False)
        return True

    def _emit(self, rec: dict) -> None:
        if self._mark(rec["record_id"]):
            usage_record.record(**rec)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._emit(from_logging_event(kwargs, start_time, end_time, ok=True))
        except Exception as exc:
            _log.warning("usage: success not recorded (%s)", type(exc).__name__)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._emit(from_logging_event(kwargs, start_time, end_time, ok=False))
        except Exception as exc:
            _log.warning("usage: failure not recorded (%s)", type(exc).__name__)

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict,
                                           traceback_str=None):
        try:
            rec = from_refusal(request_data, original_exception, user_api_key_dict)
        except Exception as exc:
            _log.warning("usage: refusal not recorded (%s)", type(exc).__name__)
            return None

        async def later():
            await asyncio.sleep(REFUSAL_DELAY_S)
            try:
                self._emit(rec)
            except Exception as exc:
                _log.warning("usage: refusal not recorded (%s)", type(exc).__name__)
        try:
            task = asyncio.get_running_loop().create_task(later())
            self._tasks.add(task)                 # keep a reference until it has run
            task.add_done_callback(self._tasks.discard)
        except RuntimeError:
            self._emit(rec)
        return None                               # never transforms the error the caller sees


usage_recorder = UsageRecorder()
