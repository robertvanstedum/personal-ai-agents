"""
domains/cos/backends/openclaw_backend.py — OpenClaw backend stub for CoS

Phase 1: interface only. The dedicated, CoS-scoped OpenClaw Gateway instance
(own port, own config dir, isolated from personal OpenClaw #1 at port 18789)
has not yet been provisioned.

Per config/cos_interface.md v0.2, when implemented this backend:
- Connects via WebSocket to the CoS-scoped gateway (default: ws://localhost:18770)
- Configures the gateway's native tools to observation-only scope (no mutation)
- Returns the gateway's response string
- Writes memory via the coordination layer's write_memory callable

Phase 1 goal: prove the call_backend() boundary is clean. Swapping to this backend
from GrokBackend must require zero changes in chief_of_staff.py (the coordination layer).
"""

import logging

log = logging.getLogger("cos.openclaw_backend")

_GATEWAY_PORT = 18770  # CoS-scoped instance — distinct from personal OpenClaw #1 at 18789


class OpenClawBackend:
    """
    OpenClaw backend for chief_of_staff.py's coordination layer.

    Implements the same call_backend() interface as GrokBackend so the
    coordination layer can swap between backends without code changes.

    Not operational until a CoS-scoped OpenClaw Gateway instance is provisioned
    at the gateway_url and configured with observation-only native tools.
    """

    backend_label = "OpenClaw"
    model_label   = "pending"

    def __init__(self, write_memory, dispatch_tool, gateway_url: str = f"ws://localhost:{_GATEWAY_PORT}"):
        self._write_memory = write_memory
        self._dispatch_tool = dispatch_tool
        self._gateway_url = gateway_url

    def call_backend(self, prompt: str, context: dict, tool_policy: dict) -> str:
        """
        Route prompt to the CoS-scoped OpenClaw Gateway.

        Not yet implemented. Raises NotImplementedError until the gateway
        instance is configured and running at self._gateway_url.
        """
        raise NotImplementedError(
            f"OpenClaw backend not yet configured. "
            f"Provision a CoS-scoped OpenClaw Gateway at {self._gateway_url} "
            f"(distinct from personal OpenClaw #1 at port 18789). "
            f"See config/cos_interface.md v0.2 for the integration spec."
        )
