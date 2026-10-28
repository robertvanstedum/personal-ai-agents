"""A voice session's summary as a plain completion through the model gateway (Spec 165 v0.2 §5.4, decision D22 (a)).

The summary is not the person's agent turn: a "use no tools" prompt is not a boundary, and the agent runtimes run with
tools. So it is one chat completion through the LiteLLM model gateway, the way the agent runtimes reach it
(http://model-gateway:4000/v1, an OpenAI-compatible Chat Completions endpoint), with:

* no tool definitions, no tool choice, no agent runtime and no conversation or session binding (no ``user``);
* this session's kept records only, as the core's summary prompt bounds them, and the person's name and card;
* a capped key of its own per host, named by the host's adapter (MC: ``voice-summary-mc``), read from the environment
  as MINIMOI_GATEWAY_KEY_<NAME> (MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC); the model is gateway configuration: the request
  names the gateway's alias, minimoi-voice-summary, and the gateway maps it to a concrete model;
* one call, bounded: 5 s to connect and 30 s to answer (a non-streaming completion sends nothing until it is done), so
  the server has stopped waiting, and never retries, well inside the page's 45 s bound. When the page says "Summary
  unavailable", nothing is still being written for it here.

Anything but a plain text answer is unavailable: no key, the gateway down or refusing, a timeout, an error status, a
malformed answer, or a tool-call-shaped answer (tool_calls, function_call, finish_reason tool_calls). Unavailable is
never the live agent: there is no fallback. The bytes here never reach a log.
"""
from __future__ import annotations

import os
import re

GATEWAY_URL_ENV = "MINIMOI_MODEL_GATEWAY_URL"
DEFAULT_GATEWAY_URL = "http://model-gateway:4000/v1"       # the agent runtimes' own gateway address
SUMMARY_MODEL = "minimoi-voice-summary"                    # a gateway alias: the concrete model is gateway configuration
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 30
MAX_TOKENS = 1200
TEMPERATURE = 0.2
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def key_env(key_name):
    """The environment variable that holds a host's capped gateway key: voice-summary-mc -> MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC."""
    if not isinstance(key_name, str) or not _NAME.fullmatch(key_name):
        raise ValueError("a summary key's name is lower-case letters, digits and '-'")
    return "MINIMOI_GATEWAY_KEY_" + key_name.upper().replace("-", "_")


class GatewaySummary:
    """One host's summary client: ``(system, prompt) -> (text, None) | (None, error word)``. ``http_post`` is
    requests.post unless a test hands in its own."""

    def __init__(self, key_name, *, environ=None, http_post=None):
        environ = os.environ if environ is None else environ
        self.key_name, self.env = key_name, key_env(key_name)
        self._key = environ.get(self.env) or ""
        self.url = (environ.get(GATEWAY_URL_ENV) or DEFAULT_GATEWAY_URL).rstrip("/") + "/chat/completions"
        self._post = http_post

    def available(self):
        return bool(self._key)

    def request(self, system, prompt):
        """The whole request body: a plain completion, nothing else (no tools, no tool choice, no user binding)."""
        return {"model": SUMMARY_MODEL, "stream": False, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}

    def __call__(self, system, prompt):
        if not self._key:
            return None, "no_summary_key"
        post = self._post
        if post is None:
            import requests
            post = requests.post
        try:
            response = post(self.url, json=self.request(system, prompt), timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                            allow_redirects=False,
                            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        except Exception as exc:                            # never retried: one bounded call
            name = type(exc).__name__.lower()
            return None, "timeout" if "timeout" in name else "gateway_down"
        status = getattr(response, "status_code", 0)
        if status in (401, 403):
            return None, "key_refused"
        if status == 429:
            return None, "rate_limited"
        if status != 200:
            return None, "gateway_error"
        try:
            payload = response.json()
        except ValueError:
            return None, "malformed"
        return answer_text(payload)


def answer_text(payload):
    """(text, None) for a plain assistant answer, else (None, why). A tool-call-shaped answer is never a summary."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else None
    message = choice.get("message") if choice else None
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None, "malformed"
    if message.get("tool_calls") or message.get("function_call") or choice.get("finish_reason") in ("tool_calls",
                                                                                                    "function_call"):
        return None, "tool_call"
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        return None, "empty"
    return text, None
