"""HTTP adapter for the isolated, swappable COS Agent A runtime.

The permanent CoS coordination layer owns routing, context, policy, and memory.
This adapter only sends one normalized turn to the dedicated OpenClaw Gateway's
supported Chat Completions endpoint and returns assistant-visible text.
"""

import hashlib
import logging
import os
from urllib.parse import urlparse

import requests

log = logging.getLogger("cos.openclaw_backend")

_DEFAULT_RUNTIME_URL = "http://cos-agent-a:18789/v1"
_DEFAULT_AGENT_ID = "cos-agent-a"
_CONNECT_TIMEOUT_SECONDS = 5
_TURN_TIMEOUT_SECONDS = 120


class AgentRuntimeError(RuntimeError):
    """Safe, user-displayable failure from the configured agent runtime."""


class AgentSessionRecoveryRequired(AgentRuntimeError):
    """The current runtime session must be explicitly reset before reuse."""


class OpenClawBackend:
    """Conformant backend for the dedicated COS Agent A runtime."""

    backend_label = "COS Agent A (OpenClaw)"
    # Only the gateway-backed runtime emits an authenticated post-response
    # receipt. Legacy direct-provider backends intentionally skip that wait.
    supports_routing_receipts = True

    def __init__(
        self,
        write_memory,
        dispatch_tool,
        gateway_url: str | None = None,
        gateway_token: str | None = None,
        agent_id: str | None = None,
        model_label: str | None = None,
        http_post=None,
    ):
        self._write_memory = write_memory
        self._dispatch_tool = dispatch_tool
        configured_url = (
            gateway_url
            if gateway_url is not None
            else os.environ.get("COS_AGENT_RUNTIME_URL", _DEFAULT_RUNTIME_URL)
        )
        self._gateway_url = configured_url.rstrip("/")
        self._gateway_token = (
            gateway_token
            if gateway_token is not None
            else os.environ.get("COS_AGENT_RUNTIME_TOKEN", "")
        )
        self._agent_id = (
            agent_id
            if agent_id is not None
            else os.environ.get("COS_AGENT_RUNTIME_AGENT_ID", _DEFAULT_AGENT_ID)
        )
        self.model_label = (
            model_label
            if model_label is not None
            else os.environ.get("COS_AGENT_RUNTIME_MODEL_LABEL", "gateway-unresolved")
        )
        self._http_post = http_post or requests.post

        parsed = urlparse(self._gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("COS_AGENT_RUNTIME_URL must be an http(s) URL")
        if not self._gateway_token:
            raise ValueError("COS_AGENT_RUNTIME_TOKEN is required for the OpenClaw backend")
        if not self._agent_id:
            raise ValueError("COS_AGENT_RUNTIME_AGENT_ID must not be empty")

    def _session_user(self, conversation_id: str) -> str:
        """Return a stable opaque session id without leaking caller identifiers."""
        material = f"cos-confer\0{self._agent_id}\0{conversation_id}".encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:32]
        return f"cos-confer:{digest}"

    @staticmethod
    def _looks_like_corrupt_session(response) -> bool:
        """Recognize provider-history failures without exposing response text."""
        try:
            body = response.text[:8_192].casefold()
        except Exception:
            return False
        return any(marker in body for marker in (
            "encrypted_content",
            "encrypted content",
            "decrypt",
            "corrupt session",
            "invalid conversation history",
        ))

    def _post_turn(
        self,
        *,
        messages: list[dict],
        conversation_id: str,
    ) -> dict:
        """Call the Gateway once and return a validated completion payload."""
        url = f"{self._gateway_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        try:
            response = self._http_post(
                url,
                headers=headers,
                json={
                    "model": f"openclaw/{self._agent_id}",
                    "user": self._session_user(conversation_id),
                    "messages": messages,
                    "stream": False,
                },
                timeout=(_CONNECT_TIMEOUT_SECONDS, _TURN_TIMEOUT_SECONDS),
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as exc:
            raise AgentRuntimeError("COS Agent A timed out before replying.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise AgentRuntimeError("COS Agent A is currently unavailable.") from exc
        except requests.exceptions.RequestException as exc:
            raise AgentRuntimeError("COS Agent A request failed.") from exc

        if response.status_code == 401:
            raise AgentRuntimeError("COS Agent A authentication failed.")
        if not 200 <= response.status_code < 300:
            if self._looks_like_corrupt_session(response):
                raise AgentSessionRecoveryRequired(
                    "COS Agent A session needs recovery. Send /new to reset it."
                )
            raise AgentRuntimeError(
                f"COS Agent A returned an HTTP {response.status_code} error."
            )

        try:
            return response.json()
        except ValueError as exc:
            raise AgentRuntimeError("COS Agent A returned an invalid response.") from exc

    def reset_conversation(self, conversation_id: str) -> bool:
        """Reset the mapped Gateway session in place using its supported command."""
        payload = self._post_turn(
            messages=[{"role": "user", "content": "/reset"}],
            conversation_id=conversation_id,
        )
        try:
            reply = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentRuntimeError("COS Agent A returned an invalid reset response.") from exc
        if not isinstance(reply, str) or not reply.strip():
            raise AgentRuntimeError("COS Agent A did not confirm the session reset.")
        return True

    def call_backend(self, prompt: str, context: dict, tool_policy: dict) -> str:
        """Send one text turn and return only the final assistant-visible text."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must not be empty")

        confer = context.get("confer") or {}
        conversation_id = str(confer.get("conversation_id") or "owner")
        system_prompt = str(context.get("system_prompt") or "").strip()
        receipt_id = str(confer.get("receipt_id") or "").strip()
        if receipt_id:
            logical_model = os.environ.get(
                "COS_AGENT_RUNTIME_LOGICAL_MODEL", "minimoi-cos-agent"
            ).strip()
            system_prompt = (
                f"{system_prompt}\n\n"
                f"<!-- minimoi-routing-receipt:{receipt_id};"
                f"logical-model:{logical_model} -->"
            ).strip()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt.strip()})

        payload = self._post_turn(
            messages=messages,
            conversation_id=conversation_id,
        )
        try:
            reply = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentRuntimeError("COS Agent A returned an invalid response.") from exc

        if not isinstance(reply, str) or not reply.strip():
            raise AgentRuntimeError("COS Agent A returned no visible reply.")
        return reply.strip()
