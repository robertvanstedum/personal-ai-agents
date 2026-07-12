"""
domains/cos/backends/grok_backend.py — Grok API backend for CoS

Implements the call_backend() boundary from config/cos_interface.md v0.2.
Wraps the coordination layer's existing Grok chat logic as a clean, swappable module.

Key design points (per cos_interface.md v0.2):
- Receives prompt + context (including pre-built system prompt) from the coordination layer
- Dispatches tool calls back through the coordination layer's _dispatch_tool()
- Writes memory via the coordination layer's _append_memory() — never opens the file directly
- Memory-worthiness judgment lives here (backend quality dimension, not platform logic)
"""

import json
import logging

log = logging.getLogger("cos.grok_backend")

_MODEL = "grok-4-1-fast-reasoning"
_MAX_TOOL_ROUNDS = 3

# Observation tools — always offered (unrestricted per observe/mutate principle)
_OBSERVATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ops_status",
            "description": (
                "Get the current live status of the Operations agent — "
                "disk usage, service health, uptime, open escalations."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ops_log",
            "description": (
                "Get the last N maintenance actions Operations has taken — "
                "what it fixed, what it escalated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of recent actions to return. Default 5.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_domain_health",
            "description": (
                "Check whether the core mini-moi services are responding — "
                "Curator (8766), German (8767), Portal (5001)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# Internal-write tools — offered even with mutation=False because they only
# create pending items for Robert's manual review; never auto-executed
_INTERNAL_WRITE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "queue_recommendation",
            "description": (
                "Add an actionable item to the CoS recommendations queue for "
                "Robert to review later. Creates a pending entry only — never "
                "auto-executed. Robert manually decides whether to act on it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Which domain: curator, german, career_focus, mini_moi, operations",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the recommendation is",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0.0 to 1.0",
                    },
                },
                "required": ["domain", "description"],
            },
        },
    },
]


class GrokBackend:
    """
    Grok API backend for chief_of_staff.py's coordination layer.

    Initialized with callables from the coordination layer so this module
    never imports from chief_of_staff.py (avoids circular dependency) and
    never opens cos_memory.md directly (write path stays in coordination layer).

    Args:
        write_memory: callable — coordination layer's _append_memory()
        dispatch_tool: callable — coordination layer's _dispatch_tool(name, args)
    """

    backend_label = "Grok (direct API)"
    model_label   = "grok-4-1-fast-reasoning"

    def __init__(self, write_memory, dispatch_tool):
        self._write_memory = write_memory
        self._dispatch_tool = dispatch_tool

    def _get_client(self):
        from get_secret import get_secret
        try:
            api_key = get_secret("XAI_API_KEY", "xai", "api_key")
        except Exception as e:
            raise ValueError(f"xAI API key not found: {e}")
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    def _select_tools(self, tool_policy: dict) -> list:
        """Gate tool schemas by policy. Observation always on; mutation tools excluded."""
        tools = list(_OBSERVATION_TOOLS) if tool_policy.get("observation", True) else []
        # Internal-write tools (queue_recommendation) are always offered —
        # they only create pending review items, not domain mutations
        tools.extend(_INTERNAL_WRITE_TOOLS)
        return tools

    def call_backend(self, prompt: str, context: dict, tool_policy: dict) -> str:
        """
        Call the Grok API. Tool dispatch flows through the coordination layer's
        dispatch_tool callable. Memory writes flow through write_memory.

        prompt: str — user's message (channel payload already stripped)
        context: dict — must include "system_prompt" (pre-built by coordination layer)
        tool_policy: dict — {"observation": True, "mutation": False} is the default
        """
        system_prompt = context.get("system_prompt", "")
        if not system_prompt:
            log.warning("call_backend: no system_prompt in context — falling back to empty")

        tools = self._select_tools(tool_policy)
        messages = [{"role": "user", "content": prompt}]
        client = self._get_client()

        for _ in range(_MAX_TOOL_ROUNDS):
            resp = client.chat.completions.create(
                model=_MODEL,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=600,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                self._maybe_update_memory(prompt, reply)
                return reply

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                result = self._dispatch_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        return "I ran into a loop trying to gather data. Try asking again."

    def _maybe_update_memory(self, user_text: str, reply: str):
        """
        Memory-worthiness judgment — backend quality dimension, per cos_interface.md v0.2.
        Writes through the coordination layer's write_memory callable, never directly.
        """
        try:
            client = self._get_client()
            check_prompt = (
                "Review this conversation snippet. "
                "Did it contain a new fact about Robert's goals, a decision he made, "
                "or a non-obvious system observation worth remembering long-term?\n\n"
                f"User: {user_text}\n"
                f"CoS: {reply}\n\n"
                "If yes: reply with a single sentence starting with the key fact (no preamble).\n"
                "If no: reply with exactly the word NONE."
            )
            resp = client.chat.completions.create(
                model=_MODEL,
                messages=[{"role": "user", "content": check_prompt}],
                temperature=0.0,
                max_tokens=80,
            )
            result = (resp.choices[0].message.content or "").strip()
            if result and result.upper() != "NONE":
                self._write_memory(result)
        except Exception as e:
            log.error("memory_check_error: %s", e)
